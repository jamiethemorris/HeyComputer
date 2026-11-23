"""Exercise real HTTP body/line reads on an owned socket pair, never the network."""
import http.client
import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from heycomputer.mcp_loop import run_loop
from heycomputer.mcp_responses import make_turn
from heycomputer.protocol import PlanError
from test_mcp_loop import Client, call, message


# The child inherits only the fixture socket. Both HTTP transports use it instead
# of DNS/TCP/TLS, retaining their real request, header and buffered read paths.
CHILD = '''
import http.client, runpy, socket, sys
sock = socket.socket(fileno=int(sys.argv[1]))
http.client.HTTPConnection.connect = http.client.HTTPSConnection.connect = lambda conn: setattr(conn, 'sock', sock)
runpy.run_module(sys.argv[2], run_name='__main__')
'''


class ProviderWaitTests(unittest.TestCase):
    def exercise(self, provider, mode):
        client = Client()
        started, closed = threading.Event(), threading.Event()
        item = message('Ready.') if mode == 'complete' else call('track_get_all', 'late')
        body = json.dumps({'status': 'completed', 'output': [item]}).encode()
        stream = provider == 'chatgpt'
        if stream:
            body = b'{"type":"response.completed","response":' + body + b'}\n\n'

        with ExitStack() as stack:
            local, peer = socket.socketpair()
            stack.callback(local.close)
            stack.callback(peer.close)
            peer.settimeout(3)

            def serve():
                with peer.makefile('rb') as wire:
                    wire.readline()  # request line
                    headers = http.client.parse_headers(wire)
                    wire.read(int(headers['Content-Length']))
                media = b'text/event-stream' if stream else b'application/json'
                peer.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: ' + media + b'\r\n\r\n' + (b'data: ' if stream else b' '))
                if mode == 'complete':
                    peer.sendall(body)
                    peer.shutdown(socket.SHUT_WR)
                    return
                # Allow the reader to enter read()/readline() before cancelling.
                time.sleep(.1)
                started.set()
                # Rescue an unbounded implementation with a valid late tool call.
                rescue = time.monotonic() + 2
                while time.monotonic() < rescue:
                    if select.select([peer], [], [], .025)[0] and peer.recv(1) == b'':
                        closed.set()
                        return
                    if mode == 'trickle':
                        try:
                            peer.sendall(b' ')
                        except BrokenPipeError:  # Reader closed just after select.
                            closed.set()
                            return
                peer.sendall(body)
                peer.shutdown(socket.SHUT_WR)

            server = threading.Thread(target=serve)
            server.start()
            stack.callback(server.join, 3)
            children = []
            popen = subprocess.Popen

            def spawn(argv, **kwargs):
                child = popen([sys.executable, '-B', '-c', CHILD, str(local.fileno()), argv[-1]],
                              pass_fds=(local.fileno(),), **kwargs)
                children.append(child)
                local.close()
                return child

            stack.enter_context(patch('subprocess.Popen', side_effect=spawn))
            # Same offline transport when reproducing the old in-process wait.
            for cls in (http.client.HTTPConnection, http.client.HTTPSConnection):
                stack.enter_context(patch.object(cls, 'connect', lambda conn: setattr(conn, 'sock', local)))
            stack.enter_context(patch.dict(os.environ, NO_PROXY='*', no_proxy='*'))
            stack.enter_context(patch('heycomputer.providers.api_key', return_value='fixture-key'))

            def check():
                if mode == 'stall' and started.is_set():
                    raise PlanError('fixture cancelled')

            turn = make_turn({'fallback_provider': provider}, check, access_token='fixture-token')
            before = time.monotonic()
            def run():
                return run_loop(client, turn, 'Inspect tracks', {}, check, lambda: .65 if mode == 'trickle' else 10)

            if mode == 'complete':
                self.assertEqual(run(), 'Ready.')
            else:
                with self.assertRaisesRegex(PlanError, 'cancelled' if mode == 'stall' else 'timed out'):
                    run()
                self.assertTrue(started.is_set(), 'must reach the body read')
                self.assertLess(time.monotonic() - before, 1.4)
                self.assertTrue(closed.wait(.3), 'peer did not observe connection cleanup')
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode, 'provider child not reaped')
            self.assertEqual(client.calls, [])

    def test_trickling_body_cannot_extend_turn_deadline(self):
        for provider in ('openai', 'openrouter', 'chatgpt'):
            with self.subTest(provider=provider):
                self.exercise(provider, 'trickle')

    def test_cancel_interrupts_stalled_body(self):
        for provider in ('openai', 'openrouter', 'chatgpt'):
            with self.subTest(provider=provider):
                self.exercise(provider, 'stall')

    def test_completed_turn_returns_through_supervisor(self):
        for provider in ('openai', 'openrouter', 'chatgpt'):
            with self.subTest(provider=provider):
                self.exercise(provider, 'complete')
