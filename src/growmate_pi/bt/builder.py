"""Intent JSON -> py_trees tree.

The builder is the V2 equivalent of ``AICore._intent_to_tree`` from the legacy
``growmate_voice`` package: it owns the deterministic mapping from a flat
action string to a structurally correct subtree, with safety conditions
inserted up-front.

The research-claim contract (CLAUDE.md §6 rule 2) lives here. Adding an
action without the right preceding ``CheckAvailable`` / ``CheckBounds`` /
``CheckPlantFound`` is a research-claim violation, not a typo.
"""

from __future__ import annotations

from typing import List, Optional

import py_trees

from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import Intent

from growmate_pi.bt.action_nodes import (
    EmergencyStop,
    MoveTo,
    PublishCmd,
    ReadSensor,
    Respond,
    Wait,
)
from growmate_pi.bt.condition_nodes import (
    CheckAvailable,
    CheckBounds,
    CheckPlantFound,
    ResolveTarget,
)


def _seq(name: str, *children) -> py_trees.composites.Sequence:
    s = py_trees.composites.Sequence(name=name, memory=True)
    s.add_children(list(children))
    return s


def _safety_and_target(
    bridge: FarmBotROS2Bridge,
    garden: GardenConfig,
    target: Optional[str],
):
    """Standard safety + resolution prefix used by plant-targeted intents."""
    return [
        CheckAvailable(bridge),
        ResolveTarget(garden, target),
        CheckPlantFound(),
        CheckBounds(garden),
    ]


# ---------- Per-action subtree builders ---------------------------------------


