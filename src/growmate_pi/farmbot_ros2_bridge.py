"""ROS2 bridge for the Pi-side intent server.

Single connection point to ROS2. py_trees action nodes call ``publish()`` here
to send a FarmBot command string to ``keyboard_topic``. ``emergency_stop()``
publishes ``e`` directly, bypassing any tree.

Modes:

* **Real**: rclpy is importable, a node and publisher are created. Used on the
  Pi or in WSL with ROS2 sourced.
* **Simulation**: rclpy is missing (or ``ros2_enabled=False``). The bridge
  prints what *would* be published. Used for desktop dev on Windows.

The class is re-entrant — constructing it twice in the same process reuses
``rclpy.init()`` via a class-level lock. The intent server should construct
one instance and pass it to the BT builder.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CommandRecord:
    command: str
    status: str  # "sent", "simulated", or "error"
    description: str
    error: str = ""


class FarmBotROS2Bridge:
    """Publishes FarmBot command strings to ``keyboard_topic``.

    Args:
        ros2_enabled: If False, runs in sim mode regardless of rclpy
            availability. If True, attempts to init rclpy; falls back to sim
            mode if rclpy is missing.
        topic: ROS2 topic name. Defaults to ``keyboard_topic`` — the same
            topic the upstream ``keyboard_controller`` publishes to. Do not
            change without a research-claim discussion.
    """

    _init_lock = threading.Lock()
    _rclpy_inited = False

    def __init__(self, ros2_enabled: bool = True, topic: str = "keyboard_topic"):
        self.topic = topic
        self.ros2_enabled = False
        self._node = None
        self._publisher = None
        self._String = None
        self.command_log: List[CommandRecord] = []

        if ros2_enabled:
            self._init_ros2()
        else:
            print("[growmate_pi] Bridge: simulation mode (commands printed)")

    def _init_ros2(self) -> None:
        try:
            import rclpy
            from std_msgs.msg import String
        except ImportError:
            print("[growmate_pi] rclpy unavailable — running in simulation mode")
            return

        with FarmBotROS2Bridge._init_lock:
            if not FarmBotROS2Bridge._rclpy_inited:
                if not rclpy.ok():
                    rclpy.init()
                FarmBotROS2Bridge._rclpy_inited = True

            self._String = String
            self._node = rclpy.create_node("growmate_pi_bridge")
            self._publisher = self._node.create_publisher(String, self.topic, 10)
            self.ros2_enabled = True
            print(f"[growmate_pi] Bridge: connected, publishing to '{self.topic}'")

    def is_ready(self) -> bool:
        """True when either real ROS2 publisher is up, or sim mode is active.

        Used by the ``CheckAvailable`` condition node — sim mode is always
        ready; real mode requires the publisher to be created.
        """
        if not self.ros2_enabled:
            return True  # sim mode is always "available"
        return self._publisher is not None

    def publish(self, command: str) -> CommandRecord:
        """Send one FarmBot command string. Always returns a record; errors
        are recorded in the ``status`` field rather than raised.
        """
        description = self._describe(command)

        if self.ros2_enabled and self._publisher is not None:
            try:
                msg = self._String()
                msg.data = command
                self._publisher.publish(msg)
                record = CommandRecord(command, "sent", description)
            except Exception as exc:
                record = CommandRecord(command, "error", description, str(exc))
        else:
            print(f"  -> [SIM] FarmBot: {command}  ({description})")
            record = CommandRecord(command, "simulated", description)

        self.command_log.append(record)
        return record

    def emergency_stop(self) -> CommandRecord:
        """Publish the e-stop command directly. Bypasses the BT entirely.

        Called by the ``/estop`` HTTP endpoint and by the
        ``EmergencyStop`` py_trees action node.
        """
        return self.publish("e")

    def reset_emergency_stop(self) -> CommandRecord:
        """Publish the reset command after an e-stop."""
        return self.publish("E")

    @staticmethod
    def _describe(command: str) -> str:
        """Human-readable description for logging."""
        parts = command.split()
        code = parts[0] if parts else ""
        single = {
            "H_0": "Go to home position",
            "H_1": "Find all home positions",
            "e": "EMERGENCY STOP",
            "E": "Reset emergency stop",
            "P_3": "Seed all plants",
            "P_4": "Water all plants",
            "P_5": "Water plants by moisture",
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
            "I_2": "Create panorama",
            "I_4": "Detect weeds",
        }
        if code in single:
            return single[code]
        if code == "M" and len(parts) >= 4:
            return f"Move to ({parts[1]}, {parts[2]}, {parts[3]})"
        if code == "M_S" and len(parts) >= 5:
            return f"Move to ({parts[1]},{parts[2]},{parts[3]}) at {parts[4]}%"
        return f"Command: {command}"

    def shutdown(self) -> None:
        """Destroy the ROS2 node. Safe to call multiple times."""
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
            self._publisher = None
