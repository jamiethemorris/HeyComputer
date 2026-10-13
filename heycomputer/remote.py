"""Opt-in, paired LAN remote. REAPER remains the sole owner of UI/project state.

This first version uses HTTP on a trusted local network, not an internet service.
Only three explicit recording actions cross this bridge; no arbitrary jobs do.
"""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from urllib.parse import urlencode

import qrcode

from .protocol import PlanError


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as output:
        os.chmod(temp, 0o600)
        json.dump(value, output)
    temp.replace(path)


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def local_address(address):
    try:
        ip = ipaddress.ip_address(address)
        return ip.is_loopback or ip.is_link_local or any(ip in network for network in (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16")))
    except ValueError:
        return False


def lan_address():
    """Prefer the active physical LAN interface, never an internet/VPN address."""
    for interface in ("en0", "en1", "en2"):
        try:
            address = subprocess.check_output(["/usr/sbin/ipconfig", "getifaddr", interface],
                                              text=True, timeout=1, stderr=subprocess.DEVNULL).strip()
            if local_address(address) and not ipaddress.ip_address(address).is_loopback:
                return address
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


class RemoteError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class RemoteControl:
    def __init__(self, directory):
        self.directory = directory
        self.token_path = directory.parent / "remote-token.json"
        self.token = read_json(self.token_path).get("token", "")
        if not isinstance(self.token, str) or not re.fullmatch(r"[0-9a-f]{64}", self.token):
            self.token = ""
        self.lock = threading.RLock()
        self.server = self.thread = None
        self.error = ""
        self.code = ""
        self.pair_until = 0
        self.attempts = 0
        self.seen = OrderedDict()
        self.hostname = socket.gethostname().split(".")[0] + ".local"
        try:
            host = subprocess.check_output(["/usr/sbin/scutil", "--get", "LocalHostName"],
                                           text=True, timeout=2).strip()
            if host:
                self.hostname = host + ".local"
        except (OSError, subprocess.SubprocessError):
            pass

    def info(self):
        with self.lock:
            port = self.server.server_port if self.server else None
            return {"running": self.server is not None, "error": self.error,
                    "url": f"http://{self.hostname}:{port}" if port else ""}

    def configure(self, enabled, port):
        if type(port) is not int or not 1024 <= port <= 65535:
            self.error = "Remote port must be between 1024 and 65535."
            return
        if self.server and (not enabled or self.server.server_port != port):
            self.stop()
        if not enabled or self.server:
            return
        self.error = ""
        try:
            server = HTTPServer(("0.0.0.0", port), self.handler())
        except OSError:
            self.error = "Remote control could not open its port. Choose another port and save."
            return
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever,
                                       kwargs={"poll_interval": .1}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
        with self.lock:
            self.server = self.thread = None
            self.code = ""
            self.seen.clear()
            (self.directory / "remote-command.json").unlink(missing_ok=True)

    def begin_pairing(self):
        with self.lock:
            if not self.server:
                raise PlanError("Enable remote control and save settings first.")
            self.code = f"{secrets.randbelow(1000000):06d}"
            self.pair_until = time.monotonic() + 300
            self.attempts = 0
            info = self.info()
            address = lan_address()
            if address:
                info["url"] = f"http://{address}:{self.server.server_port}"
            payload = "heycomputer://pair?" + urlencode({"v": "1", "address": info["url"], "code": self.code})
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
            qr.add_data(payload)
            qr.make(fit=True)
            # Render locally as squares in ReaImGui. No image files or web service.
            rows = ["".join("1" if cell else "0" for cell in row) for row in qr.get_matrix()]
            return {"kind": "remote_pairing", "code": self.code, "expires_in": 300,
                    "remote": info, "qr_rows": rows}

    def pair(self, body):
        with self.lock:
            if not self.code or time.monotonic() >= self.pair_until or self.attempts >= 5:
                raise RemoteError(403, "Pairing closed. Get a new code in HeyComputer Settings.")
            self.attempts += 1
            code = body.get("code")
            if not isinstance(code, str) or not secrets.compare_digest(code.encode(), self.code.encode()):
                raise RemoteError(403, "Incorrect pairing code.")
            token = secrets.token_hex(32)
            atomic_json(self.token_path, {"token": token})
            self.token, self.code = token, ""
            self.seen.clear()
            (self.directory / "remote-command.json").unlink(missing_ok=True)
            return {"token": token}

    def revoke(self):
        with self.lock:
            self.token_path.unlink(missing_ok=True)
            self.token, self.code = "", ""
            self.seen.clear()
            (self.directory / "remote-command.json").unlink(missing_ok=True)

    def authorize(self, header):
        with self.lock:
            if not self.token or not secrets.compare_digest(
                    (header or "").encode(), ("Bearer " + self.token).encode()):
                raise RemoteError(401, "Pair your iPhone with HeyComputer again.")

    def status(self):
        path = self.directory / "remote-state.json"
        state = read_json(path)
        try:
            fresh = 0 <= time.time() - path.stat().st_mtime < 3
        except OSError:
            fresh = False
        if not fresh or not state.get("session"):
            raise RemoteError(503, "HeyComputer is unavailable. Open its panel on the Mac.")
        return state

    def command(self, body):
        with self.lock:
            command_id, action = body.get("id"), body.get("action")
            if (not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", command_id)
                    or action not in ("start", "stop", "cancel")
                    or type(body.get("revision")) is not int or not isinstance(body.get("session"), str)):
                raise RemoteError(400, "Invalid recording command.")
            fingerprint = (action, body["session"], body["revision"])
            expires = body.get("expires_at")
            if expires is not None and (type(expires) not in (int, float) or not math.isfinite(expires)
                    or not time.time() < expires <= time.time() + 10):
                raise RemoteError(409, "Recording request expired. Refresh and try again.")
            if command_id in self.seen:
                if self.seen[command_id] != fingerprint:
                    raise RemoteError(409, "Request ID already used.")
                return {"id": command_id, "queued": True}
            state = self.status()
            if body["session"] != state["session"] or body["revision"] != state["revision"]:
                raise RemoteError(409, "HeyComputer changed. Refresh and try again.")
            if not state.get("can_" + action):
                raise RemoteError(409, "That control is unavailable while HeyComputer is busy.")
            path = self.directory / "remote-command.json"
            if path.exists():
                raise RemoteError(409, "A remote request is awaiting confirmation.")
            atomic_json(path, {"id": command_id, "action": action, "session": state["session"],
                               "revision": state["revision"], "created": int(time.time())})
            self.seen[command_id] = fingerprint
            while len(self.seen) > 256:
                self.seen.popitem(last=False)
            return {"id": command_id, "queued": True}

    def handler(self):
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *args):
                pass  # Never log pairing codes, credentials or conversation text.

            def respond(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                if self.headers.get("Transfer-Encoding"):
                    raise RemoteError(400, "Chunked requests are unsupported.")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise RemoteError(400, "Invalid request length.")
                if not 0 < length <= 2048:
                    raise RemoteError(413, "Request must be under 2 KB.")
                if self.headers.get_content_type() != "application/json":
                    raise RemoteError(415, "Expected JSON.")
                try:
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
                except (ValueError, UnicodeError):
                    raise RemoteError(400, "Invalid JSON.")

            def route(self):
                try:
                    if not local_address(self.client_address[0]) or self.headers.get("Origin"):
                        raise RemoteError(403, "Only paired local apps are supported.")
                    # Keep credential checks and actions atomic with revocation/pairing.
                    with remote.lock:
                        if self.command == "POST" and self.path == "/v1/pair":
                            self.respond(200, remote.pair(self.body()))
                            return
                        remote.authorize(self.headers.get("Authorization"))
                        if self.command == "GET" and self.path == "/v1/status":
                            self.respond(200, remote.status())
                        elif self.command == "POST" and self.path == "/v1/command":
                            self.respond(202, remote.command(self.body()))
                        else:
                            raise RemoteError(404, "Unknown remote endpoint.")
                except RemoteError as exc:
                    self.respond(exc.status, {"message": exc.message})
                except (OSError, TimeoutError):
                    self.close_connection = True

            do_GET = do_POST = route

        return Handler
