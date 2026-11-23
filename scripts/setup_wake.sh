#!/bin/sh
set -eu
HEYCOMPUTER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$HEYCOMPUTER_ROOT"
if [ ! -x .venv/bin/python ]; then
  sh scripts/setup.sh
fi
.venv/bin/python -m pip install -e '.[wake]'
.venv/bin/python scripts/install_wake_model.py "$@"
printf '\nReopen HeyComputer, then enable Local wake & stop words in Settings.\n'
