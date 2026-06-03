# Sending FarmBot commands over SSH

Your app already knows the command strings (`H_0`, `M 400 200 -100`,
`D_W_1`, …). It just needs to hand them to the Pi over SSH. The Pi runs a
tiny shim that reads commands from stdin and publishes each one to
`/keyboard_topic`.

```
your app ──ssh──▶ farmbot-send (Pi) ──▶ keyboard_topic ──▶ FarmBot
```

Three things to set up: SSH keys, the Pi-side shim, and your app's
SSH calls.

---

## 1. SSH key auth (so no password prompts)

On the machine where your app runs:

```bash
# generate a key if you don't have one
ssh-keygen -t ed25519 -f ~/.ssh/farmbot_id -N ""

# copy it to the Pi (replace user/host)
ssh-copy-id -i ~/.ssh/farmbot_id.pub farmbotdev@192.168.0.54
```

Now `ssh -i ~/.ssh/farmbot_id farmbotdev@192.168.0.54` works without
typing a password. Your app uses the same key.

If you can't use keys (Windows app, kiosk, etc.) you can install
`sshpass` and pass the password on the command line — less safe but works
on a closed LAN.

---

## 2. Install the publisher shim on the Pi

This is a 25-line Python script + a 3-line wrapper. It reads command
strings from stdin, one per line, publishes each to `/keyboard_topic`,
then exits. ROS 2 init is done **once** per invocation.

SSH into the Pi:

```bash
ssh farmbotdev@192.168.0.54
```

Create the Python publisher:

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
    rclpy.spin_once(node, timeout_sec=0.5)  # DDS discovery handshake

    for line in sys.stdin:
        cmd = line.strip()
        if not cmd or cmd.startswith("#"):
            continue
        msg = String()
        msg.data = cmd
        pub.publish(msg)
        sys.stderr.write(f"-> {cmd}\n")
        rclpy.spin_once(node, timeout_sec=0.05)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
PY
```

Create the wrapper that sources ROS 2 first:

```bash
sudo tee /usr/local/bin/farmbot-send >/dev/null <<'SH'
#!/usr/bin/env bash
# Source ROS 2 + the AURA workspace, then run the publisher shim.
# Edit ROS_DISTRO and WS_PATH for your Pi.
ROS_DISTRO="${ROS_DISTRO:-jazzy}"
WS_PATH="${WS_PATH:-$HOME/Rishabh_Growmate_FarmBot}"

source "/opt/ros/${ROS_DISTRO}/setup.bash"
[ -f "${WS_PATH}/install/setup.bash" ] && source "${WS_PATH}/install/setup.bash"

exec python3 /usr/local/bin/farmbot-publish.py
SH

sudo chmod +x /usr/local/bin/farmbot-publish.py /usr/local/bin/farmbot-send
```

Adjust `ROS_DISTRO` and `WS_PATH` in the wrapper to match your Pi setup
(`echo $ROS_DISTRO` while sourced gives the right value).

Quick test, still on the Pi:

```bash
echo "H_0" | farmbot-send
```

If the gantry homes and you see `-> H_0` printed to stderr, the shim is
good.

---

## 3. Call it from your app

The pattern is identical in every language: SSH in, run `farmbot-send`,
pipe commands.

### Bash / shell

One command at a time:

```bash
echo "H_0" | ssh farmbotdev@192.168.0.54 farmbot-send
```

Many at once:

```bash
ssh farmbotdev@192.168.0.54 farmbot-send <<'EOF'
H_0
M 400 200 -100
D_W_1
D_W_0
EOF
```

Lines starting with `#` are skipped, so you can keep notes:

```bash
ssh farmbotdev@192.168.0.54 farmbot-send <<'EOF'
# water tomatoes
M 400 200 -100
D_W_1
EOF
```

### Python (paramiko)

```python
import paramiko

class FarmBotSSH:
    def __init__(self, host, user, key_path):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(host, username=user, key_filename=key_path)

    def send(self, *commands: str) -> str:
        stdin, stdout, stderr = self.client.exec_command("farmbot-send")
        stdin.write("\n".join(commands) + "\n")
        stdin.channel.shutdown_write()
        return stderr.read().decode()      # publisher logs each cmd to stderr

    def close(self):
        self.client.close()


fb = FarmBotSSH("192.168.0.54", "farmbotdev", "~/.ssh/farmbot_id")
print(fb.send("H_0"))
print(fb.send("M 400 200 -100", "D_W_1", "D_W_0"))
fb.close()
```

