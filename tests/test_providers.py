import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from heycomputer import providers
from heycomputer.protocol import PlanError
from heycomputer.settings import DEFAULTS, PRESETS, load_config, service_config
from heycomputer.worker import Worker, Recorder


REPLY = {"kind": "reply", "message": "Which track?", "actions": [], "lua": ""}


def response(result=REPLY):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(result)}]}]}


class RoutingTests(unittest.TestCase):
    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("heycomputer.providers.request_json", return_value=response())
    def test_only_relevant_installed_launch_actions_reach_model(self, request, key):
        snapshot = {"launch_actions": [{"id": "_RSa", "name": "Script: ReaSplice.lua"},
                                       {"id": "_RSb", "name": "Script: Unrelated tool.lua"}]}
        providers.openai_plan("open rea splice", snapshot, DEFAULTS, [])
        project = json.loads(request.call_args.args[2]["input"])["project"]
        self.assertEqual([a["id"] for a in project["launch_actions"]], ["_RSa"])
        self.assertEqual(len(snapshot["launch_actions"]), 2)

    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("heycomputer.providers.request_json", return_value={"text": "fixture"})
    def test_direct_transcription_hints_include_canonical_correction_terms(self, request, key):
        config = dict(DEFAULTS, transcription_provider="openai", speech_corrections="ree splice = ReaSplice")
        providers.transcribe(b"audio", config, ["Guitar 1"])
        self.assertIn(b"ReaSplice", request.call_args.kwargs["body"])

    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("heycomputer.providers.request_json", return_value=response())
    def test_project_context_reaches_planner_without_action_search_removing_option_states(self, request, key):
        timing = {"start": {"time_signature": "7/8", "tempo_bpm": 91},
                  "tempo_markers": [], "tempo_map_complete": True}
        context = {"grid": {"whole_note_division": .125},
                   "options": [{"name": "Options: Toggle metronome", "on": True}]}
        snapshot = {"timing": timing, "project_context": context,
                    "actions": [{"id": "40005", "name": "Options: Toggle metronome", "requires": "none", "on": True}]}
        providers.openai_plan("create quarter-note pan automation through bar 4", snapshot, DEFAULTS, [])
        payload = request.call_args.args[2]
        project = json.loads(payload["input"])["project"]
        self.assertEqual(project["timing"], timing)
        self.assertEqual(project["project_context"], context)
        self.assertIn("Read the current project snapshot before asking", payload["instructions"])
        self.assertIn("denominator/4 beats", payload["instructions"])
        self.assertEqual(len(snapshot["actions"]), 1)

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "shared-fixture-key"}, clear=True)
    @patch("heycomputer.providers.request_json")
    def test_one_key_routes_all_three_services(self, request):
        request.return_value = {"answers": {}}
        self.assertIsNone(providers.jev_plan("create tracks", {}, DEFAULTS))
        jev_call = request.call_args
        request.return_value = response()
        self.assertEqual(providers.openai_plan("help", {}, DEFAULTS, []), REPLY)
        gpt_call = request.call_args
        request.return_value = {"text": " Make a kick track "}
        wav = b"RIFF-audio-fixture"
        self.assertEqual(providers.transcribe(wav, DEFAULTS, []), "Make a kick track")
        voice_call = request.call_args
        for service, call in zip(PRESETS, (jev_call, gpt_call, voice_call)):
            self.assertEqual(call.args[0], PRESETS[service]["openrouter"]["endpoint"])
            self.assertEqual(call.args[1], "shared-fixture-key")
            self.assertEqual(call.args[2]["model"], DEFAULTS[service + "_model"])
        self.assertEqual(base64.b64decode(voice_call.args[2]["input_audio"]["data"]), wav)
        self.assertEqual(voice_call.args[2]["input_audio"]["format"], "wav")
        self.assertFalse(gpt_call.args[2]["store"])
        self.assertTrue(gpt_call.args[2]["text"]["format"]["strict"])

    @patch("heycomputer.providers.api_key", side_effect=lambda provider: provider + "-fixture")
    @patch("heycomputer.providers.request_json")
    def test_mixed_providers_custom_endpoints_and_models(self, request, key):
        config = dict(DEFAULTS, transcription_provider="openai",
                      transcription_endpoint="https://voice.example/v1/audio/transcriptions",
                      transcription_model="whisper-1", fallback_model="openai/gpt-6-luna",
                      fallback_endpoint="https://proxy.example/v1/responses",
                      jev_endpoint="https://openrouter.ai/api/alpha/decisions")
        request.return_value = {"text": "kick"}
        providers.transcribe(b"audio", config, ["Genome"])
        self.assertEqual(request.call_args.args[:2], (config["transcription_endpoint"], "openai-fixture"))
        self.assertIn(b"whisper-1", request.call_args.kwargs["body"])
        self.assertIn(b"Genome", request.call_args.kwargs["body"])
        request.return_value = response()
        providers.openai_plan("help", {}, config, [])
        self.assertEqual(request.call_args.args[0], config["fallback_endpoint"])
        self.assertEqual(request.call_args.args[1], "openrouter-fixture")
        self.assertEqual(request.call_args.args[2]["model"], "openai/gpt-6-luna")
        request.return_value = {"answers": {}}
        providers.jev_plan("help", {}, config)
        self.assertEqual(request.call_args.args[0], config["jev_endpoint"])

    @patch("heycomputer.providers.api_key", side_effect=lambda p: "fixture" if p == "openrouter" else "")
    @patch("heycomputer.providers.openai_plan", return_value=dict(REPLY))
    @patch("heycomputer.providers.jev_plan", side_effect=PlanError("Unavailable"))
    def test_openrouter_only_key_supports_fallback(self, jev, fallback, key):
        self.assertEqual(providers.plan("help", {}, DEFAULTS, [])["kind"], "reply")
        fallback.assert_called_once()

    @patch("heycomputer.providers.api_key", side_effect=lambda p: "fixture" if p == "typesafe" else "")
    def test_missing_openrouter_key_does_not_use_direct_key(self, key):
        result = providers.plan("help", {}, DEFAULTS, [])
        self.assertIn("OpenRouter API key", result["message"])

    @patch("heycomputer.providers.request_json", return_value={"text": None})
    @patch("heycomputer.providers.api_key", return_value="fixture")
    def test_invalid_transcript_cannot_become_command(self, key, request):
        with self.assertRaises(PlanError):
            providers.transcribe(b"audio", DEFAULTS, [])


