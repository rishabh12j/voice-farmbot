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
    CheckDry,
    CheckEstop,
    EmergencyStop,
    EnsureTool,
    HOME_TIMEOUT_S,
    LogPlantEvent,
    MOVE_TIMEOUT_S,
    MoveTo,
    NoteSkip,
    PUMP_TIMEOUT_S,
    PublishCmd,
    ReadSensor,
    RecordSoil,
    Respond,
    StepNotify,
    SummariseSmart,
    TaskBoundary,
    Wait,
    WEED_PLUNGE_Z,
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

# Bed-scan grid (scan_bed). Step ~= camera FOV at the calibration height so the
# whole bed is covered; SCAN_Z MUST match the I_0 calibration height or the
# pixel->mm scale won't hold. Slow (small FOV) — a one-time setup; cap the extent
# via SCAN_MAX_*_MM for quick/partial runs.
SCAN_STEP_MM = 160.0
SCAN_Z = -250.0
SCAN_DWELL_S = 2.0       # let detection finish at a static spot before moving on
SCAN_MAX_X_MM: Optional[float] = None
SCAN_MAX_Y_MM: Optional[float] = None


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


def _move_to_named_target(
    bridge: FarmBotROS2Bridge,
    garden: GardenConfig,
    target: str,
) -> List[py_trees.behaviour.Behaviour]:
    """Guarded nodes to move to a NAMED target — position from the live
    active_map FIRST (authoritative, same source as water/move), config only
    for named non-plant locations. Shared by photo and check_sensor so every
    plant-targeting move uses one source of plant truth; the config
    garden.plants positions are a stale snapshot and must not drive motion.

    On a known map plant: CheckBounds+MoveTo on the map's explicit coords.
    Otherwise: the config ResolveTarget path (a named location, or — for an
    unknown target — a ResolveTarget that fails so CheckPlantFound aborts the
    tree cleanly, preserving photo/check_sensor's prior behaviour).
    """
    from growmate_pi.intent_server import find_plants_by_species

    matches = find_plants_by_species(target)
    if matches:
        p = matches[0]
        px, py, pz = float(p["x"]), float(p["y"]), 0.0
        return [
            CheckBounds(garden, x=px, y=py, z=pz),
            MoveTo(bridge, x=px, y=py, z=pz, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, check_mm=garden.position_verify_mm()),
        ]
    return [
        ResolveTarget(garden, target),
        CheckPlantFound(),
        CheckBounds(garden),
        MoveTo(bridge, verify=True, timeout_s=MOVE_TIMEOUT_S,
               check_mm=garden.position_verify_mm()),
    ]


# ---------- Per-action subtree builders ---------------------------------------


