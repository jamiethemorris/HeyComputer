"""Local keyword detection. Only explicit stop events enter the normal voice path."""
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import time
import tempfile
import uuid

from .protocol import PlanError

MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL_NAME}.tar.bz2"
MODEL_SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"
MODEL_FILES = ("tokens.txt", "bpe.model", *(
    f"{part}-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
    for part in ("encoder", "decoder", "joiner")))


def phrases(config):
    values = []
    for key in ("wake_phrase", "wake_stop_phrase"):
        value = config.get(key, "")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]+(?:[' ][A-Za-z]+)*", value.strip()) or len(value) > 60:
            raise PlanError("Wake and stop phrases must contain English words and spaces (up to 60 characters).")
        value = " ".join(value.lower().split())
        if len(value.split()) > 6:
            raise PlanError("Use at most six words in a wake or stop phrase.")
        values.append(value)
    if values[0] == values[1]:
        raise PlanError("Choose different wake and stop phrases.")
    threshold = config.get("wake_threshold", .35)
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not .05 <= threshold <= .95:
        raise PlanError("Wake sensitivity must be between 0.05 and 0.95.")
    for key in ("wake_enabled", "wake_log_enabled"):
        if type(config.get(key)) is not bool:
            raise PlanError("Invalid wake-word settings.")
    return tuple(values)


def strip_stop_phrase(text, phrase):
    # Only called after a locally detected stop; never rewrite ordinary typed commands.
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"[\s.,!?;:]*$"
    return re.sub(pattern, "", text, flags=re.I).strip().rstrip(",;:").strip()


class ActivationLog:
    """Append only on events. Two bounded local files, never audio or continuous text."""
    LIMIT = 1024 * 1024

    def __init__(self, directory):
        self.path = Path(directory) / "wake-activations.jsonl"
        self.previous = self.path.with_suffix(".previous.jsonl")
        self.lock = threading.Lock()
        self.error = ""

    def add(self, activation, event, **fields):
        allowed = {k: str(v)[:12000] for k, v in fields.items()
                   if k in ("phrase", "transcript", "reason", "source") and v is not None}
        row = {"time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
               "activation": activation, "event": event, **allowed}
        try:
            with self.lock:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.LIMIT:
                    self.path.replace(self.previous)
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.error = ""
        except OSError:
            self.error = "Activation log could not be written. Check the resource folder permissions."

    def read(self):
        with self.lock:
            rows = deque(maxlen=100)
            for path in (self.previous, self.path):
                if not path.exists():
                    continue
                with path.open(encoding="utf-8") as source:
                    for line in source:
                        try:
                            row = json.loads(line)
                            if isinstance(row, dict):
                                rows.append(row)
                        except ValueError:
                            pass
            return list(reversed(rows))

    def clear(self):
        with self.lock:
            self.path.unlink(missing_ok=True)
            self.previous.unlink(missing_ok=True)
            self.error = ""


class Detector:
    """One CPU thread and quantized English models. No microphone or network access."""
    def __init__(self, directory, config):
        try:
            import sherpa_onnx
            import sentencepiece
        except ImportError as exc:
            raise PlanError("Run scripts/setup_wake.sh, then reopen HeyComputer to enable wake words.") from exc
        directory = Path(directory)
        if not all((directory / name).is_file() for name in MODEL_FILES):
            raise PlanError("Wake-word model is missing. Run scripts/setup_wake.sh, then reopen HeyComputer.")
        wake, stop = phrases(config)
        tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(directory / "bpe.model"))
        self.keywords = {
            "wake": " ".join(tokenizer.encode(wake.upper(), out_type=str)) + " @WAKE",
            "stop": " ".join(tokenizer.encode(stop.upper(), out_type=str)) + " @STOP",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="heycomputer-keywords-") as words:
            # The constructor requires a real file, even with per-stream keywords.
            words.write("\n")
            words.flush()
            self.spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(directory / "tokens.txt"),
                encoder=str(directory / MODEL_FILES[2]), decoder=str(directory / MODEL_FILES[3]),
                joiner=str(directory / MODEL_FILES[4]), keywords_file=words.name, num_threads=1,
                provider="cpu", keywords_threshold=config["wake_threshold"],
                keywords_score=1.0, num_trailing_blanks=2)
        self.stream = None

    def reset(self, mode):
        self.mode = mode
        self.stream = self.spotter.create_stream(self.keywords[mode]) if mode in self.keywords else None
        if mode in self.keywords and self.stream is None:
            raise PlanError("Could not initialize that wake/stop phrase. Choose another English phrase.")

    def feed(self, rate, samples):
        if self.stream is None:
            return False
        self.stream.accept_waveform(rate, samples)
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result = self.spotter.get_result(self.stream)
            if result:
                self.spotter.reset_stream(self.stream)
                if result == self.mode.upper():
                    return True
        return False


