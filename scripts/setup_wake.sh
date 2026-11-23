#!/bin/sh
# Kept for older instructions: setup.sh now installs the wake-word engine and model.
set -eu
exec sh "$(dirname -- "$0")/setup.sh" "$@"
