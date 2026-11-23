import unittest

from heycomputer.protocol import PlanError
from heycomputer.settings import KEY_PROVIDERS, PRESETS, load_config, service_config


class ChatGPTSettingsTests(unittest.TestCase):
    def test_account_provider_is_only_for_planning_and_visual_control(self):
        self.assertNotIn("chatgpt", KEY_PROVIDERS)
        for service in ("jev", "transcription"):
            with self.subTest(service=service):
                self.assertNotIn("chatgpt", PRESETS[service])
                with self.assertRaises(PlanError):
                    service_config({service + "_provider": "chatgpt"}, service)
        provider, endpoint, model = service_config({"fallback_provider": "chatgpt"}, "fallback")
        self.assertEqual(provider, "chatgpt")
        self.assertEqual(endpoint, "https://api.openai.com/v1/responses")
        self.assertEqual(model, PRESETS["fallback"]["chatgpt"]["model"])

    def test_account_provider_ignores_legacy_or_untrusted_endpoint_but_keeps_model(self):
        config = load_config({"fallback_provider": "chatgpt", "fallback_endpoint": "https://example.invalid/responses",
                              "fallback_model": "fixture-model"})
        preset = PRESETS["fallback"]["chatgpt"]
        self.assertEqual(config["fallback_endpoint"], preset["endpoint"])
        config["fallback_endpoint"] = "https://example.invalid/responses"
        self.assertEqual(service_config(config, "fallback"), ("chatgpt", preset["endpoint"], "fixture-model"))

    def test_saved_provider_without_model_gets_its_own_default(self):
        config = load_config({"fallback_provider": "chatgpt"})
        self.assertEqual(config["fallback_model"], PRESETS["fallback"]["chatgpt"]["model"])

    def test_new_installs_default_to_chatgpt_conversation_without_jev(self):
        preset = PRESETS["fallback"]["chatgpt"]
        config = load_config(None)  # no settings.json yet
        self.assertIs(config["use_jev"], False)
        self.assertEqual(service_config(config, "fallback"), ("chatgpt", preset["endpoint"], preset["model"]))
        # A ChatGPT sign-in cannot transcribe, so Talk is local wherever Whisper can run.
        from heycomputer.settings import APPLE_SILICON
        expected = "mlx_whisper" if APPLE_SILICON else "openrouter"
        self.assertEqual(service_config(config, "transcription")[0], expected)
        # A settings file with no provider keys is an old install and keeps its direct routes.
        self.assertEqual(load_config({})["fallback_provider"], "openai")

    def test_saved_choices_and_stale_remote_keys_are_respected(self):
        config = load_config({"use_jev": True, "fallback_provider": "openrouter", "fallback_model": "saved",
                              "fallback_endpoint": "https://openrouter.ai/api/v1/responses",
                              "remote_enabled": True, "remote_port": 9999})
        self.assertIs(config["use_jev"], True)
        self.assertEqual(config["fallback_provider"], "openrouter")
        self.assertEqual(config["fallback_model"], "saved")
        self.assertFalse(any(key.startswith("remote") for key in config))
