"""Action-vocabulary contract test — the enum, the builder, and the LLM agree.

Why this exists: the action vocabulary is duplicated across eight places with
nothing tying them together, and both existing contract tests pass *vacuously*
when a verb is added (``test_wire_grammar`` ticks a hardcoded scenario list;
``test_verify_semantics`` never imports ``schemas``). The two ways that drift
bites are both silent:

1. A verb in ``schemas.Action`` with no ``build_subtree`` arm falls through to
   ``Respond(intent.response, name=f"Unknown({a})")`` — a tree that returns
   SUCCESS and publishes nothing while speaking the LLM's forward-tense line
   ("Stowing the nozzle."). The robot does nothing and says it did something.
   That is the honest-or-blank rule broken by a missing ``if``. Worse, an eval
   case expecting a refusal scores it as a PASS.
2. A verb in ``schemas.Action`` but missing from ``AICore.ACTIONS`` is coerced
   to ``general_question`` by the client before it ever reaches the Pi
   (app.py's validation gate), so the capability is dead on arrival with no
   error anywhere.

Neither is a typo — both are research-claim violations, so they fail the build.

``AICore.ACTIONS`` and the prompt text are read from source with ``ast`` rather
than imported: ``growmate_voice`` pulls in the STT/LLM/TTS stack, which is not
installed in the sim/CI environment. Same trick ``test_wire_grammar`` uses on
the controller.

Run (no ROS, no robot, no LLM, ~1 s — trees are built, never ticked):

    PYTHONPATH=src python tools/test_action_coverage.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Optional, Set, get_args

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from growmate_pi.bt.builder import build_subtree  # noqa: E402
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge  # noqa: E402
from growmate_pi.garden_config import GardenConfig  # noqa: E402
from growmate_pi.schemas import Action, Intent  # noqa: E402

AI_CORE_PY = (REPO / "src" / "growmate_voice" / "growmate_voice" / "ai_core.py")
CONFIGS = ["farmbot.yaml", "gh1.yaml", "farmbotdev.yaml"]

# Actions deliberately absent from the LLM prompt's AVAILABLE ACTIONS list.
# emergency_stop is matched before the LLM is ever called, so offering it as a
# classification target would be wrong.
PROMPT_EXEMPT = {"emergency_stop"}

# A target that lets each verb build its real subtree rather than its
# "no target supplied" stub. Actions not listed here take None.
SAMPLE_TARGETS = {
    "move": "lettuce",
    "water": "lettuce",
    "water_smart": "lettuce",
    "photo": "lettuce",
    "check_sensor": "lettuce",
    "label_plants": "left lettuce",
    "mount_tool": "watering nozzle",
}


def _assign_of(tree: ast.Module, name: str) -> Optional[ast.AST]:
    """The value assigned to ``name`` anywhere in the module (incl. in a class)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    return None


def ai_core_actions() -> Set[str]:
    """``AICore.ACTIONS`` read from source, without importing the voice stack."""
    tree = ast.parse(AI_CORE_PY.read_text(encoding="utf-8"))
    value = _assign_of(tree, "ACTIONS")
    if value is None:
        raise SystemExit(f"FAIL: no ACTIONS assignment found in {AI_CORE_PY}")
    return set(ast.literal_eval(value))


def ai_core_prompt_text() -> str:
    """Every string constant in ai_core.py, concatenated.

    The prompt is one big f-string, so its literal chunks land here; that is
    enough to ask "does the word 'stow_tool' appear in the prompt at all".
    """
    tree = ast.parse(AI_CORE_PY.read_text(encoding="utf-8"))
    return "\n".join(
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def main() -> int:
    failures: List[str] = []
    actions = set(get_args(Action))
    print(f"schemas.Action: {len(actions)} verbs")

    # --- 1. Every action builds a real subtree, on every garden config -------
    # Config-driven, because a verb can dispatch fine on one robot and refuse
    # on another (gh1 retired its config plant block; gh2 has fewer tools).
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    for cfg_name in CONFIGS:
        cfg = REPO / "src" / "growmate_pi" / "config" / cfg_name
        if not cfg.exists():
            failures.append(f"config {cfg_name} missing")
            continue
        garden = GardenConfig(cfg)
        for action in sorted(actions):
            intent = Intent(action=action, target=SAMPLE_TARGETS.get(action),
                            response="placeholder response")
            try:
                tree = build_subtree(bridge, garden, intent)
            except Exception as exc:
                failures.append(f"{cfg_name}: build_subtree({action}) raised {exc!r}")
                continue
            if tree is None:
                failures.append(f"{cfg_name}: build_subtree({action}) returned None")
                continue
            # The fallback names itself Unknown(<action>). Hitting it means the
            # dispatch ladder has no arm for this verb.
            if tree.name == f"Unknown({action})":
                failures.append(
                    f"{cfg_name}: '{action}' has NO build_subtree arm — it would "
                    "speak the LLM's response over a tree that does nothing")
        print(f"  ok  {cfg_name}: all {len(actions)} verbs dispatch")

    # --- 2. The Pi enum and the client's allowlist are the same set ----------
    client = ai_core_actions()
    print(f"AICore.ACTIONS: {len(client)} verbs")
    for missing in sorted(actions - client):
        failures.append(
            f"'{missing}' is in schemas.Action but not AICore.ACTIONS — the "
            "client would coerce it to general_question before it reaches the Pi")
    for extra in sorted(client - actions):
        failures.append(
            f"'{extra}' is in AICore.ACTIONS but not schemas.Action — the Pi "
            "would reject it with a 422")

    # --- 3. Every classifiable action appears in the prompt ------------------
    # An action the LLM is never told about cannot be emitted, so the verb is
    # unreachable however well the Pi implements it.
    prompt = ai_core_prompt_text()
    for action in sorted(actions - PROMPT_EXEMPT):
        if action not in prompt:
            failures.append(
                f"'{action}' never appears in the ai_core.py prompt text — the "
                "LLM is not told it exists")

    print(f"\n{'-' * 60}")
    if failures:
        print(f"Action coverage: {len(failures)} failures")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print(f"Action coverage: {len(actions)} verbs x {len(CONFIGS)} configs, "
          "enum/builder/client/prompt all agree; 0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
