"""Exercise local submission with a fake panel, never a model or real REAPER."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("test_command_cli", Path(__file__).resolve().parents[1] / "scripts/test_command.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class CommandCliTests(unittest.TestCase):
    def test_submitted_text_and_session_reach_panel_and_result_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = Path(tmp) / "s1"
            panel.mkdir()
            state = {"session": "s1", "updated": time.time(), "available": True}
            (panel / "test-state.json").write_text(json.dumps(state))

            def respond(_):
                request = json.loads((panel / "test-command.json").read_text())
                self.assertEqual(request["session"], "s1")
                self.assertEqual(request["text"], "What is panning?")
                state["last"] = {"id": request["id"], "state": "finished", "status": "Reply", "message": "A test reply"}
                (panel / "test-state.json").write_text(json.dumps(state))

            output = io.StringIO()
            with patch.object(cli.sys, "argv", ["test_command.py", "--runtime", tmp]), \
                 patch.object(cli.sys, "stdin", io.StringIO("What is panning?")), \
                 patch.object(cli.time, "sleep", respond), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["message"], "A test reply")

    def test_busy_expired_and_ambiguous_panels_never_receive_command(self):
        for case in ("busy", "expired", "ambiguous"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                for session in (["s1", "s2"] if case == "ambiguous" else ["s1"]):
                    panel = Path(tmp) / session
                    panel.mkdir()
                    (panel / "test-state.json").write_text(json.dumps({
                        "session": session, "updated": time.time() - (10 if case == "expired" else 0),
                        "available": case != "busy"}))
                with patch.object(cli.sys, "argv", ["test_command.py", "--runtime", tmp, "Question"]), \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main()
                self.assertEqual(list(Path(tmp).glob("*/test-command.json")), [])
