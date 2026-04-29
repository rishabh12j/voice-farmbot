"""Piper TTS backend (ONNX, CPU-only, in-process via piper Python API)."""

from __future__ import annotations

import io
import os
import wave
from typing import Tuple

import numpy as np


class PiperBackend:
    name = "piper"

    def __init__(self, onnx_path: str | None = None, config_path: str | None = None):
        self.onnx_path = onnx_path or os.environ.get("PIPER_VOICE_MODEL", "")
        self.config_path = config_path or os.environ.get("PIPER_VOICE_CONFIG", "")
        self._voice = None

    def _load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice

        if not self.onnx_path or not os.path.isfile(self.onnx_path):
            raise RuntimeError(
                "Piper voice not found. Set PIPER_VOICE_MODEL to an .onnx file "
                "and PIPER_VOICE_CONFIG to its .onnx.json."
            )
        self._voice = PiperVoice.load(
            self.onnx_path,
            config_path=self.config_path or None,
            use_cuda=False,
        )

    def synthesise(self, text: str) -> Tuple[np.ndarray, int]:
        self._load()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self._voice.synthesize(text, wf)  # type: ignore[union-attr]
        buf.seek(0)
        with wave.open(buf, "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, sr

    def is_available(self) -> bool:
        try:
            from piper import PiperVoice  # noqa: F401
        except ImportError:
            return False
        return bool(self.onnx_path) and os.path.isfile(self.onnx_path)


class NullTTS:
    name = "none"

    def synthesise(self, text: str) -> Tuple[np.ndarray, int]:
        return np.zeros(16_000, dtype=np.float32), 16_000

    def is_available(self) -> bool:
        return True
