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
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Sim-mode fake busy-cycle duration. Short enough to keep verify_sim fast, long
# enough that the 10 Hz executor sees at least one RUNNING tick before it
# resolves — so the verify path is genuinely exercised in dev, not skipped.
_SIM_BUSY_S = 0.2

# Soil moisture sensor is Farmduino analog pin 59 (D_S_C -> F42 P59 M1 ->
# R41 P59 V<value>). Sim fakes a reading in this raw-ADC band on D_S_C.
_SOIL_PIN = 59
_SIM_SOIL_RANGE = (250, 750)


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

    def __init__(
        self,
        ros2_enabled: bool = True,
        topic: str = "keyboard_topic",
        verify_enabled: bool = True,
        busy_topic: str = "busy_state",
        uart_topic: str = "uart_receive",
    ):
        self.topic = topic
        self.busy_topic = busy_topic
        self.uart_topic = uart_topic
        self.verify_enabled = verify_enabled
        self.ros2_enabled = False
        self._node = None
        self._publisher = None
        self._busy_sub = None
        self._uart_sub = None
        self._String = None
        self._Bool = None
        self.command_log: List[CommandRecord] = []

        # Live gantry position for the UI map. Real mode parses the firmware's
        # R82 position reports off /uart_receive (true position as it moves);
        # sim mode derives it from the M commands we publish (target snap).
        # None until the first report. Guarded by ``_busy_lock``.
        self._pos_x: Optional[float] = None
        self._pos_y: Optional[float] = None
        self._pos_z: Optional[float] = None

        # Last analog pin readings from R41 reports: pin -> (value, monotonic
        # ts). The soil sensor (pin 59) lands here; ReadSensor waits for a
        # fresh entry. Guarded by ``_busy_lock``.
        self._pin_readings: Dict[int, Tuple[float, float]] = {}

        # Busy-state tracking for the tick-and-verify gate. ``_busy`` mirrors
        # the firmware's /busy_state; ``_completion_count`` ticks up on every
        # True->False edge (one finished command). Verified action nodes poll
        # ``completion_count()`` to know their command actually ran rather than
        # was merely published. Guarded by a lock because the spin thread and
        # the BT executor thread both touch it.
        self._busy_lock = threading.Lock()
        self._busy = False
        self._completion_count = 0
        self._sim_busy_until: Optional[float] = None  # sim-mode fake cycle
        self._sim_tool_mounted = False  # sim-mode UTM state (pin 63)

        # Background spin thread (real mode only) so subscription callbacks
        # fire — the node was previously publish-only and never spun.
        self._spin_thread: Optional[threading.Thread] = None
        self._spinning = False

        if ros2_enabled:
            self._init_ros2()
        else:
            print("[growmate_pi] Bridge: simulation mode (commands printed)")

    def _init_ros2(self) -> None:
        try:
            import rclpy
            from std_msgs.msg import Bool, String
        except ImportError:
            print("[growmate_pi] rclpy unavailable — running in simulation mode")
            return

        with FarmBotROS2Bridge._init_lock:
            if not FarmBotROS2Bridge._rclpy_inited:
                if not rclpy.ok():
                    rclpy.init()
                FarmBotROS2Bridge._rclpy_inited = True

            self._String = String
            self._Bool = Bool
            self._node = rclpy.create_node("growmate_pi_bridge")
            self._publisher = self._node.create_publisher(String, self.topic, 10)
            # Tick-and-verify: listen to the firmware busy flag so verified
            # action nodes can wait for real completion, not just publish.
            self._busy_sub = self._node.create_subscription(
                Bool, self.busy_topic, self._on_busy, 10
            )
            # Live position: parse R82 reports off the UART feed for the map.
            self._uart_sub = self._node.create_subscription(
                String, self.uart_topic, self._on_uart, 10
            )
            self.ros2_enabled = True
            self._start_spin(rclpy)
            print(
                f"[growmate_pi] Bridge: connected, publishing to '{self.topic}', "
                f"verifying via '{self.busy_topic}' "
                f"(verify={'on' if self.verify_enabled else 'off'})"
            )

    # ---------- Busy-state plumbing (tick-and-verify gate) --------------------

    def _start_spin(self, rclpy) -> None:
        """Spin the node in a daemon thread so subscription callbacks fire.

        One thread per bridge instance; ``shutdown()`` stops it. The loop guard
        also catches a torn-down node mid-spin so shutdown doesn't crash here.
        """
        if self._spinning:
            return
        self._spinning = True

        def _loop():
            while self._spinning and rclpy.ok() and self._node is not None:
                try:
                    rclpy.spin_once(self._node, timeout_sec=0.1)
                except Exception:
                    break

        self._spin_thread = threading.Thread(
            target=_loop, name="growmate_pi_bridge_spin", daemon=True
        )
        self._spin_thread.start()

    def _on_busy(self, msg) -> None:
        """/busy_state callback. Count each True->False edge as one completion."""
        new = bool(msg.data)
        with self._busy_lock:
            was = self._busy
            self._busy = new
            if was and not new:
                self._completion_count += 1

    def _refresh_sim(self) -> None:
        """Advance the simulated busy cycle (sim mode only).

        ``publish(track=True)`` arms ``_sim_busy_until``; once the clock passes
        it we drop busy and bump the completion count, mimicking the firmware's
        True->False edge so verified nodes resolve in dev too.
        """
        if self._sim_busy_until is None:
            return
        if time.monotonic() >= self._sim_busy_until:
            with self._busy_lock:
                if self._busy:
                    self._busy = False
                    self._completion_count += 1
            self._sim_busy_until = None

    def verify_active(self) -> bool:
        """True when verified nodes should poll for completion rather than
        fast-succeed: verify_enabled AND a working channel (real publisher up,
        or sim mode which fakes the cycle)."""
        if not self.verify_enabled:
            return False
        if self.ros2_enabled:
            return self._publisher is not None
        return True  # sim mode simulates a cycle

    def is_busy(self) -> bool:
        if not self.ros2_enabled:
            self._refresh_sim()
        with self._busy_lock:
            return self._busy

    def completion_count(self) -> int:
        if not self.ros2_enabled:
            self._refresh_sim()
        with self._busy_lock:
            return self._completion_count

    # ---------- Live position (UI map) ---------------------------------------

    def _on_uart(self, msg) -> None:
        """Parse position (R82) and analog-pin (R41) reports off /uart_receive.

        ``R82 X1234.5 Y678.9 Z-12.3`` → live gantry position (any axis may be
        absent; we keep the last value per axis), mirrors the upstream parser.
        ``R41 P59 V512`` → an analog pin read (the soil sensor is pin 59);
        stored with a timestamp so ReadSensor can wait for a *fresh* one.
        """
        try:
            data = getattr(msg, "data", None) or str(msg)
            parts = data.split()
            if not parts:
                return
            if parts[0] == "R82":
                x = y = z = None
                for tok in parts[1:]:
                    if len(tok) < 2:
                        continue
                    axis = tok[0].upper()
                    try:
                        val = float(tok[1:])
                    except ValueError:
                        continue
                    if axis == "X":
                        x = val
                    elif axis == "Y":
                        y = val
                    elif axis == "Z":
                        z = val
                with self._busy_lock:
                    if x is not None:
                        self._pos_x = x
                    if y is not None:
                        self._pos_y = y
                    if z is not None:
                        self._pos_z = z
            elif parts[0] == "R41":
                pin = value = None
                for tok in parts[1:]:
                    if len(tok) < 2:
                        continue
                    try:
                        num = float(tok[1:])
                    except ValueError:
                        continue
                    if tok[0].upper() == "P":
                        pin = int(num)
                    elif tok[0].upper() == "V":
                        value = num
                if pin is not None and value is not None:
                    with self._busy_lock:
                        self._pin_readings[pin] = (value, time.monotonic())
        except Exception:
            pass

    def _sim_update_position(self, command: str) -> None:
        """Sim mode: derive position from the command we just published so the
        UI map still follows in dev. ``M x y z`` snaps to the target; ``H_0``
        returns to the origin."""
        parts = command.split()
        if not parts:
            return
        if parts[0] == "M" and len(parts) >= 4:
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                return
            with self._busy_lock:
                self._pos_x, self._pos_y, self._pos_z = x, y, z
        elif parts[0] == "H_0":
            with self._busy_lock:
                self._pos_x = self._pos_y = self._pos_z = 0.0

    def _sim_maybe_fake_reading(self, command: str) -> None:
        """Sim: there's no firmware to answer pin reads, so fake them.

        - ``D_S_C`` → a plausible soil reading on pin 59 (ReadSensor).
        - ``T<n>_1`` / ``T<n>_2`` → flip the UTM mount state.
        - ``D_C`` → report pin 63 from that state (0 = mounted, 1 = not),
          which MountTool/UnmountTool wait on.
        """
        c = command.strip()
        if c == "D_S_C":
            import random
            value = float(random.randint(*_SIM_SOIL_RANGE))
            with self._busy_lock:
                self._pin_readings[_SOIL_PIN] = (value, time.monotonic())
        elif c.startswith("T") and c[1:-2].isdigit() and c[-2:] in ("_1", "_2"):
            self._sim_tool_mounted = c.endswith("_1")
            with self._busy_lock:
                self._pin_readings[63] = (
                    0.0 if self._sim_tool_mounted else 1.0, time.monotonic()
                )
        elif c == "D_C":
            with self._busy_lock:
                self._pin_readings[63] = (
                    0.0 if self._sim_tool_mounted else 1.0, time.monotonic()
                )

    def position(self) -> Optional[dict]:
        """Last known gantry position, or None if never reported."""
        with self._busy_lock:
            if self._pos_x is None and self._pos_y is None and self._pos_z is None:
                return None
            return {"x": self._pos_x, "y": self._pos_y, "z": self._pos_z}

    def last_reading(
        self, pin: int, newer_than: Optional[float] = None
    ) -> Optional[Tuple[float, float]]:
        """Most recent (value, monotonic_ts) for an analog pin, or None.

        ``newer_than``: if given, only return a reading strictly newer than it,
        so a caller can wait for a *fresh* read instead of a stale one.
        """
        with self._busy_lock:
            entry = self._pin_readings.get(pin)
        if entry is None:
            return None
        if newer_than is not None and entry[1] <= newer_than:
            return None
        return entry

    def is_ready(self) -> bool:
        """True when either real ROS2 publisher is up, or sim mode is active.

        Used by the ``CheckAvailable`` condition node — sim mode is always
        ready; real mode requires the publisher to be created.
        """
        if not self.ros2_enabled:
            return True  # sim mode is always "available"
        return self._publisher is not None

    def publish(self, command: str, track: bool = False) -> CommandRecord:
        """Send one FarmBot command string. Always returns a record; errors
        are recorded in the ``status`` field rather than raised.

        Args:
            command: the FarmBot command string.
            track: when True and running in sim mode, arm a short fake
                busy-state cycle so a verified action node observes a
                completion. Ignored in real mode — there the firmware drives
                /busy_state and the bridge just listens.
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
            # No firmware in sim, so reflect the move target as the position
            # for the UI map, and fake a soil reading for D_S_C.
            self._sim_update_position(command)
            self._sim_maybe_fake_reading(command)
            if track:
                # Fake one busy cycle so a verified node sees a completion.
                with self._busy_lock:
                    self._busy = True
                self._sim_busy_until = time.monotonic() + _SIM_BUSY_S

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
        """Stop the spin thread and destroy the ROS2 node. Safe to call
        multiple times."""
        self._spinning = False
        if self._spin_thread is not None:
            self._spin_thread.join(timeout=1.0)
            self._spin_thread = None
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
            self._publisher = None
            self._busy_sub = None
            self._uart_sub = None
