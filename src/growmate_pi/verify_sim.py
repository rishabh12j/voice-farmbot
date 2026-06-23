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
        raw_text="water the bananas",  # should fail: unknown plant
        client_id="sim",
    ),
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
        is_refusal = "bananas" in req.raw_text
        if is_refusal:
            # No-match is a CLEAN refusal: success + an "I don't see any" message,
            # not a hard failure (Q-design — see _tree_water).
            if result.status != "success" or "don't see" not in tts.lower():
                failures += 1
                print("  -> expected clean refusal (success + \"I don't see any\"), "
                      f"got {result.status} / {tts!r}")
        elif result.status == "failure":
            failures += 1
            print("  -> UNEXPECTED FAILURE")

    print(f"\n{'-' * 60}")
    print(f"Failures: {failures}/{len(SCENARIOS)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
