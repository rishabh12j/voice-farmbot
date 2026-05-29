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
from pathlib import Path
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
from .ai_core import AICore
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
    aicore: Optional[AICore] = None       # V2: LLM intent classifier for natural-language
    aicore_disabled: bool = False         # set true if Ollama unreachable, to skip retries
    model: str = "gemma3:4b"
    ollama_url: str = "http://localhost:11434"
    lights_on: bool = False               # tracked so the "lights" toggle flips correctly


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


def _dispatch_via_pi(action: str, source: str,
                     step_mm: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Send the action to the Pi intent server as an Intent JSON POST.

    Returns a position_payload-shaped dict on success, or None when this
    action isn't yet mapped on the V2 side (caller falls back to local).
    Estop and reset bypass the BT and hit their own endpoints.

    ``step_mm`` overrides the default jog step (used by /api/jog so the user
    can pick 10/50/100/500 mm). Voice jog still uses _VOICE_STEP_MM.
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
            step = float(step_mm) if step_mm is not None else _VOICE_STEP_MM
            new_x = _clamp(_STATE.pos_x + (step * direction if axis == "x" else 0), *_BOUNDS["x"])
            new_y = _clamp(_STATE.pos_y + (step * direction if axis == "y" else 0), *_BOUNDS["y"])
            new_z = _clamp(_STATE.pos_z + (step * direction if axis == "z" else 0), *_BOUNDS["z"])
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
                                   raw_text=f"(jog {step:.0f}mm) {action}",
                                   client_id="growmate_voice.app")
            status = "sent" if reply.status == "success" else reply.status
            cmds = reply.commands_published or [f"M {new_x:.0f} {new_y:.0f} {new_z:.0f}"]
            note = f"{cmds[0]}  — {label} {step:.0f}mm (via Pi)  [{status}]"
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


def _execute_action(action: str, source: str,
                    step_mm: Optional[float] = None) -> Dict[str, Any]:
    """Dispatch an action.

    ``step_mm`` only matters for the six jog actions (``x_plus`` etc.). When
    omitted, falls back to ``_VOICE_STEP_MM`` (the default for voice jogs).
    """
    # V2 path: if --pi-url is set and the action is wired, route to the Pi.
    routed = _dispatch_via_pi(action, source, step_mm=step_mm)
    if routed is not None:
        return routed

    step = float(step_mm) if step_mm is not None else _VOICE_STEP_MM

    if action == "estop":   return _do_estop(source)
    if action == "reset":   return _do_reset(source)
    if action == "home":    return _do_home(source)
    if action == "x_plus":  return _do_jog("x", +1, step, source)
    if action == "x_minus": return _do_jog("x", -1, step, source)
    if action == "y_plus":  return _do_jog("y", +1, step, source)
    if action == "y_minus": return _do_jog("y", -1, step, source)
    if action == "z_plus":  return _do_jog("z", +1, step, source)
    if action == "z_minus": return _do_jog("z", -1, step, source)
    if action == "water":     return _do_emit(["P_4"], "water", "water", source)
    if action == "photo":     return _do_emit(["I_1"], "photo", "photo", source)
    if action == "light_on":  return _do_emit(["D_L_1"], "light_on", "lights on", source)
    if action == "light_off": return _do_emit(["D_L_0"], "light_off", "lights off", source)
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
        # Top-level x/y/z for the new UI's status poll
        "x": _STATE.pos_x,
        "y": _STATE.pos_y,
        "z": _STATE.pos_z,
        "connected": _STATE.ready,
        # Nested for the legacy UI
        "position": _position_payload(),
        "farmbot": _farmbot_status_text(),
        "ros2_enabled": _STATE.ros2_enabled,
    }


@app.get("/api/commands")
def api_commands() -> List[Dict[str, Any]]:
    return [{"variants": v, "action": a} for v, a in COMMAND_MAP]


# Map upstream plant_name strings to the UI's plant type keys (which control colour).
_PLANT_TYPE_MAP = {
    # Edibles
    "tomato":             "tomato",
    "lettuce_little_gem": "lettuce",
    "lettuce":            "lettuce",
    "scallion":           "scallion",
    "spring_onion":       "scallion",
    "green_onion":        "scallion",
    "mixed pepper":       "pepper",
    "mixed_pepper":       "pepper",
    "pepper":             "pepper",
    # Herbs
    "basil":              "basil",
    "spearmint":          "spearmint",
    "mint":               "spearmint",
    # Flowers
    "marigold":           "marigold",
    "lily":               "lily",
    "asiatic_lily":       "lily",
    "geranium":           "geranium",
    "pelargonium":        "geranium",
    "cardinal flower":    "cardinal",
    "cardinal_flower":    "cardinal",
    "dianthus":           "dianthus",
    "carnation":          "dianthus",
    "sweet_william":      "dianthus",
    "euonymus":           "euonymus",
    "petunia":            "petunia",
    "begonia":            "begonia",
}


