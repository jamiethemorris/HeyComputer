#!/bin/sh
# One command for everything HeyComputer runs locally. Safe to rerun after updates.
# Arguments are passed to the wake-word model installer (e.g. --runtime PATH).
set -eu
HEYCOMPUTER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$HEYCOMPUTER_ROOT"
# The panel supplies an absolute interpreter; manual installs default to python3.
if [ ! -e .venv/bin/python ] && [ ! -L .venv/bin/python ]; then
  "${PYTHON:-python3}" -m venv .venv
fi
# Check the interpreter that will actually run pip. Never replace an old venv
# silently, or invoke a macOS developer-tools launcher through a venv symlink.
if { [ "$(uname -s)" = Darwin ] && [ .venv/bin/python -ef /usr/bin/python3 ]; } ||
   ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  printf '%s\n' 'Setup needs Python 3.11 or newer. Move the unusable .venv aside and rerun setup with a supported Python.' >&2
  exit 1
fi
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
