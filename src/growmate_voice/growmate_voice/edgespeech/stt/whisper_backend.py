"""Faster-Whisper tiny.en backend."""

from __future__ import annotations

import time
from typing import Tuple

import numpy as np

from ..audio_utils import SAMPLE_RATE, pad_audio


class WhisperBackend:
    name = "whisper"

    def __init__(self, model_size: str = "tiny.en"):
        self.model_size = model_size
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Tuple[str, float]:
        self._load()
        audio = pad_audio(audio, min_duration=1.0, sample_rate=sample_rate)
        t0 = time.perf_counter()
        segments, _info = self._model.transcribe(  # type: ignore[attr-defined]
            audio,
            language="en",
            vad_filter=False,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (time.perf_counter() - t0) * 1000.0

    def is_available(self) -> bool:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
            return True
        except ImportError:
            return False
