"""Service presets and validation; credentials live outside settings."""
import os
import platform
import sys
from urllib.parse import urlsplit

from .protocol import PlanError
from .reaper_mcp_client import DEFAULT_COMMAND

CHATGPT_ENDPOINT = "https://api.openai.com/v1/responses"

PRESETS = {
    "jev": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/systemone", "model": "~typesafe/jev-latest"},
        "typesafe": {"endpoint": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"},
    },
    "fallback": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/responses", "model": "openai/gpt-6.1-sol"},
        "openai": {"endpoint": "https://api.openai.com/v1/responses", "model": "gpt-6.1-sol"},
        "chatgpt": {"endpoint": CHATGPT_ENDPOINT, "model": "gpt-6.1-sol"},
    },
    "transcription": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/audio/transcriptions", "model": "openai/gpt-transcribe"},
        "openai": {"endpoint": "https://api.openai.com/v1/audio/transcriptions", "model": "gpt-transcribe"},
        "mlx_whisper": {"endpoint": "", "model": "mlx-community/whisper-base.en-mlx"},
    },
}
KEY_PROVIDERS = ("openrouter", "typesafe", "openai")
DEFAULTS = {"use_jev": False, "allow_lua": False, "mic_device": "", "mic_channel": 1, "aliases": "", "speech_corrections": "",
            "reaper_mcp_enabled": False,
            "vision_enabled": False, "ui_memory_enabled": True,
            "wake_enabled": False, "wake_phrase": "hey computer", "wake_stop_phrase": "execute",
            "wake_threshold": 0.35, "wake_log_enabled": True,
            "speech_enabled": False, "speech_voice": "", "speech_device": "",
            "speech_channel": 1, "speech_channels": 2, "speech_volume": .2}
# New installs: conversation through the ChatGPT account, which cannot transcribe,
# so Talk uses local Whisper where it can run. Everything else is OpenRouter.
APPLE_SILICON = sys.platform == "darwin" and platform.machine() == "arm64"
NEW_INSTALL_PROVIDER = {"fallback": "chatgpt", "transcription": "mlx_whisper" if APPLE_SILICON else "openrouter"}
for service, presets in PRESETS.items():
    provider = NEW_INSTALL_PROVIDER.get(service, "openrouter")
    DEFAULTS[service + "_provider"] = provider
    for field, value in presets[provider].items():
        DEFAULTS[service + "_" + field] = value


def reaper_mcp_command() -> str:
    """Resolve the executable from the trusted helper environment only."""
    # DEFAULTS is also the save-settings allowlist. Keep executable input out of it.
    return os.environ.get("HEYCOMPUTER_REAPER_MCP_COMMAND", DEFAULT_COMMAND)


def load_config(saved=None):
    config = dict(DEFAULTS)
    if not isinstance(saved, dict):
        return config
    # Old installs keep their direct-provider routes until the user switches.
    for service in PRESETS:
        if service + "_provider" not in saved:
            provider = "typesafe" if service == "jev" else "openai"
            config[service + "_provider"] = provider
            for field, value in PRESETS[service][provider].items():
                config[service + "_" + field] = value
    config.update({k: v for k, v in saved.items() if k in DEFAULTS})
    config["reaper_mcp_enabled"] = config["reaper_mcp_enabled"] is True
    if config["fallback_provider"] == "chatgpt":
        config["fallback_endpoint"] = CHATGPT_ENDPOINT
        config["fallback_model"] = saved.get("fallback_model", PRESETS["fallback"]["chatgpt"]["model"])
    if config["transcription_provider"] == "mlx_whisper":
        preset = PRESETS["transcription"]["mlx_whisper"]
        config["transcription_endpoint"] = preset["endpoint"]
        config["transcription_model"] = saved.get("transcription_model", preset["model"])
    return config


def validate_endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise PlanError("Enter a complete API endpoint URL without spaces.")
    try:
        url = urlsplit(value)
        _ = url.port  # Force validation of a malformed port.
        valid = (url.scheme == "https" or
                 (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))
        valid = valid and bool(url.hostname) and bool(url.path.strip("/"))
        valid = valid and not (url.username or url.password or url.query or url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PlanError("Use a complete HTTPS endpoint without credentials, query or fragment. Localhost may use HTTP.")
    return value


def service_config(config, service):
    provider = config.get(service + "_provider", DEFAULTS[service + "_provider"])
    if not isinstance(provider, str) or provider not in PRESETS[service]:
        raise PlanError("Choose a supported API provider in Settings.")
    preset = PRESETS[service][provider]
    endpoint = preset["endpoint"] if provider in ("chatgpt", "mlx_whisper") else validate_endpoint(
        config.get(service + "_endpoint", preset["endpoint"]))
    model = config.get(service + "_model", preset["model"])
    if not isinstance(model, str) or not model or len(model) > 200 or any(c.isspace() for c in model):
        raise PlanError("Enter a model ID without spaces in Settings.")
    return provider, endpoint, model
