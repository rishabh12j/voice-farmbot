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
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .edgespeech.audio_utils import (
    SAMPLE_RATE,
    audio_info,
    audio_to_wav_bytes,
    load_wav_from_bytes,
)
from .edgespeech.command_map import COMMAND_MAP, TTS_PHRASES, get_tts_phrase, match_command
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
    whisper_model: str = "small.en"       # Day 4: bumped from tiny.en for elderly accuracy
    whisper_prompt: Optional[str] = None  # plant-name biasing, built at first STT call
    pending_confirms: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Day 5


_STATE = AppState()

_BOUNDS = {"x": (0.0, 5691.2), "y": (0.0, 2734.0), "z": (-500.0, 0.0)}
_BRINGUP_NODES = ["farmbotcontroller", "mapcontroller", "devicecmdhandler"]
_VOICE_STEP_MM = 100

# Day 5: soft-confirm layer for destructive actions.
# Actions that need confirmation when triggered by voice / text / button.
# Pattern-side action keys (from edgespeech command_map) → "water" = P_4.
_CONFIRM_PATTERN_ACTIONS = {"water"}
# AICore intent action keys (from schemas.Action) needing confirm.
_CONFIRM_AICORE_ACTIONS = {"water_all"}
# Anything in this set ALWAYS bypasses confirm — emergency is instant.
_NEVER_CONFIRM = {"estop", "reset", "emergency_stop"}

# Day 10 ("today's care" panel) and Day 11 (fast-path plant queries) both
# read from the per-plant event log + needs_attention list. After hardware
# testing on farmbotdev (Jun 2026), the memory model showed real flaws:
# P_4 watered_all rows landed even when the BT didn't actually finish, and
# fast-path date queries were getting mis-routed via the matcher into
# water_all confirms. Pausing both until the event-log model gets a proper
# "tick-and-verify" gate (see PLANS.md follow-up). Flip back to True to
# re-enable both at once.
_MEMORY_FEATURES_ENABLED = False
_PENDING_TTL_S = 10.0


# --------------------------------------------------------------------------- init
def _ensure_initialised(ros2_enabled: bool) -> None:
    with _STATE.init_lock:
        if _STATE.robot is not None:
            return
        _STATE.ros2_enabled = ros2_enabled
        log.info("Initialising — ros2=%s", ros2_enabled)
        _STATE.robot = ROS2Publisher(ros2_enabled=ros2_enabled)
        log.info("GrowMate backend ready.  Log: %s", log_path())


def _build_whisper_prompt() -> str:
    """Day 4: bias Whisper toward the plant names this garden actually has.

    The prompt is intentionally short and casual — Whisper uses it as a
    soft prior; longer prompts get truncated. We include:
      - all plant names + aliases from the garden config
      - the location name (for weather questions)
      - common garden actions the user might say

    We load the garden config directly from disk so prompt biasing works
    even when Ollama isn't reachable (degraded modes shouldn't degrade STT).
    """
    parts: list[str] = []

    # Load garden config from disk — independent of AICore / Ollama
    try:
        from .ai_core import GardenConfig
        # repo_root/src/growmate_pi/config/farmbot.yaml is the V2 config with
        # the full plant list. parents[2] = ../../.. = src/
        here = Path(__file__).resolve()
        cfg = here.parents[2] / "growmate_pi" / "config" / "farmbot.yaml"
        if not cfg.exists():
            cfg = here.parents[1] / "config" / "farmbot.yaml"
        garden = GardenConfig(str(cfg))
        for p in garden.plants:
            parts.append(str(p.get("name", "")))
            for a in p.get("aliases", []) or []:
                parts.append(str(a))
        for loc in garden.locations:
            parts.append(str(loc.get("name", "")))
        if garden.location_name:
            parts.append(str(garden.location_name))
    except Exception as exc:
        log.warning("Whisper prompt: garden config load failed (%s) — action vocab only", exc)

    # Action vocab — primes Whisper for the words it'll hear most often
    parts.extend([
        "water", "watering", "move", "go home", "lights on", "lights off",
        "photo", "check", "moisture", "stop", "halt", "emergency",
    ])

    # Dedupe while preserving order; cap length so Whisper doesn't truncate
    seen: set = set()
    deduped = []
    for w in parts:
        w = w.strip()
        if not w or w.lower() in seen:
            continue
        seen.add(w.lower())
        deduped.append(w)
    sentence = "Garden assistant vocabulary: " + ", ".join(deduped) + "."
    return sentence[:800]  # ~200 tokens cap


def _get_stt(name: str) -> Any:
    if name not in _STATE.stt_cache:
        _STATE.stt_cache[name] = load_stt(name, model_size=_STATE.whisper_model)
    backend = _STATE.stt_cache[name]
    # Apply prompt biasing on first use (after AICore + garden are loaded)
    if name in ("whisper", "faster-whisper", "fw") and _STATE.whisper_prompt is None:
        try:
            prompt = _build_whisper_prompt()
            if prompt:
                _STATE.whisper_prompt = prompt
                if hasattr(backend, "set_initial_prompt"):
                    backend.set_initial_prompt(prompt)
                log.info("Whisper prompt biased with %d chars", len(prompt))
        except Exception as exc:
            log.warning("Whisper prompt build failed: %s", exc)
    elif name in ("whisper", "faster-whisper", "fw") and _STATE.whisper_prompt:
        # Already built; ensure backend has it (in case cache was warmed before prompt was ready)
        if hasattr(backend, "set_initial_prompt") and not getattr(backend, "initial_prompt", None):
            backend.set_initial_prompt(_STATE.whisper_prompt)
    return backend
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


# Day 6: friendly user-facing labels. Technical command strings stay in
# the server log and the history record for traceability.
_FRIENDLY_DIRECTIONS = {
    ("x", -1): "left", ("x", +1): "right",
    ("y", -1): "back", ("y", +1): "forward",
    ("z", -1): "down", ("z", +1): "up",
}


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
    direction_word = _FRIENDLY_DIRECTIONS.get((axis, +1 if direction > 0 else -1), "")
    if axis == "z":
        friendly = f"{'Lifted' if direction > 0 else 'Lowered'} the arm by {int(step)} mm."
    else:
        friendly = f"Moved {direction_word} {int(step)} mm."
    log.info("JOG axis=%s dir=%+d step=%.0f cmd=%s status=%s",
             axis, direction, step, cmd, records[0].status)
    technical = f"{cmd} [{records[0].status}]"
    _record(source, f"{axis}_{'plus' if direction > 0 else 'minus'}",
            [cmd], records[0].status, technical)
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
    return payload


def _do_estop(source: str = "button") -> Dict[str, Any]:
    log.critical("EMERGENCY STOP triggered (source=%s)", source)
    record = _STATE.robot.emergency_stop()
    friendly = "Stopped. The robot is halted."
    _record(source, "estop", ["e"], record.status, f"e [{record.status}]")
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
    return payload


def _do_reset(source: str = "button") -> Dict[str, Any]:
    log.warning("E-stop RESET (source=%s)", source)
    records = _STATE.robot.execute(["E"])
    friendly = "All clear. Ready to go again."
    _record(source, "reset", ["E"], records[0].status, f"E [{records[0].status}]")
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
    return payload


def _do_home(source: str = "button") -> Dict[str, Any]:
    records = _STATE.robot.execute(["H_0"])
    _STATE.pos_x = _STATE.pos_y = 0.0
    _STATE.pos_z = 0.0
    friendly = "Heading home."
    _record(source, "home", ["H_0"], records[0].status, f"H_0 [{records[0].status}]")
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
    return payload


# Friendly captions for each emit action (translated from the technical action key).
_EMIT_FRIENDLY = {
    "water": "Watering all the plants.",
    "photo": "Taking a photo for you.",
    "light_on": "Lights on.",
    "light_off": "Lights off.",
}


def _do_emit(emissions: List[str], action: str, label: str, source: str = "button") -> Dict[str, Any]:
    records = _STATE.robot.execute(emissions)
    statuses = ", ".join(r.status for r in records)
    friendly = _EMIT_FRIENDLY.get(action, label.capitalize() + ".")
    technical = f"{' | '.join(emissions)} [{statuses}]"
    _record(source, action, emissions, records[0].status, technical)
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
    return payload


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
            friendly = "Stopped. The robot is halted."
            _record(source, "estop", ["e"], "sent", "e [pi]")
            payload = _position_payload(last_cmd=friendly)
            payload["tts_text"] = friendly
            return payload

        if action == "reset":
            pi_post_reset_estop(base)
            friendly = "All clear. Ready to go again."
            _record(source, "reset", ["E"], "sent", "E [pi]")
            payload = _position_payload(last_cmd=friendly)
            payload["tts_text"] = friendly
            return payload

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
            direction_word = _FRIENDLY_DIRECTIONS.get((axis, +1 if direction > 0 else -1), "")
            if axis == "z":
                friendly = f"{'Lifted' if direction > 0 else 'Lowered'} the arm by {int(step)} mm."
            else:
                friendly = f"Moved {direction_word} {int(step)} mm."
            intent = PiIntent(
                action="move",
                params={"x": new_x, "y": new_y, "z": new_z},
                response=friendly,
            )
            reply = pi_post_intent(_STATE.pi_url, [intent],
                                   raw_text=f"(jog {step:.0f}mm) {action}",
                                   client_id="growmate_voice.app")
            status = "sent" if reply.status == "success" else reply.status
            cmds = reply.commands_published or [f"M {new_x:.0f} {new_y:.0f} {new_z:.0f}"]
            _record(source, action, cmds, status, f"{cmds[0]} [pi:{status}]")
            payload = _position_payload(last_cmd=friendly)
            payload["tts_text"] = friendly
            return payload

        intent = app_action_to_intent(action)
        if intent is None:
            return None

        # Use the standard friendly phrase for this action; falls back to the
        # intent's own response (set by app_action_to_intent) if not mapped.
        friendly = TTS_PHRASES.get(action) or intent.response or f"{action.replace('_', ' ').capitalize()}."

        reply = pi_post_intent(
            _STATE.pi_url,
            [intent],
            raw_text=f"(button) {action}",
            client_id="growmate_voice.app",
        )
        status = "sent" if reply.status == "success" else reply.status
        cmds = reply.commands_published or [intent.action]
        _record(source, action, cmds, status, f"{' | '.join(cmds)} [pi:{status}]")
        payload = _position_payload(last_cmd=friendly)
        payload["tts_text"] = friendly
        return payload
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


