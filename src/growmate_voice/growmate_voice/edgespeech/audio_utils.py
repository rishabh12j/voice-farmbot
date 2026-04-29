"""Audio helpers: mic capture, WAV I/O, padding, format conversion."""

from __future__ import annotations

import io
import wave
from math import gcd
from pathlib import Path
from typing import Tuple

import numpy as np


SAMPLE_RATE = 16_000
MIN_DURATION = 1.0


def record_mic(duration: float = 3.0, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    import sounddevice as sd

    audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate,
                   channels=1, dtype="float32")
    sd.wait()
    return audio.squeeze()


def load_wav(path: str | Path, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate != target_rate:
        audio = _resample(audio, rate, target_rate)
    return audio.astype(np.float32)


def load_wav_from_bytes(data: bytes, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    import soundfile as sf

    buf = io.BytesIO(data)
    audio, rate = sf.read(buf, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate != target_rate:
        audio = _resample(audio, rate, target_rate)
    return audio.astype(np.float32)


def pad_audio(audio: np.ndarray, min_duration: float = MIN_DURATION,
              sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    min_samples = int(min_duration * sample_rate)
    if len(audio) < min_samples:
        audio = np.concatenate(
            [audio, np.zeros(min_samples - len(audio), dtype=audio.dtype)]
        )
    return audio


def audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())
    buf.seek(0)
    return buf.read()


def audio_info(audio: np.ndarray, sr: int = SAMPLE_RATE) -> Tuple[float, float]:
    duration = len(audio) / sr
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    return duration, peak


def _resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate:
        return audio.astype(np.float32)
    try:
        from scipy.signal import resample_poly

        g = gcd(orig_rate, target_rate)
        return resample_poly(audio, target_rate // g, orig_rate // g).astype(np.float32)
    except ImportError:
        duration = len(audio) / orig_rate
        new_len = int(duration * target_rate)
        x_old = np.linspace(0, 1, len(audio))
        x_new = np.linspace(0, 1, new_len)
        return np.interp(x_new, x_old, audio).astype(np.float32)
