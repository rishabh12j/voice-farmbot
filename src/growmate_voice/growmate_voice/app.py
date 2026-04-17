"""GrowMate Gradio web app — jog panel with voice control.

Six-command gantry control designed for elderly users:
  - Large, high-contrast buttons on a dark background
  - Emergency stop + reset
  - Four directional arrows (X+/X-/Y+/Y-)
  - Voice input via browser mic, classified by string matching (no LLM)

Usage::

    python -m growmate_voice.app --no-ros2
    python -m growmate_voice.app            # real robot, ROS2 required
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .ros2_publisher import ROS2Publisher
from .speech import AudioTranscriber


# --------------------------------------------------------------------------- state
@dataclass
class AppState:
    robot: Optional[ROS2Publisher] = None
    transcriber: Optional[AudioTranscriber] = None
    ros2_enabled: bool = True
    init_lock: threading.Lock = field(default_factory=threading.Lock)
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    farmbot_process: Optional[Any] = None   # Popen handle for farmbot_bringup


_STATE = AppState()

_BOUNDS = {"x": (0.0, 2800.0), "y": (0.0, 5800.0), "z": (-500.0, 0.0)}

_CSS = """
/* ── global dark background ── */
body,
.gradio-container,
.gradio-container > .main,
.contain,
div.block {
    background: #1a1a1a !important;
    color: #e8e8e8 !important;
}
footer { display: none !important; }

/* Gradio default light-mode overrides */
.gradio-container .prose,
.gradio-container label,
.gradio-container .block,
.gradio-container .form {
    background: #1a1a1a !important;
    color: #e8e8e8 !important;
}

/* ── textbox wrappers ── */
.gradio-container .wrap,
.gradio-container input,
.gradio-container textarea {
    background: #2d2d2d !important;
    color: #e8e8e8 !important;
    border-color: #404040 !important;
}

