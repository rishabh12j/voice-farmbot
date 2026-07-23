"""Structural guard-coverage contract test — every coordinate ``MoveTo`` is
preceded by a matching ``CheckBounds``.

Why this exists (audit closure criterion #2, 2026-07-22): the research contract
(AGENTS.md §2) and the paper's Fig. 2 claim a bounds guard prefixes *every*
robot-touching move. Nothing enforced it. The multi-plant builders
(``water``, ``water_all``, both ``water_smart`` passes, ``clear_weeds``,
``scan_bed``) drove the gantry with ``MoveTo(x, y, z)`` and no ``CheckBounds`` —
an out-of-bounds active-map/detection coordinate would have published an
unguarded ``M`` command, the exact failure the thesis says is impossible. The
existing contract tests could not catch this: ``test_action_coverage`` checks
enum/builder/client/prompt alignment, ``test_wire_grammar`` ticks a fixed
scenario list, ``test_verify_semantics`` never touches bounds. This test builds
every action tree and proves the guard is structurally present, so the
invariant cannot silently regress.

Invariant: in every subtree ``build_subtree`` produces (20 actions x 3 configs,
with a synthetic map so the loops materialize), each ``MoveTo`` leaf L has, among
its earlier siblings in the same composite (no intervening ``MoveTo``), a
``CheckBounds`` whose mode matches L — static coords equal L's coords, or both in
blackboard mode. A ``StepNotify`` etc. between guard and move is allowed (the
existing ``move`` path has one).

Both nodes store their target the same way: ``self._static`` is an ``(x, y, z)``
tuple for explicit coords or ``None`` in blackboard mode. That symmetry is what
this test leans on.

Run (no ROS, no robot, no LLM — trees are built, never ticked):

    PYTHONPATH=src python tools/test_guard_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List
from typing import get_args

import py_trees

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from growmate_pi import intent_server as _isrv  # noqa: E402
from growmate_pi.bt.action_nodes import MoveTo  # noqa: E402
from growmate_pi.bt.builder import build_subtree  # noqa: E402
from growmate_pi.bt.condition_nodes import CheckBounds  # noqa: E402
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge  # noqa: E402
from growmate_pi.garden_config import GardenConfig  # noqa: E402
from growmate_pi.schemas import Action, Intent  # noqa: E402

CONFIGS = ["farmbot.yaml", "gh1.yaml", "farmbotdev.yaml"]

# Targets that let each verb build its real (moving) subtree rather than a "no
# target" stub. Mirrors tools/test_action_coverage.py.
SAMPLE_TARGETS = {
    "move": "lettuce",
    "water": "lettuce",
    "water_smart": "lettuce",
    "photo": "lettuce",
    "check_sensor": "lettuce",
    "label_plants": "left lettuce",
    "mount_tool": "watering nozzle",
}

# The loop builders whose MoveTo leaves are the whole point of this test — assert
# each actually materialized a move (else the synthetic map didn't take and the
# test is silently vacuous). water_smart only materializes on a *calibrated*
# config, so it is asserted on gh1.yaml only (see LOOP_CFG).
LOOP_ACTIONS = ["water", "water_all", "water_smart", "clear_weeds", "scan_bed"]
LOOP_CFG = "gh1.yaml"  # calibrated: true, so water_smart builds its two passes


def _synthetic_plants() -> List[Dict[str, Any]]:
    """One in-bounds and one out-of-bounds plant. The OOB one matters: the water
    paths have no Python pre-filter, so an OOB coordinate must STILL emit a guard
    in the tree (fail-closed at tick time). In-bounds coords (100, 100, 0) are
    valid under every config (x_max >= 5000, y_max >= 2700, z in [-500, 0])."""
    return [
        {"x": 100.0, "y": 100.0, "z": 0.0, "name": "lettuce A", "index": 1,
         "species": "lettuce", "type": "lettuce", "water_quantity": 6},
        {"x": 999999.0, "y": 999999.0, "z": 0.0, "name": "lettuce OOB", "index": 2,
         "species": "lettuce", "type": "lettuce", "water_quantity": 6},
    ]


def _synthetic_weeds() -> List[Dict[str, Any]]:
    # clear_weeds Python-filters OOB weeds out, so an in-bounds weed is what
    # materializes its three per-weed moves.
    return [{"x": 200.0, "y": 200.0, "radius": 10.0}]


def _install_synthetic_map() -> None:
    """Patch the intent_server lookups the builders import at call time, so the
    multi-plant/detection loops build their MoveTo leaves without a live map."""
    _isrv.find_plants_by_species = lambda target: list(_synthetic_plants())
    _isrv.find_all_plants_in_garden = lambda: list(_synthetic_plants())
    _isrv.find_weeds = lambda: list(_synthetic_weeds())
    _isrv.clear_detections = lambda: None  # scan_bed calls this; keep it pure


def _walk(node) -> Iterator[Any]:
    yield node
    if isinstance(node, py_trees.composites.Composite):
        for child in node.children:
            yield from _walk(child)


def _check_move(move: MoveTo, siblings: List[Any], j: int, path: str) -> List[str]:
    """The guard predicate for one MoveTo at index j of its parent's children."""
    guard = None
    for k in range(j - 1, -1, -1):
        sib = siblings[k]
        if isinstance(sib, MoveTo):
            break  # an earlier move — its guard must not count for this one
        if isinstance(sib, CheckBounds):
            guard = sib
            break
    if guard is None:
        return [f"{path}: MoveTo '{move.name}' has no CheckBounds guard before "
                "it in its sequence"]
    mv, gd = move._static, guard._static
    if (mv is None) != (gd is None):
        return [f"{path}: MoveTo '{move.name}' / guard '{guard.name}' mode "
                f"mismatch (move static={mv is not None}, guard "
                f"static={gd is not None})"]
    if mv is not None:
        mvc = tuple(float(v) for v in mv)
        gdc = tuple(float(v) for v in gd)
        if mvc != gdc:
            return [f"{path}: CheckBounds '{guard.name}' coords {gdc} != "
                    f"MoveTo '{move.name}' coords {mvc}"]
    return []


