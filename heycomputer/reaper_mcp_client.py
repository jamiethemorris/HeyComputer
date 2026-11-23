"""Synchronous stdio transport for the pinned local ReaperMCP server."""

import json
import math
import operator
import os
import select
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

DEFAULT_COMMAND = "uvx --python 3.13 xdarkzx-reaper-mcp==0.8.3"
_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_POLL = .05
_DISCOVERY_TIMEOUT = 30
_CALL_TIMEOUT = 610  # The pinned bridge allows 600 seconds for long commands.
_MAX_FRAME = 16 * 1024 * 1024
_BRIDGE_HELP = ("In REAPER, load and run reaper_mcp_server.lua from Actions > Show action list. "
                "If it is already running, check that REAPER and ReaperMCP use the same TMPDIR.")
_UNKNOWN_OUTCOME = "The tool outcome is unknown; check REAPER before trying again."

_SCHEMA_TYPES = {
    "object": (dict,), "array": (list,), "string": (str,),
    "integer": (int,), "number": (int, float), "boolean": (bool,), "null": (type(None),),
}
_SCHEMA_METADATA = {"title", "description", "default"}
_NUMERIC_BOUNDS = {"minimum": operator.ge, "maximum": operator.le,
                   "exclusiveMinimum": operator.gt, "exclusiveMaximum": operator.lt}
_LENGTH_BOUNDS = {
    "string": {"minLength": operator.ge, "maxLength": operator.le},
    "array": {"minItems": operator.ge, "maxItems": operator.le},
}


def _finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _supported_node(schema):
    if type(schema) is not dict:
        return False
    if "anyOf" in schema:
        choices = schema["anyOf"]
        # Pinned nullable arguments use a union with annotations only.
        return (not (schema.keys() - _SCHEMA_METADATA - {"anyOf"})
                and type(choices) is list and bool(choices) and all(map(_supported_node, choices)))
    kind = schema.get("type")
    if type(kind) is not str or kind not in _SCHEMA_TYPES:
        return False
    keywords = _SCHEMA_METADATA | {"type", "enum"}
    lengths = _LENGTH_BOUNDS.get(kind, {})
    keywords.update(lengths)
    if any(type(schema[k]) is not int or schema[k] < 0 for k in schema.keys() & lengths.keys()):
        return False
    if kind in ("integer", "number"):
        keywords.update(_NUMERIC_BOUNDS)
        if any(not _finite_number(schema[k]) for k in schema.keys() & _NUMERIC_BOUNDS.keys()):
            return False
    if kind == "object":
        keywords |= {"properties", "required", "additionalProperties"}
    elif kind == "array":
        keywords.add("items")
    if schema.keys() - keywords:
        return False
    if "enum" in schema:
        choices = schema["enum"]
        if (type(choices) is not list or not choices or not all(
                type(choice) in (str, bool, type(None)) or _finite_number(choice) for choice in choices)):
            return False
    if kind == "object":
        props, required = schema.get("properties", {}), schema.get("required", [])
        return (type(schema.get("additionalProperties", False)) is bool
                and type(props) is dict and type(required) is list
                and all(type(key) is str and key in props for key in required)
                and all(type(key) is str and _supported_node(child) for key, child in props.items()))
    if kind == "array":
        return _supported_node(schema.get("items"))
    return True


def supported_schema(schema) -> bool:
    """Whether the entire published input schema can be enforced locally."""
    try:
        return type(schema) is dict and schema.get("type") == "object" and _supported_node(schema)
    except RecursionError:
        return False


def _validate_node(schema: dict, value: Any, path: str):
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            with suppress(ValueError):
                _validate_node(choice, value, path)
                return
        raise ValueError(f"Argument at {path} matches no allowed schema.")
    kind = schema["type"]
    if type(value) not in _SCHEMA_TYPES[kind] or (kind == "number" and not _finite_number(value)):
        raise ValueError(f"Expected {kind} at {path}.")
    if "enum" in schema and not any(
            value == choice and (type(value) is type(choice) or
                                 (_finite_number(value) and _finite_number(choice)))
            for choice in schema["enum"]):
        raise ValueError(f"Invalid enum value at {path}.")
    bounds = _NUMERIC_BOUNDS if kind in ("integer", "number") else _LENGTH_BOUNDS.get(kind, {})
    if bounds:
        measured: Any = len(value) if kind in _LENGTH_BOUNDS else value
        for key, meets in bounds.items():
            if key in schema and not meets(measured, schema[key]):
                raise ValueError(f"Argument at {path} violates {key}.")
    if kind == "object":
        props = schema.get("properties", {})
        # MCP arguments are closed even when the published schema permits extras.
        if value.keys() - props.keys():
            raise ValueError(f"Unknown argument at {path}.")
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"Missing required argument at {path}.")
        for key, child in value.items():
            _validate_node(props[key], child, f"{path}.{key}")
    elif kind == "array":
        for index, item in enumerate(value):
            _validate_node(schema["items"], item, f"{path}[{index}]")


