"""FastAPI app — receives intent JSON from a client, runs the BT, replies.

This is the only HTTP service on the Pi. It owns:

* A singleton ``FarmBotROS2Bridge`` (real or sim) that all trees publish through.
* A singleton ``GardenConfig`` loaded from ``config/farmbot.yaml``.

Endpoints:

* ``GET  /status``  - liveness + bridge mode (sim vs real)
* ``POST /intent``  - the main path; body is ``IntentRequest`` JSON
* ``POST /estop``   - bypass everything, publish ``e`` directly
* ``POST /reset_estop`` - publish ``E`` (operator releases the stop)
* ``GET  /history`` - last N commands published (debugging)

Run with:

    python -m growmate_pi.intent_server --no-ros2   # sim mode
    python -m growmate_pi.intent_server             # real ROS2 on Pi
"""

from __future__ import annotations

import argparse
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml as _yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from growmate_pi.bt.builder import build_tree
from growmate_pi.bt.executor import execute_tree, read_tts_text
from growmate_pi.event_log import DEFAULT_DB_PATH, EventLog
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import (
    SCHEMA_VERSION,
    Intent,
    IntentRequest,
    IntentResponse,
    TreeResult,
)
from growmate_pi.task_state import get_task_state
from growmate_pi.tool_state import get_tool_state


DEFAULT_CONFIG = (
    Path(__file__).parent / "config" / "farmbot.yaml"
).resolve()


# Map upstream plant_name strings to the UI's plant type keys (which control colour).
# Keep in sync with growmate_voice.app:_PLANT_TYPE_MAP — both layers want the same
# colour palette regardless of which one reads the map.
_PLANT_TYPE_MAP: Dict[str, str] = {
    # Edibles
    "tomato": "tomato",
    "lettuce_little_gem": "lettuce", "lettuce": "lettuce",
    "scallion": "scallion", "spring_onion": "scallion", "green_onion": "scallion",
    "mixed pepper": "pepper", "mixed_pepper": "pepper", "pepper": "pepper",
    # Herbs
    "basil": "basil",
    "spearmint": "spearmint", "mint": "spearmint",
    # Flowers
    "marigold": "marigold",
    "lily": "lily", "asiatic_lily": "lily",
    "geranium": "geranium", "pelargonium": "geranium",
    "cardinal flower": "cardinal", "cardinal_flower": "cardinal",
    "dianthus": "dianthus", "carnation": "dianthus", "sweet_william": "dianthus",
    "euonymus": "euonymus",
    "petunia": "petunia",
    "begonia": "begonia",
}


def _installed_map_path() -> Optional[Path]:
    """Locate the active_map.yaml the running map_handler is actually using.

    Preferred location is the AURA install share directory (what's actually
    loaded by the running map_controller). Falls back to the repo's source
    copy so this also works when ament_index_python isn't available (tests
    on Windows / WSL without colcon build).
    """
    try:
        from ament_index_python.packages import get_package_share_directory
        share = Path(get_package_share_directory("map_handler")) / "config"
        for name in ("active_map.yaml", "map_references.yaml"):
            candidate = share / name
            if candidate.exists():
                return candidate
    except Exception:
        pass
    # Fallback: walk up from this file to repo root, then into the source tree
    here = Path(__file__).resolve()
    repo_src = here.parents[1]  # src/
    for sub in (
        repo_src / "map_handler" / "map_handler" / "config" / "active_map.yaml",
        repo_src / "map_handler" / "map_handler" / "config" / "map_references.yaml",
    ):
        if sub.exists():
            return sub
    return None


