#!/bin/sh
# One command for everything HeyComputer runs locally. Safe to rerun after updates.
# Arguments are passed to the wake-word model installer (e.g. --runtime PATH).
set -eu
HEYCOMPUTER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$HEYCOMPUTER_ROOT"
python3 -m venv .venv
# Local Whisper is the default transcription on Apple Silicon; it cannot run elsewhere.
if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
  EXTRAS='.[wake,local-speech]'
else
  EXTRAS='.[wake]'
fi
.venv/bin/python -m pip install -e "$EXTRAS"
# The wake-word model (about 5.5 MB) is optional at runtime: a failed download
# must not leave the rest of the setup looking broken.
if ! .venv/bin/python scripts/install_wake_model.py "$@"; then
  printf '\nWake-word model was not installed. Rerun this script to try again; everything else is ready.\n' >&2
fi
printf '\nSetup complete. Load reaper/HeyComputer.lua in REAPER’s Action List.\n'