def validate_schema(schema, args) -> None:
    """Raise ValueError for unsupported schemas or invalid tool arguments."""
    if not supported_schema(schema):
        raise ValueError("Unsupported MCP input schema.")
    try:
        _validate_node(schema, args, "arguments")
    except RecursionError:
        raise ValueError("MCP arguments are too deeply nested.") from None


def _child_env():
    env = dict(os.environ, REAPER_MCP_NO_SUPERSEDE="1")
    # A Dock-launched REAPER passes only the system PATH, which lacks uvx. Append
    # the usual install folders; inherited entries keep priority.
    path = [p for p in env.get("PATH", "").split(":") if p]
    for folder in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin"),
                   os.path.expanduser("~/.cargo/bin")):
        if folder not in path:
            path.append(folder)
    env["PATH"] = ":".join(path)
    if sys.platform == "darwin":
        # A GUI-launched REAPER normally uses the macOS per-user temp directory,
        # not a TMPDIR override inherited by the HeyComputer worker.
        try:
            temp = subprocess.check_output(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
                                           timeout=1, text=True).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Could not find macOS DARWIN_USER_TEMP_DIR for ReaperMCP.") from exc
        if not os.path.isabs(temp) or not os.path.isdir(temp):
            raise RuntimeError("macOS DARWIN_USER_TEMP_DIR is not an accessible directory for ReaperMCP.")
        env["TMPDIR"] = temp
    return env


def _remote_error(operation, detail):
    detail = str(detail)[:2000]
    if any(marker in detail for marker in (
        "[CONNECTION_", "[SEND_FAILED (1004)]", "[RECV_FAILED (1005)]", "[COMMAND_TIMEOUT (2002)]",
        "REAPER MCP server not running", "REAPER MCP server appears stale", "REAPER MCP server stopped",
    )):
        detail = f"{detail} {_BRIDGE_HELP}"
    return RuntimeError(f"ReaperMCP {operation}: {detail}")


