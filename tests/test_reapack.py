"""Offline checks for the hand-maintained ReaPack release."""
import re
import tomllib
import unittest
import xml.etree.ElementTree as ET
from itertools import takewhile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
PANEL = 'reaper/HeyComputer.lua'
HEADER = ''.join(takewhile(lambda line: line.startswith('--'),
                           (ROOT / PANEL).read_text().splitlines(keepends=True)))
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']


def runtime_files():
    paths = {'pyproject.toml', PANEL, 'reaper/HeyComputer - Send.lua',
             'reaper/HeyComputer - Talk or stop and send.lua', 'reaper/lib/setup.lua',
             'scripts/panel_setup.sh', 'scripts/setup.sh', 'scripts/setup_wake.sh',
             'scripts/install_wake_model.py', 'scripts/run_worker.py'}
    for pattern in ('reaper/lib/*', 'heycomputer/**/*.py'):
        paths.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file())
    return paths


class ReaPackManifestTests(unittest.TestCase):
    def release(self):
        path = ROOT / 'index.xml'
        self.assertTrue(path.is_file(), 'missing index.xml')
        index = ET.parse(path).getroot()  # Repository fixture; no remote XML is read.
        self.assertEqual((index.tag, index.get('version')), ('index', '1'))
        self.assertEqual(index.get('name'), 'HeyComputer')
        self.assertEqual([c.get('name') for c in index.findall('category')], ['Assistant'])
        packages = index.findall('category/reapack')
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].get('type'), 'script')
        self.assertEqual(packages[0].get('name'), 'HeyComputer.lua')
        return packages[0].findall('version')[-1]

    def test_release_versions_match(self):
        self.assertEqual(re.findall(r'^-- @version (\S+)$', HEADER, re.M), [VERSION])
        self.assertEqual(self.release().get('name'), VERSION)

    def test_index_installs_complete_runtime_at_repo_relative_paths(self):
        sources = self.release().findall('source')
        self.assertCountEqual([s.get('file') for s in sources], runtime_files())
        for s in sources:
            name = s.attrib['file']
            with self.subTest(file=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertEqual(s.text, f'https://github.com/jamiethemorris/HeyComputer/raw/v{VERSION}/{quote(name)}')
                self.assertEqual(s.get('main'), 'main' if name == PANEL else None)
                self.assertEqual(s.get('platform'), 'darwin')

    def test_provides_installs_same_runtime(self):
        self.assertIn('-- @metapackage', HEADER.splitlines())  # No implicit second panel.
        block = re.search(r'^-- @provides\n((?:--   .+\n)+)', HEADER, re.M)
        if not block:
            self.fail('missing @provides list')
        installed = []
        for line in block[1].splitlines():
            entry = re.fullmatch(r'--   \[darwin (main=main|nomain)\] (.+) > (.+)', line)
            if not entry:
                self.fail(line)
            action, pattern, destination = entry.groups()
            matches = list((ROOT / 'reaper').glob(pattern))
            self.assertTrue(matches, pattern)
            for path in matches:
                source = path.resolve().relative_to(ROOT).as_posix()
                target = destination + path.name if destination.endswith('/') else destination
                self.assertEqual(source, target)
                self.assertEqual(action, 'main=main' if source == PANEL else 'nomain')
                installed.append(target)
        self.assertCountEqual(installed, runtime_files())
