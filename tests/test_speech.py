import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from heycomputer.protocol import PlanError
from heycomputer.speech import correct_text, correction_pairs
from heycomputer.worker import Worker, Recorder


class SpeechTests(unittest.TestCase):
    def test_whole_phrases_case_whitespace_and_literal_punctuation(self):
        config = {"speech_corrections": "ree splice = ReaSplice\npro q = Pro-Q 4\nA+B = Sum"}
        self.assertEqual(correct_text("open REE   SPLICE and pro q with A+B", config),
                         "open ReaSplice and Pro-Q 4 with Sum")
        self.assertEqual(correct_text("free splice and pro quality", config), "free splice and pro quality")

    def test_longest_match_and_no_recursive_replacements(self):
        config = {"speech_corrections": "ree = re\nree splice => ReaSplice\nReaSplice = something else"}
        self.assertEqual(correct_text("open ree splice", config), "open ReaSplice")

    def test_invalid_definitions_are_rejected_before_save(self):
        for value in (None, "word", "= blank", "blank =", "A = B\na = C", "a" * 8001,
                      "\n".join(f"word{i} = correct" for i in range(129))):
            with self.subTest(value=value), self.assertRaises(PlanError):
                correction_pairs(value)

    @patch.object(Recorder, "devices", return_value=[])
    @patch("heycomputer.providers.api_key", return_value="")
    def test_corrections_persist_and_invalid_save_keeps_previous_settings(self, key, devices):
        with tempfile.TemporaryDirectory() as folder:
            w = Worker(Path(folder) / "s1")
            w.save_settings({"config": {"speech_corrections": "ReSplice = ReaSplice"}})
            self.assertEqual(Worker(Path(folder) / "s2").config["speech_corrections"], "ReSplice = ReaSplice")
            with self.assertRaises(PlanError):
                w.save_settings({"config": {"speech_corrections": "broken"}})
            self.assertEqual(w.config["speech_corrections"], "ReSplice = ReaSplice")

    @patch.object(Recorder, "stop", return_value=b"audio")
    @patch("heycomputer.providers.transcribe", return_value="open ree splice")
    @patch("heycomputer.providers.api_key", side_effect=AssertionError("Exact launch needs no API"))
    def test_voice_transcript_correction_and_launch_are_one_pass(self, key, transcribe, stop):
        with tempfile.TemporaryDirectory() as folder:
            w = Worker(Path(folder) / "session")
            w.config["speech_corrections"] = "ree splice = ReaSplice\nReaSplice = Wrong"
            snapshot = {"timeline": {}, "launch_actions": [{"id": "_RSfixture", "name": "Script: ReaSplice.lua"}]}
            w.dispatch({"id": "j1", "kind": "record_stop", "snapshot": snapshot})
            path = w.directory / "j1.response.json"
            deadline = time.monotonic() + 3
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            result = json.loads(path.read_text())
            self.assertEqual(result["transcript"], "open ReaSplice")
            self.assertEqual(result["actions"][0]["value"], "_RSfixture")
            self.assertEqual(json.loads((w.directory / "transcript.json").read_text())["text"], "open ReaSplice")
