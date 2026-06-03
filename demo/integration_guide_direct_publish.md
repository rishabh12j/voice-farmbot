# Publishing FarmBot commands directly to `/keyboard_topic`

How to drive the AURA FarmBot stack by publishing raw command strings —
no extra middleware, no behaviour tree, just `rclpy` + `std_msgs/String`.

The AURA controller subscribes to `/keyboard_topic`. Whatever you publish there
gets parsed and turned into Farmduino G-code. The official
`keyboard_controller` node uses this. Your own code can too.

---

## 0. Prerequisites

You should already have:

- ROS 2 (any distro that matches your AURA install — Humble or Jazzy) sourced
- The AURA FarmBot ROS 2 stack built and bringup running
  (`ros2 launch farmbot_bringup standard.launch.py`)
- The Farmduino reachable over UART (you'll see `R99 ARDUINO STARTUP COMPLETE`
  on bringup)
- For Python: any environment where `import rclpy; from std_msgs.msg import String`
  works

Smoke test before writing code — open two terminals, sourced.

Terminal A:

```bash
ros2 topic echo /keyboard_topic
```

Terminal B:

```bash
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'H_0'}"
```

Terminal A should show `data: H_0` and the gantry should go to home. If that
works, the rest of this guide is just structuring real applications around it.

---

## 1. The command vocabulary

These are the strings AURA's `farmbot_controller` understands. Anything
you publish to `/keyboard_topic` ends up in the `case` statement at the
top of `farmbot_controller.py`.

### Movement

| String | Meaning |
|---|---|
| `M x y z` | Move gantry to absolute position (mm). e.g. `M 400 200 -100` |
| `M_S x y z s` | Move at speed `s` (%). e.g. `M_S 400 200 -100 50` |
| `H_0` | Go to home position (0, 0, 0) |
| `H_1` | Find all home positions |
| `H_2 axis` | Home one axis. e.g. `H_2 X` |

### Emergency

| String | Meaning |
|---|---|
| `e` | Emergency stop — halts everything immediately |
| `E` | Reset emergency stop (after `e` is cleared) |

### Plant-aware sequences (require an `active_map.yaml`)