class WakeListener:
    """Bounded audio queue; the REAPER panel validates every proposed transition."""
    def __init__(self, directory, recorder, log, config, write, detector_factory=Detector):
        self.directory, self.recorder, self.log = Path(directory), recorder, log
        self.config, self.write = dict(config), write
        self.phrase, self.stop_phrase = phrases(config)
        self.queue = queue.Queue(maxsize=10)
        self.events = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.overflow = threading.Event()
        self.mode = (0, "", "", 0)
        self.pending = None
        self.cooldown = 0.0
        self.ready = False
        self.error = ""
        self.status = "Starting wake-word detector…"
        self.thread = threading.Thread(target=self._run, args=(detector_factory,), daemon=True,
                                       name="HeyComputer wake words")
        self.thread.start()

    def _run(self, factory):
        try:
            detector = factory(self.directory.parent / "models" / "kws-en", self.config)
            self.ready = True
            previous = None
            while not self.closed.is_set():
                try:
                    mode, rate, samples = self.queue.get(timeout=.2)
                except queue.Empty:
                    continue
                if mode != self.mode or not mode[1] or self.overflow.is_set():
                    if self.overflow.is_set():
                        self._drain()
                        self.overflow.clear()
                    previous = None
                    continue
                if mode != previous:
                    detector.reset(mode[1])
                    previous = mode
                if detector.feed(rate, samples):
                    if mode == self.mode:
                        try:
                            self.events.put_nowait((mode, time.monotonic()))
                        except queue.Full:
                            pass
                    detector.reset(mode[1])
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, PlanError) else "Wake-word detector failed (" + type(exc).__name__ + "). Reopen HeyComputer to retry."
        finally:
            self.ready = False

    def _drain(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def feed(self, rate, samples, dropped=False):
        # Called from audio callback: bounded memory, no inference or logging here.
        mode = self.mode
        if self.closed.is_set() or not mode[1]:
            return
        if dropped:
            self.overflow.set()
            return
        try:
            self.queue.put_nowait((mode, rate, samples))
        except queue.Full:
            self.overflow.set()

    def _set_mode(self, kind, record_id="", revision=0):
        if self.mode[1:] != (kind, record_id, revision):
            self.mode = (self.mode[0] + 1, kind, record_id, revision)
            self._drain()

    def _log(self, activation, event, **fields):
        if self.config.get("wake_log_enabled"):
            self.log.add(activation, event, **fields)

    def poll(self, busy=False):
        now = time.time()
        # REAPER's bridge uses os.time(), which has whole-second precision.
        # A fractional timestamp otherwise looks like a future event until the
        # next second, so the panel rejects a freshly detected keyword.
        created = int(now)
        try:
            path = self.directory / "wake-state.json"
            state = json.loads(path.read_text())
            if (not isinstance(state, dict) or not 0 <= now - path.stat().st_mtime <= 2
                    or state.get("session") != self.directory.name):
                state = {}
        except (OSError, ValueError):
            state = {}
        if self.pending:
            receipt = state.get("last_event") or {}
            if receipt.get("id") == self.pending["id"]:
                if not receipt.get("accepted"):
                    self._log(self.pending["id"], "ignored", reason=receipt.get("reason") or "Panel state changed before activation.")
                self.pending = None
            elif now - self.pending["created"] > 3:
                self._log(self.pending["id"], "expired", reason="Panel did not accept the activation.")
                self.pending = None
        # Timeouts only propose discard, never upload. The panel checks the recording ID.
        if (not busy and not self.pending and self.recorder.stream and state.get("can_cancel")
                and state.get("record_id") == self.recorder.record_id
                and (self.recorder.problem or time.monotonic() - self.recorder.started >= 90)):
            event = {"id": uuid.uuid4().hex, "session": self.directory.name,
                     "revision": state["revision"], "record_id": self.recorder.record_id,
                     "created": created, "action": "cancel", "reason": self.recorder.problem or "Recording timed out; audio discarded."}
            self.write(self.directory / "wake-event.json", event)
            self.pending = event
            self.cooldown = time.monotonic() + 2
        kind = ""
        if self.ready and not self.error and not busy and not self.pending:
            if state.get("can_stop") and self.recorder.stream and state.get("record_id") == self.recorder.record_id:
                kind = "stop"
            elif state.get("can_start") and not self.recorder.stream and time.monotonic() >= self.cooldown:
                kind = "wake"
        self._set_mode(kind, state.get("record_id", ""), state.get("revision", 0))
        if self.ready and not self.error and state.get("enabled"):
            try:
                self.recorder.listen(self.config, self.feed)
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, PlanError) else "Wake-word microphone unavailable. Check the selected input and permissions."
                self._set_mode("")
        else:
            self.recorder.unlisten()
        self.status = self.error or ("Wake word listening" if kind == "wake" else
                                    "Stop word listening" if kind == "stop" else
                                    "Wake word paused" if self.ready else "Starting wake-word detector…")
        while True:
            try:
                event_mode, detected = self.events.get_nowait()
            except queue.Empty:
                break
            if self.pending or event_mode != self.mode or not kind or time.monotonic() - detected > 1:
                continue
            action = "start" if kind == "wake" else "stop"
            event = {"id": uuid.uuid4().hex, "session": self.directory.name,
                     "revision": state["revision"], "record_id": state.get("record_id", ""),
                     "created": created, "action": action}
            self.write(self.directory / "wake-event.json", event)
            self.pending = event
            self.cooldown = time.monotonic() + 1.5
            self._log(event["id"], "wake_detected" if action == "start" else "stop_detected",
                      phrase=self.phrase if action == "start" else self.stop_phrase)
            self._set_mode("")

    def info(self):
        return {"status": self.status, "error": self.error, "log_error": self.log.error,
                "listening": bool(self.mode[1]), "phrase": self.phrase, "stop_phrase": self.stop_phrase}

    def close(self):
        self.closed.set()
        self._set_mode("")
        self.recorder.unlisten()
        self.thread.join(timeout=2)
        self._drain()
