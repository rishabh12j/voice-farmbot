"""End-to-end sim verification — no Pi, no FarmBot, no rclpy.

Builds the bridge in sim mode, the garden config, and a tree for several
representative intents, then ticks each tree and prints what *would* have
been published to ``keyboard_topic``.

Run from the repo root (WSL or Windows) with:

    PYTHONPATH=src python3 -m growmate_pi.verify_sim

Expected output: each scenario shows the simulated FarmBot commands in
order, the final tree status, and the aggregated TTS text. No errors.
"""

from __future__ import annotations

from pathlib import Path

from growmate_pi.bt.builder import build_tree
from growmate_pi.bt.executor import execute_tree, read_tts_text
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import Intent, IntentRequest
from growmate_pi.tool_state import get_tool_state


SCENARIOS: list[IntentRequest] = [
    IntentRequest(
        intents=[Intent(action="water", target="tomatoes", response="Watering tomatoes!")],
        raw_text="water the tomatoes",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="move", target="lettuce", response="Moving to the lettuce.")],
        raw_text="move to the lettuce",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="water_all", target=None, response="Watering everything.")],
        raw_text="water everything",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="go_home", target=None, response="Heading home.")],
        raw_text="go home",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="check_sensor", target="lettuce", response="Checking lettuce.")],
        raw_text="check on the lettuce",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="water_smart", target="marigold",
                        response="Checking which marigolds need water.")],
        raw_text="water the dry marigolds",
        client_id="sim",
    ),
    IntentRequest(
        # No detection on record -> clean "scan first" refusal (success).
        intents=[Intent(action="clear_weeds", target=None, response="Clearing weeds.")],
        raw_text="clear the weeds",
        client_id="sim",
    ),
    IntentRequest(
        # No scan on record -> clean "scan first" (success).
        intents=[Intent(action="find_plants", target=None, response="Finding plants.")],
        raw_text="find the plants",
        client_id="sim",
    ),
    IntentRequest(
        # Nothing staged -> clean "find first" (success).
        intents=[Intent(action="label_plants", target="left lettuce",
                        response="Labeling.")],
        raw_text="the left bed is lettuce",
        client_id="sim",
    ),
    IntentRequest(
        intents=[
            Intent(action="water", target="tomatoes", response="Watering tomatoes."),
            Intent(action="go_home", target=None, response="Now heading home."),
        ],
        raw_text="water the tomatoes and then go home",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="water", target="bananas", response="Watering bananas.")],
        raw_text="water the bananas",  # unknown plant -> clean refusal
        client_id="sim",
    ),
    # --- tool verbs + the resolve-or-refuse guard ---------------------------
    # Order is load-bearing and the scenarios form a chain: the nozzle is on
    # from the water_smart scenario above, so this stows it and leaves the UTM
    # EMPTY. The two refusal scenarios that follow depend on that: with the
    # probe already mounted EnsureTool would no-op and publish nothing, so a
    # regression that mounts before refusing would slip through unseen.
    IntentRequest(
        intents=[Intent(action="stow_tool", target=None, response="Putting it back.")],
        raw_text="put the tool back",
        client_id="sim",
    ),
    IntentRequest(
        # Unknown target must refuse BEFORE the probe is fetched — the old
        # order mounted first and then aborted the tree as a failure.
        intents=[Intent(action="check_sensor", target="dragonfruit",
                        response="Checking the dragonfruit.")],
        raw_text="check on the dragonfruit",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="photo", target="dragonfruit",
                        response="Photographing the dragonfruit.")],
        raw_text="take a photo of the dragonfruit",
        client_id="sim",
    ),
    IntentRequest(
        # Spoken alias, not the config key: "soil probe" -> soil_sensor (T_1_1).
        intents=[Intent(action="mount_tool", target="soil probe",
                        response="Fetching the soil probe.")],
        raw_text="pick up the soil probe",
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="mount_tool", target="jackhammer",
                        response="Fetching the jackhammer.")],
        raw_text="pick up the jackhammer",  # not a tool -> clean refusal
        client_id="sim",
    ),
    IntentRequest(
        intents=[Intent(action="stow_tool", target=None, response="Putting it back.")],
        raw_text="put the probe away",
        client_id="sim",
    ),
    IntentRequest(
        # UTM is empty now: stowing nothing is a clean refusal, not a failure.
        intents=[Intent(action="stow_tool", target=None, response="Putting it back.")],
        raw_text="take the tool off",
        client_id="sim",
    ),
    # Emergency LATCHES the estop, so it stays last — anything after it would
    # abort on the latch rather than on its own merits.
    IntentRequest(
        intents=[Intent(action="emergency_stop", target=None, response="Stopping.")],
        raw_text="stop",
        emergency=True,
        client_id="sim",
    ),
]


