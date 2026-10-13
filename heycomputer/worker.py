"""Private file-based IPC keeps REAPER's UI/audio threads free of network I/O."""
import argparse
import io
import json
import os
from pathlib import Path
import threading
import time
import wave

from . import providers
from .protocol import PlanError
from .settings import DEFAULTS, PRESETS, KEY_PROVIDERS, load_config, service_config
from .remote import RemoteControl
from .speech import correction_pairs, correct_text
from .migration import import_legacy_settings


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


class Recorder:
    def __init__(self):
        self.stream = None
        self.chunks = []
        self.level = 0.0
        self.peak = 0.0
        self.started = 0.0
        self.problem = ""

    @staticmethod
    def devices():
        import sounddevice as sd
        hosts = sd.query_hostapis()
        return [{"id": f'{hosts[d["hostapi"]]["name"]}: {d["name"]}',
                 "name": d["name"], "channels": d["max_input_channels"], "index": i}
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]

    def start(self, config):
        import numpy as np
        import sounddevice as sd
        if self.stream:
            raise PlanError("The microphone is already recording.")
        device = config.get("mic_device", "")
        matches = [d for d in self.devices() if d["id"] == device]
        if device and len(matches) != 1:
            raise PlanError("The selected microphone is unavailable. Choose it again in Settings.")
        index = matches[0]["index"] if device else None
        info = sd.query_devices(index, "input")
        channel = int(config.get("mic_channel", 1))
        if not 1 <= channel <= info["max_input_channels"]:
            raise PlanError("That microphone channel is unavailable.")
        self.rate = int(info["default_samplerate"])
        self.chunks, self.level, self.peak, self.problem = [], 0.0, 0.0, ""
        self.started = time.monotonic()

        def callback(data, frames, timing, status):
            if status:
                self.problem = "Audio capture dropped samples. Try again with a shorter command."
            if time.monotonic() - self.started > 90:
                self.problem = "Recording reached the 90-second limit. Try a shorter command."
                raise sd.CallbackStop
            mono = data[:, channel - 1].copy()
            self.level = float(np.sqrt(np.mean(mono * mono)))
            self.peak = max(self.peak, self.level)
            self.chunks.append(mono)

        stream = sd.InputStream(device=index, channels=channel, samplerate=self.rate,
                                dtype="float32", callback=callback)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream

    def stop(self, discard=False):
        import numpy as np
        if not self.stream:
            raise PlanError("The microphone was not recording.")
        self.stream.stop()
        self.stream.close()
        self.stream = None
        chunks, self.chunks = self.chunks, []
        self.level = 0.0
        if discard:
            return b""
        if self.problem:
            raise PlanError(self.problem)
        if not chunks or self.peak < 0.003:
            raise PlanError("The microphone was silent. Check the device and input channel.")
        samples = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2")
        if len(samples) < self.rate * 0.25:
            raise PlanError("That recording was too short. Try again.")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.rate)
            output.writeframes(samples.tobytes())
        return buf.getvalue()


