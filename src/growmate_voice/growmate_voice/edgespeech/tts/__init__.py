"""TTS backends for GrowMate / EdgeSpeech."""

from typing import Any


def load_tts(name: str) -> Any:
    name = name.lower()
    if name == "piper":
        from .piper_backend import PiperBackend
        return PiperBackend()
    if name == "kokoro":
        from .kokoro_backend import KokoroBackend
        return KokoroBackend()
    if name in ("none", "off"):
        from .piper_backend import NullTTS
        return NullTTS()
    raise ValueError(f"Unknown TTS backend: {name}")
