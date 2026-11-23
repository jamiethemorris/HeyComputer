import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from heycomputer import settings
from heycomputer.reaper_mcp_client import DEFAULT_COMMAND
from heycomputer.worker import Worker


class MCPSettingsTests(unittest.TestCase):
    def test_fresh_and_legacy_config_default_to_off(self):
        for saved in (None, {}, {"fallback_model": "legacy-model"}):
            with self.subTest(saved=saved):
                self.assertIs(settings.load_config(saved)["reaper_mcp_enabled"], False)

    def test_non_boolean_saved_values_do_not_enable_mcp(self):
        for value in ("true", 1, None, []):
            with self.subTest(value=value):
                config = settings.load_config({"reaper_mcp_enabled": value})
                self.assertIs(config["reaper_mcp_enabled"], False)

    @patch.dict(os.environ, {}, clear=True)
    def test_command_comes_only_from_the_local_process_environment(self):
        self.assertEqual(settings.reaper_mcp_command(), DEFAULT_COMMAND)
        override = '"/fixture/Local Tools/reaper-mcp" --stdio'
        with patch.dict(os.environ, {"HEYCOMPUTER_REAPER_MCP_COMMAND": override}):
            self.assertEqual(settings.reaper_mcp_command(), override)
            self.assertNotIn("reaper_mcp_command", settings.load_config({"reaper_mcp_command": "untrusted"}))

    @patch("heycomputer.worker.providers.api_key", return_value="")
    @patch("heycomputer.worker.VoiceOutput")
    @patch("heycomputer.worker.Recorder")
    @patch.dict(os.environ, {"HEYCOMPUTER_REAPER_MCP_COMMAND": "/fixture/local-mcp"})
    def test_panel_save_persists_toggle_but_not_command(self, *_):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "session"
            worker = Worker(directory)
            self.assertIs(worker.config["reaper_mcp_enabled"], False)
            for enabled in (True, False):
                with self.subTest(enabled=enabled):
                    result = worker.save_settings({"config": {
                        "reaper_mcp_enabled": enabled, "reaper_mcp_command": "untrusted",
                    }})
                    saved = json.loads(worker.config_path.read_text())
                    worker = Worker(directory)
                    for config in (result["config"], saved, worker.config):
                        self.assertIs(config["reaper_mcp_enabled"], enabled)
                        self.assertNotIn("reaper_mcp_command", config)
