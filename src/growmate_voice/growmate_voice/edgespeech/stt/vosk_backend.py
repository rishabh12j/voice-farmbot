"""Vosk backend — grammar-constrained to GrowMate voice vocabulary."""

from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

import numpy as np

from ..command_map import ALL_VARIANTS


MODEL_PATH_ENV = "VOSK_MODEL_PATH"


class VoskBackend:
    name = "vosk"

    def __init__(self, model_path: Optional[str] = None, sample_rate: int = 16_000):
        self.model_path = model_path or os.environ.get(MODEL_PATH_ENV, "")
        self.sample_rate = sample_rate
        self._model = None
        self._rec = None

    def _load(self) -> None:
        if self._rec is not None:
            return
        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not self.model_path or not os.path.isdir(self.model_path):
            raise RuntimeError(
                f"Vosk model not found. Set {MODEL_PATH_ENV} to an unpacked model directory."
            )
        SetLogLevel(-1)
        self._model = Model(self.model_path)
        grammar = json.dumps(ALL_VARIANTS + ["[unk]"])
        self._rec = KaldiRecognizer(self._model, self.sample_rate, grammar)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> Tuple[str, float]:
        self._load()
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        t0 = time.perf_counter()
        self._rec.AcceptWaveform(pcm)
        final = json.loads(self._rec.FinalResult())
        text = (final.get("text") or "").strip()
        if text == "[unk]":
            text = ""
        return text, (time.perf_counter() - t0) * 1000.0

    def is_available(self) -> bool:
        try:
            from vosk import Model  # noqa: F401
        except ImportError:
            return False
        return bool(self.model_path) and os.path.isdir(self.model_path)
