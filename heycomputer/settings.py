"""Service presets and validation; API credentials live separately in Keychain."""
from urllib.parse import urlsplit

from .protocol import PlanError

PRESETS = {
    "jev": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/systemone", "model": "~typesafe/jev-latest"},
        "typesafe": {"endpoint": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"},
    },
    "fallback": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/responses", "model": "openai/gpt-6.1-sol"},
        "openai": {"endpoint": "https://api.openai.com/v1/responses", "model": "gpt-6.1-sol"},
    },
    "transcription": {
        "openrouter": {"endpoint": "https://openrouter.ai/api/v1/audio/transcriptions", "model": "openai/gpt-transcribe"},
        "openai": {"endpoint": "https://api.openai.com/v1/audio/transcriptions", "model": "gpt-transcribe"},
    },
}
KEY_PROVIDERS = ("openrouter", "typesafe", "openai")
DEFAULTS = {"use_jev": True, "allow_lua": False, "mic_device": "", "mic_channel": 1, "aliases": "", "speech_corrections": "",
            "remote_enabled": False, "remote_port": 8765, "vision_enabled": False, "ui_memory_enabled": True}
for service, presets in PRESETS.items():
    DEFAULTS[service + "_provider"] = "openrouter"
    for field, value in presets["openrouter"].items():
        DEFAULTS[service + "_" + field] = value


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
    if provider not in PRESETS[service]:
        raise PlanError("Choose a supported API provider in Settings.")
    preset = PRESETS[service][provider]
    endpoint = validate_endpoint(config.get(service + "_endpoint", preset["endpoint"]))
    model = config.get(service + "_model", preset["model"])
    if not isinstance(model, str) or not model or len(model) > 200 or any(c.isspace() for c in model):
        raise PlanError("Enter a model ID without spaces in Settings.")
    return provider, endpoint, model
