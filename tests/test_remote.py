import http.client
from http.server import HTTPServer
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from heycomputer.remote import RemoteControl, RemoteError, atomic_json, local_address
from heycomputer.worker import Worker
from heycomputer.protocol import PlanError


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "session"
        self.directory.mkdir()
        self.remote = RemoteControl(self.directory)
        self.token = "a" * 64
        self.remote.token = self.token
        self.state = {"session": "session", "revision": 1, "can_start": True,
                      "can_stop": False, "can_cancel": False, "recording": False,
                      "busy": False, "status": "Ready", "message": ""}
        atomic_json(self.directory / "remote-state.json", self.state)

    def tearDown(self):
        self.remote.stop()
        self.temp.cleanup()

    def command(self, **changes):
        return dict({"id": "00000000-0000-0000-0000-000000000001", "action": "start",
                     "session": "session", "revision": 1}, **changes)

    def start_server(self):
        # Exercise the real listener lifecycle, binding loopback/ephemeral in tests.
        with patch("heycomputer.remote.HTTPServer", side_effect=lambda address, handler:
                   HTTPServer(("127.0.0.1", 0), handler)):
            self.remote.configure(True, 8765)
        self.assertIsNotNone(self.remote.server, self.remote.error)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.remote.server.server_port, timeout=4)
        try:
            merged = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
            merged.update(headers or {})
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=merged)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_http_requires_auth_and_rejects_browsers_and_arbitrary_jobs(self):
        self.start_server()
        self.assertEqual(self.request("GET", "/v1/status", headers={"Authorization": ""})[0], 401)
        self.assertEqual(self.request("GET", "/v1/status", headers={"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.request("GET", "/v1/status")[1]["status"], "Ready")
        self.assertEqual(self.request("POST", "/v1/command", self.command(action="plan"))[0], 400)
        self.assertEqual(self.request("POST", "/v1/command", ["not an object"])[0], 400)
        self.assertEqual(self.request("POST", "/v1/command", {"text": "x" * 3000})[0], 413)
        self.assertEqual(self.request("POST", "/v1/save_settings", {})[0], 404)
        self.assertEqual(self.request("GET", "/v1/status?token=abc")[0], 404)
        self.assertFalse((self.directory / "remote-command.json").exists())

    def test_http_queue_is_private_and_duplicate_does_not_replay(self):
        self.start_server()
        body = self.command()
        self.assertEqual(self.request("POST", "/v1/command", body)[0], 202)
        path = self.directory / "remote-command.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())["action"], "start")
        path.unlink()  # Simulate REAPER consuming it.
        self.assertEqual(self.request("POST", "/v1/command", body)[0], 202)
        self.assertFalse(path.exists())
        self.assertEqual(self.request("POST", "/v1/command", self.command(action="stop"))[0], 409)

    def test_stale_busy_and_conflicting_requests_never_queue(self):
        for body in [self.command(session="old"), self.command(revision=0), self.command(action="stop")]:
            with self.assertRaises(RemoteError) as error:
                self.remote.command(body)
            self.assertEqual(error.exception.status, 409)
        self.remote.command(self.command())
        with self.assertRaises(RemoteError):
            self.remote.command(self.command(id="00000000-0000-0000-0000-000000000002"))
        (self.directory / "remote-command.json").unlink()
        path = self.directory / "remote-state.json"
        os.utime(path, (time.time() - 10, time.time() - 10))
        with self.assertRaises(RemoteError) as error:
            self.remote.command(self.command(id="00000000-0000-0000-0000-000000000003"))
        self.assertEqual(error.exception.status, 503)
        self.assertFalse((self.directory / "remote-command.json").exists())

    def test_pairing_is_expiring_single_use_and_revokes_old_token(self):
        self.start_server()
        code = self.remote.begin_pairing()["code"]
        status, pair = self.request("POST", "/v1/pair", {"code": code}, {"Authorization": ""})
        self.assertEqual(status, 200)
        self.assertEqual(len(pair["token"]), 64)
        self.assertEqual(self.remote.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request("GET", "/v1/status")[0], 401)
        self.assertEqual(self.request("POST", "/v1/pair", {"code": code})[0], 403)
        self.token = pair["token"]
        self.assertEqual(self.request("GET", "/v1/status")[0], 200)
        self.remote.command(self.command())
        self.remote.revoke()
        self.assertEqual(self.request("GET", "/v1/status")[0], 401)
        self.assertFalse(self.remote.token_path.exists())
        self.assertFalse((self.directory / "remote-command.json").exists())
        code = self.remote.begin_pairing()["code"]
        self.remote.pair_until = time.monotonic() - 1
        with self.assertRaises(RemoteError):
            self.remote.pair({"code": code})

    def test_pairing_locks_after_five_wrong_attempts(self):
        self.start_server()
        code = self.remote.begin_pairing()["code"]
        for _ in range(5):
            with self.assertRaises(RemoteError):
                self.remote.pair({"code": "wrong"})
        with self.assertRaises(RemoteError):
            self.remote.pair({"code": code})

    def test_qr_carries_single_use_pairing_and_private_lan_address(self):
        self.start_server()
        import qrcode
        with patch("heycomputer.remote.lan_address", return_value="192.168.1.4"), \
             patch.object(qrcode.QRCode, "add_data", autospec=True, wraps=None) as encode:
            result = self.remote.begin_pairing()
        payload = encode.call_args.args[1]
        url = urlsplit(payload)
        self.assertEqual((url.scheme, url.netloc), ("heycomputer", "pair"))
        self.assertEqual(parse_qs(url.query), {"v": ["1"], "address": [result["remote"]["url"]], "code": [result["code"]]})
        self.assertTrue(result["remote"]["url"].startswith("http://192.168.1.4:"))
        self.assertNotIn(self.token, payload)
        # Real generation, including the four-module white border for scanning.
        result = self.remote.begin_pairing()
        rows = result["qr_rows"]
        self.assertTrue(all(len(row) == len(rows) and set(row) <= {"0", "1"} for row in rows))
        self.assertTrue(all(set(row) == {"0"} for row in rows[:4] + rows[-4:]))
        self.assertEqual(len(self.remote.pair({"code": result["code"]})["token"]), 64)

    def test_expired_phone_commands_never_execute(self):
        for value in (time.time() - 1, time.time() + 60, float("nan"), True, "tomorrow"):
            with self.assertRaises(RemoteError):
                self.remote.command(self.command(expires_at=value))
            self.assertFalse((self.directory / "remote-command.json").exists())
        self.remote.command(self.command(expires_at=time.time() + 5))
        self.assertTrue((self.directory / "remote-command.json").exists())

    def test_disabled_listener_and_shutdown_drop_queued_commands(self):
        self.assertFalse(self.remote.info()["running"])
        with self.assertRaises(PlanError):
            self.remote.begin_pairing()
        self.start_server()
        self.remote.command(self.command())
        thread = self.remote.thread
        self.remote.configure(False, 8765)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.remote.info()["running"])
        self.assertFalse((self.directory / "remote-command.json").exists())

    def test_only_local_peers_allowed(self):
        for address in ("127.0.0.1", "192.168.1.2", "10.0.0.4", "172.16.0.1", "169.254.1.1"):
            self.assertTrue(local_address(address))
        for address in ("8.8.8.8", "1.1.1.1", "172.32.0.1", "not an IP"):
            self.assertFalse(local_address(address))

    def test_worker_remote_settings_and_pair_dispatch(self):
        worker = Worker(self.directory)
        self.assertFalse(worker.config["remote_enabled"])
        with self.assertRaises(PlanError):
            worker.save_settings({"config": {"remote_port": 80}})
        with self.assertRaises(PlanError):
            worker.save_settings({"config": {"remote_enabled": "yes"}})
        with patch.object(worker.remote, "begin_pairing", return_value={"kind": "remote_pairing", "code": "123456"}):
            worker.dispatch({"id": "pair1", "kind": "remote_pair"})
            path = self.directory / "pair1.response.json"
            limit = time.monotonic() + 2
            while not path.exists() and time.monotonic() < limit:
                time.sleep(.01)
            self.assertEqual(json.loads(path.read_text())["code"], "123456")


if __name__ == "__main__":
    unittest.main()