def _active_map_path() -> Optional[Path]:
    """Locate active_map.yaml in the repo, preferring the upstream map_handler
    config (the canonical 42-plant layout) and falling back to the Pi copy."""
    here = Path(__file__).resolve()
    src_dir = here.parents[2]   # .../src/
    candidates = [
        src_dir / "map_handler" / "map_handler" / "config" / "active_map.yaml",
        src_dir / "growmate_pi" / "config" / "active_map.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@app.get("/api/plants")
def api_plants() -> Dict[str, Any]:
    """Return the real garden layout for the UI map.

    Reads ``map_handler/config/active_map.yaml`` (the AURA-managed map with all
    42 plants and their actual coordinates) and projects each plant into the
    flat ``{type, x, y, name, water_quantity}`` shape the UI's renderPlants()
    expects.
    """
    import yaml as _yaml
    path = _active_map_path()
    if path is None:
        return {"plants": [], "source": None, "error": "active_map.yaml not found"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
    except Exception as exc:
        return {"plants": [], "source": str(path), "error": str(exc)}

    ref = data.get("map_reference", {}) or {}
    x_len = ref.get("x_len", 5691.2)
    y_len = ref.get("y_len", 2734.0)

    plants_raw = (data.get("plant_details", {}) or {}).get("plants", {}) or {}
    plants_out: List[Dict[str, Any]] = []
    for _, p in plants_raw.items():
        idents = p.get("identifiers", {}) or {}
        details = p.get("plant_details", {}) or {}
        pos = p.get("position", {}) or {}
        status = p.get("status", {}) or {}

        raw_name = str(idents.get("plant_name", "Unknown"))
        ptype = _PLANT_TYPE_MAP.get(raw_name.lower(), "lettuce")
        # human-friendly name for the tooltip / aria-label
        display = raw_name.replace("_", " ").title()
        idx = idents.get("index", len(plants_out) + 1)

        plants_out.append({
            "type": ptype,
            "x": float(pos.get("x", 0)),
            "y": float(pos.get("y", 0)),
            "name": f"{display} #{idx}",
            "water_quantity": float(details.get("water_quantity", 2.0)),
            "stage": status.get("growth_stage", ""),
        })

    plants_out.sort(key=lambda p: (p["x"], p["y"]))
    return {
        "plants": plants_out,
        "count": len(plants_out),
        "workspace": {"x_len": x_len, "y_len": y_len},
        "source": str(path),
    }


@app.post("/api/farmbot/power")
def api_farmbot_power(body: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either {action: 'on'|'off'} (legacy) or {on: true|false} (new UI)."""
    if "on" in body:
        action = "on" if body["on"] else "off"
    else:
        action = body.get("action", "")
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
    return {"farmbot": msg, "ready": _STATE.ready, "on": _STATE.ready, "ok": True}


# Map the new UI's compact jog directions ("x+", "y-", ...) to (axis, sign).
_JOG_DIR_MAP = {
    "x+": ("x", +1), "x-": ("x", -1),
    "y+": ("y", +1), "y-": ("y", -1),
    "z+": ("z", +1), "z-": ("z", -1),
}


@app.post("/api/jog")
def api_jog(body: Dict[str, Any]) -> Dict[str, Any]:
    """Accept {dir: 'x+', step: 100} (new UI) or {axis, direction, step} (legacy)."""
    if "dir" in body:
        m = _JOG_DIR_MAP.get(body["dir"])
        if not m:
            return JSONResponse(status_code=400, content={"error": f"bad dir '{body['dir']}'"})
        axis, sign = m
    else:
        axis = body.get("axis", "")
        sign = 1 if int(body.get("direction", 1)) > 0 else -1
    if axis not in _BOUNDS:
        return JSONResponse(status_code=400, content={"error": f"bad axis '{axis}'"})
    step = float(body.get("step", _VOICE_STEP_MM))
    action_name = f"{axis}_{'plus' if sign > 0 else 'minus'}"
    payload = _execute_action(action_name, source="button", step_mm=step)
    return {**payload, "x": _STATE.pos_x, "y": _STATE.pos_y, "z": _STATE.pos_z, "ok": True}


# Map new UI action names to the existing _execute_action vocabulary.
_ACTION_ALIASES = {
    "water_all": "water",
    "lights": "lights_toggle",  # handled below
}


@app.post("/api/action")
def api_action(body: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a high-level action. Accepts JSON {action: '...'}."""
    raw = body.get("action", "")
    action = _ACTION_ALIASES.get(raw, raw)

    if action == "lights_toggle":
        # Toggle server-side state — the UI doesn't have to know what was last.
        # Allow override via {currently_on: bool} if the client knows better
        # (e.g. after a manual D_L_0/D_L_1 from keyboard_controller).
        if "currently_on" in body:
            _STATE.lights_on = bool(body["currently_on"])
        next_action = "light_off" if _STATE.lights_on else "light_on"
        payload = _execute_action(next_action, source="button")
        _STATE.lights_on = (next_action == "light_on")
        payload["lightsOn"] = _STATE.lights_on
        return payload

    # Keep server state in sync when the UI uses explicit light_on / light_off
    if action == "light_on":  _STATE.lights_on = True
    if action == "light_off": _STATE.lights_on = False

    allowed = {"home", "water", "photo", "reset", "estop", "light_on", "light_off"}
    if action not in allowed:
        return JSONResponse(status_code=400, content={"error": f"bad action '{action}'"})
    return _execute_action(action, source="button")


@app.post("/api/estop")
def api_estop() -> Dict[str, Any]:
    # Route through _execute_action so /estop on the Pi gets hit when --pi-url is set
    return _execute_action("estop", source="button")


@app.post("/api/reset")
def api_reset() -> Dict[str, Any]:
    return _execute_action("reset", source="button")


@app.get("/api/history")
def api_history(limit: int = 50) -> Dict[str, Any]:
    entries = _STATE.history.recent(limit=limit)
    return {"entries": entries, "history": entries}


@app.post("/api/history/clear")
def api_history_clear() -> Dict[str, Any]:
    removed = _STATE.history.clear()
    log.info("History cleared (%d entries removed)", removed)
    return {"removed": removed}


# --------------------------------------------------------------------------- router
_PLANT_HINTS = (
    "tomato", "tomatoes", "lettuce", "marigold", "scallion",
    "pepper", "peppers", "herbs", "carrot", "strawberr", "basil",
    "the plants", "all plants", "everything",
)
_QUESTION_HINTS = ("when ", "why ", "how ", "what ", "should ", "tell ", "explain ")
_EMERGENCY_HINTS = ("stop", "halt", "emergency", "freeze", "abort")


def _route_transcript(transcript: str, matched_action: Optional[str],
                      confidence: str) -> str:
    """Decide whether to run the matched pattern or go to AICore.

    Returns "pattern" or "aicore". Emergency always returns "pattern" so the
    safety-critical path never waits for the LLM.
    """
    t = transcript.lower().strip()
    if any(p in t for p in _EMERGENCY_HINTS):
        return "pattern"
    if any(p in t for p in _PLANT_HINTS):
        return "aicore"
    if any(t.startswith(q) or f" {q.strip()} " in f" {t} " for q in _QUESTION_HINTS):
        return "aicore"
    if matched_action is not None and confidence == "exact":
        return "pattern"
    return "aicore"


def _get_aicore() -> Optional[AICore]:
    """Lazy singleton — returns None if Ollama is unreachable."""
    if _STATE.aicore_disabled:
        return None
    if _STATE.aicore is not None:
        return _STATE.aicore
    try:
        # Use the Pi-side garden config so plant names match what Pi resolves.
        from pathlib import Path
        cfg = (Path(__file__).resolve().parents[3] / "growmate_pi" / "config" / "farmbot.yaml")
        if not cfg.exists():
            # fallback to local growmate_voice config
            cfg = Path(__file__).resolve().parents[1] / "config" / "farmbot.yaml"
        _STATE.aicore = AICore(config_path=str(cfg),
                               model=_STATE.model, ollama_url=_STATE.ollama_url)
        if not _STATE.aicore.is_available():
            log.warning("Ollama not reachable at %s — AICore disabled", _STATE.ollama_url)
            _STATE.aicore_disabled = True
            _STATE.aicore = None
            return None
        log.info("AICore ready (model=%s, config=%s)", _STATE.model, cfg)
        return _STATE.aicore
    except Exception as exc:
        log.warning("AICore init failed: %s — disabling", exc)
        _STATE.aicore_disabled = True
        return None


def _dispatch_via_aicore(transcript: str, source: str) -> Dict[str, Any]:
    """Run the LLM classifier and dispatch the resulting intents to Pi.

    Returns a position_payload-shaped dict. If anything fails (LLM down,
    Pi unreachable, no intents), falls back to local sim mode with the
    transcript as the last_cmd note.
    """
    ai = _get_aicore()
    if ai is None:
        _record(source, None, [], "ignored",
                "AICore unavailable", transcript=transcript)
        return _position_payload(last_cmd=f"(LLM unavailable for: {transcript})")

    intents = ai._classify(transcript) or []
    if not intents:
        _record(source, None, [], "ignored",
                "No intents from LLM", transcript=transcript)
        return _position_payload(last_cmd=f"(LLM no intents: {transcript})")

    # Build PiIntent objects from the raw classifier dicts
    if not (_PI_CLIENT_AVAILABLE and _STATE.pi_url):
        # No Pi configured — log and return; can't execute robot actions client-side
        responses = " ".join(i.get("response", "") for i in intents)
        _record(source, intents[0].get("action"), [], "simulated",
                f"AICore (no Pi): {responses}", transcript=transcript)
        return _position_payload(last_cmd=f"AICore -> {intents[0].get('action')} (no Pi)")

    pi_intents = [
        PiIntent(
            action=i.get("action", "general_question"),
            target=i.get("target"),
            params=i.get("params", {}) or {},
            response=i.get("response", "Done."),
            question=i.get("question"),
        )
        for i in intents
    ]

    try:
        reply = pi_post_intent(_STATE.pi_url, pi_intents,
                               raw_text=transcript, client_id="growmate_voice.app")
    except Exception as exc:
        log.warning("AICore dispatch to Pi failed: %s", exc)
        _record(source, intents[0].get("action"), [], "error",
                f"Pi error: {exc}", transcript=transcript)
        return _position_payload(last_cmd=f"(Pi error: {exc})")

    status = "sent" if reply.status == "success" else reply.status
    cmds = reply.commands_published or []
    actions = ",".join(i.get("action", "?") for i in intents)
    note = f"{' | '.join(cmds) or '(no cmds)'}  — {actions} (LLM via Pi)  [{status}]"
    _record(source, intents[0].get("action"), cmds, status, note,
            transcript=transcript)
    payload = _position_payload(last_cmd=note)
    payload["tts_text"] = reply.tts_text or " ".join(i.get("response", "") for i in intents)
    return payload


@app.post("/api/text")
async def api_text(body: Dict[str, Any]) -> Any:
    """Text-input variant of /api/voice.

    Skips STT and runs the rest of the pipeline: router -> pattern OR AICore
    -> Pi dispatch -> TTS. Used by the web UI's "type instead" fallback.
    """
    text = (body or {}).get("text", "").strip()
    tts = (body or {}).get("tts", "kokoro")
    enable_tts = str((body or {}).get("enable_tts", "true")).lower()

    if not text:
        return JSONResponse(status_code=400, content={"error": "empty text"})

    pipeline_log: List[str] = [f"📝 (text) '{text}'"]
    try:
        action, confidence = match_command(text)
        route = _route_transcript(text, action, confidence)
        pipeline_log.append(f"🔍 Pattern: {action} ({confidence})  ➜  Route: {route}")

        if route == "pattern" and action is not None:
            position_payload = _execute_action(action, source="text")
            if _STATE.history._entries:
                last = _STATE.history._entries[-1]
                last.transcript = text
                last.confidence = confidence
            pipeline_log.append(f"🤖 {position_payload['last_cmd']}")
        else:
            position_payload = _dispatch_via_aicore(text, source="text")
            pipeline_log.append(f"🧠 AICore: {position_payload['last_cmd']}")

        tts_spoken = ""
        tts_audio_b64: Optional[str] = None
        if enable_tts == "true" and tts != "none":
            phrase = (position_payload.get("tts_text") or "").strip() or get_tts_phrase(action)
            try:
                tts_backend = _get_tts(tts)
                if tts_backend.is_available() and phrase:
                    tts_np, tts_sr = tts_backend.synthesise(phrase)
                    wav_out = audio_to_wav_bytes(tts_np, sample_rate=tts_sr)
                    tts_audio_b64 = base64.b64encode(wav_out).decode("ascii")
                    tts_spoken = phrase
            except Exception as exc:
                pipeline_log.append(f"⚠ TTS error: {exc}")

        pipeline_log.append("✅ Done")
        return JSONResponse({
            "result": {
                "raw_transcript": text,
                "matched_action": action,
                "confidence": confidence,
                "tts_spoken": tts_spoken,
                "position": position_payload,
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": tts_audio_b64,
        })
    except Exception as exc:
        log.exception("Text pipeline error")
        return JSONResponse(status_code=500, content={
            "error": str(exc),
            "log": "\n".join(pipeline_log),
            "trace": traceback.format_exc(),
        })


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
        route = _route_transcript(transcript, action, confidence)
        pipeline_log.append(f"🔍 Pattern: {action} ({confidence})  ➜  Route: {route}")

        if route == "pattern" and action is not None:
            # Fast path — pattern match drives the action (works for emergency,
            # home, lights, photo, jog, generic water/photo).
            position_payload = _execute_action(action, source="voice")
            if _STATE.history._entries:
                last = _STATE.history._entries[-1]
                last.transcript = transcript
                last.confidence = confidence
            pipeline_log.append(f"🤖 {position_payload['last_cmd']}")
        else:
            # Smart path — LLM classifies (plant-targeted, general questions,
            # or anything pattern match couldn't handle). Falls back to ignored
            # if Ollama or Pi is unavailable.
            position_payload = _dispatch_via_aicore(transcript, source="voice")
            pipeline_log.append(f"🧠 AICore: {position_payload['last_cmd']}")

        tts_spoken = ""
        tts_audio_b64: Optional[str] = None
        if enable_tts.lower() == "true" and tts != "none":
            # Prefer the LLM-generated response (richer + plant-specific) when
            # we took the AICore path; otherwise use the canned pattern phrase.
            phrase = (position_payload.get("tts_text") or "").strip() or get_tts_phrase(action)
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
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>GrowMate — your garden companion</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
/* ---------- design tokens ---------- */
:root{
  --moss:        #4a7c59;
  --moss-deep:   #355a40;
  --moss-soft:   #cfe0d2;
  --cream:       #faf6ee;
  --cream-deep:  #f1ead9;
  --paper:       #ffffff;
  --clay:        #c97c5d;
  --clay-soft:   #f1d9cb;
  --tomato:      #c1392b;
  --tomato-deep: #962b20;
  --ink:         #2b2a26;
  --ink-soft:    #5e5b53;
  --line:        #e6dfcf;

  --radius-s: 12px;
  --radius-m: 18px;
  --radius-l: 28px;
  --radius-xl: 36px;

  --shadow-s: 0 1px 2px rgba(43,42,38,.06), 0 2px 6px rgba(43,42,38,.04);
  --shadow-m: 0 2px 6px rgba(43,42,38,.07), 0 10px 24px rgba(43,42,38,.07);
  --shadow-l: 0 8px 20px rgba(43,42,38,.10), 0 24px 60px rgba(43,42,38,.10);

  --focus: 0 0 0 3px var(--cream), 0 0 0 6px var(--moss);
  --focus-danger: 0 0 0 3px var(--cream), 0 0 0 6px var(--tomato);

  --tap: 56px;
}

*{ box-sizing: border-box; }
html, body{ margin:0; padding:0; }
html{ background: var(--cream); }
body{
  font-family: "Nunito", system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 18px;
  line-height: 1.4;
  color: var(--ink);
  background: var(--cream);
  -webkit-font-smoothing: antialiased;
  min-height: 100dvh;
  min-height: 100vh;
}

button{ font: inherit; color: inherit; cursor: pointer; }
button:focus-visible, a:focus-visible, [tabindex]:focus-visible{
  outline: none;
  box-shadow: var(--focus);
}
.danger:focus-visible{ box-shadow: var(--focus-danger); }

.sr-only{
  position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0;
}

/* ---------- layout shell ---------- */
.app{
  max-width: 1080px;
  margin: 0 auto;
  padding: 16px clamp(14px, 3vw, 28px) 24px;
  display: grid;
  gap: 16px;
}

/* ---------- header ---------- */
.header{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.brand{
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark{
  width: 48px; height: 48px;
  background: var(--moss);
  border-radius: 14px;
  display: grid; place-items: center;
  color: var(--cream);
  box-shadow: var(--shadow-s);
  flex: 0 0 auto;
}
.brand-name{
  font-weight: 800;
  font-size: 28px;
  letter-spacing: -0.01em;
  line-height: 1;
}
.brand-sub{
  font-size: 14px;
  color: var(--ink-soft);
  font-weight: 600;
  margin-top: 2px;
}

.header-right{
  display: flex;
  align-items: center;
  gap: 10px;
}

.conn{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 10px 14px;
  font-weight: 700;
  font-size: 15px;
  white-space: nowrap;
  min-height: 44px;
}
.conn .dot{
  width: 10px; height: 10px;
  background: var(--moss);
  border-radius: 999px;
  box-shadow: 0 0 0 4px rgba(74,124,89,.18);
}
.conn[data-state="offline"] .dot{
  background: var(--ink-soft);
  box-shadow: 0 0 0 4px rgba(94,91,83,.18);
  animation: blink 1.4s infinite ease-in-out;
}
.conn[data-state="reconnect"] .dot{
  background: var(--clay);
  box-shadow: 0 0 0 4px rgba(201,124,93,.22);
  animation: blink 1s infinite;
}
@keyframes blink{ 50%{ opacity: .35; } }

.icon-btn{
  width: var(--tap); height: var(--tap);
  border-radius: 14px;
  background: var(--paper);
  border: 1px solid var(--line);
  display: grid; place-items: center;
  color: var(--ink);
  transition: transform .12s ease, background .12s ease;
}
.icon-btn:hover{ background: var(--cream-deep); }
.icon-btn:active{ transform: scale(.96); }

.estop{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: var(--tomato);
  color: #fff;
  border: none;
  border-radius: 16px;
  padding: 14px 18px;
  font-weight: 800;
  font-size: 17px;
  letter-spacing: .01em;
  min-height: var(--tap);
  box-shadow: 0 4px 0 var(--tomato-deep), var(--shadow-m);
  transition: transform .08s ease, box-shadow .08s ease;
}
.estop:hover{ background: #cf4334; }
.estop:active{
  transform: translateY(2px);
  box-shadow: 0 2px 0 var(--tomato-deep), var(--shadow-s);
}
.estop-dot{
  width: 12px; height: 12px;
  background: #fff;
  border-radius: 999px;
  box-shadow: 0 0 0 4px rgba(255,255,255,.25);
}

/* ---------- garden state strip ---------- */
.state-strip{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-l);
  padding: 16px 20px;
  display: grid;
  grid-template-columns: 1fr 1.2fr 1.6fr;
  gap: 18px;
  align-items: center;
  box-shadow: var(--shadow-s);
}
@media (max-width: 720px){
  .state-strip{ grid-template-columns: 1fr; gap: 10px; padding: 14px 16px;}
}
.state-cell{ min-width: 0; }
.state-label{
  font-size: 13px;
  color: var(--ink-soft);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.state-value{
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.state-value.quote{
  font-weight: 600;
  color: var(--moss-deep);
}
.state-value.quote::before{
  content: "“";
  color: var(--clay);
  font-weight: 800;
  margin-right: 2px;
}
.state-value.quote::after{
  content: "”";
  color: var(--clay);
  font-weight: 800;
}

/* ---------- main two-column ---------- */
.main{
  display: grid;
  grid-template-columns: 1.25fr 1fr;
  gap: 16px;
  align-items: stretch;
}
@media (max-width: 880px){
  .main{ grid-template-columns: 1fr; }
}

.panel{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-l);
  padding: 20px;
  box-shadow: var(--shadow-s);
}

.panel-head{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}
.panel-title{
  font-size: 16px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-soft);
  margin: 0;
}
.panel-action{
  font-size: 14px;
  font-weight: 700;
  color: var(--moss-deep);
  background: none;
  border: none;
  padding: 8px 10px;
  border-radius: 10px;
}
.panel-action:hover{ background: var(--cream-deep); }

/* ---------- voice panel ---------- */
.voice-panel{
  background: linear-gradient(160deg, #f6f0df 0%, var(--cream) 60%);
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: 12px;
  position: relative;
  overflow: hidden;
  min-height: 620px;
}

/* prompt block (hint + chips) sits right under the header */
.prompt-block{
  position: relative;
  z-index: 2;
  display: grid;
  gap: 10px;
}
.prompt-block .hint{ font-size: 14px; }

/* chat feed: its own row in the grid, scrollable, bubbles drift up from the mic */
.chat-feed{
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 8px;
  min-height: 80px;
  padding: 4px 2px 0;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, transparent 0, #000 50px, #000 100%);
          mask-image: linear-gradient(180deg, transparent 0, #000 50px, #000 100%);
}
.bubble{
  max-width: 88%;
  padding: 9px 13px;
  border-radius: 14px;
  font-size: 14.5px;
  font-weight: 600;
  line-height: 1.35;
  background: rgba(255,255,255,.85);
  border: 1px solid rgba(74,124,89,.16);
  box-shadow: 0 2px 6px rgba(43,42,38,.05);
  opacity: 0;
  transform: translateY(8px);
  animation: bubbleIn .5s cubic-bezier(.22,.61,.36,1) forwards;
  position: relative;
  word-wrap: break-word;
  overflow-wrap: anywhere;
}
.bubble .b-label{
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-soft);
  margin-bottom: 2px;
  display: block;
}
.bubble.you{
  align-self: flex-start;
  background: rgba(255,255,255,.72);
  color: var(--ink);
}
.bubble.you .b-label{ color: var(--clay); }
.bubble.bot{
  align-self: flex-end;
  background: rgba(74,124,89,.92);
  color: #fff;
  border-color: rgba(53,90,64,.4);
}
.bubble.bot .b-label{ color: rgba(255,255,255,.7); }
.bubble.fade-old{ opacity: .55; }
.bubble.fade-older{ opacity: .28; }
@keyframes bubbleIn{
  from{ opacity: 0; transform: translateY(10px); }
  to  { opacity: 1; transform: translateY(0); }
}
.bubble.bot.is-typing::after{
  content: "•••";
  letter-spacing: .15em;
  animation: dots 1.2s infinite steps(4, end);
}
@keyframes dots{
  0%   { content: "•"; }
  33%  { content: "••"; }
  66%  { content: "•••"; }
  100% { content: "••••"; }
}
.voice-panel::after{
  /* botanical line-art accent (sprout) */
  content: "";
  position: absolute;
  right: -40px; bottom: -40px;
  width: 220px; height: 220px;
  background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200' fill='none' stroke='%234a7c59' stroke-width='2' stroke-linecap='round'><path d='M100 180 V90'/><path d='M100 120 C70 110 55 90 55 65 C80 70 100 88 100 120 Z'/><path d='M100 100 C130 95 148 75 148 50 C122 55 100 70 100 100 Z'/><path d='M70 180 Q100 165 130 180'/></svg>") center/contain no-repeat;
  opacity: .14;
  pointer-events: none;
}

.voice-stage{
  display: grid;
  place-items: center;
  padding: 20px 0 30px;
  position: relative;
  z-index: 2;
}
/* gentle wash behind mic so chat bubbles softly recede toward it */
.voice-stage::before{
  content: "";
  position: absolute;
  inset: -6px;
  background: radial-gradient(closest-side, rgba(250,246,238,.75) 45%, rgba(250,246,238,.35) 70%, transparent 90%);
  pointer-events: none;
  z-index: -1;
}

.mic-wrap{
  position: relative;
  width: clamp(180px, 26vw, 220px);
  aspect-ratio: 1/1;
}
.mic-wrap .ring{
  position: absolute;
  inset: 0;
  border-radius: 999px;
  border: 2px solid var(--moss);
  opacity: 0;
  pointer-events: none;   /* decorative — never capture clicks from the mic */
}
.mic[data-state="recording"] ~ .ring,
.mic-wrap[data-state="recording"] .ring{
  animation: ripple 2.2s cubic-bezier(.22,.61,.36,1) infinite;
}
.mic-wrap[data-state="recording"] .ring.r2{ animation-delay: .7s; }
.mic-wrap[data-state="recording"] .ring.r3{ animation-delay: 1.4s; }
@keyframes ripple{
  0%   { transform: scale(.7); opacity: .55; }
  100% { transform: scale(1.35); opacity: 0; }
}

.mic{
  position: absolute;
  inset: 14%;
  border-radius: 999px;
  background: var(--moss);
  color: #fff;
  border: none;
  display: grid;
  place-items: center;
  box-shadow: 0 10px 0 var(--moss-deep), var(--shadow-l);
  transition: transform .1s ease, box-shadow .1s ease, background .2s ease;
  cursor: pointer;
}
.mic:hover{ background: #557f63; }
.mic:active{
  transform: translateY(4px);
  box-shadow: 0 6px 0 var(--moss-deep), var(--shadow-m);
}
.mic[data-state="recording"]{
  background: var(--tomato);
  box-shadow: 0 10px 0 var(--tomato-deep), var(--shadow-l);
  animation: pulseScale 1.6s ease-in-out infinite;
}
.mic[data-state="processing"]{
  background: var(--clay);
  box-shadow: 0 10px 0 #8c5440, var(--shadow-l);
}
@keyframes pulseScale{
  0%,100% { transform: scale(1); }
  50%     { transform: scale(1.04); }
}

.mic svg{ width: 46%; height: 46%; }
.mic-label{
  position: absolute;
  bottom: -42px; left: 0; right: 0;
  text-align: center;
  font-weight: 800;
  font-size: 18px;
  color: var(--ink);
}

.voice-foot{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 36px;
  position: relative;
  z-index: 1;
  flex-wrap: wrap;
}
.hint{
  font-size: 15px;
  color: var(--ink-soft);
  max-width: 60%;
}
.hint b{ color: var(--ink); font-weight: 800; }

.text-fallback{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  font-weight: 700;
  color: var(--moss-deep);
  min-height: 48px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.text-fallback:hover{ background: var(--cream-deep); }

.text-input-row{
  display: none;
  gap: 8px;
  width: 100%;
  margin-top: 8px;
}
.text-input-row.show{ display: flex; }
.text-input-row input{
  flex: 1;
  font: inherit;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: var(--paper);
  min-height: 48px;
}
.text-input-row input:focus{
  outline: none;
  border-color: var(--moss);
  box-shadow: 0 0 0 3px rgba(74,124,89,.18);
}
.send-btn{
  background: var(--moss);
  color: #fff;
  border: none;
  border-radius: 12px;
  padding: 0 18px;
  font-weight: 800;
  min-height: 48px;
}
.send-btn:hover{ background: #557f63; }

.example-chips{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}
.example-chips .chip{
  background: var(--moss-soft);
  color: var(--moss-deep);
  border: none;
  border-radius: 999px;
  padding: 7px 13px;
  font-size: 13.5px;
  font-weight: 700;
}
.example-chips .chip:hover{ background: #bfd4c2; }

/* ---------- right column ---------- */
.right-col{
  display: grid;
  gap: 16px;
  align-content: start;
}

/* ---------- quick actions ---------- */
.quick-grid{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.quick{
  appearance: none;
  background: var(--paper);
  border: 1.5px solid var(--line);
  border-radius: 20px;
  padding: 16px 14px;
  min-height: 120px;
  display: grid;
  grid-template-rows: 1fr auto;
  align-items: center;
  justify-items: start;
  gap: 8px;
  text-align: left;
  transition: transform .08s ease, border-color .12s ease, background .12s ease;
}
.quick:hover{ border-color: var(--moss); background: var(--cream-deep); }
.quick:active{ transform: scale(.98); }
.quick .q-icon{
  width: 44px; height: 44px;
  border-radius: 12px;
  background: var(--moss-soft);
  color: var(--moss-deep);
  display: grid; place-items: center;
}
.quick[data-tone="clay"]  .q-icon{ background: var(--clay-soft); color: var(--clay); }
.quick[data-tone="warn"]  .q-icon{ background: #f6e2c8; color: #a06a26; }
.quick[data-tone="moss"]  .q-icon{ background: var(--moss-soft); color: var(--moss-deep); }
.quick .q-label{
  font-weight: 800;
  font-size: 17px;
  line-height: 1.2;
}
.quick .q-sub{
  font-size: 13px;
  color: var(--ink-soft);
  font-weight: 600;
}

/* ---------- garden map ---------- */
.map-panel{ padding: 18px; }
.map-head{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.map-meta{
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-soft);
}
.map-wrap{
  position: relative;
  border-radius: 18px;
  overflow: hidden;
  background:
    repeating-linear-gradient(0deg,   rgba(74,124,89,.05) 0 1px, transparent 1px 40px),
    repeating-linear-gradient(90deg,  rgba(74,124,89,.05) 0 1px, transparent 1px 40px),
    linear-gradient(180deg, #ead8b8 0%, #e1ca9b 100%);
  border: 1.5px solid #c9b58a;
  box-shadow: inset 0 0 0 4px #f0e3c8, inset 0 0 0 6px #c9b58a;
  aspect-ratio: 27 / 57;
  max-width: 250px;
  margin: 0 auto;
}
.map-svg{
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  display: block;
}
.plant{
  cursor: pointer;
  transition: transform .15s ease;
  transform-origin: center;
  transform-box: fill-box;
}
.plant:hover{ transform: scale(1.3); }
.plant:focus{ outline: none; }
.plant:focus-visible{ outline: 3px solid var(--moss); outline-offset: 2px; }
.gantry-rail{
  stroke: var(--moss-deep);
  stroke-width: 30;
  stroke-linecap: round;
  opacity: .85;
  transition: y .6s cubic-bezier(.22,.61,.36,1);
}
.gantry-head{
  fill: var(--moss-deep);
  stroke: #fff;
  stroke-width: 10;
  transition: cx .6s cubic-bezier(.22,.61,.36,1), cy .6s cubic-bezier(.22,.61,.36,1);
}
.gantry-pulse{
  fill: var(--moss);
  opacity: .35;
  transform-origin: center;
  transform-box: fill-box;
  animation: gantryPulse 2.4s ease-in-out infinite;
  transition: cx .6s cubic-bezier(.22,.61,.36,1), cy .6s cubic-bezier(.22,.61,.36,1);
}
@keyframes gantryPulse{
  0%,100% { r: 60; opacity: .35; }
  50%     { r: 140; opacity: 0; }
}
.home-marker{
  fill: none;
  stroke: var(--clay);
  stroke-width: 8;
  stroke-dasharray: 18 10;
}
.map-legend{
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 12px;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink);
}
.map-legend .lg{ display: inline-flex; align-items: center; gap: 6px; }
.map-legend .sw{
  width: 12px; height: 12px;
  border-radius: 999px;
  display: inline-block;
  box-shadow: 0 0 0 2px rgba(255,255,255,.6);
}
.map-tooltip{
  position: absolute;
  background: var(--ink);
  color: #fff;
  padding: 6px 10px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  pointer-events: none;
  transform: translate(-50%, -120%);
  white-space: nowrap;
  opacity: 0;
  transition: opacity .15s ease;
  z-index: 5;
  box-shadow: var(--shadow-m);
}
.map-tooltip.show{ opacity: 1; }

/* ---------- manual move disclosure ---------- */
.disclosure{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow-s);
  overflow: hidden;
}
.disclosure-toggle{
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  background: none;
  border: none;
  padding: 14px 20px;
  font: inherit;
  font-weight: 800;
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-soft);
  min-height: 52px;
}
.disclosure-toggle:hover{ background: var(--cream-deep); }
.disclosure-toggle .chev{
  transition: transform .25s ease;
  color: var(--moss-deep);
}
.disclosure[data-open="1"] .disclosure-toggle .chev{ transform: rotate(90deg); }
.disclosure-body{
  display: none;
  padding: 4px 20px 20px;
}
.disclosure[data-open="1"] .disclosure-body{ display: block; }

/* ---------- quick-actions / manual-move swap ---------- */
.qa-swap{
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.qa-view-manual .jog-wrap{ justify-content: center; }

/* ---------- jog ---------- */
.jog-wrap{
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 18px;
  align-items: center;
}
@media (max-width: 480px){ .jog-wrap{ grid-template-columns: 1fr; }}

.step-row{
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.step-row .step-label{
  font-weight: 700;
  color: var(--ink-soft);
  margin-right: 4px;
}
.step-btn{
  min-height: var(--tap);
  padding: 0 16px;
  border-radius: var(--radius-m);
  border: 2px solid var(--line);
  background: var(--paper);
  color: var(--ink);
  font-family: inherit;
  font-size: 17px;
  font-weight: 700;
  cursor: pointer;
  transition: all .12s ease;
}
.step-btn:hover{ background: var(--cream-deep); }
.step-btn[aria-checked="true"]{
  background: var(--moss);
  color: #fff;
  border-color: var(--moss-deep);
  box-shadow: var(--shadow-s);
}
.step-btn:focus-visible{ outline: none; box-shadow: var(--focus); }

.dpad{
  display: grid;
  grid-template-columns: repeat(3, 64px);
  grid-template-rows: repeat(3, 64px);
  gap: 8px;
  justify-content: center;
}
.dpad .jog-btn{
  border-radius: 16px;
  background: var(--cream-deep);
  border: 1.5px solid var(--line);
  display: grid; place-items: center;
  color: var(--moss-deep);
  min-width: 48px; min-height: 48px;
  transition: transform .08s ease, background .1s ease;
}
.dpad .jog-btn:hover{ background: #ebe2c6; }
.dpad .jog-btn:active{ transform: scale(.94); background: var(--moss-soft); }
.dpad .center{
  background: var(--paper);
  border: 1.5px dashed var(--line);
  font-size: 12px;
  font-weight: 800;
  color: var(--ink-soft);
  letter-spacing: .04em;
  cursor: default;
}
.dpad .center:hover{ background: var(--paper); }
.dpad .up    { grid-column: 2; grid-row: 1; }
.dpad .left  { grid-column: 1; grid-row: 2; }
.dpad .right { grid-column: 3; grid-row: 2; }
.dpad .down  { grid-column: 2; grid-row: 3; }
.dpad .center{ grid-column: 2; grid-row: 2; }

.z-col{
  display: grid;
  gap: 8px;
  grid-template-rows: auto auto auto;
  justify-items: center;
}
.z-btn{
  width: 64px; height: 64px;
  border-radius: 16px;
  background: var(--cream-deep);
  border: 1.5px solid var(--line);
  display: grid; place-items: center;
  font-weight: 800;
  color: var(--moss-deep);
  font-size: 22px;
}
.z-btn:hover{ background: #ebe2c6; }
.z-btn:active{ background: var(--moss-soft); transform: scale(.94); }
.z-label{
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .08em;
  color: var(--ink-soft);
  text-transform: uppercase;
}

/* ---------- history ---------- */
.history-list{
  list-style: none;
  margin: 0; padding: 0;
  display: grid;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.history-list::-webkit-scrollbar{ width: 8px; }
.history-list::-webkit-scrollbar-thumb{ background: var(--line); border-radius: 999px; }

.hist{
  background: var(--cream);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 12px 14px;
  display: grid;
  grid-template-columns: 36px 1fr auto;
  gap: 12px;
  align-items: start;
  text-align: left;
  width: 100%;
  transition: background .1s ease;
}
.hist:hover{ background: var(--cream-deep); }
.hist:focus-visible{ background: var(--cream-deep); }

.hist .hist-icon{
  width: 36px; height: 36px;
  border-radius: 10px;
  display: grid; place-items: center;
  background: var(--moss-soft);
  color: var(--moss-deep);
  flex: 0 0 auto;
}
.hist[data-status="fail"] .hist-icon{
  background: #f3d4d0;
  color: var(--tomato-deep);
}
.hist .hist-body{ min-width: 0; }
.hist .hist-said{
  font-weight: 700;
  font-size: 16px;
  line-height: 1.3;
  word-break: break-word;
}
.hist .hist-did{
  font-size: 14px;
  color: var(--ink-soft);
  font-weight: 600;
  margin-top: 2px;
}
.hist .hist-time{
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-soft);
  white-space: nowrap;
  padding-top: 4px;
}
.hist-empty{
  text-align: center;
  font-size: 15px;
  color: var(--ink-soft);
  padding: 28px 10px;
  font-weight: 600;
}

/* ---------- footer / settings ---------- */
.footer{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 4px 0;
  flex-wrap: wrap;
}
.legal{
  font-size: 13px;
  color: var(--ink-soft);
  font-weight: 600;
}
.foot-buttons{ display: inline-flex; gap: 8px; flex-wrap: wrap; }
.ghost{
  background: none;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 14px;
  font-weight: 700;
  font-size: 14px;
  color: var(--ink);
  min-height: 44px;
}
.ghost:hover{ background: var(--paper); }

/* ---------- power gate ---------- */
.gate{
  position: fixed;
  inset: 0;
  z-index: 50;
  background:
    radial-gradient(1100px 700px at 20% -10%, #e5ecdd 0%, transparent 60%),
    radial-gradient(900px 700px at 110% 110%, #f1d9cb55 0%, transparent 60%),
    var(--cream);
  display: grid;
  place-items: center;
  padding: 28px;
  transition: opacity .35s ease, visibility .35s ease;
}
.gate.hidden{ opacity: 0; visibility: hidden; pointer-events: none; }
.gate-card{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-xl);
  padding: 36px 32px;
  max-width: 520px;
  text-align: center;
  box-shadow: var(--shadow-l);
  display: grid;
  gap: 18px;
  justify-items: center;
}
.gate-mark{
  width: 84px; height: 84px;
  background: var(--moss-soft);
  border-radius: 26px;
  display: grid; place-items: center;
  color: var(--moss-deep);
}
.gate h1{
  margin: 0;
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.gate p{
  margin: 0;
  color: var(--ink-soft);
  font-size: 17px;
  font-weight: 600;
  max-width: 38ch;
}
.power-btn{
  position: relative;
  background: var(--moss);
  color: #fff;
  border: none;
  border-radius: 999px;
  padding: 18px 32px;
  min-height: 64px;
  font-size: 20px;
  font-weight: 800;
  display: inline-flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 6px 0 var(--moss-deep), var(--shadow-m);
  transition: transform .08s ease, box-shadow .08s ease, background .2s ease;
}
.power-btn:hover{ background: #557f63; }
.power-btn:active{
  transform: translateY(3px);
  box-shadow: 0 3px 0 var(--moss-deep), var(--shadow-s);
}
.power-btn[data-loading="1"]{
  background: var(--clay);
  box-shadow: 0 6px 0 #8c5440, var(--shadow-m);
  pointer-events: none;
}
.power-btn[data-loading="1"] .pwr-icon{ animation: spin 1.1s linear infinite; }
@keyframes spin{ to{ transform: rotate(360deg); } }
.gate-foot{
  font-size: 14px;
  color: var(--ink-soft);
  font-weight: 600;
}

/* ---------- toast ---------- */
.toast-wrap{
  position: fixed;
  left: 0; right: 0; bottom: 16px;
  display: grid; place-items: center;
  pointer-events: none;
  z-index: 60;
}
.toast{
  background: var(--ink);
  color: #fff;
  border-radius: 999px;
  padding: 12px 20px;
  font-weight: 700;
  font-size: 15px;
  box-shadow: var(--shadow-l);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  opacity: 0;
  transform: translateY(10px);
  transition: opacity .25s ease, transform .25s ease;
  max-width: 90vw;
  text-align: center;
}
.toast.show{ opacity: 1; transform: translateY(0); }
.toast[data-tone="warn"]{ background: var(--tomato); }

/* ---------- settings drawer ---------- */
.drawer-back{
  position: fixed; inset: 0;
  background: rgba(43,42,38,.45);
  opacity: 0; pointer-events: none;
  transition: opacity .25s ease;
  z-index: 70;
}
.drawer{
  position: fixed;
  right: 0; top: 0; bottom: 0;
  width: min(420px, 92vw);
  background: var(--paper);
  z-index: 80;
  transform: translateX(100%);
  transition: transform .3s cubic-bezier(.22,.61,.36,1);
  display: flex;
  flex-direction: column;
  box-shadow: -10px 0 40px rgba(0,0,0,.12);
}
.drawer.open{ transform: translateX(0); }
.drawer-back.show{ opacity: 1; pointer-events: auto; }
.drawer-head{
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 20px;
  border-bottom: 1px solid var(--line);
}
.drawer-head h2{ margin: 0; font-size: 22px; font-weight: 800; }
.drawer-body{ padding: 18px 20px; overflow-y: auto; }
.field{ margin-bottom: 16px; }
.field label{
  display: block;
  font-size: 13px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-soft);
  margin-bottom: 6px;
}
.field select, .field input[type="text"]{
  width: 100%;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid var(--line);
  font: inherit;
  background: var(--cream);
  min-height: 48px;
}
.toggle-row{
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.toggle-row:first-of-type{ border-top: none; }
.toggle{
  width: 52px; height: 30px;
  background: var(--line);
  border: none;
  border-radius: 999px;
  position: relative;
  transition: background .2s ease;
}
.toggle::after{
  content: "";
  position: absolute;
  top: 3px; left: 3px;
  width: 24px; height: 24px;
  background: #fff;
  border-radius: 999px;
  box-shadow: 0 2px 6px rgba(0,0,0,.18);
  transition: left .2s ease;
}
.toggle[aria-pressed="true"]{ background: var(--moss); }
.toggle[aria-pressed="true"]::after{ left: 25px; }

/* ---------- responsive nudges ---------- */
@media (max-width: 520px){
  .header{ flex-wrap: wrap; }
  .header-right{ order: 3; width: 100%; justify-content: space-between; }
  .brand-name{ font-size: 24px; }
  .estop{ flex: 1; justify-content: center; }
  .quick{ min-height: 110px; }
  .quick .q-label{ font-size: 16px; }
  .voice-panel{ padding: 16px; }
  .panel{ padding: 16px; }
}

/* respect reduced motion */
@media (prefers-reduced-motion: reduce){
  *, *::before, *::after{
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }
}
</style>
</head>
<body>

<!-- ============ Power ON gate ============ -->
<div class="gate" id="gate" role="dialog" aria-modal="true" aria-labelledby="gate-title">
  <div class="gate-card">
    <div class="gate-mark" aria-hidden="true">
      <!-- sprout -->
      <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 20V10"/>
        <path d="M12 13c-3-.3-5-2.2-5-5 2.5.3 5 1.9 5 5z"/>
        <path d="M12 11c3-.3 5-2.2 5-5-2.5.3-5 1.9-5 5z"/>
        <path d="M7 20h10"/>
      </svg>
    </div>
    <div>
      <h1 id="gate-title">Good morning, gardener.</h1>
      <p>Press the button when you're ready and GrowMate will wake up your FarmBot.</p>
    </div>
    <button class="power-btn" id="powerBtn" aria-label="Power on the FarmBot">
      <span class="pwr-icon" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2v10"/><path d="M5.5 6.5a9 9 0 1 0 13 0"/>
        </svg>
      </span>
      <span id="powerBtnText">Power on the garden</span>
    </button>
    <div class="gate-foot">Takes about 5 seconds · the robot will move to its home position</div>
  </div>
</div>

<!-- ============ App ============ -->
<main class="app" id="app" aria-hidden="true">

  <!-- Header -->
  <header class="header">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 20V10"/>
          <path d="M12 13c-3-.3-5-2.2-5-5 2.5.3 5 1.9 5 5z"/>
          <path d="M12 11c3-.3 5-2.2 5-5-2.5.3-5 1.9-5 5z"/>
        </svg>
      </div>
      <div>
        <div class="brand-name">GrowMate</div>
        <div class="brand-sub">Your garden companion</div>
      </div>
    </div>
    <div class="header-right">
      <div class="conn" id="conn" data-state="online" aria-live="polite">
        <span class="dot" aria-hidden="true"></span>
        <span id="connText">Connected to your garden</span>
      </div>
      <button class="icon-btn" id="settingsBtn" aria-label="Open settings">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>
        </svg>
      </button>
      <button class="estop danger" id="estopBtn" aria-label="Emergency stop — halt all motion immediately">
        <span class="estop-dot" aria-hidden="true"></span>
        STOP
      </button>
    </div>
  </header>

  <!-- Garden state strip -->
  <section class="state-strip" aria-label="Current garden state">
    <div class="state-cell">
      <div class="state-label">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 22s8-7 8-13a8 8 0 1 0-16 0c0 6 8 13 8 13z"/><circle cx="12" cy="9" r="2.5"/>
        </svg>
        Robot position
      </div>
      <div class="state-value" id="posValue">x 0 · y 0 · z 0 mm</div>
    </div>
    <div class="state-cell">
      <div class="state-label">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
        Last action
      </div>
      <div class="state-value" id="lastActionValue">Resting at home</div>
    </div>
    <div class="state-cell">
      <div class="state-label">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z"/>
        </svg>
        GrowMate said
      </div>
      <div class="state-value quote" id="lastResponseValue">Ready when you are.</div>
    </div>
  </section>

  <!-- Two-column main -->
  <section class="main">

    <!-- Left: Voice panel -->
    <div class="panel voice-panel" aria-labelledby="voice-title">
      <div class="panel-head">
        <h2 class="panel-title" id="voice-title">Talk to your garden</h2>
        <button class="panel-action" id="toggleTextInput" aria-label="Type a command instead of speaking">Type instead ›</button>
      </div>

      <div class="prompt-block">
        <div class="hint">
          Try saying <b>“water the tomatoes”</b> or <b>“how are they looking?”</b>
        </div>
        <div class="example-chips" id="chipRow" aria-label="Suggested commands">
          <button class="chip" data-cmd="Water the tomatoes">Water the tomatoes</button>
          <button class="chip" data-cmd="Water everything">Water everything</button>
          <button class="chip" data-cmd="How are the tomatoes looking?">How are the tomatoes looking?</button>
          <button class="chip" data-cmd="Go home">Go home</button>
          <button class="chip" data-cmd="When should I plant basil?">When should I plant basil?</button>
        </div>
      </div>

      <div class="chat-feed" id="chatFeed" aria-live="polite" aria-label="Recent conversation"></div>

      <div class="voice-stage">
        <div class="mic-wrap" id="micWrap" data-state="idle">
          <button class="mic" id="micBtn" data-state="idle"
                  aria-label="Tap and hold to talk to GrowMate"
                  aria-pressed="false">
            <svg id="micIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="9" y="2" width="6" height="13" rx="3"/>
              <path d="M5 11a7 7 0 0 0 14 0"/>
              <path d="M12 18v4"/><path d="M9 22h6"/>
            </svg>
          </button>
          <span class="ring r1" aria-hidden="true"></span>
          <span class="ring r2" aria-hidden="true"></span>
          <span class="ring r3" aria-hidden="true"></span>
          <div class="mic-label" id="micLabel">Tap to talk</div>
        </div>
      </div>

      <div class="text-input-row" id="textRow" role="group" aria-label="Type a command">
        <input id="textInput" type="text" placeholder="Type what you'd say to GrowMate…" aria-label="Type a command"/>
        <button class="send-btn" id="sendTextBtn" aria-label="Send command">Send</button>
      </div>
    </div>

    <!-- Right column -->
    <div class="right-col">

      <!-- Garden map -->
      <div class="panel map-panel" aria-labelledby="map-title">
        <div class="map-head">
          <h2 class="panel-title" id="map-title" style="margin:0">Your garden</h2>
          <span class="map-meta" id="mapMeta">5.7 m × 2.7 m · 42 plants</span>
        </div>
        <div class="map-wrap" id="mapWrap">
          <svg class="map-svg" id="mapSvg" viewBox="0 0 2700 5700" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Top-down map of the garden bed showing all plants and the robot's current position">
            <!-- gantry cross-beam (spans short axis, slides along long axis) -->
            <line class="gantry-rail" id="gantryRail" x1="0" y1="0" x2="2700" y2="0"/>
            <!-- tool head pulse -->
            <circle class="gantry-pulse" id="gantryPulse" cx="0" cy="0" r="60"/>
            <!-- tool head -->
            <circle class="gantry-head" id="gantryHead" cx="0" cy="0" r="50"/>
            <!-- home marker -->
            <circle class="home-marker" cx="0" cy="0" r="100"/>
            <g id="plantsLayer"></g>
          </svg>
          <div class="map-tooltip" id="mapTooltip"></div>
        </div>
        <div class="map-legend" aria-hidden="true">
          <span class="lg"><span class="sw" style="background:#c1392b"></span>Tomato</span>
          <span class="lg"><span class="sw" style="background:#88b04b"></span>Lettuce</span>
          <span class="lg"><span class="sw" style="background:#4a7c59"></span>Scallion</span>
          <span class="lg"><span class="sw" style="background:#e08e3a"></span>Pepper</span>
          <span class="lg"><span class="sw" style="background:#f0b323"></span>Marigold</span>
          <span class="lg"><span class="sw" style="background:#355a40;box-shadow:0 0 0 2px #fff"></span>Robot</span>
        </div>
      </div>

      <!-- Quick actions / Manual move (swappable) -->
      <div class="panel" id="qaPanel" aria-labelledby="qa-title" data-view="quick">
        <div class="panel-head">
          <h2 class="panel-title" id="qa-title">
            <span class="qa-title-quick">Quick actions</span>
            <span class="qa-title-manual" hidden>Manual move</span>
          </h2>
          <button class="panel-action qa-swap" id="qaSwapBtn" aria-label="Switch to manual move controls">
            <span class="qa-swap-to-manual">Manual move ›</span>
            <span class="qa-swap-to-quick" hidden>‹ Quick actions</span>
          </button>
        </div>

        <!-- Quick view -->
        <div class="qa-view qa-view-quick">
          <div class="quick-grid">
            <button class="quick" data-tone="clay" data-action="home" aria-label="Send the robot back to its home position">
              <div class="q-icon" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>
                </svg>
              </div>
              <div>
                <div class="q-label">Go home</div>
                <div class="q-sub">Return to start</div>
              </div>
            </button>
            <button class="quick" data-tone="warn" data-action="lights" aria-label="Toggle the grow lights on or off">
              <div class="q-icon" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M9 18h6"/><path d="M10 22h4"/>
                  <path d="M12 2a7 7 0 0 0-4 12.7c.6.4 1 1 1 1.7V18h6v-1.6c0-.7.4-1.3 1-1.7A7 7 0 0 0 12 2z"/>
                </svg>
              </div>
              <div>
                <div class="q-label" id="lightsLabel">Lights on</div>
                <div class="q-sub" id="lightsSub">Currently off</div>
              </div>
            </button>
          </div>
        </div>

        <!-- Manual view -->
        <div class="qa-view qa-view-manual" hidden>
          <div class="step-row" role="radiogroup" aria-label="Step size for each move">
            <span class="step-label">Step:</span>
            <button class="step-btn" data-step="10"  role="radio" aria-checked="false">10 mm</button>
            <button class="step-btn" data-step="50"  role="radio" aria-checked="false">50 mm</button>
            <button class="step-btn" data-step="100" role="radio" aria-checked="true">100 mm</button>
            <button class="step-btn" data-step="500" role="radio" aria-checked="false">500 mm</button>
          </div>
          <div class="jog-wrap">
            <div class="dpad" role="group" aria-label="Move robot along the bed">
              <button class="jog-btn up" data-jog="y+" aria-label="Move forward (y plus)">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
              </button>
              <button class="jog-btn left" data-jog="x-" aria-label="Move left (x minus)">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M12 5l-7 7 7 7"/></svg>
              </button>
              <div class="jog-btn center" aria-hidden="true">X / Y</div>
              <button class="jog-btn right" data-jog="x+" aria-label="Move right (x plus)">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
              </button>
              <button class="jog-btn down" data-jog="y-" aria-label="Move backward (y minus)">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12l7 7 7-7"/></svg>
              </button>
            </div>
            <div class="z-col" role="group" aria-label="Move robot up or down">
              <button class="z-btn" data-jog="z+" aria-label="Move up (z plus)">+</button>
              <div class="z-label">Z axis</div>
              <button class="z-btn" data-jog="z-" aria-label="Move down (z minus)">−</button>
            </div>
          </div>
        </div>
      </div>

    </div>
  </section>

  <!-- History -->
  <section class="panel" aria-labelledby="hist-title">
    <div class="panel-head">
      <h2 class="panel-title" id="hist-title">Recent activity</h2>
      <button class="panel-action" id="clearHistBtn" aria-label="Clear all command history">Clear history</button>
    </div>
    <ul class="history-list" id="histList" aria-live="polite"></ul>
    <div class="hist-empty" id="histEmpty" hidden>Nothing yet — try saying <b>“water the tomatoes”</b>.</div>
  </section>

  <footer class="footer">
    <div class="legal">GrowMate · FarmBot Genesis XL · 5.7 m × 2.7 m bed</div>
    <div class="foot-buttons">
      <button class="ghost" id="resetBtn" aria-label="Reset the robot system">Reset system</button>
      <button class="ghost" id="powerOffBtn" aria-label="Power off the FarmBot">Power off</button>
    </div>
  </footer>

</main>

<!-- ============ Settings drawer ============ -->
<div class="drawer-back" id="drawerBack" aria-hidden="true"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" aria-hidden="true">
  <div class="drawer-head">
    <h2 id="drawer-title">Settings</h2>
    <button class="icon-btn" id="drawerCloseBtn" aria-label="Close settings">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
    </button>
  </div>
  <div class="drawer-body">
    <div class="field">
      <label for="sttSel">Speech-to-text engine</label>
      <select id="sttSel">
        <option>Whisper (local)</option>
        <option>Whisper.cpp</option>
        <option>Browser (fallback)</option>
      </select>
    </div>
    <div class="field">
      <label for="ttsSel">Voice (text-to-speech)</label>
      <select id="ttsSel">
        <option>Piper · Joanna (warm)</option>
        <option>Piper · Liam (calm)</option>
        <option>System default</option>
      </select>
    </div>
    <div class="field">
      <label for="modelSel">Intent model</label>
      <select id="modelSel">
        <option>VoiceBT · flat-intent (default)</option>
        <option>VoiceBT · debug trace</option>
      </select>
    </div>
    <div class="toggle-row">
      <div>
        <div style="font-weight:800">Confirmation chime</div>
        <div style="font-size:14px;color:var(--ink-soft);font-weight:600">Subtle sound after each action</div>
      </div>
      <button class="toggle" id="chimeToggle" aria-pressed="true" aria-label="Toggle confirmation chime"></button>
    </div>
    <div class="toggle-row">
      <div>
        <div style="font-weight:800">Spoken feedback</div>
        <div style="font-size:14px;color:var(--ink-soft);font-weight:600">GrowMate speaks responses aloud</div>
      </div>
      <button class="toggle" id="ttsToggle" aria-pressed="true" aria-label="Toggle spoken feedback"></button>
    </div>
    <div class="toggle-row">
      <div>
        <div style="font-weight:800">Large text</div>
        <div style="font-size:14px;color:var(--ink-soft);font-weight:600">Boost size for easier reading</div>
      </div>
      <button class="toggle" id="largeToggle" aria-pressed="false" aria-label="Toggle large text"></button>
    </div>
  </div>
</aside>

<!-- ============ Toast ============ -->
<div class="toast-wrap" aria-live="polite" aria-atomic="true">
  <div class="toast" id="toast"></div>
</div>

<script>
/* ========================================================================
   GrowMate front-end
   Talks to FastAPI endpoints; falls back to a mock if endpoints are missing
   so this file works standalone as a prototype.
   ====================================================================== */

(() => {
  // ----- state -----
  const state = {
    powered: false,
    online: true,
    pos: { x: 0, y: 0, z: 0 },
    lastAction: 'Resting at home',
    lastResponse: 'Ready when you are.',
    lightsOn: false,
    micState: 'idle',          // idle | recording | processing
    history: [],
    chimeEnabled: true,
    jogStep: 100,             // mm — selected step size for manual moves
  };

  // ----- dom refs -----
  const $ = (id) => document.getElementById(id);
  const gate         = $('gate');
  const powerBtn     = $('powerBtn');
  const powerBtnText = $('powerBtnText');
  const app          = $('app');
  const conn         = $('conn');
  const connText     = $('connText');
  const settingsBtn  = $('settingsBtn');
  const drawer       = $('drawer');
  const drawerBack   = $('drawerBack');
  const drawerClose  = $('drawerCloseBtn');
  const estopBtn     = $('estopBtn');
  const posValue     = $('posValue');
  const lastActionV  = $('lastActionValue');
  const lastResponseV= $('lastResponseValue');
  const micBtn       = $('micBtn');
  const micWrap      = $('micWrap');
  const micLabel     = $('micLabel');
  const toggleText   = $('toggleTextInput');
  const textRow      = $('textRow');
  const textInput    = $('textInput');
  const sendTextBtn  = $('sendTextBtn');
  const chipRow      = $('chipRow');
  const histList     = $('histList');
  const histEmpty    = $('histEmpty');
  const clearHistBtn = $('clearHistBtn');
  const resetBtn     = $('resetBtn');
  const powerOffBtn  = $('powerOffBtn');
  const lightsLabel  = $('lightsLabel');
  const lightsSub    = $('lightsSub');
  const toast        = $('toast');
  const chatFeed     = $('chatFeed');
  const chimeToggle  = $('chimeToggle');
  const ttsToggle    = $('ttsToggle');
  const largeToggle  = $('largeToggle');

  // ----- helpers -----
  const fmtMM = (n) => Math.round(n) + '';
  const formatRel = (ts) => {
    const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (s < 5)  return 'just now';
    if (s < 60) return s + ' s ago';
    const m = Math.round(s / 60);
    if (m < 60) return m + ' min ago';
    const h = Math.round(m / 60);
    return h + ' h ago';
  };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  let toastTimer;
  const showToast = (msg, tone='ok') => {
    toast.textContent = msg;
    toast.dataset.tone = tone;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
  };

  // ----- API wrapper (real fetch with mock fallback) -----
  // Endpoints expected: /api/voice  /api/jog  /api/action  /api/estop
  // /api/reset  /api/status  /api/history  /api/history/clear  /api/farmbot/power
  async function api(path, opts = {}) {
    try {
      const r = await fetch(path, {
        method: opts.method || 'GET',
        headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
        body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
      });
      if (!r.ok) throw new Error('http ' + r.status);
      const ct = r.headers.get('content-type') || '';
      return ct.includes('application/json') ? r.json() : r.text();
    } catch (e) {
      // graceful mock so the prototype still functions if backend is absent
      return mockAPI(path, opts);
    }
  }

  // ----- Mock backend (for the standalone prototype demo only) -----
  function mockAPI(path, opts) {
    const body = opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData) ? opts.body : {};
    if (path === '/api/farmbot/power') return { ok: true, on: body.on };
    if (path === '/api/status')        return { x: state.pos.x, y: state.pos.y, z: state.pos.z, connected: state.online };
    if (path === '/api/jog') {
      const step = 100;
      const dir = body.dir || '';
      if (dir === 'x+') state.pos.x += step;
      if (dir === 'x-') state.pos.x -= step;
      if (dir === 'y+') state.pos.y += step;
      if (dir === 'y-') state.pos.y -= step;
      if (dir === 'z+') state.pos.z += step;
      if (dir === 'z-') state.pos.z -= step;
      return { ok: true, x: state.pos.x, y: state.pos.y, z: state.pos.z };
    }
    if (path === '/api/action') {
      const action = body.action || '';
      const map = {
        water_all: { say: 'Watering all the plants now.', did: 'Watering 42 plants' },
        home:      { say: 'Heading home.', did: 'Returning to home position' },
        photo:     { say: 'Smile! Taking a photo of the garden.', did: 'Captured a garden photo' },
        lights:    {
          say: state.lightsOn ? 'Turning the lights off.' : 'Turning the lights on.',
          did: state.lightsOn ? 'Grow lights off' : 'Grow lights on'
        },
      };
      const r = map[action] || { say: 'Done.', did: action };
      if (action === 'home') state.pos = { x: 0, y: 0, z: 0 };
      return { ok: true, ...r, lightsOn: action === 'lights' ? !state.lightsOn : state.lightsOn };
    }
    if (path === '/api/estop')   return { ok: true };
    if (path === '/api/reset')   return { ok: true };
    if (path === '/api/history') return { history: state.history };
    if (path === '/api/history/clear') return { ok: true };
    if (path === '/api/voice') {
      const said = (body && body.text) || 'Water the tomatoes';
      const lc = said.toLowerCase();
      let did = 'Spoke to GrowMate', say = "I'm not sure how to do that yet.";
      if (/stop|halt|emergency/.test(lc))       { did = 'Emergency stop'; say = 'Stopped.'; }
      else if (/water.*every|all|whole/.test(lc)){ did = 'Watering 42 plants'; say = 'Watering everything.'; state.pos = {x:2200,y:1100,z:0}; }
      else if (/water.*tomato/.test(lc))         { did = 'Watered the tomatoes'; say = 'Watering the tomatoes now.'; state.pos = {x:1240,y:540,z:0}; }
      else if (/water.*lettuce/.test(lc))        { did = 'Watered the lettuce'; say = 'Watering the lettuce.'; state.pos = {x:980,y:820,z:0}; }
      else if (/home/.test(lc))                  { did = 'Returned home'; say = 'Heading home.'; state.pos = {x:0,y:0,z:0}; }
      else if (/photo|look|see|how.*tomato/.test(lc)) { did = 'Photographed the tomatoes'; say = 'Your tomatoes look healthy and ripe.'; state.pos = {x:1240,y:540,z:120}; }
      else if (/basil/.test(lc))                 { did = 'Answered a question'; say = 'Basil likes warm soil — plant after the last frost.'; }
      else if (/light/.test(lc))                 { did = state.lightsOn ? 'Grow lights off' : 'Grow lights on'; say = state.lightsOn ? 'Lights off.' : 'Lights on.'; }
      else if (/move|forward|back|left|right/.test(lc)){ did = 'Jogged the robot'; say = 'Moving.'; }
      return { ok: true, said, did, say, success: true };
    }
    return { ok: false };
  }

  // ----- UI updates -----
  function addChatBubble(kind, label, text) {
    if (!text) return;
    const div = document.createElement('div');
    div.className = 'bubble ' + (kind === 'bot' ? 'bot' : 'you');
    div.innerHTML = `<span class="b-label">${escapeHTML(label)}</span>${escapeHTML(text)}`;
    chatFeed.appendChild(div);
    // age older bubbles
    const bubbles = [...chatFeed.children];
    bubbles.forEach((b, i) => {
      const fromEnd = bubbles.length - 1 - i;
      b.classList.remove('fade-old', 'fade-older');
      if (fromEnd === 2) b.classList.add('fade-old');
      if (fromEnd >= 3) b.classList.add('fade-older');
    });
    // keep last 6 in DOM
    while (chatFeed.children.length > 6) chatFeed.removeChild(chatFeed.firstChild);
  }

  function addTypingBubble() {
    const div = document.createElement('div');
    div.className = 'bubble bot is-typing';
    div.id = 'typingBubble';
    div.innerHTML = `<span class="b-label">GrowMate</span>`;
    chatFeed.appendChild(div);
    while (chatFeed.children.length > 6) chatFeed.removeChild(chatFeed.firstChild);
  }
  function removeTypingBubble() {
    const t = document.getElementById('typingBubble');
    if (t) t.remove();
  }

  function renderState() {
    posValue.textContent = `x ${fmtMM(state.pos.x)} · y ${fmtMM(state.pos.y)} · z ${fmtMM(state.pos.z)} mm`;
    lastActionV.textContent = state.lastAction;
    lastResponseV.textContent = state.lastResponse;
    lightsLabel.textContent = state.lightsOn ? 'Lights off' : 'Lights on';
    lightsSub.textContent   = state.lightsOn ? 'Currently on'  : 'Currently off';
    updateGantry();
  }

  // ----- Garden map -----
  // FarmBot Genesis XL bed: 5700 mm × 2700 mm. 42 plants.
  const PLANT_COLORS = {
    // Edibles
    tomato:    '#c1392b',
    lettuce:   '#88b04b',
    scallion:  '#4a7c59',
    pepper:    '#e08e3a',
    // Herbs
    basil:     '#3f7d44',
    spearmint: '#5fa66b',
    // Flowers
    marigold:  '#f0b323',
    lily:      '#f4e2d8',
    geranium:  '#d34a5e',
    cardinal:  '#b71c1c',
    dianthus:  '#e882a4',
    euonymus:  '#6b8e23',
    petunia:   '#8e4585',
    begonia:   '#e85d4a',
  };
  // Generate 42 plant positions. Padding from edges, 5 rows x ~varying cols.
  function buildPlants(){
    const plants = [];
    const xs = [600, 1300, 2000, 2700, 3400, 4100, 4800, 5400]; // 8 cols
    // 6 tomatoes (back row, every other col)
    [0,2,4,6].forEach((i,n)=> plants.push({type:'tomato', x:xs[i+1]||xs[i], y:500, name:`Tomato ${String.fromCharCode(65+n)}`}));
    plants.push({type:'tomato', x:800,  y:500, name:'Tomato E'});
    plants.push({type:'tomato', x:5200, y:500, name:'Tomato F'});
    // 8 peppers (row 2)
    xs.forEach((x,i)=> plants.push({type:'pepper', x, y:900, name:`Pepper ${i+1}`}));
    // 8 lettuce (row 3)
    xs.forEach((x,i)=> plants.push({type:'lettuce', x, y:1350, name:`Lettuce ${i+1}`}));
    // 8 scallions (row 4)
    xs.forEach((x,i)=> plants.push({type:'scallion', x, y:1800, name:`Scallion ${i+1}`}));
    // 10 marigolds (border row at front)
    const mx = [500, 1100, 1700, 2300, 2900, 3500, 4100, 4700, 5200, 5500];
    mx.forEach((x,i)=> plants.push({type:'marigold', x, y:2300, name:`Marigold ${i+1}`}));
    return plants.slice(0, 42);
  }
  // Default to the generated 42-plant grid; replaced at runtime by
  // fetchPlantsFromBackend() if /api/plants returns real data.
  let PLANTS = buildPlants();

  async function fetchPlantsFromBackend(){
    try {
      const r = await fetch('/api/plants');
      if (!r.ok) return;
      const data = await r.json();
      if (Array.isArray(data?.plants) && data.plants.length > 0) {
        PLANTS = data.plants;
        if (data.workspace) {
          // Resize the SVG viewBox to match the real bed (y_len wide, x_len tall — portrait)
          const ws = data.workspace;
          const svg = document.getElementById('mapSvg');
          if (svg && ws.x_len && ws.y_len) {
            svg.setAttribute('viewBox', `0 0 ${ws.y_len} ${ws.x_len}`);
          }
        }
        renderPlants();
      }
    } catch (_) { /* keep mock data */ }
  }

  const mapSvg     = $('mapSvg');
  const plantsLayer= $('plantsLayer');
  const gantryRail = $('gantryRail');
  const gantryHead = $('gantryHead');
  const gantryPulse= $('gantryPulse');
  const mapWrap    = $('mapWrap');
  const mapTooltip = $('mapTooltip');
  const mapMeta    = $('mapMeta');

  function renderPlants(){
    plantsLayer.innerHTML = '';
    const svgNS = 'http://www.w3.org/2000/svg';
    PLANTS.forEach((p, i) => {
      const g = document.createElementNS(svgNS, 'g');
      g.setAttribute('class', 'plant');
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      g.setAttribute('aria-label', `${p.name}. Tap to water.`);
      g.dataset.idx = i;
      // portrait: real X (long axis) -> SVG y, real Y (short axis) -> SVG x
      const sx = p.y;
      const sy = p.x;
      // outer ring
      const ring = document.createElementNS(svgNS, 'circle');
      ring.setAttribute('cx', sx);
      ring.setAttribute('cy', sy);
      ring.setAttribute('r', 70);
      ring.setAttribute('fill', '#fff');
      ring.setAttribute('opacity', '.65');
      // colored core
      const dot = document.createElementNS(svgNS, 'circle');
      dot.setAttribute('cx', sx);
      dot.setAttribute('cy', sy);
      dot.setAttribute('r', 55);
      dot.setAttribute('fill', PLANT_COLORS[p.type]);
      g.appendChild(ring);
      g.appendChild(dot);
      plantsLayer.appendChild(g);
    });
    mapMeta.textContent = `5.7 m × 2.7 m · ${PLANTS.length} plants`;
  }

  // map robot x/y (mm) into portrait SVG coordinates (clamped)
  function updateGantry(){
    const rx = Math.max(0, Math.min(5700, state.pos.x || 0)); // long axis
    const ry = Math.max(0, Math.min(2700, state.pos.y || 0)); // short axis
    // gantry cross-beam: horizontal line at vertical position rx (spans full width)
    gantryRail.setAttribute('x1', 0);
    gantryRail.setAttribute('x2', 2700);
    gantryRail.setAttribute('y1', rx);
    gantryRail.setAttribute('y2', rx);
    gantryHead.setAttribute('cx', ry);
    gantryHead.setAttribute('cy', rx);
    gantryPulse.setAttribute('cx', ry);
    gantryPulse.setAttribute('cy', rx);
  }

  function plantAtEvent(e){
    const g = e.target.closest('.plant');
    return g ? PLANTS[+g.dataset.idx] : null;
  }

  plantsLayer.addEventListener('click', (e) => {
    const p = plantAtEvent(e);
    if (!p) return;
    hideTooltip();
    sendCommand(`Water the ${p.type}s`);
  });
  plantsLayer.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const p = plantAtEvent(e);
    if (!p) return;
    e.preventDefault();
    sendCommand(`Water the ${p.type}s`);
  });

  // tooltip on hover
  function showTooltipFor(g){
    const p = PLANTS[+g.dataset.idx];
    if (!p) return;
    const rect = mapWrap.getBoundingClientRect();
    const svgRect = mapSvg.getBoundingClientRect();
    const ratioX = svgRect.width / 2700;
    const ratioY = svgRect.height / 5700;
    const offX = svgRect.left - rect.left;
    const offY = svgRect.top  - rect.top;
    // portrait mapping: plant.y -> svg x, plant.x -> svg y
    const left = offX + p.y * ratioX;
    const top  = offY + p.x * ratioY;
    mapTooltip.style.left = left + 'px';
    mapTooltip.style.top  = top  + 'px';
    mapTooltip.textContent = `${p.name} — tap to water`;
    mapTooltip.classList.add('show');
  }
  function hideTooltip(){ mapTooltip.classList.remove('show'); }
  plantsLayer.addEventListener('mouseover', (e) => {
    const g = e.target.closest('.plant'); if (g) showTooltipFor(g);
  });
  plantsLayer.addEventListener('mouseout', (e) => {
    if (!e.relatedTarget || !e.relatedTarget.closest?.('.plant')) hideTooltip();
  });
  plantsLayer.addEventListener('focusin', (e) => {
    const g = e.target.closest('.plant'); if (g) showTooltipFor(g);
  });
  plantsLayer.addEventListener('focusout', hideTooltip);

  // ----- Quick actions / Manual move swap -----
  const qaPanel = $('qaPanel');
  const qaSwapBtn = $('qaSwapBtn');
  function setQAView(view) {
    qaPanel.dataset.view = view;
    const quickEls = qaPanel.querySelectorAll('.qa-view-quick, .qa-title-quick, .qa-swap-to-manual');
    const manualEls= qaPanel.querySelectorAll('.qa-view-manual, .qa-title-manual, .qa-swap-to-quick');
    quickEls.forEach(e => e.hidden = (view !== 'quick'));
    manualEls.forEach(e => e.hidden = (view !== 'manual'));
    qaSwapBtn.setAttribute('aria-label',
      view === 'quick' ? 'Switch to manual move controls' : 'Switch back to quick actions');
  }
  qaSwapBtn.addEventListener('click', () => {
    setQAView(qaPanel.dataset.view === 'quick' ? 'manual' : 'quick');
  });

  function renderHistory() {
    histList.innerHTML = '';
    if (state.history.length === 0) {
      histEmpty.hidden = false;
      return;
    }
    histEmpty.hidden = true;
    state.history.slice(0, 10).forEach((h, idx) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.className = 'hist';
      btn.dataset.status = h.success ? 'ok' : 'fail';
      btn.setAttribute('aria-label',
        `${h.success ? 'Succeeded' : 'Failed'}: ${h.said}. ${h.did}. ${formatRel(h.ts)}. Tap to repeat.`);
      btn.innerHTML = `
        <span class="hist-icon" aria-hidden="true">
          ${h.success
            ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`
            : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`}
        </span>
        <span class="hist-body">
          <span class="hist-said">${escapeHTML(h.said)}</span>
          <span class="hist-did">${escapeHTML(h.did)}</span>
        </span>
        <span class="hist-time">${formatRel(h.ts)}</span>
      `;
      btn.addEventListener('click', () => sendCommand(h.said));
      li.appendChild(btn);
      histList.appendChild(li);
    });
  }

  function escapeHTML(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

  function pushHistory(entry) {
    state.history.unshift({ ts: Date.now(), success: true, ...entry });
    if (state.history.length > 20) state.history.length = 20;
    renderHistory();
  }

  // ----- Power gate -----
  powerBtn.addEventListener('click', async () => {
    powerBtn.dataset.loading = '1';
    powerBtnText.textContent = 'Waking up…';
    await api('/api/farmbot/power', { method: 'POST', body: { on: true } });
    await sleep(900);
    state.powered = true;
    gate.classList.add('hidden');
    app.setAttribute('aria-hidden', 'false');
    showToast('Good morning — your garden is awake');
    pushHistory({ said: 'Power on', did: 'FarmBot is awake and homed', success: true });
    state.lastAction = 'Robot is awake and homed';
    state.lastResponse = "Good morning. I'm ready to help.";
    renderState();
    addChatBubble('you', 'Last action', state.lastAction);
    addChatBubble('bot', 'GrowMate', state.lastResponse);
  });

  powerOffBtn.addEventListener('click', async () => {
    await api('/api/farmbot/power', { method: 'POST', body: { on: false } });
    state.powered = false;
    state.history = [];
    state.pos = {x:0,y:0,z:0};
    state.lastAction = 'Resting at home';
    state.lastResponse = 'Goodnight.';
    renderState();
    renderHistory();
    powerBtn.dataset.loading = '0';
    powerBtnText.textContent = 'Power on the garden';
    gate.classList.remove('hidden');
    app.setAttribute('aria-hidden', 'true');
  });

  // ----- Settings drawer -----
  function openDrawer() {
    drawer.classList.add('open');
    drawerBack.classList.add('show');
    drawer.setAttribute('aria-hidden','false');
  }
  function closeDrawer() {
    drawer.classList.remove('open');
    drawerBack.classList.remove('show');
    drawer.setAttribute('aria-hidden','true');
  }
  settingsBtn.addEventListener('click', openDrawer);
  drawerClose.addEventListener('click', closeDrawer);
  drawerBack.addEventListener('click', closeDrawer);

  function bindToggle(el, onChange) {
    el.addEventListener('click', () => {
      const next = el.getAttribute('aria-pressed') !== 'true';
      el.setAttribute('aria-pressed', String(next));
      onChange?.(next);
    });
  }
  bindToggle(chimeToggle, (v) => { state.chimeEnabled = v; });
  bindToggle(ttsToggle);
  bindToggle(largeToggle, (v) => {
    document.documentElement.style.fontSize = v ? '20px' : '';
  });

  // ----- Emergency stop -----
  estopBtn.addEventListener('click', async () => {
    estopBtn.style.transform = 'scale(.96)';
    setTimeout(() => estopBtn.style.transform = '', 120);
    await api('/api/estop', { method: 'POST' });
    if (state.micState !== 'idle') stopRecording(true);
    state.lastAction = 'Emergency stop — all motion halted';
    state.lastResponse = 'Stopped. You are safe.';
    renderState();
    pushHistory({ said: 'STOP', did: 'Emergency stop — all motion halted', success: true });
    showToast('Emergency stop — robot halted', 'warn');
  });

  // ----- Voice button (real audio capture -> WAV -> POST /api/voice) -----
  function setMic(stateName) {
    state.micState = stateName;
    micBtn.dataset.state = stateName;
    micWrap.dataset.state = stateName;
    micBtn.setAttribute('aria-pressed', String(stateName === 'recording'));
    if (stateName === 'idle')       micLabel.textContent = 'Tap to talk';
    if (stateName === 'recording')  micLabel.textContent = 'Listening… tap to stop';
    if (stateName === 'processing') micLabel.textContent = 'Thinking…';
  }

  const TARGET_SR = 16000;
  let recState = { stream: null, audioCtx: null, source: null, processor: null,
                   chunks: [], startedAt: 0, recording: false };

  async function startRecording() {
    console.log('[mic] startRecording: requesting mic permission');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.error('[mic] getUserMedia not supported in this browser');
      showToast('Mic not supported in this browser', 'warn');
      textRow.classList.add('show');
      textInput.focus();
      return;
    }
    try {
      recState.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      console.log('[mic] permission granted');
    } catch (e) {
      console.error('[mic] permission denied:', e);
      showToast('Microphone unavailable — try typing instead', 'warn');
      textRow.classList.add('show');
      textInput.focus();
      return;
    }
    try {
      recState.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SR });
      recState.source = recState.audioCtx.createMediaStreamSource(recState.stream);
      recState.processor = recState.audioCtx.createScriptProcessor(4096, 1, 1);
      recState.chunks = [];
      recState.processor.onaudioprocess = (e) => {
        recState.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      recState.source.connect(recState.processor);
      recState.processor.connect(recState.audioCtx.destination);
      recState.recording = true;
      recState.startedAt = Date.now();
      setMic('recording');
      console.log('[mic] recording started');
    } catch (e) {
      console.error('[mic] audio context setup failed:', e);
      showToast('Audio setup failed: ' + e.message, 'warn');
      try { recState.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
      setMic('idle');
    }
  }

  async function stopRecording(cancelled) {
    if (!recState.recording) { setMic('idle'); return; }
    recState.recording = false;
    const tooShort = Date.now() - recState.startedAt < 350;

    // Tear down the audio graph regardless
    try { recState.processor.disconnect(); recState.source.disconnect(); } catch (_) {}
    try { recState.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    try { await recState.audioCtx.close(); } catch (_) {}

    if (cancelled || tooShort) {
      setMic('idle');
      if (tooShort) showToast('Hold a moment longer — I didn’t catch that');
      return;
    }

    setMic('processing');

    // Merge captured Float32 chunks into one buffer
    const total = recState.chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Float32Array(total);
    let off = 0;
    for (const c of recState.chunks) { merged.set(c, off); off += c.length; }

    const wav = encodeWAV(merged, TARGET_SR);
    const form = new FormData();
    form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'rec.wav');
    form.append('stt', 'whisper');
    form.append('tts', (state.ttsEnabled !== false) ? 'kokoro' : 'none');
    form.append('enable_tts', (state.ttsEnabled !== false) ? 'true' : 'false');

    try {
      const r = await fetch('/api/voice', { method: 'POST', body: form });
      const data = await r.json();
      // Normalise the backend response into the shape finishCommand expects
      const norm = adaptVoiceResponse(data);
      // Auto-play TTS if returned
      if (data.tts_audio_b64) {
        try {
          const audio = new Audio('data:audio/wav;base64,' + data.tts_audio_b64);
          audio.play().catch(() => {});
        } catch (_) {}
      }
      finishCommand(norm);
    } catch (e) {
      showToast('Voice request failed: ' + e.message, 'warn');
      setMic('idle');
    }
  }

  // Map the FastAPI /api/voice response into {said, did, say, success}
  function adaptVoiceResponse(data) {
    if (!data) return { said: '', did: '', say: '', success: false };
    const result = data.result || {};
    const pos = result.position || {};
    const said = result.raw_transcript || '';
    const did  = pos.last_cmd || result.matched_action || 'Done';
    const say  = result.tts_spoken || data.tts_text || '';
    if (typeof pos.x === 'number') state.pos = { x: pos.x, y: pos.y, z: pos.z };
    return { said, did, say, success: !data.error };
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

  micBtn.addEventListener('click', () => {
    console.log('[mic] click; current state:', state.micState);
    if (state.micState === 'idle')       startRecording();
    else if (state.micState === 'recording') stopRecording();
  });

  // ----- Text fallback -----
  toggleText.addEventListener('click', () => {
    textRow.classList.toggle('show');
    if (textRow.classList.contains('show')) textInput.focus();
  });
  sendTextBtn.addEventListener('click', () => {
    const v = textInput.value.trim();
    if (!v) return;
    textInput.value = '';
    sendCommand(v);
  });
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendTextBtn.click();
  });

  // ----- Example chips -----
  chipRow.addEventListener('click', (e) => {
    const c = e.target.closest('.chip');
    if (!c) return;
    sendCommand(c.dataset.cmd);
  });

  async function sendCommand(text) {
    addChatBubble('you', 'You said', text);
    setMic('processing');
    addTypingBubble();
    let norm;
    try {
      const r = await fetch('/api/text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await r.json();
      if (data.tts_audio_b64) {
        try { new Audio('data:audio/wav;base64,' + data.tts_audio_b64).play().catch(() => {}); } catch (_) {}
      }
      norm = adaptVoiceResponse(data);
    } catch (e) {
      norm = await api('/api/voice', { method: 'POST', body: { text } });
    }
    finishCommand(norm, text);
  }

  async function finishCommand(res, override) {
    await sleep(600);
    removeTypingBubble();
    const said = override || res?.said || 'Voice command';
    state.lastAction = res?.did || 'Done';
    state.lastResponse = res?.say || 'Done.';
    if (typeof res?.lightsOn === 'boolean') state.lightsOn = res.lightsOn;
    if (/lights on|lights off/.test(state.lastAction.toLowerCase())) {
      state.lightsOn = state.lastAction.toLowerCase().includes('on');
    }
    if (!override) addChatBubble('you', 'You said', said);
    addChatBubble('bot', 'GrowMate', state.lastResponse);
    renderState();
    pushHistory({ said, did: state.lastAction, success: res?.success !== false });
    setMic('idle');
  }

  // ----- Quick actions -----
  document.querySelectorAll('.quick').forEach(btn => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;
      btn.style.transform = 'scale(.97)';
      setTimeout(() => btn.style.transform = '', 100);
      const res = await api('/api/action', { method: 'POST', body: { action } });
      if (action === 'lights') state.lightsOn = !state.lightsOn;
      if (action === 'home') state.pos = { x:0, y:0, z:0 };
      state.lastAction = res?.did || labelFor(action);
      state.lastResponse = res?.say || 'Done.';
      addChatBubble('you', 'Last action', state.lastAction);
      addChatBubble('bot', 'GrowMate', state.lastResponse);
      renderState();
      pushHistory({ said: labelFor(action), did: state.lastAction, success: true });
      showToast(state.lastResponse);
    });
  });
  function labelFor(a) {
    return ({
      water_all: 'Water everything',
      home: 'Go home',
      photo: 'Take a photo',
      lights: 'Toggle lights',
    })[a] || a;
  }

  // ----- Step-size selector -----
  document.querySelectorAll('.step-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.jogStep = parseInt(b.dataset.step, 10) || 100;
      document.querySelectorAll('.step-btn').forEach(other => {
        other.setAttribute('aria-checked', other === b ? 'true' : 'false');
      });
    });
  });

  // ----- Jog -----
  document.querySelectorAll('[data-jog]').forEach(b => {
    b.addEventListener('click', async () => {
      const dir = b.dataset.jog;
      const step = state.jogStep;
      const res = await api('/api/jog', { method: 'POST', body: { dir, step } });
      if (res?.x !== undefined) state.pos = { x: res.x, y: res.y, z: res.z };
      const human = ({
        'x+': 'right', 'x-': 'left', 'y+': 'forward', 'y-': 'backward',
        'z+': 'up', 'z-': 'down',
      })[dir] || dir;
      state.lastAction = `Jogged ${human} ${step} mm`;
      state.lastResponse = `Moved ${human} ${step} mm.`;
      renderState();
      pushHistory({ said: `Jog ${human} ${step} mm`, did: state.lastAction, success: true });
    });
  });

  // ----- History controls -----
  clearHistBtn.addEventListener('click', async () => {
    await api('/api/history/clear', { method: 'POST' });
    state.history = [];
    renderHistory();
    showToast('History cleared');
  });

  resetBtn.addEventListener('click', async () => {
    await api('/api/reset', { method: 'POST' });
    state.pos = { x:0, y:0, z:0 };
    state.lastAction = 'System reset';
    state.lastResponse = 'All clear.';
    renderState();
    pushHistory({ said: 'Reset system', did: 'Cleared error state', success: true });
    showToast('System reset');
  });

  // ----- Status polling (graceful offline) -----
  let offlineStreak = 0;
  async function pollStatus() {
    try {
      const r = await fetch('/api/status');
      if (!r.ok) throw new Error('bad');
      const j = await r.json();
      offlineStreak = 0;
      conn.dataset.state = 'online';
      connText.textContent = 'Connected to your garden';
      if (typeof j?.x === 'number') state.pos = { x: j.x, y: j.y, z: j.z };
      renderState();
    } catch (e) {
      offlineStreak++;
      if (offlineStreak >= 2) {
        conn.dataset.state = 'reconnect';
        connText.textContent = 'Reconnecting to your garden…';
      }
      if (offlineStreak >= 5) {
        conn.dataset.state = 'offline';
        connText.textContent = 'Offline · using local controls';
      }
    }
  }
  // Don't poll the (non-existent) backend in the standalone preview — keep "Connected" green by default.
  // In the real FastAPI deployment, the line below should be uncommented:
  // setInterval(pollStatus, 4000);

  // ----- Refresh "time ago" labels periodically -----
  setInterval(() => {
    document.querySelectorAll('.hist-time').forEach((el, i) => {
      const h = state.history[i];
      if (h) el.textContent = formatRel(h.ts);
    });
  }, 15000);

  // ----- Keyboard shortcuts -----
  document.addEventListener('keydown', (e) => {
    if (!state.powered) return;
    if (e.key === 'Escape') {
      if (drawer.classList.contains('open')) closeDrawer();
    }
    if (e.key === ' ' && (e.target === document.body)) {
      e.preventDefault();
      micBtn.click();
    }
  });

  // ----- Init -----
  renderPlants();
  renderState();
  renderHistory();
  fetchPlantsFromBackend();   // override with active_map.yaml plants if available
})();
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
    parser.add_argument("--model", default="gemma3:4b",
                        help="Ollama model tag for AICore (default: gemma3:4b)")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama server URL (default: http://localhost:11434)")
    args = parser.parse_args(argv)

    _STATE.model = args.model
    _STATE.ollama_url = args.ollama_url

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
