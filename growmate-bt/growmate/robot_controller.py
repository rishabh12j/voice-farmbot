"""
GrowMate Robot Controller -- FarmBot ROS2 Interface

Handles publishing command strings to the FarmBot ROS2 stack.

In production: publishes to 'keyboard_topic' via rclpy
In development: prints commands that WOULD be published (simulation mode)

The controller receives exact FarmBot command strings from the AI Core
(e.g., "M 400 200 0", "P_4", "D_W_1") and sends them to the robot.
"""


class RobotController:
    """
    Interface to the FarmBot ROS2 stack.
    
    In simulation mode (default): prints commands
    In ROS2 mode: publishes to keyboard_topic
    """

    def __init__(self, ros2_enabled: bool = False):
        self.ros2_enabled = ros2_enabled
        self.ros2_publisher = None
        self.command_log = []  # History of all sent commands

        if ros2_enabled:
            self._init_ros2()
        else:
            print("  [ROBOT] Robot: Simulation mode (commands will be printed, not executed)")
            print("  [HINT] Enable ROS2: pass ros2_enabled=True (requires rclpy)")

    def _init_ros2(self):
        """Initialize ROS2 node and publisher."""
        try:
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import String

            if not rclpy.ok():
                rclpy.init()

            self._node = rclpy.create_node('growmate_voice_controller')
            self.ros2_publisher = self._node.create_publisher(String, 'keyboard_topic', 10)
            self.ros2_enabled = True
            print("  [ROBOT] Robot: Connected to FarmBot ROS2 (publishing to keyboard_topic)")
        except ImportError:
            print("  [WARN]  rclpy not available -- falling back to simulation mode")
            print("  [HINT] Install ROS2 and source the workspace to enable")
            self.ros2_enabled = False

    def execute(self, commands: list) -> list:
        """
        Execute a list of FarmBot command strings.
        
        Args:
            commands: List of command strings like ["M 400 200 0", "D_W_1"]
            
        Returns:
            List of execution results
        """
        results = []
        for cmd in commands:
            result = self._send_command(cmd)
            results.append(result)
            self.command_log.append({"command": cmd, "result": result})
        return results

    def _send_command(self, command: str) -> dict:
        """Send a single command to FarmBot."""
        if self.ros2_enabled and self.ros2_publisher:
            return self._send_ros2(command)
        else:
            return self._simulate(command)

    def _send_ros2(self, command: str) -> dict:
        """Publish command to ROS2 topic."""
        try:
            from std_msgs.msg import String
            msg = String()
            msg.data = command
            self.ros2_publisher.publish(msg)
            return {"status": "sent", "command": command}
        except Exception as e:
            return {"status": "error", "command": command, "error": str(e)}

    def _simulate(self, command: str) -> dict:
        """Simulate command execution (print what would happen)."""
        description = self._describe_command(command)
        print(f"    -> FarmBot: {command}  ({description})")
        return {"status": "simulated", "command": command, "description": description}

    def _describe_command(self, command: str) -> str:
        """Human-readable description of a FarmBot command."""
        parts = command.split()
        code = parts[0] if parts else ""

        descriptions = {
            "M": f"Move to x={parts[1]}, y={parts[2]}, z={parts[3]}" if len(parts) >= 4 else "Move",
            "M_S": f"Move to ({parts[1]},{parts[2]},{parts[3]}) at {parts[4]}% speed" if len(parts) >= 5 else "Move with speed",
            "H_0": "Go to home position",
            "H_1": "Find all home positions",
            "e": "EMERGENCY STOP",
            "E": "Reset emergency stop",
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

        return descriptions.get(code, f"Command: {command}")

    def get_history(self) -> list:
        """Get command execution history."""
        return self.command_log
