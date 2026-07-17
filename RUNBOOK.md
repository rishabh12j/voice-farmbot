# RUNBOOK — bring GrowMate up

The one operational document: update and launch a greenhouse Pi, start the voice
app on Windows, and check it actually works.

For *why* the system is built this way — and the safety invariants any change
must preserve — see [README.md](README.md).

## The two greenhouses

| | gh1 | gh2 |
|---|---|---|
| Address | `192.168.0.38` | `192.168.0.53` |
| SSH user | `gh1` | `farmbotdev` |
| Plants | 56 | 35 |
| Garden config | `src/growmate_pi/config/gh1.yaml` | `src/growmate_pi/config/farmbotdev.yaml` |
| Map seed | `tools/maps/gh1.yaml` | `tools/maps/gh2.yaml` |
| Status | primary thesis hardware | second greenhouse |

Naming note: gh2 is the greenhouse previously called **farmbotdev**. Its Pi
hostname and SSH user are both `farmbotdev` and its config file is still
`farmbotdev.yaml` — the file has local edits, so the rename is deferred rather
than forgotten.

`src/growmate_pi/config/farmbot.yaml` is neither greenhouse: it is the **sim
default** used by the desktop client and the eval harness.

---

## 1. Pi — update

SSH in (`ssh gh1@192.168.0.38` / `ssh farmbotdev@192.168.0.53`), then:

```bash
cd ~/Rishabh_Growmate_FarmBot
git status                 # expect a clean tree; deal with local edits first
git pull origin main
```

If `git pull` refuses because a tracked file has local changes, look at the file
before discarding it — map/config edits made on the Pi are sometimes the only
copy that exists.

The code is a plain checkout on each Pi. **Pure `growmate_pi` changes need no
build** — a pull plus a restart of the intent server is enough. The build below
is only for changes to the AURA packages (`farmbot_*`, `map_handler`,
`camera_handler`).

## 2. Pi — build (only when AURA packages changed)

**The rule: build OUTSIDE the venv, run the intent server INSIDE it.** The venv
lacks ROS 2's message-generation deps; building inside it fails with
`ModuleNotFoundError: No module named 'em'` and silently leaves you running the
upstream packages instead of yours.

```bash
deactivate 2>/dev/null                    # IMPORTANT — out of the venv to build
which python3                             # want /usr/bin/python3

source /opt/ros/$ROS_DISTRO/setup.bash
source ~/FarmBot_ROS2/install/setup.bash 2>/dev/null || true   # upstream FIRST

colcon build --symlink-install \
  --allow-overriding farmbot_command_handler farmbot_controllers \
                     farmbot_interfaces map_handler camera_handler farmbot_bringup

source install/setup.bash                 # ours LAST, so it wins
```

Then confirm every package resolves to **your** workspace, not the standalone
upstream one:

```bash
for pkg in farmbot_bringup farmbot_controllers farmbot_command_handler \
           farmbot_interfaces map_handler camera_handler; do
  printf "%-30s -> %s\n" "$pkg" "$(ros2 pkg prefix $pkg 2>/dev/null || echo NOT-FOUND)"
done
```

Every line must end in `~/Rishabh_Growmate_FarmBot/install/...`. If any points
at `~/FarmBot_ROS2/install/...`, the upstream copy is shadowing your edits: the
robot will home fine and then fail to water, because the upstream `map_handler`
knows nothing about this garden. `~/.bashrc` on these Pis often auto-sources the
upstream workspace, which puts it back in front no matter what order you typed —
use `source ~/use-rishabh.sh` (from `tools/use-rishabh.sh`) to fix the order for
the current shell only, leaving other people's sessions alone.

First time on a Pi, create the venv **after** a successful build:

```bash
python3 -m venv --system-site-packages venv   # --system-site-packages or no rclpy
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
touch venv/COLCON_IGNORE
deactivate
```

## 3. Pi — one-time state-dir migration

