"""Explicit pre-2026-11 routing for tests that exercise the OpenRouter/Jev paths.

New installs now default to ChatGPT conversation, local Whisper and Jev off, so these tests
state the route they need instead of inheriting it from settings.DEFAULTS."""
from heycomputer.settings import DEFAULTS, PRESETS

OPENROUTER_DEFAULTS = dict(DEFAULTS, use_jev=True, fallback_provider="openrouter",
                           transcription_provider="openrouter",
                           **{service + "_" + k: v for service in ("fallback", "transcription")
                              for k, v in PRESETS[service]["openrouter"].items()})
