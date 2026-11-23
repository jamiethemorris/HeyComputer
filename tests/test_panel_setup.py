"""Exercise the packaged shell entry points, never the checkout's interpreter or pip."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class PanelSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.scripts = self.tmp / 'package with spaces' / 'scripts'
        self.scripts.mkdir(parents=True)
        self.data = self.tmp / 'panel data'
        self.bin = self.tmp / 'fake bin'
        self.bin.mkdir()
        self.env = dict(PATH=str(self.bin), HOME=str(self.tmp), PYTHONDONTWRITEBYTECODE='1')
        for name in ('dirname', 'ln', 'mkdir', 'mv', 'rmdir'):
            target = shutil.which(name)
            assert target
            (self.bin / name).symlink_to(target)
        self.fake(self.bin / 'uname', 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac')
        self.setup = self.scripts / 'setup.sh'
        self.setup.write_text((SCRIPTS / 'setup.sh').read_text())
        self.panel = self.scripts / 'panel_setup.sh'
        self.venv = self.scripts.parent / '.venv/bin/python'

    def install_panel(self):
        # Map macOS's absolute discovery locations into the fixture, not the host.
        source = (SCRIPTS / self.panel.name).read_text()
        for prefix in ('/opt/homebrew', '/usr/local', '/Library/Frameworks', '/usr/bin/python3'):
            source = source.replace(prefix, shlex.quote(str(self.tmp) + prefix))
        self.panel.write_text(source)

    def fake(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
        path.chmod(0o755)

    def invoke(self, script, *args, **env):
        return subprocess.run(['/bin/sh', str(script), *args], cwd=self.tmp,
                              env=self.env | env, capture_output=True, text=True, timeout=5)

    def check_status(self, value):
        self.assertEqual((self.data / 'setup.status').read_text(), value + '\n')
        self.assertFalse((self.data / 'setup.lock').exists())

    def test_setup_interpreter_override_and_manual_default(self):
        for name, env in [('chosen python', {'PYTHON': str(self.bin / 'chosen python')}),
                          ('python3', {})]:
            with self.subTest(name=name):
                self.fake(self.bin / name, 'test "$*" = "-m venv .venv"; exit 37')
                self.assertEqual(self.invoke(self.setup, **env).returncode, 37)

    def test_existing_venv_is_checked_and_reused(self):
        self.fake(self.venv, '''case "$1" in
-c) exit "${VERSION_RC:-0}";;
-m) echo 'pip diagnostic' >&2; exit 41;;
esac''')
        result = self.invoke(self.setup, VERSION_RC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('3.11', result.stderr)
        self.assertNotIn('pip diagnostic', result.stderr)
        result = self.invoke(self.setup)
        self.assertEqual(result.returncode, 41)
        self.assertIn('pip diagnostic', result.stderr)

    def test_panel_discovery_lock_and_log(self):
        self.install_panel()
        brew = self.tmp / 'opt/homebrew/bin/python3'
        self.fake(brew, 'exit 1')  # Too old; continue search.
        framework = self.tmp / 'Library/Frameworks/Python.framework/Versions/3.12/bin/python3'
        self.fake(framework, 'test "$1" = -c')
        self.fake(self.bin / 'python3', 'exit 0')
        self.setup.write_text('''set -eu
test "$PYTHON" = "$EXPECTED"
printf '%s\\n' 'stdout marker'
printf '%s\\n' 'stderr marker' >&2
read -r status < "$DATA/setup.status"
test "$status" = running && test -d "$DATA/setup.lock" || exit 33
# A second launcher must not change our log, status or lock.
/bin/sh "$(dirname "$0")/panel_setup.sh" "$DATA"
test -d "$DATA/setup.lock"
exit "${SETUP_RC:-0}"
''')
        for rc, status in [(29, 'failed:29'), (0, 'done')]:
            result = self.invoke(self.panel, self.data, EXPECTED=str(framework), DATA=str(self.data), SETUP_RC=str(rc))
            self.assertEqual(result.returncode, rc, result.stderr)
            self.check_status(status)
            log = (self.data / 'setup.log').read_text()
            self.assertIn('stdout marker\n', log)
            self.assertIn('stderr marker\n', log)

    def test_panel_skips_system_stub_and_explains_no_python(self):
        self.install_panel()
        stub = self.tmp / 'usr/bin/python3'
        self.fake(stub, ': > "$HOME/stub-called"')
        (self.bin / 'python3').symlink_to(stub)
        result = self.invoke(self.panel, self.data)
        self.assertFalse((self.tmp / 'stub-called').exists())
        self.assertNotEqual(result.returncode, 0)
        self.check_status('no-python')
        self.assertIn('3.11', (self.data / 'setup.log').read_text())

    def test_fresh_panel_setup_uses_path_and_tolerates_optional_model_failure(self):
        self.install_panel()
        self.fake(self.bin / 'python3', '''case "$1" in
-c) exit 0;;
-m) test "$*" = '-m venv .venv'; mkdir -p .venv/bin; ln -s "$FAKE_VENV" .venv/bin/python;;
*) exit 19;;
esac''')

        fake_venv = self.bin / 'venv python'
        self.fake(fake_venv, '''case "$1" in
-c) exit 0;;
-m) test "$*" = '-m pip install -e .[wake,local-speech]'; echo 'editable install';;
scripts/install_wake_model.py) exit 23;;
*) exit 20;;
esac''')
        result = self.invoke(self.panel, self.data, FAKE_VENV=str(fake_venv))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.check_status('done')
        log = (self.data / 'setup.log').read_text()
        self.assertIn('editable install', log)
        self.assertIn('Wake-word model was not installed', log)

    def test_panel_can_update_with_only_existing_venv(self):
        self.install_panel()
        self.fake(self.venv, 'case "$1" in -c) exit 0;; -m) exit 32;; esac')
        self.assertEqual(self.invoke(self.panel, self.data).returncode, 32)
        self.check_status('failed:32')

    def test_model_installer_runtime_arguments(self):
        self.install_panel()
        self.fake(self.venv, '''if [ "$1" = scripts/install_wake_model.py ]; then
  shift
  for arg do printf '%s\\n' "$arg"; done > "$HOME/model-args"
fi''')
        for command, expected in [([self.panel, self.data], ['--runtime', str(self.data)]),
                                  ([self.setup], [])]:
            with self.subTest(command=command):
                receipt = self.tmp / 'model-args'
                self.assertEqual(self.invoke(*command).returncode, 0)
                self.assertEqual(receipt.read_text().splitlines(), expected)
                receipt.unlink()
