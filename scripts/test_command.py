"""Submit one local test command through the running HeyComputer panel."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time
import uuid


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", help="Command, or read it from standard input")
    parser.add_argument("--runtime", type=Path, default=Path.home() / "Library/Application Support/REAPER/Data/HeyComputer")
    parser.add_argument("--session", help="Panel session ID, required if multiple panels are open")
    parser.add_argument("--timeout", type=float, default=370)
    args = parser.parse_args()
    command = args.text if args.text is not None else sys.stdin.read()
    if not command.strip() or len(command.encode()) > 12000:
        parser.error("Provide a nonempty command of at most 12,000 UTF-8 bytes.")
    candidates = []
    for path in args.runtime.glob("*/test-state.json"):
        state = read(path)
        if (isinstance(state.get("updated"), (int, float)) and
                0 <= time.time() - state["updated"] < 3 and
                state.get("session") == path.parent.name and
                (args.session is None or args.session == state["session"])):
            candidates.append(path.parent)
    if len(candidates) != 1:
        parser.error("Open one updated HeyComputer panel, or select an active panel with --session.")
    directory = candidates[0]
    # Other CLI callers cannot replace a request while this one is waiting.
    with (directory / "test-command.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another test command is already waiting for a result.")
        if read(directory / "test-state.json").get("available") is not True:
            parser.error("Panel is busy, has a draft, or is awaiting script review.")
        job_id = uuid.uuid4().hex
        request = {"id": job_id, "session": directory.name, "created": int(time.time()), "text": command}
        temporary = directory / "test-command.json.tmp"
        temporary.write_text(json.dumps(request))
        os.replace(temporary, directory / "test-command.json")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            rejected = read(directory / "test-rejected.json")
            if rejected.get("id") == job_id:
                print(json.dumps(rejected, ensure_ascii=False))
                return 1
            result = read(directory / "test-state.json").get("last") or {}
            if result.get("id") == job_id and result.get("state") != "working":
                print(json.dumps(result, ensure_ascii=False))
                return 0 if result.get("state") == "finished" and result.get("status") in ("Done", "Reply") else 1
            time.sleep(.1)
        print("Timed out waiting. The request may still be running; check HeyComputer or use Cancel request.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
