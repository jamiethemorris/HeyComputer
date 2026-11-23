"""Optional local macOS speech, played through a dedicated output stream."""
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave

from .protocol import PlanError

FIELDS = ('speech_enabled', 'speech_voice', 'speech_device', 'speech_channel', 'speech_channels', 'speech_volume')


def validate(config):
    if type(config.get('speech_enabled')) is not bool:
        raise PlanError('Choose whether spoken replies are enabled.')
    for key in ('speech_voice', 'speech_device'):
        if not isinstance(config.get(key), str) or len(config[key]) > 256 or any(ord(c) < 32 for c in config[key]):
            raise PlanError('Choose an installed voice and audio output.')
    if type(config.get('speech_channel')) is not int or not 1 <= config['speech_channel'] <= 256:
        raise PlanError('Choose a valid speech output channel.')
    if type(config.get('speech_channels')) is not int or config['speech_channels'] not in (1, 2):
        raise PlanError('Choose mono or stereo speech output.')
    volume = config.get('speech_volume')
    if type(volume) not in (int, float) or not math.isfinite(volume) or not 0 <= volume <= 1:
        raise PlanError('Speech volume must be between 0 and 100 percent.')


def spoken_text(text):
    # Treat responses as plain text, never Speech Synthesis Manager commands.
    text = re.sub(r'\[\[.*?\]\]', '', str(text), flags=re.S)
    text = text.replace('[', '').replace(']', '')
    text = re.sub(r'https?://\S+', 'link in the conversation', text)
    text = re.sub(r'[`*#_]', '', text)
    text = ' '.join(text.split())
    if len(text) > 1600:
        text = text[:1550].rsplit(' ', 1)[0] + '. The rest is in the conversation.'
    return text


class VoiceOutput:
    def __init__(self):
        self._lock = threading.Lock()
        self._control = threading.RLock()
        self._thread = None
        self._process = None
        self._cancel = threading.Event()
        self._echo_until = 0.0
        self.error = ''

    @staticmethod
    def voices():
        if sys.platform != 'darwin':
            return []
        result = subprocess.run(['/usr/bin/say', '-v', '?'], capture_output=True, text=True, timeout=5, check=True)
        voices = []
        for line in result.stdout.splitlines():
            match = re.fullmatch(r'(.+?)\s+([a-z]{2,3}_[A-Za-z_]+)\s+#.*', line)
            if match:
                voices.append({'id': match[1].strip(), 'name': match[1].strip(), 'language': match[2]})
        return sorted(voices, key=lambda v: (not v['language'].startswith('en_'), v['name']))

    @staticmethod
    def devices():
        import sounddevice as sd
        hosts = sd.query_hostapis()
        return [{'id': f'{hosts[d["hostapi"]]["name"]}: {d["name"]}', 'name': d['name'],
                 'channels': d['max_output_channels'], 'index': i, 'default': i == sd.default.device[1]}
                for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0]

    @property
    def active(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def suppress_wake(self):
        return self.active or time.monotonic() < self._echo_until

    def info(self):
        return {'speaking': self.active, 'error': self.error}

    def stop(self):
        with self._control:
            self._cancel.set()
            with self._lock:
                process = self._process
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(timeout=3)
            self._echo_until = time.monotonic() + .6

    def speak(self, text, config):
        validate(config)
        text = spoken_text(text)
        with self._control:
            self.stop()
            if self.active:
                self.error = 'Voice output is still stopping. Try again.'
                return
            self.error = ''
            if not text or config['speech_volume'] == 0:
                return
            self._cancel = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(text, dict(config), self._cancel), daemon=True)
            self._thread.start()

    def _run(self, text, config, cancel):
        try:
            if sys.platform != 'darwin':
                raise PlanError('Local spoken replies currently require macOS.')
            import sounddevice as sd
            selected = config['speech_device']
            matches = [d for d in self.devices() if d['id'] == selected]
            if selected and len(matches) != 1:
                raise PlanError('The selected speech output is unavailable. Choose it again in Settings.')
            device = matches[0]['index'] if selected else None
            info = sd.query_devices(device, 'output')
            first, count = config['speech_channel'] - 1, config['speech_channels']
            if first + count > info['max_output_channels']:
                raise PlanError('Those speech output channels are unavailable. Choose them again in Settings.')
            rate = int(info['default_samplerate'])
            voice = config['speech_voice']
            if voice and voice not in {v['id'] for v in self.voices()}:
                raise PlanError('That voice is unavailable. Choose an installed macOS voice.')
            if cancel.is_set():
                return
            # No shell, text in argv, network synthesis, or persistent audio.
            with tempfile.TemporaryDirectory(prefix='heycomputer-voice-') as directory:
                path = Path(directory) / 'reply.wav'
                command = ['/usr/bin/say', '-o', str(path), '--file-format=WAVE',
                           f'--data-format=LEI16@{rate}', '--channels=1', '-f', '-']
                if voice:
                    command += ['-v', voice]
                with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, text=True) as process:
                    with self._lock:
                        self._process = process
                    try:
                        if cancel.is_set():
                            process.terminate()
                        process.communicate(text, timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.communicate()
                        raise PlanError('Local voice synthesis timed out. Try a different macOS voice.') from None
                    finally:
                        with self._lock:
                            self._process = None
                    if cancel.is_set():
                        return
                    if process.returncode:
                        raise PlanError('macOS could not speak that reply. Try a different installed voice.')
                self._play(path, device, rate, first, count, config['speech_volume'], cancel)
        except Exception as exc:
            if not cancel.is_set():
                self.error = str(exc) if isinstance(exc, PlanError) else 'Voice output unavailable. Check the selected output and voice in Settings.'
        finally:
            self._echo_until = time.monotonic() + .6

    @staticmethod
    def _play(path, device, rate, first, count, volume, cancel):
        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), 'rb') as audio:
            if audio.getsampwidth() != 2 or audio.getnchannels() != 1 or audio.getframerate() != rate or not audio.getnframes():
                raise PlanError('The macOS voice returned no usable audio. Try another installed voice.')
            with sd.OutputStream(device=device, samplerate=rate, channels=first+count, dtype='float32') as stream:
                while not cancel.is_set():
                    block = audio.readframes(max(1, int(rate * .04)))
                    if not block:
                        break
                    mono = np.frombuffer(block, dtype='<i2').astype('float32') / 32768
                    # Attenuation only: never normalize quiet speech upwards.
                    mono = np.clip(mono * volume, -.8, .8)
                    output = np.zeros((len(mono), first+count), dtype='float32')
                    output[:, first:first+count] = mono[:, None]
                    stream.write(output)
                if cancel.is_set():
                    stream.abort()