def _load_plants_from_map_handler() -> Dict[str, Any]:
    """Read the AURA active_map.yaml and project it into the UI's plant shape."""
    path = _installed_map_path()
    if path is None:
        return {"plants": [], "count": 0, "source": None,
                "error": "map_handler share directory not found"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
    except Exception as exc:
        return {"plants": [], "count": 0, "source": str(path), "error": str(exc)}

    ref = data.get("map_reference", {}) or {}
    plants_raw = (data.get("plant_details", {}) or {}).get("plants", {}) or {}
    plants_out: List[Dict[str, Any]] = []
    for _, p in plants_raw.items():
        idents = p.get("identifiers", {}) or {}
        details = p.get("plant_details", {}) or {}
        pos = p.get("position", {}) or {}
        status = p.get("status", {}) or {}
        raw_name = str(idents.get("plant_name", "Unknown"))
        ptype = _PLANT_TYPE_MAP.get(raw_name.lower(), "lettuce")
        display = raw_name.replace("_", " ").title()
        idx = idents.get("index", len(plants_out) + 1)
        plants_out.append({
            "index": int(idx),
            "type": ptype,
            "species": raw_name,
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
        "workspace": {
            "x_len": ref.get("x_len", 5691.2),
            "y_len": ref.get("y_len", 2734.0),
        },
        "source": str(path),
    }


# --- Tier B: species-resolution helpers for the multi-plant water tree -------
# Used by build_tree / _tree_water so "water the lettuces" can walk all 8
# instances in the active map. Plural / alias / case folding is done once here
# so the BT side stays a thin "give me the plants" caller.


def _species_forms(target: str) -> set:
    """All lowercase forms of a target string a plant entry might match.

    Mirrors the Day 11 / fast-path normalisation: 'tomatoes' -> {'tomatoes',
    'tomato', 'tomatoe'}, 'lilies' -> {'lilies', 'lily', 'lili', 'lilie'}.
    The active_map stores species like 'Lettuce_little_gem' or 'Mixed Pepper'
    so we match by substring against both the raw species name and the
    UI-projected type slug.
    """
    t = (target or "").lower().strip().rstrip("?.,").strip()
    if not t:
        return set()
    forms = {t}
    if t.endswith("ies") and len(t) > 3:
        forms.add(t[:-3] + "y")
    if t.endswith("es") and len(t) > 3:
        forms.add(t[:-2])
    if t.endswith("s") and len(t) > 1:
        forms.add(t[:-1])
    # Common LLM phrasings — "the lettuce", "lettuce bed" etc. shouldn't
    # match here but we tolerate a trailing word the LLM might emit.
    return forms


# Plants within this many mm of X are treated as one column. Bigger than the
# within-column X scatter, smaller than the gap between columns. Tune to layout.
_BAND_TOL_MM = 150.0


def _snake_order(plants: List[Dict[str, Any]],
                 band_tol: float = _BAND_TOL_MM) -> List[Dict[str, Any]]:
    """Order plants for the quickest deterministic gantry sweep (boustrophedon).

    Band the plants into X-columns (a new column starts when X jumps by more
    than ``band_tol``), walk the columns left-to-right, and sweep Y within each
    column — alternating the Y direction every column ("snake"). The heavy X
    axis makes a single monotonic pass while the light Y axis does the fanning,
    and because direction flips each column the gantry enters every column at
    the end nearest where it left the previous one (no full-height Y returns).
    """
    if not plants:
        return []
    by_x = sorted(plants, key=lambda p: (p["x"], p["y"]))
    out: List[Dict[str, Any]] = []
    band: List[Dict[str, Any]] = []
    last_x = None
    descending = False  # first column sweeps Y ascending, then alternate
    for p in by_x:
        if last_x is not None and p["x"] - last_x > band_tol:
            band.sort(key=lambda q: q["y"], reverse=descending)
            out.extend(band)
            band = []
            descending = not descending
        band.append(p)
        last_x = p["x"]
    band.sort(key=lambda q: q["y"], reverse=descending)
    out.extend(band)
    return out


def find_plants_by_species(target: str) -> List[Dict[str, Any]]:
    """Return all active_map plants whose species/type/name matches ``target``.

    Ordered by ``_snake_order`` (band-by-X, snaked-Y) for the quickest sweep.
    Empty list if no plants match (caller decides whether that's a no-match
    failure tree or a different fallback).
    """
    forms = _species_forms(target)
    if not forms:
        return []
    data = _load_plants_from_map_handler()
    plants = data.get("plants") or []
    matches: List[Dict[str, Any]] = []
    for p in plants:
        species = (p.get("species") or "").lower()
        ptype = (p.get("type") or "").lower()
        pname = (p.get("name") or "").lower()
        if any(f in species or f in ptype or f in pname for f in forms):
            matches.append(p)
    return _snake_order(matches)


def find_all_plants_in_garden() -> List[Dict[str, Any]]:
    """Return every plant in the active map, snake-ordered for the fastest walk.

    Used by the multi-plant ``water_all`` tree so "water everything" walks each
    plant individually (per-leaf event-log writes + estop checkpoints) rather
    than firing the firmware-level P_4 one-shot. Order is ``_snake_order``
    (band-by-X column, alternating Y direction).
    """
    data = _load_plants_from_map_handler()
    plants = list(data.get("plants") or [])
    return _snake_order(plants)


def _camera_handler_config(filename: str) -> Optional[Path]:
    """Locate a file in the camera_handler config dir (install share or src)."""
    try:
        from ament_index_python.packages import get_package_share_directory
        cand = Path(get_package_share_directory("camera_handler")) / "config" / filename
        if cand.exists():
            return cand
    except Exception:
        pass
    here = Path(__file__).resolve()
    cand = here.parents[1] / "camera_handler" / "camera_handler" / "config" / filename
    return cand if cand.exists() else None


def find_weeds() -> List[Dict[str, Any]]:
    """Weed coordinates (robot mm) from the last detection (other_plants.yaml).

    Weed detection (I_4) appends circles ``[[x, y], radius, is_known(False),
    within100]`` in robot coordinates. Returns ``[{x, y, radius}]`` snake-ordered
    for the shortest removal pass. Empty if no detection has run yet — the
    clear_weeds tree then asks the user to scan first.
    """
    path = _camera_handler_config("other_plants.yaml")
    if path is None:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or []
    except Exception:
        return []
    weeds: List[Dict[str, Any]] = []
    for entry in data:
        try:
            (cx, cy), radius = entry[0], entry[1]
            weeds.append({"x": float(cx), "y": float(cy), "radius": float(radius)})
        except Exception:
            continue
    return _snake_order(weeds)


def find_detected_plants() -> List[Dict[str, Any]]:
    """Every detected canopy (robot mm) from the last scan, deduped + snake-ordered.

    Reads the plant-detection output (other_plants.yaml + known_plants.yaml,
    written by I_4) and merges them — when building a map every green circle is a
    candidate plant. Dedups detections that overlap (same plant seen in adjacent
    camera FOVs). Empty if no scan has run.
    """
    out: List[Dict[str, Any]] = []
    for fname in ("other_plants.yaml", "known_plants.yaml"):
        path = _camera_handler_config(fname)
        if path is None:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or []
        except Exception:
            continue
        for entry in data:
            try:
                (cx, cy), radius = entry[0], entry[1]
                out.append({"x": float(cx), "y": float(cy), "radius": float(radius)})
            except Exception:
                continue
    deduped: List[Dict[str, Any]] = []
    for p in out:
        r = max(p["radius"], 50.0)
        if not any((p["x"] - q["x"]) ** 2 + (p["y"] - q["y"]) ** 2 < r * r
                   for q in deduped):
            deduped.append(p)
    return _snake_order(deduped)


def clear_detections() -> None:
    """Delete the previous scan's detection files so a new scan starts fresh."""
    for fname in ("other_plants.yaml", "known_plants.yaml"):
        path = _camera_handler_config(fname)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass


# --- Voice plant-labelling: staged ("pending") detections between find_plants
# and label_plants. Per-Pi state (not in git), inspectable. -------------------
_PENDING_PLANTS_PATH = Path.home() / ".growmate_pi" / "pending_plants.yaml"


def read_pending_plants() -> List[Dict[str, Any]]:
    try:
        with open(_PENDING_PLANTS_PATH, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or []
    except Exception:
        return []


def write_pending_plants(plants: List[Dict[str, Any]]) -> None:
    _PENDING_PLANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PENDING_PLANTS_PATH, "w", encoding="utf-8") as f:
        _yaml.safe_dump(list(plants), f)


def filter_plants_by_region(plants: List[Dict[str, Any]], region: Optional[str],
                            workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Select pending plants by a spoken region word. None/'all' -> everything."""
    r = (region or "").lower().strip()
    if not r or r in ("all", "everything", "the rest", "rest", "them", "the lot"):
        return list(plants)
    x_max = float(workspace.get("x_max", 5691.2))
    y_max = float(workspace.get("y_max", 2734.0))
    if any(k in r for k in ("left", "leftmost")):
        return [p for p in plants if p["x"] < x_max / 3.0]
    if any(k in r for k in ("right", "rightmost")):
        return [p for p in plants if p["x"] > 2.0 * x_max / 3.0]
    if any(k in r for k in ("middle", "centre", "center")):
        return [p for p in plants if x_max / 3.0 <= p["x"] <= 2.0 * x_max / 3.0]
    if "front" in r:
        return [p for p in plants if p["y"] < y_max / 2.0]
    if "back" in r:
        return [p for p in plants if p["y"] >= y_max / 2.0]
    return []  # unknown region word -> nothing; caller reports


def list_species_in_garden() -> List[str]:
    """All distinct plant species/type slugs currently in the active map.

    Used by the Windows side to detect "water all the lettuces" - style
    phrasings where the LLM emitted ``water_all`` but the user actually
    named a specific species. Order: ascending by frequency-descending,
    so the most common species come first (deterministic when ties).
    """
    data = _load_plants_from_map_handler()
    plants = data.get("plants") or []
    seen: Dict[str, int] = {}
    for p in plants:
        for key in ("type", "species"):
            v = (p.get(key) or "").lower().strip()
            if v:
                seen[v] = seen.get(v, 0) + 1
    return sorted(seen.keys(), key=lambda s: (-seen[s], s))


# ---------- Module-level singletons (populated by ``build_app``) -------------


_bridge: Optional[FarmBotROS2Bridge] = None
_garden: Optional[GardenConfig] = None
_event_log: Optional[EventLog] = None


# ---------- Async intent execution -------------------------------------------
# The tick-and-verify gate makes a tree run for as long as the firmware takes
# (a multi-plant water is minutes). Holding the HTTP request open that long
# trips client timeouts, so /intent ticks the tree on a background thread and
# returns a task_id; the client polls /intent_status/{task_id} for the terminal
# result. A short grace wait lets quick trees still return inline (one trip).
# Only one tree runs at a time — a new non-emergency intent is refused while a
# tree is in flight (preserves the old one-at-a-time execution semantics).

_INTENT_GRACE_S = 8.0          # return inline if the tree finishes this fast
_INTENT_RESULT_TTL_S = 900.0   # keep finished results this long for late polls

_intent_lock = threading.Lock()
_intent_results: Dict[str, Dict[str, Any]] = {}   # task_id -> {"resp": dict, "ts": float}
_intent_running: Optional[str] = None             # task_id of the in-flight tree


def _prune_intent_results_locked() -> None:
    """Drop finished results older than the TTL. Call with _intent_lock held."""
    now = time.time()
    stale = [k for k, v in _intent_results.items()
             if now - v.get("ts", 0) > _INTENT_RESULT_TTL_S]
    for k in stale:
        _intent_results.pop(k, None)


def _execute_intent_tree(req: IntentRequest, task_id: str) -> IntentResponse:
    """Build, tick to completion, and return the terminal IntentResponse.

    Runs on the background worker thread (or inline within the grace window).
    Mirrors the old synchronous /intent body so behaviour is unchanged apart
    from where it runs.
    """
    bridge = _require_bridge()
    garden = _require_garden()
    task_state = get_task_state()

    commands_before = list(bridge.command_log)
    t0 = time.monotonic()
    root = build_tree(bridge, garden, req.intents, emergency=req.emergency)
    try:
        tree_result = execute_tree(root)
    finally:
        # Guarantee task_state.end() runs even when the tree fails mid-sequence
        # so the UI flips running -> stopped within one /status poll. Idempotent.
        if task_state.is_active():
            task_state.end()
    duration_ms = int((time.monotonic() - t0) * 1000)

    new_commands = [
        r.command for r in bridge.command_log[len(commands_before):]
    ]
    tts = read_tts_text() or " ".join(i.response for i in req.intents)
    status_str = tree_result.status

    # Day 7: append a per-plant event row for every care-action intent that
    # didn't fail outright. Best-effort.
    if not req.emergency:
        for intent_obj in req.intents:
            _log_intent_outcome(intent_obj, status_str, bridge=bridge)

    return IntentResponse(
        status=status_str,
        task_id=task_id,
        tree=tree_result,
        commands_published=new_commands,
        tts_text=tts.strip(),
        duration_ms=duration_ms,
        error=None if status_str == "success" else _summarise_error(tree_result),
    )


def _intent_worker(req: IntentRequest, task_id: str, done: threading.Event) -> None:
    """Background runner: tick the tree, stash the terminal result, clear the
    in-flight flag. Never raises — a failure is stored as a failure response."""
    global _intent_running
    try:
        resp = _execute_intent_tree(req, task_id)
    except Exception as exc:  # never let the worker die silently
        ts = get_task_state()
        if ts.is_active():
            ts.end()
        resp = IntentResponse(
            status="failure",
            task_id=task_id,
            tree=TreeResult(label="intent error", status="failure", node_results=[]),
            tts_text="Something went wrong running that.",
            error=str(exc),
        )
    finally:
        with _intent_lock:
            _prune_intent_results_locked()
            _intent_results[task_id] = {
                "resp": resp.model_dump(mode="json"),
                "ts": time.time(),
            }
            _intent_running = None
        done.set()


def _require_bridge() -> FarmBotROS2Bridge:
    if _bridge is None:
        raise RuntimeError("Bridge not initialised. Call build_app() first.")
    return _bridge


def _require_garden() -> GardenConfig:
    if _garden is None:
        raise RuntimeError("Garden not initialised. Call build_app() first.")
    return _garden


def _lookup_plant_index(target: Optional[str]) -> Optional[int]:
    """Find the active_map index of a plant by name. None if not found."""
    if not target:
        return None
    data = _load_plants_from_map_handler()
    plants = data.get("plants") or []
    target_lc = target.lower().strip()
    for p in plants:
        name = (p.get("name") or "")
        if target_lc in name.lower():
            # name format from /plants: "Tomato #34" — pull the trailing index.
            tail = name.rsplit("#", 1)
            if len(tail) == 2:
                try:
                    return int(tail[1])
                except ValueError:
                    pass
    return None


# Which intent actions get recorded and how they're categorised. Anything
# not in this map is skipped — keeps the log focused on actual care events
# rather than every estop / nav noise.
_INTENT_EVENT_MAP: Dict[str, str] = {
    "water":          "watered",
    "water_all":      "watered_all",
    "check_sensor":   "sensed",
    "check_moisture": "moisture_check",
    "photo":          "photographed",
    "panorama":       "panorama",
    "scan_weeds":     "weed_scan",
    "light_on":       "lights_on",
    "light_off":      "lights_off",
}


# Day 8: per-species "watering overdue" threshold, in days. Tweak per the
# Maynooth garden's actual needs — these are starting points.
_WATER_THRESHOLD_DAYS: Dict[str, float] = {
    "tomato":             2.0,
    "lettuce":            2.0,
    "lettuce_little_gem": 2.0,
    "marigold":           4.0,
    "scallion":           3.0,
    "spring_onion":       3.0,
    "mixed pepper":       3.0,
    "mixed_pepper":       3.0,
    "pepper":             3.0,
    "basil":              3.0,
    "spearmint":          3.0,
    "mint":               3.0,
    "lily":               3.0,
    "geranium":           3.0,
    "pelargonium":        3.0,
    "cardinal flower":    2.0,
    "cardinal_flower":    2.0,
    "dianthus":           4.0,
    "carnation":          4.0,
    "euonymus":           4.0,
    "petunia":            3.0,
    "begonia":            3.0,
}
_DEFAULT_WATER_THRESHOLD_DAYS = 3.0


def _humanise_ts(ts_ms: Optional[int]) -> Optional[str]:
    """Render a unix-epoch-ms timestamp as 'X ago' for elderly-friendly output."""
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


def _days_since(ts_ms: Optional[int]) -> Optional[float]:
    if not ts_ms:
        return None
    delta_s = max(0, time.time() * 1000 - ts_ms) / 1000.0
    return round(delta_s / 86400.0, 2)


def _derive_plant_state(plant: Dict[str, Any]) -> Dict[str, Any]:
    """Compute derived care state for one plant by mining the event log.

    Per-plant 'watered' events take priority over global 'watered_all'
    fallbacks. Both are checked so a plant that was last covered by a
    water_all run isn't flagged as 'never watered'.
    """
    plant_index = plant.get("index")
    species_key = (plant.get("species") or plant.get("type") or "").lower().strip()
    threshold = _WATER_THRESHOLD_DAYS.get(species_key, _DEFAULT_WATER_THRESHOLD_DAYS)

    last_watered_ts: Optional[int] = None
    last_watered_source: Optional[str] = None
    last_photo_ts: Optional[int] = None
    last_sensed: Optional[Dict[str, Any]] = None

    if _event_log is not None and plant_index is not None:
        targeted = _event_log.last_for_plant(plant_index, "watered")
        if targeted:
            last_watered_ts = targeted["ts"]
            last_watered_source = "targeted"

        # If no targeted water, consider the most recent water_all event.
        if last_watered_ts is None:
            recent_all = _event_log.recent(1, event_types=["watered_all"])
            if recent_all:
                last_watered_ts = recent_all[0]["ts"]
                last_watered_source = "water_all"

        photo = _event_log.last_for_plant(plant_index, "photographed")
        if photo:
            last_photo_ts = photo["ts"]

        sensed = _event_log.last_for_plant(plant_index, "sensed")
        if sensed:
            last_sensed = sensed.get("payload", {}) or {}

    days_since_watered = _days_since(last_watered_ts)
    attention_flag = False
    attention_reason: Optional[str] = None
    if days_since_watered is None:
        attention_flag = True
        attention_reason = "Never watered."
    elif days_since_watered > threshold:
        attention_flag = True
        rounded = max(1, int(round(days_since_watered)))
        attention_reason = f"Not watered in {rounded} day{'s' if rounded != 1 else ''}."

    return {
        "last_watered_ts": last_watered_ts,
        "last_watered_human": _humanise_ts(last_watered_ts),
        "last_watered_source": last_watered_source,  # 'targeted' | 'water_all' | None
        "days_since_watered": days_since_watered,
        "water_threshold_days": threshold,
        "last_photo_ts": last_photo_ts,
        "last_photo_human": _humanise_ts(last_photo_ts),
        "last_sensed_moisture": (last_sensed or {}).get("moisture"),
        "last_sensed_payload": last_sensed,
        "attention_flag": attention_flag,
        "attention_reason": attention_reason,
    }


def _log_intent_outcome(intent: Intent, tree_status: str, bridge=None) -> None:
    """Append an event row for one successfully-executed intent.

    Memory contract: a row is ``(action, when, entity, effect)``. The first
    three were always recorded; the EFFECT slot is filled here — for sensing
    intents the actual soil reading (the value ``_derive_plant_state`` reads
    back as ``last_sensed_moisture``, which was silently always None before).
    """
    if _event_log is None or tree_status not in ("success", "partial"):
        return
    event_type = _INTENT_EVENT_MAP.get(intent.action)
    if not event_type:
        return
    plant_name = (intent.target or "").strip() or None
    plant_index = _lookup_plant_index(plant_name) if plant_name else None
    payload: Dict[str, Any] = {}
    if intent.params:
        payload.update({k: v for k, v in intent.params.items() if k in (
            "duration_s", "x", "y", "z", "step_mm", "priority",
        )})
    if intent.action == "water_all" and "plant_count" not in payload:
        data = _load_plants_from_map_handler()
        if data.get("count"):
            payload["plant_count"] = data["count"]
    # Effect: the soil value this sensing pass actually read (pin 59). The
    # ReadSensor leaf already parsed it off /uart_receive into the bridge.
    if bridge is not None and event_type in ("sensed", "moisture_check"):
        try:
            from growmate_pi.bt.action_nodes import SOIL_PIN
            reading = bridge.last_reading(SOIL_PIN)
            if reading is not None:
                payload["moisture"] = reading[0]
        except Exception:
            pass  # effect capture is best-effort, never blocks the row
    try:
        _event_log.log(
            event_type=event_type,
            plant_index=plant_index,
            plant_name=plant_name,
            payload=payload or None,
        )
    except Exception:
        # The event log is best-effort — never let it kill a successful intent.
        pass


def build_app(
    ros2_enabled: bool = True,
    config_path: Optional[Path] = None,
    cors_origins: Optional[list] = None,
    events_db: Optional[Path] = None,
    verify_enabled: bool = True,
) -> FastAPI:
    """Build the FastAPI app. Constructs the bridge and loads the config.

    Wrapping construction in a function (rather than module-level state) lets
    tests build multiple app instances with different config / ros2 flags.

    ``verify_enabled`` turns on the tick-and-verify gate: action nodes that
    publish a move/pump/home wait for the firmware to confirm completion via
    /busy_state before reporting SUCCESS. Off -> legacy fire-and-forget.
    """
    global _bridge, _garden, _event_log
    _bridge = FarmBotROS2Bridge(
        ros2_enabled=ros2_enabled, verify_enabled=verify_enabled
    )
    _garden = GardenConfig(config_path or DEFAULT_CONFIG)
    _event_log = EventLog(events_db or DEFAULT_DB_PATH)

    app = FastAPI(title="GrowMate Pi", version=SCHEMA_VERSION)

    # Boot-time tool preflight (audit F5): ToolState is process memory, so a
    # restart forgets what's physically on the UTM. In real mode, read pin 63
    # once; if a head is seated with no recorded mount, mark the state
    # unknown — EnsureTool then refuses tool work (with spoken guidance)
    # instead of blindly swapping into an occupied bay. Runs in a daemon
    # thread: the D_C answer takes a firmware round-trip and boot shouldn't
    # block on it.
    def _tool_preflight() -> None:
        try:
            rec = _bridge.publish("D_C")
            if rec.status != "sent":
                return
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                reading = _bridge.last_reading(63)
                if reading is not None:
                    if reading[0] < 0.5 and get_tool_state().current() is None:
                        get_tool_state().mark_unknown()
                        print("[growmate_pi] Preflight: pin 63 reads MOUNTED "
                              "but no tool is recorded — tool state set to "
                              "'unknown'; resolve via POST /tool_state or "
                              "hand-unmount before tool commands.")
                    return
                time.sleep(0.25)
        except Exception:
            pass  # preflight is best-effort; never block serving

    if _bridge.ros2_enabled:
        threading.Thread(
            target=_tool_preflight, name="growmate_tool_preflight", daemon=True
        ).start()

    # CORS so a phone-side web app can call us. Wide-open by default; tighten
    # by passing an explicit origin list from the CLI in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/status")
    def status():
        # Tier B: include task_state so the Windows UI can poll this
        # endpoint at 1 Hz and show the blocking overlay during long
        # multi-plant waters.
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "bridge_mode": "ros2" if _bridge.ros2_enabled else "sim",
            "bridge_ready": _bridge.is_ready(),
            "verify_enabled": _bridge.verify_active(),
            "topic": _bridge.topic,
            "config": str((config_path or DEFAULT_CONFIG)),
            "task": get_task_state().snapshot(),
            "position": _bridge.position(),
            "tool": get_tool_state().current(),
        }

    @app.post("/tool_state")
    def set_tool_state(body: Dict[str, Any]):
        """Operator override for the mounted-tool record (audit F5).

        ``{"tool": "watering_nozzle"}`` — declare what is physically on the
        UTM (must be a configured tool name); ``{"tool": null}`` — declare
        the head empty (e.g. after a hand-unmount). Clears the boot-time
        'unknown' refusal.
        """
        tool = body.get("tool")
        if tool is not None:
            tool = str(tool)
            if tool not in _garden.tools_by_name():
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown tool '{tool}' — configured: "
                           f"{sorted(_garden.tools_by_name())}",
                )
            get_tool_state().set(tool)
        else:
            get_tool_state().clear()
        return {"ok": True, "tool": get_tool_state().current()}

    @app.on_event("shutdown")
    def _on_shutdown():
        # Stop the bridge's busy_state spin thread and destroy its node so a
        # restart doesn't leak a spinning thread / duplicate node.
        if _bridge is not None:
            _bridge.shutdown()

    @app.get("/plants/by_species/{target}")
    def plants_by_species(target: str):
        """Tier B helper: how many plants of this species are in the
        active map? Windows side calls this AFTER LLM classification but
        BEFORE building the intent payload, so the soft-confirm gate can
        fire on Q2 (N >= 5). Also returns the matched plant rows so the
        UI can preview "Tomato #34, Tomato #18, …" if it wants to."""
        matches = find_plants_by_species(target)
        return {
            "target": target,
            "count": len(matches),
            "plants": matches,
        }

    @app.get("/plants/species")
    def plants_species():
        """All distinct species slugs in the loaded garden.

        Used by the Windows-side ``water_all -> water target=<species>``
        rewrite to detect when the LLM misclassified a phrasing like
        "water all the lettuces". Cheap enough for the Windows app to
        refresh once per session (or once a minute) rather than per call.
        """
        return {"species": list_species_in_garden()}

    @app.get("/plants")
    def plants():
        """Return the real garden layout this Pi has currently loaded.

        Reads the AURA-installed ``active_map.yaml`` via
        ``ament_index_python`` so the answer always reflects what
        map_handler is actually using — not the repo's source-tree copy.
        Falls back to ``map_references.yaml`` if no active_map is present.
        """
        return _load_plants_from_map_handler()

    # --- Day 8: per-plant detail + needs-attention list ----------------
    # Route order matters in FastAPI: more specific literal paths
    # ("/plants/needs_attention") must be declared BEFORE path-parameter
    # routes ("/plants/{idx}") or the parameter route will swallow them.

    @app.get("/plants/needs_attention")
    def plants_needs_attention(limit: int = 200):
        """List plants whose ``attention_flag`` is true, sorted by urgency.

        Used by the UI's "Today's tasks" panel (Day 10) and by voice queries
        like "what needs my attention?". Plants that have never been watered
        come first; otherwise sorted by days_since_watered descending.
        """
        data = _load_plants_from_map_handler()
        plants_list = data.get("plants") or []
        flagged: List[Dict[str, Any]] = []
        for p in plants_list:
            state = _derive_plant_state(p)
            if state.get("attention_flag"):
                flagged.append({
                    **p,
                    "state": state,
                })
        # Never-watered (None days_since) ranks above "10 days overdue".
        flagged.sort(
            key=lambda r: (
                0 if r["state"].get("days_since_watered") is None else 1,
                -(r["state"].get("days_since_watered") or 0),
            )
        )
        return {
            "plants": flagged[:limit],
            "count": min(len(flagged), limit),
            "total_in_garden": len(plants_list),
        }

    @app.get("/plants/care_summary")
    def plants_care_summary():
        """Compact per-species care digest — the garden MEMORY for inference.

        One line per species: how many plants, how many currently need water,
        when the species was last watered, and the most recent soil reading.
        Small enough to inject into the intent-classifier prompt, so the LLM
        resolves vague speech ("the thirsty ones", "how's the garden") against
        what has actually been DONE and MEASURED — the (action, when, entity,
        effect) log queried per species.
        """
        data = _load_plants_from_map_handler()
        plants_list = data.get("plants") or []
        by_species: Dict[str, Dict[str, Any]] = {}
        for p in plants_list:
            sp = (p.get("species") or p.get("type") or p.get("name") or "").lower().strip()
            if not sp:
                continue
            row = by_species.setdefault(sp, {
                "count": 0, "need_water": 0,
                "last_watered_ts": None, "last_watered_human": None,
                "last_soil": None, "last_soil_ts": None,
            })
            row["count"] += 1
            state = _derive_plant_state(p)
            if state.get("attention_flag"):
                row["need_water"] += 1
            ts = state.get("last_watered_ts")
            if ts and (row["last_watered_ts"] is None or ts > row["last_watered_ts"]):
                row["last_watered_ts"] = ts
                row["last_watered_human"] = state.get("last_watered_human")
            sensed = state.get("last_sensed_payload") or {}
            moisture = state.get("last_sensed_moisture")
            s_ts = sensed.get("ts")
            if moisture is not None and (row["last_soil_ts"] is None
                                         or (s_ts or 0) >= (row["last_soil_ts"] or 0)):
                row["last_soil"] = moisture
                row["last_soil_ts"] = s_ts
        return {
            "species": by_species,
            "total_plants": len(plants_list),
            "generated_at_ms": int(time.time() * 1000),
        }

    @app.get("/plants/{idx}/history")
    def plant_history(idx: int, limit: int = 100,
                      event_type: Optional[str] = None):
        """Just the event timeline for one plant.

        Lighter than ``/plants/{idx}`` — skips the map lookup. Used by the
        care card's history scroller (Day 9).
        """
        if _event_log is None:
            raise HTTPException(503, "event log not initialised")
        types = [event_type] if event_type else None
        rows = _event_log.for_plant(idx, limit=max(1, min(500, int(limit))),
                                    event_types=types)
        return {
            "plant_index": idx,
            "events": rows,
            "count": len(rows),
        }

    @app.get("/plants/{idx}")
    def plant_detail(idx: int, history_limit: int = 30):
        """Full per-plant view: map data + derived state + recent events.

        Returns 404 if the index isn't in the currently loaded map.
        """
        data = _load_plants_from_map_handler()
        plants_list = data.get("plants") or []
        match: Optional[Dict[str, Any]] = None
        for p in plants_list:
            if p.get("index") == idx:
                match = p
                break
        if match is None:
            raise HTTPException(404, f"plant index {idx} not in current map")
        state = _derive_plant_state(match)
        history: List[Dict[str, Any]] = []
        if _event_log is not None:
            history = _event_log.for_plant(idx,
                                           limit=max(1, min(500, int(history_limit))))
        return {
            **match,
            "state": state,
            "recent_events": history,
            "history_count": len(history),
        }

    @app.post("/intent", response_model=IntentResponse)
    def intent(req: IntentRequest) -> IntentResponse:
        # Schema-version guard. We only enforce on the major.
        client_major = req.schema_version.split(".")[0]
        server_major = SCHEMA_VERSION.split(".")[0]
        if client_major != server_major:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Schema major mismatch: client={req.schema_version}, "
                    f"server={SCHEMA_VERSION}"
                ),
            )

        global _intent_running
        bridge = _require_bridge()
        task_state = get_task_state()

        # Emergency is the safety path: publish 'e' immediately (like /estop)
        # and flip the estop latch so any in-flight background tree aborts on
        # its next tick. No tree-building, no queueing — and the latch then
        # requires /reset_estop before the next command, same as the button.
        if req.emergency:
            task_state.request_estop()
            record = bridge.emergency_stop()
            return IntentResponse(
                status="success",
                task_id="emergency",
                tree=TreeResult(label="Emergency stop", status="success",
                                node_results=[]),
                commands_published=[record.command],
                tts_text="Emergency stop, robot halted.",
            )

        # Refuse non-emergency intents while the operator's estop latch is set —
        # a clear "press reset first" beats a half-rendered overlay that flips
        # straight back to stopped on the first CheckEstop.
        if task_state.estop_requested():
            msg = ("The robot is stopped. Please press reset to clear "
                   "the safety stop before sending another command.")
            return IntentResponse(
                status="failure",
                tree=TreeResult(label="Estop latched", status="failure",
                                node_results=[]),
                tts_text=msg,
                error="estop_latched",
            )

        # One tree at a time. A verified tree runs in the background (below);
        # refuse a new command while one is in flight rather than ticking two
        # trees over the same bridge/blackboard. Reserve the task_id slot under
        # the lock so two concurrent requests can't both start.
        with _intent_lock:
            busy = _intent_running is not None
            if not busy:
                task_id = _uuid.uuid4().hex[:12]
                _intent_running = task_id
        if busy:
            return IntentResponse(
                status="failure",
                tree=TreeResult(label="Busy", status="failure", node_results=[]),
                tts_text=("I'm still working on the last command. "
                          "Give me a moment, or press stop."),
                error="busy",
            )

        # Launch the tree on a background worker and wait briefly. A quick tree
        # (move, home, lights) finishes inside the grace window and returns its
        # terminal result inline — one round-trip, old behaviour. A long one
        # (multi-plant water) returns "running" + task_id for the client to
        # poll via /intent_status, so the HTTP request is never held for minutes.
        done = threading.Event()
        worker = threading.Thread(
            target=_intent_worker, args=(req, task_id, done),
            name=f"intent-{task_id}", daemon=True,
        )
        worker.start()

        if done.wait(_INTENT_GRACE_S):
            with _intent_lock:
                stored = _intent_results.get(task_id)
            if stored is not None:
                return IntentResponse.model_validate(stored["resp"])

        # Still running — hand back the task_id and the forward-tense reply so
        # the client can announce ("Watering the tomatoes") while it polls.
        forward_tts = " ".join(i.response for i in req.intents).strip()
        return IntentResponse(
            status="running",
            task_id=task_id,
            tts_text=forward_tts,
        )

    @app.get("/intent_status/{task_id}")
    def intent_status(task_id: str) -> Dict[str, Any]:
        """Poll target for async /intent execution. Returns the terminal
        IntentResponse dict once the tree finishes, ``{"status": "running"}``
        (with the task_state snapshot) while it's still ticking, or an
        ``unknown`` failure once the result has expired / never existed."""
        with _intent_lock:
            stored = _intent_results.get(task_id)
            running = (_intent_running == task_id)
        if stored is not None:
            return stored["resp"]
        if running:
            return {
                "status": "running",
                "task_id": task_id,
                "task": get_task_state().snapshot(),
            }
        return {
            "status": "failure",
            "task_id": task_id,
            "error": "unknown or expired task_id",
            "tts_text": "",
        }

    @app.post("/estop")
    def estop():
        bridge = _require_bridge()
        # Tier B: flip the task-state flag FIRST so any in-flight Wait
        # node sees it on its next tick (~100 ms) and short-circuits to
        # FAILURE — the bridge publish below halts the firmware, and the
        # blackboard flag halts the BT.
        get_task_state().request_estop()
        # Publish 'e' twice with a short gap. A single publish was sometimes
        # missed on hardware (race with panel_controller / sequencer state).
        record = bridge.emergency_stop()
        time.sleep(0.12)
        bridge.emergency_stop()
        print(f"[growmate_pi] /estop -> published 'e' x2 (status: {record.status})",
              flush=True)
        return {"status": record.status, "command": record.command, "repeated": 2}

    @app.post("/reset_estop")
    def reset_estop():
        bridge = _require_bridge()
        # Publish 'E' three times with small gaps. On the hardware run with
        # gh1 / farmbotdev a single 'E' frequently failed to take — the
        # firmware F09 round-trip needs the panel_controller to be alive
        # and the keyboard_topic subscription primed. Three publishes
        # (~180 ms each) survive a missed first message reliably.
        statuses = []
        for i in range(3):
            rec = bridge.reset_emergency_stop()
            statuses.append(rec.status)
            if i < 2:
                time.sleep(0.18)
        # Tier B: clear the task-state estop flag so the next /intent
        # can run a Wait node again. Any in-flight tree has already
        # short-circuited and will report partial in its response.
        get_task_state().clear_estop()
        print(f"[growmate_pi] /reset_estop -> published 'E' x3 (statuses: {statuses})",
              flush=True)
        return {"status": statuses[-1], "command": "E", "repeated": 3,
                "all_statuses": statuses}

    @app.get("/events")
    def events(limit: int = 50, plant: Optional[str] = None,
               event_type: Optional[str] = None):
        """Per-plant care event log (Day 7).

        Query params:
          - ``limit`` (default 50, max 500)
          - ``plant``: index (int) or name (str). Filters to one plant.
          - ``event_type``: e.g. "watered" / "sensed" / "photographed"
        """
        if _event_log is None:
            return {"events": [], "count": 0, "error": "event log not initialised"}
        limit = max(1, min(500, int(limit)))
        types = [event_type] if event_type else None
        if plant is not None:
            # Numeric? Try int; else treat as name
            try:
                plant_key: int | str = int(plant)
            except (TypeError, ValueError):
                plant_key = str(plant)
            rows = _event_log.for_plant(plant_key, limit=limit, event_types=types)
        else:
            rows = _event_log.recent(limit=limit, event_types=types)
        return {
            "events": rows,
            "count": len(rows),
            "total_in_db": _event_log.count(),
        }

    @app.get("/history")
    def history(limit: int = 50):
        bridge = _require_bridge()
        records = bridge.command_log[-limit:]
        return [
            {
                "command": r.command,
                "status": r.status,
                "description": r.description,
                "error": r.error,
            }
            for r in records
        ]

    return app


def _summarise_error(tree_result) -> str:
    failed = [n for n in tree_result.node_results if n.status == "failure"]
    if failed:
        n = failed[0]
        return f"{n.name}: {n.message or 'failed'}"
    return "tree did not complete successfully"


# ---------- CLI entry point ---------------------------------------------------


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="GrowMate Pi intent server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-ros2",
        action="store_true",
        help="Run in simulation mode (no rclpy / no real FarmBot)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help=(
            "Disable the tick-and-verify gate: action nodes report SUCCESS on "
            "publish instead of waiting for firmware /busy_state confirmation. "
            "Use only if the firmware command handler isn't publishing busy_state."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to farmbot.yaml (defaults to growmate_pi/config/farmbot.yaml)",
    )
    parser.add_argument(
        "--cors",
        action="append",
        default=None,
        help="Explicit CORS origin (repeatable). Defaults to '*'.",
    )
    args = parser.parse_args(argv)

    app = build_app(
        ros2_enabled=not args.no_ros2,
        config_path=args.config,
        cors_origins=args.cors,
        verify_enabled=not args.no_verify,
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
