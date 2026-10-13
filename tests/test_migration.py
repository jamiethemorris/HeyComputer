import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from heycomputer.migration import import_legacy_settings
from heycomputer.providers import api_key
from heycomputer.worker import Worker


class RenameTests(unittest.TestCase):
    def test_imports_settings_pairing_and_memory_without_replaying_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy, current = (Path(folder) / name for name in ("ReaJev", "HeyComputer"))
            legacy.mkdir()
            saved = {"mic_device": "Core Audio: Guitar 1", "mic_channel": 2,
                     "speech_corrections": "ree splice = ReaSplice"}
            files = {"settings.json": saved, "remote-token.json": {"token": "a" * 64},
                     "ui-memory.json": {"notes": ["fixture"]}}
            for name, value in files.items():
                (legacy / name).write_text(json.dumps(value))
            (legacy / "old-session").mkdir()
            (legacy / "old-session" / "j1.request.json").write_text('{"kind":"record_start"}')
            worker = Worker(current / "new-session")
            self.assertEqual(worker.config["mic_device"], saved["mic_device"])
            self.assertEqual(worker.config["mic_channel"], 2)
            self.assertEqual(worker.config["speech_corrections"], saved["speech_corrections"])
            self.assertEqual(worker.remote.token, "a" * 64)
            for name, value in files.items():
                self.assertEqual(json.loads((current / name).read_text()), value)
                self.assertEqual(json.loads((legacy / name).read_text()), value)
                self.assertEqual((current / name).stat().st_mode & 0o777, 0o600)
            self.assertFalse((current / "old-session").exists())

    def test_current_data_wins_on_every_subsequent_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy, current = (Path(folder) / name for name in ("ReaJev", "HeyComputer"))
            legacy.mkdir(); current.mkdir()
            (legacy / "settings.json").write_text('{"mic_channel":1}')
            (current / "settings.json").write_text('{"mic_channel":4}')
            import_legacy_settings(current)
            import_legacy_settings(current)
            self.assertEqual(json.loads((current / "settings.json").read_text()), {"mic_channel": 4})
            self.assertEqual(list(current.glob(".migration-*")), [])

    @patch.dict(os.environ, {}, clear=True)
    @patch("keyring.get_password", side_effect=[None, "old-key-fixture"])
    def test_saved_key_from_old_name_remains_available(self, lookup):
        self.assertEqual(api_key("openrouter"), "old-key-fixture")
        self.assertEqual(lookup.call_args_list, [call("HeyComputer", "openrouter"), call("ReaJev", "openrouter")])

    @patch.dict(os.environ, {}, clear=True)
    @patch("keyring.get_password", return_value="new-key-fixture")
    def test_newly_saved_key_takes_precedence(self, lookup):
        self.assertEqual(api_key("openrouter"), "new-key-fixture")
        lookup.assert_called_once_with("HeyComputer", "openrouter")