def _findings(node, path: str) -> List[str]:
    """Check every MoveTo in ``node``'s subtree. A MoveTo is only ever validated
    against its siblings in its parent composite (via _check_move), so we check
    it there and recurse into composites only — never into the MoveTo leaf."""
    out: List[str] = []
    if isinstance(node, MoveTo):
        # Reached only for a bare-root MoveTo (build_subtree never returns one,
        # but a MoveTo with no parent composite can have no guard).
        out.append(f"{path}: root is an unguarded MoveTo '{node.name}'")
    elif isinstance(node, py_trees.composites.Composite):
        children = node.children
        for j, child in enumerate(children):
            if isinstance(child, MoveTo):
                out += _check_move(child, children, j, path)
            else:
                out += _findings(child, f"{path}/{child.name}")
    return out


def main() -> int:
    _install_synthetic_map()
    failures: List[str] = []
    actions = sorted(get_args(Action))
    print(f"schemas.Action: {len(actions)} verbs")

    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    total_moves = 0
    loop_move_counts: Dict[str, int] = {}
    for cfg_name in CONFIGS:
        cfg = REPO / "src" / "growmate_pi" / "config" / cfg_name
        if not cfg.exists():
            failures.append(f"config {cfg_name} missing")
            continue
        garden = GardenConfig(cfg)
        cfg_moves = 0
        for action in actions:
            intent = Intent(action=action, target=SAMPLE_TARGETS.get(action),
                            response="placeholder")
            try:
                tree = build_subtree(bridge, garden, intent)
            except Exception as exc:
                failures.append(f"{cfg_name}: build_subtree({action}) raised {exc!r}")
                continue
            moves = [n for n in _walk(tree) if isinstance(n, MoveTo)]
            cfg_moves += len(moves)
            if cfg_name == LOOP_CFG:
                loop_move_counts[action] = len(moves)
            failures += _findings(tree, f"{cfg_name}:{action}")
        print(f"  ok  {cfg_name}: {cfg_moves} MoveTo leaves checked")
        total_moves += cfg_moves

    # Non-vacuous guarantees: the loops must actually have produced moves, or the
    # synthetic map failed to apply and this test proved nothing.
    if total_moves == 0:
        failures.append("no MoveTo leaves materialized across any config — "
                        "synthetic map not applied; test is vacuous")
    for action in LOOP_ACTIONS:
        if loop_move_counts.get(action, 0) == 0:
            failures.append(f"{LOOP_CFG}: '{action}' materialized no MoveTo — its "
                            "loop did not build; guard coverage unproven for it")

    print(f"\n{'-' * 60}")
    if failures:
        print(f"Guard coverage: {len(failures)} failures")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print(f"Guard coverage: {total_moves} MoveTo leaves across {len(CONFIGS)} "
          "configs, every one bounds-guarded; 0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