class SettingsTests(unittest.TestCase):
    def test_legacy_install_keeps_direct_routes_and_model_choices(self):
        config = load_config({"fallback_model": "gpt-6-luna", "transcription_model": "whisper-1"})
        self.assertEqual(service_config(config, "jev"), ("typesafe", "https://api.typesafe.ai/v1/systemone", "jev-latest"))
        self.assertEqual(service_config(config, "fallback"), ("openai", "https://api.openai.com/v1/responses", "gpt-6-luna"))
        self.assertEqual(config["transcription_provider"], "openai")
        self.assertEqual(config["transcription_model"], "whisper-1")
        self.assertEqual(load_config()["jev_provider"], "openrouter")

    @patch.object(Recorder, "devices", return_value=[])
    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("keyring.set_password")
    def test_saved_routes_roundtrip_with_one_key_in_keychain(self, save_key, key, devices):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(Path(folder) / "s1")
            config = dict(DEFAULTS, transcription_endpoint="https://voice.example/transcribe",
                          transcription_model="custom/model")
            result = worker.save_settings({"config": config, "keys": {"openrouter": "private-fixture"}})
            save_key.assert_called_once_with("HeyComputer", "openrouter", "private-fixture")
            self.assertNotIn("private-fixture", worker.config_path.read_text())
            self.assertNotIn("private-fixture", json.dumps(result))
            self.assertEqual(Worker(Path(folder) / "s2").config, config)
            self.assertEqual(result["presets"], PRESETS)

    def test_invalid_endpoints_rejected_before_settings_or_key_write(self):
        endpoints = ["", "http://api.example/v1/responses", "https://api.example",
                     "https://user:secret@api.example/responses", "https://api.example/responses?key=secret",
                     "https://api.example/responses#fragment", "https://api.example:bad/responses",
                     "https://api.example/\nresponses"]
        with tempfile.TemporaryDirectory() as folder, patch("keyring.set_password") as key:
            worker = Worker(Path(folder) / "session")
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint), self.assertRaises(PlanError):
                    worker.save_settings({"config": {"fallback_endpoint": endpoint}, "keys": {"openrouter": "fixture"}})
            key.assert_not_called()
            self.assertFalse(worker.config_path.exists())


class HTTPTests(unittest.TestCase):
    @patch("urllib.request.build_opener")
    def test_bearer_transport_and_json_payload(self, build):
        build.return_value.open.return_value = io.BytesIO(b'{"answers": {}}')
        providers.request_json("https://openrouter.ai/api/v1/systemone", "fixture", {"model": "jev-latest"})
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture")
        self.assertEqual(json.loads(request.data)["model"], "jev-latest")

    def test_redirect_cannot_forward_bearer_key(self):
        with self.assertRaisesRegex(PlanError, "redirected"):
            providers.NoRedirect().redirect_request(None, None, 307, "Redirect", {}, "https://elsewhere.example/receive")

    @patch("urllib.request.build_opener")
    def test_bad_response_and_http_errors_are_sanitized(self, build):
        for data in (b"<html>private-provider-data</html>", b'{"error":"private-provider-data"}', b'[]'):
            build.return_value.open.return_value = io.BytesIO(data)
            with self.assertRaises(PlanError) as error:
                providers.request_json("https://api.example/responses", "fixture", {})
            self.assertNotIn("private-provider-data", str(error.exception))
        build.return_value.open.side_effect = urllib.error.HTTPError(
            "https://api.example/responses", 401, "private-provider-data", {}, None)
        with self.assertRaisesRegex(PlanError, "HTTP 401") as error:
            providers.request_json("https://api.example/responses", "fixture", {})
        self.assertNotIn("private-provider-data", str(error.exception))


if __name__ == "__main__":
    unittest.main()