def main() -> int:
    config_path = Path(__file__).parent / "config" / "farmbot.yaml"
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    garden = GardenConfig(config_path)
    # Bay indices are config, not constants — assert against the real ones so
    # the tool scenarios don't quietly rot if a bay is renumbered.
    tools = garden.tools_by_name()

    failures = 0
    for idx, req in enumerate(SCENARIOS, start=1):
        print(f"\n=== Scenario {idx}: '{req.raw_text}' ===")
        bridge.command_log.clear()
        root = build_tree(bridge, garden, req.intents, emergency=req.emergency)
        result = execute_tree(root)
        tts = read_tts_text()

        print(f"  Tree     : {result.label}")
        print(f"  Status   : {result.status}")
        print(f"  Commands : {[r.command for r in bridge.command_log]}")
        print(f"  TTS      : {tts!r}")
        cmds = [r.command for r in bridge.command_log]
        # Task 1 (tool-mount in the demo): plain watering must auto-mount the
        # nozzle (T_<idx>_1 — the controller's underscored grammar) BEFORE the
        # first move. Checked on the first water scenario, where the tool
        # state starts empty so a real mount is emitted (later water
        # scenarios no-op because the nozzle is already on).
        if req.raw_text == "water the tomatoes":
            first_move = next((k for k, c in enumerate(cmds) if c.startswith("M ")), None)
            mount = next((k for k, c in enumerate(cmds)
                          if c.startswith("T_") and c.endswith("_1")), None)
            if mount is None or first_move is None or mount > first_move:
                failures += 1
                print("  -> expected nozzle mount (T*_1) BEFORE first move; "
                      f"mount={mount} first_move={first_move}")

        tool_cmds = [c for c in cmds if c.startswith("T_")]

        # A1 regression guard: an unresolvable target must be refused BEFORE
        # any tool is fetched. Asserting the ABSENCE of the mount is the whole
        # point — the bug was mounting the probe and only then discovering
        # there was nothing to check, so a status-only check would pass.
        if "dragonfruit" in req.raw_text:
            if result.status != "success" or "don't see" not in tts.lower():
                failures += 1
                print("  -> expected clean refusal (success + \"I don't see any\"), "
                      f"got {result.status} / {tts!r}")
            if tool_cmds:
                failures += 1
                print(f"  -> refused, but fetched a tool first: {tool_cmds}")
            if cmds:
                failures += 1
                print(f"  -> refusal must publish nothing, got {cmds}")

        elif "bananas" in req.raw_text:
            # No-match is a CLEAN refusal: success + an "I don't see any" message,
            # not a hard failure (Q-design — see _tree_water).
            if result.status != "success" or "don't see" not in tts.lower():
                failures += 1
                print("  -> expected clean refusal (success + \"I don't see any\"), "
                      f"got {result.status} / {tts!r}")

        elif req.raw_text == "put the tool back":
            # The nozzle is on from the water_smart scenario; stowing must
            # publish that head's unmount and leave ToolState empty.
            want = f"T_{tools['watering_nozzle']}_2"
            if want not in cmds:
                failures += 1
                print(f"  -> expected {want} (stow the mounted nozzle), got {cmds}")
            if get_tool_state().current() is not None:
                failures += 1
                print(f"  -> UTM should be empty after a stow, ToolState="
                      f"{get_tool_state().current()!r}")

        elif req.raw_text == "pick up the soil probe":
            # Spoken alias resolved to the config key, fresh mount (UTM empty).
            want = f"T_{tools['soil_sensor']}_1"
            if want not in cmds:
                failures += 1
                print(f"  -> expected {want} ('soil probe' -> soil_sensor), got {cmds}")
            if get_tool_state().current() != "soil_sensor":
                failures += 1
                print(f"  -> ToolState should be soil_sensor, got "
                      f"{get_tool_state().current()!r}")

        elif req.raw_text == "pick up the jackhammer":
            if result.status != "success" or "don't have" not in tts.lower():
                failures += 1
                print("  -> expected clean refusal (success + \"I don't have\"), "
                      f"got {result.status} / {tts!r}")
            if tool_cmds:
                failures += 1
                print(f"  -> refused an unknown tool but still moved the UTM: {tool_cmds}")

        elif req.raw_text == "put the probe away":
            want = f"T_{tools['soil_sensor']}_2"
            if want not in cmds:
                failures += 1
                print(f"  -> expected {want} (stow the probe), got {cmds}")
            if get_tool_state().current() is not None:
                failures += 1
                print(f"  -> UTM should be empty after a stow, ToolState="
                      f"{get_tool_state().current()!r}")

        elif req.raw_text == "take the tool off":
            # Nothing on: a clean spoken refusal, no motion, no failure.
            if result.status != "success" or "no tool" not in tts.lower():
                failures += 1
                print("  -> expected clean refusal (success + \"no tool\"), "
                      f"got {result.status} / {tts!r}")
            if cmds:
                failures += 1
                print(f"  -> stowing an empty UTM must publish nothing, got {cmds}")

        elif result.status == "failure":
            failures += 1
            print("  -> UNEXPECTED FAILURE")

    # Sim-level USC tripwire (audit F2): the bridge records any motion/pump
    # command published while a tool choreography still had queued moves.
    if bridge.sim_interleave_violations:
        failures += 1
        print("\nINTERLEAVE VIOLATIONS — motion published while a tool "
              f"choreography was still running: {bridge.sim_interleave_violations}")

    print(f"\n{'-' * 60}")
    print(f"Failures: {failures}/{len(SCENARIOS)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
