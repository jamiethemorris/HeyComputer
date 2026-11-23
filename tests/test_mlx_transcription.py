import io
from importlib.util import find_spec
import json
from pathlib import Path
import sys
import tempfile

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from heycomputer import providers
from heycomputer.protocol import PlanError
from heycomputer.settings import load_config, service_config
from heycomputer.worker import Worker, atomic_json


MODEL = "mlx-community/whisper-base.en-mlx"


def wav_fixture(rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(np.full(rate // 2, 8192, dtype="<i2").tobytes())
    return buf.getvalue()


class MlxTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config({"transcription_provider": "mlx_whisper", "speech_corrections": "ree splice = ReaSplice"})
        self.mlx = Mock(return_value={"text": "  open ree splice, execute. "})
        self.modules = patch.dict(sys.modules, {"mlx_whisper": SimpleNamespace(transcribe=self.mlx)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        for name in ("api_key", "request_json"):
            guard = patch.object(providers, name, side_effect=AssertionError("Unexpected cloud call"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_default_and_custom_model_without_endpoint(self):
        self.assertEqual(service_config(self.config, "transcription"), ("mlx_whisper", "", MODEL))
        self.config.update(transcription_model="mlx-community/whisper-small-mlx", transcription_endpoint="not a URL")
        self.assertEqual(providers.transcribe(wav_fixture(), self.config, ["Guitar 1"]), "open ree splice, execute.")
        args, options = self.mlx.call_args
        self.assertEqual(options["path_or_hf_repo"], self.config["transcription_model"])
        self.assertIn("ReaSplice", options["initial_prompt"])
        self.assertIn("Guitar 1", options["initial_prompt"])
        self.assertEqual(options["language"], "en")
        self.assertEqual(args[0].shape, (8000,))
        self.assertEqual(args[0].dtype, np.float32)
        np.testing.assert_array_equal(args[0], np.full(8000, 0.25, dtype=np.float32))

    @unittest.skipUnless(find_spec("scipy"), "resampling requires local-speech extra")
    def test_resamples_native_device_rate_in_memory(self):
        providers.transcribe(wav_fixture(48000), self.config, [])
        signal = self.mlx.call_args.args[0]
        self.assertEqual(signal.shape, (8000,))
        self.assertEqual(signal.dtype, np.float32)
        np.testing.assert_allclose(signal[100:-100], 0.25, atol=0.001)

    def test_missing_package_is_actionable(self):
        with patch.dict(sys.modules, {"mlx_whisper": None}):
            with self.assertRaisesRegex(PlanError, r"scripts/setup\.sh.*another transcription provider"):
                providers.transcribe(wav_fixture(), self.config, [])

    def test_model_failure_is_actionable(self):
        self.mlx.side_effect = FileNotFoundError("model missing")
        with self.assertRaisesRegex(PlanError, "model ID.*first download"):
            providers.transcribe(wav_fixture(), self.config, [])

    def test_no_speech_remains_an_error(self):
        self.mlx.return_value = {"text": "  "}
        with self.assertRaisesRegex(PlanError, "No speech was recognized"):
            providers.transcribe(wav_fixture(), self.config, [])

    def worker(self, folder):
        worker = Worker(Path(folder) / "session")
        worker.config.update(self.config)
        worker.speaker.stop = Mock()
        return worker

    def run_job(self, worker, **job):
        # Run dispatch synchronously but preserve the real dispatch and response path.
        def inline_thread(target, args, daemon):
            return SimpleNamespace(start=lambda: target(*args))
        with patch("heycomputer.worker.threading.Thread", side_effect=inline_thread):
            worker.dispatch({"id": "test", **job})
        return json.loads((worker.directory / "test.response.json").read_text())

    def test_save_and_reload_local_settings(self):
        with tempfile.TemporaryDirectory(dir="tests") as folder:
            worker = Worker(Path(folder) / "session")
            worker.configure_wake = Mock()
            worker.settings = Mock(return_value={"kind": "settings"})
            self.run_job(worker, kind="save_settings", config={"transcription_provider": "mlx_whisper"})
            reopened = Worker(worker.directory)
            self.assertEqual(service_config(reopened.config, "transcription"), ("mlx_whisper", "", MODEL))

    @patch.object(providers, "plan", return_value={"kind": "reply", "message": "fixture"})
    def test_submitted_talk_gets_corrected_and_stop_phrase_removed(self, plan):
        with tempfile.TemporaryDirectory(dir="tests") as folder:
            worker = self.worker(folder)
            worker.recorder.stop = Mock(return_value=wav_fixture())
            worker.recorder.record_id = "r1"
            result = self.run_job(worker, kind="record_stop", record_id="r1", stop_phrase="execute")
            self.assertEqual(result["transcript"], "open ReaSplice")
            self.assertEqual(plan.call_args.args[0], "open ree splice")

    @patch.object(providers, "plan")
    def test_cancel_after_submission_drops_late_result_before_planning(self, plan):
        with tempfile.TemporaryDirectory(dir="tests") as folder:
            worker = self.worker(folder)
            worker.recorder.stop = Mock(return_value=wav_fixture())
            def finish_after_cancel(*args, **kwargs):
                atomic_json(worker.directory / "visual-cancel.json", {"id": "test"})
                worker.poll_visual_cancel()
                return {"text": "late result"}
            self.mlx.side_effect = finish_after_cancel
            result = self.run_job(worker, kind="record_stop")
            self.assertIn("cancelled", result["message"])
            self.assertFalse((worker.directory / "transcript.json").exists())
            plan.assert_not_called()

    def test_silent_or_cancelled_capture_never_reaches_transcription(self):
        with tempfile.TemporaryDirectory(dir="tests") as folder:
            worker = self.worker(folder)
            # Use the real Recorder.stop with no captured chunks or live device.
            worker.recorder._recording = True
            self.assertIn("silent", self.run_job(worker, kind="record_stop")["message"])
            worker.recorder._recording = True
            self.assertEqual(self.run_job(worker, kind="record_cancel")["kind"], "cancelled")
            self.mlx.assert_not_called()
