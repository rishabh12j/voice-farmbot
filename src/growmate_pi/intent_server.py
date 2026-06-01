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
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import (
    SCHEMA_VERSION,
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
    """Locate the active_map.yaml the running map_handler is actually using."""
    try:
        from ament_index_python.packages import get_package_share_directory
        share = Path(get_package_share_directory("map_handler")) / "config"
    except Exception:
        return None
    for name in ("active_map.yaml", "map_references.yaml"):
        candidate = share / name
        if candidate.exists():
            return candidate
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
        "workspace": {
            "x_len": ref.get("x_len", 5691.2),
            "y_len": ref.get("y_len", 2734.0),
        },
        "source": str(path),
    }


# ---------- Module-level singletons (populated by ``build_app``) -------------


_bridge: Optional[FarmBotROS2Bridge] = None
_garden: Optional[GardenConfig] = None


def _require_bridge() -> FarmBotROS2Bridge:
    if _bridge is None:
        raise RuntimeError("Bridge not initialised. Call build_app() first.")
    return _bridge


def _require_garden() -> GardenConfig:
    if _garden is None:
        raise RuntimeError("Garden not initialised. Call build_app() first.")
    return _garden


def build_app(
    ros2_enabled: bool = True,
    config_path: Optional[Path] = None,
    cors_origins: Optional[list] = None,
) -> FastAPI:
    """Build the FastAPI app. Constructs the bridge and loads the config.

    Wrapping construction in a function (rather than module-level state) lets
    tests build multiple app instances with different config / ros2 flags.
    """
    global _bridge, _garden
    _bridge = FarmBotROS2Bridge(ros2_enabled=ros2_enabled)
    _garden = GardenConfig(config_path or DEFAULT_CONFIG)

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
        record = bridge.emergency_stop()
        return {"status": record.status, "command": record.command}

    @app.post("/reset_estop")
    def reset_estop():
        bridge = _require_bridge()
        record = bridge.reset_emergency_stop()
        return {"status": record.status, "command": record.command}

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
