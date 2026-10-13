import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from heycomputer.worker import Worker, Recorder, atomic_json


class WorkerTests(unittest.TestCase):
    def test_private_ipc_and_plan_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / "session")
            result = {"kind": "reply", "message": "Which input?", "actions": [], "lua": ""}
            with patch("heycomputer.worker.providers.plan", return_value=result) as provider:
                worker.dispatch({"id": "j1", "kind": "plan", "text": "arm it", "snapshot": {}, "history": []})
                response = worker.directory / "j1.response.json"
                deadline = time.monotonic() + 3
                while not response.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                data = json.loads(response.read_text())
                self.assertEqual(data["message"], "Which input?")
                self.assertEqual(data["transcript"], "arm it")
                self.assertEqual(response.stat().st_mode & 0o777, 0o600)
                self.assertEqual(worker.directory.stat().st_mode & 0o777, 0o700)
                provider.assert_called_once()

    def test_invalid_job_id_cannot_escape_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / "session")
            worker.dispatch({"id": "../escape", "kind": "plan"})
            self.assertFalse(list(Path(directory).rglob("*.json")))

    def test_config_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / "session")
            with patch("heycomputer.worker.providers.api_key", return_value=""), patch.object(Recorder, "devices", return_value=[]):
                worker.save_settings({"config": {"secret": "not-persisted", "fallback_model": "gpt-6-luna"}})
            saved = json.loads(worker.config_path.read_text())
            self.assertNotIn("secret", saved)
            self.assertEqual(saved["fallback_model"], "gpt-6-luna")

    def test_microphone_channel_is_selected_not_downmixed(self):
        try:
            import numpy as np
            import sounddevice
        except ImportError:
            self.skipTest("Install voice dependencies to run the audio fixture")
        recorder = Recorder()
        captured = {}
        class Stream:
            def __init__(self, **kwargs):
                captured.update(kwargs)
            def start(self):
                samples = np.zeros((8000, 2), dtype="float32")
                samples[:, 1] = .25
                captured["callback"](samples, len(samples), None, None)
            def stop(self): pass
            def close(self): pass
        with patch.object(Recorder, "devices", return_value=[{"id": "fixture", "index": 3}]), \
             patch.object(sounddevice, "query_devices", return_value={"default_samplerate": 16000, "max_input_channels": 2}), \
             patch.object(sounddevice, "InputStream", Stream):
            recorder.start({"mic_device": "fixture", "mic_channel": 2})
            wav = recorder.stop()
        with wave.open(io.BytesIO(wav)) as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getframerate(), 16000)
            sample = int.from_bytes(audio.readframes(1), "little", signed=True)
            self.assertAlmostEqual(sample, int(.25 * 32767), delta=1)
        self.assertEqual(captured["device"], 3)


if __name__ == "__main__":
    unittest.main()