Mutable robot state (`active_map.yaml`, `watering_guide.yaml`) lives in one
writable directory outside the build tree — `FARMBOT_STATE_DIR`, default
`~/.farmbot` — read and written by both the AURA stack and GrowMate. Before this
existed, every `colcon build` clobbered `watering_guide.yaml` and the map had two
homes. **Run once per Pi, before the first launch of this code:**

```bash
STATE="${FARMBOT_STATE_DIR:-$HOME/.farmbot}"
mkdir -p "$STATE"
SHARE=$(ros2 pkg prefix map_handler)/share/map_handler/config
cp -n "$SHARE/active_map.yaml"     "$STATE/active_map.yaml"     2>/dev/null || \
  echo "no install-share active_map — will seed empty on first launch"
cp -n "$SHARE/watering_guide.yaml" "$STATE/watering_guide.yaml" 2>/dev/null || true
ls -l "$STATE"
```

Check whether a Pi has been migrated with `ls ~/.farmbot`. To load a fresh
garden instead, copy the seed map in: `cp tools/maps/gh1.yaml ~/.farmbot/active_map.yaml`
(`gh2.yaml` on gh2), then confirm the plant count in step 5.

## 4. Pi — launch

One command starts bringup, then the intent server, then the daily scheduler:

```bash
cd ~/Rishabh_Growmate_FarmBot
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash

# gh1 (config defaults to gh1.yaml)
ros2 launch ./launch/greenhouse.launch.py scheduler:=false

# gh2
ros2 launch ./launch/greenhouse.launch.py \
    config:=src/growmate_pi/config/farmbotdev.yaml scheduler:=false
```

| Launch arg | Default | Use |
|---|---|---|
| `config:=` | `src/growmate_pi/config/gh1.yaml` | which greenhouse |
| `scheduler:=` | `true` | `false` while testing — otherwise it waters at 08:00 |
| `camera:=` | `true` | `false` if the camera is faulty and flooding the log |
| `port:=` | `8000` | intent server port |
| `state_dir:=` | `~/.farmbot` | mutable state location |
| `venv_python:=`, `src:=` | repo paths | non-standard layouts |

Wait for **both** `R99 ARDUINO STARTUP COMPLETE` and `Initialized with active
config from previous run`, with no `map_controller` traceback. On a robot with no
`activeConfig.yaml`, first bringup needs **C_0 calibration then CONF** to capture
real workspace dimensions — a preset alone is not enough.

## 5. Pi — confirm it is real, not sim

```bash
curl -s http://localhost:8000/status | python3 -m json.tool
```

Three things matter:

- `"bridge_mode": "ros2"` — **`sim` means the intent server could not import
  `rclpy`** and is quietly pretending. Usually `PYTHONPATH` was replaced instead
  of prepended (`src:$PYTHONPATH`), or the venv lacks `--system-site-packages`.
- `"verify_enabled": true` — the tick-and-verify gate is on. It depends on
  `/busy_state`; check the firmware publishes it with `ros2 topic echo /busy_state`
  while homing (expect `true` then `false`). If it never appears, run the intent
  server by hand with `--no-verify` rather than letting every move time out.
- The plant count matches the garden:

```bash
curl -s http://localhost:8000/plants \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('plants:', d.get('count'), '| source:', d.get('source'))"
```

Expect `plants: 56` on gh1, `plants: 35` on gh2. A count of 0 means the map
didn't load — fix that before touching the app, or `H_0` will work and `P_4`
will do nothing.

## 6. Windows — voice app

Ollama must be running with `gemma3:4b` pulled; it is the classifier.

```powershell
cd C:\Users\risha\growmate-bt\voice-farmbot
$env:PYTHONPATH = "C:\Users\risha\growmate-bt\voice-farmbot\src;" + $env:PYTHONPATH

# gh1
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.0.38:8000/intent

# gh2
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.0.53:8000/intent
```