class ReaperMCPClient:
    """One stdio session for a trusted local command; use from one thread.

    A raising check() closes the session and propagates unchanged. Closing the
    transport cannot undo or stop a tool already dispatched to REAPER.
    """

    def __init__(self, command: str, check: Callable[[], None]):
        self._command = command
        self._check = check
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self):
        if self._process is not None:
            raise RuntimeError("ReaperMCP session is already open.")
        self._check()
        if os.name != "posix":
            raise RuntimeError("The ReaperMCP stdio adapter requires POSIX pipes.")
        try:
            argv = shlex.split(self._command)
            if not argv:
                raise ValueError("empty command")
            env = _child_env()
        except ValueError as exc:
            raise RuntimeError(f"Invalid ReaperMCP command: {exc}") from exc
        self._check()
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, env=env, start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not start ReaperMCP ({argv[0]!r}): {exc}. "
                               "Make sure uvx is installed or use an absolute path in the command.") from exc
        self._process = process
        self._id = 0
        self._stdout = bytearray()
        self._stderr = b""
        self._stderr_open = True
        try:
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None
            self._in_fd, self._out_fd, self._err_fd = (p.fileno() for p in (process.stdin, process.stdout, process.stderr))
            for fd in (self._in_fd, self._out_fd, self._err_fd):
                os.set_blocking(fd, False)
            deadline = time.monotonic() + _DISCOVERY_TIMEOUT
            result = self._request("initialize", {
                "protocolVersion": _PROTOCOLS[0], "capabilities": {},
                "clientInfo": {"name": "HeyComputer", "version": "0.1.0"},
            }, deadline)
            version = result.get("protocolVersion")
            if version not in _PROTOCOLS:
                raise RuntimeError(f"Unsupported ReaperMCP protocol version: {version!r}.")
            self._send({"method": "notifications/initialized"}, "initialize", deadline)
            self._poll("initialize", deadline)
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        process, self._process = self._process, None
        if process is None:
            return
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        try:
            process.stdin.close()
            try:
                process.wait(timeout=.15)
            except subprocess.TimeoutExpired:
                # uvx may be a launcher; signal only our private process group.
                # macOS can return EPERM for a group whose members already exited.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    with suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, sig)
                    if sig == signal.SIGTERM:
                        time.sleep(.15)  # Reserve the leader's PID until escalation.
                process.wait(timeout=.5)
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            if exc is None:
                raise RuntimeError(f"Could not stop and reap ReaperMCP: {cleanup_error}") from cleanup_error
            exc.add_note(f"Could not stop and reap ReaperMCP: {cleanup_error}")
        finally:
            process.stdout.close()
            process.stderr.close()

    def _poll(self, operation, deadline):
        self._check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            message = f"ReaperMCP {operation} timed out."
            if operation == "tools/call":
                message += f" {_UNKNOWN_OUTCOME} {_BRIDGE_HELP}"
            raise RuntimeError(message)
        return min(remaining, _POLL)

    def _drain_stderr(self):
        if self._stderr_open:
            try:
                chunk = os.read(self._err_fd, 65536)
            except BlockingIOError:
                return
            self._stderr_open = bool(chunk)
            self._stderr = (self._stderr + chunk)[-4096:]

    def _ready(self, operation, deadline, *, writing=False):
        target = self._in_fd if writing else self._out_fd
        stderr = self._err_fd
        while True:
            timeout = self._poll(operation, deadline)
            readers = [stderr] if self._stderr_open else []
            if not writing:
                readers.append(target)
            try:
                readable, writable, _ = select.select(readers, [target] if writing else [], [], timeout)
                if stderr in readable:
                    self._drain_stderr()
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"ReaperMCP {operation} pipe failed: {exc}") from exc
            if target in readable or target in writable:
                self._poll(operation, deadline)
                return target

    def _disconnected(self, operation):
        with suppress(OSError):
            self._drain_stderr()
        detail = self._stderr.decode("utf-8", errors="replace").strip()
        message = (f"ReaperMCP disconnected during {operation}. "
                   "Check the configured command and the ReaperMCP installation.")
        if operation == "tools/call":
            message += f" {_UNKNOWN_OUTCOME}"
        return RuntimeError(f"{message} Stderr: {detail}" if detail else message)

    def _send(self, message, operation, deadline):
        payload = memoryview((json.dumps({"jsonrpc": "2.0", **message}, allow_nan=False) + "\n").encode("utf-8"))
        while payload:
            fd = self._ready(operation, deadline, writing=True)
            try:
                payload = payload[os.write(fd, payload[:65536]):]
            except BlockingIOError:
                continue
            except OSError as exc:
                raise self._disconnected(operation) from exc

    def _receive(self, operation, deadline):
        while True:
            self._poll(operation, deadline)
            end = self._stdout.find(b"\n")
            if end >= 0:
                line = self._stdout[:end]
                del self._stdout[:end + 1]
                try:
                    message = json.loads(line.decode("utf-8"))
                except (ValueError, RecursionError) as exc:
                    raise RuntimeError(f"Invalid ReaperMCP JSON-RPC frame during {operation}.") from exc
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise RuntimeError(f"Invalid ReaperMCP JSON-RPC envelope during {operation}.")
                return message
            if len(self._stdout) >= _MAX_FRAME:
                raise RuntimeError(f"ReaperMCP JSON-RPC frame is too large during {operation}.")
            fd = self._ready(operation, deadline)
            try:
                chunk = os.read(fd, min(65536, _MAX_FRAME - len(self._stdout)))
            except BlockingIOError:
                continue
            except OSError as exc:
                raise self._disconnected(operation) from exc
            if not chunk:
                raise self._disconnected(operation)
            self._stdout.extend(chunk)

    def _request(self, method, params, deadline):
        if self._process is None:
            raise RuntimeError("ReaperMCP session is not open.")
        self._id += 1
        request_id = self._id
        try:
            self._poll(method, deadline)
            self._send(dict(id=request_id, method=method, params=params), method, deadline)
            while True:
                reply = self._receive(method, deadline)
                if type(reply.get("id")) is int and reply["id"] == request_id:
                    break
            self._poll(method, deadline)
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        if "error" in reply:
            error = reply["error"]
            raise _remote_error(method, error.get("message", error) if isinstance(error, dict) else error)
        result = reply.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Invalid ReaperMCP result for {method}.")
        return result

    def list_tools(self) -> list[dict]:
        deadline = time.monotonic() + _DISCOVERY_TIMEOUT
        tools, seen = [], set()
        params = {}
        while True:
            page = self._request("tools/list", params, deadline)
            batch = page.get("tools")
            if not isinstance(batch, list) or not all(isinstance(tool, dict) for tool in batch):
                raise RuntimeError("Invalid ReaperMCP tools/list result.")
            tools.extend(batch)
            cursor = page.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                raise RuntimeError("Invalid or repeated ReaperMCP tools/list cursor.")
            seen.add(cursor)
            params = {"cursor": cursor}

    def call(self, name: str, arguments: dict) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments},
                               time.monotonic() + _CALL_TIMEOUT)
        content = result.get("content", [])
        data = result.get("structuredContent")
        is_error = result.get("isError", False)
        if (not isinstance(content, list) or not isinstance(is_error, bool)
                or (data is not None and not isinstance(data, dict))
                or (data is None and "content" not in result and not is_error)):
            raise RuntimeError(f"Invalid ReaperMCP tool result for {name}.")
        texts = [item["text"] for item in content if isinstance(item, dict)
                 and item.get("type") == "text" and isinstance(item.get("text"), str)]
        if is_error:
            raise _remote_error(name, "\n".join(texts) or data or "tool reported an error")
        if data is None:
            data = content
            if len(content) == len(texts) == 1:
                data = texts[0]
                with suppress(json.JSONDecodeError):
                    data = json.loads(data)
        if isinstance(data, dict) and data.get("success") is False:
            raise _remote_error(name, data.get("error") or "tool reported failure")
        return data
