"""Wire-grammar contract test — everything the BT publishes must parse
against the REAL farmbot_controller acceptor.

Why this exists (audit F1, documentation/sim2real_gap_audit.md): the sim
bridge accepted ``T2_1`` while the real ``farmbot_controller`` silently
dropped it — its command ``match`` has **no default arm**, so a wrong wire
format produces no motion, no warning, and passes every sim gate. This test
closes the whole class:

1. The set of legal command heads is extracted from the controller SOURCE by
   AST-walking its ``match`` case patterns — if the controller's grammar and
   this repo's publishers drift apart, the test fails; nothing is shadowed.
2. Arity/typing rules per head replicate what the controller (and the
   map_handler behind it) actually parse.
3. A corpus of builder trees is ticked in sim mode and every string handed
   to ``bridge.publish`` is checked. The corpus is ordered so EnsureTool
   exercises fresh-mount AND both swap directions (all four T_1/T_2 mount/
   unmount strings must be observed, else the test fails as vacuous).
4. Every configured tool index in every garden config must be mountable and
   registrable within the controller's accepted range (this is what caught
   weeder=4 / rotating_weeder=5 being unreachable).

Run (no ROS, no robot, ~30 s — the corpus ticks real trees with real Waits):

    PYTHONPATH=src python tools/test_wire_grammar.py

scan_bed is deliberately not ticked (hundreds of grid moves); its command
vocabulary (M / I_4) is covered by the other scenarios.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from growmate_pi.bt.builder import build_tree  # noqa: E402
from growmate_pi.bt.executor import execute_tree  # noqa: E402
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge  # noqa: E402
from growmate_pi.garden_config import GardenConfig  # noqa: E402
from growmate_pi.schemas import Intent  # noqa: E402

CONTROLLER_PY = (REPO / "src" / "farmbot_controllers" / "farmbot_controllers"
                 / "farmbot_controller.py")

_AXES = {"X", "Y", "Z"}


# ---------- 1. The controller's grammar, extracted from its source ------------


def controller_case_literals() -> Set[str]:
    """Every string literal that appears in a ``case`` pattern of
    farmbot_controller.py — i.e. the exact set of command heads the real
    stack will act on. Anything else is silently dropped there."""
    tree = ast.parse(CONTROLLER_PY.read_text(encoding="utf-8"))
    literals: Set[str] = set()

    def pattern_strings(p: ast.AST) -> Iterable[str]:
        if isinstance(p, ast.MatchValue) and isinstance(p.value, ast.Constant) \
                and isinstance(p.value.value, str):
            yield p.value.value
        elif isinstance(p, ast.MatchOr):
            for sub in p.patterns:
                yield from pattern_strings(sub)

    for node in ast.walk(tree):
        if isinstance(node, ast.Match):
            for case in node.cases:
                literals.update(pattern_strings(case.pattern))
    return literals


def _floats(tokens: List[str]) -> bool:
    try:
        for t in tokens:
            float(t)
        return True
    except ValueError:
        return False


def make_acceptor(heads: Set[str]):
    """Return ``accepted(cmd) -> (ok, reason)`` replicating the controller's
    match-on-first-token semantics plus the arity/typing each arm (and the
    map_handler behind it) actually parses."""

    def accepted(cmd: str) -> Tuple[bool, str]:
        parts = cmd.split()
        if not parts:
            return False, "empty command"
        head = parts[0]
        if head not in heads:
            return False, f"head {head!r} matches no controller case (silent drop)"

        n = len(parts)
        if head in ("e", "E", "w", "s", "a", "d", "1", "2", "3",
                    "H_0", "H_1", "P_3", "P_4", "P_5", "P_9",
                    "I_0", "I_1", "I_2", "I_3", "I_4",
                    "D_L_1", "D_L_0", "D_V_0", "D_V_1", "D_W_0", "D_W_1",
                    "D_C", "D_S_C", "P4_0", "P4_1", "P5_0", "P5_1"):
            return (n == 1), f"{head} takes no args (got {n - 1})"
        if head == "M":
            return (n == 4 and _floats(parts[1:4])), "M needs 3 float coords"
        if head == "M_S":
            return (n == 5 and _floats(parts[1:5])), "M_S needs 3 coords + speed"
        if head == "H_2":
            return (n == 2 and parts[1] in _AXES), "H_2 needs an axis X|Y|Z"
        if head == "CONF":
            return (n == 1 or (n == 2 and parts[1] in ("S", "M"))), "CONF [S|M]"
        if head == "C_0":
            return (n == 1 or (n == 2 and parts[1] in _AXES)), "C_0 [X|Y|Z]"
        if head == "C_1":
            return (n == 2), "C_1 needs a config name"
        if head == "C_2":
            return (n == 2 and parts[1] in _AXES), "C_2 needs an axis X|Y|Z"
        if re.fullmatch(r"T_\d_0", head):
            # T_n_0 Name x y z release_dir  (map_controller.add_tool parse)
            ok = (n == 6 and _floats(parts[2:5])
                  and parts[5].isdigit() and 1 <= int(parts[5]) <= 4)
            return ok, f"{head} needs: Name x y z release_dir(1-4)"
        if re.fullmatch(r"T_\d_[12]", head):
            return (n == 1), f"{head} takes no args"
        if re.fullmatch(r"S_\d_0", head):
            return (n == 7), f"{head} needs 6 args (tray registration)"
        if head == "P_1":
            # P_1 x y z excl_r canopy_r water_qty max_z name stage
            return (n == 10 and _floats(parts[1:8])), \
                "P_1 needs 7 floats + name + stage"
        if head == "P_2":
            return (n == 2 and parts[1].lstrip("-").isdigit()), "P_2 needs an index"
        # A head the controller knows but this acceptor doesn't model —
        # that's acceptor drift, fail loudly rather than guess.
        return False, f"acceptor has no arity rule for {head!r} — update this test"

    return accepted


# ---------- 2. The ticked corpus ----------------------------------------------


def _intent(action: str, target: Optional[str] = None,
            params: Optional[Dict] = None) -> Intent:
    return Intent(action=action, target=target, params=params or {},
                  response=f"({action})")


def run_corpus(bridge: FarmBotROS2Bridge, garden: GardenConfig
               ) -> List[Tuple[str, List[str], str]]:
    """Build + tick each scenario; return (label, commands, status) rows.

    Order matters: check_sensor first (fresh mount of the soil probe), then
    water (swap probe->nozzle), then water_smart (swap nozzle->probe->nozzle)
    — together they force all four mount/unmount strings for tools 1 and 2.
    """
    import growmate_pi.intent_server as srv

    # label_plants touches the pending-plants store: stub the I/O, keep the
    # tree-building logic real.
    srv.read_pending_plants = lambda: [
        {"x": 500.0, "y": 300.0, "radius": 30.0},
        {"x": 700.0, "y": 400.0, "radius": 25.0},
    ]
    srv.write_pending_plants = lambda plants: None

    scenarios: List[Tuple[str, List[Intent], bool]] = [
        ("check_sensor spearmint (fresh probe mount)",
         [_intent("check_sensor", "spearmint")], False),
        ("water spearmint (swap probe->nozzle)",
         [_intent("water", "spearmint")], False),
        ("water_smart spearmint (swap both ways)",
         [_intent("water_smart", "spearmint")], False),
        ("move named", [_intent("move", "spearmint")], False),
        ("move explicit coords",
         [_intent("move", None, {"x": 100.0, "y": 200.0, "z": 0.0})], False),
        ("go_home", [_intent("go_home")], False),
        ("lights on", [_intent("light_on")], False),
        ("lights off", [_intent("light_off")], False),
        ("photo of plant", [_intent("photo", "spearmint")], False),
        ("panorama", [_intent("panorama")], False),
        ("scan_weeds", [_intent("scan_weeds")], False),
        ("clear_weeds (none on record -> no commands)",
         [_intent("clear_weeds")], False),
        ("check_moisture", [_intent("check_moisture")], False),
        ("label_plants", [_intent("label_plants", "left lettuce")], False),
        ("emergency stop", [_intent("emergency_stop")], True),
    ]

    rows: List[Tuple[str, List[str], str]] = []
    for label, intents, emergency in scenarios:
        bridge.command_log.clear()
        root = build_tree(bridge, garden, intents, emergency=emergency)
        result = execute_tree(root)
        rows.append((label, [r.command for r in bridge.command_log],
                     result.status))
    return rows


# ---------- 3. Main ------------------------------------------------------------


def main() -> int:
    heads = controller_case_literals()
    accepted = make_acceptor(heads)
    failures = 0

    # Sanity: the extraction itself found a plausible grammar.
    for must in ("M", "H_0", "D_W_1", "e"):
        if must not in heads:
            print(f"FAIL: controller grammar extraction lost {must!r} "
                  f"(got {len(heads)} heads) — AST walk broken?")
            return 1

    # --- 3a. Config contract: every configured tool is reachable ------------
    print("== Tool-index contract (configs vs controller grammar) ==")
    for cfg_name in ("farmbot.yaml", "gh1.yaml", "farmbotdev.yaml"):
        path = REPO / "src" / "growmate_pi" / "config" / cfg_name
        if not path.exists():
            continue
        garden = GardenConfig(path)
        for tool, idx in sorted(garden.tools_by_name().items()):
            for cmd in (f"T_{idx}_1", f"T_{idx}_2",
                        f"T_{idx}_0 {tool} 10.0 1760.0 -285.04 2"):
                ok, why = accepted(cmd)
                mark = "ok " if ok else "FAIL"
                if not ok:
                    failures += 1
                print(f"  [{mark}] {cfg_name}: {tool} (index {idx}) -> {cmd!r}"
                      + ("" if ok else f"  <- {why}"))

    # --- 3b. Ticked corpus: everything the builder publishes ---------------
    print("\n== Builder corpus (sim-ticked) ==")
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    garden = GardenConfig(REPO / "src" / "growmate_pi" / "config" / "farmbot.yaml")
    seen: Set[str] = set()
    checked = 0
    for label, cmds, status in run_corpus(bridge, garden):
        bad = []
        for c in cmds:
            checked += 1
            seen.add(c)
            ok, why = accepted(c)
            if not ok:
                bad.append((c, why))
        mark = "ok " if not bad else "FAIL"
        if bad:
            failures += len(bad)
        print(f"  [{mark}] {label}: {len(cmds)} cmds, status={status}")
        for c, why in bad:
            print(f"         REJECTED {c!r}: {why}")

    # --- 3c. Vacuity guards --------------------------------------------------
    must_see = {"T_1_1", "T_1_2", "T_2_1", "T_2_2"}  # both swap directions
    missing = must_see - seen
    if missing:
        failures += len(missing)
        print(f"\nFAIL vacuity guard: corpus never published {sorted(missing)} "
              "— EnsureTool coverage lost (did a tree refuse unexpectedly?)")
    if checked == 0:
        failures += 1
        print("FAIL vacuity guard: corpus published zero commands")

    print(f"\n{'-' * 60}")
    print(f"Wire-grammar: {checked} published commands checked, "
          f"{failures} failures; controller grammar = {len(heads)} heads")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
