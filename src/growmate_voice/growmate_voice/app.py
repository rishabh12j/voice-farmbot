"""GrowMate FastAPI app — gated jog panel + voice pipeline + command history.

UX flow:
  1. On load the user sees a single "Power ON" screen.
     - In ``--no-ros2`` mode the button just unlocks the app.
     - In ROS2 mode it additionally launches ``farmbot_bringup standard.launch.py``.
  2. Once ready the main UI appears with two tabs:
     - **Controls** — d-pad, Z+/-, home, water, photo, reset + always-visible e-stop
     - **Voice**    — browser mic -> STT -> command match -> ROS2 -> TTS
  3. A persistent history panel below the tabs shows every action taken.

Architecture:
  Browser records mono PCM @ 16 kHz via Web Audio, encodes WAV client-side,
  POSTs to /api/voice. Server runs STT -> command match -> ROS2 publish ->
  TTS -> returns JSON with base64 WAV. TTS inlined as data:audio/wav;base64.

Run (simulation)::

    python -m growmate_voice.app --no-ros2

Real robot::

    python -m growmate_voice.app
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .edgespeech.audio_utils import (
    SAMPLE_RATE,
    audio_info,
    audio_to_wav_bytes,
    load_wav_from_bytes,
)
from .edgespeech.command_map import COMMAND_MAP, get_tts_phrase, match_command
from .edgespeech.stt import load_stt
from .edgespeech.tts import load_tts
from .history import History
from .logger import log, log_path
from .ros2_publisher import ROS2Publisher

# V2: optional Pi-side dispatch. Lazy-import so app.py still runs when
# growmate_pi isn't on PYTHONPATH (legacy V1 mode is unaffected).
try:
    from growmate_pi.pi_client import (
        app_action_to_intent,
        ping as pi_ping,
        post_estop as pi_post_estop,
        post_intent as pi_post_intent,
        post_reset_estop as pi_post_reset_estop,
    )
    from growmate_pi.schemas import Intent as PiIntent
    _PI_CLIENT_AVAILABLE = True
except ImportError:
    _PI_CLIENT_AVAILABLE = False


# --------------------------------------------------------------------------- state
@dataclass
class AppState:
    robot: Optional[ROS2Publisher] = None
    ros2_enabled: bool = True
    ready: bool = False
    init_lock: threading.Lock = field(default_factory=threading.Lock)
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    farmbot_process: Optional[Any] = None
    stt_cache: Dict[str, Any] = field(default_factory=dict)
    tts_cache: Dict[str, Any] = field(default_factory=dict)
    history: History = field(default_factory=History)
    pi_url: Optional[str] = None  # V2: when set, the voice + button paths POST here


_STATE = AppState()

_BOUNDS = {"x": (0.0, 5691.2), "y": (0.0, 2734.0), "z": (-500.0, 0.0)}
_BRINGUP_NODES = ["farmbotcontroller", "mapcontroller", "devicecmdhandler"]
_VOICE_STEP_MM = 100


# --------------------------------------------------------------------------- init
def _ensure_initialised(ros2_enabled: bool) -> None:
    with _STATE.init_lock:
        if _STATE.robot is not None:
            return
        _STATE.ros2_enabled = ros2_enabled
        log.info("Initialising — ros2=%s", ros2_enabled)
        _STATE.robot = ROS2Publisher(ros2_enabled=ros2_enabled)
        log.info("GrowMate backend ready.  Log: %s", log_path())


def _get_stt(name: str) -> Any:
    if name not in _STATE.stt_cache:
        _STATE.stt_cache[name] = load_stt(name)
    return _STATE.stt_cache[name]


def _get_tts(name: str) -> Any:
    if name not in _STATE.tts_cache:
        _STATE.tts_cache[name] = load_tts(name)
    return _STATE.tts_cache[name]


# --------------------------------------------------------------------------- helpers
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _position_dict() -> Dict[str, int]:
    return {"x": int(_STATE.pos_x), "y": int(_STATE.pos_y), "z": int(_STATE.pos_z)}


def _position_payload(last_cmd: str = "") -> Dict[str, Any]:
    return {**_position_dict(), "last_cmd": last_cmd}


# --------------------------------------------------------------------------- robot actions
def _record(
    source: str,
    action: Optional[str],
    emitted: List[str],
    status: str,
    note: str,
    transcript: str = "",
    confidence: str = "",
) -> None:
    _STATE.history.append(
        source=source,
        action=action,
        emitted=emitted,
        status=status,
        position=_position_dict(),
        note=note,
        transcript=transcript,
        confidence=confidence,
    )


def _do_jog(axis: str, direction: int, step: float, source: str = "button") -> Dict[str, Any]:
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
    log.info("JOG axis=%s dir=%+d step=%.0f cmd=%s status=%s",
             axis, direction, step, cmd, records[0].status)
    note = f"{cmd}  — {label}  [{records[0].status}]"
    _record(source, f"{axis}_{'plus' if direction > 0 else 'minus'}",
            [cmd], records[0].status, note)
    return _position_payload(last_cmd=note)


def _do_estop(source: str = "button") -> Dict[str, Any]:
    log.critical("EMERGENCY STOP triggered (source=%s)", source)
    record = _STATE.robot.emergency_stop()
    note = f"EMERGENCY STOP  [{record.status.upper()}]"
    _record(source, "estop", ["e"], record.status, note)
    return _position_payload(last_cmd=note)


def _do_reset(source: str = "button") -> Dict[str, Any]:
    log.warning("E-stop RESET (source=%s)", source)
    records = _STATE.robot.execute(["E"])
    note = f"E  — reset  [{records[0].status}]"
    _record(source, "reset", ["E"], records[0].status, note)
    return _position_payload(last_cmd=note)


def _do_home(source: str = "button") -> Dict[str, Any]:
    records = _STATE.robot.execute(["H_0"])
    _STATE.pos_x = _STATE.pos_y = 0.0
    _STATE.pos_z = 0.0
    note = f"H_0  — home  [{records[0].status}]"
    _record(source, "home", ["H_0"], records[0].status, note)
    return _position_payload(last_cmd=note)


def _do_emit(emissions: List[str], action: str, label: str, source: str = "button") -> Dict[str, Any]:
    records = _STATE.robot.execute(emissions)
    statuses = ", ".join(r.status for r in records)
    note = f"{' | '.join(emissions)}  — {label}  [{statuses}]"
    _record(source, action, emissions, records[0].status, note)
    return _position_payload(last_cmd=note)


def _dispatch_via_pi(action: str, source: str) -> Optional[Dict[str, Any]]:
    """Send the action to the Pi intent server as an Intent JSON POST.

    Returns a position_payload-shaped dict on success, or None when this
    action isn't yet mapped on the V2 side (caller falls back to local).
    Estop and reset bypass the BT and hit their own endpoints.
    """
    if _STATE.pi_url is None or not _PI_CLIENT_AVAILABLE:
        return None

    base = _STATE.pi_url.rsplit("/intent", 1)[0]
    try:
        if action == "estop":
            pi_post_estop(base)
            note = "EMERGENCY STOP (via Pi)  [sent]"
            _record(source, "estop", ["e"], "sent", note)
            return _position_payload(last_cmd=note)

        if action == "reset":
            pi_post_reset_estop(base)
            note = "E (reset, via Pi)  [sent]"
            _record(source, "reset", ["E"], "sent", note)
            return _position_payload(last_cmd=note)

        # Jog: compute absolute target on Windows (where position state lives),
        # then send explicit coords to Pi so it doesn't need to track position.
        if action in {"x_plus", "x_minus", "y_plus", "y_minus", "z_plus", "z_minus"}:
            axis, sign = action.split("_")
            direction = +1 if sign == "plus" else -1
            new_x = _clamp(_STATE.pos_x + (_VOICE_STEP_MM * direction if axis == "x" else 0), *_BOUNDS["x"])
            new_y = _clamp(_STATE.pos_y + (_VOICE_STEP_MM * direction if axis == "y" else 0), *_BOUNDS["y"])
            new_z = _clamp(_STATE.pos_z + (_VOICE_STEP_MM * direction if axis == "z" else 0), *_BOUNDS["z"])
            _STATE.pos_x, _STATE.pos_y, _STATE.pos_z = new_x, new_y, new_z
            label = {"x": ("RIGHT" if direction > 0 else "LEFT"),
                     "y": ("FORWARD" if direction > 0 else "BACK"),
                     "z": ("UP" if direction > 0 else "DOWN")}[axis]
            intent = PiIntent(
                action="move",
                params={"x": new_x, "y": new_y, "z": new_z},
                response=f"{label}.",
            )
            reply = pi_post_intent(_STATE.pi_url, [intent],
                                   raw_text=f"(jog) {action}", client_id="growmate_voice.app")
            status = "sent" if reply.status == "success" else reply.status
            cmds = reply.commands_published or [f"M {new_x:.0f} {new_y:.0f} {new_z:.0f}"]
            note = f"{cmds[0]}  — {label} (via Pi)  [{status}]"
            _record(source, action, cmds, status, note)
            return _position_payload(last_cmd=note)

        intent = app_action_to_intent(action)
        if intent is None:
            return None

        reply = pi_post_intent(
            _STATE.pi_url,
            [intent],
            raw_text=f"(button) {action}",
            client_id="growmate_voice.app",
        )
        status = "sent" if reply.status == "success" else reply.status
        cmds = reply.commands_published or [intent.action]
        note = f"{' | '.join(cmds)}  — {action} (via Pi)  [{status}]"
        _record(source, action, cmds, status, note)
        return _position_payload(last_cmd=note)
    except Exception as exc:
        log.warning("Pi dispatch failed for '%s': %s — falling back to local", action, exc)
        return None


def _execute_action(action: str, source: str) -> Dict[str, Any]:
    # V2 path: if --pi-url is set and the client + action are wired, route
    # to the Pi. Fall through to legacy local execution on any failure so the
    # demo never strands the user.
    routed = _dispatch_via_pi(action, source)
    if routed is not None:
        return routed

    if action == "estop":   return _do_estop(source)
    if action == "reset":   return _do_reset(source)
    if action == "home":    return _do_home(source)
    if action == "x_plus":  return _do_jog("x", +1, _VOICE_STEP_MM, source)
    if action == "x_minus": return _do_jog("x", -1, _VOICE_STEP_MM, source)
    if action == "y_plus":  return _do_jog("y", +1, _VOICE_STEP_MM, source)
    if action == "y_minus": return _do_jog("y", -1, _VOICE_STEP_MM, source)
    if action == "z_plus":  return _do_jog("z", +1, _VOICE_STEP_MM, source)
    if action == "z_minus": return _do_jog("z", -1, _VOICE_STEP_MM, source)
    if action == "water":   return _do_emit(["P_4"], "water", "water", source)
    if action == "photo":   return _do_emit(["I_1"], "photo", "photo", source)
    return _position_payload(last_cmd=f"(unhandled action: {action})")


# --------------------------------------------------------------------------- farmbot bringup
def _farmbot_status_text() -> str:
    if _STATE.farmbot_process is not None and _STATE.farmbot_process.poll() is not None:
        log.warning("FarmBot process exited (rc=%s)", _STATE.farmbot_process.returncode)
        _STATE.farmbot_process = None
    try:
        result = subprocess.run(
            ["ros2", "node", "list"], capture_output=True, text=True, timeout=4
        )
        nodes = result.stdout.lower()
        if any(n in nodes for n in _BRINGUP_NODES):
            return "● ONLINE — FarmBot is running"
        return "● OFFLINE — FarmBot is not running"
    except FileNotFoundError:
        return "● OFFLINE — ros2 not found (source your workspace)"
    except subprocess.TimeoutExpired:
        return "● TIMEOUT — ROS2 not responding"
    except Exception as e:  # noqa: BLE001
        return f"● ERROR — {e}"


def _launch_farmbot() -> str:
    """Launch farmbot_bringup. In --no-ros2 mode this is a no-op."""
    if not _STATE.ros2_enabled:
        log.info("Power ON pressed in simulation mode — no process launched")
        return "● SIMULATION — bringup skipped (--no-ros2)"
    if _STATE.farmbot_process is not None and _STATE.farmbot_process.poll() is None:
        return "● Already running"
    try:
        _STATE.farmbot_process = subprocess.Popen(
            ["ros2", "launch", "farmbot_bringup", "standard.launch.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Launched farmbot_bringup pid=%s", _STATE.farmbot_process.pid)
        time.sleep(3)
        return _farmbot_status_text()
    except FileNotFoundError:
        return "● FAILED — ros2 not found (source your workspace first)"
    except Exception as e:  # noqa: BLE001
        return f"● FAILED — {e}"


def _stop_farmbot() -> str:
    if _STATE.farmbot_process is None:
        return "● OFFLINE — Nothing was running" if _STATE.ros2_enabled \
               else "● SIMULATION — powered off"
    pid = _STATE.farmbot_process.pid
    _STATE.farmbot_process.terminate()
    try:
        _STATE.farmbot_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _STATE.farmbot_process.kill()
        log.warning("FarmBot SIGKILL pid=%s", pid)
    _STATE.farmbot_process = None
    return "● OFFLINE — FarmBot stopped"


# --------------------------------------------------------------------------- fastapi
app = FastAPI(title="GrowMate — FarmBot Voice Control")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    return {
        "ready": _STATE.ready,
        "position": _position_payload(),
        "farmbot": _farmbot_status_text(),
        "ros2_enabled": _STATE.ros2_enabled,
    }


@app.get("/api/commands")
def api_commands() -> List[Dict[str, Any]]:
    return [{"variants": v, "action": a} for v, a in COMMAND_MAP]


@app.post("/api/farmbot/power")
def api_farmbot_power(action: str = Form(...)) -> Dict[str, Any]:
    if action == "on":
        msg = _launch_farmbot()
        _STATE.ready = True
        log.info("App state -> READY (ros2=%s)", _STATE.ros2_enabled)
    elif action == "off":
        msg = _stop_farmbot()
        _STATE.ready = False
        log.info("App state -> NOT READY")
    else:
        msg = _farmbot_status_text()
    return {"farmbot": msg, "ready": _STATE.ready}


@app.post("/api/jog")
def api_jog(
    axis: str = Form(...),
    direction: int = Form(...),
    step: float = Form(100.0),
) -> Dict[str, Any]:
    if axis not in _BOUNDS:
        return JSONResponse(status_code=400, content={"error": f"bad axis '{axis}'"})
    return _do_jog(axis, 1 if direction > 0 else -1, step, source="button")


@app.post("/api/action")
def api_action(action: str = Form(...)) -> Dict[str, Any]:
    """Execute a high-level action by name (home, water, photo, reset, estop)."""
    allowed = {"home", "water", "photo", "reset", "estop"}
    if action not in allowed:
        return JSONResponse(status_code=400, content={"error": f"bad action '{action}'"})
    return _execute_action(action, source="button")


@app.post("/api/estop")
def api_estop() -> Dict[str, Any]:
    return _do_estop(source="button")


@app.post("/api/reset")
def api_reset() -> Dict[str, Any]:
    return _do_reset(source="button")


@app.get("/api/history")
def api_history(limit: int = 50) -> Dict[str, Any]:
    return {"entries": _STATE.history.recent(limit=limit)}


@app.post("/api/history/clear")
def api_history_clear() -> Dict[str, Any]:
    removed = _STATE.history.clear()
    log.info("History cleared (%d entries removed)", removed)
    return {"removed": removed}


@app.post("/api/voice")
async def api_voice(
    audio: UploadFile = File(...),
    stt: str = Form("whisper"),
    tts: str = Form("none"),
    enable_tts: str = Form("true"),
) -> Any:
    pipeline_log: List[str] = []
    try:
        wav_bytes = await audio.read()
        pipeline_log.append(f"📥 Received {len(wav_bytes)} bytes")

        np_audio = load_wav_from_bytes(wav_bytes)
        duration, peak = audio_info(np_audio, SAMPLE_RATE)
        pipeline_log.append(f"🎧 {duration:.2f}s @ {SAMPLE_RATE} Hz | peak={peak:.3f}")

        stt_backend = _get_stt(stt)
        if not stt_backend.is_available():
            pipeline_log.append(f"⚠ STT '{stt}' not available")
            return JSONResponse(status_code=400, content={
                "error": f"STT backend '{stt}' is not available.",
                "log": "\n".join(pipeline_log),
            })

        pipeline_log.append(f"🎙 STT: {stt_backend.name}")
        transcript, latency_ms = stt_backend.transcribe(np_audio, sample_rate=SAMPLE_RATE)
        pipeline_log.append(f"📝 '{transcript}' ({latency_ms:.1f} ms)")

        action, confidence = match_command(transcript)
        pipeline_log.append(f"🔍 Matched: {action} ({confidence})")

        if action is not None:
            position_payload = _execute_action(action, source="voice")
            # Augment the last history entry with transcript + confidence
            if _STATE.history._entries:
                last = _STATE.history._entries[-1]
                last.transcript = transcript
                last.confidence = confidence
            pipeline_log.append(f"🤖 {position_payload['last_cmd']}")
        else:
            position_payload = _position_payload()
            _record("voice", None, [], "ignored",
                    "No match", transcript=transcript, confidence=confidence)
            pipeline_log.append("🤖 No action — command not recognised")

        tts_spoken = ""
        tts_audio_b64: Optional[str] = None
        if enable_tts.lower() == "true" and tts != "none":
            phrase = get_tts_phrase(action)
            try:
                tts_backend = _get_tts(tts)
                if not tts_backend.is_available():
                    pipeline_log.append(f"⚠ TTS '{tts}' not available")
                else:
                    pipeline_log.append(f"🔊 TTS ({tts}): '{phrase}'")
                    tts_np, tts_sr = tts_backend.synthesise(phrase)
                    wav_out = audio_to_wav_bytes(tts_np, sample_rate=tts_sr)
                    tts_audio_b64 = base64.b64encode(wav_out).decode("ascii")
                    tts_spoken = phrase
            except Exception as exc:  # noqa: BLE001
                pipeline_log.append(f"⚠ TTS error: {exc}")

        pipeline_log.append("✅ Done")
        return JSONResponse({
            "result": {
                "raw_transcript": transcript,
                "matched_action": action,
                "confidence": confidence,
                "stt_latency_ms": round(latency_ms, 1),
                "stt_backend": stt_backend.name,
                "tts_spoken": tts_spoken,
                "position": position_payload,
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": tts_audio_b64,
        })
    except Exception as exc:  # noqa: BLE001
        pipeline_log.append(f"❌ Error: {exc}")
        log.exception("Voice pipeline error")
        return JSONResponse(status_code=500, content={
            "error": str(exc),
            "log": "\n".join(pipeline_log),
            "trace": traceback.format_exc(),
        })


# --------------------------------------------------------------------------- html
INDEX_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>🌱 GrowMate — FarmBot Voice Control</title>
<style>
  :root {
    --bg: #1a1a1a; --panel: #2d2d2d; --panel2: #0d2218;
    --fg: #e8e8e8; --muted: #999;
    --green: #6fcf97; --green-dk: #2d6a4f;
    --red: #b71c1c; --red-dk: #7f0000;
    --orange: #bf360c;
    --blue: #1a3a6e; --blue-lt: #90caf9; --blue-dk: #1e3a5f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font-family: system-ui, -apple-system, Segoe UI, sans-serif;
  }
  .wrap { max-width: 920px; margin: 0 auto; padding: 24px 18px 48px; }
  h1 {
    text-align: center; color: var(--green);
    font-size: 2.4em; font-weight: 900; letter-spacing: 1px;
    margin: 10px 0 4px;
  }
  .sub {
    text-align: center; color: var(--muted); font-size: 1.05em;
    margin-bottom: 24px; letter-spacing: 0.5px;
  }
  .tag {
    display: inline-block; background: #223; color: #aac;
    padding: 2px 8px; border-radius: 8px; font-size: 0.8em; margin-left: 6px;
  }

  /* ===== start screen ===== */
  .gate {
    text-align: center; padding: 60px 20px;
    background: linear-gradient(180deg, #162418 0%, var(--bg) 100%);
    border-radius: 18px; margin: 40px 0;
    border: 2px solid var(--green-dk);
  }
  .gate h2 { color: var(--green); margin: 0 0 14px; font-size: 1.8em; }
  .gate .hint { color: var(--muted); margin-bottom: 28px; font-size: 1.05em; }
  .gate .power-btn {
    background: #1b4332; color: #fff; border: 3px solid var(--green-dk);
    padding: 36px 70px; border-radius: 24px;
    font-size: 2em; font-weight: 900; cursor: pointer;
    box-shadow: 0 8px 24px rgba(111, 207, 151, 0.35);
    letter-spacing: 3px;
    transition: all 0.15s;
  }
  .gate .power-btn:hover { background: var(--green-dk); transform: translateY(-2px); }
  .gate .status { color: var(--muted); margin-top: 22px; font-family: monospace; }

  /* ===== main UI ===== */
  .section-title {
    font-size: 1.2em; font-weight: 700; margin: 22px 0 10px 4px;
    letter-spacing: 0.5px;
  }
  .section-title.blue   { color: var(--blue-lt); }
  .section-title.red    { color: #ef9a9a; }
  .section-title.green  { color: var(--green); }
  hr { border: none; border-top: 1px solid #2a2a2a; margin: 18px 0; }

  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: stretch; margin: 8px 0; }
  .row > * { flex: 1; min-width: 0; }

  button {
    cursor: pointer; border-radius: 14px; font-weight: 700;
    color: #fff; border: 2px solid transparent; padding: 14px 18px;
    font-size: 1.05em; transition: background 0.12s;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }

  .power-off { background: #3b1f1f; color: #ef9a9a; border-color: var(--red-dk); }
  .power-off:hover { background: #5c2020; }
  .power-chk { background: #1a2a3a; color: var(--blue-lt); border-color: var(--blue-dk); font-size: 1em; }

  .estop {
    background: var(--red); border-color: var(--red-dk);
    font-size: 1.5em; font-weight: 900; min-height: 90px;
    letter-spacing: 2px;
    box-shadow: 0 4px 18px rgba(183,28,28,0.55);
    flex: 3;
  }
  .estop:hover { background: #c62828; }
  .reset {
    background: var(--orange); border-color: #870000;
    font-size: 1.2em; min-height: 90px;
    box-shadow: 0 4px 14px rgba(191,54,12,0.45);
    flex: 2;
  }
  .reset:hover { background: #d84315; }

  .status-box, .fb-status {
    background: var(--panel2); color: var(--green);
    border: 2px solid var(--green-dk); border-radius: 12px;
    padding: 14px 16px; font-family: ui-monospace, monospace;
    font-size: 1.2em; font-weight: 700; line-height: 1.6;
    white-space: pre-wrap; min-height: 1em;
  }
  .fb-status {
    background: #0d1a2e; color: var(--blue-lt); border-color: var(--blue-dk);
    font-size: 1.05em;
  }

  /* ===== tabs ===== */
  .tabs { display: flex; gap: 4px; margin: 20px 0 0; border-bottom: 2px solid #2a2a2a; }
  .tab {
    background: transparent; border: none; color: var(--muted);
    padding: 14px 22px; font-size: 1.1em; font-weight: 700;
    cursor: pointer; border-radius: 10px 10px 0 0;
    border: 2px solid transparent; border-bottom: none;
  }
  .tab.active {
    color: var(--fg); background: var(--panel);
    border-color: #2a2a2a;
  }
  .tab-pill {
    display: inline-block; background: var(--green-dk); color: #eafff2;
    font-size: 0.65em; font-weight: 800; padding: 2px 7px;
    border-radius: 6px; margin-left: 6px; letter-spacing: 0.5px;
    text-transform: uppercase;
  }
  .tab-panel { display: none; padding: 18px 4px; }
  .tab-panel.active { display: block; }
  .tab-note {
    color: var(--muted); font-size: 0.95em; font-style: italic;
    margin-bottom: 10px;
  }

  /* voice hero */
  .voice-hero {
    text-align: center; padding: 18px 12px 6px;
  }
  .voice-hero-title {
    color: var(--green); font-size: 1.5em; font-weight: 800;
    letter-spacing: 0.5px; margin-bottom: 4px;
  }
  .voice-hero-hint { color: var(--muted); font-size: 1em; }

  .rec-wrap {
    display: flex; flex-direction: column; align-items: center;
    gap: 10px; margin: 14px 0 18px;
  }
  .rec-btn-big {
    background: linear-gradient(180deg, #2d6a4f 0%, #1b4332 100%);
    color: #fff; border: 3px solid var(--green-dk);
    width: 100%; max-width: 420px; min-height: 120px;
    border-radius: 24px; font-size: 1.4em; font-weight: 800;
    letter-spacing: 1px; cursor: pointer;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; transition: transform 0.1s, box-shadow 0.1s;
    box-shadow: 0 8px 24px rgba(111, 207, 151, 0.3);
  }
  .rec-btn-big:hover { transform: translateY(-2px); box-shadow: 0 10px 28px rgba(111, 207, 151, 0.4); }
  .rec-btn-big.recording {
    background: linear-gradient(180deg, #c62828 0%, #7a0000 100%);
    border-color: var(--red-dk);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .rec-btn-big .mic-icon { font-size: 2.2em; }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 8px 24px rgba(198, 40, 40, 0.4); }
    50%      { box-shadow: 0 8px 34px rgba(198, 40, 40, 0.8); }
  }

  /* ===== d-pad ===== */
  .pad { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 6px; }
  .slot { background: transparent; }
  .arrow {
    background: var(--blue); border-color: #1565c0;
    font-size: 1.5em; min-height: 100px; line-height: 1.15;
    box-shadow: 0 4px 14px rgba(26,58,110,0.5);
  }
  .arrow:hover { background: #1565c0; }
  .cross {
    display: flex; align-items: center; justify-content: center;
    font-size: 2.2em; color: #404040;
  }

  .step-row { gap: 10px; margin-top: 8px; flex-wrap: wrap; }
  .step-btn {
    background: var(--panel); color: var(--fg);
    border: 2px solid #404040; font-weight: 600; min-height: 52px;
    padding: 10px 18px; font-size: 1em;
  }
  .step-btn.selected {
    background: #0d2040; border-color: #1976d2; color: var(--blue-lt);
  }

  .action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
  .action-btn {
    background: #1b4332; border-color: var(--green-dk); min-height: 72px;
    font-size: 1.1em; letter-spacing: 0.5px;
  }
  .action-btn:hover { background: var(--green-dk); }
  .action-btn.warn { background: #4a2a1f; border-color: var(--orange); color: #ffb380; }
  .action-btn.warn:hover { background: var(--orange); color: #fff; }

  /* ===== voice ===== */
  .voice-row label { font-size: 0.95em; color: #ccc; }
  .voice-row select {
    background: var(--panel); color: var(--fg); border: 1px solid #404040;
    padding: 8px 10px; border-radius: 8px; font-size: 1em;
  }
  .rec-btn {
    background: var(--red); border-color: var(--red-dk);
    min-height: 72px; font-size: 1.1em; letter-spacing: 1px;
  }
  .rec-btn.recording { background: #7a0000; }
  .rec-btn:hover { background: #c62828; }
  .mic-status { color: var(--muted); font-size: 0.95em; margin-left: 6px; }

  pre.log {
    background: #111; color: #cfe0ff; padding: 12px 14px;
    border-radius: 10px; font-family: ui-monospace, monospace;
    font-size: 0.88em; white-space: pre-wrap; min-height: 3em;
    border: 1px solid #222;
  }
  pre.result {
    background: #0d1a2e; color: var(--blue-lt); padding: 12px 14px;
    border-radius: 10px; font-family: ui-monospace, monospace;
    font-size: 0.9em; white-space: pre-wrap; border: 1px solid var(--blue-dk);
  }
  audio { width: 100%; margin-top: 8px; }

  /* ===== history ===== */
  .history-wrap {
    margin-top: 28px; background: var(--panel);
    border-radius: 12px; padding: 14px 16px;
    border: 1px solid #333;
  }
  .history-head {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 10px;
  }
  .history-head h3 { margin: 0; color: var(--blue-lt); font-size: 1.1em; }
  .history-head button {
    padding: 6px 14px; font-size: 0.85em; min-height: 0;
    background: transparent; border-color: #555; color: var(--muted);
  }
  .history-head button:hover { color: var(--fg); border-color: #888; }
  .history-list {
    max-height: 320px; overflow-y: auto; font-family: ui-monospace, monospace;
    font-size: 0.86em; line-height: 1.5;
  }
  .history-list .row-item {
    display: grid; grid-template-columns: 90px 70px 80px 1fr;
    gap: 10px; padding: 6px 4px; border-bottom: 1px solid #222;
    align-items: baseline;
  }
  .history-list .row-item:last-child { border-bottom: none; }
  .history-list .ts { color: var(--muted); }
  .history-list .src { color: var(--blue-lt); font-weight: 700; }
  .history-list .src.voice  { color: #c48cff; }
  .history-list .src.api    { color: #ffcc80; }
  .history-list .action { color: var(--green); font-weight: 700; }
  .history-list .note { color: #ccc; overflow: hidden; text-overflow: ellipsis; }
  .history-empty { color: var(--muted); font-style: italic; padding: 10px 4px; }
</style>
</head>
<body>
<div class="wrap">

  <h1>🌱 GrowMate</h1>
  <div class="sub">FarmBot Gantry Control <span class="tag" id="modeTag">…</span></div>

  <!-- ===== START SCREEN ===== -->
  <div id="gate" class="gate" style="display:none;">
    <h2>System is powered off</h2>
    <div class="hint" id="gateHint">Press Power ON to bring up the FarmBot and unlock controls.</div>
    <button class="power-btn" onclick="powerOn()">⚡ POWER ON</button>
    <div class="status" id="gateStatus">—</div>
  </div>

  <!-- ===== MAIN UI ===== -->
  <div id="main" style="display:none;">

    <!-- always-on status strip -->
    <div class="row" style="margin-bottom: 4px;">
      <div class="fb-status" id="fbStatus" style="flex: 3;">—</div>
      <button class="power-off" onclick="powerOff()" style="flex: 1;">⏻ Power OFF</button>
    </div>

    <div class="section-title green">📍 Current Position</div>
    <div class="status-box" id="posBox">X 0 mm    Y 0 mm    Z 0 mm</div>

    <!-- always-visible safety -->
    <div class="row" style="margin-top: 14px;">
      <button class="estop" onclick="doAction('estop')">🛑 EMERGENCY STOP</button>
      <button class="reset" onclick="doAction('reset')">⟳ Reset</button>
    </div>

    <!-- tabs -->
    <div class="tabs">
      <button class="tab active" data-tab="voice">🎤 Voice  <span class="tab-pill">primary</span></button>
      <button class="tab"         data-tab="controls">🕹️ Manual controls</button>
    </div>

    <!-- Controls tab -->
    <div class="tab-panel" id="panel-controls">
      <div class="tab-note">Manual fallback for voice — use when the microphone isn't available or a command didn't match.</div>
      <div class="section-title blue">Step size</div>
      <div class="row step-row" id="stepRow">
        <button class="step-btn"          data-step="50">Small — 50 mm</button>
        <button class="step-btn selected" data-step="100">Medium — 100 mm</button>
        <button class="step-btn"          data-step="250">Large — 250 mm</button>
      </div>

      <div class="section-title blue">Move gantry</div>
      <div class="pad">
        <div class="slot"></div>
        <button class="arrow" onclick="jog('y', +1)">↑<br>FORWARD</button>
        <div class="slot"></div>
        <button class="arrow" onclick="jog('x', -1)">←<br>LEFT</button>
        <div class="cross">✛</div>
        <button class="arrow" onclick="jog('x', +1)">→<br>RIGHT</button>
        <div class="slot"></div>
        <button class="arrow" onclick="jog('y', -1)">↓<br>BACK</button>
        <div class="slot"></div>
      </div>
      <div class="row" style="margin-top: 10px;">
        <button class="arrow" onclick="jog('z', +1)">⤴ Z+ (up)</button>
        <button class="arrow" onclick="jog('z', -1)">⤵ Z− (down)</button>
      </div>

      <div class="section-title green">High-level actions</div>
      <div class="action-grid">
        <button class="action-btn" onclick="doAction('home')">🏠 Home</button>
        <button class="action-btn" onclick="doAction('water')">💧 Water</button>
        <button class="action-btn" onclick="doAction('photo')">📷 Photo</button>
      </div>
    </div>

    <!-- Voice tab (primary) -->
    <div class="tab-panel active" id="panel-voice">
      <div class="voice-hero">
        <div class="voice-hero-title">Speak a command</div>
        <div class="voice-hero-hint">e.g. &ldquo;move right&rdquo;, &ldquo;go home&rdquo;, &ldquo;water the plants&rdquo;, &ldquo;stop&rdquo;</div>
      </div>
      <div class="row voice-row">
        <label>STT:
          <select id="stt">
            <option value="whisper">whisper</option>
            <option value="vosk">vosk</option>
            <option value="moonshine">moonshine</option>
          </select>
        </label>
        <label>TTS:
          <select id="tts">
            <option value="none">none</option>
            <option value="piper">piper</option>
            <option value="kokoro">kokoro</option>
          </select>
        </label>
        <label><input type="checkbox" id="enableTts" checked/> Enable TTS</label>
      </div>
      <div class="rec-wrap">
        <button id="recBtn" class="rec-btn-big">
          <span class="mic-icon">🎙</span>
          <span id="recLabel">Tap to record</span>
        </button>
        <div class="mic-status" id="micStatus">Idle</div>
      </div>

      <pre class="result" id="voiceResult">(no voice result yet)</pre>
      <pre class="log"    id="voiceLog">—</pre>
      <audio id="ttsAudio" controls></audio>
    </div>

    <!-- history panel -->
    <div class="history-wrap">
      <div class="history-head">
        <h3>📜 Command History</h3>
        <div>
          <button onclick="refreshHistory()">Refresh</button>
          <button onclick="clearHistory()">Clear</button>
        </div>
      </div>
      <div class="history-list" id="historyList">
        <div class="history-empty">No commands yet.</div>
      </div>
    </div>

  </div>
</div>

<script>
const TARGET_SR = 16000;
let stepMm = 100;

/* ============ step selector ============ */
document.querySelectorAll('#stepRow .step-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#stepRow .step-btn').forEach(x => x.classList.remove('selected'));
    b.classList.add('selected');
    stepMm = Number(b.dataset.step);
  });
});

/* ============ tab switching ============ */
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
  });
});

/* ============ gate ============ */
async function powerOn() {
  const form = new FormData(); form.append('action', 'on');
  document.getElementById('gateStatus').textContent = 'Bringing up FarmBot…';
  try {
    const r = await fetch('/api/farmbot/power', { method: 'POST', body: form });
    const data = await r.json();
    document.getElementById('gateStatus').textContent = data.farmbot;
    if (data.ready) {
      showMain();
      refreshStatus();
      refreshHistory();
    }
  } catch (e) {
    document.getElementById('gateStatus').textContent = 'Error: ' + e.message;
  }
}

async function powerOff() {
  if (!confirm('Power OFF FarmBot? This will stop the bringup.')) return;
  const form = new FormData(); form.append('action', 'off');
  const r = await fetch('/api/farmbot/power', { method: 'POST', body: form });
  const data = await r.json();
  showGate(data.farmbot);
}

function showGate(statusText) {
  document.getElementById('gate').style.display = 'block';
  document.getElementById('main').style.display = 'none';
  if (statusText) document.getElementById('gateStatus').textContent = statusText;
}
function showMain() {
  document.getElementById('gate').style.display = 'none';
  document.getElementById('main').style.display = 'block';
}

/* ============ status / actions ============ */
function renderPosition(pos) {
  const line1 = `X ${pos.x} mm    Y ${pos.y} mm    Z ${pos.z} mm`;
  const line2 = pos.last_cmd ? `\nLast: ${pos.last_cmd}` : '';
  document.getElementById('posBox').textContent = line1 + line2;
}

async function refreshStatus() {
  const r = await fetch('/api/status');
  const data = await r.json();
  renderPosition(data.position);
  document.getElementById('fbStatus').textContent = data.farmbot;
  document.getElementById('modeTag').textContent =
    data.ros2_enabled ? 'ROS2 live' : 'simulation';
  if (data.ready) showMain(); else showGate(data.farmbot);
}

async function jog(axis, direction) {
  const form = new FormData();
  form.append('axis', axis);
  form.append('direction', direction);
  form.append('step', stepMm);
  const r = await fetch('/api/jog', { method: 'POST', body: form });
  const data = await r.json();
  renderPosition(data);
  refreshHistory();
}

async function doAction(action) {
  const form = new FormData();
  form.append('action', action);
  const r = await fetch('/api/action', { method: 'POST', body: form });
  const data = await r.json();
  renderPosition(data);
  refreshHistory();
}

/* ============ history ============ */
async function refreshHistory() {
  try {
    const r = await fetch('/api/history?limit=50');
    const data = await r.json();
    renderHistory(data.entries || []);
  } catch (e) { /* ignore */ }
}

async function clearHistory() {
  if (!confirm('Clear all history entries?')) return;
  await fetch('/api/history/clear', { method: 'POST' });
  refreshHistory();
}

function fmtTs(iso) {
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}

function renderHistory(entries) {
  const list = document.getElementById('historyList');
  if (!entries.length) {
    list.innerHTML = '<div class="history-empty">No commands yet.</div>';
    return;
  }
  list.innerHTML = entries.map(e => `
    <div class="row-item">
      <span class="ts">${fmtTs(e.ts)}</span>
      <span class="src ${e.source}">${e.source}</span>
      <span class="action">${e.action || '—'}</span>
      <span class="note">${(e.transcript ? '🎙 "' + e.transcript + '" → ' : '') + (e.note || '')}</span>
    </div>
  `).join('');
}

/* ============ voice recording ============ */
const recBtn = document.getElementById('recBtn');
const micStatus = document.getElementById('micStatus');
const voiceLog = document.getElementById('voiceLog');
const voiceResult = document.getElementById('voiceResult');
const ttsAudio = document.getElementById('ttsAudio');

let recording = false;
let audioCtx = null, source = null, processor = null, stream = null;
let chunks = [];

recBtn.addEventListener('click', async () => {
  if (!recording) await startRec(); else await stopRec();
});

async function startRec() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  } catch (e) {
    micStatus.textContent = 'Mic denied: ' + e.message;
    return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SR });
  source = audioCtx.createMediaStreamSource(stream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);
  chunks = [];
  processor.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(processor);
  processor.connect(audioCtx.destination);
  recording = true;
  recBtn.classList.add('recording');
  document.getElementById('recLabel').textContent = 'Recording… tap to stop';
  micStatus.textContent = 'Recording…';
}

async function stopRec() {
  recording = false;
  recBtn.classList.remove('recording');
  document.getElementById('recLabel').textContent = 'Tap to record';
  micStatus.textContent = 'Processing…';

  processor.disconnect(); source.disconnect();
  stream.getTracks().forEach(t => t.stop());
  await audioCtx.close();

  const total = chunks.reduce((n, c) => n + c.length, 0);
  const merged = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.length; }

  const wav = encodeWAV(merged, TARGET_SR);
  const form = new FormData();
  form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'rec.wav');
  form.append('stt',  document.getElementById('stt').value);
  form.append('tts',  document.getElementById('tts').value);
  form.append('enable_tts', document.getElementById('enableTts').checked ? 'true' : 'false');

  try {
    const resp = await fetch('/api/voice', { method: 'POST', body: form });
    const data = await resp.json();
    voiceResult.textContent = JSON.stringify(data.result || data, null, 2);
    voiceLog.textContent    = data.log || '(no log)';
    if (data.result && data.result.position) renderPosition(data.result.position);
    if (data.tts_audio_b64) {
      ttsAudio.src = 'data:audio/wav;base64,' + data.tts_audio_b64;
      ttsAudio.play().catch(() => {});
    } else {
      ttsAudio.removeAttribute('src');
    }
    micStatus.textContent = 'Done';
    refreshHistory();
  } catch (e) {
    micStatus.textContent = 'Error: ' + e.message;
  }
}

function encodeWAV(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return buffer;
}

/* ============ init ============ */
refreshStatus();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="GrowMate FastAPI jog + voice panel")
    parser.add_argument("--no-ros2", action="store_true",
                        help="Simulation mode — commands printed, not published")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument(
        "--pi-url",
        default=None,
        help=(
            "V2: send actions to a running growmate_pi intent server "
            "(e.g. http://localhost:8000/intent). When set, voice and "
            "button actions POST to the Pi instead of executing locally."
        ),
    )
    args = parser.parse_args(argv)

    if args.pi_url:
        if not _PI_CLIENT_AVAILABLE:
            log.warning("--pi-url given but growmate_pi.pi_client not importable; "
                        "running in legacy local mode")
        else:
            _STATE.pi_url = args.pi_url
            log.info("V2 mode: dispatching to Pi at %s", args.pi_url)
            status = pi_ping(args.pi_url.rsplit("/intent", 1)[0])
            if status is None:
                log.warning("Pi not reachable at %s — will retry per-request", args.pi_url)
            else:
                log.info("Pi ready: %s", status)

    log.info("=== GrowMate startup ros2=%s pi_url=%s %s:%s ===",
             not args.no_ros2, _STATE.pi_url, args.host, args.port)
    _ensure_initialised(ros2_enabled=not args.no_ros2)

    try:
        import uvicorn
    except ImportError:
        log.critical("uvicorn not installed — run: pip install uvicorn[standard] fastapi")
        sys.exit(1)

    log.info("GrowMate at http://%s:%s  (log: %s)", args.host, args.port, log_path())
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if _STATE.robot is not None:
            _STATE.robot.shutdown()


if __name__ == "__main__":
    main()
