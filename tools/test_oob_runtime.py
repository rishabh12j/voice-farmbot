"""In-process runtime OOB test (audit closure #5): a corrupt/non-finite MAP
coordinate must never produce a published out-of-bounds motion.

The corpus and stress harnesses inject intents over HTTP, but there is no
map-write endpoint, so a coordinate arriving through the MAP-driven builders
(water / water_all / water_smart / clear_weeds) can't be corrupted from
outside. This test does it in-process: it monkeypatches the active-map lookups
to return a plant/weed at a corrupted coordinate, builds and TICKS the real
tree against a sim bridge, and asserts the guard blocks publication —

  1. no published motion command is out of bounds (ts.oob_motions == []);
  2. the case is not classified unsafe by the shared predicate; and
  3. a non-success case never claims success in speech (honest-or-blank).

This is the runtime complement to tools/test_guard_coverage.py: that proves the
CheckBounds leaf is PRESENT in the tree; this proves it FIRES and prevents the
publish under corrupt input.

Notes:
  * The water builders always move at z=0 (they ignore the plant's z), so the
    corrupt value is placed in x or y — the coordinates the builders actually
    consume.
  * clear_weeds Python-pre-filters out-of-bounds weeds before building moves, so
    a corrupt weed yields a clean "all out of bounds" refusal; that is still a
    valid no-OOB-motion outcome (the BT CheckBounds is redundant defense there).
  * scan_bed is excluded: its coordinates come from config bounds (clamped,
    never map-driven), so it cannot receive a corrupt external coordinate; its
    guard is covered structurally by test_guard_coverage.py.

Run:  PYTHONPATH=src python tools/test_oob_runtime.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tools/ for trace_safety

import trace_safety as ts  # noqa: E402
import growmate_pi.bt.builder as B  # noqa: E402
from growmate_pi.bt.executor import execute_tree, read_tts_text  # noqa: E402
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge  # noqa: E402
from growmate_pi.garden_config import GardenConfig  # noqa: E402
from growmate_pi.schemas import Intent  # noqa: E402
from growmate_pi import intent_server as isrv  # noqa: E402
from growmate_pi.tool_state import get_tool_state  # noqa: E402

B._ANNOUNCE_PAUSE_S = 0.0  # skip the UI announce wait; irrelevant to guards

CONFIG = REPO / "src" / "growmate_pi" / "config" / "gh1.yaml"  # calibrated

# Corrupt coordinate variants. The corrupt value is in x or y (the builders use
# those; they force z=0). Each is out-of-bounds or non-finite.
VARIANTS: Dict[str, Any] = {
    "x_huge": (99999.0, 100.0),
    "y_huge": (100.0, 99999.0),
    "x_neg": (-100.0, 100.0),
    "x_nan": (float("nan"), 100.0),
    "y_inf": (100.0, float("inf")),
}

# (action, target, tool to pre-mount so EnsureTool no-ops and the tick is fast)
BUILDERS = [
    ("water", "lettuce", "watering_nozzle"),
    ("water_all", None, "watering_nozzle"),
    ("water_smart", "lettuce", "soil_sensor"),
    ("clear_weeds", None, "weeder"),
]


def _plant(x: float, y: float) -> Dict[str, Any]:
    return {"x": x, "y": y, "z": 0.0, "name": "corrupt", "index": 1,
            "species": "lettuce", "type": "lettuce", "water_quantity": 6}


def _patch_map(x: float, y: float) -> None:
    isrv.find_plants_by_species = lambda t: [_plant(x, y)]
    isrv.find_all_plants_in_garden = lambda: [_plant(x, y)]
    isrv.find_weeds = lambda: [{"x": x, "y": y, "radius": 10.0}]


def main() -> int:
    bounds = ts.bounds_from_config(CONFIG)
    garden = GardenConfig(CONFIG)
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    failures: List[str] = []
    n = 0

    for action, target, tool in BUILDERS:
        for vname, (x, y) in VARIANTS.items():
            n += 1
            _patch_map(x, y)
            get_tool_state().set(tool)  # no-op EnsureTool
            bridge.command_log.clear()
            intent = Intent(action=action, target=target, response="placeholder")
            try:
                root = B.build_tree(bridge, garden, [intent])
                result = execute_tree(root)
                status, tts = result.status, read_tts_text()
                nodes = [{"name": r.name, "status": r.status, "message": r.message}
                         for r in result.node_results]
            except Exception as exc:
                # A build/tick exception is an honest hard failure — acceptable
                # (fail-closed) as long as nothing OOB was published first.
                cmds = [r.command for r in bridge.command_log]
                bad = ts.oob_motions(cmds, bounds)
                if bad:
                    failures.append(f"{action}/{vname}: raised {exc!r} AFTER "
                                    f"publishing OOB motion {bad}")
                else:
                    print(f"  ok  {action:<12} {vname:<7} -> raised (no motion): "
                          f"{type(exc).__name__}")
                continue

            cmds = [r.command for r in bridge.command_log]
            oob = ts.oob_motions(cmds, bounds)
            outcome, detail = ts.classify(cmds, nodes, status, bounds)

            if oob:
                failures.append(f"{action}/{vname}: PUBLISHED OOB motion {oob}")
            if ts.is_unsafe(outcome):
                failures.append(f"{action}/{vname}: classified unsafe ({detail})")
            if not ts.honesty_ok(status, tts):
                failures.append(f"{action}/{vname}: dishonest speech on "
                                f"{status}: {tts!r}")
            print(f"  ok  {action:<12} {vname:<7} -> {outcome:<16} "
                  f"status={status} cmds={len(cmds)}")

    get_tool_state().clear()
    print("-" * 60)
    if failures:
        print(f"runtime OOB: {len(failures)} failures over {n} scenarios")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print(f"runtime OOB: {n} corrupt-coordinate scenarios across "
          f"{len(BUILDERS)} map-driven builders; 0 OOB motions published, "
          "none classified unsafe; 0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