/* ── position status (green tint) ── */
.status-box textarea {
    font-size: 1.35em !important;
    font-family: ui-monospace, monospace !important;
    font-weight: 700 !important;
    background: #0d2218 !important;
    color: #6fcf97 !important;
    border: 2px solid #2d6a4f !important;
    border-radius: 12px !important;
    padding: 12px 16px !important;
    line-height: 1.6 !important;
}
.status-box label span { color: #6fcf97 !important; }

/* ── voice feedback (blue tint) ── */
.voice-feedback textarea {
    font-size: 1.2em !important;
    font-family: ui-monospace, monospace !important;
    font-weight: 600 !important;
    background: #0d1a2e !important;
    color: #90caf9 !important;
    border: 2px solid #1e3a5f !important;
    border-radius: 12px !important;
    padding: 12px 16px !important;
    line-height: 1.6 !important;
}
.voice-feedback label span { color: #90caf9 !important; }

/* ── safety buttons ── */
.estop-btn {
    background: #b71c1c !important;
    color: #ffffff !important;
    font-size: 1.55em !important;
    font-weight: 900 !important;
    min-height: 90px !important;
    border-radius: 16px !important;
    letter-spacing: 2px !important;
    box-shadow: 0 4px 18px rgba(183,28,28,0.55) !important;
    border: 3px solid #7f0000 !important;
}
.estop-btn:hover { background: #c62828 !important; }

.reset-btn {
    background: #bf360c !important;
    color: #ffffff !important;
    font-size: 1.3em !important;
    font-weight: 700 !important;
    min-height: 90px !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 14px rgba(191,54,12,0.45) !important;
    border: 3px solid #870000 !important;
}
.reset-btn:hover { background: #d84315 !important; }

/* ── arrow buttons ── */
.arrow-btn {
    background: #1a3a6e !important;
    color: #ffffff !important;
    font-size: 1.9em !important;
    font-weight: 700 !important;
    min-height: 100px !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 14px rgba(26,58,110,0.5) !important;
    border: 2px solid #1565c0 !important;
    line-height: 1.2 !important;
}
.arrow-btn:hover { background: #1565c0 !important; }

/* ── voice button ── */
.voice-btn {
    background: #1b4332 !important;
    color: #ffffff !important;
    font-size: 1.35em !important;
    font-weight: 700 !important;
    min-height: 72px !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 14px rgba(27,67,50,0.5) !important;
    border: 2px solid #2d6a4f !important;
}
.voice-btn:hover { background: #2d6a4f !important; }

/* ── step-size radio ── */
.step-radio .wrap { gap: 12px !important; flex-wrap: wrap !important; }
.step-radio label {
    font-size: 1.15em !important;
    padding: 12px 22px !important;
    border-radius: 12px !important;
    border: 2px solid #404040 !important;
    background: #2d2d2d !important;
    color: #e8e8e8 !important;
    cursor: pointer !important;
    font-weight: 600 !important;
}
.step-radio input[type="radio"]:checked + span,
.step-radio label.selected {
    background: #0d2040 !important;
    border-color: #1976d2 !important;
    color: #90caf9 !important;
}

/* ── farmbot power buttons ── */
.power-on-btn {
    background: #1b4332 !important;
    color: #ffffff !important;
    font-size: 1.2em !important;
    font-weight: 700 !important;
    min-height: 72px !important;
    border-radius: 16px !important;
    border: 2px solid #2d6a4f !important;
}
.power-on-btn:hover { background: #2d6a4f !important; }

.power-off-btn {
    background: #3b1f1f !important;
    color: #ef9a9a !important;
    font-size: 1.2em !important;
    font-weight: 700 !important;
    min-height: 72px !important;
    border-radius: 16px !important;
    border: 2px solid #7f0000 !important;
}
.power-off-btn:hover { background: #5c2020 !important; }

.status-btn {
    background: #1a2a3a !important;
    color: #90caf9 !important;
    font-size: 1.05em !important;
    font-weight: 600 !important;
    min-height: 52px !important;
    border-radius: 12px !important;
    border: 2px solid #1e3a5f !important;
}

/* ── farmbot status box ── */
.fb-status textarea {
    font-size: 1.2em !important;
    font-family: ui-monospace, monospace !important;
    font-weight: 700 !important;
    background: #0d1a2e !important;
    color: #90caf9 !important;
    border: 2px solid #1e3a5f !important;
    border-radius: 12px !important;
    padding: 10px 14px !important;
}

/* ── audio widget ── */
.gradio-container .audio-container,
.gradio-container [data-testid="audio"] {
    background: #2d2d2d !important;
    border-color: #404040 !important;
    border-radius: 12px !important;
}
"""


# --------------------------------------------------------------------------- init
def _ensure_initialised(ros2_enabled: bool, whisper_size: str = "tiny.en") -> None:
    """Initialise robot publisher and transcriber exactly once."""
    with _STATE.init_lock:
        if _STATE.robot is not None:
            return
        _STATE.ros2_enabled = ros2_enabled
        _STATE.robot = ROS2Publisher(ros2_enabled=ros2_enabled)
        _STATE.transcriber = AudioTranscriber(model_size=whisper_size)
        print("[growmate_voice] Jog panel ready.")


# --------------------------------------------------------------------------- helpers
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _status(last_cmd: str = "") -> str:
    pos = f"X {_STATE.pos_x:.0f} mm    Y {_STATE.pos_y:.0f} mm    Z {_STATE.pos_z:.0f} mm"
    if last_cmd:
        return f"{pos}\nLast command: {last_cmd}"
    return pos


# --------------------------------------------------------------------------- handlers
def _emergency_stop() -> str:
    record = _STATE.robot.emergency_stop()
    return f"EMERGENCY STOP  [{record.status.upper()}]"


def _reset_estop() -> str:
    records = _STATE.robot.execute(["E"])
    return _status(last_cmd=f"E  — reset  [{records[0].status}]")


def _jog(axis: str, direction: int, step: float) -> str:
    lo, hi = _BOUNDS[axis]
    if axis == "x":
        _STATE.pos_x = _clamp(_STATE.pos_x + direction * step, lo, hi)
    elif axis == "y":
        _STATE.pos_y = _clamp(_STATE.pos_y + direction * step, lo, hi)
    elif axis == "z":
        _STATE.pos_z = _clamp(_STATE.pos_z + direction * step, lo, hi)

    cmd = f"M {int(_STATE.pos_x)} {int(_STATE.pos_y)} {int(_STATE.pos_z)}"
    records = _STATE.robot.execute([cmd])
    label = {"x": ("LEFT" if direction < 0 else "RIGHT"),
             "y": ("BACK" if direction < 0 else "FORWARD"),
             "z": ("DOWN" if direction < 0 else "UP")}[axis]
    return _status(last_cmd=f"{cmd}  — {label}  [{records[0].status}]")


# --------------------------------------------------------------------------- farmbot bringup
_BRINGUP_NODES = ["farmbotcontroller", "mapcontroller", "devicecmdhandler"]


def _farmbot_status() -> str:
    """Check if farmbot_bringup is running by querying ros2 node list."""
    # If we launched it ourselves, check the process first
    if _STATE.farmbot_process is not None:
        if _STATE.farmbot_process.poll() is not None:
            _STATE.farmbot_process = None   # it exited

    try:
        result = subprocess.run(
            ["ros2", "node", "list"],
            capture_output=True, text=True, timeout=4,
        )
        nodes = result.stdout.lower()
        if any(n in nodes for n in _BRINGUP_NODES):
            return "● ONLINE — FarmBot is running"
        return "● OFFLINE — FarmBot is not running"
    except FileNotFoundError:
        return "● OFFLINE — ros2 not found (source your workspace)"
    except subprocess.TimeoutExpired:
        return "● TIMEOUT — ROS2 not responding"
    except Exception as e:
        return f"● ERROR — {e}"


def _launch_farmbot() -> str:
    """Start farmbot_bringup standard.launch.py as a background process."""
    if _STATE.farmbot_process is not None and _STATE.farmbot_process.poll() is None:
        return "● Already running — nothing to launch"
    try:
        _STATE.farmbot_process = subprocess.Popen(
            ["ros2", "launch", "farmbot_bringup", "standard.launch.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give nodes a moment to register before status check
        import time; time.sleep(3)
        return _farmbot_status()
    except FileNotFoundError:
        return "● FAILED — ros2 not found (source your workspace first)"
    except Exception as e:
        return f"● FAILED — {e}"


def _stop_farmbot() -> str:
    """Terminate farmbot_bringup."""
    if _STATE.farmbot_process is not None:
        _STATE.farmbot_process.terminate()
        try:
            _STATE.farmbot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _STATE.farmbot_process.kill()
        _STATE.farmbot_process = None
        return "● OFFLINE — FarmBot stopped"
    return "● OFFLINE — Nothing was running"


# --------------------------------------------------------------------------- voice
# Pure string matching — no LLM.  Emergency checked first (arch rule #3).
_VOICE_MAP = [
    (["stop", "emergency", "halt", "freeze", "abort"],      "estop"),
    (["reset", "resume", "clear stop", "clear"],            "reset"),
    (["forward", "ahead", "y plus", "y+"],                  "y_plus"),
    (["back", "backward", "backwards", "y minus", "y-"],    "y_minus"),
    (["left", "x minus", "x-"],                             "x_minus"),
    (["right", "x plus", "x+"],                             "x_plus"),
]


def _classify_voice(text: str) -> Optional[str]:
    t = text.lower().strip()
    for triggers, action in _VOICE_MAP:
        if any(trigger in t for trigger in triggers):
            return action
    return None


def _process_voice(audio_path: Optional[str], step: float):
    """Transcribe → classify → execute. Returns (voice_feedback, position_status)."""
    import os, shutil

    if not audio_path:
        return "No audio received.", _status()

    if not os.path.exists(audio_path):
        return f"Audio file not found: {audio_path}", _status()

    file_kb = os.path.getsize(audio_path) / 1024
    if file_kb < 1:
        return f"Audio file too small ({file_kb:.1f} KB) — recording may be empty.", _status()

    if _STATE.transcriber is None:
        return "Transcriber not initialised — restart the app.", _status()

    if _STATE.transcriber.backend is None:
        has_ffmpeg = shutil.which("ffmpeg") is not None
        if not has_ffmpeg:
            return "ffmpeg not found. Run:  sudo apt install ffmpeg  then restart.", _status()
        return "No STT backend loaded. Run:  pip install faster-whisper  then restart.", _status()

    transcript = _STATE.transcriber.transcribe(audio_path)
    if not transcript:
        return (f"Transcription returned empty (file: {os.path.basename(audio_path)}, "
                f"{file_kb:.1f} KB, backend: {_STATE.transcriber.backend}). "
                "Try speaking more clearly or closer to the mic."), _status()

    action = _classify_voice(transcript)
    heard = f'Heard:  "{transcript}"'

    dispatch = {
        "estop":   lambda: (_emergency_stop(),       "EMERGENCY STOP"),
        "reset":   lambda: (_reset_estop(),          "Reset e-stop"),
        "y_plus":  lambda: (_jog("y", +1, step),     "FORWARD"),
        "y_minus": lambda: (_jog("y", -1, step),     "BACK"),
        "x_minus": lambda: (_jog("x", -1, step),     "LEFT"),
        "x_plus":  lambda: (_jog("x", +1, step),     "RIGHT"),
    }

    if action in dispatch:
        pos_status, label = dispatch[action]()
        return f"{heard}\nCommand:  {label}", pos_status

    return f"{heard}\nCommand:  (not understood — try again)", _status()


# --------------------------------------------------------------------------- ui
def build_ui():
    import gradio as gr

    with gr.Blocks(title="GrowMate — FarmBot Control", css=_CSS) as app:

        # ── header ──────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="text-align:center; padding:20px 0 10px 0;">
          <span style="font-size:2.4em; font-weight:900; color:#6fcf97;
                       letter-spacing:1px;">
            🌱 GrowMate
          </span>
          <div style="font-size:1.1em; color:#999; margin-top:6px;
                      letter-spacing:0.5px;">
            FarmBot Gantry Control
          </div>
        </div>
        """)

        # ── farmbot bringup ─────────────────────────────────────────────────
        gr.HTML("""
        <div style="font-size:1.2em; font-weight:700; color:#90caf9;
                    margin:8px 0 10px 4px;">
          ⚡  FarmBot System
        </div>
        """)
        fb_status_box = gr.Textbox(
            value="Press Check Status to verify connection",
            label="",
            interactive=False,
            lines=1,
            elem_classes=["fb-status"],
        )
        with gr.Row():
            power_on_btn  = gr.Button("⚡  Power ON",      elem_classes=["power-on-btn"])
            power_off_btn = gr.Button("⏹  Power OFF",     elem_classes=["power-off-btn"])
            check_btn     = gr.Button("🔍  Check Status",  elem_classes=["status-btn"])

        gr.HTML('<hr style="border-color:#2a2a2a; margin:16px 0;">')

        # ── safety ──────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="font-size:1.2em; font-weight:700; color:#ef9a9a;
                    margin:8px 0 10px 4px; letter-spacing:0.5px;">
          ⚠️  Safety Controls
        </div>
        """)
        with gr.Row():
            estop_btn = gr.Button(
                "🛑  EMERGENCY STOP",
                elem_classes=["estop-btn"],
                scale=3,
            )
            reset_btn = gr.Button(
                "⟳  Reset E-Stop",
                elem_classes=["reset-btn"],
                scale=2,
            )

        # ── position display ─────────────────────────────────────────────────
        gr.HTML("""
        <div style="font-size:1.2em; font-weight:700; color:#6fcf97;
                    margin:20px 0 10px 4px;">
          📍  Current Position
        </div>
        """)
        status_box = gr.Textbox(
            value=_status(),
            label="",
            interactive=False,
            lines=2,
            elem_classes=["status-box"],
        )

        # ── movement ─────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="font-size:1.2em; font-weight:700; color:#90caf9;
                    margin:20px 0 10px 4px;">
          🕹️  Move Gantry
        </div>
        """)

        step = gr.Radio(
            choices=[("Small  —  50 mm", 50),
                     ("Medium  —  100 mm", 100),
                     ("Large  —  250 mm", 250)],
            value=100,
            label="Step Size per Press",
            elem_classes=["step-radio"],
        )

        # D-pad
        with gr.Row():
            gr.HTML("")
            y_plus  = gr.Button("↑\nFORWARD",  elem_classes=["arrow-btn"])
            gr.HTML("")

        with gr.Row():
            x_minus = gr.Button("←\nLEFT",     elem_classes=["arrow-btn"])
            gr.HTML("""
              <div style="display:flex;align-items:center;justify-content:center;
                          font-size:2.5em;color:#404040;">✛</div>
            """)
            x_plus  = gr.Button("→\nRIGHT",    elem_classes=["arrow-btn"])

        with gr.Row():
            gr.HTML("")
            y_minus = gr.Button("↓\nBACK",     elem_classes=["arrow-btn"])
            gr.HTML("")

        # ── voice ────────────────────────────────────────────────────────────
        gr.HTML("""
        <div style="font-size:1.2em; font-weight:700; color:#a5d6a7;
                    margin:24px 0 6px 4px;">
          🎤  Voice Control
        </div>
        <div style="font-size:1.05em; color:#888; margin-bottom:14px;
                    line-height:1.8em;">
          Record your command, then press <strong style="color:#e8e8e8;">
          Process Voice</strong>.<br>
          You can say:&nbsp;
          <span style="color:#90caf9;">stop</span> &nbsp;·&nbsp;
          <span style="color:#90caf9;">reset</span> &nbsp;·&nbsp;
          <span style="color:#90caf9;">move left</span> &nbsp;·&nbsp;
          <span style="color:#90caf9;">move right</span> &nbsp;·&nbsp;
          <span style="color:#90caf9;">move forward</span> &nbsp;·&nbsp;
          <span style="color:#90caf9;">move back</span>
        </div>
        """)

        audio_in = gr.Audio(
            sources=["microphone"],
            type="filepath",
            label="Record your command here",
        )
        voice_btn = gr.Button("🎤  Process Voice", elem_classes=["voice-btn"])
        voice_status = gr.Textbox(
            value="",
            label="Voice Feedback",
            interactive=False,
            lines=2,
            elem_classes=["voice-feedback"],
        )

        # ── wiring ───────────────────────────────────────────────────────────
        power_on_btn .click(_launch_farmbot,   [], fb_status_box)
        power_off_btn.click(_stop_farmbot,     [], fb_status_box)
        check_btn    .click(_farmbot_status,   [], fb_status_box)

        estop_btn.click(_emergency_stop, [],       status_box)
        reset_btn.click(_reset_estop,    [],       status_box)

        y_plus .click(lambda s: _jog("y", +1, s), [step], status_box)
        y_minus.click(lambda s: _jog("y", -1, s), [step], status_box)
        x_minus.click(lambda s: _jog("x", -1, s), [step], status_box)
        x_plus .click(lambda s: _jog("x", +1, s), [step], status_box)

        voice_btn.click(
            _process_voice,
            [audio_in, step],
            [voice_status, status_box],
        )

    return app


# --------------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="GrowMate jog panel")
    parser.add_argument("--no-ros2", action="store_true",
                        help="Simulation mode — commands printed, not published")
    parser.add_argument("--whisper", default="tiny.en",
                        help="faster-whisper model size (default: tiny.en)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args(argv)

    _ensure_initialised(ros2_enabled=not args.no_ros2, whisper_size=args.whisper)

    try:
        import gradio as gr
    except ImportError:
        print("[growmate_voice] ERROR: gradio is not installed  (pip install gradio)")
        sys.exit(1)

    app = build_ui()
    print(f"[growmate_voice] Jog panel at http://{args.host}:{args.port}")
    try:
        app.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
            css=_CSS,
        )
    finally:
        if _STATE.robot is not None:
            _STATE.robot.shutdown()


if __name__ == "__main__":
    main()
