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

import time as _time
import uuid as _uuid
from typing import Any, Callable, Dict, List, Optional

import py_trees

from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import Intent

from growmate_pi.bt.action_nodes import (
    CheckEstop,
    EmergencyStop,
    HOME_TIMEOUT_S,
    LogPlantEvent,
    MOVE_TIMEOUT_S,
    MoveTo,
    PUMP_TIMEOUT_S,
    PublishCmd,
    ReadSensor,
    Respond,
    StepNotify,
    TaskBoundary,
    Wait,
)
from growmate_pi.bt.condition_nodes import (
    CheckAvailable,
    CheckBounds,
    CheckPlantFound,
    ResolveTarget,
)


# Seconds the multi-plant trees wait after task_state.start() before the
# first MoveTo. Long enough for the browser TTS to speak "Watering 3
# marigolds." before the gantry begins moving. Tick-aware Wait so an
# estop during this window still halts within one tick.
_ANNOUNCE_PAUSE_S = 2.5


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
            MoveTo(bridge, x=x, y=y, z=z, verify=True, timeout_s=MOVE_TIMEOUT_S),
            Respond(intent.response),
        )
    # Named target move (resolve plant/location from garden config)
    return _seq(
        f"Move to {intent.target}",
        *_safety_and_target(bridge, garden, intent.target),
        MoveTo(bridge, verify=True, timeout_s=MOVE_TIMEOUT_S),
        Respond(intent.response),
    )


