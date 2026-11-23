import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from heycomputer import reaper_mcp_client as mcp


# Wire shapes from xdarkzx-reaper-mcp 0.8.3 and its cached MCP 1.30.0 SDK.
# This child only uses stdio; it never imports the bridge or touches REAPER.
SERVER = r'''
import json, os, signal, sys, time

mode, literal = sys.argv[1:]
assert literal == "spaces; $(not-a-command) 'quoted'"
signal.alarm(8)  # Rescue a broken test without leaving a child running.

def receive():
    return json.loads(input())

def send(**payload):
    print(json.dumps(dict(jsonrpc="2.0", **payload), ensure_ascii=False), flush=True)

def text(value):
    return [{"type": "text", "text": value}]

if mode == "exit":
    sys.stderr.write("x" * 100_000 + "\nfixture startup diagnostic\n")
    sys.exit(7)
if mode == "stall-init":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(10)

request = receive()
assert request["jsonrpc"] == "2.0" and request["method"] == "initialize"
assert request["params"]["capabilities"] == {}
assert request["params"]["clientInfo"]["name"] == "HeyComputer"
version = "0.0.0" if mode == "bad-version" else request["params"]["protocolVersion"]
send(id=request["id"], result=dict(protocolVersion=version, capabilities={"tools": {}},
                                  serverInfo={"name": "ReaperMCP", "version": "1.30.0"}))
assert receive() == {"jsonrpc": "2.0", "method": "notifications/initialized"}
if mode == "stall-write":
    time.sleep(10)

for line in sys.stdin:
    request = json.loads(line)
    rid = request["id"]
    params = request.get("params", {})
    if request["method"] == "tools/list":
        if not params:
            send(method="notifications/message", params={"data": "fixture log"})
            send(id=-1, result={"tools": [{"name": "unrelated"}]})
            send(id=float(rid), result={"tools": [{"name": "wrong id type"}]})
            send(id=rid, result={"tools": [{"name": "first", "inputSchema": {"type": "object"}}],
                                 "nextCursor": "page-two"})
        else:
            assert params == {"cursor": "page-two"}
            send(id=rid, result={"tools": [{"name": "second", "description": "→" * 40000}]})
        continue
    assert request["method"] == "tools/call"
    name, args = params["name"], params["arguments"]
    if name == "rpc_error":
        send(id=rid, error={"code": -32602, "message": "Fixture invalid arguments"})
        continue
    if name == "invalid_frame":
        print("this is not JSON-RPC", flush=True)
        continue
    if name == "disconnect":
        sys.exit(2)
    if name == "stall_read":
        os.write(1, b'{"jsonrpc":"2.0",')  # Readable pipe, but no complete line.
        time.sleep(10)
    if name == "late":
        time.sleep(.5)
    if name == "bridge_missing":
        result = {"isError": True, "content": text("Error executing tool: [CONNECTION_REFUSED (1001)] "
                                                   "REAPER MCP server not running.")}
    elif name == "bridge_stale":
        result = {"isError": True, "content": text("[CONNECTION_LOST (1003)] "
                                                   "REAPER MCP server appears stale (no heartbeat).")}
    elif name == "failure":
        data = {"success": False, "error": "Fixture operation rejected"}
        result = {"structuredContent": data, "content": text(json.dumps(data)), "isError": False}
    elif name == "json_text":
        result = {"content": text(json.dumps([1, args["label"]]))}
    elif name == "plain_text":
        result = {"content": text("plain text")}
    elif name == "invalid_result":
        result = {"isError": False}
    else:
        data = {"arguments": args, "tmpdir": os.environ.get("TMPDIR"),
                "pid": os.getpid(), "no_supersede": os.environ.get("REAPER_MCP_NO_SUPERSEDE")}
        result = {"structuredContent": data, "content": text(json.dumps(data)), "isError": False}
    send(id=rid, result=result)
'''


class Cancelled(Exception):
    pass


