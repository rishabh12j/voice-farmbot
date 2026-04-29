"""Kokoro 82M TTS backend."""

from __future__ import annotations

from typing import Tuple

import numpy as np


class KokoroBackend:
    name = "kokoro"

    def __init__(self, voice: str = "af_heart", lang_code: str = "a", sample_rate: int = 24_000):
        self.voice = voice
        self.lang_code = lang_code
        self.sample_rate = sample_rate
        self._pipeline = None

    def _load(self) -> None:
        if self._pipeline is not None:
            return
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code=self.lang_code)

    def synthesise(self, text: str) -> Tuple[np.ndarray, int]:
        self._load()
        chunks = []
        for _g, _p, audio in self._pipeline(text, voice=self.voice):  # type: ignore[misc]
            if audio is not None:
                chunks.append(np.asarray(audio, dtype=np.float32))
        if not chunks:
            return np.zeros(self.sample_rate, dtype=np.float32), self.sample_rate
        return np.concatenate(chunks), self.sample_rate

    def is_available(self) -> bool:
        try:
            from kokoro import KPipeline  # noqa: F401
            return True
        except ImportError:
            return False