def _pi_get(path: str, timeout_s: float = 4.0) -> Optional[Dict[str, Any]]:
    """Thin proxy helper — GET a path on the configured Pi and return JSON.

    Returns None if no Pi is configured or the call fails. Logs warnings for
    real failures so the browser-side caller can degrade gracefully.
    """
    if not (_STATE.pi_url and _PI_CLIENT_AVAILABLE):
        return None
    try:
        import httpx
        base = _STATE.pi_url.rsplit("/intent", 1)[0]
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(base + path)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.warning("Pi GET %s failed: %s", path, exc)
        return None


@app.get("/api/plants/needs_attention")
def api_plants_needs_attention(limit: int = 200) -> Dict[str, Any]:
    """Day 8/9 proxy: forward to the Pi's needs_attention list.

    Returns an empty list (with note) when no Pi is configured — the UI
    knows how to show "nothing to do here" in that case.
    """
    body = _pi_get(f"/plants/needs_attention?limit={int(limit)}")
    if body is None:
        return {"plants": [], "count": 0, "total_in_garden": 0,
                "error": "Pi not configured or unreachable"}
    return body


@app.get("/api/plants/{idx}")
def api_plant_detail(idx: int, history_limit: int = 30) -> Dict[str, Any]:
    """Day 8/9 proxy: forward to the Pi's per-plant detail endpoint."""
    body = _pi_get(f"/plants/{idx}?history_limit={int(history_limit)}")
    if body is None:
        return JSONResponse(status_code=503, content={
            "error": "Pi not reachable",
            "plant_index": idx,
        })
    return body


# --- Tier B Windows-side proxies ---------------------------------------------

@app.get("/api/pi_status")
def api_pi_status() -> Dict[str, Any]:
    """Forward the Pi's /status (which now includes task_state).

    The browser polls this at 1 Hz so the blocking overlay can render
    "Plant 3 of 8" in real time during multi-plant waters. Returns a
    benign body when no Pi is configured so the UI can keep polling
    harmlessly in sim/dev mode without a flood of console errors.
    """
    body = _pi_get("/status")
    if body is None:
        return {"ok": False, "task": {"task_active": False},
                "error": "Pi not configured or unreachable"}
    return body


@app.get("/api/plants/by_species/{target}")
def api_plants_by_species(target: str) -> Dict[str, Any]:
    """Proxy the Pi's /plants/by_species/{target} count + match list.

    Used by ``_dispatch_via_aicore`` to decide whether N >= 5 and the
    confirm gate should fire, but also exposed to the browser in case
    the UI wants to show a "this will water 8 plants" preview before
    the user confirms.
    """
    from urllib.parse import quote
    body = _pi_get(f"/plants/by_species/{quote(target)}")
    if body is None:
        return {"target": target, "count": 0, "plants": [],
                "error": "Pi not configured or unreachable"}
    return body


# --- Tier B follow-up: water_all -> water(target=species) rewrite -----------
# Cached species list so the post-classification check doesn't HTTP-hop the
# Pi on every voice/text call. ~60 s freshness is plenty — the active map
# doesn't change on its own.
_SPECIES_CACHE: Dict[str, Any] = {"species": None, "fetched_at": 0.0}
_SPECIES_TTL_S = 60.0


def _known_species_in_garden() -> List[str]:
    """List of distinct species slugs in the currently loaded garden.

    Cached for ``_SPECIES_TTL_S`` seconds so the species-rewrite check
    is essentially free on hot voice paths. Falls back to an empty list
    when no Pi is configured (the rewrite then becomes a no-op).
    """
    now = time.monotonic()
    cached = _SPECIES_CACHE.get("species")
    if cached is not None and (now - _SPECIES_CACHE.get("fetched_at", 0.0)) < _SPECIES_TTL_S:
        return cached  # type: ignore[return-value]
    body = _pi_get("/plants/species")
    species = (body or {}).get("species") or []
    if not isinstance(species, list):
        species = []
    _SPECIES_CACHE["species"] = species
    _SPECIES_CACHE["fetched_at"] = now
    return species


def _detect_species_in_transcript(transcript: str) -> Optional[str]:
    """Return the first known species mentioned in ``transcript`` or None.

    Plural-tolerant: matches "tomato", "tomatoes", "tomato bed", etc.
    Order is the species list's frequency order (most common first), so
    "water all the lettuces" hits "lettuce" before some rarer overlap.
    """
    t = (transcript or "").lower()
    if not t:
        return None
    species_list = _known_species_in_garden()
    if not species_list:
        return None
    # Tokens for whole-word checks (cheap regex).
    words = re.findall(r"\b[a-z]+\b", t)
    word_set = set(words)
    for sp in species_list:
        sp_l = sp.lower().strip()
        if not sp_l:
            continue
        # Direct word hit
        if sp_l in word_set:
            return sp_l
        # Plural / form-of: drop trailing 'es' / 's' and check
        plurals = {sp_l + "s", sp_l + "es"}
        if plurals & word_set:
            return sp_l
        # "Y -> ies" / common forms in the other direction
        if sp_l.endswith("y") and (sp_l[:-1] + "ies") in word_set:
            return sp_l
        # Substring catch (e.g. "lettuce_little_gem" species, "lettuce"
        # word in transcript)
        if any(w in sp_l for w in words if len(w) > 3):
            return sp_l
    return None


@app.get("/api/plants")
def api_plants() -> Dict[str, Any]:
    """Return the real garden layout for the UI map.

    Preference order:
      1. If --pi-url is set, ask the Pi for its installed map (so the UI
         always reflects the actual loaded garden — different Pis can have
         different gardens).
      2. Otherwise, read the repo's ``map_handler/config/active_map.yaml``
         as a fallback (sim mode / dev without a Pi).
    """
    # 1. Try the Pi first
    if _STATE.pi_url and _PI_CLIENT_AVAILABLE:
        try:
            import httpx
            base = _STATE.pi_url.rsplit("/intent", 1)[0]
            with httpx.Client(timeout=4.0) as client:
                r = client.get(base + "/plants")
                r.raise_for_status()
                body = r.json()
            if body.get("plants"):
                body["from_pi"] = True
                return body
        except Exception as exc:
            log.warning("Pi /plants fetch failed (%s) — falling back to local map", exc)

    # 2. Fall back to the local repo copy
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
# --------------------------------------------------------------------------- soft-confirm (Day 5)
import uuid as _uuid


def _cleanup_expired_confirms() -> None:
    now = time.monotonic()
    expired = [k for k, v in _STATE.pending_confirms.items() if v.get("expires_at", 0) < now]
    for k in expired:
        _STATE.pending_confirms.pop(k, None)


def _store_pending(payload: Dict[str, Any]) -> str:
    """Stash a deferred action; return its confirmation ID."""
    _cleanup_expired_confirms()
    cid = _uuid.uuid4().hex[:8]
    payload["expires_at"] = time.monotonic() + _PENDING_TTL_S
    _STATE.pending_confirms[cid] = payload
    return cid


def _pop_pending(cid: str) -> Optional[Dict[str, Any]]:
    _cleanup_expired_confirms()
    item = _STATE.pending_confirms.pop(cid, None)
    if item is None or item.get("expires_at", 0) < time.monotonic():
        return None
    return item


def _pattern_action_needs_confirm(action: Optional[str]) -> bool:
    if not action or action in _NEVER_CONFIRM:
        return False
    return action in _CONFIRM_PATTERN_ACTIONS


def _aicore_intents_need_confirm(intents: List[Dict[str, Any]]) -> bool:
    if not intents:
        return False
    actions = {(i or {}).get("action") for i in intents}
    if actions & _NEVER_CONFIRM:
        return False
    return bool(actions & _CONFIRM_AICORE_ACTIONS)


def _confirm_question(transcript: str, action_desc: str) -> str:
    """Phrasing that re-states what was heard, then asks for confirmation."""
    t = (transcript or "").strip()
    if t:
        return f"I heard you say: {t}. Should I {action_desc}?"
    return f"Should I {action_desc}?"


def _synthesise_tts_b64(text: str, backend_name: str = "kokoro") -> Optional[str]:
    """Synthesise ``text`` and return base64-encoded WAV, or None on failure."""
    if not text or backend_name == "none":
        return None
    try:
        backend = _get_tts(backend_name)
        if not backend.is_available():
            return None
        tts_np, tts_sr = backend.synthesise(text)
        wav_bytes = audio_to_wav_bytes(tts_np, sample_rate=tts_sr)
        return base64.b64encode(wav_bytes).decode("ascii")
    except Exception as exc:
        log.warning("TTS synth failed for '%s...': %s", text[:30], exc)
        return None