@unittest.skipUnless(os.name == "posix", "requires POSIX pipes")
class ReaperMCPClientTests(unittest.TestCase):
    def client(self, mode="normal", check=lambda: None):
        command = shlex.join([sys.executable, "-u", "-c", SERVER, mode,
                              "spaces; $(not-a-command) 'quoted'"])
        return mcp.ReaperMCPClient(command, check)

    def assert_reaped(self, pid):
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_default_command_is_pinned(self):
        self.assertEqual(mcp.DEFAULT_COMMAND, "uvx --python 3.13 xdarkzx-reaper-mcp==0.8.3")

    def test_handshake_discovery_and_decoding(self):
        with self.client() as client:
            tools = client.list_tools()
            self.assertEqual([tool["name"] for tool in tools], ["first", "second"])
            self.assertEqual(tools[1]["description"], "→" * 40000)
            args = {"label": "clé\nmix"}
            result = client.call("structured", args)
            self.assertEqual(result["arguments"], args)
            self.assertEqual(result["no_supersede"], "1")
            self.assertEqual(client.call("json_text", args), [1, args["label"]])
            self.assertEqual(client.call("plain_text", {}), "plain text")
        self.assert_reaped(result["pid"])

    @unittest.skipUnless(sys.platform == "darwin", "uses the macOS session temp directory")
    def test_child_uses_macos_user_temp_even_with_worker_tmpdir(self):
        expected = subprocess.check_output(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
                                           text=True).strip()
        with patch.dict(os.environ, {"TMPDIR": "/fixture-worker-scratch"}):
            with self.client() as client:
                self.assertEqual(client.call("structured", {})["tmpdir"], expected)
            self.assertEqual(os.environ["TMPDIR"], "/fixture-worker-scratch")

    def test_tool_and_rpc_errors(self):
        with self.client() as client:
            for name, message in [("rpc_error", "Fixture invalid arguments"),
                                  ("failure", "Fixture operation rejected"),
                                  ("bridge_missing", "reaper_mcp_server.lua"),
                                  ("bridge_stale", "reaper_mcp_server.lua")]:
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, message):
                    client.call(name, {})
            self.assertEqual(client.call("plain_text", {}), "plain text")

    def test_launch_errors_are_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "uvx.*absolute path"):
            with mcp.ReaperMCPClient("/no/such/uvx", lambda: None):
                self.fail("unexpected startup")
        with self.assertRaisesRegex(RuntimeError, "fixture startup diagnostic"):
            with self.client(mode="exit"):
                self.fail("unexpected startup")
        with self.assertRaisesRegex(RuntimeError, "protocol version"):
            with self.client(mode="bad-version"):
                self.fail("unexpected startup")

    def test_transport_error_closes_session(self):
        with self.client() as client:
            pid = client.call("structured", {})["pid"]
            with self.assertRaisesRegex(RuntimeError, "JSON-RPC"):
                client.call("invalid_frame", {})
            self.assert_reaped(pid)

    def test_disconnect_during_tool_reports_unknown_outcome(self):
        with self.client() as client:
            with self.assertRaisesRegex(RuntimeError, "disconnected.*outcome.*unknown"):
                client.call("disconnect", {})

    def test_missing_tool_content_is_not_success(self):
        with self.client() as client:
            with self.assertRaisesRegex(RuntimeError, "Invalid ReaperMCP tool result"):
                client.call("invalid_result", {})

    def test_transport_deadline_rejects_late_result(self):
        with self.client() as client, patch.object(mcp, "_CALL_TIMEOUT", .15):
            with self.assertRaisesRegex(RuntimeError, "timed out.*outcome.*unknown"):
                client.call("late", {})

    def assert_cancelled_promptly(self, run, error):
        start = time.monotonic()
        with self.assertRaises(type(error)) as caught:
            run()
        self.assertIs(caught.exception, error)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_already_cancelled_does_not_spawn(self):
        error = Cancelled()

        def check():
            raise error

        with patch.object(subprocess, "Popen") as spawn:
            self.assert_cancelled_promptly(self.client(check=check).__enter__, error)
        spawn.assert_not_called()

    def test_cancellation_during_startup_kills_stubborn_child(self):
        error = Cancelled()
        processes = []
        popen = subprocess.Popen
        startup_deadline = float("inf")

        def launch(argv, **kwargs):
            nonlocal startup_deadline
            child = popen(argv, **kwargs)
            if argv[0] == sys.executable:
                processes.append(child)
                startup_deadline = time.monotonic() + .25
            return child

        def check():
            if time.monotonic() >= startup_deadline:
                raise error

        with patch.object(subprocess, "Popen", side_effect=launch):
            self.assert_cancelled_promptly(self.client("stall-init", check).__enter__, error)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)
        self.assert_reaped(processes[0].pid)

    def test_cancellation_on_incomplete_frame_closes_even_if_caught_inside_context(self):
        error = Cancelled()
        deadline = float("inf")

        def check():
            if time.monotonic() >= deadline:
                raise error

        with self.client(check=check) as client:
            pid = client.call("structured", {})["pid"]
            deadline = time.monotonic() + .15
            self.assert_cancelled_promptly(lambda: client.call("stall_read", {}), error)
            self.assert_reaped(pid)

    def test_cancellation_when_child_does_not_read_stdin(self):
        error = OSError("caller cancellation must not become a transport error")
        deadline = float("inf")

        def check():
            if time.monotonic() >= deadline:
                raise error

        with self.client("stall-write", check) as client:
            deadline = time.monotonic() + .15
            self.assert_cancelled_promptly(lambda: client.call("unused", {"value": "x" * 1_000_000}), error)