def _tree_water(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Tier B: water all plants in the active map matching ``intent.target``.

    Three branches:
      1. No target supplied  -> error subtree (LLM should have given one).
      2. Target supplied but the active_map has zero matches -> a tiny
         failure tree with a clear "I don't see any X in this garden" TTS.
         This was the begonia bug from the WSL test: the LLM emitted
         water target=begonia, but the active_map has none, so before this
         we silently fell back to a placeholder coord from farmbot.yaml.
      3. Target supplied with N >= 1 matches -> a TaskBoundary("start") +
         N x (CheckEstop -> StepNotify -> MoveTo -> Pump on -> Wait ->
         Pump off -> LogPlantEvent) + TaskBoundary("end") sequence.

    The CheckEstop leaves between plants are belt-and-braces: the Wait
    inside each pulse is already tick-aware, but a checkpoint at every
    plant boundary makes the abort behaviour predictable to demo on
    stage ("press Stop now, robot halts on the next plant").
    """
    # Local import to dodge a circular: intent_server imports builder,
    # but find_plants_by_species lives in intent_server.
    from growmate_pi.intent_server import find_plants_by_species

    target = (intent.target or "").strip()
    if not target:
        return _seq(
            "Water (no target)",
            Respond("I need to know which plant to water. "
                    "Try saying 'water the tomatoes'."),
        )

    matches = find_plants_by_species(target)
    if not matches:
        # Q-design: refuse cleanly rather than fall back to water_all or
        # to a placeholder coordinate.
        return _seq(
            f"Water {target} (no match)",
            Respond(f"I don't see any {target} in this garden. "
                    "Tell me a different plant."),
        )

    # Build the per-plant duration once. Active_map carries water_quantity
    # in seconds; default to 6 s if the row is malformed.
    def _plant_duration(p: Dict[str, Any]) -> int:
        try:
            return int(float(p.get("water_quantity") or 6.0))
        except Exception:
            return 6

    task_id = _uuid.uuid4().hex[:8]
    total = len(matches)
    label = f"Watering {total} {target}" if total > 1 else f"Watering one {target}"

    children: List[py_trees.behaviour.Behaviour] = [
        TaskBoundary("start", task_id=task_id, label=label, total_steps=total,
                     name=f"BeginTask({total})"),
        CheckAvailable(bridge),
        # Speak-then-move: the browser TTS announces the task label as
        # soon as task_active flips True. Give it a moment to actually
        # play before the gantry starts moving. The Wait is tick-aware,
        # so an estop press during the announcement still halts within
        # ~100 ms.
        Wait(_ANNOUNCE_PAUSE_S, name="AnnouncePause"),
    ]

    for i, plant in enumerate(matches, start=1):
        x = float(plant["x"])
        y = float(plant["y"])
        z = 0.0
        duration = _plant_duration(plant)
        plant_name = str(plant.get("name") or f"plant {plant.get('index', '?')}")
        plant_index = int(plant.get("index", 0))
        step_label = f"{plant_name} ({i}/{total})"

        # Per-leaf event-log writer — bound to this specific plant. Runs
        # AFTER the pump-off so the row only exists if the cycle truly
        # completed; that's the Tier B "honest log" payoff.
        def _make_log_fn(p_idx: int, p_name: str) -> Callable[[], None]:
            def _fn() -> None:
                # Local import for the same circular-avoidance reason as above.
                from growmate_pi.intent_server import _event_log
                if _event_log is None:
                    return
                _event_log.log(
                    event_type="watered",
                    plant_index=p_idx,
                    plant_name=p_name,
                    payload={"source": "multi_plant_water", "task_id": task_id},
                )
            return _fn

        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, step_label, name=f"Step({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=z, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, name=f"MoveTo({plant_name})"),
            PublishCmd("D_W_1", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOn({i})"),
            Wait(duration, name=f"Pulse({duration}s, {i})"),
            PublishCmd("D_W_0", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOff({i})"),
            LogPlantEvent(_make_log_fn(plant_index, plant_name),
                          name=f"LogWatered({i})"),
        ])

    # Past-tense summary spoken after the last plant — the browser-side
    # overlay announces the forward-tense label ("Watering 3 marigolds")
    # via SpeechSynthesis at task start, so the final TTS is the
    # confirmation, not the announcement. Always overrides the LLM's
    # intent.response (which is forward-tense and doesn't know N yet).
    summary = (f"Done watering {total} {target}." if total > 1
               else f"Done watering the {target}.")
    children.append(Respond(summary, name="Summarise"))
    children.append(TaskBoundary("end", name="EndTask"))

    return _seq(label, *children)


def _tree_water_all(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Walk every plant in the active map, watering each in turn.

    Replaces the prior fire-and-forget ``PublishCmd("P_4")`` (which the
    firmware dispatched silently with no UI feedback) with the same
    multi-plant pattern used by ``_tree_water``: TaskBoundary("start"),
    CheckAvailable, N x (CheckEstop, StepNotify, MoveTo, PumpOn, Wait,
    PumpOff, LogPlantEvent), summary, TaskBoundary("end"). Means:

      - The blocking overlay shows up during P_4 (Bug #2 from the
        second WSL test).
      - Per-plant event log rows so "today's care" is honest about
        what actually got watered.
      - Estop interrupts mid-sequence within ~100 ms rather than
        waiting on firmware-side completion.

    Fallback: if the active map is empty (loader failure / Pi has no
    map_handler) we publish bare P_4 and let the firmware handle it —
    better than refusing the user's "water everything" outright.
    """
    from growmate_pi.intent_server import find_all_plants_in_garden

    plants = find_all_plants_in_garden()
    if not plants:
        return _seq(
            "Water all (P_4 fallback)",
            CheckAvailable(bridge),
            PublishCmd("P_4", bridge, name="WaterAllPlants"),
            Respond(intent.response or "Watering all plants."),
        )

    def _plant_duration(p: Dict[str, Any]) -> int:
        try:
            return int(float(p.get("water_quantity") or 6.0))
        except Exception:
            return 6

    task_id = _uuid.uuid4().hex[:8]
    total = len(plants)
    label = f"Watering all {total} plants"

    children: List[py_trees.behaviour.Behaviour] = [
        TaskBoundary("start", task_id=task_id, label=label, total_steps=total,
                     name=f"BeginTask({total})"),
        CheckAvailable(bridge),
        # Same speak-then-move pause as _tree_water. See comment there.
        Wait(_ANNOUNCE_PAUSE_S, name="AnnouncePause"),
    ]

    for i, plant in enumerate(plants, start=1):
        x = float(plant["x"])
        y = float(plant["y"])
        z = 0.0
        duration = _plant_duration(plant)
        plant_name = str(plant.get("name") or f"plant {plant.get('index', '?')}")
        plant_index = int(plant.get("index", 0))
        step_label = f"{plant_name} ({i}/{total})"

        def _make_log_fn(p_idx: int, p_name: str) -> Callable[[], None]:
            def _fn() -> None:
                from growmate_pi.intent_server import _event_log
                if _event_log is None:
                    return
                _event_log.log(
                    event_type="watered",
                    plant_index=p_idx,
                    plant_name=p_name,
                    payload={"source": "water_all", "task_id": task_id},
                )
            return _fn

        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, step_label, name=f"Step({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=z, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, name=f"MoveTo({plant_name})"),
            PublishCmd("D_W_1", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOn({i})"),
            Wait(duration, name=f"Pulse({duration}s, {i})"),
            PublishCmd("D_W_0", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOff({i})"),
            LogPlantEvent(_make_log_fn(plant_index, plant_name),
                          name=f"LogWatered({i})"),
        ])

    children.append(Respond(f"Done watering all {total} plants.", name="Summarise"))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(label, *children)


def _tree_go_home(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Go home",
        CheckAvailable(bridge),
        PublishCmd("H_0", bridge, verify=True, timeout_s=HOME_TIMEOUT_S,
                   name="GoHome"),
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
                # Verified move so the reading is taken once the gantry has
                # actually arrived at the plant, not mid-travel.
                MoveTo(bridge, verify=True, timeout_s=MOVE_TIMEOUT_S),
            ]
        )
    # ReadSensor speaks the result ("the soil reads 512 — that's moist"), so no
    # trailing generic Respond — that would talk over the real reading.
    children.append(ReadSensor(bridge))
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
