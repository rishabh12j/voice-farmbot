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
import time
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
)


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


# ---------- Module-level singletons (populated by ``build_app``) -------------


_bridge: Optional[FarmBotROS2Bridge] = None
_garden: Optional[GardenConfig] = None
_event_log: Optional[EventLog] = None


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


def _log_intent_outcome(intent: Intent, tree_status: str) -> None:
    """Append an event row for one successfully-executed intent."""
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
) -> FastAPI:
    """Build the FastAPI app. Constructs the bridge and loads the config.

    Wrapping construction in a function (rather than module-level state) lets
    tests build multiple app instances with different config / ros2 flags.
    """
    global _bridge, _garden, _event_log
    _bridge = FarmBotROS2Bridge(ros2_enabled=ros2_enabled)
    _garden = GardenConfig(config_path or DEFAULT_CONFIG)
    _event_log = EventLog(events_db or DEFAULT_DB_PATH)

    app = FastAPI(title="GrowMate Pi", version=SCHEMA_VERSION)

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
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "bridge_mode": "ros2" if _bridge.ros2_enabled else "sim",
            "bridge_ready": _bridge.is_ready(),
            "topic": _bridge.topic,
            "config": str((config_path or DEFAULT_CONFIG)),
        }

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

        bridge = _require_bridge()
        garden = _require_garden()

        commands_before = list(bridge.command_log)
        t0 = time.monotonic()
        root = build_tree(bridge, garden, req.intents, emergency=req.emergency)
        tree_result = execute_tree(root)
        duration_ms = int((time.monotonic() - t0) * 1000)

        new_commands = [
            r.command for r in bridge.command_log[len(commands_before):]
        ]
        tts = read_tts_text() or " ".join(i.response for i in req.intents)

        status_str = tree_result.status

        # Day 7: append a per-plant event row for every care-action intent
        # that didn't fail outright. The log is best-effort.
        if not req.emergency:
            for intent_obj in req.intents:
                _log_intent_outcome(intent_obj, status_str)

        return IntentResponse(
            status=status_str,
            tree=tree_result,
            commands_published=new_commands,
            tts_text=tts.strip(),
            duration_ms=duration_ms,
            error=None if status_str == "success" else _summarise_error(tree_result),
        )

    @app.post("/estop")
    def estop():
        bridge = _require_bridge()
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
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
