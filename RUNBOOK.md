# RUNBOOK — clone GrowMate-BT and run it

The one operational document: clone the repo, run it in **simulation** (no robot),
or bring up a real greenhouse **Pi** and drive it from the **Windows** voice app.

For *why* the system is built this way — and the safety invariants any change must
preserve — see [README.md](README.md).

> **Placeholders.** Commands use angle-bracket placeholders — substitute your own:
> | Placeholder | Meaning | Example |
> |---|---|---|
> | `<REPO>` | repo folder on your PC | `C:\dev\voice-farmbot` |
> | `<PI_USER>@<PI_HOST>` | the Pi's SSH login + address | `pi@192.168.1.20` |
> | `<PI_REPO>` | repo path on the Pi | `~/voice-farmbot` |
> | `<GARDEN_CONFIG>` | per-greenhouse config | `src/growmate_pi/config/gh1.yaml` |
> | `<STATE_DIR>` | mutable state dir (`FARMBOT_STATE_DIR`) | `~/.farmbot` |

---

## 0. Get the code

```bash
git clone https://github.com/rishabh12j/voice-farmbot.git
cd voice-farmbot
```

**Prerequisites**
- **Python 3.10+** on the machine running the voice app (Windows is the reference).
- **[Ollama](https://ollama.com)** running, with the classifier pulled: `ollama pull gemma3:4b`.
- For **sim on Windows**: WSL is optional but is how the Pi-side server is usually run.
- For **hardware**: a Raspberry Pi with ROS 2 + the AURA FarmBot stack built (§B).

Two paths follow: **A. Simulation** (no robot, everything on your PC) and
**B. Real robot** (Pi + Windows app). The app command is the same shape in both;
only `--pi-url` changes.

---

## A. Simulation — no Pi, no robot

The behaviour tree, event log, and HTTP server are all real; only the robot is
simulated. This is also the loop the evaluation harness runs against.

Two terminals: **the intent server** (the "brain") and **the voice app**. Run the
server either in WSL or in PowerShell — pick one.

### A1. Intent server (the brain)

**Option 1 — WSL** (Linux venv holds the py_trees/pydantic deps):
```bash
cd /mnt/c/<path-to>/voice-farmbot          # the repo, seen from WSL
python3 -m venv venv && source venv/bin/activate     # first time only
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
PYTHONPATH=src python3 -m growmate_pi.intent_server --no-ros2 --port 8123
```
It binds `0.0.0.0:8123`, so a Windows app can reach it at `localhost:8123` (WSL2
localhost-forwarding). If that ever fails, get the WSL IP with `wsl hostname -I`
and point the app there instead.

**Option 2 — Windows PowerShell** (from `<REPO>`):
```powershell
pip install pydantic fastapi "uvicorn[standard]" httpx py_trees pyyaml   # first time
$env:PYTHONPATH = "src"
python -m growmate_pi.intent_server --no-ros2 --port 8123
```

### A2. Voice app (Windows PowerShell, from `<REPO>`)

```powershell
$env:PYTHONPATH = "src;src\growmate_voice"
python -m growmate_voice.app --no-ros2 --pi-url http://localhost:8123/intent
```

> **PYTHONPATH matters.** The app needs **both** `src` **and** `src\growmate_voice`
> (the app is a nested package). `src` alone fails with
> `No module named growmate_voice`. The intent server needs only `src`.

Wait for `V2 mode: dispatching to Pi at …` / `Pi ready: …`, then open
**`http://localhost:7860`** and hard-refresh (Ctrl+Shift+R) — the UI is embedded,
so a cached page runs yesterday's JavaScript. Ollama must be up with `gemma3:4b`;
it is the classifier under test.

### A3. Phone microphone (optional, Cloudflare tunnel)

The mic button needs an HTTPS origin (`localhost` is the only exception), so a
phone on the LAN gets the UI but no microphone. Tunnel `localhost:7860`:

```bash
cloudflared tunnel --url http://localhost:7860 --protocol http2
```

Open the printed `https://<random>.trycloudflare.com` on the phone. **The URL
rotates on each launch.** Start the app first, then the tunnel. Install once:
`winget install --id Cloudflare.cloudflared`.

---

## B. Real robot — Pi + Windows app

### B1. Pi — update

```bash
ssh <PI_USER>@<PI_HOST>
cd <PI_REPO>
git status                 # expect a clean tree; deal with local edits first
git pull origin main
```

If `git pull` refuses because a tracked file has local changes, **look at the file
before discarding it** — map/config edits made on the Pi are sometimes the only
copy that exists. **Pure `growmate_pi` changes need no build** — a pull plus a
restart of the intent server is enough. Build only when AURA packages
(`farmbot_*`, `map_handler`, `camera_handler`) change.

### B2. Pi — build (only when AURA packages changed)

**Build OUTSIDE the venv, run the intent server INSIDE it.** The venv lacks ROS 2's
message-generation deps; building inside it fails with `No module named 'em'` and
silently leaves you running the upstream packages instead of yours.

```bash
deactivate 2>/dev/null                    # IMPORTANT — out of the venv to build
which python3                             # want /usr/bin/python3
source /opt/ros/$ROS_DISTRO/setup.bash
source <upstream-aura-ws>/install/setup.bash 2>/dev/null || true   # upstream FIRST
colcon build --symlink-install \
  --allow-overriding farmbot_command_handler farmbot_controllers \
                     farmbot_interfaces map_handler camera_handler farmbot_bringup
source install/setup.bash                 # ours LAST, so it wins
```

Confirm every package resolves to **your** workspace, not the upstream one:

```bash
for pkg in farmbot_bringup farmbot_controllers farmbot_command_handler \
           farmbot_interfaces map_handler camera_handler; do
  printf "%-30s -> %s\n" "$pkg" "$(ros2 pkg prefix $pkg 2>/dev/null || echo NOT-FOUND)"
done
```

Every line must end in `<PI_REPO>/install/...`. If any points at the standalone
upstream workspace, it is shadowing your edits — the robot will home fine and then
fail to water, because the upstream `map_handler` knows nothing about this garden.
`~/.bashrc` often auto-sources the upstream workspace; fix the order for the
current shell only rather than globally.

First time on a Pi, create the venv **after** a successful build:

```bash
python3 -m venv --system-site-packages venv   # --system-site-packages or no rclpy
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
touch venv/COLCON_IGNORE
deactivate
```

### B3. Pi — one-time state-dir migration

Mutable state (`active_map.yaml`, `watering_guide.yaml`) lives in one writable dir
outside the build tree — `<STATE_DIR>` (default `~/.farmbot`), shared by the AURA
stack and GrowMate. **Run once per Pi, before the first launch:**

```bash
STATE="${FARMBOT_STATE_DIR:-$HOME/.farmbot}"
mkdir -p "$STATE"
SHARE=$(ros2 pkg prefix map_handler)/share/map_handler/config
cp -n "$SHARE/active_map.yaml"     "$STATE/active_map.yaml"     2>/dev/null || \
  echo "no install-share active_map — will seed empty on first launch"
cp -n "$SHARE/watering_guide.yaml" "$STATE/watering_guide.yaml" 2>/dev/null || true
ls -l "$STATE"
```

To load a fresh garden instead, copy a seed map from `tools/maps/` into
`<STATE_DIR>/active_map.yaml`, then confirm the plant count in §B5.

### B4. Pi — launch

One command starts bringup, the intent server, then the daily scheduler:

```bash
cd <PI_REPO>
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
ros2 launch ./launch/greenhouse.launch.py config:=<GARDEN_CONFIG> scheduler:=false
```

| Launch arg | Default | Use |
|---|---|---|
| `config:=` | first greenhouse config | which greenhouse |
| `scheduler:=` | `true` | `false` while testing — otherwise it waters at 08:00 |
| `camera:=` | `true` | `false` if the camera is faulty and flooding the log |
| `port:=` | `8000` | intent server port |
| `state_dir:=` | `~/.farmbot` | mutable state location |

Wait for **both** `R99 ARDUINO STARTUP COMPLETE` and `Initialized with active
config from previous run`, with no `map_controller` traceback. On a robot with no
`activeConfig.yaml`, first bringup needs **C_0 calibration then CONF** to capture
real workspace dimensions — a preset alone is not enough.

### B5. Pi — confirm it is real, not sim

```bash
curl -s http://localhost:8000/status | python3 -m json.tool
```

- `"bridge_mode": "ros2"` — **`sim` means the intent server could not import
  `rclpy`** and is quietly pretending. Usually `PYTHONPATH` was replaced instead of
  **prepended** (`src:$PYTHONPATH`), or the venv lacks `--system-site-packages`.
- `"verify_enabled": true` — the tick-and-verify gate is on. It depends on
  `/busy_state`; check with `ros2 topic echo /busy_state` while homing. If it never
  appears, run with `--no-verify` rather than letting every move time out.
- Plant count matches the garden:

```bash
curl -s http://localhost:8000/plants \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('plants:', d.get('count'), '| source:', d.get('source'))"
```

A count of 0 means the map didn't load — fix that before touching the app, or `H_0`
will work and `P_4` will do nothing.

### B6. Windows — voice app pointed at the Pi

Ollama must be running with `gemma3:4b`. From `<REPO>`:

```powershell
$env:PYTHONPATH = "src;src\growmate_voice"
python -m growmate_voice.app --no-ros2 --pi-url http://<PI_HOST>:8000/intent
```

`--no-ros2` is correct: Windows never talks ROS, it POSTs to the Pi. Then open
`http://localhost:7860`, hard-refresh, and (for phone access) tunnel it with the
Cloudflare command from §A3.

---

## C. Development gates

Before committing a builder/node/schema change, from the repo root:

```bash
PYTHONPATH=src python3 -m growmate_pi.verify_sim     # expect Failures: 0/N
python tools/test_wire_grammar.py
python tools/test_verify_semantics.py
```

On Windows this runs under WSL:
```
wsl -- bash -lc "cd <repo> && PYTHONPATH=src ./venv/bin/python3 -m growmate_pi.verify_sim"
```

Evaluation (Ollama must be up; the classifier is the thing under test):

```powershell
$env:PYTHONPATH = "src;src\growmate_voice"
python tools\evaluate_v2.py --pi-url http://localhost:8123/intent --skip-long

# the full corpus takes hours — always stream, so a crash costs nothing
python tools\evaluate_v2.py --pi-url http://localhost:8123/intent `
  --corpus tools\corpus\growmate_test_corpus.json --stream run.jsonl --resume
```

`--no-llm` is a Pi-only smoke mode that deliberately sends `water_all`; never read
its DBSR as a result.

---

## D. When it misbehaves

| Symptom | Cause | Fix |
|---|---|---|
| `No module named growmate_voice` | app PYTHONPATH missing the nested package | `PYTHONPATH=src;src\growmate_voice` (both) |
| App can't reach the WSL server on `localhost` | WSL2 localhost-forwarding flaky | use the WSL IP: `wsl hostname -I`, then `--pi-url http://<wsl-ip>:8123/intent` |
| `H_0` moves, `P_4` does nothing | map empty, or upstream `map_handler` shadowing yours | check `/plants` count (§B5) and the `ros2 pkg prefix` loop (§B2) |
| `/status` says `"bridge_mode": "sim"` on the Pi | `rclpy` import failed | `PYTHONPATH=src:$PYTHONPATH` (**prepend**); venv needs `--system-site-packages` |
| `colcon build`: `No module named 'em'` | building inside the venv | `deactivate`, `sudo apt install python3-empy python3-lark python3-catkin-pkg`, rebuild |
| `map_controller` traceback at bringup | `active_map.yaml` wiped or malformed | re-seed from `tools/maps/` into `<STATE_DIR>`, relaunch |
| Verified moves all time out to `partial` | `/busy_state` never publishes | confirm with `ros2 topic echo /busy_state`; run with `--no-verify` until fixed |
| App says "Pi not reachable" | server down or port 8000 blocked | `curl http://<PI_HOST>:8000/plants` from the PC |
| UI shows old behaviour after a restart | cached embedded HTML/JS | hard-refresh (Ctrl+Shift+R) |
| Phone mic button does nothing | page is not on an HTTPS origin | use the Cloudflare `trycloudflare.com` URL (§A3), not the LAN IP |
| ROS nodes can't see each other over WiFi | multicast discovery eaten by the network | `export ROS_DOMAIN_ID=1` and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` |
| Camera opens but returns no frames | cable/port fault, `-71 EPROTO` in `dmesg` | try another USB port, then the cable; `camera:=false` meanwhile |
