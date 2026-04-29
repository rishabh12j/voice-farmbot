"""GrowMate Voice — voice control for the AURA FarmBot ROS2 stack.

FastAPI web app that takes browser microphone input, runs offline STT
(faster-whisper / vosk / moonshine), matches against a fixed FarmBot command
vocabulary, publishes the resulting commands to ``keyboard_topic`` (the same
topic the upstream keyboard_controller uses), and confirms with TTS
(piper / kokoro).
"""

__version__ = "0.2.0"
