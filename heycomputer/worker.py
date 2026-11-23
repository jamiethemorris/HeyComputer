"""Private file-based IPC keeps REAPER's UI/audio threads free of network I/O."""
import argparse
import json
import os
from pathlib import Path
import re
import threading
import time

from . import providers
from . import settings
from .protocol import PlanError, validate_result
from .settings import DEFAULTS, PRESETS, KEY_PROVIDERS, load_config, service_config
from .speech import correction_pairs, correct_text
from .migration import import_legacy_settings
from .audio import Recorder
from .wake import ActivationLog, WakeListener, phrases, strip_stop_phrase
from .voice_output import VoiceOutput, validate as validate_voice, FIELDS as VOICE_FIELDS


# Keep legacy-only and conversational requests together; never split a prompt.
MCP_INTENT = re.compile(r'\b(tracks?|project|markers?|transport|selection|routing|sends?|mute|unmute|solo|unsolo|pan|faders?|volume)\b', re.I)
PLUGIN_INTENT = re.compile(
    r'\b(fx|effects?|plug-?ins?|vst3?|presets?|eq|pro[- ]?q|shelf|bands?|filter|cutoff|hertz|k?hz|decay|'
    r'reverb|compressor|threshold|parameters?|knobs?)\b', re.I)
LEGACY_INTENT = re.compile(r'\b(open|show window|gui|visual|click|drag|midi|items?|scripts?|actions?|explain|how (?:do|can|should) i)\b', re.I)
MCP_REQUEST_SECONDS = 120  # Leave time for cleanup and IPC before the panel's 150s timeout.


