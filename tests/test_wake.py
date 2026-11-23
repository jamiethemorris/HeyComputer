import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from heycomputer.audio import Recorder
from heycomputer.protocol import PlanError
from heycomputer.settings import DEFAULTS
from heycomputer.wake import ActivationLog, WakeListener, phrases, strip_stop_phrase
from heycomputer.worker import Worker, atomic_json


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        time.sleep(.01)
    assert predicate(), "Timed out waiting for fixture"


class FakeRecorder:
    stream = None
    record_id = ""
    problem = ""
    started = 0.0
    def listen(self, config, observer): self.observer = observer
    def unlisten(self): self.observer = None


class FakeDetector:
    def __init__(self, directory, config): self.mode = None
    def reset(self, mode): self.mode = mode
    def feed(self, rate, samples): return samples == self.mode


class WakeTests(unittest.TestCase):
    def test_phrase_validation_and_stop_removal_only_at_end(self):
        self.assertEqual(phrases(DEFAULTS), ("hey computer", "execute"))
        for changes in ({"wake_phrase": ""}, {"wake_phrase": "@wake"},
                        {"wake_stop_phrase": "hey computer"}, {"wake_threshold": float("nan")},
                        {"wake_threshold": True}, {"wake_enabled": "true"}):
            with self.subTest(changes=changes), self.assertRaises(PlanError):
                phrases(dict(DEFAULTS, **changes))
        self.assertEqual(strip_stop_phrase("make a track. EXECUTE!", "execute"), "make a track.")
        self.assertEqual(strip_stop_phrase("execute this action", "execute"), "execute this action")
        self.assertEqual(strip_stop_phrase("make a bus, send it.", "send it"), "make a bus")
        self.assertEqual(strip_stop_phrase("execute", "execute"), "")
        self.assertEqual(strip_stop_phrase("reexecute", "execute"), "reexecute")

    def test_log_is_private_bounded_and_contains_only_allowed_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            log = ActivationLog(folder)
            log.LIMIT = 150
            for i in range(15):
                log.add(str(i), "transcribed", transcript="a test command", api_key="secret", audio="not saved")
            rows = log.read()
            self.assertEqual(rows[0]["activation"], "14")
            self.assertLess(len(rows), 15)
            self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("secret", log.path.read_text())
            self.assertNotIn("audio", log.path.read_text())
            log.clear()
            self.assertEqual(log.read(), [])

    def fixture(self, folder):
        directory = Path(folder) / "session"
        directory.mkdir()
        recorder = FakeRecorder()
        config = dict(DEFAULTS, wake_enabled=True)
        log = ActivationLog(folder)
        listener = WakeListener(directory, recorder, log, config, atomic_json, FakeDetector)
        self.addCleanup(listener.close)
        wait_for(lambda: listener.ready)
        state = {"session": "session", "enabled": True, "revision": 1, "record_id": "",
                 "can_start": True, "can_stop": False, "can_cancel": False}
        atomic_json(directory / "wake-state.json", state)
        listener.poll()
        return directory, recorder, listener, log, state

    def test_wake_and_stop_are_mode_bound_and_do_not_upload(self):
        with tempfile.TemporaryDirectory() as folder, patch("heycomputer.providers.transcribe", side_effect=AssertionError("No upload")):
            directory, recorder, listener, log, state = self.fixture(folder)
            listener.feed(16000, "stop")
            time.sleep(.04)
            listener.poll()
            self.assertFalse((directory / "wake-event.json").exists())
            listener.feed(16000, "wake")
            wait_for(lambda: not listener.events.empty())
            listener.poll()
            wake = json.loads((directory / "wake-event.json").read_text())
            self.assertEqual(wake["action"], "start")
            state.update(last_event={"id": wake["id"], "accepted": True}, revision=2,
                         can_start=False, can_stop=True, can_cancel=True, record_id="r1")
            recorder.stream, recorder.record_id, recorder.started = object(), "r1", time.monotonic()
            atomic_json(directory / "wake-state.json", state)
            listener.poll()
            self.assertEqual(listener.mode[1], "stop")  # Stop works immediately after waking.
            listener.feed(16000, "stop")
            wait_for(lambda: not listener.events.empty())
            listener.poll()
            event = json.loads((directory / "wake-event.json").read_text())
            self.assertEqual((event["action"], event["record_id"]), ("stop", "r1"))
            self.assertEqual([row["event"] for row in log.read()], ["stop_detected", "wake_detected"])

    def test_stale_busy_and_changed_recordings_discard_detections(self):
        for change in ("busy", "revision", "stale", "disabled"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                directory, recorder, listener, log, state = self.fixture(folder)
                listener.feed(16000, "wake")
                wait_for(lambda: not listener.events.empty())
                if change == "revision": state["revision"] += 1
                if change == "disabled": state.update(enabled=False, can_start=False)
                atomic_json(directory / "wake-state.json", state)
                if change == "stale": os.utime(directory / "wake-state.json", (0, 0))
                listener.poll(busy=change == "busy")
                self.assertFalse((directory / "wake-event.json").exists())
                listener.close()

    def test_events_match_reapers_whole_second_clock(self):
        for action in ("start", "stop", "cancel"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                directory, recorder, listener, log, state = self.fixture(folder)
                if action != "start":
                    recorder.stream, recorder.record_id = object(), "r1"
                    recorder.started = time.monotonic()
                    state.update(revision=2, record_id="r1", can_start=False, can_stop=True, can_cancel=True)
                    atomic_json(directory / "wake-state.json", state)
                    listener.poll()
                if action == "cancel":
                    recorder.started -= 91
                else:
                    listener.events.put_nowait((listener.mode, time.monotonic()))
                os.utime(directory / "wake-state.json", (1234, 1234))
                with patch("heycomputer.wake.time.time", return_value=1234.75):
                    listener.poll()
                event = json.loads((directory / "wake-event.json").read_text())
                self.assertEqual(event["action"], action)
                # Lua os.time() returns 1234 during this same wall-clock second.
                self.assertEqual(event["created"], 1234)
                self.assertGreaterEqual(1234 - event["created"], 0)

    def test_timeout_proposes_discard_and_never_transcribes(self):
        with tempfile.TemporaryDirectory() as folder, patch("heycomputer.providers.transcribe", side_effect=AssertionError("No upload")):
            directory, recorder, listener, log, state = self.fixture(folder)
            recorder.stream, recorder.record_id, recorder.started = object(), "r1", time.monotonic()-91
            state.update(revision=2, record_id="r1", can_start=False, can_stop=True, can_cancel=True)
            atomic_json(directory / "wake-state.json", state)
            listener.poll()
            event = json.loads((directory / "wake-event.json").read_text())
            self.assertEqual((event["action"], event["record_id"]), ("cancel", "r1"))

    def test_audio_queue_overflow_is_bounded_and_invalidates_backlog(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, recorder, listener, log, state = self.fixture(folder)
            # Stop the consumer, then flood its bounded callback queue.
            listener.closed.set(); listener.thread.join(timeout=1); listener.closed.clear()
            for _ in range(100): listener.feed(16000, "wake")
            self.assertEqual(listener.queue.qsize(), 10)
            self.assertTrue(listener.overflow.is_set())

    def test_shared_mic_opens_once_and_retains_no_idle_audio(self):
        import numpy as np
        import sounddevice
        recorder = Recorder()
        streams = []
        class Stream:
            def __init__(self, **kwargs): self.callback=kwargs["callback"]; self.closed=False; streams.append(self)
            def start(self): pass
            def stop(self): pass
            def close(self): self.closed=True
        config = dict(DEFAULTS)
        with patch.object(sounddevice, "InputStream", Stream), \
             patch.object(sounddevice, "query_devices", return_value={"default_samplerate": 16000, "max_input_channels": 1}), \
             patch.object(Recorder, "devices", return_value=[]):
            received = []
            recorder.listen(config, lambda rate, data, dropped: received.append(len(data)))
            data = np.full((8000, 1), .2, dtype="float32")
            streams[0].callback(data, 8000, None, None)
            self.assertEqual(recorder.chunks, [])
            self.assertIsNone(recorder.stream)
            recorder.start(config)
            streams[0].callback(data, 8000, None, None)
            self.assertTrue(recorder.stop().startswith(b"RIFF"))
            self.assertEqual(len(streams), 1)
            self.assertFalse(streams[0].closed)
            self.assertEqual(recorder.chunks, [])
            recorder.unlisten()
            self.assertTrue(streams[0].closed)

    @patch.object(Recorder, "stop", return_value=b"audio")
    @patch("heycomputer.providers.transcribe", return_value="make a track, execute.")
    @patch("heycomputer.providers.plan", return_value={"kind": "reply", "message": "fixture"})
    def test_stop_word_removed_before_planning_and_transcript_logged(self, plan, transcribe, stop):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(Path(folder)/"session")
            worker.config.update(wake_enabled=True)
            worker.voice_activation = "activation"
            worker.recorder.record_id = "r1"
            worker.dispatch({"id": "j1", "kind": "record_stop", "record_id": "r1", "stop_phrase": "execute"})
            response = worker.directory/"j1.response.json"
            wait_for(response.exists)
            self.assertEqual(plan.call_args.args[0], "make a track")
            row = next(r for r in worker.wake_log.read() if r["event"] == "transcribed")
            self.assertEqual(row["transcript"], "make a track")

    def test_model_missing_does_not_disable_typed_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(Path(folder)/"session")
            worker.config["wake_enabled"] = True
            worker.configure_wake()
            self.addCleanup(worker.wake.close)
            wait_for(lambda: worker.wake.error)
            self.assertIn("scripts/setup.sh", worker.wake.error)
            with patch("heycomputer.providers.plan", return_value={"kind": "reply", "message": "Still usable"}):
                worker.dispatch({"id": "j1", "kind": "plan", "text": "hello"})
                response = worker.directory/"j1.response.json"
                wait_for(response.exists)
                self.assertEqual(json.loads(response.read_text())["message"], "Still usable")

    @patch.object(Recorder, "stop", return_value=b"audio")
    @patch("heycomputer.providers.transcribe", return_value="Execute.")
    @patch("heycomputer.providers.plan")
    def test_stop_phrase_alone_never_plans_an_empty_command(self, plan, transcribe, stop):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(Path(folder)/"session")
            worker.config.update(wake_enabled=True)
            worker.voice_activation = "activation"
            worker.recorder.record_id = "r1"
            worker.dispatch({"id": "j1", "kind": "record_stop", "record_id": "r1", "stop_phrase": "execute"})
            response = worker.directory/"j1.response.json"
            wait_for(response.exists)
            result = json.loads(response.read_text())
            self.assertEqual(result["kind"], "reply")
            self.assertIn("No command was heard", result["message"])
            plan.assert_not_called()
            self.assertFalse((worker.directory/"transcript.json").exists())
            self.assertEqual(worker.wake_log.read()[0]["event"], "no_command")