def _tree_move(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    # Explicit coordinate move (jog from Windows app — absolute coords already
    # computed client-side). The Pi does NOT trust the client's clamping: the
    # stress test showed an out-of-bounds M command published unguarded here,
    # so the safety prefix now includes an explicit-coords CheckBounds.
    if "x" in (intent.params or {}):
        x = float(intent.params["x"])
        y = float(intent.params.get("y", 0))
        z = float(intent.params.get("z", 0))
        return _seq(
            f"Move to ({x:.0f}, {y:.0f}, {z:.0f})",
            CheckAvailable(bridge),
            CheckBounds(garden, x=x, y=y, z=z),
            MoveTo(bridge, x=x, y=y, z=z, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   check_mm=garden.position_verify_mm()),
            Respond(intent.response),
        )
    # Named target move. Resolution order (single source of plant truth):
    #   1. the live active_map — where the plant ACTUALLY is (same source as
    #      _tree_water). This is authoritative; the config plant positions are
    #      a frozen snapshot that calibrate.py never refreshes, so they must
    #      not drive motion.
    #   2. the config, ONLY for named non-plant LOCATIONS (e.g. "home", a
    #      named corner) that live in config and aren't in the plant map.
    #   3. neither -> clean refusal (success + spoken), like _tree_water: an
    #      unknown place is a user-vocabulary issue, not a robot malfunction.
    target = (intent.target or "").strip()
    if not target:
        return _seq(
            "Move (no target)",
            Respond("I need to know where to move. Try 'move to the lettuce'."),
        )

    from growmate_pi.intent_server import find_plants_by_species

    # Named moves run under a TaskBoundary so the UI overlay tracks them like
    # a watering (big STOP button on screen during ANY motion), and the final
    # Respond is PAST-TENSE — it sits after the verified MoveTo in the
    # Sequence, so "Arrived" is only ever spoken once the gantry actually
    # confirmed at the target (honest-or-blank). The LLM's forward-tense
    # intent.response remains the announcement on the async path.
    task_id = _uuid.uuid4().hex[:8]
    matches = find_plants_by_species(target)
    if matches:
        p = matches[0]
        px, py, pz = float(p["x"]), float(p["y"]), 0.0
        # Full safety prefix on the explicit coords the map gave us — the map
        # branch used to skip CheckBounds; as the primary path it must guard
        # bounds like every other move (research contract, CLAUDE.md rule 2).
        return _seq(
            f"Move to {target} (map)",
            TaskBoundary("start", task_id=task_id,
                         label=f"Moving to the {target}", total_steps=1,
                         name="BeginTask(move)"),
            CheckAvailable(bridge),
            CheckBounds(garden, x=px, y=py, z=pz),
            StepNotify(1, f"moving to the {target}", name="Step(1/1)"),
            MoveTo(bridge, x=px, y=py, z=pz, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, check_mm=garden.position_verify_mm()),
            Respond(f"Arrived at the {target}."),
            TaskBoundary("end", name="EndTask"),
        )
    if garden.resolve_target(target) is not None:
        # A named location (not a plant) — resolve from config, fully guarded.
        return _seq(
            f"Move to {intent.target}",
            TaskBoundary("start", task_id=task_id,
                         label=f"Moving to {target}", total_steps=1,
                         name="BeginTask(move)"),
            *_safety_and_target(bridge, garden, intent.target),
            StepNotify(1, f"moving to {target}", name="Step(1/1)"),
            MoveTo(bridge, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   check_mm=garden.position_verify_mm()),
            Respond(f"Arrived at {target}."),
            TaskBoundary("end", name="EndTask"),
        )
    return _seq(
        f"Move to {target} (no match)",
        Respond(f"I don't see any {target} in this garden. "
                "Tell me a different plant."),
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
    tools = garden.tools_by_name()
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
        # Auto-mount the watering nozzle (idempotent: no-op if already on).
        # Verifies via pin 63 like MountTool, so a failed mount returns
        # FAILURE and aborts the water before any pump fires — the safety
        # prefix (CheckToolMounted) made real, not assumed.
        EnsureTool(bridge, "watering_nozzle", tools,
                   pin_usable=garden.tool_verify_pin()),
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
        # completed; that's the Tier B "honest log" payoff. duration_s is the
        # EFFECT slot of the memory row (how much water this plant received).
        def _make_log_fn(p_idx: int, p_name: str,
                         p_dur: int) -> Callable[[], None]:
            def _fn() -> None:
                # Local import for the same circular-avoidance reason as above.
                from growmate_pi.intent_server import _event_log
                if _event_log is None:
                    return
                _event_log.log(
                    event_type="watered",
                    plant_index=p_idx,
                    plant_name=p_name,
                    payload={"source": "multi_plant_water", "task_id": task_id,
                             "duration_s": p_dur},
                )
            return _fn

        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, step_label, name=f"Step({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=z, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, name=f"MoveTo({plant_name})",
                   check_mm=garden.position_verify_mm()),
            PublishCmd("D_W_1", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOn({i})"),
            Wait(duration, name=f"Pulse({duration}s, {i})"),
            PublishCmd("D_W_0", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOff({i})"),
            LogPlantEvent(_make_log_fn(plant_index, plant_name, duration),
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

    tools = garden.tools_by_name()
    plants = find_all_plants_in_garden()
    if not plants:
        return _seq(
            "Water all (P_4 fallback)",
            CheckAvailable(bridge),
            EnsureTool(bridge, "watering_nozzle", tools,
                   pin_usable=garden.tool_verify_pin()),
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
        # Auto-mount the watering nozzle before the walk (idempotent; verified
        # via pin 63). See _tree_water for the safety rationale.
        EnsureTool(bridge, "watering_nozzle", tools,
                   pin_usable=garden.tool_verify_pin()),
    ]

    for i, plant in enumerate(plants, start=1):
        x = float(plant["x"])
        y = float(plant["y"])
        z = 0.0
        duration = _plant_duration(plant)
        plant_name = str(plant.get("name") or f"plant {plant.get('index', '?')}")
        plant_index = int(plant.get("index", 0))
        step_label = f"{plant_name} ({i}/{total})"

        def _make_log_fn(p_idx: int, p_name: str,
                         p_dur: int) -> Callable[[], None]:
            def _fn() -> None:
                from growmate_pi.intent_server import _event_log
                if _event_log is None:
                    return
                _event_log.log(
                    event_type="watered",
                    plant_index=p_idx,
                    plant_name=p_name,
                    payload={"source": "water_all", "task_id": task_id,
                             "duration_s": p_dur},
                )
            return _fn

        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, step_label, name=f"Step({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=z, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, name=f"MoveTo({plant_name})",
                   check_mm=garden.position_verify_mm()),
            PublishCmd("D_W_1", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOn({i})"),
            Wait(duration, name=f"Pulse({duration}s, {i})"),
            PublishCmd("D_W_0", bridge, verify=True,
                       timeout_s=PUMP_TIMEOUT_S, name=f"PumpOff({i})"),
            LogPlantEvent(_make_log_fn(plant_index, plant_name, duration),
                          name=f"LogWatered({i})"),
        ])

    children.append(Respond(f"Done watering all {total} plants.", name="Summarise"))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(label, *children)


def _tree_water_smart(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Phase 1b: read each target plant's soil, water only the dry ones.

    Two passes — the soil probe and the watering nozzle are different tool heads,
    so we don't swap per plant. Pass 1: mount the probe, visit each plant, read
    soil (stored per plant). Pass 2: mount the nozzle, water only plants that
    read dry (or that we couldn't read). Honest per-plant log + a summary of how
    many were watered vs left moist. Same safety prefix + tick-and-verify as the
    other water trees.

    Falls back cleanly on no target / no map matches, like ``_tree_water``.
    """
    from growmate_pi.intent_server import (
        find_all_plants_in_garden,
        find_plants_by_species,
    )

    # Audit F4: until this greenhouse's soil thresholds are MEASURED, a
    # dry/wet decision is a guess about sensor polarity — refuse cleanly
    # rather than water on it (Q-design; honest-or-blank). check_sensor still
    # works (it speaks the raw number), and water/water_all are unaffected.
    if not garden.soil_calibrated():
        return _seq(
            "Water smart (uncalibrated)",
            Respond("My soil sensor isn't calibrated for this garden yet, so "
                    "I can't tell dry from wet. Say 'water all the "
                    f"{(intent.target or 'plants').strip()}' instead, or run "
                    "the soil calibration first."),
        )

    target = (intent.target or "").strip()
    if target:
        matches = find_plants_by_species(target)
        if not matches:
            return _seq(
                f"Water smart {target} (no match)",
                Respond(f"I don't see any {target} in this garden. "
                        "Tell me a different plant."),
            )
        scope = target
    else:
        # "water the dry ones" with no species -> check every plant.
        matches = find_all_plants_in_garden()
        if not matches:
            return _seq(
                "Water smart (empty map)",
                Respond("I don't have any plants in the map to check."),
            )
        scope = "plants"

    def _plant_duration(p: Dict[str, Any]) -> int:
        try:
            return int(float(p.get("water_quantity") or 6.0))
        except Exception:
            return 6

    tools = garden.tools_by_name()
    dry_above, wet_below = garden.soil_thresholds()
    task_id = _uuid.uuid4().hex[:8]
    total = len(matches)
    # Shared between the two passes: per-plant readings + watered/skipped tallies.
    state: Dict[str, Any] = {"readings": {}, "watered": 0, "skipped": 0}
    label = f"Smart-watering {total} {scope}"

    children: List[py_trees.behaviour.Behaviour] = [
        TaskBoundary("start", task_id=task_id, label=label, total_steps=total,
                     name=f"BeginTask({total})"),
        CheckAvailable(bridge),
        Wait(_ANNOUNCE_PAUSE_S, name="AnnouncePause"),
        # --- Pass 1: sense every plant with the soil probe ---
        EnsureTool(bridge, "soil_sensor", tools,
                   pin_usable=garden.tool_verify_pin()),
    ]
    for i, plant in enumerate(matches, start=1):
        x, y = float(plant["x"]), float(plant["y"])
        pname = str(plant.get("name") or f"plant {plant.get('index', '?')}")
        pidx = int(plant.get("index", i))
        children.extend([
            CheckEstop(name=f"CheckEstop.sense({i})"),
            StepNotify(i, f"checking {pname} ({i}/{total})", name=f"Sense({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=0.0, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   name=f"MoveTo.sense({pname})",
                   check_mm=garden.position_verify_mm()),
            RecordSoil(bridge, state["readings"], pidx, pname,
                       dry_above=dry_above, wet_below=wet_below),
        ])

    # --- Pass 2: water only the dry ones with the nozzle ---
    children.append(EnsureTool(bridge, "watering_nozzle", tools,
                   pin_usable=garden.tool_verify_pin()))
    for i, plant in enumerate(matches, start=1):
        x, y = float(plant["x"]), float(plant["y"])
        pname = str(plant.get("name") or f"plant {plant.get('index', '?')}")
        pidx = int(plant.get("index", i))
        dur = _plant_duration(plant)

        def _make_log_fn(p_idx: int, p_name: str,
                         p_dur: int) -> Callable[[], None]:
            def _fn() -> None:
                from growmate_pi.intent_server import _event_log
                state["watered"] += 1
                if _event_log is not None:
                    # Effect: how much water AND the soil reading that
                    # justified it (pass 1's probe) — the full memory row.
                    _event_log.log(
                        event_type="watered", plant_index=p_idx, plant_name=p_name,
                        payload={"source": "water_smart", "task_id": task_id,
                                 "duration_s": p_dur,
                                 "soil_before": state["readings"].get(p_idx)})
            return _fn

        water_seq = _seq(
            f"WaterIfDry({pname})",
            CheckDry(state["readings"], pidx, dry_above=dry_above,
                     name=f"CheckDry({pname})"),
            CheckEstop(name=f"CheckEstop.water({i})"),
            StepNotify(i, f"watering {pname} ({i}/{total})", name=f"Water({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=0.0, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   name=f"MoveTo.water({pname})",
                   check_mm=garden.position_verify_mm()),
            PublishCmd("D_W_1", bridge, verify=True, timeout_s=PUMP_TIMEOUT_S,
                       name=f"PumpOn({i})"),
            Wait(dur, name=f"Pulse({dur}s, {i})"),
            PublishCmd("D_W_0", bridge, verify=True, timeout_s=PUMP_TIMEOUT_S,
                       name=f"PumpOff({i})"),
            LogPlantEvent(_make_log_fn(pidx, pname, dur), name=f"LogWatered({i})"),
        )
        # Selector: water if dry, else note the skip — either way SUCCESS so the
        # parent sequence proceeds to the next plant.
        per_plant = py_trees.composites.Selector(name=f"Plant({i}/{total})", memory=True)
        per_plant.add_children([water_seq, NoteSkip(state, pname, name=f"Skip({pname})")])
        children.append(per_plant)

    children.append(SummariseSmart(state, total, scope))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(label, *children)


def _tree_go_home(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    # TaskBoundary so the overlay (and its STOP button) covers the homing
    # travel too; past-tense Respond only after the verified H_0 completes.
    return _seq(
        "Go home",
        TaskBoundary("start", task_id=_uuid.uuid4().hex[:8],
                     label="Heading home", total_steps=1,
                     name="BeginTask(home)"),
        CheckAvailable(bridge),
        StepNotify(1, "heading home", name="Step(1/1)"),
        PublishCmd("H_0", bridge, verify=True, timeout_s=HOME_TIMEOUT_S,
                   name="GoHome"),
        Respond("The robot is home."),
        TaskBoundary("end", name="EndTask"),
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
    # Target position comes from the live active_map (via _move_to_named_target),
    # not the stale config — same source as move/water.
    total = 2 if intent.target else 1
    children = [
        TaskBoundary("start", task_id=_uuid.uuid4().hex[:8],
                     label="Taking a photo", total_steps=total,
                     name="BeginTask(photo)"),
        CheckAvailable(bridge),
    ]
    if intent.target:
        children.append(StepNotify(1, f"moving to the {intent.target}",
                                   name=f"Step(1/{total})"))
        children.extend(_move_to_named_target(bridge, garden, intent.target))
    children.append(StepNotify(total, "taking the photo",
                               name=f"Step({total}/{total})"))
    children.append(PublishCmd("I_1", bridge, name="TakePhoto"))
    # Honest wording: I_1 is dispatched to the camera service and is NOT
    # confirmable over /busy_state — say what we actually know.
    children.append(Respond("I've asked the camera for a photo."))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(f"Photo of {intent.target or 'current pos'}", *children)


def _tree_panorama(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Panorama",
        CheckAvailable(bridge),
        PublishCmd("I_2", bridge, name="Panorama"),
        # Honest wording — the stitch runs in the camera service and takes a
        # while; we know it was requested, not that it finished.
        Respond("I've asked the camera to build the panorama — "
                "it takes a little while."),
    )


def _tree_scan_weeds(bridge, intent: Intent) -> py_trees.behaviour.Behaviour:
    return _seq(
        "Scan weeds",
        CheckAvailable(bridge),
        PublishCmd("I_4", bridge, name="ScanWeeds"),
        # Honest wording — detection runs in the camera service; we know it
        # was requested, not that it finished.
        Respond("I've asked the camera to scan for weeds. Ask me to clear "
                "them once it's done."),
    )


def _tree_clear_weeds(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Phase 3: physically remove detected weeds with the weeder tool.

    Reads the weeds from the last detection (``find_weeds`` -> other_plants.yaml,
    robot mm), mounts the weeder, and for each in-bounds weed: move over it at
    z=0 -> plunge to WEED_PLUNGE_Z -> raise. Honest per-weed log + a summary.
    If no weeds are on record it asks for a scan first rather than guessing.
    Full safety prefix + tick-and-verify on every move, e-stop checkpoints.
    """
    from growmate_pi.intent_server import find_weeds

    weeds = find_weeds()
    if not weeds:
        return _seq(
            "Clear weeds (none on record)",
            CheckAvailable(bridge),
            Respond("I don't have any weeds on record yet — say 'scan for weeds' "
                    "first, then ask me to clear them."),
        )

    # Detection can occasionally place a circle off-bed; only act on in-bounds
    # weeds (safety — the BT never drives the gantry out of the workspace).
    x_max = float(garden.workspace.get("x_max", 5691.2))
    y_max = float(garden.workspace.get("y_max", 2734.0))
    in_bounds = [w for w in weeds if 0.0 <= w["x"] <= x_max and 0.0 <= w["y"] <= y_max]
    skipped_oob = len(weeds) - len(in_bounds)
    if not in_bounds:
        return _seq(
            "Clear weeds (all out of bounds)",
            CheckAvailable(bridge),
            Respond("The weeds I found are outside the bed bounds — skipping for "
                    "safety. Try scanning again."),
        )

    tools = garden.tools_by_name()
    task_id = _uuid.uuid4().hex[:8]
    total = len(in_bounds)
    label = f"Clearing {total} weeds"

    children: List[py_trees.behaviour.Behaviour] = [
        TaskBoundary("start", task_id=task_id, label=label, total_steps=total,
                     name=f"BeginTask({total})"),
        CheckAvailable(bridge),
        Wait(_ANNOUNCE_PAUSE_S, name="AnnouncePause"),
        EnsureTool(bridge, "weeder", tools,
                   pin_usable=garden.tool_verify_pin()),
    ]

    for i, weed in enumerate(in_bounds, start=1):
        x, y = weed["x"], weed["y"]

        def _make_log_fn(wx: float, wy: float) -> Callable[[], None]:
            def _fn() -> None:
                from growmate_pi.intent_server import _event_log
                if _event_log is not None:
                    _event_log.log(
                        event_type="weeded", plant_index=0,
                        plant_name=f"weed @({wx:.0f},{wy:.0f})",
                        payload={"source": "clear_weeds", "task_id": task_id,
                                 "x": wx, "y": wy})
            return _fn

        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, f"weed {i}/{total}", name=f"Weed({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=0.0, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   name=f"OverWeed({i})",
                   check_mm=garden.position_verify_mm()),
            MoveTo(bridge, x=x, y=y, z=WEED_PLUNGE_Z, verify=True,
                   timeout_s=MOVE_TIMEOUT_S, name=f"Plunge({i})",
                   check_mm=garden.position_verify_mm()),
            MoveTo(bridge, x=x, y=y, z=0.0, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   name=f"Raise({i})",
                   check_mm=garden.position_verify_mm()),
            LogPlantEvent(_make_log_fn(x, y), name=f"LogWeeded({i})"),
        ])

    summary = f"Cleared {total} weeds."
    if skipped_oob:
        summary += f" Skipped {skipped_oob} outside the bed."
    children.append(Respond(summary, name="Summarise"))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(label, *children)


def _tree_scan_bed(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Phase 2: scan the bed, detecting plants at each grid position.

    Walks a snake grid at SCAN_STEP_MM: MoveTo (verified) -> I_4 (detect at the
    current position) -> short dwell, so the detection output accumulates plant
    positions across the whole bed. Clears the previous scan first. Feeds
    find_plants -> voice labelling. Slow (the camera FOV is small) — a one-time
    map-setup pass; cap the extent with SCAN_MAX_*_MM for quick/partial runs.
    """
    import math as _math
    from growmate_pi.intent_server import clear_detections

    x_max = float(garden.workspace.get("x_max", 5691.2))
    y_max = float(garden.workspace.get("y_max", 2734.0))
    if SCAN_MAX_X_MM is not None:
        x_max = min(x_max, SCAN_MAX_X_MM)
    if SCAN_MAX_Y_MM is not None:
        y_max = min(y_max, SCAN_MAX_Y_MM)

    nx = max(1, int(_math.ceil(x_max / SCAN_STEP_MM)))
    ny = max(1, int(_math.ceil(y_max / SCAN_STEP_MM)))
    positions: List[tuple] = []
    for ix in range(nx + 1):
        x = min(ix * SCAN_STEP_MM, x_max)
        rows = range(ny + 1) if ix % 2 == 0 else range(ny, -1, -1)  # snake
        for iy in rows:
            positions.append((x, min(iy * SCAN_STEP_MM, y_max)))

    clear_detections()  # start the scan from a clean slate
    task_id = _uuid.uuid4().hex[:8]
    total = len(positions)
    label = f"Scanning the bed ({total} spots)"

    children: List[py_trees.behaviour.Behaviour] = [
        TaskBoundary("start", task_id=task_id, label=label, total_steps=total,
                     name=f"BeginTask({total})"),
        CheckAvailable(bridge),
        Wait(_ANNOUNCE_PAUSE_S, name="AnnouncePause"),
    ]
    for i, (x, y) in enumerate(positions, start=1):
        children.extend([
            CheckEstop(name=f"CheckEstop({i})"),
            StepNotify(i, f"scanning {i}/{total}", name=f"Scan({i}/{total})"),
            MoveTo(bridge, x=x, y=y, z=SCAN_Z, verify=True, timeout_s=MOVE_TIMEOUT_S,
                   name=f"MoveTo.scan({i})",
                   check_mm=garden.position_verify_mm()),
            PublishCmd("I_4", bridge, name=f"Detect({i})"),
            Wait(SCAN_DWELL_S, name=f"Dwell({i})"),
        ])
    children.append(Respond(
        "I've scanned the bed. Say 'find the plants' to label what I found.",
        name="Summarise"))
    children.append(TaskBoundary("end", name="EndTask"))
    return _seq(label, *children)


def _tree_find_plants(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Phase 2: stage vision-detected plants for voice labelling.

    Reads the last scan's detections (find_detected_plants -> robot mm), stores
    them as 'pending', and asks the user to name them by region. Species comes
    from the follow-up label_plants ("the left bed is lettuce"); vision supplied
    position + canopy size. If nothing's detected, asks for a scan first.
    """
    from growmate_pi.intent_server import find_detected_plants, write_pending_plants

    plants = find_detected_plants()
    if not plants:
        return _seq(
            "Find plants (none detected)",
            CheckAvailable(bridge),
            Respond("I don't see any detected plants yet — scan the bed first, "
                    "then ask me to find the plants."),
        )
    write_pending_plants(plants)
    n = len(plants)
    return _seq(
        f"Find plants ({n})",
        CheckAvailable(bridge),
        Respond(f"I found {n} plants. Tell me what they are — for example, "
                f"'the left bed is lettuce' or 'they're all tomatoes'."),
    )


def _tree_label_plants(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    """Phase 2: assign a species to a region of pending plants and add them.

    Pairs with find_plants: takes a species (intent.target / params['species'])
    and an optional region (params['region']: left/middle/right/front/back/all),
    selects the matching pending plants, adds each to the map via P_1, and drops
    them from pending. Repeat until the map is built. The species the user speaks;
    vision already gave the position + canopy size.
    """
    from growmate_pi.intent_server import (
        filter_plants_by_region,
        read_pending_plants,
        write_pending_plants,
    )

    # Keep the LLM flat: it puts the whole phrase in `target` (e.g. "left
    # lettuce", "all tomatoes", "mixed pepper"); parse region + species here.
    _REGIONS = {"left", "leftmost", "right", "rightmost", "middle", "centre",
                "center", "front", "back", "all", "everything", "rest"}
    _FILLERS = {"the", "bed", "beds", "is", "are", "be", "they", "they're",
                "them", "ones", "one", "plants", "plant", "row", "section", "a"}
    raw = str(intent.target or (intent.params or {}).get("species") or "").lower()
    region = (intent.params or {}).get("region")  # explicit param wins if present
    species_tokens = []
    for tok in raw.replace(",", " ").split():
        if tok in _REGIONS and region is None:
            region = tok
        elif tok not in _FILLERS:
            species_tokens.append(tok)
    species = " ".join(species_tokens).strip()

    pending = read_pending_plants()
    if not pending:
        return _seq(
            "Label plants (nothing pending)",
            Respond("There's nothing to label yet — say 'find the plants' first."),
        )
    if not species:
        return _seq(
            "Label plants (no species)",
            Respond("Tell me the species too, like 'the left bed is lettuce'."),
        )

    selected = filter_plants_by_region(pending, region, garden.workspace)
    if not selected:
        where = f"the {region}" if region else "that area"
        return _seq(
            "Label plants (region empty)",
            Respond(f"I don't have any plants to label in {where}."),
        )

    # P_1 plant_name is space-split by the controller, so keep it one token.
    name = species.replace(" ", "_")
    children: List[py_trees.behaviour.Behaviour] = [CheckAvailable(bridge)]
    for p in selected:
        # P_1 x y z exclusion_r canopy_r water_qty max_z plant_name growth_stage
        cmd = (f"P_1 {p['x']:.1f} {p['y']:.1f} 0.0 50.0 "
               f"{float(p.get('radius', 30.0)):.1f} 2.0 100.0 {name} Seedling")
        children.append(PublishCmd(cmd, bridge,
                                   name=f"AddPlant({p['x']:.0f},{p['y']:.0f})"))

    remaining = [p for p in pending if p not in selected]
    write_pending_plants(remaining)

    added, left = len(selected), len(remaining)
    msg = f"Added {added} {species}."
    msg += f" {left} plants left to label." if left else " That's everything — map updated."
    children.append(Respond(msg, name="Summarise"))
    return _seq(f"Label {added} as {species}", *children)


def _tree_check_sensor(bridge, garden, intent: Intent) -> py_trees.behaviour.Behaviour:
    # The soil probe is a tool head — make sure it's mounted before reading
    # (no-op if it already is). Move to the plant afterwards so the probe goes
    # in at the right spot.
    total = 3 if intent.target else 2
    children = [
        TaskBoundary("start", task_id=_uuid.uuid4().hex[:8],
                     label="Checking the soil", total_steps=total,
                     name="BeginTask(sensor)"),
        CheckAvailable(bridge),
        StepNotify(1, "getting the soil probe", name=f"Step(1/{total})"),
        EnsureTool(bridge, "soil_sensor", garden.tools_by_name(),
                   pin_usable=garden.tool_verify_pin()),
    ]
    if intent.target:
        # Move to the plant using the live active_map position (verified move,
        # so the reading is taken once the gantry has actually arrived) — same
        # source as move/water, not the stale config.
        children.append(StepNotify(2, f"moving to the {intent.target}",
                                   name=f"Step(2/{total})"))
        children.extend(_move_to_named_target(bridge, garden, intent.target))
    children.append(StepNotify(total, "reading the soil",
                               name=f"Step({total}/{total})"))
    # ReadSensor speaks the result ("the soil reads 512 — that's moist"), so no
    # trailing generic Respond — that would talk over the real reading. While
    # uncalibrated it speaks the raw value without a dry/wet verdict (F4).
    dry_above, wet_below = garden.soil_thresholds()
    children.append(ReadSensor(bridge, dry_above=dry_above, wet_below=wet_below,
                               calibrated=garden.soil_calibrated()))
    children.append(TaskBoundary("end", name="EndTask"))
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
    if a == "water_smart":
        return _tree_water_smart(bridge, garden, intent)
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
    if a == "clear_weeds":
        return _tree_clear_weeds(bridge, garden, intent)
    if a == "scan_bed":
        return _tree_scan_bed(bridge, garden, intent)
    if a == "find_plants":
        return _tree_find_plants(bridge, garden, intent)
    if a == "label_plants":
        return _tree_label_plants(bridge, garden, intent)
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