| String | Meaning |
|---|---|
| `P_1 x y z excl canopy water max_z name stage` | Add a plant at (x,y,z) |
| `P_2 index` | Remove plant by index |
| `P_3` | Seed all plants in `Planning` growth stage |
| `P_4` | Water all plants (rigid, uses each plant's `water_quantity`) |
| `P_5` | Water all plants (moisture-based, uses `watering_guide.yaml`) |
| `P_9` | Read moisture for all plants |

### Direct device toggles

| String | Meaning |
|---|---|
| `D_W_1` / `D_W_0` | Water pump ON / OFF |
| `D_L_1` / `D_L_0` | LED strip ON / OFF |
| `D_V_1` / `D_V_0` | Vacuum pump ON / OFF |
| `D_S_C` | Read soil sensor |
| `D_C` | Check tool mount |

### Vision

| String | Meaning |
|---|---|
| `I_1` | Take photo at current position |
| `I_2` | Create panorama |
| `I_3` | Mosaic panorama |
| `I_4` | Detect weeds |

### Parameter / map / config

| String | Meaning |
|---|---|
| `C_0` | Calibrate all axes (drive to endstops, write firmware params) |
| `C_0 X` / `C_0 Y` / `C_0 Z` | Calibrate one axis |
| `C_1 ver` | Load parameter config from YAML. e.g. `C_1 Genesis` |
| `C_2 axis` | Invert encoder direction for one axis. e.g. `C_2 X` |
| `CONF` | Save current firmware params to `activeConfig.yaml` + publish map dims |
| `CONF S` | Save only |
| `CONF M` | Publish map dims only |

The full list of valid keys is in
[`src/farmbot_controllers/farmbot_controllers/keyboard_teleop.py`](https://github.com/rishabh12j/voice-farmbot/blob/main/src/farmbot_controllers/farmbot_controllers/keyboard_teleop.py)
in the `valid_keys` and `compound_cmds` tuples.

---

## 2. Minimal Python publisher

This is the smallest amount of code that publishes to `/keyboard_topic`.
Drop it into a regular Python file (no ROS package scaffolding needed).

```python
#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class FarmBotCommander(Node):
    def __init__(self):
        super().__init__('farmbot_commander')
        self.pub = self.create_publisher(String, 'keyboard_topic', 10)
        # Give DDS a moment to discover subscribers before the first publish
        self._ready = self.create_timer(0.5, self._noop, autostart=True)

    def _noop(self):
        pass

    def send(self, command: str):
        msg = String()
        msg.data = command
        self.pub.publish(msg)
        self.get_logger().info(f"published: {command}")


def main():
    rclpy.init()
    node = FarmBotCommander()
    # Spin briefly so the publisher attaches to subscribers
    rclpy.spin_once(node, timeout_sec=1.0)

    # Your actual commands:
    node.send("H_0")             # go home
    node.send("M 400 200 -100")  # move over the tomatoes
    node.send("D_W_1")           # pump ON

    rclpy.spin_once(node, timeout_sec=2.0)   # let it run
    node.send("D_W_0")           # pump OFF

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
```

Run it with the AURA bringup already up:

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
source ~/farmbot_ws/install/setup.bash
python3 farmbot_commander.py
```

Watch `ros2 topic echo /keyboard_topic` in another terminal to see each line
hit the wire.

---

## 3. A reusable wrapper class

Once you're past the smoke-test, structure it like this. Same code, but
with built-in safety patterns. This is what most teams settle on.

```python
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class FarmBotDirect:
    """Drive the AURA FarmBot by raw command publication.

    Safety contract — read this before extending:
      - Movement commands must always be followed by enough wait time for
        the Farmduino to actually finish moving. The controller doesn't
        block — it ACKs the command and starts moving.
      - The water pump must always be turned off after being turned on.
        Always wrap the pulse in a try/finally.
      - Emergency stop is `e` (lowercase). Reset is `E` (uppercase).
        These bypass the queue.
    """

    def __init__(self, node: Node, topic: str = "keyboard_topic"):
        self._node = node
        self._pub = node.create_publisher(String, topic, 10)
        # Discovery handshake
        rclpy.spin_once(node, timeout_sec=0.5)

    # --- low level ---------------------------------------------------------

    def publish(self, cmd: str):
        msg = String()
        msg.data = cmd
        self._pub.publish(msg)
        self._node.get_logger().info(f"-> {cmd}")
        # Tiny yield so successive publishes aren't fused
        rclpy.spin_once(self._node, timeout_sec=0.02)

    # --- high level --------------------------------------------------------

    def move_to(self, x: float, y: float, z: float, speed: int | None = None):
        if speed is None:
            self.publish(f"M {int(x)} {int(y)} {int(z)}")
        else:
            self.publish(f"M_S {int(x)} {int(y)} {int(z)} {int(speed)}")

    def home(self):
        self.publish("H_0")

    def water_for(self, seconds: float):
        try:
            self.publish("D_W_1")
            time.sleep(seconds)
        finally:
            self.publish("D_W_0")

    def lights(self, on: bool):
        self.publish("D_L_1" if on else "D_L_0")

    def photo(self):
        self.publish("I_1")

    def read_moisture(self):
        self.publish("D_S_C")

    def estop(self):
        self.publish("e")

    def reset_estop(self):
        self.publish("E")
```

Usage:

```python
def main():
    rclpy.init()
    node = Node("my_farm_app")
    fb = FarmBotDirect(node)

    fb.home()
    fb.move_to(400, 200, -100)
    fb.water_for(3.0)
    fb.move_to(800, 200, -100)
    fb.water_for(3.0)
    fb.home()

    node.destroy_node()
    rclpy.shutdown()
```

---

## 4. Knowing when a move has finished

The bare `keyboard_topic` API is **fire-and-forget**. The controller acks the
command and starts moving, but doesn't tell you when it's done. Three options:

### A. Subscribe to `/uart_receive`

The Farmduino sends `R02` (idle), `R08` (busy), and `R99 ... COMPLETE` messages
back over UART, which the AURA stack republishes to `/uart_receive`. Watch for
the right report code.

```python
from std_msgs.msg import String

class CompletionWatcher(Node):
    def __init__(self):
        super().__init__("completion_watcher")
        self.last_ack = ""
        self.sub = self.create_subscription(
            String, "uart_receive", self._on_msg, 10)

    def _on_msg(self, msg):
        if msg.data.startswith("R02") or msg.data.startswith("R99"):
            self.last_ack = msg.data
```

Spin until you see the ack you want.

### B. Subscribe to the position topic

The map_controller publishes the current position. Compare against your target
with a tolerance.

```python
# (pseudocode — check the actual topic name in your AURA build)
self.create_subscription(YourPositionMsg, "current_position", self._pos_cb, 10)

def _pos_cb(self, msg):
    if abs(msg.x - target_x) < 5 and abs(msg.y - target_y) < 5:
        self._move_done.set()
```

### C. Just sleep

Crude but works for known paths. ~10 mm/s is a typical jog speed; for a 1000 mm
move budget 100 s plus a 5 s margin. Brittle, but fine for early prototypes.

```python
fb.move_to(1000, 0, 0)
time.sleep(105)
```

---

## 5. Calibrating before your first run

The first time you bring up a fresh FarmBot, the firmware doesn't know its
own axis lengths. Movement will be wrong, and the workspace bounds the map
publishes will be zero. Fix this once per Farmduino:

```bash
# Terminal 1: bringup running

# Terminal 2: keyboard_controller, or just publish manually:
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'C_0'}"
# wait until the "Updated parameter pXX" stream in terminal 1 stops (~30 s)

ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'CONF'}"
```

After this the firmware writes `activeConfig.yaml` and the map's `x_len`,
`y_len`, `z_len` are real. The next bringup auto-loads them.

If only one axis is misbehaving:

```bash
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'C_0 Y'}"
```

---

## 6. Adding plants to the map (if you want `P_4`/`P_5` to work)

`P_4` and `P_5` iterate over plants in the AURA `active_map.yaml`. If the map
is empty, those commands silently do nothing.

Add a plant with `P_1`:

```
P_1 x y z exclusion canopy water_qty max_z name growth_stage

