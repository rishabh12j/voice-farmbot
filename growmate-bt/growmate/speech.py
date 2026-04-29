"""
GrowMate Speech Module -- STT + TTS

Handles:
- Speech-to-Text: Moonshine (if available) -> System STT fallback
- Text-to-Speech: Piper (if available) -> System TTS fallback

For MVP: uses system speech recognition (Google/Vosk via speech_recognition)
and pyttsx3 for TTS. These work on any platform without special hardware.

For production: swap to Moonshine STT + Piper TTS for on-device processing.
"""

import subprocess
import sys
import os


class SpeechToText:
    """
    Converts speech to text.
    
    Priority: Moonshine -> SpeechRecognition (Google) -> Manual input fallback
    """

    def __init__(self):
        self.engine = None
        self.recognizer = None
        self.microphone = None
        self._init_engine()

    def _init_engine(self):
        """Try to initialize the best available STT engine."""
        # Try speech_recognition library
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            self.microphone = sr.Microphone()
            self.engine = "speech_recognition"
            # Adjust for ambient noise on first init
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("  [MIC] STT: Using system microphone (speech_recognition)")
            return
        except (ImportError, OSError, AttributeError) as e:
            pass

        # Fallback: manual text input
        self.engine = "manual"
        print("  [KB]  STT: No microphone available -- using text input")
        print("  [HINT] Install speech recognition: pip install SpeechRecognition pyaudio")

    def listen(self) -> str:
        """Listen for speech and return text. Returns empty string on failure."""
        if self.engine == "speech_recognition":
            return self._listen_sr()
        else:
            return self._listen_manual()

    def _listen_sr(self) -> str:
        """Listen using speech_recognition library."""
        import speech_recognition as sr
        try:
            with self.microphone as source:
                print("  [MIC] Listening...", end="", flush=True)
                audio = self.recognizer.listen(source, timeout=10, phrase_time_limit=15)
                print(" processing...", end="", flush=True)
                text = self.recognizer.recognize_google(audio)
                print(f" PASS")
                return text
        except sr.WaitTimeoutError:
            print(" (no speech detected)")
            return ""
        except sr.UnknownValueError:
            print(" (couldn't understand)")
            return ""
        except sr.RequestError as e:
            print(f" (recognition error: {e})")
            return ""
        except Exception as e:
            print(f" (error: {e})")
            return ""

    def _listen_manual(self) -> str:
        """Fallback: manual text input."""
        try:
            return input("  [MIC] You: ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""


class TextToSpeech:
    """
    Converts text to speech.
    
    Priority: Piper -> pyttsx3 -> print-only fallback
    """

    def __init__(self):
        self.engine = None
        self.tts = None
        self._init_engine()

    def _init_engine(self):
        """Try to initialize the best available TTS engine."""
        # Try pyttsx3
        try:
            import pyttsx3
            self.tts = pyttsx3.init()
            self.tts.setProperty('rate', 160)  # Slower for elderly users
            self.engine = "pyttsx3"
            print("  [TTS] TTS: Using system voice (pyttsx3)")
            return
        except (ImportError, RuntimeError, OSError):
            pass

        # Try macOS say command
        if sys.platform == "darwin":
            self.engine = "say"
            print("  [TTS] TTS: Using macOS say")
            return

        # Fallback: print only
        self.engine = "print"
        print("  [TEXT] TTS: No voice output -- text only")
        print("  [HINT] Install TTS: pip install pyttsx3")

    def speak(self, text: str):
        """Speak the given text."""
        # Always print the text
        print(f"  [ROBOT] GrowMate: {text}")

        if self.engine == "pyttsx3":
            try:
                self.tts.say(text)
                self.tts.runAndWait()
            except Exception:
                pass
        elif self.engine == "say":
            try:
                subprocess.run(["say", "-r", "160", text],
                             capture_output=True, timeout=30)
            except Exception:
                pass
        # "print" engine: already printed above
