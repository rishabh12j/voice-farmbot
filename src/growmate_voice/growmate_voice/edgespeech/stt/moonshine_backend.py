"""Moonshine ONNX backend — tiny/base."""

from __future__ import annotations

import time
from typing import Tuple

import numpy as np

from ..audio_utils import SAMPLE_RATE, pad_audio


class MoonshineBackend:
    name = "moonshine"

    def __init__(self, model_name: str = "moonshine/tiny"):
        self.model_name = model_name
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from moonshine_onnx import MoonshineOnnxModel

        self._model = MoonshineOnnxModel(model_name=self.model_name)

    def transcribe(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Tuple[str, float]:
        self._load()
        audio = pad_audio(audio, min_duration=0.5, sample_rate=sample_rate)
        t0 = time.perf_counter()
        tokens = self._model.generate(audio[np.newaxis, :].astype(np.float32))
        text = self._model.decode(tokens)
        if isinstance(text, list):
            text = " ".join(text)
        return str(text).strip(), (time.perf_counter() - t0) * 1000.0

    def is_available(self) -> bool:
        try:
            from moonshine_onnx import MoonshineOnnxModel  # noqa: F401
            return True
        except ImportError:
            return False
