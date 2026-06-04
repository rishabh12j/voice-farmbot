# GrowMate run guide

Single reference for bringing the whole stack up — Pi (bringup +
keyboard + intent server), Windows (voice app), and the V2 evaluation
harness. Covers everything built through Day 12 of the elderly-UX
sprint. Day 13 (demo-day script + recovery) lives in
[demo_day_run.md](demo_day_run.md) once written.

If you're new to the project, read this front-to-back once. If you
already know the layout, jump to the section you need:

1. [On the Pi — bring the FarmBot online](#1-on-the-pi--bring-the-farmbot-online)
2. [Launch the intent server (Pi)](#2-launch-the-intent-server-pi)
3. [Launch the voice app (Windows)](#3-launch-the-voice-app-windows)
4. [Test order (smallest risk first)](#4-test-order-smallest-risk-first)
5. [Verifying Day 5 / 9 / 10 / 11 UX features](#5-verifying-day-5--9--10--11-ux-features)
6. [Running the V2 evaluation (Day 12)](#6-running-the-v2-evaluation-day-12)
7. [Demo day procedure (Day 13)](#7-demo-day-procedure-day-13)
8. [Common gotchas](#8-common-gotchas)

---

## 1. On the Pi — bring the FarmBot online

Same workflow on a fresh Pi as we used on `gh1` — initialize git on
the existing folder and pull. Skip the clone, everything else is the
same.

### 1.1 Find the existing code and snapshot it

```bash
cd ~/Rishabh_Growmate_FarmBot   # or wherever it lives

# one-shot tarball backup before we touch anything
tar czf ~/backup-$(date +%Y%m%d-%H%M).tar.gz .
ls -lh ~/backup-*.tar.gz
```

### 1.2 Connect to git and pull

If it's already a git repo:

```bash
git status
git pull origin main
```

If `fatal: not a git repository`:

```bash
git init
git remote add origin https://github.com/rishabh12j/voice-farmbot.git
git fetch origin main
git checkout -f main          # -f overwrites local files with the repo version
```

If `git pull` says
`Your local changes would be overwritten by merge: tools/placements.csv`:

```bash
git checkout -- tools/placements.csv
git pull origin main
```

### 1.3 Rebuild and re-source

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
source venv/bin/activate 2>/dev/null || true   # if venv exists, use it
colcon build --symlink-install \
  --allow-overriding farmbot_command_handler farmbot_controllers farmbot_interfaces map_handler
source install/setup.bash
```

If the venv doesn't exist yet (first-time on this Pi):

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
touch venv/COLCON_IGNORE
# then colcon build again
```

### 1.4 Load the right map for this garden

```bash
# for the new greenhouse (35 plants)
cp ~/Rishabh_Growmate_FarmBot/tools/maps/new_greenhouse.yaml \
   "$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml"

# verify
python3 -c "
import yaml
d = yaml.safe_load(open('$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml'))
p = d['plant_details']['plants']
pc = d['plant_details']['plant_count']
print('plant_count:', pc, '| OK' if p and pc > 0 else '| EMPTY MAP — re-copy')
"
```

Must say `| OK`. If it says `EMPTY MAP` re-copy the YAML — every
`colcon build` may wipe the install-share dir.

For the 54-plant Maynooth garden use
`tools/maps/maynooth_54.yaml` instead.

### 1.5 If `activeConfig.yaml` is missing in install dir

```bash
ls "$(ros2 pkg prefix farmbot_controllers)/share/farmbot_controllers/config/activeConfig.yaml" 2>/dev/null \
  || echo "MISSING — copy from standalone or run C_0 + CONF after first bringup"
```

If missing, find a working copy and bring it in:

```bash
find ~ -name "activeConfig.yaml" 2>/dev/null
# then for example:
cp /home/farmbotdev/FarmBot_ROS2/install/farmbot_controllers/share/farmbot_controllers/config/activeConfig.yaml \
   "$(ros2 pkg prefix farmbot_controllers)/share/farmbot_controllers/config/activeConfig.yaml"
```

If no working copy exists, first-time bringup needs **C_0 calibration**
followed by **CONF** to capture real workspace dimensions — preset
alone isn't enough.

### 1.6 Launch and verify

```bash
ros2 launch farmbot_bringup standard.launch.py 2>&1 | tee /tmp/bringup.log
```

Wait for **both** lines:

- `R99 ARDUINO STARTUP COMPLETE`
- `Initialized with active config from previous run`

And **no** `map_controller` Traceback. If you see a traceback:

```bash
grep -E "map_controller|Traceback" /tmp/bringup.log
# fix the active_map.yaml (section 1.4) and relaunch
```

### 1.7 Keyboard controller smoke test

Second terminal:

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
source ~/Rishabh_Growmate_FarmBot/install/setup.bash
ros2 run farmbot_controllers keyboard_controller
```

At the prompt:

```
E            # reset e-stop
H_0          # basic motion first — gantry should home
P_4          # water all plants
```

If `H_0` moves but `P_4` doesn't, the map didn't load — re-copy the
YAML (section 1.4) and restart bringup.

One-liner to send a keyboard command from any terminal:

```bash
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'P_4'}"
```

---

## 2. Launch the intent server (Pi)

Third terminal on the Pi:

```bash
cd ~/Rishabh_Growmate_FarmBot
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python -m growmate_pi.intent_server --port 8000
```

Expected: `Bridge: connected, publishing to 'keyboard_topic'`.

Confirm from any terminal (the Pi itself, another Pi, or the laptop):

```bash
curl -s http://<pi-ip>:8000/plants \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('plants:', d.get('count'), '| source:', d.get('source'))"
```

Should print the expected plant count + which YAML it pulled from
(e.g. `plants: 35 | source: active_map.yaml`).

Other useful endpoints once it's up:

```bash
curl -s http://<pi-ip>:8000/plants/needs_attention | python3 -m json.tool
curl -s "http://<pi-ip>:8000/events?limit=10" | python3 -m json.tool
curl -s "http://<pi-ip>:8000/events?plant=tomato&event_type=watered&limit=5" | python3 -m json.tool
```

For sim mode (no real motor moves — for the V2 eval or local dev):

```bash
PYTHONPATH=src:$PYTHONPATH python -m growmate_pi.intent_server --no-ros2 --port 8000
```

---

## 3. Launch the voice app (Windows)

```cmd
cd C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice
set PYTHONPATH=C:\Users\risha\growmate-bt\voice-farmbot\src
python -m growmate_voice.app --pi-url http://192.168.0.39:8000/intent
```

Look for `V2 mode: dispatching to Pi at ...` and `Pi ready: ...` in
the console.

Open `http://localhost:7860` in the browser. Hard-refresh after every
restart (Ctrl+Shift+R) so the new HTML/JS picks up.

---

## 4. Test order (smallest risk first)

Run these in order — each one validates a more complex layer than the
last:

1. **Lights** quick-action → publishes `D_L_1`, then again → `D_L_0`
2. **Home** quick-action → gantry to (0, 0, 0)
3. **Step 100 mm** + jog forward → `M 0 100 0`, gantry moves
4. **Photo** quick-action → `I_1`
5. Mic → say *"go home"* (pattern path) → fast, no LLM
6. Mic → say *"water the basil"* (LLM path) → AICore resolves the
   plant via the Pi's loaded map → gantry to basil's coord → pump → done
7. **Stop** button → `e` immediately, gantry halts

If any step misbehaves, the pipeline log on screen (bottom of the
page) shows what route was chosen (`🤖`, `🧠`, `⚡`, `❓`) and what
phrase the Pi reported back. Note the new Pi's IP and which step
first misbehaves.

---

## 5. Verifying Day 5 / 9 / 10 / 11 UX features

Each sprint day added a UX layer — quick verification per day so you
know they survived a rebuild:

### Day 5 — soft-confirm gate (water everything / all)

- Mic or text → *"water everything"* → modal appears with a clear
  Yes/No (64 px tap targets), large fonts.
- **No / no response within 10 s** → cancelled, `Cancelled.` spoken.
- **Yes** → executes `water_all`.
- Emergency phrases (*stop*, *halt*, *freeze*, *emergency stop*) **never**
  pass through the gate — they fire immediately.

### Day 9 — care card (tap a plant on the map)

- Tap any plant on the map view → slide-up card with the plant's
  name, species, last-watered timestamp, "Water this plant" button,
  and a footer note that photo / soil-moisture aren't active yet.

### Day 10 — Today's care panel

- Home screen, above the chat feed, shows:
  - Moss-green *"All plants are watered. Nothing waiting on you."* — OR
  - Warm-clay *"N plants need watering ▾"* — tap to expand the list.
- Tap any list row → opens the Day 9 care card for that plant.
- Polls `/api/plants/needs_attention` every 5 minutes; also refreshes
  immediately after any successful action.

### Day 11 — voice plant queries (deterministic, skips LLM)

Try each in the text input or via mic — they should answer in well
under a second (no Ollama round-trip):

- *"When did I last water the tomatoes?"* → timestamp from event log
- *"How many days since I watered the marigolds?"* → numeric count
- *"Is the basil thirsty?"* → checks `needs_attention` list
- *"Does the lettuce need water?"* → same
- *"Which plant needs water most?"* → top of attention list
- *"What should I water today?"* → same as "most urgent"

If a query falls through unexpectedly, look at the pipeline log:
`⚡ Fast plant query: ...` on a hit, `🧠 AICore: ...` on fall-through
(usually Pi unreachable mid-query).

---

## 6. Running the V2 evaluation (Day 12)

Full 29-utterance corpus against the Pi. Metrics:
**DBSR / SNSR / USC / ELC / latency** — see
[eval_v2_results.md](eval_v2_results.md) for what each one means and
the V1 baseline to compare against.

### 6.1 Sim-mode run (no wet pumps)

Pi:

```bash
ssh gh1@<pi-ip>
cd ~/Rishabh_Growmate_FarmBot
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python3 -m growmate_pi.intent_server --no-ros2 --port 8000
```

Windows:

```powershell
$env:PYTHONPATH = "src"
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent
```

Expected output: table per case + summary line including ELC.
Exit code 0 if DBSR ≥ 80 %, else 1.

### 6.2 Capture per-case detail for the appendix

```powershell
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent --json > eval_results.json
```

Then paste the `summary` block into
[eval_v2_results.md](eval_v2_results.md) under a new `### Run N`
block, fill the Δ column vs V1, and flag any DBSR/ELC misses with a
one-line reason.

### 6.3 Pi-only smoke (no LLM)

If Ollama isn't running on Windows, you can still smoke-test the Pi
side — uses canned intents instead of real classification:

```powershell
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent --no-llm
```

DBSR will be lower (canned intents are placeholders), but ELC still
tells you the Pi event log is wired up.

---

## 7. Demo day procedure (Day 13)

To be written — `demo/demo_day_run.md`. Will contain:

- Pre-demo checklist (bringup running, intent server up, mic
  permission, Ollama warm)
- 10 scripted scenarios in increasing complexity
- Recovery steps for each likely failure mode (mic denied, Pi
  disconnected, Ollama down, water pump stuck, soft-confirm misfires)

Until that's written, fall back to
[demo_day_plan.md](demo_day_plan.md) for the focus-group run-of-show.

---

## 8. Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Bringup tracebacks in `map_controller` | `active_map.yaml` was wiped by `colcon build` (install-share dir is rebuilt) | Re-copy the YAML (section 1.4), restart bringup |
| `H_0` moves but `P_4` doesn't | Map controller cached an empty map at startup | Same as above |
| `fatal: not a git repository` after pull | Folder predates git on this Pi | `git init` + `remote add` + `fetch` + `checkout -f main` (section 1.2) |
| `ModuleNotFoundError: rclpy` inside the venv | Venv created without `--system-site-packages` | `python3 -m venv --system-site-packages venv` (recreate) |
| `colcon build` picks up venv files | Missing `COLCON_IGNORE` marker | `touch venv/COLCON_IGNORE`, rebuild |
| `PYTHONPATH=src` hides ROS 2 modules | `src` overrides ROS site-packages | Use `PYTHONPATH=src:$PYTHONPATH` (prepend, don't replace) |
| Intent server returns `Pi not reachable` to UI | Pi's intent_server isn't running OR firewall blocks port 8000 | Confirm with `curl http://<pi-ip>:8000/plants` from the laptop |
| Whisper picks `tomatoes` as `tomato is`, etc. | Whisper's default vocab doesn't bias to garden words | Already on by default since Day 4 — uses `small.en` + `initial_prompt`. Check the prompt with `tools/_smoke_whisper_prompt.py` |
| Soft-confirm modal never appears for "water all" | Phrase didn't pattern-match into `water_all` | Look at pipeline log — if route was `pattern` and action was `water_all`, gate **must** have fired. If route was `aicore`, the LLM took it — same gate fires from `_CONFIRM_AICORE_ACTIONS` |
| Today's-care panel stuck on old count | UI hasn't polled yet (5-min interval) | It refreshes immediately after any successful action — just send one command |
| Fast-path *"when did I water X"* falls through to LLM every time | Pi unreachable → fast path returns `None` so LLM gets a chance | Confirm `curl http://<pi-ip>:8000/events?limit=1` from Windows |

---

## Reference: where things live

- **Pi code:** `~/Rishabh_Growmate_FarmBot/src/growmate_pi/`
- **Pi config (per-garden):** `tools/maps/{maynooth_54.yaml,new_greenhouse.yaml}`
  → copied into `$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml`
- **Pi event log (SQLite, WAL):** `~/.growmate_pi/events.db`
- **Windows app:** `src/growmate_voice/growmate_voice/app.py`
- **Garden config (LLM-side):** `src/growmate_voice/config/farmbot.yaml`
- **V2 eval:** `tools/evaluate_v2.py`
- **V2 eval log:** `demo/eval_v2_results.md`
- **Sprint plan:** `PLANS.md` §4 (15-day elderly UX sprint)
- **Integration guide (for collaborators integrating their own code):**
  `demo/INTEGRATION_GUIDE.md`
