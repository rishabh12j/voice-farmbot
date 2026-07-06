"""Verify-gate semantics tests (audit F3) — no server, no robot.

Exercises _VerifiedCommand / MoveTo directly against a sim bridge:

  1. A firmware R03 (finished-with-error) during a verified command's busy
     cycle must yield FAILURE, not "confirmed" — on the real stack the UART
     controller lowers busy on R03 exactly like R02, so the completion edge
     alone is not success.
  2. A verified MoveTo with position-check must FAIL if the live position
     does not reach the target within tolerance (grace window for R82
     latency), and must SUCCEED when it does.
  3. check_mm=None preserves the legacy completion-only behaviour.

Run: PYTHONPATH=src python tools/test_verify_semantics.py   # expect Failures: 0
"""

from __future__ import annotations

import sys
import time

import py_trees

from growmate_pi.bt import action_nodes
from growmate_pi.bt.action_nodes import EnsureTool, MoveTo, PublishCmd
from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.tool_state import get_tool_state, reset_tool_state_for_tests


TICK_TIMEOUT_S = 6.0


def _run_to_completion(node) -> py_trees.common.Status:
    """Tick a lone behaviour until it leaves RUNNING (bounded)."""
    deadline = time.monotonic() + TICK_TIMEOUT_S
    node.tick_once()
    while node.status == py_trees.common.Status.RUNNING:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{node.name} stuck RUNNING: {node.feedback_message}")
        time.sleep(0.05)
        node.tick_once()
    return node.status


def _inject_r03(bridge: FarmBotROS2Bridge) -> None:
    """Simulate the firmware reporting finished-with-error mid-cycle."""
    with bridge._busy_lock:
        bridge._error_count += 1


def main() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures += 1

    # --- 1. R03 during a verified command -> FAILURE -------------------------
    print("\n=== R03 error is not a confirmation ===")
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    node = MoveTo(bridge, x=100.0, y=200.0, z=0.0, verify=True, timeout_s=5.0)
    node.tick_once()                      # initialise: publishes, arms busy
    _inject_r03(bridge)                   # firmware errors before completion
    status = _run_to_completion(node)
    check("MoveTo fails on R03", status == py_trees.common.Status.FAILURE,
          node.feedback_message)

    node = PublishCmd("D_W_1", bridge, verify=True, timeout_s=5.0)
    node.tick_once()
    _inject_r03(bridge)
    status = _run_to_completion(node)
    check("PublishCmd fails on R03", status == py_trees.common.Status.FAILURE,
          node.feedback_message)

    # --- 2. Position verify --------------------------------------------------
    print("\n=== Position verify (check_mm) ===")
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    node = MoveTo(bridge, x=100.0, y=200.0, z=0.0, verify=True, timeout_s=5.0,
                  check_mm=5.0)
    status = _run_to_completion(node)     # sim snaps position to target
    check("MoveTo succeeds when position matches",
          status == py_trees.common.Status.SUCCESS, node.feedback_message)

    # Force a mismatch: the "gantry" reports a stalled position. Shrink the
    # grace so the test stays fast (the constant is read per tick).
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    real_position = bridge.position
    bridge.position = lambda: {"x": 42.0, "y": 200.0, "z": 0.0}  # x stalled
    old_grace = action_nodes.POSITION_GRACE_S
    action_nodes.POSITION_GRACE_S = 0.4
    try:
        node = MoveTo(bridge, x=100.0, y=200.0, z=0.0, verify=True,
                      timeout_s=5.0, check_mm=5.0)
        status = _run_to_completion(node)
        check("MoveTo fails when gantry is not at target",
              status == py_trees.common.Status.FAILURE, node.feedback_message)
        check("failure names the position",
              "position unverified" in node.feedback_message)
    finally:
        action_nodes.POSITION_GRACE_S = old_grace
        bridge.position = real_position

    # --- 2b. Pin-less tool changes (gh1: pin 63 hardware-stuck at 0) ---------
    # Confirmation must come from choreography evidence (>=1 busy cycle +
    # quiescence), never from the meaningless pin — and a silently-dropped
    # command must FAIL, not pass on 'quiet'.
    print("\n=== Tool changes without a usable pin 63 ===")
    tools = {"watering_nozzle": 2, "soil_sensor": 1, "phantom": 9}

    def _fast_quiesce(node, bridge):
        node._quiesce = action_nodes._BusyQuiescence(bridge, window_s=0.8)

    reset_tool_state_for_tests()
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    node = EnsureTool(bridge, "watering_nozzle", tools, timeout_s=20.0,
                      pin_usable=False)
    _fast_quiesce(node, bridge)
    old_deadline = globals()["TICK_TIMEOUT_S"]
    globals()["TICK_TIMEOUT_S"] = 15.0
    try:
        status = _run_to_completion(node)
        check("pin-less fresh mount succeeds after choreography",
              status == py_trees.common.Status.SUCCESS, node.feedback_message)
        check("success wording admits the pin is unavailable",
              "unavailable" in node.feedback_message)
        check("no D_C polls in pin-less mode",
              not any(r.command == "D_C" for r in bridge.command_log))
        check("ToolState updated",
              get_tool_state().current() == "watering_nozzle")

        # Swap nozzle -> probe: unmount + mount choreographies in order.
        node = EnsureTool(bridge, "soil_sensor", tools, timeout_s=30.0,
                          pin_usable=False)
        _fast_quiesce(node, bridge)
        globals()["TICK_TIMEOUT_S"] = 25.0
        status = _run_to_completion(node)
        t_cmds = [r.command for r in bridge.command_log if r.command.startswith("T_")]
        check("pin-less swap succeeds",
              status == py_trees.common.Status.SUCCESS, node.feedback_message)
        check("swap published unmount then mount",
              t_cmds[-2:] == ["T_2_2", "T_1_1"], str(t_cmds))
        check("ToolState is the new head",
              get_tool_state().current() == "soil_sensor")

        # A command the controller grammar would DROP (index 9): no busy cycle
        # ever arrives, so quiet alone must not be read as success.
        reset_tool_state_for_tests()
        bridge = FarmBotROS2Bridge(ros2_enabled=False)
        node = EnsureTool(bridge, "phantom", tools, timeout_s=3.0,
                          pin_usable=False)
        _fast_quiesce(node, bridge)
        globals()["TICK_TIMEOUT_S"] = 8.0
        status = _run_to_completion(node)
        check("pin-less dropped command times out honestly",
              status == py_trees.common.Status.FAILURE, node.feedback_message)
    finally:
        globals()["TICK_TIMEOUT_S"] = old_deadline
        reset_tool_state_for_tests()

    # --- 3. check_mm=None keeps legacy completion-only behaviour -------------
    print("\n=== Legacy behaviour without check_mm ===")
    bridge = FarmBotROS2Bridge(ros2_enabled=False)
    bridge.position = lambda: {"x": 0.0, "y": 0.0, "z": 0.0}  # wrong, ignored
    node = MoveTo(bridge, x=100.0, y=200.0, z=0.0, verify=True, timeout_s=5.0)
    status = _run_to_completion(node)
    check("completion-only MoveTo unaffected",
          status == py_trees.common.Status.SUCCESS, node.feedback_message)

    print(f"\n{'-' * 60}")
    print(f"Failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