class Worker:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        os.chmod(directory.parent, 0o700)
        import_legacy_settings(directory.parent)
        self.config_path = directory.parent / "settings.json"
        self.config = dict(DEFAULTS)
        if self.config_path.exists():
            try:
                saved = json.loads(self.config_path.read_text())
                self.config = load_config(saved)
            except (ValueError, OSError):
                pass
        self.recorder = Recorder()
        self.busy = False
        self.started = time.time()
        self.shutdown = False
        self.output_lock = threading.Lock()
        self.remote = RemoteControl(directory)
        self.desktop = None
        self.cancel_visual = threading.Event()
        self.visual_active = False
        self.active_id = None
        self.visual_preparation = None
        self.parameter_preparation = None

    def get_desktop(self):
        if self.desktop is None:
            from .vision_macos import MacDesktop
            self.desktop = MacDesktop()
        return self.desktop

    def visual_status(self, prompt=False):
        try:
            return self.get_desktop().status(prompt)
        except PlanError as exc:
            return {"error": str(exc), "hosts": []}

    def visual_plan(self, text, history, config, snapshot, job_id, opened_fx=None):
        from . import vision
        if config.get("vision_enabled") is not True:
            raise PlanError("Visual control is disabled.")
        first = True
        def guard():
            nonlocal first
            path = self.directory / "visual-guard.json"
            try:
                if self.shutdown or time.time() - path.stat().st_mtime > 3:
                    raise ValueError()
                live = json.loads(path.read_text())
                if live.get("project") != snapshot.get("project"):
                    raise ValueError()
                if live.get("recording") is not False:
                    raise ValueError()
                if opened_fx and live.get("opened_fx") != opened_fx.get("guid"):
                    raise ValueError()
                if first and live.get("revision") != snapshot.get("revision"):
                    raise ValueError()
            except (OSError, ValueError):
                raise vision.VisualStopped("REAPER changed project, started recording, or is no longer responding. Send the command again.") from None
            first = False
        def progress(message):
            with self.output_lock:
                if not self.shutdown:
                    atomic_json(self.directory / "visual-progress.json", {"id": job_id, "message": message})
        self.visual_active = True
        try:
            from .ui_memory import UIMemory
            memory = UIMemory(self.directory.parent / "ui-memory.json") if config.get("ui_memory_enabled", True) else None
            return vision.run(text, history, config, self.get_desktop(), self.cancel_visual, guard, progress, opened_fx=opened_fx, memory=memory)
        finally:
            self.visual_active = False

    def poll_visual_cancel(self):
        path = self.directory / "visual-cancel.json"
        if path.exists():
            try:
                if json.loads(path.read_text()).get("id") == self.active_id:
                    self.cancel_visual.set()
            except (OSError, ValueError):
                pass
            path.unlink(missing_ok=True)
        if self.visual_active and self.desktop and self.desktop.escape_pressed():
            self.cancel_visual.set()

    def settings(self):
        error = ""
        try:
            devices = self.recorder.devices()
        except Exception as exc:
            devices = []
            error = "Microphone discovery unavailable: " + type(exc).__name__
        return {"kind": "settings", "config": self.config, "devices": devices, "presets": PRESETS,
                "keys": {p: bool(providers.api_key(p)) for p in KEY_PROVIDERS},
                "audio_error": error, "remote": self.remote.info(),
                "vision": self.visual_status() if self.config.get("vision_enabled") else {}}

    def save_settings(self, job):
        config = dict(self.config)
        config.update({k: v for k, v in job.get("config", {}).items() if k in DEFAULTS})
        for service in PRESETS:
            service_config(config, service)
        if type(config["mic_channel"]) is not int or not 1 <= config["mic_channel"] <= 256:
            raise PlanError("Invalid microphone channel.")
        if not isinstance(config["aliases"], str) or len(config["aliases"]) > 8000:
            raise PlanError("Preferences must be under 8,000 characters.")
        correction_pairs(config["speech_corrections"])
        if any(type(config[k]) is not bool for k in ("use_jev", "allow_lua", "remote_enabled", "vision_enabled", "ui_memory_enabled")):
            raise PlanError("Invalid settings.")
        if type(config["remote_port"]) is not int or not 1024 <= config["remote_port"] <= 65535:
            raise PlanError("Remote port must be between 1024 and 65535.")
        for provider in KEY_PROVIDERS:
            value = job.get("keys", {}).get(provider, "").strip()
            if value:
                import keyring
                keyring.set_password("HeyComputer", provider, value)
        atomic_json(self.config_path, config)
        self.config = config
        if not config["vision_enabled"] and self.desktop:
            self.desktop.allowed_hosts.clear()
            self.desktop.clear()
        self.remote.configure(config["remote_enabled"], config["remote_port"])
        return self.settings()

    def complete(self, job_id, fn, native_completed=False):
        try:
            result = fn()
        except PlanError as exc:
            result = {"kind": "error", "message": str(exc)}
        except Exception as exc:
            result = {"kind": "error", "message": f"The helper encountered {type(exc).__name__}. No project changes were made."}
        if native_completed and result.get("kind") == "error" and not result.get("message", "").startswith("Native setup completed."):
            result["message"] = "Native setup completed. " + result.get("message", "").replace("No project changes were made.", "")
            result["visual"] = True
        result["id"] = job_id
        with self.output_lock:
            self.busy = False
            if not self.shutdown:
                atomic_json(self.directory / f"{job_id}.response.json", result)

    def dispatch(self, job):
        job_id = job["id"]
        if not isinstance(job_id, str) or not job_id.isalnum() or len(job_id) > 80:
            return
        kind = job.get("kind")
        if kind == "shutdown":
            self.cancel_visual.set()
            self.shutdown = True
            return
        if self.busy:
            atomic_json(self.directory / f"{job_id}.response.json",
                        {"kind": "error", "message": "A request is already running.", "id": job_id})
            return
        self.busy = True
        self.active_id = job_id
        self.cancel_visual.clear()
        config = dict(self.config)
        preparation = self.visual_preparation
        parameter_preparation = self.parameter_preparation
        self.visual_preparation = None
        self.parameter_preparation = None

        def route_result(result, text, snapshot, history):
            if self.cancel_visual.is_set():
                message = "Request cancelled before execution."
                if kind == "parameter_continue" and job.get("native_setup") is True:
                    message = "Request cancelled. Earlier native setup remains; parameter edits were not applied."
                return {"kind": "reply", "message": message}
            if result.get("kind") == "inspect":
                if snapshot.get("fx_parameters") is not None:
                    raise PlanError("Plugin inspection did not resolve this request; no parameter edits were applied.")
                self.parameter_preparation = {"id": job_id, "created": time.time(), "project": snapshot.get("project"),
                    "text": result["message"], "model": result.get("model")}
                return dict(result, kind="parameter_prepare")
            if result.get("kind") == "vision":
                if result.get("actions"):
                    self.visual_preparation = {"id": job_id, "created": time.time(), "project": snapshot.get("project"),
                        "text": result["message"], "history": [], "model": result.get("model")}
                    return dict(result, kind="visual_prepare")
                return self.visual_plan(text, history, config, snapshot, job_id)
            return result

        def perform():
            if kind == "parameter_continue":
                prep = parameter_preparation
                if not prep or prep["id"] != job_id or time.time() - prep["created"] > 30:
                    raise PlanError("Plugin inspection expired; earlier native setup may already exist.")
                snapshot = job.get("snapshot", {})
                if snapshot.get("project") != prep["project"] or not isinstance(snapshot.get("fx_parameters"), list) or len(snapshot["fx_parameters"]) != 1:
                    raise PlanError("The inspected project or plugin changed.")
                result = providers.openai_plan(prep["text"], snapshot, config, [])
                result["model"] = prep.get("model")
                return route_result(result, prep["text"], snapshot, [])
            if kind == "visual_continue":
                # One-use continuation: the model never bypasses native execution.
                if not preparation or preparation["id"] != job_id or time.time() - preparation["created"] > 30:
                    raise PlanError("Visual preparation expired. Native edits may already exist; inspect the project.")
                snapshot = job.get("snapshot", {})
                if snapshot.get("project") != preparation["project"] or not isinstance(job.get("opened_fx"), dict):
                    raise PlanError("The prepared project or plugin changed. Native edits may already exist.")
                result = self.visual_plan(preparation["text"], preparation["history"], config, snapshot, job_id, job["opened_fx"])
                result["message"] = "Native setup completed. " + result.get("message", "")
                result["model"] = preparation.get("model")
                return result
            if kind == "settings":
                return self.settings()
            if kind == "save_settings":
                return self.save_settings(job)
            if kind in ("visual_permissions", "visual_hosts", "visual_approve"):
                if config.get("vision_enabled") is not True:
                    raise PlanError("Enable visual control and save settings first.")
                if kind == "visual_approve":
                    if type(job.get("window")) is not int or type(job.get("enabled")) is not bool:
                        raise PlanError("Invalid plugin-window selection.")
                    self.get_desktop().approve_host(job["window"], job["enabled"])
                return {"kind": "visual_status", "vision": self.visual_status(kind == "visual_permissions")}
            if kind == "remote_pair":
                return self.remote.begin_pairing()
            if kind == "remote_revoke":
                self.remote.revoke()
                return self.settings()
            if kind == "record_start":
                self.recorder.start(config)
                return {"kind": "recording"}
            if kind == "record_cancel":
                self.recorder.stop(discard=True)
                return {"kind": "cancelled"}
            if kind in ("plan", "record_stop"):
                snapshot, history = job.get("snapshot", {}), job.get("history", [])
                text = job.get("text", "").strip()
                if kind == "record_stop":
                    wav = self.recorder.stop()
                    vocabulary = [i["name"] for i in snapshot.get("inputs", [])]
                    vocabulary += re_words(config.get("aliases", ""))
                    vocabulary += [p["name"] for p in snapshot.get("plugins", [])
                                   if any(term in p["name"].lower() for term in ("genome", "fabfilter"))]
                    text = providers.transcribe(wav, config, vocabulary)
                    raw_text = text
                    text = correct_text(text, config)
                    # Transcript appears immediately while command interpretation runs.
                    atomic_json(self.directory / "transcript.json", {"id": job_id, "text": text})
                else:
                    raw_text = text
                    text = correct_text(text, config)
                if not text or len(text) > 12000:
                    raise PlanError("Enter a command shorter than 12,000 characters.")
                result = providers.plan(raw_text, snapshot, config, history)
                result = route_result(result, text, snapshot, history)
                result["transcript"] = text
                return result
            raise PlanError("Unknown helper request.")

        threading.Thread(target=self.complete, args=(job_id, perform, (kind == "visual_continue" or kind == "parameter_continue" and job.get("native_setup") is True)), daemon=True).start()

    def run(self):
        # This must precede the background request threads. It initializes a
        # connection only: no screen capture, input or permission prompts.
        try:
            from .vision_macos import prepare_window_server
            prepare_window_server()
        except ImportError:
            pass  # Native/audio commands still work before optional libs exist.
        self.remote.configure(self.config.get("remote_enabled") is True, self.config.get("remote_port", 8765))
        atomic_json(self.directory / "ready.json", {"pid": os.getpid(), "version": "0.1.0"})
        try:
            while not self.shutdown:
                self.poll_visual_cancel()
                heartbeat = self.directory / "heartbeat"
                last_seen = heartbeat.stat().st_mtime if heartbeat.exists() else self.started
                if time.time() - last_seen > 20:
                    break
                for path in sorted(self.directory.glob("*.request.json")):
                    try:
                        job = json.loads(path.read_text())
                        path.unlink()  # Secrets in save requests do not remain on disk.
                        self.dispatch(job)
                    except (ValueError, KeyError, OSError):
                        path.unlink(missing_ok=True)
                atomic_json(self.directory / "meter.json", {
                    "level": self.recorder.level, "recording": bool(self.recorder.stream),
                    "problem": self.recorder.problem})
                time.sleep(0.10)
        finally:
            self.cancel_visual.set()
            self.shutdown = True
            self.remote.stop()
            if self.recorder.stream:
                self.recorder.stop(discard=True)
            with self.output_lock:
                for pattern in ("*.request.json", "*.response.json", "*.tmp",
                                "ready.json", "meter.json", "transcript.json", "heartbeat",
                                "remote-state.json", "remote-command.json", "visual-guard.json",
                                "visual-cancel.json", "visual-progress.json", "test-state.json",
                                "test-command.json", "test-rejected.json", "test-command.lock"):
                    for path in self.directory.glob(pattern):
                        path.unlink(missing_ok=True)
                try:
                    self.directory.rmdir()
                except OSError:
                    pass


def re_words(value):
    import re
    return [s.strip() for s in re.split(r"[=,;\n]", value) if s.strip()][:100]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    Worker(args.directory).run()
