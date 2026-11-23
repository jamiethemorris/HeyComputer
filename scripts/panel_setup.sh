#!/bin/sh
# Usage: /bin/sh scripts/panel_setup.sh DATA_DIR
# After a hard kill, first confirm setup and its children are no longer running,
# then rmdir DATA_DIR/setup.lock and retry. Never steal a possibly live lock.
set -eu
[ "$#" -eq 1 ] && [ -n "$1" ] || { echo 'Usage: panel_setup.sh DATA_DIR' >&2; exit 2; }
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data=$1
mkdir -p -- "$data"
lock=$data/setup.lock
if ! mkdir -- "$lock" 2>/dev/null; then
  [ -d "$lock" ] && exit 0  # Another launcher owns status and log.
  echo "Cannot create setup lock: $lock" >&2
  exit 1
fi

status() {
  printf '%s\n' "$1" > "$data/setup.status.tmp" && mv -f -- "$data/setup.status.tmp" "$data/setup.status"
}
finish() {
  rc=$?
  trap - EXIT
  status "${outcome:-failed:$rc}" || rc=1
  rmdir -- "$lock" || rc=1
  exit "$rc"
}
outcome=
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
exec > "$data/setup.log" 2>&1
status running

if [ ! -e "$root/.venv/bin/python" ] && [ ! -L "$root/.venv/bin/python" ]; then
  os=$(uname -s)
  PYTHON=
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
      /opt/homebrew/opt/python@3.*/libexec/bin/python3 /usr/local/opt/python@3.*/libexec/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/*/bin/python3 "$(command -v python3 || :)"; do
    [ -x "$candidate" ] || continue
    # Even probing the Xcode stub can open an install dialog. Check aliases too.
    if [ "$os" = Darwin ] && [ "$candidate" -ef /usr/bin/python3 ]; then continue; fi
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      case $candidate in /*) PYTHON=$candidate ;; *) PYTHON=$PWD/$candidate ;; esac
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo 'Install Python 3.11 or newer from python.org or Homebrew, then run setup again.'
    outcome=no-python
    exit 1
  fi
  export PYTHON
fi
# ReaPack does not need to preserve executable bits for shell scripts.
/bin/sh "$root/scripts/setup.sh" --runtime "$data"
outcome=done
