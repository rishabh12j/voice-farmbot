"""Speech I/O for GrowMate.

Two distinct entry points:

1. ``AudioTranscriber.transcribe(path)`` — takes a path to an audio file
   (what the Gradio mic widget produces) and returns transcribed text.
   Backed by faster-whisper, falling back to openai-whisper, falling back
   to a clear error message.

2. ``MicListener.listen()`` — legacy direct-mic capture via the
   ``speech_recognition`` library, used by the headless CLI mode.
   Kept for backwards compatibility with the original growmate-bt
   workflow but **not** the recommended path: the browser mic via the
   Gradio app is more portable and works on phones.

3. ``TextToSpeech`` — best-effort spoken output. Tries Piper, then
   pyttsx3, then macOS ``say``, then prints. The Gradio app calls
   ``synthesize_to_file`` to get a WAV path it can hand back to the
   browser; the CLI calls ``speak`` for in-process audio.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


# ---------------------------------------------------------------- transcription
class AudioTranscriber:
    """Transcribes an audio file (any common format) to text.

    Backend priority:
        1. faster-whisper (recommended; CPU-friendly, robust)
        2. openai-whisper (slower but pure-python, easier to install)
        3. None — raises a clear error message on use

    The default model is ``tiny.en`` (39 MB, ~1 s/utterance on a Pi 5).
    Pass ``model_size="base.en"`` for noticeably better accuracy at ~3x
    the latency, or ``"small.en"`` for the best CPU-only quality.
    """

    def __init__(self, model_size: str = "tiny.en", device: str = "cpu"):
        self.model_size = model_size
        self.device = device
        self.backend: Optional[str] = None
        self._model = None
        self._init_backend()

    def _init_backend(self) -> None:
        # Prefer faster-whisper.
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type="int8"
            )
            self.backend = "faster-whisper"
            print(f"[growmate_voice] STT: faster-whisper ({self.model_size}) on {self.device}")
            return
        except ImportError:
            pass
        except Exception as exc:
            print(f"[growmate_voice] faster-whisper failed to load: {exc}")

        # Fall back to openai-whisper.
        try:
            import whisper
            # openai-whisper takes a slightly different model name format.
            ow_size = self.model_size.replace(".en", "")
            self._model = whisper.load_model(ow_size)
            self.backend = "openai-whisper"
            print(f"[growmate_voice] STT: openai-whisper ({ow_size})")
            return
        except ImportError:
            pass
        except Exception as exc:
            print(f"[growmate_voice] openai-whisper failed to load: {exc}")

        self.backend = None
        print("[growmate_voice] STT: no backend available.")
        print("[growmate_voice]   Install one of:")
        print("[growmate_voice]     pip install faster-whisper   (recommended)")
        print("[growmate_voice]     pip install openai-whisper   (fallback)")

    def transcribe(self, audio_path: str) -> str:
        """Return the transcribed text. Empty string on any failure."""
        if self.backend is None or self._model is None:
            return ""
        if not audio_path or not os.path.exists(audio_path):
            return ""

        try:
            if self.backend == "faster-whisper":
                segments, _info = self._model.transcribe(
                    audio_path, beam_size=1, language="en"
                )
                return " ".join(seg.text for seg in segments).strip()
            elif self.backend == "openai-whisper":
                result = self._model.transcribe(audio_path, language="en")
                return result.get("text", "").strip()
        except Exception as exc:
            print(f"[growmate_voice] Transcription error: {exc}")
            return ""

        return ""


# ------------------------------------------------------------------ legacy mic
class MicListener:
    """Direct-mic capture via the ``speech_recognition`` library.

    Used by the CLI for backwards compatibility. The Gradio app does NOT
    use this — it gets a file path from the browser and passes it to
    AudioTranscriber instead.
    """

    def __init__(self):
        self.recognizer = None
        self.microphone = None
        self.available = False
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            self.microphone = sr.Microphone()
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
            self.available = True
            print("[growmate_voice] Mic: speech_recognition initialised")
        except Exception:
            print("[growmate_voice] Mic: unavailable (install SpeechRecognition + pyaudio)")

    def listen(self) -> str:
        if not self.available:
            return ""
        import speech_recognition as sr
        try:
            with self.microphone as source:
                print("  [MIC] Listening...", end="", flush=True)
                audio = self.recognizer.listen(source, timeout=10, phrase_time_limit=15)
                print(" processing...", end="", flush=True)
                text = self.recognizer.recognize_google(audio)
                print(" done.")
                return text
        except sr.WaitTimeoutError:
            print(" (no speech detected)")
        except sr.UnknownValueError:
            print(" (couldn't understand)")
        except Exception as exc:
            print(f" (error: {exc})")
        return ""


# ------------------------------------------------------------------ tts
class TextToSpeech:
    """Best-effort spoken output.

    Backend priority for the in-process ``speak`` method:
        1. Piper (highest quality, on-device)
        2. pyttsx3 (cross-platform, system voices)
        3. macOS ``say`` command
        4. Print only

    The ``synthesize_to_file`` method always returns a WAV path or None,
    and is what the Gradio app uses for in-browser playback.
    """

    def __init__(self):
        self.engine: Optional[str] = None
        self.tts = None
        self._piper_voice: Optional[str] = None
        self._init_engine()

    def _init_engine(self) -> None:
        # Piper has both a Python binding and a CLI; check the CLI as it's
        # more common on robot deployments.
        if shutil.which("piper") is not None:
            voice = os.environ.get("PIPER_VOICE", "")
            if voice and os.path.exists(voice):
                self._piper_voice = voice
                self.engine = "piper"
                print(f"[growmate_voice] TTS: Piper ({voice})")
                return
        # Try pyttsx3.
        try:
            import pyttsx3
            self.tts = pyttsx3.init()
            self.tts.setProperty('rate', 160)
            self.engine = "pyttsx3"
            print("[growmate_voice] TTS: pyttsx3")
            return
        except Exception:
            pass

        if sys.platform == "darwin":
            self.engine = "say"
            print("[growmate_voice] TTS: macOS say")
            return

        self.engine = "print"
        print("[growmate_voice] TTS: text-only (install pyttsx3 or piper for audio)")

    def speak(self, text: str) -> None:
        """In-process speech (CLI mode)."""
        print(f"  [GrowMate] {text}")
        if self.engine == "pyttsx3":
            try:
                self.tts.say(text)
                self.tts.runAndWait()
            except Exception:
                pass
        elif self.engine == "say":
            try:
                subprocess.run(["say", "-r", "160", text], capture_output=True, timeout=30)
            except Exception:
                pass
        elif self.engine == "piper" and self._piper_voice:
            self._piper_to_aplay(text)

    def synthesize_to_file(self, text: str) -> Optional[str]:
        """Return a path to a WAV file containing the spoken text, or None.

        Used by the Gradio app for in-browser playback.
        """
        if not text:
            return None

        out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name

        if self.engine == "piper" and self._piper_voice:
            try:
                proc = subprocess.run(
                    ["piper", "--model", self._piper_voice, "--output_file", out_path],
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode == 0 and os.path.getsize(out_path) > 0:
                    return out_path
            except Exception as exc:
                print(f"[growmate_voice] Piper error: {exc}")

        if self.engine == "say":
            # macOS: say can write AIFF, then we'd need to convert. Skip; just print.
            try:
                aiff_path = out_path.replace(".wav", ".aiff")
                subprocess.run(["say", "-o", aiff_path, text], capture_output=True, timeout=30)
                if os.path.exists(aiff_path):
                    return aiff_path
            except Exception:
                pass

        # No usable file backend.
        return None

    def _piper_to_aplay(self, text: str) -> None:
        """Pipe Piper output straight into aplay (Linux speakers)."""
        try:
            piper = subprocess.Popen(
                ["piper", "--model", self._piper_voice, "--output_raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            aplay = subprocess.Popen(
                ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                stdin=piper.stdout,
            )
            piper.stdin.write(text.encode("utf-8"))
            piper.stdin.close()
            aplay.wait(timeout=30)
        except Exception:
            pass