def mcp_intent(text):
    from .mcp_catalog import families
    if LEGACY_INTENT.search(text):
        return False
    # Return/send and envelope workflows can now inspect and set native FX in
    # the same loop. Standalone plugin setup still uses the existing planner.
    return bool(families(text) & {'routing', 'env', 'parallel'} or
                MCP_INTENT.search(text) and not PLUGIN_INTENT.search(text))


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


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
        self.desktop = None
        self.cancel_visual = threading.Event()
        self.visual_active = False
        self.active_id = None
        self.visual_preparation = None
        self.parameter_preparation = None
        self.wake = None
        self.wake_error = ""
        self.wake_lock = threading.RLock()
        self.wake_log = ActivationLog(directory.parent)
        self.voice_activation = ""
        self.speaker = VoiceOutput()
        self.last_speech_id = None

    def poll_speech(self):
        """Consume one fresh panel speech event; never queue it for later."""
        try:
            state_path = self.directory / 'speech-state.json'
            state = json.loads(state_path.read_text())
            available = (state.get('session') == self.directory.name and state.get('ready') is True
                         and 0 <= time.time() - state_path.stat().st_mtime < 3
                         and not state.get('recording') and not state.get('transport_recording')
                         and not state.get('busy') and not self.busy and not self.recorder.stream)
        except (OSError, ValueError, AttributeError):
            state, available = {}, False
        if not available and self.speaker.active:
            self.speaker.stop()
        path = self.directory / 'speech-command.json'
        try:
            command = json.loads(path.read_text())
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            return
        path.unlink(missing_ok=True)
        if not isinstance(command, dict):
            return
        created, identifier = command.get('created'), command.get('id')
        if (command.get('session') != self.directory.name or not isinstance(identifier, str)
                or not identifier.isalnum() or len(identifier) > 64 or identifier == self.last_speech_id
                or type(created) not in (int, float) or not 0 <= time.time() - created < 5):
            return
        self.last_speech_id = identifier
        if command.get('kind') == 'stop':
            self.speaker.stop()
            return
        if not available:
            return
        config = dict(self.config)
        if command.get('kind') == 'preview':
            options = command.get('options')
            if not isinstance(options, dict):
                return
            config.update({key: options[key] for key in VOICE_FIELDS if key in options})
            text = 'Which track would you like me to use?'
        elif command.get('kind') == 'reply' and config.get('speech_enabled') and state.get('enabled'):
            text = command.get('text')
            if not isinstance(text, str) or len(text) > 12000:
                return
        else:
            return
        try:
            self.speaker.speak(text, config)
        except PlanError as exc:
            self.speaker.error = str(exc)

    def configure_wake(self):
        with self.wake_lock:
            if self.wake:
                self.wake.close()
                self.wake = None
            self.wake_error = ""
            if self.config.get("wake_enabled"):
                try:
                    self.wake = WakeListener(self.directory, self.recorder, self.wake_log, self.config, atomic_json)
                except PlanError as exc:
                    self.wake_error = str(exc)

    def wake_info(self):
        with self.wake_lock:
            return self.wake.info() if self.wake else {"status": self.wake_error or "Wake word off", "error": self.wake_error}

    def log_voice(self, activation, event, **fields):
        if activation and self.config.get("wake_enabled") and self.config.get("wake_log_enabled"):
            self.wake_log.add(activation, event, **fields)

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

    def check_project(self, snapshot, require_revision, opened_fx=None, *, job_id=None):
        from .vision import VisualStopped
        path = self.directory / 'visual-guard.json'
        try:
            with path.open() as guard:
                live = json.load(guard)
                age = time.time() - os.fstat(guard.fileno()).st_mtime
            if (self.shutdown or not 0 <= age <= 3
                    or not isinstance(live, dict) or live.get('recording') is not False
                    or job_id is not None and live.get('job') != job_id
                    or not snapshot.get('project') or live.get('project') != snapshot['project']
                    or require_revision and (type(snapshot.get('revision')) is not int or
                                             type(live.get('revision')) is not int or
                                             live['revision'] != snapshot.get('revision'))
                    or opened_fx and live.get('opened_fx') != opened_fx.get('guid')):
                raise ValueError()
        except (OSError, ValueError):
            raise VisualStopped('REAPER changed project, started recording, or is no longer responding. Check REAPER before retrying.') from None

    def visual_plan(self, text, history, config, snapshot, job_id, opened_fx=None):
        from . import vision
        if config.get("vision_enabled") is not True:
            raise PlanError("Visual control is disabled.")
        credentials = self.chatgpt_credentials(config)
        if credentials and not credentials["access_token"]:
            raise PlanError("Sign in with ChatGPT in Settings before using visual control.")

        first = True
        def guard():
            nonlocal first
            self.check_project(snapshot, first, opened_fx)
            first = False
        def progress(message):
            with self.output_lock:
                if not self.shutdown:
                    atomic_json(self.directory / "visual-progress.json", {"id": job_id, "message": message})
        self.visual_active = True
        try:
            from .ui_memory import UIMemory
            memory = UIMemory(self.directory.parent / "ui-memory.json") if config.get("ui_memory_enabled", True) else None
            return vision.run(text, history, config, self.get_desktop(), self.cancel_visual, guard, progress,
                              opened_fx=opened_fx, memory=memory, **credentials)
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

    def chatgpt_status(self):
        try:
            from .chatgpt_auth import account_status
            status = account_status(self.directory.parent)
        except Exception:
            return "unavailable"
        if status == "Signed in":
            return "signed_in"
        if status == "Signed out":
            return "signed_out"
        if status == "Signed in (ChatGPT plan usage not enabled)":
            return "usage_disabled"
        return "unavailable"

    def chatgpt_token(self):
        try:
            from .chatgpt_auth import access_token
            return access_token(self.directory.parent)
        except Exception:
            return None  # Native plans can still run; the model requires a credential.

    def chatgpt_credentials(self, config):
        return {"access_token": self.chatgpt_token()} if config.get("fallback_provider") == "chatgpt" else {}

    def mcp_request(self, text, snapshot, config, history, deadline, job_id):
        from .mcp_catalog import WRITES
        from .mcp_companion import WRITES as COMPANION_WRITES
        from .mcp_ipc import exchange
        from .mcp_loop import run_loop
        from .mcp_responses import make_turn
        from .reaper_mcp_client import ReaperMCPClient

        def remaining():
            return deadline - time.monotonic()

        edited = False
        def check():
            if self.active_id != job_id or self.cancel_visual.is_set() or self.shutdown:
                raise PlanError('Request cancelled. Check REAPER before retrying.')
            if remaining() <= 0:
                raise PlanError('Request timed out. Check REAPER before retrying.')
            if self.recorder.stream:
                raise PlanError('Stop microphone recording before using ReaperMCP.')
            self.check_project(snapshot, not edited, job_id=job_id)

        check()
        credentials = self.chatgpt_credentials(config)
        provider, _, model = service_config(config, 'fallback')
        if provider == 'chatgpt' and not credentials.get('access_token'):
            raise PlanError('Sign in with ChatGPT in Settings first.')
        if provider != 'chatgpt' and not providers.api_key(provider):
            raise PlanError('Add your API key in Settings first.')
        turn = make_turn(config, check, **credentials)
        context = {'history': history[-16:], 'preferences': config.get('aliases', '')}
        try:
            check()
            with ReaperMCPClient(settings.reaper_mcp_command(), check) as client:
                class GuardedClient:
                    list_tools = staticmethod(client.list_tools)

                    @staticmethod
                    def call(name, args):
                        nonlocal edited
                        check()
                        # Our first write can itself advance the panel revision.
                        if name in WRITES:
                            edited = True
                        return client.call(name, args)

                def companion(request, budget, seconds):
                    def publish(path, value):
                        nonlocal edited
                        with self.output_lock:
                            check()
                            if not edited:
                                value['revision'] = snapshot['revision']
                            if request['name'] in COMPANION_WRITES:
                                edited = True
                            atomic_json(path, value)
                    return exchange(self.directory, job_id, snapshot['project'], request, budget, seconds, publish)

                message = run_loop(GuardedClient(), turn, text, context, check, remaining, companion=companion)
            check()
        except PlanError:
            raise  # Keep the loop's verified/uncertain edit receipts.
        except RuntimeError as exc:
            raise PlanError(str(exc)) from exc  # Includes bridge setup guidance.
        except Exception as exc:
            raise PlanError('ReaperMCP request failed. Check REAPER before retrying.') from exc
        return {'kind': 'reply', 'message': message, 'model': model}

    def chatgpt_models(self):
        result = {"kind": "chatgpt_models", "models": []}
        token = self.chatgpt_token()
        if not token:
            return dict(result, message="Sign in with ChatGPT in Settings before refreshing models.")
        try:
            models = providers.chatgpt_list_models(token)
            result["models"] = [{"slug": m["slug"], "display_name": m["display_name"]} for m in models
                                if len(m["slug"]) <= 200 and len(m["display_name"]) <= 200
                                and token not in m["slug"] and token not in m["display_name"]]
        except Exception:
            result["message"] = "Could not refresh ChatGPT models. Check your sign-in and try again."
        return result

    def chatgpt_account(self, kind):
        result = {"kind": "chatgpt_account"}
        if kind != "chatgpt_status":
            if self.recorder.stream:
                raise PlanError("Stop recording before changing the ChatGPT account.")
            try:
                if kind == "chatgpt_sign_in":
                    from .chatgpt_auth import sign_in
                    sign_in(self.directory.parent)
                else:
                    from .chatgpt_auth import sign_out
                    if sign_out(self.directory.parent) == "Signed out locally; remote revocation was not confirmed. Disconnect the app in ChatGPT Settings.":
                        result["message"] = "Signed out locally. To confirm revocation, disconnect HeyComputer in ChatGPT Settings."
            except Exception:
                # OAuth errors may contain codes or tokens; never relay their text.
                verb = "sign in to" if kind == "chatgpt_sign_in" else "sign out of"
                result["message"] = f"Could not {verb} ChatGPT. Try again in Settings."
        result["status"] = self.chatgpt_status()
        return result

    def settings(self):
        error = ""
        try:
            devices = self.recorder.devices()
        except Exception as exc:
            devices = []
            error = "Microphone discovery unavailable: " + type(exc).__name__
        outputs, voices, voice_error = [], [], ''
        try:
            outputs, voices = self.speaker.devices(), self.speaker.voices()
        except Exception:
            voice_error = 'Voice/output discovery unavailable. Refresh audio devices to try again.'
        return {"kind": "settings", "config": self.config, "devices": devices, "presets": PRESETS,
                "outputs": outputs, "voices": voices, "voice_error": voice_error,
                "keys": {p: bool(providers.api_key(p)) for p in KEY_PROVIDERS}, "chatgpt_status": self.chatgpt_status(),
                "audio_error": error, "wake": self.wake_info(),
                "vision": self.visual_status() if self.config.get("vision_enabled") else {}}

    def save_settings(self, job):
        config = dict(self.config)
        updates = job.get("config", {})
        config.update({k: v for k, v in updates.items() if k in DEFAULTS})
        if config["fallback_provider"] == "chatgpt":
            if self.config["fallback_provider"] != "chatgpt" and "fallback_model" not in updates:
                config["fallback_model"] = PRESETS["fallback"]["chatgpt"]["model"]
            config["fallback_endpoint"] = PRESETS["fallback"]["chatgpt"]["endpoint"]
        if config["transcription_provider"] == "mlx_whisper":
            preset = PRESETS["transcription"]["mlx_whisper"]
            if self.config["transcription_provider"] != "mlx_whisper" and "transcription_model" not in updates:
                config["transcription_model"] = preset["model"]
            config["transcription_endpoint"] = preset["endpoint"]
        for service in PRESETS:
            service_config(config, service)
        if type(config["mic_channel"]) is not int or not 1 <= config["mic_channel"] <= 256:
            raise PlanError("Invalid microphone channel.")
        if not isinstance(config["aliases"], str) or len(config["aliases"]) > 8000:
            raise PlanError("Preferences must be under 8,000 characters.")
        correction_pairs(config["speech_corrections"])
        phrases(config)
        validate_voice(config)
        if any(type(config[k]) is not bool for k in ("use_jev", "allow_lua", "vision_enabled", "ui_memory_enabled")):
            raise PlanError("Invalid settings.")
        for provider in KEY_PROVIDERS:
            value = job.get("keys", {}).get(provider, "").strip()
            if value:
                import keyring
                keyring.set_password("HeyComputer", provider, value)
        atomic_json(self.config_path, config)
        self.speaker.stop()
        self.config = config
        if not config["vision_enabled"] and self.desktop:
            self.desktop.allowed_hosts.clear()
            self.desktop.clear()
        self.configure_wake()
        return self.settings()

    def complete(self, job_id, fn, native_completed=False, activation=""):
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
        if activation and result.get("kind") == "error":
            self.log_voice(activation, "error", reason=result.get("message", "Request failed."))
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
            self.speaker.stop()
            self.shutdown = True
            return
        if self.busy:
            atomic_json(self.directory / f"{job_id}.response.json",
                        {"kind": "error", "message": "A request is already running.", "id": job_id})
            return
        self.busy = True
        self.speaker.stop()
        self.active_id = job_id
        self.cancel_visual.clear()
        config = dict(self.config)
        deadline = time.monotonic() + MCP_REQUEST_SECONDS
        preparation = self.visual_preparation
        parameter_preparation = self.parameter_preparation
        activation = self.voice_activation if kind == "record_stop" else ""
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
                result = providers.openai_plan(prep["text"], snapshot, config, [], **self.chatgpt_credentials(config))
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
            if kind == "audio_devices":
                return dict(self.settings(), kind="audio_devices")
            if kind == "save_settings":
                return self.save_settings(job)
            if kind in ("chatgpt_status", "chatgpt_sign_in", "chatgpt_sign_out"):
                return self.chatgpt_account(kind)
            if kind == "chatgpt_models":
                return self.chatgpt_models()
            if kind in ("wake_log", "wake_log_clear"):
                if kind == "wake_log_clear":
                    self.wake_log.clear()
                return {"kind": "wake_log", "entries": self.wake_log.read()}
            if kind in ("visual_permissions", "visual_hosts", "visual_approve"):
                if config.get("vision_enabled") is not True:
                    raise PlanError("Enable visual control and save settings first.")
                if kind == "visual_approve":
                    if type(job.get("window")) is not int or type(job.get("enabled")) is not bool:
                        raise PlanError("Invalid plugin-window selection.")
                    self.get_desktop().approve_host(job["window"], job["enabled"])
                return {"kind": "visual_status", "vision": self.visual_status(kind == "visual_permissions")}
            if kind == "record_start":
                self.recorder.start(config)
                self.voice_activation = job.get("wake_event") or self.recorder.record_id
                self.log_voice(self.voice_activation, "recording_started",
                               source="wake word" if job.get("wake_event") else "Talk button / action")
                return {"kind": "recording", "record_id": self.recorder.record_id}
            if kind == "record_cancel":
                self.recorder.stop(discard=True)
                self.log_voice(self.voice_activation, "cancelled", reason=job.get("reason", "Cancelled; audio discarded."))
                self.voice_activation = ""
                return {"kind": "cancelled"}
            if kind in ("plan", "record_stop"):
                snapshot, history = job.get("snapshot", {}), job.get("history", [])
                text = job.get("text", "").strip()
                if kind == "record_stop":
                    def cancelled():
                        self.log_voice(activation, "cancelled", reason="Transcription request cancelled.")
                        return {"kind": "reply", "message": "Request cancelled before execution."}

                    spoken_stop = (job.get("stop_phrase") == config["wake_stop_phrase"]
                                   and job.get("record_id") == self.recorder.record_id)
                    wav = self.recorder.stop(discard=True) if self.cancel_visual.is_set() else self.recorder.stop()
                    if self.cancel_visual.is_set():
                        return cancelled()
                    self.log_voice(activation, "submitted", source="stop phrase" if spoken_stop else "Stop & send")
                    vocabulary = [i["name"] for i in snapshot.get("inputs", [])]
                    vocabulary += re_words(config.get("aliases", ""))
                    vocabulary += [p["name"] for p in snapshot.get("plugins", [])
                                   if any(term in p["name"].lower() for term in ("genome", "fabfilter"))]
                    try:
                        text = providers.transcribe(wav, config, vocabulary)
                    except PlanError:
                        if self.cancel_visual.is_set():
                            return cancelled()
                        raise
                    if self.cancel_visual.is_set():
                        return cancelled()
                    if spoken_stop:
                        text = strip_stop_phrase(text, config["wake_stop_phrase"])
                    self.log_voice(activation, "transcribed", transcript=text)
                    raw_text = text
                    text = correct_text(text, config)
                    if not text.strip():
                        self.log_voice(activation, "no_command", reason="No command was heard before stopping.")
                        return {"kind": "reply", "message": "No command was heard before stopping."}
                    # Transcript appears immediately while command interpretation runs.
                    atomic_json(self.directory / "transcript.json", {"id": job_id, "text": text})
                else:
                    raw_text = text
                    text = correct_text(text, config)
                if not text or len(text) > 12000:
                    raise PlanError("Enter a command shorter than 12,000 characters.")
                if config.get('reaper_mcp_enabled') is True:
                    # quick_plan accepts the WHOLE corrected utterance, never a
                    # single step split out of a compound request.
                    direct = providers.quick_plan(text, snapshot)
                    awaiting = bool(history and history[-1].get('kind') in ('reply', 'lua'))
                    if direct and (not awaiting or direct['actions'][0]['op'] in ('transport', 'launch_action')):
                        result = route_result(validate_result(direct, snapshot), text, snapshot, history)
                        return dict(result, transcript=text)
                    if (not awaiting and mcp_intent(text)
                            and snapshot.get('fx_parameters') is None):
                        result = self.mcp_request(text, snapshot, config, history, deadline, job_id)
                        # Already executed; never turn an uncertain write into
                        # a legacy plan or a 'cancelled before execution' reply.
                        return dict(result, transcript=text)
                result = providers.plan(raw_text, snapshot, config, history, **self.chatgpt_credentials(config))
                result = route_result(result, text, snapshot, history)
                result["transcript"] = text
                return result
            raise PlanError("Unknown helper request.")

        threading.Thread(target=self.complete, args=(job_id, perform, (kind == "visual_continue" or kind == "parameter_continue" and job.get("native_setup") is True), activation), daemon=True).start()

    def run(self):
        # This must precede the background request threads. It initializes a
        # connection only: no screen capture, input or permission prompts.
        try:
            from .vision_macos import prepare_window_server
            prepare_window_server()
        except ImportError:
            pass  # Native/audio commands still work before optional libs exist.
        self.configure_wake()
        atomic_json(self.directory / "ready.json", {"pid": os.getpid(), "version": "0.1.0"})
        timed_out = False
        try:
            while not self.shutdown:
                self.poll_visual_cancel()
                heartbeat = self.directory / "heartbeat"
                last_seen = heartbeat.stat().st_mtime if heartbeat.exists() else self.started
                if time.time() - last_seen > 20:
                    timed_out = True
                    break
                for path in sorted(self.directory.glob("*.request.json")):
                    try:
                        job = json.loads(path.read_text())
                        path.unlink()  # Secrets in save requests do not remain on disk.
                        self.dispatch(job)
                    except (ValueError, KeyError, OSError):
                        path.unlink(missing_ok=True)
                self.poll_speech()
                with self.wake_lock:
                    if self.wake:
                        self.wake.poll(self.busy or self.speaker.suppress_wake)
                atomic_json(self.directory / "meter.json", {
                    "level": self.recorder.level, "recording": bool(self.recorder.stream),
                    "problem": self.recorder.problem, "wake": self.wake_info(), "voice": self.speaker.info()})
                time.sleep(0.10)
        finally:
            self.cancel_visual.set()
            self.shutdown = True
            self.speaker.stop()
            with self.wake_lock:
                if self.wake:
                    self.wake.close()
            if self.recorder.stream:
                self.log_voice(self.voice_activation, "cancelled", reason="Panel closed; audio discarded.")
            self.recorder.close()
            with self.output_lock:
                for pattern in ("*.request.json", "*.response.json", "*.tmp", "*.companion-call.json", "*.companion-reply.json",
                                "ready.json", "meter.json", "transcript.json", "heartbeat",
                                "visual-guard.json",
                                "visual-cancel.json", "visual-progress.json", "test-state.json",
                                "test-command.json", "test-rejected.json", "test-command.lock",
                                "wake-state.json", "wake-event.json", "speech-state.json", "speech-command.json"):
                    for path in self.directory.glob(pattern):
                        path.unlink(missing_ok=True)
                if timed_out:
                    # REAPER dialogs and native actions can pause deferred Lua
                    # frames while the panel is still open. Leave its directory
                    # intact so a resumed frame cannot crash on a status write.
                    # Remove requests above; never replay them after reconnect.
                    atomic_json(self.directory / "worker-stopped.json", {"reason": "heartbeat_timeout"})
                else:
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
