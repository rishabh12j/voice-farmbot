"""Smoke-test the voice-assisted map-building flow without hardware.

This creates fake camera detection YAML in a temporary directory, points the
Pi-side helpers at it, then exercises:

  detection YAML -> find_detected_plants()
  find_plants staging -> pending_plants.yaml
  label_plants region/species parsing -> P_1 add-plant commands

Run from the repo root:

    python tools/smoke_map_builder_flow.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from growmate_pi.bt.builder import build_tree
from growmate_pi.bt.executor import execute_tree, read_tts_text
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import Intent
import growmate_pi.intent_server as srv


def show(label: str, ok: bool) -> bool:
    print(f"  {'OK' if ok else 'MISS'}  {label}")
    return ok


def _write_yaml(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


def main() -> int:
    failures = 0
    print("=== Map-builder flow smoke ===\n")

    garden = GardenConfig(ROOT / "src" / "growmate_pi" / "config" / "gh1.yaml")
    bridge = FarmBotROS2Bridge(ros2_enabled=False)

    original_camera_config = srv._camera_handler_config
    original_pending_path = srv._PENDING_PLANTS_PATH

    with tempfile.TemporaryDirectory(prefix="growmate-map-smoke-") as td:
        tmp = Path(td)
        srv._PENDING_PLANTS_PATH = tmp / "pending_plants.yaml"

        # Same loose shape written by camera_handler.plant_detection:
        # [ [robot_x_mm, robot_y_mm], radius_mm, is_known, within_100 ]
        # The first two entries overlap and should dedupe to one plant.
        _write_yaml(
            tmp / "other_plants.yaml",
            [
                [[300.0, 400.0], 18.0, False, False],
                [[320.0, 410.0], 18.0, False, False],
                [[2200.0, 500.0], 25.0, False, False],
                [[4700.0, 2100.0], 30.0, False, False],
            ],
        )
        _write_yaml(
            tmp / "known_plants.yaml",
            [
                [[1000.0, 1200.0], 22.0, True, True],
            ],
        )

        def fake_camera_config(filename: str) -> Path | None:
            candidate = tmp / filename
            return candidate if candidate.exists() else None

        srv._camera_handler_config = fake_camera_config

        try:
            detections = srv.find_detected_plants()
            failures += not show(
                f"deduped detections: got {len(detections)}, want 4",
                len(detections) == 4,
            )

            left = srv.filter_plants_by_region(
                detections, "left", garden.workspace
            )
            middle = srv.filter_plants_by_region(
                detections, "middle", garden.workspace
            )
            right = srv.filter_plants_by_region(
                detections, "right", garden.workspace
            )
            failures += not show(
                f"region split left/middle/right = {len(left)}/{len(middle)}/{len(right)}",
                (len(left), len(middle), len(right)) == (2, 1, 1),
            )

            srv.write_pending_plants(detections)
            failures += not show(
                "pending detections written",
                len(srv.read_pending_plants()) == 4,
            )

            req1 = [
                Intent(
                    action="label_plants",
                    target="left basil",
                    response="Labeling the left basil.",
                )
            ]
            before = len(bridge.command_log)
            result1 = execute_tree(build_tree(bridge, garden, req1))
            cmds1 = [r.command for r in bridge.command_log[before:]]
            tts1 = read_tts_text()
            failures += not show(
                f"label left basil succeeds ({result1.status})",
                result1.status == "success",
            )
            failures += not show(
                f"left basil generated 2 P_1 commands ({len(cmds1)})",
                len(cmds1) == 2 and all(c.startswith("P_1 ") for c in cmds1),
            )
            failures += not show(
                "left basil command uses one-token species",
                all(" basil " in f" {c} " for c in cmds1),
            )
            failures += not show(
                f"2 pending plants remain ({len(srv.read_pending_plants())})",
                len(srv.read_pending_plants()) == 2,
            )
            print(f"    TTS: {tts1!r}")
            print(f"    Commands: {cmds1}")

            req2 = [
                Intent(
                    action="label_plants",
                    target="all geranium",
                    response="Labeling the rest geranium.",
                )
            ]
            before = len(bridge.command_log)
            result2 = execute_tree(build_tree(bridge, garden, req2))
            cmds2 = [r.command for r in bridge.command_log[before:]]
            tts2 = read_tts_text()
            failures += not show(
                f"label remaining geranium succeeds ({result2.status})",
                result2.status == "success",
            )
            failures += not show(
                f"remaining geranium generated 2 P_1 commands ({len(cmds2)})",
                len(cmds2) == 2 and all(c.startswith("P_1 ") for c in cmds2),
            )
            failures += not show(
                "pending list is empty after labeling all",
                srv.read_pending_plants() == [],
            )
            print(f"    TTS: {tts2!r}")
            print(f"    Commands: {cmds2}")
        finally:
            srv._camera_handler_config = original_camera_config
            srv._PENDING_PLANTS_PATH = original_pending_path

    print(f"\nFailures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
