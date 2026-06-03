# Driving the FarmBot from your own code

Three integration paths, from highest-control / most code to lowest-effort /
most reliable. Pick the one that matches what you want to build.

## Pick your path

| Approach | Latency | Effort | When to use |
|---|---|---|---|
| [A. Direct ROS 2 publish](#approach-a--direct-ros-2-publishing) | ~1 ms | Highest | Python + rclpy app on the Pi; you want every command and every ack |
| [B. SSH bridge](#approach-b--ssh-bridge) | 5–500 ms | Lowest | Any language, off-Pi, hand the Pi a list of command strings |
| [C. V2 intent server](#approach-c--v2-intent-server) | 100–500 ms | Medium | Semantic commands ("water tomatoes"), safety guards run for you, multi-step trees |

You can run **B and C simultaneously** on the same Pi without conflict — the
downstream AURA stack doesn't care which process publishes to `keyboard_topic`.

---

## The command vocabulary (used by all three approaches)

Whatever path you choose, the strings that end up on `/keyboard_topic` are
the same. The full list lives in
[`src/farmbot_controllers/farmbot_controllers/farmbot_controller.py`](../src/farmbot_controllers/farmbot_controllers/farmbot_controller.py)
in the `case` statement at the top.

| Code | Meaning |
|---|---|
| `M x y z` | Move gantry to absolute position (mm). e.g. `M 400 200 -100` |
| `M_S x y z s` | Move at speed `s` (% of max). |
| `H_0` | Go to home position |
| `e` | **Emergency stop** — halts everything (lowercase) |
| `E` | Reset emergency stop (uppercase) |
| `P_1 x y z excl can wat max name stage` | Add a plant to the map |
| `P_2 index` | Remove plant by index |
| `P_3` | Seed all plants in `Planning` stage |
| `P_4` | Water all plants (rigid, per-plant `water_quantity`) |
| `P_5` | Water all plants (moisture-based) |
| `P_9` | Read moisture for all plants |
| `D_W_1` / `D_W_0` | Water pump ON / OFF |
| `D_L_1` / `D_L_0` | LED strip ON / OFF |
| `D_V_1` / `D_V_0` | Vacuum pump ON / OFF |
| `D_S_C` | Read soil sensor |
| `D_C` | Check tool mount |
| `I_1` | Take photo at current position |
| `I_2` | Stitch panorama |
| `I_4` | Detect weeds |
| `C_0 [X/Y/Z]` | Calibrate axes (drive to endstops + write firmware params) |
| `C_1 ver` | Load a parameter preset (`C_1 Genesis`) |
| `CONF` | Save running params to `activeConfig.yaml` + publish workspace dims |

## Things to know before any approach works

- **Bringup must be running** on the Pi: `ros2 launch farmbot_bringup standard.launch.py`. Wait for `R99 ARDUINO STARTUP COMPLETE` before publishing.
- **First-boot calibration**: on a fresh Pi, the firmware doesn't know its own axis lengths. Run `C_0` then `CONF` once. After that, every bringup auto-loads `activeConfig.yaml`.
- **Z is negative**: rail is `z = 0`, soil is `z = -200`-ish. Z out of bounds gets rejected.
- **Z bounds and X/Y bounds come from `active_map.yaml`** — if the map's `x_len/y_len/z_len` is zero, every move command fails. Run `CONF` to rebuild map dims from the current firmware params.

---

## Approach A — Direct ROS 2 publishing

Lowest latency, full control. Your code is a ROS 2 node on the Pi that
publishes `std_msgs/String` to `/keyboard_topic`. The AURA controller picks
it up exactly as if `keyboard_controller` had sent it.

### Smoke test (no code)

```bash
# Terminal 1: bringup running
# Terminal 2:
ros2 topic echo /keyboard_topic &
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'H_0'}"
```

You should see `data: H_0` in terminal 2 and the gantry should home.

### Minimal publisher

```python
#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class FarmBotCommander(Node):
    def __init__(self):
        super().__init__('farmbot_commander')
        self.pub = self.create_publisher(String, 'keyboard_topic', 10)

    def send(self, command: str):
        msg = String(); msg.data = command
        self.pub.publish(msg)
        self.get_logger().info(f"published: {command}")


def main():
    rclpy.init()
    node = FarmBotCommander()
    rclpy.spin_once(node, timeout_sec=1.0)   # DDS discovery

    node.send("H_0")
    node.send("M 400 200 -100")
    node.send("D_W_1")
    rclpy.spin_once(node, timeout_sec=2.0)
    node.send("D_W_0")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
```

Run with the AURA workspace sourced:

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
source ~/farmbot_ws/install/setup.bash
python3 farmbot_commander.py
```

### Reusable wrapper class

```python
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class FarmBotDirect:
    """Drive the AURA FarmBot by raw command publication.

    Safety contract:
      - Movement is fire-and-forget; the controller ACKs the command and starts
        moving asynchronously. Build in worst-case waits or subscribe to
        /uart_receive to know when a move finishes.
      - Always pair D_W_1 with D_W_0 in a try/finally. A crash mid-water
        floods the bed.
      - Emergency stop ('e') must bypass any command queue.
    """

    def __init__(self, node: Node, topic: str = "keyboard_topic"):
        self._node = node
        self._pub = node.create_publisher(String, topic, 10)
        rclpy.spin_once(node, timeout_sec=0.5)

    def publish(self, cmd: str):
        msg = String(); msg.data = cmd
        self._pub.publish(msg)
        self._node.get_logger().info(f"-> {cmd}")
        rclpy.spin_once(self._node, timeout_sec=0.02)

    def move_to(self, x, y, z, speed=None):
        if speed is None:
            self.publish(f"M {int(x)} {int(y)} {int(z)}")
        else:
            self.publish(f"M_S {int(x)} {int(y)} {int(z)} {int(speed)}")

    def home(self): self.publish("H_0")
    def lights(self, on: bool): self.publish("D_L_1" if on else "D_L_0")
    def photo(self): self.publish("I_1")
    def read_moisture(self): self.publish("D_S_C")
    def estop(self): self.publish("e")
    def reset_estop(self): self.publish("E")

    def water_for(self, seconds: float):
        try:
            self.publish("D_W_1")
            time.sleep(seconds)
        finally:
            self.publish("D_W_0")
```

### Knowing when a move has finished

The `keyboard_topic` API is fire-and-forget. Three options:

1. **Subscribe to `/uart_receive`** — Farmduino sends `R02` (idle) and
   `R99 ... COMPLETE` over UART, republished to this topic. Watch for the
   ack you want.
2. **Subscribe to the position topic** — compare against your target with a
   tolerance.
3. **Sleep** — crude but works for prototyping. ~10 mm/s typical jog speed;
   budget 100 s per 1000 mm move plus a 5 s margin.

---

## Approach B — SSH bridge

Your app already knows the command strings (`H_0`, `M ...`, `D_W_1`, ...).
SSH into the Pi and hand them to a tiny shim that publishes each one.

```
your app ──ssh──▶ farmbot-send (Pi) ──▶ keyboard_topic ──▶ FarmBot
```

### 1. SSH key auth (so no password prompts)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/farmbot_id -N ""
ssh-copy-id -i ~/.ssh/farmbot_id.pub farmbotdev@<pi-host>
```

### 2. Install the publisher shim on the Pi (one-time)

The shim reads commands from stdin and publishes each to `/keyboard_topic`:

```bash
sudo tee /usr/local/bin/farmbot-publish.py >/dev/null <<'PY'
#!/usr/bin/env python3
"""Read commands from stdin, publish each to /keyboard_topic, exit."""
import sys
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def main():
    rclpy.init()
    node = Node("farmbot_publish_shim")
    pub = node.create_publisher(String, "keyboard_topic", 10)
    rclpy.spin_once(node, timeout_sec=0.5)  # DDS discovery

    for line in sys.stdin:
        cmd = line.strip()
        if not cmd or cmd.startswith("#"):
            continue
        msg = String(); msg.data = cmd
        pub.publish(msg)
        sys.stderr.write(f"-> {cmd}\n")
        rclpy.spin_once(node, timeout_sec=0.05)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
PY

sudo tee /usr/local/bin/farmbot-send >/dev/null <<'SH'
#!/usr/bin/env bash
# Source ROS 2 + AURA workspace, then run the publisher shim.
ROS_DISTRO="${ROS_DISTRO:-jazzy}"
WS_PATH="${WS_PATH:-$HOME/Rishabh_Growmate_FarmBot}"

source "/opt/ros/${ROS_DISTRO}/setup.bash"
[ -f "${WS_PATH}/install/setup.bash" ] && source "${WS_PATH}/install/setup.bash"

exec python3 /usr/local/bin/farmbot-publish.py
SH

sudo chmod +x /usr/local/bin/farmbot-publish.py /usr/local/bin/farmbot-send
```

Adjust `ROS_DISTRO` and `WS_PATH` in `/usr/local/bin/farmbot-send` to match
your Pi.

Quick test on the Pi: `echo "H_0" | farmbot-send` should home the gantry.

### 3. Call it from anywhere

**Bash:**
```bash
echo "H_0" | ssh farmbotdev@<pi-host> farmbot-send

# many commands at once
ssh farmbotdev@<pi-host> farmbot-send <<'EOF'
H_0
M 400 200 -100
D_W_1
D_W_0
EOF
```

**Python (paramiko):**
```python
import paramiko

class FarmBotSSH:
    def __init__(self, host, user, key_path):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(host, username=user, key_filename=key_path)

    def send(self, *commands: str) -> str:
        stdin, _, stderr = self.client.exec_command("farmbot-send")
        stdin.write("\n".join(commands) + "\n")
        stdin.channel.shutdown_write()
        return stderr.read().decode()

    def close(self): self.client.close()


fb = FarmBotSSH("<pi-host>", "farmbotdev", "~/.ssh/farmbot_id")
print(fb.send("H_0"))
print(fb.send("M 400 200 -100", "D_W_1", "D_W_0"))
fb.close()
```

**Node.js (ssh2):**
```javascript
const { Client } = require('ssh2');
const fs = require('fs');

function sendFarmBot(commands) {
  return new Promise((resolve, reject) => {
    const conn = new Client();
    conn.on('ready', () => {
      conn.exec('farmbot-send', (err, stream) => {
        if (err) return reject(err);
        let log = '';
        stream.stderr.on('data', d => { log += d.toString(); });
        stream.on('close', () => { conn.end(); resolve(log); });
        stream.end(commands.join('\n') + '\n');
      });
    }).connect({
      host: '<pi-host>',
      username: 'farmbotdev',
      privateKey: fs.readFileSync('/home/me/.ssh/farmbot_id'),
    });
  });
}

sendFarmBot(['H_0', 'M 400 200 -100']).then(log => console.log(log));
```

### Drop SSH handshake latency to ~5 ms

OpenSSH ControlMaster keeps a session open and reuses it. In `~/.ssh/config`:

```
Host farmbot
    HostName <pi-host>
    User farmbotdev
    IdentityFile ~/.ssh/farmbot_id
    ControlMaster auto
    ControlPath ~/.ssh/cm_%r@%h:%p
    ControlPersist 10m
```

First `ssh farmbot` opens the connection. Subsequent `ssh farmbot
farmbot-send` calls reuse it.

### Emergency stop pattern

`e` must not queue behind other commands. Give it its own SSH call:

```bash
ssh farmbot farmbot-send <<< "e"
```

Wrap in your app's panic / signal handler. If the SSH session is hung,
fail to a hardware e-stop.

---

## Approach C — V2 intent server

Highest level. POST semantic JSON to the Pi, it builds a py_trees behaviour
tree with safety guards (bounds, plant lookup, availability) and executes.
Your app doesn't need to know FarmBot command strings.

### Setup on the Pi (sparse checkout)

```bash
cd ~
git clone --filter=blob:none --no-checkout https://github.com/rishabh12j/voice-farmbot.git growmate
cd growmate
git sparse-checkout init --cone
git sparse-checkout set src/growmate_pi
git checkout main

python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
```

### Configure your garden

Edit `src/growmate_pi/config/farmbot.yaml`:

```yaml
robot:
  workspace: {x_max: 2900, y_max: 1400, z_min: -300, z_max: 0}

garden:
  plants:
    - name: tomatoes
      aliases: [the tomatoes, tomato bed]
      position: {x: 400, y: 200, z: -100}
      water_quantity: 6
    - name: lettuce
      aliases: [the lettuce, salad]
      position: {x: 1200, y: 200, z: -100}
      water_quantity: 5

safety:
  workspace_bounds:
    x: [0, 2900]
    y: [0, 1400]
    z: [-300, 0]
```

### Run the intent server

```bash
cd ~/growmate
source /opt/ros/$ROS_DISTRO/setup.bash
source venv/bin/activate
PYTHONPATH=src python -m growmate_pi.intent_server --port 8000
```

Expected first line: `Bridge: connected, publishing to 'keyboard_topic'`.

### Endpoints

| Method + path | Purpose |
|---|---|
| `POST /intent` | Build + tick a BT from an Intent JSON |
| `POST /estop` | Publish `e` immediately (no body) |
| `POST /reset_estop` | Publish `E` (no body) |
| `GET /status` | Bridge state + config path |
| `GET /plants` | The plant list from the loaded `active_map.yaml` |
| `GET /history?limit=N` | Last N commands published |
| `GET /docs` | Interactive Swagger UI |

### The semantic actions

| `action` | Becomes |
|---|---|
| `water_all` | `P_4` |
| `water` + plant target | `M x y z` → `D_W_1` → wait → `D_W_0` |
| `move` + target OR explicit x/y/z | `M x y z` |
| `go_home` | `H_0` |
| `light_on` / `light_off` | `D_L_1` / `D_L_0` |
| `photo` | `I_1` |
| `panorama` | `I_2` |
| `scan_weeds` | `I_4` |
| `check_moisture` | `P_9` |
| `check_sensor` + target | `M x y z` → `D_S_C` |
| `emergency_stop` | `e` (or use `POST /estop`) |
| `general_question` | No robot action — just records text for TTS |

### Example payloads

**Water by plant name** (Pi resolves the name from its garden config):
```json
POST /intent
{
  "intents": [
    {"action": "water", "target": "tomatoes",
     "params": {"duration_s": 3},
     "response": "Watering tomatoes."}
  ],
  "raw_text": "water the tomatoes",
  "client_id": "my-app"
}
```

**Move to arbitrary coordinates:**
```json
{
  "intents": [
    {"action": "move",
     "params": {"x": 1500, "y": 800, "z": -200},
     "response": "Moving."}
  ],
  "raw_text": "move",
  "client_id": "my-app"
}
```

**Multi-step sequence** — Pi runs them in order with safety checks between:
```json
{
  "intents": [
    {"action": "go_home", "response": "Home."},
    {"action": "light_on", "response": "Lights on."},
    {"action": "photo", "response": "Photo."}
  ],
  "raw_text": "home, lights, photo",
  "client_id": "my-app"
}
```

### What you DON'T get with the intent server

- **No arbitrary command strings.** If you need `M_S 400 200 -100 50` (custom
  speed), `C_2 X` (invert encoder), or `T_1_0` (tool commands), those aren't
  wrapped. Drop to Approach A or B for those.
- **No live UART acks.** You get the whole BT result when it finishes. For
  intermediate Farmduino acks, subscribe to `/uart_receive` directly.

### Wire-format contract

The frozen Pydantic schema lives at
[`src/growmate_pi/schemas.py`](../src/growmate_pi/schemas.py). Both client
and server import from there.

```python
class IntentRequest(BaseModel):
    intents: List[Intent]
    raw_text: str
    emergency: bool = False
    client_id: str = "unknown"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0.0"
```

### Run as a service on boot

`/etc/systemd/system/growmate-intent.service`:

```ini
[Unit]
Description=GrowMate intent server
After=network.target

[Service]
Type=simple
User=YOUR_PI_USER
WorkingDirectory=/home/YOUR_PI_USER/growmate
Environment="PYTHONPATH=/home/YOUR_PI_USER/growmate/src"
ExecStart=/bin/bash -lc 'source /opt/ros/$ROS_DISTRO/setup.bash && \
  source /home/YOUR_PI_USER/growmate/venv/bin/activate && \
  python -m growmate_pi.intent_server --port 8000'
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now growmate-intent
```

---

## Common gotchas (apply to all three approaches)

- **Publishing before bringup is ready.** If you publish before `R99 ARDUINO
  STARTUP COMPLETE`, the command goes into the void. Wait for the log line
  or subscribe to `/uart_receive` and wait for the ack.
- **Forgetting to source the workspace.** Every shell needs
  `source install/setup.bash`. ROS 2 fails silently otherwise.
- **`H_0` is rehoming, not "move to origin".** Drives until it hits the
  endstops. If you want to go to (0,0,0) without rehoming, use `M 0 0 0`.
- **Speed must be 1–100.** `M_S x y z 150` won't work — capped firmware-side.
- **Map is empty after `cp`.** Restart the bringup so `map_controller`
  reloads the file. Edits to a running map_handler don't take effect until
  restart.
- **Map exists but P_4 silently does nothing.** Check
  `plant_details.plant_count` in the YAML. If it's 0 or
  `plant_details.plants` is null, the build script wrote an empty container.
- **Two e-stops at once.** Whoever publishes `e` first wins; the second is
  a no-op. Safe but check the logs to confirm Farmduino received it.
- **Commands queue without backpressure.** Both rclpy and the SSH shim
  publish-and-forget. The Farmduino has its own internal queue, and if you
  flood it, the AURA stack will drop messages. Pace your commands.

## Where to look next

- Controller source (canonical command list):
  [`src/farmbot_controllers/farmbot_controllers/farmbot_controller.py`](../src/farmbot_controllers/farmbot_controllers/farmbot_controller.py)
- Keyboard teleop (valid input strings):
  [`src/farmbot_controllers/farmbot_controllers/keyboard_teleop.py`](../src/farmbot_controllers/farmbot_controllers/keyboard_teleop.py)
- Higher-level autonomous logic to model your own app after:
  [`src/farmbot_controllers/farmbot_controllers/autonomous_controller.py`](../src/farmbot_controllers/farmbot_controllers/autonomous_controller.py)
- ROS 2 publisher tutorial: https://docs.ros.org/en/humble/Tutorials/Beginner-Client-Libraries/Writing-A-Simple-Py-Publisher-And-Subscriber.html
