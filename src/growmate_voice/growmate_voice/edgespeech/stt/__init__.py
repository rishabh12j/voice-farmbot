"""STT backends for GrowMate / EdgeSpeech."""

from typing import Any, Optional


def load_stt(name: str, model_size: Optional[str] = None) -> Any:
    name = name.lower()
    if name == "vosk":
        from .vosk_backend import VoskBackend
        return VoskBackend()
    if name in ("whisper", "faster-whisper", "fw"):
        from .whisper_backend import WhisperBackend
        if model_size:
            return WhisperBackend(model_size=model_size)
        return WhisperBackend()
    if name == "moonshine":
        from .moonshine_backend import MoonshineBackend
        return MoonshineBackend()
    raise ValueError(f"Unknown STT backend: {name}")