def _tree_move(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    # Explicit coordinate move (jog from Windows app — absolute coords already computed)
    if "x" in (intent.params or {}):
        x = float(intent.params["x"])
        y = float(intent.params.get("y", 0))
        z = float(intent.params.get("z", 0))
        return _seq(
            f"Move to ({x:.0f}, {y:.0f}, {z:.0f})",
            CheckAvailable(bridge),
            MoveTo(bridge, x=x, y=y, z=z),
            Respond(intent.response),
        )
    # Named target move (resolve plant/location from garden config)
    return _seq(
        f"Move to {intent.target}",
        *_safety_and_target(bridge, garden, intent.target),
        MoveTo(bridge),
        Respond(intent.response),
    )


def _tree_water(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    resolved = garden.resolve_target(intent.target) if intent.target else None
    duration = int(
        intent.params.get(
            "duration_s",
            resolved.water_quantity if resolved else 6,
        )
    )
    return _seq(
        f"Water {intent.target}",
        *_safety_and_target(bridge, garden, intent.target),
        MoveTo(bridge),
        PublishCmd("D_W_1", bridge, name="WaterPumpOn"),
        Wait(duration, name=f"Pulse({duration}s)"),
        PublishCmd("D_W_0", bridge, name="WaterPumpOff"),
        Respond(intent.response),
    )


def _tree_water_all(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Water all",
        CheckAvailable(bridge),
        PublishCmd("P_4", bridge, name="WaterAllPlants"),
        Respond(intent.response),
    )


def _tree_go_home(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Go home",
        CheckAvailable(bridge),
        PublishCmd("H_0", bridge, name="GoHome"),
        Respond(intent.response),
    )


def _tree_light(bridge, intent: Intent, on: bool) -> py_trees.behaviour.Behaviour:
    cmd = "D_L_1" if on else "D_L_0"
    label = "Lights on" if on else "Lights off"
    return _seq(
        label,
        CheckAvailable(bridge),
        PublishCmd(cmd, bridge, name=label),
        Respond(intent.response),
    )


def _tree_photo(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    # If a target is given, move there first; otherwise take photo at current pos.
    children = [CheckAvailable(bridge)]
    if intent.target:
        children.extend(
            [
                ResolveTarget(garden, intent.target),
                CheckPlantFound(),
                CheckBounds(garden),
                MoveTo(bridge),
            ]
        )
    children.append(PublishCmd("I_1", bridge, name="TakePhoto"))
    children.append(Respond(intent.response))
    return _seq(f"Photo of {intent.target or 'current pos'}", *children)


def _tree_panorama(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Panorama",
        CheckAvailable(bridge),
        PublishCmd("I_2", bridge, name="Panorama"),
        Respond(intent.response),
    )


def _tree_scan_weeds(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Scan weeds",
        CheckAvailable(bridge),
        PublishCmd("I_4", bridge, name="ScanWeeds"),
        Respond(intent.response),
    )


def _tree_check_sensor(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    children = [CheckAvailable(bridge)]
    if intent.target:
        children.extend(
            [
                ResolveTarget(garden, intent.target),
                CheckPlantFound(),
                CheckBounds(garden),
                MoveTo(bridge),
            ]
        )
    children.append(ReadSensor(bridge))
    children.append(Respond(intent.response))
    return _seq(f"Check sensor at {intent.target or 'current'}", *children)


def _tree_check_moisture(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Check moisture",
        CheckAvailable(bridge),
        PublishCmd("P_9", bridge, name="CheckMoisture"),
        Respond(intent.response),
    )


def _tree_emergency(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Emergency stop",
        EmergencyStop(bridge),
        Respond(intent.response or "Emergency stop, robot halted."),
    )


def _tree_general_question(intent: Intent) -> py_trees.behaviour.Behaviour:
    """Knowledge query — no robot action. Just respond.

    On V2 the client side has already produced an answer via the LLM call.
    The Pi's job is to record it for TTS. (Future: PlanSys2 may augment.)
    """
    return _seq(
        "General question",
        Respond(intent.response),
    )


# ---------- Public API --------------------------------------------------------


def build_subtree(
    bridge: FarmBotROS2Bridge,
    garden: GardenConfig,
    intent: Intent,
) -> py_trees.behaviour.Behaviour:
    """Map a single Intent to a py_trees subtree."""
    a = intent.action
    if a == "move":
        return _tree_move(bridge, garden, intent)
    if a == "water":
        return _tree_water(bridge, garden, intent)
    if a == "water_all":
        return _tree_water_all(bridge, garden, intent)
    if a == "go_home":
        return _tree_go_home(bridge, garden, intent)
    if a == "light_on":
        return _tree_light(bridge, intent, on=True)
    if a == "light_off":
        return _tree_light(bridge, intent, on=False)
    if a == "photo":
        return _tree_photo(bridge, garden, intent)
    if a == "panorama":
        return _tree_panorama(bridge, intent)
    if a == "scan_weeds":
        return _tree_scan_weeds(bridge, intent)
    if a == "check_sensor":
        return _tree_check_sensor(bridge, garden, intent)
    if a == "check_moisture":
        return _tree_check_moisture(bridge, intent)
    if a == "emergency_stop":
        return _tree_emergency(bridge, intent)
    if a == "general_question":
        return _tree_general_question(intent)
    # Should never happen: schemas.Action Literal restricts this set.
    return Respond(intent.response, name=f"Unknown({a})")


def build_tree(
    bridge: FarmBotROS2Bridge,
    garden: GardenConfig,
    intents: List[Intent],
    emergency: bool = False,
) -> py_trees.behaviour.Behaviour:
    """Build the root tree for an entire ``IntentRequest``.

    - If ``emergency`` is True, returns a tree that publishes e-stop and
      ignores every other intent in the list.
    - If there's exactly one intent, returns its subtree directly.
    - Otherwise wraps all subtrees in one Sequence (executes in order).
    """
    if emergency:
        return _seq(
            "Emergency stop",
            EmergencyStop(bridge),
            Respond("Emergency stop, robot halted."),
        )

    if not intents:
        return Respond("Nothing to do.", name="NoIntents")

    if len(intents) == 1:
        return build_subtree(bridge, garden, intents[0])

    children = [build_subtree(bridge, garden, i) for i in intents]
    labels = " then ".join(i.action for i in intents)
    return _seq(labels, *children)
