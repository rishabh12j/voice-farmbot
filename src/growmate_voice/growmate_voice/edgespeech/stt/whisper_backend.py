"""Faster-Whisper STT backend with plant-name prompt biasing.

Default model is ``small.en`` — a sweet spot for elderly speech: faster
than ``medium`` (~3× quicker on CPU) but still markedly more accurate than
``tiny`` for uncommon vocabulary like ``Begonia`` / ``Euonymus`` /
``Dianthus``. Pass ``--whisper-model medium.en`` (or ``large-v3``) from the
app's CLI for higher accuracy at the cost of latency.

The ``initial_prompt`` is fed to Whisper to bias decoding toward known
plant names. The web app builds the prompt at startup from the garden
config and calls ``set_initial_prompt`` before the first transcription.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import numpy as np

from ..audio_utils import SAMPLE_RATE, pad_audio


class WhisperBackend:
    name = "whisper"

    def __init__(self, model_size: str = "small.en",
                 initial_prompt: Optional[str] = None):
        self.model_size = model_size
        self.initial_prompt = initial_prompt
        self._model = None
        # Cache the loaded model's identity so changing model_size triggers a reload
        self._loaded_size: Optional[str] = None

    # -------------------------------------------------------- configuration

    def set_model_size(self, model_size: str) -> None:
        """Change the model. Reloads on the next ``transcribe`` call."""
        if model_size != self.model_size:
            self.model_size = model_size
            self._model = None
            self._loaded_size = None

    def set_initial_prompt(self, prompt: Optional[str]) -> None:
        """Bias future transcriptions toward the words in ``prompt``.

        Whisper uses this as a soft prior — it doesn't constrain the output,
        but mention 'Begonia' once and the decoder is much less likely to
        emit 'bag onya'. Aim for ~50-200 tokens; longer prompts get truncated
        by the underlying model.
        """
        self.initial_prompt = (prompt or "").strip() or None

    # -------------------------------------------------------- model loading

    def _load(self) -> None:
        if self._model is not None and self._loaded_size == self.model_size:
            return
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_size, device="cpu", compute_type="int8"
        )
        self._loaded_size = self.model_size

    # -------------------------------------------------------- transcribe

    def transcribe(self, audio: np.ndarray,
                   sample_rate: int = SAMPLE_RATE) -> Tuple[str, float]:
        self._load()
        audio = pad_audio(audio, min_duration=1.0, sample_rate=sample_rate)
        t0 = time.perf_counter()
        segments, _info = self._model.transcribe(  # type: ignore[attr-defined]
            audio,
            language="en",
            vad_filter=False,
            # Wider beam helps elderly speech where the most-likely token
            # often isn't the correct one — costs ~30% more latency but
            # routinely catches mis-hearings.
            beam_size=3,
            best_of=3,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (time.perf_counter() - t0) * 1000.0

    def is_available(self) -> bool:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
            return True
        except ImportError:
            return False
