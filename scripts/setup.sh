#!/bin/sh
set -eu
HEYCOMPUTER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$HEYCOMPUTER_ROOT"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
printf '\nSetup complete. Load reaper/HeyComputer.lua in REAPER’s Action List.\n'