e.g.  P_1 400 200 0 50 100 6 -200 tomato Seedling
```

Then `CONF` to save:

```bash
ros2 topic pub --once /keyboard_topic std_msgs/msg/String \
  "{data: 'P_1 400 200 0 50 100 6 -200 tomato Seedling'}"
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'CONF'}"
```

For larger gardens it's easier to write the YAML directly. See the AURA
`map_handler` README for the schema.

---

## 7. Emergency stop, properly

`e` halts the gantry mid-motion. The Farmduino remains in the `E_STOP` state
until you publish `E`.

**Always make `e` a separate code path that doesn't go through your normal
publishing queue.** If your app crashes, the watchdog should still be able
to send `e`. The simplest: a small dedicated publisher you only ever use for
e-stop, attached to a hardware button or signal handler:

```python
import signal

def _on_sigint(signum, frame):
    fb.estop()
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, _on_sigint)
```

If the user's app talks to the FarmBot over a network, an additional hardware
e-stop (a physical switch wired into the Farmduino) is recommended — the
network can drop.

---

## 8. What you'll want to add next (and which AURA features handle each)

| You'll want… | AURA gives you… |
|---|---|
| Sequencing several actions reliably | `farmbot_command_handler/state_command_handler` queue |
| Per-plant watering with moisture sensing | `P_5` + `watering_guide.yaml` |
| Stitched garden photos | `I_2` + `camera_handler/panorama.py` |
| Weed detection | `I_4` (publishes detection results) |
| Tool changes (seeder, water nozzle) | `T_n_*` commands; tools live in `map_handler` config |
| Daily watering on a schedule | A cron job that publishes `P_4` at a fixed time |

If your app needs more than the raw vocabulary, look at how
`autonomous_controller.py` in `farmbot_controllers` composes higher-level
behaviours — it's a good template.

---

## 9. Common mistakes

- **Publishing before the bringup is ready.** If you publish before
  `R99 ARDUINO STARTUP COMPLETE`, the command goes into the void. Either
  wait for the log line or subscribe to `/uart_receive` and wait for
  `R99 ARDUINO STARTUP COMPLETE` before starting your sequence.
- **Forgetting to source the workspace.** `source install/setup.bash` in
  every shell. ROS 2 fails silently here.
- **Using `H_0` to "go to origin".** `H_0` drives until it hits the endstops,
  which takes seconds. If you want to *move* to (0,0,0) without rehoming,
  use `M 0 0 0`.
- **Z is negative.** The gantry rail is at Z = 0 and the soil is at Z = -200
  or so. Positive Z means above the rail, which is usually not what you
  want. The controller will refuse if Z is outside the bounds in
  `active_map.yaml`'s `z_len`.
- **Speed must be 1–100.** `M_S x y z 150` won't work — the % cap is
  enforced firmware-side.
- **You publish `D_W_1` but nothing happens.** Bringup is up, but the
  Farmduino didn't ACK. Probably `e` is active — publish `E` first.

---

## 10. Where to look next

- AURA controller source: `src/farmbot_controllers/farmbot_controllers/farmbot_controller.py`
  — the `case` statement at the top is the canonical command list.
- The `keyboard_teleop.py` `valid_keys` / `compound_cmds` tuples are the
  formally accepted strings.
- ROS 2 docs on publishers: https://docs.ros.org/en/humble/Tutorials/Beginner-Client-Libraries/Writing-A-Simple-Py-Publisher-And-Subscriber.html

That's everything you need to drive the FarmBot from your own code. No
middleware, no behaviour tree, no extra processes.
