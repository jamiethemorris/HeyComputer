import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from heycomputer.protocol import PlanError
from heycomputer.settings import DEFAULTS, load_config
from heycomputer.voice_output import VoiceOutput, spoken_text, validate
from heycomputer.worker import Worker, atomic_json


class VoiceOutputTests(unittest.TestCase):
    def test_defaults_and_invalid_output_values(self):
        self.assertFalse(load_config({})['speech_enabled'])
        self.assertEqual(DEFAULTS['speech_volume'], .2)
        validate(DEFAULTS)
        for changes in ({'speech_volume': float('nan')}, {'speech_volume': 1.1}, {'speech_volume': True},
                        {'speech_channel': 0}, {'speech_channels': 3}, {'speech_enabled': 'yes'},
                        {'speech_voice': 'voice\nname'}, {'speech_device': None}):
            with self.subTest(changes=changes), self.assertRaises(PlanError):
                validate(dict(DEFAULTS, **changes))

    def test_plain_text_cannot_change_synthesizer_settings(self):
        text = spoken_text('[[volm 1.0]][[rate 500]] **Which track?** https://example.com/track')
        self.assertNotIn('[[', text)
        self.assertNotIn('volm', text)
        self.assertIn('Which track?', text)
        self.assertIn('link in the conversation', text)
        self.assertNotIn('[', spoken_text('[[volm 1.0'))
        self.assertLess(len(spoken_text('long answer ' * 1000)), 1601)

    @patch('heycomputer.voice_output.subprocess.run')
    @patch('heycomputer.voice_output.sys.platform', 'darwin')
    def test_installed_voices_with_spaces_and_locales(self, run):
        run.return_value = SimpleNamespace(stdout='Samantha en_US # Hello\nBad News en_US # Hello\nAmélie fr_CA # Bonjour\n')
        self.assertEqual({v['id'] for v in VoiceOutput.voices()}, {'Samantha', 'Bad News', 'Amélie'})
        self.assertEqual(run.call_args.args[0], ['/usr/bin/say', '-v', '?'])

    def test_output_mapping_gain_and_cancellation(self):
        import sounddevice as sd
        writes = []
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        cancel = threading.Event()
        def capture(block):
            writes.append(block.copy())
            cancel.set()
        stream.write.side_effect = capture
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.wav'
            with wave.open(str(path), 'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000)
                w.writeframes(np.full(4800, 16384, dtype='<i2').tobytes())
            with patch.object(sd, 'OutputStream', return_value=stream) as output:
                VoiceOutput._play(path, 7, 48000, 2, 2, .2, cancel)
        self.assertEqual(output.call_args.kwargs['device'], 7)
        self.assertEqual(output.call_args.kwargs['channels'], 4)
        self.assertEqual(len(writes), 1)
        np.testing.assert_array_equal(writes[0][:, :2], 0)
        np.testing.assert_allclose(writes[0][:, 2:], .1)
        stream.abort.assert_called_once()

    def test_unavailable_output_does_not_fall_back_to_speakers(self):
        speaker = VoiceOutput()
        with patch.object(VoiceOutput, 'devices', return_value=[]), \
             patch('heycomputer.voice_output.sys.platform', 'darwin'), \
             patch('heycomputer.voice_output.subprocess.Popen') as process:
            speaker._run('Which track?', dict(DEFAULTS, speech_device='missing'), threading.Event())
        self.assertIn('unavailable', speaker.error)
        process.assert_not_called()

    def test_zero_volume_and_stop_do_not_launch_synthesis(self):
        speaker = VoiceOutput()
        with patch.object(speaker, '_run') as run:
            speaker.speak('Which track?', dict(DEFAULTS, speech_volume=0))
            run.assert_not_called()
        self.assertFalse(speaker.active)


class VoiceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Worker(Path(self.temp.name) / 'session')
        self.worker.config['speech_enabled'] = True
        self.speaker = self.worker.speaker = Mock(active=False)
        self.state = {'session': 'session', 'ready': True, 'recording': False,
                      'transport_recording': False, 'busy': False, 'enabled': True}
        self.serial = 0

    def event(self, **changes):
        self.serial += 1
        event = {'id': f'v{self.serial}', 'session': 'session', 'created': int(time.time()),
                 'kind': 'reply', 'text': 'Which track?'}
        event.update(changes)
        atomic_json(self.worker.directory / 'speech-state.json', self.state)
        atomic_json(self.worker.directory / 'speech-command.json', event)
        self.worker.poll_speech()
        self.assertFalse((self.worker.directory / 'speech-command.json').exists())
        return event

    def test_reply_plays_once_and_never_uses_a_model(self):
        with patch('heycomputer.providers.request_json', side_effect=AssertionError('Local speech needs no API')):
            event = self.event()
            self.speaker.speak.assert_called_once_with('Which track?', self.worker.config)
            self.event(**event)
            self.assertEqual(self.speaker.speak.call_count, 1)

    def test_stale_foreign_and_busy_events_are_discarded_not_queued(self):
        for changes in ({'created': time.time()-10}, {'session': 'other'}, {'created': time.time()+20}):
            self.event(**changes)
        for key in ('recording', 'transport_recording', 'busy'):
            self.state[key] = True
            self.event()
            self.state[key] = False
        self.worker.busy = True;self.event();self.worker.busy = False
        self.worker.poll_speech()
        self.speaker.speak.assert_not_called()

    def test_preview_uses_unsaved_choices_without_saving_or_enabling(self):
        self.worker.config['speech_enabled'] = False
        self.state['enabled'] = False
        self.event(kind='preview', options={'speech_device': 'headphones', 'speech_volume': .05,
                                           'fallback_endpoint': 'ignored'})
        text, config = self.speaker.speak.call_args.args
        self.assertIn('Which track', text)
        self.assertEqual(config['speech_device'], 'headphones')
        self.assertEqual(config['speech_volume'], .05)
        self.assertNotEqual(config['fallback_endpoint'], 'ignored')
        self.assertFalse(self.worker.config['speech_enabled'])
        self.assertEqual(self.worker.config['speech_device'], '')
        self.assertFalse(self.worker.config_path.exists())

    def test_recording_and_expired_panel_state_stop_current_speech(self):
        self.speaker.active = True
        self.state['transport_recording'] = True
        self.event()
        self.speaker.stop.assert_called_once()
        self.state['transport_recording'] = False
        path = self.worker.directory / 'speech-state.json'
        atomic_json(path, self.state)
        os.utime(path, (time.time()-10, time.time()-10))
        self.worker.poll_speech()
        self.assertEqual(self.speaker.stop.call_count, 2)

    def test_stop_works_even_when_automatic_replies_are_disabled(self):
        self.state['enabled'] = False
        self.worker.config['speech_enabled'] = False
        self.event(kind='stop')
        self.speaker.stop.assert_called_once()
        self.event()
        self.speaker.speak.assert_not_called()

    def test_invalid_voice_settings_do_not_replace_saved_settings(self):
        original = dict(self.worker.config)
        atomic_json(self.worker.config_path, original)
        with self.assertRaises(PlanError):
            self.worker.save_settings({'config': {'speech_volume': 50}})
        self.assertEqual(json.loads(self.worker.config_path.read_text()), original)

    def test_starting_talk_stops_speech_before_opening_the_microphone(self):
        order = []
        self.speaker.stop.side_effect = lambda: order.append('stop speech')
        with patch.object(self.worker.recorder, 'start', side_effect=lambda _: order.append('start microphone')):
            self.worker.dispatch({'id': 'j1', 'kind': 'record_start'})
            path = self.worker.directory / 'j1.response.json'
            deadline = time.monotonic() + 3
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertEqual(order, ['stop speech', 'start microphone'])
        self.assertEqual(json.loads(path.read_text())['kind'], 'recording')