### Node.js (ssh2)

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
        stream.on('data', d => { /* stdout */ });
        stream.stderr.on('data', d => { log += d.toString(); });
        stream.on('close', () => { conn.end(); resolve(log); });
        stream.end(commands.join('\n') + '\n');
      });
    }).connect({
      host: '192.168.0.54',
      username: 'farmbotdev',
      privateKey: fs.readFileSync('/home/me/.ssh/farmbot_id'),
    });
  });
}

sendFarmBot(['H_0', 'M 400 200 -100']).then(log => console.log(log));
```

### Curl-style one-liner from a CI script

```bash
ssh -i ~/.ssh/farmbot_id farmbotdev@192.168.0.54 farmbot-send <<< "P_4"
```

---

## 4. Persistent connection (lower latency)

Every `ssh` invocation pays the TCP + key handshake (~200–500 ms on a LAN).
For high-rate command streams, keep a single SSH session open and reuse
it via OpenSSH's control master:

In `~/.ssh/config`:

```
Host farmbot
    HostName 192.168.0.54
    User farmbotdev
    IdentityFile ~/.ssh/farmbot_id
    ControlMaster auto
    ControlPath ~/.ssh/cm_%r@%h:%p
    ControlPersist 10m
```

First `ssh farmbot` opens the connection. Subsequent `ssh farmbot
farmbot-send` calls reuse it — handshake cost drops to ~5 ms.

For applications that need sub-millisecond latency, see section 6 below.

---

## 5. Emergency stop

`e` must not queue behind other commands. Give it its own SSH call (don't
batch it with anything else):

```bash
ssh farmbot farmbot-send <<< "e"
```

Wrap it in your app's panic / signal handler. If the SSH session is hung,
fail to a hardware e-stop — see section 7 of the direct-publish guide.

---

## 6. If SSH latency hurts — alternatives

SSH adds ~5–500 ms per command depending on connection reuse. Three faster
options:

| Option | Latency | Trade-off |
|---|---|---|
| Persistent SSH with ControlMaster | ~5 ms | Easiest, no new daemon |
| HTTP daemon on Pi (your code POSTs) | ~2 ms | One small Flask/FastAPI server |
| MQTT broker on Pi + ROS bridge | ~1 ms | Best for many publishers, more infra |

If you need any of these, ask — they're all small additions.

---

## 7. What you don't get

Important: this shim is a thin transport layer. It does **not** do any of
this for you. You are responsible for:

- **Knowing what commands to send.** If you publish `D_W_1` and forget
  `D_W_0`, the pump runs until power is cut.
- **Sequencing.** The shim publishes each line and moves on. The Farmduino
  doesn't ACK back to you. For sequential operations either subscribe to
  `/uart_receive` yourself (see direct-publish guide section 4) or build
  in worst-case sleeps.
- **Bounds checking.** `M 99999 99999 99999` will be published verbatim.
  The Farmduino will refuse it, but other components may misbehave.
- **Concurrency.** Two apps SSHing in at the same time will interleave
  their commands on `/keyboard_topic`. The downstream controller can't
  tell whose command is whose.

If you want any of the above, that's the layer above this. Ping me.

---

## 8. Troubleshooting

- **`farmbot-send: command not found`** — `chmod +x` didn't stick, or the
  script lives outside `$PATH`. Confirm with `which farmbot-send` while
  SSHed in.
- **`ImportError: No module named rclpy`** — the wrapper isn't sourcing
  ROS 2. Verify `ROS_DISTRO` in `/usr/local/bin/farmbot-send` matches
  your install.
- **Commands published but FarmBot doesn't move** — bringup isn't running
  (`ros2 launch farmbot_bringup standard.launch.py`), or you're in an
  active e-stop state. Send `E` to clear, then retry.
- **First command of a session is lost** — DDS discovery hasn't completed
  before the first publish. The shim sleeps 0.5 s for this; if it's still
  flaky, bump to 1.0 s in `farmbot-publish.py`.
- **`Permission denied (publickey)`** — your key isn't on the Pi. Re-run
  `ssh-copy-id`.

---

## Cheat-sheet

```bash
# one-shot
echo "H_0" | ssh farmbot farmbot-send

# multi-command
ssh farmbot farmbot-send <<EOF
H_0
M 400 200 -100
D_W_1
D_W_0
EOF

# emergency stop (always on its own)
ssh farmbot farmbot-send <<< "e"
```

That's the whole interface.