Look for `V2 mode: dispatching to Pi at ...` and `Pi ready: ...`, then open
`http://localhost:7860` and hard-refresh (Ctrl+Shift+R) — the UI is embedded, so
a cached page will silently run yesterday's JavaScript.

`--no-ros2` is correct here: Windows never talks ROS, it POSTs to the Pi.

**Phone access:** the mic button needs an HTTPS origin (`localhost` is the only
exception), so a phone on the LAN gets the UI but no microphone. For a demo,
tunnel it: `cloudflared tunnel --url http://localhost:7860 --protocol http2` and
open the printed `https://…trycloudflare.com` URL. The URL rotates each launch.

## 7. Local sim — no Pi, no robot

```powershell
# Terminal 1
$env:PYTHONPATH = "src;" + $env:PYTHONPATH
python -m growmate_pi.intent_server --no-ros2 --port 8123

# Terminal 2
$env:PYTHONPATH = "src;" + $env:PYTHONPATH
python -m growmate_voice.app --no-ros2 --pi-url http://localhost:8123/intent
```

The BT, event log, and HTTP server are all real; only the robot is not. This is
also the loop the evaluation harness runs against:

```powershell
$env:PYTHONPATH = "src;src\growmate_voice;" + $env:PYTHONPATH
python tools\evaluate_v2.py --pi-url http://localhost:8123/intent --skip-long

# the full 2000-case corpus takes hours — always stream, so a crash costs nothing
python tools\evaluate_v2.py --pi-url http://localhost:8123/intent `
  --corpus tools\corpus\growmate_test_corpus.json `
  --stream run.jsonl --resume
```

Ollama must be up with `gemma3:4b` — the classifier is the thing under test.
`--no-llm` is a Pi-only smoke mode that deliberately sends `water_all`; never
read its DBSR as a result.

Before committing a builder/node/schema change, from the repo root:

```bash
PYTHONPATH=src python3 -m growmate_pi.verify_sim     # expect Failures: 0/N
python tools/test_wire_grammar.py
python tools/test_verify_semantics.py
```

On Windows this runs under WSL:
`wsl -d Ubuntu-22.04 -- bash -lc "cd <repo> && PYTHONPATH=src ./venv-wsl/bin/python3 -m growmate_pi.verify_sim"`.

---

## 8. When it misbehaves

| Symptom | Cause | Fix |
|---|---|---|
| `H_0` moves, `P_4` does nothing | Map empty, or upstream `map_handler` shadowing yours | Check `/plants` count (§5) and the `ros2 pkg prefix` loop (§2) |
| `/status` says `"bridge_mode": "sim"` on the Pi | `rclpy` import failed | `PYTHONPATH=src:$PYTHONPATH` (prepend!); venv needs `--system-site-packages` |
| `colcon build`: `No module named 'em'` | Building inside the venv | `deactivate`, `sudo apt install python3-empy python3-lark python3-catkin-pkg`, rebuild |
| `map_controller` traceback at bringup | `active_map.yaml` wiped or malformed | Re-seed from `tools/maps/gh1.yaml` into `~/.farmbot`, relaunch |
| Verified moves all time out to `partial` | `/busy_state` never publishes | Confirm with `ros2 topic echo /busy_state`; run with `--no-verify` until fixed |
| App says "Pi not reachable" | Server down or port 8000 blocked | `curl http://<pi-ip>:8000/plants` from Windows |
| UI shows old behaviour after a restart | Cached embedded HTML/JS | Hard-refresh (Ctrl+Shift+R) |
| ROS nodes can't see each other over WiFi | Multicast discovery eaten by the network | `export ROS_DOMAIN_ID=1` and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` (gh1's working setup) |
| Camera opens but returns no frames | Cable/port fault, `-71 EPROTO` in `dmesg` | Try another USB port, then the cable; `camera:=false` to keep working meanwhile |
| A `pkill -f <pattern>` exits 255 | The pattern matched its own command line | Kill by PID, or bracket the pattern |
