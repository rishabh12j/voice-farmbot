"""Headless ROS2 voice controller node.

A true drop-in replacement for ``keyboard_controller`` from the
``farmbot_controllers`` package. Run this instead of (or alongside) the
keyboard teleop node, and instead of typing commands like ``M 400 200 -100``
you type natural language like ``water the tomatoes`` and the BT pipeline
does the translation.

No web UI, no mic capture — just a terminal prompt. For the full voice
experience use ``voice_app`` instead.

Usage::

    # In one terminal, bring up the FarmBot stack as usual:
    ros2 launch farmbot_bringup standard.launch.py

    # In another terminal, run this node instead of keyboard_controller:
    ros2 run growmate_voice voice_controller

Why keep this around when ``voice_app`` exists? Two reasons:

1. On a headless Pi with no display, it's the lightest way to drive
   the robot with natural language.
2. For the paper evaluation (``evaluate_bt.py``) we want to run a fixed
   corpus through the same pipeline the robot uses at runtime, with no
   browser in the loop.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from .ai_core import AICore
from .bt_engine import BTEngine, NodeStatus
from .ros2_publisher import ROS2Publisher


def _print_tree(tree: dict, indent: int = 0) -> None:
    """Print the behaviour tree so the operator can see the plan."""
    if indent == 0:
        print("\n  ┌─ Plan ──────────────────────────────────")

    prefix = "  │ " + "  " * indent
    t = tree.get("type", "?")

    if t in ("sequence", "selector"):
        symbol = "→" if t == "sequence" else "?"
        label = tree.get("label", t)
        print(f"{prefix}{symbol} {label}")
        for child in tree.get("children", []):
            _print_tree(child, indent + 1)
    elif t == "robot_action":
        name = tree.get("name", "")
        params = tree.get("params", {})
        params_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        print(f"{prefix}[robot] {name}({params_str})")
    elif t == "function_call":
        name = tree.get("name", "")
        store = tree.get("store_as", "")
        print(f"{prefix}[fn]    {name}() -> ${store}")
    elif t == "llm_reason":
        q = tree.get("question", "")[:40]
        print(f"{prefix}[llm]   reason: {q}...")
    elif t == "respond":
        msg = tree.get("message", "")[:50]
        print(f"{prefix}[say]   {msg}...")
    elif t == "condition":
        print(f"{prefix}[cond]  {tree.get('name', '')}")
    elif t == "confirm":
        print(f"{prefix}[??]    {tree.get('message', '')[:40]}")
    elif t == "wait":
        print(f"{prefix}[wait]  {tree.get('seconds', '?')}s")
    else:
        print(f"{prefix}? {t}")

    if indent == 0:
        print("  └─────────────────────────────────────────")


def _print_result(result) -> None:
    print(f"\n  ┌─ Result ────────────────────────────────")
    print(f"  │ Status: {'success' if result.success else 'FAILED'}")
    print(f"  │ Time: {result.total_time_ms:.0f} ms")
    if result.farmbot_commands:
        print(f"  │ Commands: {result.farmbot_commands}")
    for nr in result.node_results:
        if nr.status == NodeStatus.SUCCESS:
            icon = "✓"
        elif nr.status == NodeStatus.FAILURE:
            icon = "✗"
        else:
            icon = "·"
        if nr.message and not nr.message.startswith("Sequence"):
            print(f"  │   {icon} {nr.message}")
    print("  └─────────────────────────────────────────")


def _resolve_config_path(explicit: Optional[str]) -> str:
    """Find the YAML config: explicit arg, then colcon share, then source."""
    if explicit and os.path.exists(explicit):
        return explicit
    try:
        from ament_index_python.packages import get_package_share_directory
        share = get_package_share_directory('growmate_voice')
        candidate = os.path.join(share, 'config', 'farmbot.yaml')
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, '..', 'config', 'farmbot.yaml'))
    if os.path.exists(candidate):
        return candidate
    print("[growmate_voice] ERROR: could not find farmbot.yaml")
    print("[growmate_voice]   Pass --config /path/to/farmbot.yaml")
    sys.exit(1)


def main(argv=None) -> None:
    # ``ros2 run`` strips the first argv element, so we accept argv=None
    # and let argparse pick up sys.argv.
    parser = argparse.ArgumentParser(description="GrowMate voice controller (headless)")
    parser.add_argument("--model", default="gemma3:4b",
                        help="Ollama model name (default: gemma3:4b)")
    parser.add_argument("--config", default=None,
                        help="Path to garden config YAML")
    parser.add_argument("--no-ros2", action="store_true",
                        help="Simulation mode — don't publish to ROS2")
    args, _unknown = parser.parse_known_args(argv)

    config_path = _resolve_config_path(args.config)

    print("=" * 60)
    print("  GrowMate Voice Controller (headless)")
    print("  Drop-in replacement for keyboard_controller")
    print("=" * 60)

    ai = AICore(config_path=config_path, model=args.model)
    if not ai.is_available():
        print(f"  WARNING: Ollama not reachable for model '{args.model}'")
        print(f"  Start Ollama and run: ollama pull {args.model}")

    robot = ROS2Publisher(ros2_enabled=not args.no_ros2)
    bt = BTEngine(garden_config=ai.garden, robot_controller=robot)
    bt.llm_callback = ai.reason

    print("\n  Ready. Type a natural-language command, or 'quit' to exit.")
    print("  Special commands: 'tree' shows the last tree, 'estop' publishes e.")
    print()

    last_tree = None
    pending_commands = None

    try:
        while True:
            try:
                user_input = input("\n  > ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break
            if user_input.lower() == "estop":
                record = robot.emergency_stop()
                print(f"  EMERGENCY STOP: {record.command} ({record.status})")
                continue
            if user_input.lower() == "tree":
                if last_tree is None:
                    print("  (no tree yet)")
                else:
                    print("\n" + json.dumps(last_tree, indent=2))
                continue

            # Confirmation response to a pending action.
            if pending_commands is not None:
                if user_input.lower() in ("yes", "y", "ok", "sure", "go"):
                    print("  Confirmed. Publishing commands...")
                    robot.execute(pending_commands)
                    pending_commands = None
                    continue
                if user_input.lower() in ("no", "n", "cancel"):
                    print("  Cancelled.")
                    pending_commands = None
                    continue
                # Anything else: fall through and treat as a new utterance.
                pending_commands = None

            tree = ai.construct_tree(user_input)
            if tree is None:
                print("  Couldn't build a plan for that. Try rephrasing.")
                continue
            last_tree = tree
            _print_tree(tree)

            result = bt.execute(tree)
            _print_result(result)

            if result.needs_confirmation:
                print(f"\n  ⚠  {result.confirmation_prompt}")
                print("  Type 'yes' to publish, 'no' to cancel.")
                pending_commands = list(result.farmbot_commands)
                continue

            if result.farmbot_commands:
                robot.execute(result.farmbot_commands)

            if result.response_text:
                print(f"\n  GrowMate: {result.response_text}")

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        robot.shutdown()


if __name__ == "__main__":
    main()
