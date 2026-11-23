"""One microphone stream shared by Talk and the optional local keyword detector."""
import io
from functools import wraps
import threading
import time
import uuid
import wave

from .protocol import PlanError


def device_operation(fn):
    @wraps(fn)
    def locked(self, *args, **kwargs):
        with self._device_lock:
            return fn(self, *args, **kwargs)
    return locked


class Recorder:
    def __init__(self):
        self._input = None
        self._recording = False
        self._selection = None
        self._observer = None
        self._lock = threading.Lock()
        self._device_lock = threading.RLock()
        self.chunks = []
        self.level = self.peak = self.started = 0.0
        self.problem = ""
        self.record_id = ""
        self.rate = 16000

    @property
    def stream(self):
        # Existing callers use this to mean an active Talk recording, not idle listening.
        return self._input if self._recording else None

    @staticmethod
    def devices():
        import sounddevice as sd
        hosts = sd.query_hostapis()
        return [{"id": f'{hosts[d["hostapi"]]["name"]}: {d["name"]}',
                 "name": d["name"], "channels": d["max_input_channels"], "index": i}
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]

    def _open(self, config):
        import sounddevice as sd
        selection = (config.get("mic_device", ""), config.get("mic_channel", 1))
        if self._input and self._selection == selection:
            return
        if self._input:
            self._close_input()
        device, channel = selection
        matches = [d for d in self.devices() if d["id"] == device]
        if device and len(matches) != 1:
            raise PlanError("The selected microphone is unavailable. Choose it again in Settings.")
        index = matches[0]["index"] if device else None
        info = sd.query_devices(index, "input")
        if type(channel) is not int or not 1 <= channel <= info["max_input_channels"]:
            raise PlanError("That microphone channel is unavailable.")
        self.rate = int(info["default_samplerate"])

        def callback(data, frames, timing, status):
            import numpy as np
            # No inference, files, or network on the device callback.
            mono = data[:, channel - 1].copy()
            now = time.monotonic()
            with self._lock:
                if self._recording:
                    if status:
                        self.problem = "Audio capture dropped samples. Try again with a shorter command."
                    if now - self.started >= 90:
                        self.problem = "Recording reached the 90-second limit."
                    if not self.problem:
                        self.level = float(np.sqrt(np.mean(mono * mono)))
                        self.peak = max(self.peak, self.level)
                        self.chunks.append(mono)
                observer = self._observer
            if observer:
                observer(self.rate, mono, bool(status))

        stream = sd.InputStream(device=index, channels=channel, samplerate=self.rate,
                                blocksize=max(1, int(self.rate * .08)),
                                dtype="float32", callback=callback)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._input, self._selection = stream, selection

    @device_operation
    def listen(self, config, observer):
        self._open(config)
        with self._lock:
            self._observer = observer

    @device_operation
    def unlisten(self):
        with self._lock:
            self._observer = None
        if not self._recording:
            self._close_input()

    @device_operation
    def start(self, config):
        if self._recording:
            raise PlanError("The microphone is already recording.")
        with self._lock:
            self.chunks, self.level, self.peak, self.problem = [], 0.0, 0.0, ""
            self.started = time.monotonic()
            self.record_id = uuid.uuid4().hex
            self._recording = True
        try:
            self._open(config)
        except Exception:
            self._recording = False
            self.record_id = ""
            raise

    def _close_input(self):
        stream, self._input = self._input, None
        self._selection = None
        if stream:
            try:
                stream.stop()
            finally:
                stream.close()

    @device_operation
    def close(self):
        with self._lock:
            self._observer = None
            self._recording = False
            self.chunks = []
            self.record_id = ""
            self.level = 0.0
        self._close_input()

    @device_operation
    def stop(self, discard=False):
        import numpy as np
        with self._lock:
            if not self._recording:
                raise PlanError("The microphone was not recording.")
            self._recording = False
            chunks, self.chunks = self.chunks, []
            self.level = 0.0
            self.record_id = ""
        if not self._observer:
            self._close_input()
        if discard:
            return b""
        if self.problem:
            raise PlanError(self.problem)
        if not chunks or self.peak < 0.003:
            raise PlanError("The microphone was silent. Check the device and input channel.")
        samples = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2")
        if len(samples) < self.rate * .25:
            raise PlanError("That recording was too short. Try again.")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.rate)
            output.writeframes(samples.tobytes())
        return buf.getvalue()
