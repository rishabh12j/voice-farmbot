"""ROS2 publisher for GrowMate.

Publishes ``std_msgs/String`` to ``keyboard_topic`` — the same topic the
existing ``keyboard_controller`` writes to. The downstream FarmBot
controller stack does not know or care which publisher the message came
from, which is what makes this a true drop-in replacement.

Two modes:

* **ROS2 mode** (default when ``rclpy`` is available): lazy-imports rclpy,
  initialises it once if not already initialised, creates a single node
  named ``growmate_voice_controller`` and a publisher on ``keyboard_topic``,
  and publishes there. Re-entrant: importing or constructing this class
  twice in the same process is safe.
* **Simulation mode**: prints what *would* be published. Used for desktop
  development without a robot or a sourced ROS2 workspace.

The class also keeps a rolling history of every command sent, which the
Gradio app uses to show the user what reached the wire.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CommandRecord:
    command: str
    status: str          # "sent", "simulated", or "error"
    description: str
    error: str = ""


class ROS2Publisher:
    """Publishes FarmBot command strings to ``keyboard_topic``.

    Args:
        ros2_enabled: If True, attempts to initialise rclpy and publish for
            real. If False (or rclpy is unavailable), runs in simulation mode
            and prints commands instead.
        topic: ROS2 topic name. Defaults to ``keyboard_topic`` to match the
            existing keyboard_controller. Do not change without a reason.
    """

    # Class-level lock so multi-threaded callers (Gradio) can't double-init.
    _init_lock = threading.Lock()
    _rclpy_inited = False

    def __init__(self, ros2_enabled: bool = True, topic: str = "keyboard_topic"):
        self.topic = topic
        self.ros2_enabled = False
        self._node = None
        self._publisher = None
        self._rclpy = None
        self._String = None
        self.command_log: List[CommandRecord] = []

        if ros2_enabled:
            self._init_ros2()
        else:
            print("[growmate_voice] Robot: simulation mode (commands printed, not published)")

    # ------------------------------------------------------------------ init
    def _init_ros2(self) -> None:
        """Lazy-import rclpy and create a single node + publisher."""
        try:
            import rclpy
            from std_msgs.msg import String
        except ImportError:
            print("[growmate_voice] rclpy unavailable — falling back to simulation mode")
            print("[growmate_voice] Source your ROS2 workspace and re-run to publish for real")
            return

        with ROS2Publisher._init_lock:
            if not ROS2Publisher._rclpy_inited:
                if not rclpy.ok():
                    rclpy.init()
                ROS2Publisher._rclpy_inited = True

            self._rclpy = rclpy
            self._String = String
            self._node = rclpy.create_node('growmate_voice_controller')
            self._publisher = self._node.create_publisher(String, self.topic, 10)
            self.ros2_enabled = True
            print(f"[growmate_voice] Connected to ROS2; publishing to '{self.topic}'")

    # --------------------------------------------------------------- execute
    def execute(self, commands: List[str]) -> List[CommandRecord]:
        """Send a list of FarmBot command strings.

        Returns one CommandRecord per command, in order. Always returns —
        errors are recorded in the status field rather than raised, because
        a partial publish failure should not crash the BT engine.
        """
        results: List[CommandRecord] = []
        for cmd in commands:
            record = self._send_one(cmd)
            results.append(record)
            self.command_log.append(record)
        return results

    def emergency_stop(self) -> CommandRecord:
        """Publish the FarmBot e-stop command directly. Bypasses everything."""
        return self._send_one('e')

    def _send_one(self, command: str) -> CommandRecord:
        description = self._describe(command)
        if self.ros2_enabled and self._publisher is not None:
            try:
                msg = self._String()
                msg.data = command
                self._publisher.publish(msg)
                return CommandRecord(command=command, status="sent", description=description)
            except Exception as exc:
                return CommandRecord(
                    command=command, status="error", description=description, error=str(exc)
                )
        else:
            print(f"  -> [SIM] FarmBot: {command}  ({description})")
            return CommandRecord(command=command, status="simulated", description=description)

    # ------------------------------------------------------------ describe
    @staticmethod
    def _describe(command: str) -> str:
        """Return a human-readable description for a FarmBot command string."""
        parts = command.split()
        code = parts[0] if parts else ""
        single = {
            "H_0": "Go to home position",
            "H_1": "Find all home positions",
            "e": "EMERGENCY STOP",
            "E": "Reset emergency stop",
            "P_3": "Seed all plants in planning",
            "P_4": "Water all plants",
            "P_5": "Water plants based on moisture",
            "P_9": "Check moisture for all plants",
            "D_W_1": "Water pump ON",
            "D_W_0": "Water pump OFF",
            "D_L_1": "LED strip ON",
            "D_L_0": "LED strip OFF",
            "D_V_1": "Vacuum pump ON",
            "D_V_0": "Vacuum pump OFF",
            "D_S_C": "Read soil sensor",
            "D_C": "Check tool mount",
            "I_1": "Take photo",
            "I_2": "Create bed panorama",
            "I_4": "Detect weeds",
        }
        if code in single:
            return single[code]
        if code == "M" and len(parts) >= 4:
            return f"Move to x={parts[1]}, y={parts[2]}, z={parts[3]}"
        if code == "M_S" and len(parts) >= 5:
            return f"Move to ({parts[1]},{parts[2]},{parts[3]}) at {parts[4]}% speed"
        return f"Command: {command}"

    # --------------------------------------------------------------- cleanup
    def shutdown(self) -> None:
        """Destroy the ROS2 node. Safe to call multiple times."""
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
            self._publisher = None