def _maybe_defer_for_confirm(
    transcript: str,
    pattern_action: Optional[str],
    route: str,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Return a pending response if the input triggers a destructive action.

    Heuristic gate — keeps the path cheap by avoiding re-classification:
      - Pattern route + ``water`` action (= P_4, water all) → confirm.
      - AICore route + transcript contains "everything" / "all plants" / etc.
        AND isn't phrased as a question → confirm.
    Anything matching ``_NEVER_CONFIRM`` (estop, etc.) is allowed straight
    through by the upstream router.
    """
    t = (transcript or "").lower().strip()
    if not t:
        return None

    if route == "pattern" and _pattern_action_needs_confirm(pattern_action):
        cid = _store_pending({
            "type": "pattern",
            "action": pattern_action,
            "source": source,
            "transcript": transcript,
        })
        return _build_pending_response(cid, transcript, "water all the plants")

    if route == "aicore":
        confirm_phrases = (
            "everything", "all the plants", "all plants", "the whole garden",
            "every plant", "all my plants", "all of them",
        )
        # Phrases that LOOK like questions ("can you …?", "could you …?") but
        # are really polite imperatives. Without this, "can you help me water
        # all the plants?" was sliding past the confirm gate because of the
        # trailing "?" — gh1 hardware run showed this misfire.
        polite_imperatives = (
            "can you ", "could you ", "would you ", "will you ",
            "please ", " please", "help me ", "help to ",
        )
        knowledge_starters = (
            "when ", "why ", "how ", "what ", "what's ", "which ",
            "who ", "should ", "is ", "are ", "do ", "does ",
            "has ", "have ", "tell ", "explain ",
        )
        looks_polite = any(p in t for p in polite_imperatives)
        is_knowledge_question = (not looks_polite) and (
            any(t.startswith(q) for q in knowledge_starters)
            or any(f" {q.strip()} " in f" {t} " for q in knowledge_starters)
        )
        if any(p in t for p in confirm_phrases) and not is_knowledge_question:
            cid = _store_pending({
                "type": "aicore_transcript",
                "transcript": transcript,
                "source": source,
            })
            return _build_pending_response(cid, transcript, "water all the plants")

    return None


def _build_pending_response(
    cid: str,
    transcript: str,
    action_desc: str,
    tts_backend: str = "kokoro",
) -> Dict[str, Any]:
    """Build the full /api/voice-shaped response for a deferred action."""
    question = _confirm_question(transcript, action_desc)
    payload = _position_payload(last_cmd=f"(awaiting confirmation: {action_desc})")
    payload["requires_confirm"] = True
    payload["confirm_id"] = cid
    payload["confirm_question"] = question
    payload["confirm_timeout_s"] = _PENDING_TTL_S
    payload["tts_text"] = question
    return payload


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


def _summarise_plants_for_target(target: str) -> Optional[Dict[str, Any]]:
    """Build an LLM-ready summary of plants matching ``target``.

    Reuses ``api_plants()``'s Pi-first / local-fallback chain so we always
    pick the most live data available. Filters the full plant list by
    matching the target against type or name (singular/plural-tolerant),
    then aggregates count, growth stage, water needs, and position range.

    Returns None when no match — caller falls back to the single-plant
    entry in ``garden.find(target)``.
    """
    if not target:
        return None
    try:
        all_data = api_plants()
    except Exception as exc:
        log.warning("_summarise_plants_for_target: api_plants failed: %s", exc)
        return None
    plants = all_data.get("plants", []) if isinstance(all_data, dict) else []
    if not plants:
        return None

    target_lc = target.lower().strip()
    # Tolerate common plural forms — "tomatoes" -> "tomato", "lilies" -> "lily",
    # "begonias" -> "begonia". Each form is matched as a substring against the
    # plant's type and name fields.
    forms = {target_lc}
    if target_lc.endswith("ies") and len(target_lc) > 3:
        forms.add(target_lc[:-3] + "y")
    if target_lc.endswith("es") and len(target_lc) > 3:
        forms.add(target_lc[:-2])
    if target_lc.endswith("s") and len(target_lc) > 1:
        forms.add(target_lc[:-1])

    matching = []
    for p in plants:
        ptype = (p.get("type") or "").lower()
        pname = (p.get("name") or "").lower()
        if any(f == ptype or f in ptype or f in pname for f in forms):
            matching.append(p)
    if not matching:
        return None

    xs = [float(p["x"]) for p in matching]
    ys = [float(p["y"]) for p in matching]
    stages = sorted({(p.get("stage") or "").strip() for p in matching if p.get("stage")})
    waters = sorted({p.get("water_quantity") for p in matching if p.get("water_quantity") is not None})

    return {
        "type": matching[0].get("type"),
        "count": len(matching),
        "growth_stage": stages[0] if len(stages) == 1 else stages,
        "water_seconds_per_plant": waters[0] if len(waters) == 1 else waters,
        "position_x_mm": [int(min(xs)), int(max(xs))] if len(xs) > 1 else [int(xs[0])],
        "position_y_mm": [int(min(ys)), int(max(ys))] if len(ys) > 1 else [int(ys[0])],
        "sample_names": [p["name"] for p in matching[:3]],
        "total_plants_in_garden": all_data.get("count"),
    }


# --------------------------------------------------------------------------- Day 11 — fast-path plant-state queries
# These short-circuit the LLM for common deterministic questions:
#   - "when did I last water the tomatoes?" -> Pi event log lookup
#   - "is the marigold thirsty?"            -> Pi needs_attention check
#   - "which plant needs water most?"        -> top of needs_attention list
# Spoken answers are built locally — no Ollama round-trip, no hallucination.

_FAST_Q_MOST_URGENT = [
    re.compile(r"\b(?:which|what)\s+plants?\s+need(?:s)?\s+water\s+(?:the\s+)?most\b", re.I),
    re.compile(r"\b(?:which|what)\s+plant\s+is\s+(?:the\s+)?most\s+thirsty\b", re.I),
    re.compile(r"\bwhat['s\s]+(?:the\s+)?most\s+thirsty\s+plant\b", re.I),
    re.compile(r"\bwhat\s+needs\s+water(?:ing)?\b", re.I),
    re.compile(r"\bwhat\s+should\s+i\s+water\b", re.I),
]
_FAST_Q_LAST_WATERED = [
    re.compile(r"\bwhen\s+did\s+i\s+(?:last\s+)?water(?:ed)?\s+(?:the\s+)?(.+?)(?:\s*\?)?\s*$", re.I),
    re.compile(r"\bwhen\s+was\s+(?:the\s+)?(.+?)\s+(?:last\s+)?watered(?:\s*\?)?\s*$", re.I),
]
_FAST_Q_DAYS_SINCE = [
    re.compile(r"\bhow\s+(?:many\s+days|long)\s+(?:has\s+it\s+been\s+)?since\s+(?:i\s+)?(?:last\s+)?water(?:ed)?\s+(?:the\s+)?(.+?)(?:\s*\?)?\s*$", re.I),
]
_FAST_Q_IS_THIRSTY = [
    re.compile(r"\b(?:is|are)\s+(?:the\s+|my\s+)?(.+?)\s+thirsty(?:\s*\?)?\s*$", re.I),
    re.compile(r"\bdo(?:es)?\s+(?:the\s+|my\s+)?(.+?)\s+need\s+water(?:\s*\?)?\s*$", re.I),
]


def _wall_humanise_ts(ts_ms: Optional[int]) -> Optional[str]:
    """Render unix-epoch-ms as 'X ago'. Mirrors the Pi-side helper so the
    fast-path can format event timestamps without an extra HTTP round-trip."""
    if not ts_ms:
        return None
    delta_s = max(0, (time.time() * 1000 - ts_ms) / 1000.0)
    if delta_s < 45:
        return "just now"
    if delta_s < 3600:
        m = int(round(delta_s / 60))
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta_s < 86400:
        h = int(round(delta_s / 3600))
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if delta_s < 7 * 86400:
        d = int(round(delta_s / 86400))
        return f"{d} day{'s' if d != 1 else ''} ago"
    if delta_s < 30 * 86400:
        w = int(round(delta_s / (7 * 86400)))
        return f"{w} week{'s' if w != 1 else ''} ago"
    months = int(round(delta_s / (30 * 86400)))
    return f"{months} month{'s' if months != 1 else ''} ago"


def _days_since_ms(ts_ms: Optional[int]) -> Optional[float]:
    if not ts_ms:
        return None
    delta_s = max(0, time.time() * 1000 - ts_ms) / 1000.0
    return round(delta_s / 86400.0, 1)


def _normalise_target_forms(target: str) -> List[str]:
    """Singular/plural variants of a target string, more specific first.
    'tomatoes' -> ['tomatoes', 'tomato']; 'lilies' -> ['lilies', 'lily']."""
    t = (target or "").lower().strip().rstrip("?.,").strip()
    if not t:
        return []
    forms = [t]
    if t.endswith("ies") and len(t) > 3:
        forms.append(t[:-3] + "y")
    if t.endswith("es") and len(t) > 3:
        forms.append(t[:-2])
    if t.endswith("s") and len(t) > 1:
        forms.append(t[:-1])
    seen: set = set()
    return [f for f in forms if not (f in seen or seen.add(f))]


def _find_latest_water_event(target_forms: List[str]) -> Optional[Dict[str, Any]]:
    """Most recent 'watered' event whose plant_name matches any candidate form.
    Returns None if the Pi can't be reached at all, or {} if reached but no
    match. Callers distinguish: None -> fall through to LLM; {} -> answer
    'haven't watered yet'."""
    for form in target_forms:
        body = _pi_get(f"/events?plant={quote_plus(form)}&event_type=watered&limit=1")
        if body is None:
            return None
        events = body.get("events") or []
        if events:
            return events[0]
    return {}


def _matching_plants_in_garden(target_forms: List[str]) -> List[Dict[str, Any]]:
    try:
        data = api_plants()
    except Exception as exc:
        log.warning("_matching_plants_in_garden: api_plants failed: %s", exc)
        return []
    plants = data.get("plants", []) if isinstance(data, dict) else []
    matches = []
    for p in plants:
        ptype = (p.get("type") or "").lower()
        pname = (p.get("name") or "").lower()
        if any(f in ptype or f in pname for f in target_forms):
            matches.append(p)
    return matches


def _fast_answer_most_urgent() -> Optional[Dict[str, Any]]:
    try:
        att = api_plants_needs_attention(limit=5)
    except Exception:
        return None
    plants = att.get("plants") or []
    if att.get("error") and not plants:
        return None  # Pi unreachable — let LLM try
    total = att.get("total_in_garden") or 0
    if not plants:
        msg = ("All your plants look fine. Nothing is overdue."
               if total else "I can't read the garden right now.")
        return {"answer": msg, "transcript_label": "(care status)"}
    top = plants[0]
    name = top.get("name") or "one plant"
    state = top.get("state") or {}
    days = state.get("days_since_watered")
    if days is not None:
        d = int(round(float(days)))
        msg = (f"The most urgent is {name}. "
               f"It hasn't been watered in {d} day{'s' if d != 1 else ''}.")
    else:
        reason = (state.get("attention_reason") or "It needs watering.").strip()
        if not reason.endswith("."):
            reason += "."
        msg = f"The most urgent is {name}. {reason}"
    return {"answer": msg, "transcript_label": f"(most urgent: {name})"}


def _fast_answer_last_watered(target: str, as_days: bool = False) -> Optional[Dict[str, Any]]:
    forms = _normalise_target_forms(target)
    if not forms:
        return None
    in_garden = _matching_plants_in_garden(forms)
    if not in_garden:
        return {
            "answer": f"I don't see any {target} in the current garden.",
            "transcript_label": f"(unknown plant: {target})",
        }
    event = _find_latest_water_event(forms)
    if event is None:
        return None  # Pi unreachable
    if not event:
        return {
            "answer": f"I haven't watered the {target} yet.",
            "transcript_label": f"(no watering history: {target})",
        }
    ts = event.get("ts")
    if as_days:
        days = _days_since_ms(ts)
        if days is None:
            return None
        msg = (f"It's been {days} day{'s' if days != 1 else ''} "
               f"since I last watered the {target}.")
    else:
        human = _wall_humanise_ts(ts) or "recently"
        msg = f"You watered the {target} {human}."
    return {"answer": msg, "transcript_label": f"(last watered: {target})"}


def _fast_answer_is_thirsty(target: str) -> Optional[Dict[str, Any]]:
    forms = _normalise_target_forms(target)
    if not forms:
        return None
    in_garden = _matching_plants_in_garden(forms)
    if not in_garden:
        return {
            "answer": f"I don't see any {target} in the current garden.",
            "transcript_label": f"(unknown plant: {target})",
        }
    try:
        att = api_plants_needs_attention(limit=200)
    except Exception:
        return None
    if att.get("error") and not (att.get("plants") or []):
        return None
    overdue = att.get("plants") or []
    thirsty: List[Dict[str, Any]] = []
    for p in overdue:
        species = (p.get("species") or "").lower()
        name = (p.get("name") or "").lower()
        if any(f in species or f in name for f in forms):
            thirsty.append(p)
    if thirsty:
        top = thirsty[0]
        state = top.get("state") or {}
        days = state.get("days_since_watered")
        if days is not None:
            d = int(round(float(days)))
            msg = (f"Yes, the {target} could use water. "
                   f"It's been {d} day{'s' if d != 1 else ''} since the last watering.")
        else:
            msg = f"Yes, the {target} needs water."
    else:
        msg = f"The {target} looks fine. It was watered recently enough."
    return {"answer": msg, "transcript_label": f"(thirst check: {target})"}


def _fast_path_plant_query(transcript: str) -> Optional[Dict[str, Any]]:
    """Try to answer a plant-state question deterministically without the LLM.

    Returns {'answer': str, 'transcript_label': str} on a hit, else None.
    Returning None falls through to the normal AICore path — used both for
    'pattern didn't match' and 'Pi unreachable, let LLM make something up'.
    """
    if not _MEMORY_FEATURES_ENABLED:
        return None
    t = (transcript or "").strip()
    if not t:
        return None
    for pat in _FAST_Q_MOST_URGENT:
        if pat.search(t):
            return _fast_answer_most_urgent()
    for pat in _FAST_Q_LAST_WATERED:
        m = pat.search(t)
        if m:
            return _fast_answer_last_watered(m.group(1).strip())
    for pat in _FAST_Q_DAYS_SINCE:
        m = pat.search(t)
        if m:
            return _fast_answer_last_watered(m.group(1).strip(), as_days=True)
    for pat in _FAST_Q_IS_THIRSTY:
        m = pat.search(t)
        if m:
            return _fast_answer_is_thirsty(m.group(1).strip())
    return None


_MULTI_PLANT_CONFIRM_THRESHOLD = 5


def _dispatch_via_aicore(
    transcript: str, source: str,
    skip_multi_plant_gate: bool = False,
) -> Dict[str, Any]:
    """Run the LLM classifier and dispatch the resulting intents to Pi.

    Returns a position_payload-shaped dict. If anything fails (LLM down,
    Pi unreachable, no intents), falls back to local sim mode with the
    transcript as the last_cmd note.

    Tier B / Q2: when the LLM classifies a ``water`` intent with a target
    and the Pi reports N >= ``_MULTI_PLANT_CONFIRM_THRESHOLD`` matching
    plants in the active map, this defers via the existing soft-confirm
    modal (same mechanism as water_all) so the user gets a "That's 8
    lettuces. Shall I water them all?" prompt before the gantry starts
    a 4-minute sequence. ``skip_multi_plant_gate`` is set True by
    /api/confirm after the user already accepted — without it, the
    re-dispatch would loop.
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

    # Tier B follow-up: water_all -> water(target=species) rewrite.
    # The WSL session caught the LLM classifying "water all the lettuces"
    # as water_all (firing P_4 across the whole bed). When the user
    # explicitly named a species in their request, honour that even when
    # the LLM's action label disagrees. This runs BEFORE the multi-plant
    # gate so the gate sees the rewritten action and fires properly.
    for i in intents:
        if i.get("action") != "water_all":
            continue
        detected = _detect_species_in_transcript(transcript)
        if not detected:
            continue
        log.info("water_all rewritten to water target=%s based on transcript "
                 "(LLM disagreed: %s)", detected, transcript)
        i["action"] = "water"
        i["target"] = detected

    # Tier B Q2: pre-check multi-plant water for the confirm gate.
    if not skip_multi_plant_gate:
        for i in intents:
            if i.get("action") != "water":
                continue
            target = (i.get("target") or "").strip()
            if not target:
                continue
            try:
                body = api_plants_by_species(target)
            except Exception as exc:
                log.warning("by_species lookup failed for %s: %s", target, exc)
                continue
            n = int(body.get("count") or 0)
            if n < _MULTI_PLANT_CONFIRM_THRESHOLD:
                continue
            # Defer via the same modal mechanism as water_all. We stash the
            # transcript and let /api/confirm re-run _dispatch_via_aicore
            # with skip_multi_plant_gate=True.
            action_desc = f"water all {n} {target}"
            cid = _store_pending({
                "type": "aicore_transcript",
                "transcript": transcript,
                "source": source,
                "skip_multi_plant_gate": True,
            })
            log.info("multi-plant gate: %s -> %d plants, deferring (cid=%s)",
                     target, n, cid)
            return _build_pending_response(cid, transcript, action_desc)

    # Day 1 fix: any general_question intent needs a real answer, not the
    # LLM's pre-baked "let me look that up" filler. Run AICore.reason() with
    # a minimal context (the question itself plus plant info if targeted).
    # Future phases will add weather (day 2) and live plant state (day 3).
    for i in intents:
        if i.get("action") != "general_question":
            continue
        question = (i.get("question") or "").strip() or transcript
        context: Dict[str, Any] = {
            "location": ai.garden.location_context,
        }
        target = (i.get("target") or "").strip()
        if target:
            # Prefer the live plant summary (Pi or local map) which has
            # count, position range, and aggregated stage. Fall back to the
            # single-plant entry in farmbot.yaml when no map data is available.
            summary = _summarise_plants_for_target(target)
            if summary:
                context["plant"] = summary
            else:
                plant = ai.garden.find(target)
                if plant:
                    context["plant"] = {
                        "name": plant.get("name"),
                        "stage": plant.get("stage"),
                        "water_quantity_seconds": plant.get("water_quantity"),
                    }
        # Day 10: for care / attention / "what needs water" questions, give
        # the LLM the live overdue list so its answer is data-driven instead
        # of made up. Cheap heuristic: question mentions attention/need/care
        # or words to that effect.
        q_lc = (question + " " + transcript).lower()
        if any(w in q_lc for w in ("attention", "need ", "needs ", "needed",
                                    "due ", "overdue", "thirsty", "dry",
                                    "what should i", "today")):
            try:
                attention = api_plants_needs_attention(limit=20)
                overdue = attention.get("plants") or []
                if overdue:
                    context["plants_needing_water"] = [
                        {
                            "name": p.get("name"),
                            "species": p.get("species"),
                            "reason": (p.get("state") or {}).get("attention_reason"),
                            "days_since_watered": (p.get("state") or {}).get("days_since_watered"),
                        }
                        for p in overdue
                    ]
                    context["plants_needing_water_count"] = len(overdue)
                else:
                    context["plants_needing_water"] = []
                    context["plants_needing_water_count"] = 0
            except Exception as exc:
                log.warning("attention-list lookup failed: %s", exc)
        try:
            answer = ai.reason(context, question)
        except Exception as exc:
            log.warning("AICore.reason failed: %s", exc)
            answer = i.get("response") or "I'm not sure about that."
        i["response"] = (answer or "").strip() or "I'm not sure about that."

    # Build PiIntent objects from the raw classifier dicts
    if not (_PI_CLIENT_AVAILABLE and _STATE.pi_url):
        # No Pi configured — log and return; can't execute robot actions client-side
        responses = " ".join(i.get("response", "") for i in intents)
        _record(source, intents[0].get("action"), [], "simulated",
                f"AICore (no Pi): {responses}", transcript=transcript)
        # When the only intents are general_question, expose the answer for TTS
        payload = _position_payload(last_cmd=f"AICore -> {intents[0].get('action')} (no Pi)")
        payload["tts_text"] = responses.strip()
        return payload

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
    # Friendly user-facing string: use Pi's spoken text if any, then the
    # LLM-generated intent responses. The raw command list goes into the
    # history record for traceability but never into last_cmd.
    spoken = (reply.tts_text or " ".join(i.get("response", "") for i in intents)).strip()
    friendly = spoken or f"Done ({actions})."
    _record(source, intents[0].get("action"), cmds, status,
            f"{' | '.join(cmds) or '(no cmds)'} [{status}]",
            transcript=transcript)
    payload = _position_payload(last_cmd=friendly)
    payload["tts_text"] = friendly
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

        pending = _maybe_defer_for_confirm(text, action, route, source="text")
        if pending:
            pipeline_log.append(f"❓ Awaiting confirm: {pending['confirm_id']}")
            position_payload = pending
        elif route == "pattern" and action is not None:
            position_payload = _execute_action(action, source="text")
            if _STATE.history._entries:
                last = _STATE.history._entries[-1]
                last.transcript = text
                last.confidence = confidence
            pipeline_log.append(f"🤖 {position_payload['last_cmd']}")
        else:
            # Day 11: deterministic plant-state queries skip the LLM.
            fast = _fast_path_plant_query(text)
            if fast:
                position_payload = _position_payload(last_cmd=fast["transcript_label"])
                position_payload["tts_text"] = fast["answer"]
                pipeline_log.append(f"⚡ Fast plant query: {fast['answer']}")
                _record("text", "fast_plant_query", [], "answered",
                        fast["answer"], transcript=text)
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
        result_block: Dict[str, Any] = {
            "raw_transcript": text,
            "matched_action": action,
            "confidence": confidence,
            "tts_spoken": tts_spoken,
            "position": position_payload,
        }
        if position_payload.get("requires_confirm"):
            result_block["requires_confirm"] = True
            result_block["confirm_id"] = position_payload.get("confirm_id")
            result_block["confirm_question"] = position_payload.get("confirm_question")
            result_block["confirm_timeout_s"] = position_payload.get("confirm_timeout_s")
        return JSONResponse({
            "result": result_block,
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


@app.post("/api/confirm")
def api_confirm(body: Dict[str, Any]) -> Any:
    """Resolve a pending action stashed by ``_maybe_defer_for_confirm``.

    Body: ``{confirm_id: str, confirmed: bool, tts: str?, enable_tts: str?}``.
    On YES: dispatches the deferred action through the same path that would
    have run if the gate weren't there. On NO (or unknown id): returns a
    cancellation payload with a friendly TTS "Cancelled.".
    """
    cid = (body or {}).get("confirm_id", "")
    confirmed = bool((body or {}).get("confirmed", False))
    tts = (body or {}).get("tts", "kokoro")
    enable_tts = str((body or {}).get("enable_tts", "true")).lower()

    item = _pop_pending(cid)
    pipeline_log: List[str] = [f"❓ confirm id={cid} → {'YES' if confirmed else 'NO'}"]

    if item is None:
        msg = "That request expired. Please ask again."
        pipeline_log.append("⚠ unknown or expired confirm id")
        return JSONResponse({
            "cancelled": True,
            "expired": True,
            "result": {
                "tts_spoken": msg,
                "position": _position_payload(last_cmd=f"(confirm id {cid} expired)"),
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": _synthesise_tts_b64(msg, tts) if enable_tts == "true" else None,
        })

    if not confirmed:
        msg = "Cancelled."
        pipeline_log.append("🛑 user said NO")
        _record(item.get("source", "voice"), None, [], "cancelled",
                f"User cancelled: {item.get('transcript', '')}",
                transcript=item.get("transcript", ""))
        return JSONResponse({
            "cancelled": True,
            "result": {
                "tts_spoken": msg,
                "position": _position_payload(last_cmd=f"(cancelled: {item.get('transcript', '')})"),
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": _synthesise_tts_b64(msg, tts) if enable_tts == "true" else None,
        })

    # User confirmed — execute the deferred action.
    source = item.get("source", "voice")
    transcript = item.get("transcript", "")
    # Tier B Q2: if this pending was a multi-plant gate deferral, tell the
    # re-dispatcher to skip the gate check so it doesn't loop right back to
    # another modal.
    skip_multi_plant_gate = bool(item.get("skip_multi_plant_gate", False))
    try:
        if item["type"] == "pattern":
            payload = _execute_action(item["action"], source=source)
            pipeline_log.append(f"🤖 {payload.get('last_cmd', '')}")
        elif item["type"] == "aicore_transcript":
            payload = _dispatch_via_aicore(
                transcript, source=source,
                skip_multi_plant_gate=skip_multi_plant_gate,
            )
            pipeline_log.append(f"🧠 {payload.get('last_cmd', '')}")
        else:
            payload = _position_payload(last_cmd=f"(unknown pending type: {item['type']})")
    except Exception as exc:
        log.exception("Confirmed dispatch failed")
        pipeline_log.append(f"❌ Error: {exc}")
        payload = _position_payload(last_cmd=f"(error: {exc})")

    tts_audio_b64: Optional[str] = None
    phrase = (payload.get("tts_text") or "").strip()
    if enable_tts == "true" and tts != "none" and phrase:
        tts_audio_b64 = _synthesise_tts_b64(phrase, tts)

    return JSONResponse({
        "cancelled": False,
        "result": {
            "raw_transcript": transcript,
            "tts_spoken": phrase,
            "position": payload,
        },
        "log": "\n".join(pipeline_log),
        "tts_audio_b64": tts_audio_b64,
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

        # Day 5: soft-confirm gate — destructive commands like "water everything"
        # pause for a YES/NO before firing. Emergency phrases pass through.
        pending = _maybe_defer_for_confirm(transcript, action, route, source="voice")
        if pending:
            pipeline_log.append(f"❓ Awaiting confirm: {pending['confirm_id']}")
            position_payload = pending
        elif route == "pattern" and action is not None:
            # Fast path — pattern match drives the action (works for emergency,
            # home, lights, photo, jog, generic water/photo).
            position_payload = _execute_action(action, source="voice")
            if _STATE.history._entries:
                last = _STATE.history._entries[-1]
                last.transcript = transcript
                last.confidence = confidence
            pipeline_log.append(f"🤖 {position_payload['last_cmd']}")
        else:
            # Day 11: deterministic plant-state queries skip the LLM.
            # Triggers on phrasings like "when did I last water X",
            # "is X thirsty", "which plant needs water most".
            fast = _fast_path_plant_query(transcript)
            if fast:
                position_payload = _position_payload(last_cmd=fast["transcript_label"])
                position_payload["tts_text"] = fast["answer"]
                pipeline_log.append(f"⚡ Fast plant query: {fast['answer']}")
                _record("voice", "fast_plant_query", [], "answered",
                        fast["answer"], transcript=transcript)
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
        result_block: Dict[str, Any] = {
            "raw_transcript": transcript,
            "matched_action": action,
            "confidence": confidence,
            "stt_latency_ms": round(latency_ms, 1),
            "stt_backend": stt_backend.name,
            "tts_spoken": tts_spoken,
            "position": position_payload,
        }
        # Day 5: lift confirm fields to the top of `result` so the UI
        # doesn't need to dig into result.position for them.
        if position_payload.get("requires_confirm"):
            result_block["requires_confirm"] = True
            result_block["confirm_id"] = position_payload.get("confirm_id")
            result_block["confirm_question"] = position_payload.get("confirm_question")
            result_block["confirm_timeout_s"] = position_payload.get("confirm_timeout_s")
        return JSONResponse({
            "result": result_block,
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
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='15' fill='%23faf6ee'/><path d='M16 24 Q16 14 22 10 Q18 17 16 24 Q16 14 10 10 Q14 17 16 24 Z' fill='%234a7c59'/><rect x='15' y='22' width='2' height='6' fill='%234a7c59'/></svg>">
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
  /* Day 6: body bumped from 18 → 20 px so labels and chat bubbles meet
     the elderly-readable minimum without resizing every component. */
  font-size: 20px;
  line-height: 1.45;
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

/* Day 10: Today's care nudge — parked (Jun 2026 hardware run found event-log
   accuracy issues: P_4 logged 'watered_all' even when the BT didn't finish).
   Hidden via display:none until the event log gets a proper verify gate. */
.today-card{
  display: none;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-l);
  padding: 16px 20px 14px;
  margin-top: 14px;
  box-shadow: var(--shadow-s);
}
.today-title{
  font-size: 13px;
  color: var(--ink-soft);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  margin: 0 0 8px;
}
.today-summary{
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: var(--moss-soft);
  color: var(--moss-deep);
  border: none;
  border-radius: var(--radius-m);
  font-family: inherit;
  font-size: 20px;
  font-weight: 700;
  text-align: left;
  cursor: pointer;
  transition: background .15s ease;
}
.today-summary[data-tone="warn"]{
  background: #f4d9b8;
  color: #8a4f0f;
}
.today-summary:hover{ filter: brightness(.97); }
.today-summary:focus-visible{ outline: none; box-shadow: var(--focus); }
.today-summary-chev{
  font-size: 18px;
  transition: transform .2s ease;
}
.today-summary[aria-expanded="true"] .today-summary-chev{
  transform: rotate(180deg);
}
/* When count = 0 the summary isn't a real button (no list to expand) */
.today-summary[data-tone="ok"] .today-summary-chev{ display: none; }
.today-summary[data-tone="ok"]{ cursor: default; }

.today-list{
  list-style: none;
  padding: 0;
  margin: 10px 0 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  overflow-y: auto;
}
.today-list li{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-s);
  background: var(--cream-deep);
  font-size: 18px;
  cursor: pointer;
  transition: background .12s ease;
}
.today-list li:hover{ background: #eadec1; }
.today-list li:focus-visible{ outline: none; box-shadow: var(--focus); }
.today-list .t-name{ font-weight: 700; color: var(--ink); }
.today-list .t-reason{ color: var(--ink-soft); font-size: 17px; }
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

/* ---------- Day 5: confirm modal ---------- */
.confirm-overlay{
  position: fixed; inset: 0;
  background: rgba(43,42,38,.55);
  display: grid; place-items: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity .2s ease;
  z-index: 90;
}
.confirm-overlay.show{ opacity: 1; pointer-events: auto; }
.confirm-card{
  background: var(--paper);
  border-radius: var(--radius-l);
  padding: 32px 28px 24px;
  width: min(520px, 92vw);
  box-shadow: var(--shadow-l);
  text-align: center;
  border: 2px solid var(--clay);
}
.confirm-text{
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 24px;
  color: var(--ink);
  line-height: 1.35;
}
.confirm-actions{
  display: flex;
  gap: 14px;
  justify-content: center;
  margin-bottom: 16px;
}
.confirm-yes, .confirm-no{
  min-height: 64px;
  min-width: 140px;
  border-radius: var(--radius-m);
  border: none;
  font-family: inherit;
  font-size: 22px;
  font-weight: 800;
  cursor: pointer;
  transition: transform .1s ease, background .15s ease;
}
.confirm-yes{
  background: var(--moss);
  color: #fff;
  box-shadow: 0 6px 0 var(--moss-deep);
}
.confirm-yes:hover{ background: #557f63; }
.confirm-yes:active{ transform: translateY(3px); box-shadow: 0 3px 0 var(--moss-deep); }
.confirm-no{
  background: var(--cream-deep);
  color: var(--ink);
  box-shadow: 0 6px 0 #d4cdb8;
}
.confirm-no:hover{ background: #e8dec5; }
.confirm-no:active{ transform: translateY(3px); box-shadow: 0 3px 0 #d4cdb8; }
.confirm-yes:focus-visible, .confirm-no:focus-visible{
  outline: none; box-shadow: var(--focus);
}
.confirm-hint{
  font-size: 14px;
  color: var(--ink-soft);
  margin: 0;
}

/* ---------- Day 9: plant care card ---------- */
.care-overlay{
  position: fixed; inset: 0;
  background: rgba(43,42,38,.45);
  display: grid; place-items: end center;
  opacity: 0;
  pointer-events: none;
  transition: opacity .22s ease;
  z-index: 85;
}
.care-overlay.show{ opacity: 1; pointer-events: auto; }
.care-card{
  background: var(--paper);
  border-radius: var(--radius-l) var(--radius-l) 0 0;
  padding: 28px 28px 32px;
  width: min(640px, 100vw);
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: var(--shadow-l);
  border: 2px solid var(--moss-soft);
  border-bottom: 0;
  position: relative;
  transform: translateY(20px);
  transition: transform .22s ease;
}
.care-overlay.show .care-card{ transform: translateY(0); }
.care-close{
  position: absolute;
  top: 14px; right: 14px;
  width: 48px; height: 48px;
  border-radius: 999px;
  border: none;
  background: transparent;
  color: var(--ink-soft);
  font-size: 28px;
  font-weight: 800;
  cursor: pointer;
  line-height: 1;
}
.care-close:hover{ background: var(--cream-deep); color: var(--ink); }
.care-close:focus-visible{ outline: none; box-shadow: var(--focus); }
.care-title{
  margin: 0 60px 8px 0;
  font-size: 28px;
  font-weight: 800;
  color: var(--ink);
}
.care-badge{
  display: inline-block;
  padding: 6px 14px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 18px;
  margin-bottom: 18px;
}
.care-badge[data-tone="ok"]   { background: var(--moss-soft); color: var(--moss-deep); }
.care-badge[data-tone="warn"] { background: #f4d9b8;          color: #8a4f0f; }
.care-badge[data-tone="bad"]  { background: #f1c5be;          color: var(--tomato-deep); }

.care-facts{
  list-style: none;
  padding: 0;
  margin: 0 0 24px;
  font-size: 20px;
  line-height: 1.55;
  color: var(--ink);
}
.care-facts li{
  display: flex;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--line);
}
.care-facts li:last-child{ border-bottom: 0; }
.care-facts .k{ color: var(--ink-soft); min-width: 140px; }
.care-facts .v{ color: var(--ink); font-weight: 600; }

.care-water{
  width: 100%;
  min-height: 72px;
  font-size: 22px;
  font-weight: 800;
  border: none;
  border-radius: var(--radius-m);
  background: var(--moss);
  color: #fff;
  box-shadow: 0 6px 0 var(--moss-deep);
  cursor: pointer;
  transition: transform .1s ease, background .15s ease;
  margin-bottom: 14px;
}
.care-water:hover{ background: #557f63; }
.care-water:active{ transform: translateY(3px); box-shadow: 0 3px 0 var(--moss-deep); }
.care-water:disabled{
  background: var(--cream-deep);
  color: var(--ink-soft);
  box-shadow: 0 6px 0 #d4cdb8;
  cursor: not-allowed;
}
.care-water:focus-visible{ outline: none; box-shadow: var(--focus); }

.care-note{
  font-size: 15px;
  color: var(--ink-soft);
  line-height: 1.4;
  margin: 0;
}

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

/* ============ Tier B: blocking task overlay ============ */
.task-overlay{
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(53, 90, 64, .92);   /* moss-deep, near-opaque */
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: taskFadeIn .25s ease-out;
}
.task-overlay.is-open{ display: flex; }
@keyframes taskFadeIn { from { opacity: 0 } to { opacity: 1 } }
.task-card{
  width: min(640px, 100%);
  background: var(--paper);
  border-radius: var(--radius-l);
  padding: 36px 32px 28px;
  box-shadow: 0 24px 60px rgba(0,0,0,.35);
  text-align: center;
  color: var(--ink);
}
.task-card .task-label{
  font-size: 28px;
  font-weight: 800;
  letter-spacing: .3px;
  color: var(--moss-deep);
  margin: 0 0 6px;
  line-height: 1.2;
}
.task-card .task-progress-text{
  font-size: 22px;
  font-weight: 600;
  color: var(--moss);
  margin: 0 0 18px;
}
.task-card .task-bar{
  width: 100%;
  height: 16px;
  border-radius: 10px;
  background: var(--cream-deep);
  overflow: hidden;
  margin: 0 0 8px;
}
.task-card .task-bar-fill{
  height: 100%;
  width: 0%;
  background: linear-gradient(90deg, var(--moss-soft), var(--moss));
  transition: width .3s ease-out;
}
.task-card .task-current{
  font-size: 18px;
  color: var(--ink);
  opacity: .75;
  margin: 4px 0 28px;
  min-height: 1.2em;
}
.task-card .task-abort{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  width: 100%;
  padding: 26px 24px;
  border: none;
  border-radius: var(--radius-m);
  background: var(--tomato);
  color: white;
  font-size: 28px;
  font-weight: 800;
  letter-spacing: .5px;
  cursor: pointer;
  box-shadow: 0 8px 22px rgba(193,57,43,.35);
  transition: transform .12s ease, box-shadow .2s ease, background .2s ease;
  font-family: inherit;
}
.task-card .task-abort:hover{ background: #b13629; }
.task-card .task-abort:active{ transform: scale(.97); box-shadow: 0 4px 12px rgba(193,57,43,.25); }
.task-card .task-abort-icon{
  width: 36px; height: 36px;
  border-radius: 50%;
  background: white;
  position: relative;
}
.task-card .task-abort-icon::after{
  content: "";
  position: absolute;
  inset: 8px;
  border-radius: 4px;
  background: var(--tomato);
}
.task-card .task-foot{
  margin-top: 14px;
  font-size: 16px;
  color: var(--moss-deep);
  opacity: .8;
}
/* Stopped-mode card variant: same shell, warm clay accent so it's
   obviously a different state from "running" */
.task-card[data-mode="stopped"]{ border: 3px solid var(--clay); }
.task-card .task-label.stopped{
  color: var(--clay);
  font-size: 30px;
  margin: 0 0 10px;
}
.task-card .task-stopped-sub{
  font-size: 19px;
  color: var(--ink);
  opacity: .85;
  margin: 0 0 28px;
  line-height: 1.4;
}
.task-card .task-reset{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  width: 100%;
  padding: 26px 24px;
  border: none;
  border-radius: var(--radius-m);
  background: var(--moss);
  color: white;
  font-size: 24px;
  font-weight: 800;
  letter-spacing: .4px;
  cursor: pointer;
  box-shadow: 0 8px 22px rgba(74,124,89,.35);
  transition: transform .12s ease, box-shadow .2s ease, background .2s ease;
  font-family: inherit;
}
.task-card .task-reset:hover{ background: var(--moss-deep); }
.task-card .task-reset:active{ transform: scale(.97); }
.task-card .task-reset:disabled{ opacity: .55; cursor: not-allowed; }
.task-card .task-reset-icon{
  font-size: 26px;
  font-weight: 900;
  line-height: 1;
}
.task-card .task-stopped-stay{
  margin-top: 14px;
  background: transparent;
  border: none;
  color: var(--moss-deep);
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: underline;
  font-family: inherit;
  padding: 8px 10px;
}
.task-card .task-stopped-stay:hover{ color: var(--ink); }
@media (max-width: 600px){
  .task-card{ padding: 28px 22px 22px; }
  .task-card .task-label{ font-size: 24px; }
  .task-card .task-abort{ padding: 22px 18px; font-size: 24px; }
  .task-card .task-reset{ padding: 22px 18px; font-size: 20px; }
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

  <!-- Day 10: Today's care — proactive nudge for what needs water now -->
  <section class="today-card" id="todayCard" aria-labelledby="today-title">
    <h2 class="today-title" id="today-title">Today's care</h2>
    <button class="today-summary" id="todaySummary"
            aria-expanded="false" aria-controls="todayList">
      <span class="today-summary-text" id="todaySummaryText">Checking your garden…</span>
      <span class="today-summary-chev" aria-hidden="true">▾</span>
    </button>
    <ul class="today-list" id="todayList" hidden></ul>
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

<!-- ============ Tier B: blocking task overlay ============ -->
<!-- Sits on top of everything while a long-running action (multi-plant
     water) is in progress, OR while the robot is in a post-estop
     "needs reset" state. Polled into existence by refreshPiStatus(). -->
<div class="task-overlay" id="taskOverlay" role="dialog" aria-modal="true"
     aria-labelledby="taskLabel" aria-live="polite">
  <div class="task-card" id="taskCard" data-mode="running">
    <!-- Running mode: while a multi-plant action is in flight -->
    <div class="task-running" id="taskRunningPanel">
      <h2 class="task-label" id="taskLabel">Working on it…</h2>
      <p class="task-progress-text" id="taskProgressText">Plant 1 of 1</p>
      <div class="task-bar" aria-hidden="true">
        <div class="task-bar-fill" id="taskBarFill"></div>
      </div>
      <p class="task-current" id="taskCurrent"></p>
      <button class="task-abort" id="taskAbortBtn"
              aria-label="Emergency stop — halt this task now">
        <span class="task-abort-icon" aria-hidden="true"></span>
        EMERGENCY STOP
      </button>
      <p class="task-foot">The robot will stop within a second.</p>
    </div>
    <!-- Stopped mode: after a stop, asks the operator to clear the latch -->
    <div class="task-stopped" id="taskStoppedPanel" hidden>
      <h2 class="task-label stopped" id="taskStoppedTitle">Robot stopped</h2>
      <p class="task-stopped-sub" id="taskStoppedSub">
        The robot is held still until you reset it.
      </p>
      <button class="task-reset" id="taskResetBtn"
              aria-label="Reset the robot and continue using GrowMate">
        <span class="task-reset-icon" aria-hidden="true">↻</span>
        RESET — READY TO CONTINUE
      </button>
      <button class="task-stopped-stay" id="taskStoppedStayBtn"
              aria-label="Leave the robot stopped for now">
        Leave it stopped for now
      </button>
    </div>
  </div>
</div>

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

<!-- ============ Day 5: Confirm modal ============ -->
<div class="confirm-overlay" id="confirmModal" role="dialog"
     aria-modal="true" aria-labelledby="confirmText" aria-hidden="true">
  <div class="confirm-card">
    <p class="confirm-text" id="confirmText">Should I do that?</p>
    <div class="confirm-actions">
      <button class="confirm-no" id="confirmNo" aria-label="Cancel">No</button>
      <button class="confirm-yes" id="confirmYes" aria-label="Yes, go ahead">Yes</button>
    </div>
    <p class="confirm-hint">Tap Yes to proceed or No to cancel. Auto-cancels in 10 seconds.</p>
  </div>
</div>

<!-- ============ Day 9: Plant care card ============ -->
<div class="care-overlay" id="careOverlay" role="dialog"
     aria-modal="true" aria-labelledby="careTitle" aria-hidden="true">
  <div class="care-card">
    <button class="care-close" id="careClose" aria-label="Close">×</button>
    <h2 class="care-title" id="careTitle">Plant</h2>
    <div class="care-badge" id="careBadge" data-tone="ok">All good.</div>
    <ul class="care-facts" id="careFacts"></ul>
    <button class="care-water" id="careWater" aria-label="Water this plant">
      Water this plant
    </button>
    <p class="care-note">
      The pump is connected. Photos and soil sensors are not active on this demo —
      use voice or the quick actions for those when the hardware is added.
    </p>
  </div>
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
    openCareCard(p);                  // Day 9: tap shows the care card
  });
  plantsLayer.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const p = plantAtEvent(e);
    if (!p) return;
    e.preventDefault();
    openCareCard(p);
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
    mapTooltip.textContent = `${p.name} — tap for details`;
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
    const prev = state.micState;
    state.micState = stateName;
    micBtn.dataset.state = stateName;
    micWrap.dataset.state = stateName;
    micBtn.setAttribute('aria-pressed', String(stateName === 'recording'));
    if (stateName === 'idle')       micLabel.textContent = 'Tap to talk';
    if (stateName === 'recording')  micLabel.textContent = 'Listening… tap to stop';
    if (stateName === 'processing') micLabel.textContent = 'Thinking…';
    // Day 6: speak a friendly "thinking" filler when we enter processing
    // — silence feels broken to elderly users. Uses the browser's local
    // SpeechSynthesis so there's no network call. The server's TTS reply
    // will arrive a moment later and play over the top, which is fine.
    if (stateName === 'processing' && prev !== 'processing') sayThinking();
  }

  // --- Day 6: client-side "thinking" filler ---
  const _thinkingPhrases = [
    'Just a moment.',
    'Let me check.',
    'One moment please.',
    'Looking that up.',
    'Hold on a second.',
    'Thinking.',
  ];
  let _lastThinkingIdx = -1;
  function sayThinking() {
    try {
      if (!('speechSynthesis' in window)) return;
      // Don't pile up phrases if one is already in flight.
      if (window.speechSynthesis.speaking || window.speechSynthesis.pending) return;
      // Avoid repeating the same phrase twice in a row.
      let idx = Math.floor(Math.random() * _thinkingPhrases.length);
      if (idx === _lastThinkingIdx) idx = (idx + 1) % _thinkingPhrases.length;
      _lastThinkingIdx = idx;
      const u = new SpeechSynthesisUtterance(_thinkingPhrases[idx]);
      u.rate = 0.95;     // slightly slower for clarity
      u.volume = 0.65;   // quieter than the main TTS so it doesn't compete
      window.speechSynthesis.speak(u);
    } catch (_) { /* speech not supported — silent fallback */ }
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
    // Day 5: propagate confirm state if present (lifted to result or in position)
    const requiresConfirm = result.requires_confirm === true || pos.requires_confirm === true;
    return {
      said,
      did,
      say,
      success: !data.error,
      requiresConfirm,
      confirmId: result.confirm_id || pos.confirm_id || null,
      confirmQuestion: result.confirm_question || pos.confirm_question || null,
      confirmTimeoutS: result.confirm_timeout_s || pos.confirm_timeout_s || 10,
    };
  }

  // --- Day 5: confirmation modal ---------------------------------------
  let _confirmTimer = null;
  let _confirmActiveId = null;
  let _confirmTtsBackend = 'kokoro';

  function showConfirmModal(question, confirmId, timeoutS) {
    _confirmActiveId = confirmId;
    const modal = document.getElementById('confirmModal');
    document.getElementById('confirmText').textContent = question || 'Should I do that?';
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    document.getElementById('confirmYes').focus();
    clearTimeout(_confirmTimer);
    _confirmTimer = setTimeout(() => {
      if (_confirmActiveId === confirmId) {
        respondConfirm(false);
        showToast('Cancelled (no response)', 'warn');
      }
    }, (timeoutS || 10) * 1000);
  }

  function hideConfirmModal() {
    const modal = document.getElementById('confirmModal');
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    _confirmActiveId = null;
    clearTimeout(_confirmTimer);
  }

  async function respondConfirm(yes) {
    const cid = _confirmActiveId;
    hideConfirmModal();
    if (!cid) return;
    setMic('processing');
    try {
      const r = await fetch('/api/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_id: cid, confirmed: yes, tts: _confirmTtsBackend, enable_tts: 'true' }),
      });
      const data = await r.json();
      if (data.tts_audio_b64) {
        try { new Audio('data:audio/wav;base64,' + data.tts_audio_b64).play().catch(() => {}); } catch (_) {}
      }
      const norm = adaptVoiceResponse(data);
      finishCommand(norm, yes ? null : 'Cancelled');
    } catch (e) {
      showToast('Confirm failed: ' + e.message, 'warn');
      setMic('idle');
    }
  }

  // --- Day 9: plant care card -----------------------------------------
  const careOverlay = document.getElementById('careOverlay');
  const careTitle   = document.getElementById('careTitle');
  const careBadge   = document.getElementById('careBadge');
  const careFacts   = document.getElementById('careFacts');
  const careWater   = document.getElementById('careWater');
  const careClose   = document.getElementById('careClose');
  let _careCurrent = null;

  function _factRow(k, v) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="k">${escapeHTML(k)}</span><span class="v">${escapeHTML(v)}</span>`;
    return li;
  }

  function _renderCareCard(plant, detail) {
    // plant is the map dict; detail is the /api/plants/{idx} response (may be
    // null if the Pi proxy failed).
    careTitle.textContent = plant.name || 'Plant';
    const state = (detail && detail.state) || {};
    const flagged = state.attention_flag === true;
    careBadge.textContent = flagged
      ? (state.attention_reason || 'Needs water.')
      : 'All good.';
    careBadge.dataset.tone = flagged ? 'warn' : 'ok';

    careFacts.innerHTML = '';
    if (state.last_watered_human) {
      const src = state.last_watered_source === 'water_all'
        ? ' (from watering all)' : '';
      careFacts.appendChild(_factRow('Last watered', state.last_watered_human + src));
    } else {
      careFacts.appendChild(_factRow('Last watered', 'No record yet'));
    }
    if (plant.species) careFacts.appendChild(_factRow('Species', String(plant.species).replace(/_/g, ' ')));
    if (plant.stage)   careFacts.appendChild(_factRow('Stage', plant.stage));
    if (typeof plant.x === 'number')
      careFacts.appendChild(_factRow('Position', `x ${Math.round(plant.x)} mm  ·  y ${Math.round(plant.y)} mm`));
    if (plant.water_quantity)
      careFacts.appendChild(_factRow('Each watering', `${plant.water_quantity} seconds`));
  }

  async function openCareCard(plant) {
    _careCurrent = plant;
    // Render with what we already have so the modal feels instant,
    // then enrich once the Pi answers.
    _renderCareCard(plant, null);
    careOverlay.classList.add('show');
    careOverlay.setAttribute('aria-hidden', 'false');
    careWater.focus();

    // Lookup index — the /plants list provides it; older mock data may not.
    const idx = plant.index;
    if (!idx) return;
    try {
      const r = await fetch(`/api/plants/${idx}`);
      if (!r.ok) return;
      const detail = await r.json();
      if (_careCurrent === plant) _renderCareCard(plant, detail);
    } catch (_) { /* keep the offline rendering */ }
  }

  function closeCareCard() {
    careOverlay.classList.remove('show');
    careOverlay.setAttribute('aria-hidden', 'true');
    _careCurrent = null;
  }

  careClose.addEventListener('click', closeCareCard);
  careOverlay.addEventListener('click', (e) => {
    if (e.target.id === 'careOverlay') closeCareCard();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && careOverlay.classList.contains('show')) closeCareCard();
  });
  careWater.addEventListener('click', () => {
    const p = _careCurrent;
    closeCareCard();
    if (!p) return;
    // Use the species (raw name like "Lettuce_little_gem") if available,
    // falls back to the UI type ("tomato" / "lettuce"). Strip underscores
    // so the LLM hears natural words.
    const target = (p.species || p.type || '').replace(/_/g, ' ');
    sendCommand(`Water the ${target}`);
  });

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

  // ----- Day 5: confirm modal buttons -----
  document.getElementById('confirmYes').addEventListener('click', () => respondConfirm(true));
  document.getElementById('confirmNo').addEventListener('click', () => respondConfirm(false));
  // Esc / outside-click cancels the confirm too
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _confirmActiveId) respondConfirm(false);
  });
  document.getElementById('confirmModal').addEventListener('click', (e) => {
    if (e.target.id === 'confirmModal') respondConfirm(false);
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
    // Day 5: intercept confirm-required responses BEFORE rendering as a normal action.
    if (res?.requiresConfirm && res?.confirmId) {
      if (!override) addChatBubble('you', 'You said', said);
      addChatBubble('bot', 'GrowMate', res.confirmQuestion || 'Should I proceed?');
      showConfirmModal(res.confirmQuestion, res.confirmId, res.confirmTimeoutS);
      setMic('idle');
      return;
    }
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
    // Day 10: any successful action might change the attention list —
    // a watered plant clears, a long wait could push others overdue.
    // Cheap call; doesn't block the bubble render.
    if (res?.success !== false) refreshTodayCare();
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

  // ============ Tier B: blocking task overlay ============
  // Polls /api/pi_status at 1 Hz so the user sees "Plant 3 of 8" updating
  // in real time during a multi-plant water. Two modes:
  //   - "running":  big progress + EMERGENCY STOP button (interactable)
  //   - "stopped":  big RESET button after a stop, plus a "stay stopped" link
  // Also announces task milestones via the browser's SpeechSynthesis so
  // the elderly user hears "Watering 3 marigolds" before motion starts
  // and the per-plant label as each plant comes up.
  const taskOverlay         = $('taskOverlay');
  const taskCard            = $('taskCard');
  const taskRunningPanel    = $('taskRunningPanel');
  const taskStoppedPanel    = $('taskStoppedPanel');
  const taskLabelEl         = $('taskLabel');
  const taskProgressText    = $('taskProgressText');
  const taskBarFill         = $('taskBarFill');
  const taskCurrent         = $('taskCurrent');
  const taskAbortBtn        = $('taskAbortBtn');
  const taskResetBtn        = $('taskResetBtn');
  const taskStoppedStayBtn  = $('taskStoppedStayBtn');
  const taskStoppedTitle    = $('taskStoppedTitle');
  const taskStoppedSub      = $('taskStoppedSub');

  let _taskLastRevision  = -1;
  let _taskLastLabel     = "";
  let _taskLastCurLabel  = "";
  let _taskLastMode      = "";   // 'running' / 'stopped' / 'idle'
  let _taskUserDismissed = false;  // user clicked "Leave it stopped"

  function _speak(text) {
    // Browser-side TTS for status announcements. Picked over Kokoro
    // because per-plant updates need to be instant; a 300 ms HTTP round
    // trip per plant feels laggy. Best-effort: silently skip if the
    // browser doesn't support synth.
    try {
      const synth = window.speechSynthesis;
      if (!synth || !text) return;
      synth.cancel();   // drop any in-flight phrase — newer is better
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.0; u.pitch = 1.0; u.volume = 0.85;
      synth.speak(u);
    } catch (_) { /* no synth, fall through silently */ }
  }

  function _openOverlay() {
    if (!taskOverlay.classList.contains('is-open')) {
      taskOverlay.classList.add('is-open');
      if (typeof app !== 'undefined') app.setAttribute('aria-hidden', 'true');
    }
  }
  function _closeOverlay() {
    if (taskOverlay.classList.contains('is-open')) {
      taskOverlay.classList.remove('is-open');
      if (typeof app !== 'undefined') app.setAttribute('aria-hidden', 'false');
    }
  }

  function _showRunningPanel() {
    taskCard.setAttribute('data-mode', 'running');
    taskRunningPanel.hidden = false;
    taskStoppedPanel.hidden = true;
    if (taskAbortBtn) taskAbortBtn.disabled = false;
  }
  function _showStoppedPanel() {
    taskCard.setAttribute('data-mode', 'stopped');
    taskRunningPanel.hidden = true;
    taskStoppedPanel.hidden = false;
    if (taskResetBtn) taskResetBtn.disabled = false;
  }

  function _renderTaskOverlay(task) {
    if (!task) { _closeOverlay(); _taskLastMode = 'idle'; return; }

    const running = !!task.task_active;
    const stopped = !running && !!task.estop_requested;

    if (running) {
      // Switch to running panel
      if (_taskLastMode !== 'running') {
        _showRunningPanel();
        _taskUserDismissed = false;   // new task: reset the dismissed flag
        _taskLastMode = 'running';
      }
      const cur = task.current_step || 0;
      const tot = task.total_steps || 0;
      const pct = tot > 0 ? Math.min(100, Math.max(0, Math.round((cur / tot) * 100))) : 0;
      const label = task.task_label || 'Working on it…';
      const curLabel = task.current_label || '';

      taskLabelEl.textContent      = label;
      taskProgressText.textContent = tot > 0 ? `Plant ${cur} of ${tot}` : 'Starting…';
      taskBarFill.style.width      = pct + '%';
      taskCurrent.textContent      = curLabel;

      _openOverlay();

      // Pre-announcement: speak the task label the first time we see it.
      if (label && label !== _taskLastLabel) {
        _taskLastLabel = label;
        _speak(label + '.');
      }
      // Per-plant status: speak each time the current label changes.
      if (curLabel && curLabel !== _taskLastCurLabel) {
        _taskLastCurLabel = curLabel;
        // Strip the trailing "(3/8)" from the snapshot so the spoken
        // phrase reads naturally as "Plant 3 of 8: Tomato hash 34".
        const cleanCur = curLabel.replace(/\s*\(\d+\/\d+\)\s*$/, '');
        _speak(`Plant ${cur} of ${tot}.`);
      }
      return;
    }

    if (stopped && !_taskUserDismissed) {
      if (_taskLastMode !== 'stopped') {
        _showStoppedPanel();
        _speak('Robot stopped. Press reset to continue.');
        _taskLastMode = 'stopped';
      }
      _openOverlay();
      return;
    }

    // No task active, no estop latch, OR user dismissed: close.
    // If we're transitioning from "running" -> "idle" without an estop
    // latch having been set, that's a clean natural completion. Speak
    // a quick "All done" via the browser TTS so the user gets snappy
    // feedback while the Kokoro-synthesised summary ("Done watering 3
    // marigolds.") makes its way back through /intent. The Kokoro
    // line still plays, just a beat later.
    if (_taskLastMode === 'running' && !task.estop_requested) {
      _speak('All done.');
    }
    _closeOverlay();
    _taskLastMode = 'idle';
    _taskLastLabel = '';
    _taskLastCurLabel = '';
  }

  async function refreshPiStatus() {
    try {
      const r = await fetch('/api/pi_status');
      if (!r.ok) return;
      const j = await r.json();
      const task = j?.task;
      if (!task) return;
      const rev = task.revision != null
        ? task.revision
        : (task.task_active ? 1 : 0);
      // Always re-evaluate when the overlay is open so we catch the
      // running -> stopped transition. Skip a render only when nothing
      // has changed AND the overlay is already closed.
      if (rev !== _taskLastRevision
          || taskOverlay.classList.contains('is-open')) {
        _taskLastRevision = rev;
        _renderTaskOverlay(task);
      }
    } catch (_) { /* offline / Pi down: leave overlay alone */ }
  }

  if (taskAbortBtn) {
    taskAbortBtn.addEventListener('click', async () => {
      taskAbortBtn.disabled = true;
      taskAbortBtn.style.transform = 'scale(.97)';
      try {
        await fetch('/api/estop', { method: 'POST' });
      } catch (_) { /* Pi unreachable: still flip the local UI */ }
      setTimeout(() => { taskAbortBtn.style.transform = ''; }, 400);
      // Don't optimistically close — let the next 1 Hz poll switch us
      // into the "stopped" panel so the user has a clear reset CTA.
    });
  }
  if (taskResetBtn) {
    // Holds the original label so we can restore it after the
    // visible "Resetting…" / "Robot ready" transition. Captured the
    // first time the button is clicked.
    let _resetBtnOriginalHTML = null;
    taskResetBtn.addEventListener('click', async () => {
      if (_resetBtnOriginalHTML === null) _resetBtnOriginalHTML = taskResetBtn.innerHTML;
      taskResetBtn.disabled = true;
      taskResetBtn.innerHTML =
        '<span class="task-reset-icon" aria-hidden="true">…</span>RESETTING…';
      taskStoppedSub.textContent = 'Clearing the safety stop.';
      _speak('Resetting the robot.');
      let ok = false;
      try {
        const r = await fetch('/api/reset', { method: 'POST' });
        ok = r && r.ok;
      } catch (_) { ok = false; }
      if (ok) {
        // Visible "it actually worked" confirmation before we close so
        // the user isn't left wondering whether the press registered.
        taskResetBtn.innerHTML =
          '<span class="task-reset-icon" aria-hidden="true">✓</span>ROBOT READY';
        taskStoppedSub.textContent = 'Safety stop cleared. You can speak the next command.';
        _speak('Robot ready. You can speak the next command.');
        // Give the user a beat to read it before we close.
        setTimeout(() => {
          taskResetBtn.innerHTML = _resetBtnOriginalHTML;
          taskResetBtn.disabled = false;
          taskStoppedSub.textContent = 'The robot is held still until you reset it.';
          _closeOverlay();
          _taskLastMode = 'idle';
        }, 1400);
      } else {
        // Reset call failed - keep the overlay open so the user can
        // try again, and surface the failure clearly.
        taskResetBtn.innerHTML = _resetBtnOriginalHTML;
        taskResetBtn.disabled = false;
        taskStoppedSub.textContent =
          "Reset didn't take. Make sure the Pi is reachable, then try again.";
        _speak('Reset did not take. Please try again.');
      }
    });
  }
  if (taskStoppedStayBtn) {
    taskStoppedStayBtn.addEventListener('click', () => {
      // User explicitly chose to leave the robot stopped — close the
      // overlay and don't re-open it on the next poll until either
      // (a) reset is pressed elsewhere, or (b) a new task starts.
      _taskUserDismissed = true;
      _closeOverlay();
      _taskLastMode = 'idle';
    });
  }

  refreshPiStatus();
  setInterval(refreshPiStatus, 1000);

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

  // --- Day 10: Today's care --------------------------------------------
  const todayCard         = document.getElementById('todayCard');
  const todaySummary      = document.getElementById('todaySummary');
  const todaySummaryText  = document.getElementById('todaySummaryText');
  const todayList         = document.getElementById('todayList');

  function _openCareCardByIndex(idx) {
    // Try the live PLANTS list first (loaded from /api/plants)
    const local = PLANTS.find(p => p && p.index === idx);
    if (local) { openCareCard(local); return; }
    // Fall back to a minimal stub so the card still opens and enriches on its own
    openCareCard({ index: idx, name: `Plant #${idx}`, type: 'lettuce' });
  }

  function _renderTodayCare(data) {
    const overdue = (data && data.plants) || [];
    const n = overdue.length;
    if (n === 0) {
      todaySummary.dataset.tone = 'ok';
      todaySummary.setAttribute('aria-expanded', 'false');
      todaySummary.disabled = true;
      todaySummaryText.textContent = 'All plants are watered. Nothing waiting on you.';
      todayList.hidden = true;
      todayList.innerHTML = '';
      return;
    }
    todaySummary.dataset.tone = 'warn';
    todaySummary.disabled = false;
    todaySummaryText.textContent =
      n === 1 ? '1 plant needs watering' : `${n} plants need watering`;

    todayList.innerHTML = '';
    for (const item of overdue.slice(0, 50)) {
      const state = item.state || {};
      const li = document.createElement('li');
      li.tabIndex = 0;
      li.setAttribute('role', 'button');
      const reason = state.attention_reason || 'Needs water.';
      li.innerHTML =
        `<span class="t-name">${escapeHTML(item.name || ('Plant #' + item.index))}</span>` +
        `<span class="t-reason">${escapeHTML(reason)}</span>`;
      li.addEventListener('click', () => _openCareCardByIndex(item.index));
      li.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          _openCareCardByIndex(item.index);
        }
      });
      todayList.appendChild(li);
    }
  }

  // Day 10 today's-care panel parked — keep the function as a no-op so
  // any caller (finishCommand, init) stays a single line and is easy to
  // re-arm once the event-log verify gate lands.
  const MEMORY_FEATURES_ENABLED = false;
  async function refreshTodayCare() {
    if (!MEMORY_FEATURES_ENABLED) return;
    try {
      const r = await fetch('/api/plants/needs_attention');
      if (!r.ok) return;
      const data = await r.json();
      _renderTodayCare(data);
    } catch (_) { /* offline: leave the previous render in place */ }
  }

  todaySummary.addEventListener('click', () => {
    if (todaySummary.disabled) return;
    const open = todaySummary.getAttribute('aria-expanded') === 'true';
    todaySummary.setAttribute('aria-expanded', String(!open));
    todayList.hidden = open;
  });

  // ----- Init -----
  renderPlants();
  renderState();
  renderHistory();
  fetchPlantsFromBackend();   // override with active_map.yaml plants if available
  refreshTodayCare();
  if (MEMORY_FEATURES_ENABLED) {
    setInterval(refreshTodayCare, 5 * 60 * 1000);   // re-check every 5 min
  }
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
    parser.add_argument(
        "--whisper-model",
        default="small.en",
        help=("Whisper model size: tiny.en (fastest) | base.en | small.en "
              "(default, recommended for elderly speech) | medium.en | "
              "large-v3 (most accurate, slowest)"),
    )
    args = parser.parse_args(argv)

    _STATE.model = args.model
    _STATE.ollama_url = args.ollama_url
    _STATE.whisper_model = args.whisper_model

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
