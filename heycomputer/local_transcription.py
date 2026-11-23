"""In-memory MLX Whisper adapter for submitted Talk recordings only."""
import io
from math import gcd
import wave

from .protocol import PlanError


INSTALL_HELP = (
    "Local Whisper is not installed. On an Apple Silicon Mac, run sh scripts/setup.sh from the HeyComputer "
    "folder and reopen the panel. Otherwise choose another transcription provider in Settings under Voice input."
)


def transcribe(wav, model, prompt):
    try:
        import mlx_whisper
        import numpy as np
    except (ImportError, OSError, RuntimeError) as exc:
        raise PlanError(INSTALL_HELP) from exc

    # Recorder.stop() produces mono PCM16 at the selected device's native rate.
    # Pass a 16 kHz float waveform, so MLX never needs a WAV file or FFmpeg.
    try:
        with wave.open(io.BytesIO(wav), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth()) != (1, 2):
                raise wave.Error("Expected mono PCM16")
            rate = audio.getframerate()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32)
    except (wave.Error, EOFError, ValueError) as exc:
        raise PlanError("Could not read the recording for MLX Whisper. Try recording again.") from exc
    samples *= 1 / 32768
    if rate != 16000:
        try:
            from scipy.signal import resample_poly
        except (ImportError, OSError) as exc:
            raise PlanError(INSTALL_HELP) from exc
        divisor = gcd(16000, rate)
        samples = resample_poly(samples, 16000 // divisor, rate // divisor)

    try:
        return mlx_whisper.transcribe(samples, path_or_hf_repo=model, language="en",
                                      initial_prompt=prompt, verbose=None)
    except Exception as exc:
        raise PlanError(
            "MLX Whisper could not load or run the model. Check the model ID in Settings and "
            "your connection for the first download. If it still fails, reinstall the local-speech extra."
        ) from exc
