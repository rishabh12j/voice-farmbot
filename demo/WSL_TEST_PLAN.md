# WSL test plan (no FarmBot)

Commands to exercise the whole pipeline using **WSL as a virtual Pi**
(intent server in sim mode, no ROS 2 needed) plus the Windows voice
app. Verifies everything built through Day 12, including the four
hardware-run hotfixes from commit 9afcde4.

Sim mode publishes nothing to a real robot — instead the bridge prints
`-> [SIM] FarmBot: <command>  (<description>)` to the WSL console. So
the WSL terminal is your "robot did this" feedback.

---

## 0. One-time WSL prep (skip if already done)

```bash
# In WSL (Ubuntu)
cd /mnt/c/Users/risha/growmate-bt/voice-farmbot

# git pull to grab the hotfixes
git pull origin main

# venv if not present
python3 -m venv venv-wsl
source venv-wsl/bin/activate
pip install fastapi 'uvicorn[standard]' httpx pyyaml py_trees pydantic

# If pip says "py_trees already satisfied" but the intent server then
# crashes with `ModuleNotFoundError: No module named 'py_trees'`, pip
# was finding the ROS 2 copy at /opt/ros/humble/lib/... which isn't on
# the venv's import path. Force-install into the venv:
pip install --force-reinstall py_trees

# install a copy of the map at the location the intent server expects
mkdir -p ~/.growmate_pi
cp tools/maps/maynooth_54plants.yaml \
   src/growmate_pi/config/active_map.yaml 2>/dev/null || true
```

---

## 1. Boot order

**Terminal A — WSL (Pi sim):**

```bash
cd /mnt/c/Users/risha/growmate-bt/voice-farmbot
source venv-wsl/bin/activate
PYTHONPATH=src python3 -m growmate_pi.intent_server --no-ros2 --port 8000
```

You should see:

```
[growmate_pi] Bridge: simulation mode (commands printed)
Uvicorn running on http://0.0.0.0:8000
```

Get the WSL IP so the Windows app can reach it:

```bash
hostname -I | awk '{print $1}'   # note this IP, e.g. 172.x.x.x
```

(`localhost` may also work because WSL2 forwards Linux ports back to
Windows; if `localhost:8000` from Windows works, skip the IP step.)

**Terminal B — Windows (voice app):**

```cmd
cd C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice
set PYTHONPATH=C:\Users\risha\growmate-bt\voice-farmbot\src
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.137.161:8000/intent
```

> **`--no-ros2` is required on Windows.** The Pi side (WSL) does all
> the ROS publishing; the Windows app only proxies HTTP. Without
> `--no-ros2` the app hangs at `Initialising — ros2=True` while
> trying to find or init a local rclpy that isn't there.

Open `http://localhost:7860` in the browser. **Hard-refresh** (Ctrl+Shift+R).

---

## 2. What to look at while testing

Three windows tell you what happened:

1. **Browser**: pipeline log strip at the bottom of the page shows the
   route taken — `🔍 Pattern` / `🧠 AICore` / `❓ Awaiting confirm` /
   `⚡ Fast plant query` (parked).
2. **WSL terminal**: every command that "would have been published" to
   the FarmBot prints there as `-> [SIM] FarmBot: <code>`.
3. **Windows terminal**: HTTP logs (`POST /api/voice`, `POST /api/text`,
   `POST /api/confirm`, `POST /api/reset`) confirm the endpoints are
   being hit.

If the WSL terminal doesn't print the expected sim line, the bridge
didn't receive your intent — the bug is upstream of the bridge.

---

## 3. Pattern-path commands (fast, no LLM)

These match `command_map.py` exactly → route is `🔍 Pattern`. Use the
text input or mic.

| Phrase | Route | Sim output | TTS reply |
|---|---|---|---|
| `stop` | Pattern → `estop` | `-> [SIM] FarmBot: e` | "Stopped. The robot is halted." |
| `reset` | Pattern → `reset` | `-> [SIM] FarmBot: E` ×3 | "All clear. Ready to go again." |
| `go home` | Pattern → `home` | `-> [SIM] FarmBot: H_0` | "Heading home." |
| `forward` | Pattern → `y_plus` | `-> [SIM] FarmBot: M ...` | "Moving forward." |
| `move left` | Pattern → `x_minus` | `-> [SIM] FarmBot: M ...` | "Moving left." |
| `lift` | Pattern → `z_plus` | `-> [SIM] FarmBot: M ...` | "Lifting the arm up." |
| `take photo` | Pattern → `photo` | `-> [SIM] FarmBot: I_1` | "Taking a photo for you." |

**What this proves:** matcher works, bridge wired, TTS pipeline alive.

### Reset hotfix verification

Type `reset`. In the WSL terminal you should see **three** lines:

```
-> [SIM] FarmBot: E  (Reset emergency stop)
-> [SIM] FarmBot: E  (Reset emergency stop)
-> [SIM] FarmBot: E  (Reset emergency stop)
[growmate_pi] /reset_estop -> published 'E' x3 (statuses: ['simulated', 'simulated', 'simulated'])
```

Type `stop`. Should print **two** `[SIM] FarmBot: e` lines and a
`/estop -> published 'e' x2` summary.

---

## 3a. First WSL test session findings — fixed in Tier B

The first end-to-end WSL test surfaced four issues. All four are
fixed as of the Tier B commit. Kept here as the post-mortem record.

<details>
<summary>Original session log (click to expand)</summary>

```

(venv-wsl) ee650@LAPTOP-1SCNMHIJ:/mnt/c/Users/risha/growmate-bt/voice-farmbot$ PYTHONPATH=src python3 -m growmate_pi.intent_server --no-ros2 --port 8000
[growmate_pi] Bridge: simulation mode (commands printed)
INFO:     Started server process [787]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     192.168.128.1:50832 - "GET /status HTTP/1.1" 200 OK
INFO:     192.168.128.1:49362 - "GET /intent HTTP/1.1" 405 Method Not Allowed
INFO:     192.168.128.1:49362 - "GET /favicon.ico HTTP/1.1" 404 Not Found
INFO:     192.168.128.1:58767 - "GET /status HTTP/1.1" 200 OK
INFO:     192.168.128.1:59885 - "GET /plants HTTP/1.1" 200 OK
  -> [SIM] FarmBot: e  (EMERGENCY STOP)
  -> [SIM] FarmBot: e  (EMERGENCY STOP)
[growmate_pi] /estop -> published 'e' x2 (status: simulated)
INFO:     192.168.128.1:64586 - "POST /estop HTTP/1.1" 200 OK
  -> [SIM] FarmBot: E  (Reset emergency stop)
  -> [SIM] FarmBot: E  (Reset emergency stop)
  -> [SIM] FarmBot: E  (Reset emergency stop)
[growmate_pi] /reset_estop -> published 'E' x3 (statuses: ['simulated', 'simulated', 'simulated'])
INFO:     192.168.128.1:63163 - "POST /reset_estop HTTP/1.1" 200 OK
  -> [SIM] FarmBot: H_0  (Go to home position)
INFO:     192.168.128.1:52424 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: D_L_1  (LED strip ON)
INFO:     192.168.128.1:51108 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: D_L_0  (LED strip OFF)
INFO:     192.168.128.1:51109 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: e  (EMERGENCY STOP)
  -> [SIM] FarmBot: e  (EMERGENCY STOP)
[growmate_pi] /estop -> published 'e' x2 (status: simulated)
INFO:     192.168.128.1:63402 - "POST /estop HTTP/1.1" 200 OK
  -> [SIM] FarmBot: E  (Reset emergency stop)
  -> [SIM] FarmBot: E  (Reset emergency stop)
  -> [SIM] FarmBot: E  (Reset emergency stop)
[growmate_pi] /reset_estop -> published 'E' x3 (statuses: ['simulated', 'simulated', 'simulated'])
INFO:     192.168.128.1:63393 - "POST /reset_estop HTTP/1.1" 200 OK
  -> [SIM] FarmBot: H_0  (Go to home position)
INFO:     192.168.128.1:49962 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.128.1:54133 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: M 4250.0 1150.0 0.0  (Move to (4250.0, 1150.0, 0.0))
  -> [SIM] FarmBot: D_W_1  (Water pump ON)
  -> [SIM] FarmBot: D_W_0  (Water pump OFF)
INFO:     192.168.128.1:62289 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: M 1850.0 1100.0 0.0  (Move to (1850.0, 1100.0, 0.0))
  -> [SIM] FarmBot: D_S_C  (Read soil sensor)
INFO:     192.168.128.1:58800 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: M 1850.0 1100.0 0.0  (Move to (1850.0, 1100.0, 0.0))
  -> [SIM] FarmBot: D_W_1  (Water pump ON)
  -> [SIM] FarmBot: D_W_0  (Water pump OFF)
INFO:     192.168.128.1:54459 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: M 3500.0 2400.0 0.0  (Move to (3500.0, 2400.0, 0.0))
  -> [SIM] FarmBot: D_W_1  (Water pump ON)
  -> [SIM] FarmBot: D_W_0  (Water pump OFF)
INFO:     192.168.128.1:63528 - "POST /intent HTTP/1.1" 200 OK
  -> [SIM] FarmBot: P_4  (Water all plants)
INFO:     192.168.128.1:63003 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.128.1:52383 - "GET /plants/needs_attention?limit=20 HTTP/1.1" 200 OK
INFO:     192.168.128.1:60849 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.128.1:52318 - "GET /plants HTTP/1.1" 200 OK
INFO:     192.168.128.1:52321 - "POST /intent HTTP/1.1" 200 OK


Recent activity

What are the requirements for sunflowers? Sunflowers require a minimum of six to eight hours of direct sunlight daily to thrive. Given the forecast for Maynooth, with anticipated rainfall beginning on June 4th and continuing through June 7th, you should select a sheltered location for your sunflowers, perhaps near a building or fence, to minimize the impact of the light rain. Plant the seeds approximately 60 to 90 centimeters apart, and ensure the soil is well-drained, amending it with compost to provide sufficient nutrients for healthy growth. The current temperature of 13.9 degrees Celsius is suitable for germination, but monitor the soil moisture carefully as the rainfall increases.
51 s ago
correct

what's the weather looking like today? Currently in Maynooth, the temperature is 13.9 degrees Celsius with 76 percent humidity and overcast conditions. A wind speed of 25.6 kilometers per hour is present. Over the next four days, light to moderate rain is forecast, beginning with 2.9 millimeters on June 4th and increasing to 7.8 millimeters on June 7th.
2 min ago
correct

can we please water everything? Watering all plants.
2 min ago
correct asked me for yes no

Cancelled (cancelled: can we please water everything on the garden?)
3 min ago
I clicked no

water the begonias Watering the begonias!
4 min ago
there are no begonias still it watered did not check map for location context

water just the lettuces Watering the lettuce!
5 min ago
why did it water just the first one and not all

what are just the lettuces? Let me check on the lettuce.
5 min ago
I said water the lettuces it heard wrong

water just the tomatoes Watering just the tomatoes!
6 min ago
why did it water just the first one and not all


please water just the spearmint Watering the herbs now.
6 min ago
why did it water just the first one and not all

go alone Heading home.
```

</details>

**Findings and resolutions:**

| # | Symptom from this session | Root cause | Fix (Tier B) |
|---|---|---|---|
| 1 | "water the begonias" → "Watering the begonias!" + a M-D_W_1-D_W_0 cycle even though there are no begonias | `farmbot.yaml` had a placeholder begonia entry the BT resolved blindly | Multi-plant tree now queries the **active map** (real garden); no match → refuse cleanly with "I don't see any begonias in this garden." |
| 2 | "water just the lettuces" → watered only 1 of 8 | Single representative coord per species in `farmbot.yaml` | Active-map query returns all N matching plants; tree sequences through each |
| 3 | "please water just the spearmint" → "Watering the herbs now." | LLM abstracted species to "herbs" in its response | Fallback summary now uses the actual count + target ("Watered 3 spearmint") |
| 4 | "go alone" matched "go home" | Whisper STT + lenient fuzzy match | Acknowledged as STT artifact; not a code change |

---

## 4. LLM-path commands (route via AICore)

These don't match a bare-water variant exactly, so they fall through to
the LLM. Route is `🧠 AICore`.

| Phrase | What it tests | Sim output |
|---|---|---|
| `water the tomatoes` | targeted single-plant water (the basil hotfix) | `M <x> <y> 0` then `D_W_1` / `D_W_0` (or `P_4` if classifier picks water_all) |


| `water the marigolds please` | "please" wrapper + plural | same as above for marigolds |
| `take a photo of the lettuce` | targeted photo | `M <x> <y> 0` then `I_1` |
| `the herbs look thirsty` | indirect water | move to herbs + water |
| `give the basil a drink` | informal water of a plant **not** in the 54-plant map | LLM either skips or asks for clarification — TTS reply: "I don't see basil…" |
| `how often should I water tomatoes?` | general_question | no sim line (no robot action); TTS reply contains advice |
| `what's the weather like today?` | weather context kicks in | TTS reply mentions live weather (Open-Meteo) |

**Critical hotfix check for "water the basil":** The pipeline log
should read `🧠 AICore: ...`, **not** `🔍 Pattern: water`. If it shows
Pattern, the matcher fix didn't deploy — re-pull on WSL and re-run.

---

## 5. Soft-confirm gate (the polite-imperative hotfix)

These should all pop the Yes/No modal with the question "Should I
water all the plants?" before anything moves.

| Phrase | Should defer? | Why |
|---|---|---|
| `water everything` | YES | bare destructive |
| `water all the plants` | YES | bare destructive |
| `can you help me water all the plants?` | **YES** (hotfix) | polite imperative, NOT a knowledge query |
| `could you water everything please` | YES (hotfix) | polite imperative |
| `please water all the plants` | YES (hotfix) | polite imperative |
| `how often should I water all the plants?` | NO | knowledge question — answers from LLM |
| `when did I last water everything?` | NO | knowledge question |
| `water the tomatoes` | NO | targeted, no "everything" / "all" keyword |

**For each YES row:**
1. Modal appears with the question.
2. Sim terminal sees **nothing yet** — gate intercepted the action.
3. Click **No** → modal closes, TTS says "Cancelled."
4. Repeat the same phrase, click **Yes** → sim terminal prints
   `-> [SIM] FarmBot: P_4` (water_all).

**For each NO row:** No modal, action (or answer) flows immediately.

---

## 5a. Tier B — multi-plant watering + blocking overlay

This is the architectural fix from the second WSL session. Before
Tier B, *"water the lettuces"* watered ONE plant and lied about it,
and *"water the begonias"* (not in the garden) silently watered a
placeholder coordinate. Now:

- The Pi resolves the target against the **active map** (the loaded
  garden), not `farmbot.yaml`.
- N matches → builds an N-step BT (CheckEstop → Move → PumpOn →
  Wait → PumpOff → LogPlantEvent per plant), row-sorted by Y then X.
- N = 0 → tiny refuse tree with TTS *"I don't see any X in this
  garden."*
- N ≥ 5 → soft-confirm gate fires before anything moves.
- During execution, the browser polls `/api/pi_status` at 1 Hz and
  drops a **blocking overlay** with a progress bar + single big
  EMERGENCY STOP button.
- Estop now flips a blackboard flag the `Wait` node checks every
  ~100 ms → tree halts within a tick, not 30 seconds.

### 5a.1 No-match refuse (no robot motion)

Type or say each:

- *"water the begonias"*
- *"water the tomatos"* (typo)
- *"water the dragons"*

Expected:

- WSL terminal: **no** `[SIM] FarmBot:` lines (nothing published)
- TTS reply: *"I don't see any begonias in this garden. Tell me a
  different plant."*
- Browser: no overlay shown

Proves Bug B (begonia placeholder) is fixed.

### 5a.2 Multi-plant under threshold (1 ≤ N < 5, no confirm)

- *"water the spearmint"* — 3 spearmints in Maynooth-54 → N = 3

Expected during execution:

- WSL terminal prints one cycle per plant: `M <x> <y> 0` then `D_W_1`,
  then `D_W_0`
- Browser: full-screen moss-green overlay showing
  **"Watering 3 spearmint"**, progress bar fills 33% → 66% → 100%,
  current label updates per plant
- After 3rd plant: overlay slides away, TTS says *"Watered 3
  spearmint."*

### 5a.3 Multi-plant OVER threshold (N ≥ 5, confirm fires)

- *"water all the scallions"* — 18 scallions in Maynooth-54

Expected:

1. Modal: *"I heard you say: water all the scallions. Should I water
   all 18 scallion?"* Yes/No.
2. Click **No** → "Cancelled.", no motion.
3. Repeat the phrase, click **Yes** → overlay appears, gantry walks all 18.
4. During execution, overlay shows "Plant N of 18" and the current
   plant label, refreshing each second.

### 5a.4 EMERGENCY STOP mid-task (the headline test)

1. *"water all the scallions"* → click Yes on the confirm
2. After ~3 plants, click the BIG EMERGENCY STOP button in the overlay
3. **Within one second:**
   - WSL prints `-> [SIM] FarmBot: e x2` and the `/estop -> published
     'e' x2` summary
   - The Wait node ticking for the current plant halts immediately
   - Overlay slides away
4. Click **Reset system** in the footer → WSL prints
   `[SIM] FarmBot: E x3` and `/reset_estop -> published 'E' x3`
5. Try a fresh command (*"go home"*) → it should now work normally

What's being tested: the Tier B tick-aware Wait + task_state estop
flag interrupting a long-running BT mid-sequence, plus the original
Stop/Reset hotfixes still working. The event log will have rows
ONLY for the plants that actually finished (e.g. 3 rows, not 18).

### 5a.5 Per-leaf event log honesty (curl)

After a partial stop:

```bash
curl -s "http://localhost:8000/events?event_type=watered&limit=20" | python3 -m json.tool
```

You should see one row per plant that **completed**, each with its
real `plant_index` populated — NOT a single summary row for the whole
batch. This is the "honest log" payoff that justified parking Day 10/11.

---

## 6. Memory features — verify they're parked

Both today's-care panel and Day 11 fast-path are off behind
`_MEMORY_FEATURES_ENABLED = False`. Quick sanity:

1. **Browser home screen**: there should be **no** "N plants need
   watering" panel above the chat feed. (It's hidden via `display:
   none`.)
2. **Pipeline log**: try `when did I last water the tomatoes?` — log
   should say `🧠 AICore: ...` (LLM answer, possibly hedged), **not**
   `⚡ Fast plant query: ...`.
3. **DevTools Network tab**: filter on `needs_attention`. You should
   see this fire only when the care card opens, **not** on a 5-minute
   polling interval.

To temporarily re-arm during this session (NOT recommended for the
demo, but useful for local debug):

- Edit `src/growmate_voice/growmate_voice/app.py` → set
  `_MEMORY_FEATURES_ENABLED = True`.
- In the same file's inline JS, find `const MEMORY_FEATURES_ENABLED =
  false;` and flip to `true`.
- Restart the Windows app, hard-refresh.

---

## 7. Whisper bias check (mic only)

The Whisper STT is biased with your garden's plant names so it
transcribes "marigold" correctly instead of "mary gold" etc.

1. Mic → say *"water the marigolds"* slowly and clearly.
2. The pipeline log shows the transcript. It should read **"water the
   marigolds"** (or close), not "mary golds" / "mary cold".
3. The Windows console should log `Whisper prompt biased with N chars`
   on first STT call.

If the transcript is wrong every time, the prompt file lookup may have
broken — check `tools/_smoke_whisper_prompt.py` outputs.

---

## 8. Direct curl checks (no browser)

Useful for confirming the Pi endpoints work in isolation. Run from
either WSL or Windows (substitute `localhost` with the WSL IP if you
have to).

```bash
# Plants in the loaded map
curl -s http://localhost:8000/plants | python3 -m json.tool | head -30

# /events — should be a small list (just sim runs from this session)
curl -s "http://localhost:8000/events?limit=10" | python3 -m json.tool

# Manual estop / reset
curl -s -X POST http://localhost:8000/estop
curl -s -X POST http://localhost:8000/reset_estop

# Manual intent — water all the plants
curl -s -X POST http://localhost:8000/intent \
  -H "Content-Type: application/json" \
  -d '{
    "intents": [{"action": "water_all", "response": "Watering all."}],
    "raw_text": "water everything",
    "emergency": false,
    "client_id": "manual_curl",
    "timestamp": "2026-06-04T12:00:00Z",
    "schema_version": "1.0.0"
  }' | python3 -m json.tool
```

Each `/intent` POST should print one or more `-> [SIM] FarmBot: ...`
lines in the WSL terminal AND echo the `commands_published` array in
the JSON response.

---

## 9. V2 evaluation harness (Day 12) — full sweep

Runs all 29 utterances through the LLM and the sim Pi. Takes ~3-5 min
depending on Ollama warm time.

```cmd
:: Windows side
cd C:\Users\risha\growmate-bt\voice-farmbot
set PYTHONPATH=src
python tools\evaluate_v2.py --pi-url http://localhost:8000/intent
```

Expected summary line at the bottom:

```
Summary: {'n_cases': 29, 'DBSR': 8?.?, 'SNSR': 9?.?, 'USC': 0,
          'ELC': 9?.?, 'ELC_n_applicable': 14, 'latency_ms_mean': ~150,
          'latency_ms_max': ~500}
```

If `DBSR < 80` the script exits 1. Paste the summary block into a new
`### Run N` section of [eval_v2_results.md](eval_v2_results.md).

For a Pi-only smoke (no Ollama needed — uses canned intents):

```cmd
python tools\evaluate_v2.py --pi-url http://localhost:8000/intent --no-llm
```

---

## 10. Quick-fail cheatsheet

| Symptom | Likely cause | Quick check |
|---|---|---|
| Browser hangs on first `/api/voice` | Kokoro TTS first-load is slow | wait 30 s; subsequent calls are fast |
| All voice → pipeline says `🧠 AICore: (LLM unavailable …)` | Ollama not running on Windows | `ollama list` then `ollama serve` |
| All voice → `🤖 (Pi error: …)` | WSL intent server isn't reachable | curl the WSL IP `/plants` from Windows |
| `water the basil` shows `🔍 Pattern: water` | Hotfix didn't deploy | `git log -1` in WSL — should be on commit 9afcde4 or later |
| No modal on `can you help me water everything?` | Same as above | check `app.py` for `polite_imperatives` |
| Today's-care panel visible | JS cache | hard-refresh; check `MEMORY_FEATURES_ENABLED = false` in the served HTML via DevTools |
| WSL sim never prints `[SIM] FarmBot:` for any command | bridge didn't init in sim mode | restart with `--no-ros2` explicitly |
| `ModuleNotFoundError: fastapi` in WSL | venv-wsl missing deps | `pip install fastapi 'uvicorn[standard]' httpx pyyaml py_trees pydantic` |
| `ModuleNotFoundError: py_trees` even after pip says "already satisfied" | pip found ROS 2's copy at `/opt/ros/humble/...` but the venv can't import from there | `pip install --force-reinstall py_trees` |
| Windows app hangs at `Initialising — ros2=True`, never reaches "GrowMate at http://…" | Default tries to init a local rclpy; on Windows that hangs (no ROS) | Restart with `--no-ros2`. The Pi handles all ROS publishing; Windows is HTTP-only. |
| Multi-plant overlay never appears even though sim is watering N>1 plants | `/api/pi_status` not returning task_active=true; either Pi not reachable or browser hasn't picked up the new HTML | hard-refresh browser; `curl http://localhost:8000/status` should show `"task_active": true` mid-sequence |
| EMERGENCY STOP in overlay doesn't halt the WSL sim | Wait node not seeing the flag — old code | confirm git pull included the Tier B commit; smoke: `python tools/_smoke_tier_b.py` (60 checks) |
| "Water all the scallions" doesn't ask for confirmation | Pi `/plants/by_species/scallion` returned < 5 OR the LLM didn't classify as `water` with target=scallion | curl `http://localhost:8000/plants/by_species/scallion` and verify count >= 5; check the pipeline log to see what action+target the LLM emitted |

---

## What this plan does NOT cover (needs a real FarmBot)

- The actual e-stop / reset round-trip through `panel_controller` and
  the firmware F09. Sim mode only verifies the bridge published the
  right strings the right number of times.
- Real gantry motion against bounds (USC metric in the V2 eval).
- Camera capture latency, pump priming time, soil sensor readings.
- The Day 9 care-card "Water this plant" button — it still hits
  `/intent` in sim mode, you'll see the `M …` + `D_W_1` lines in WSL,
  but no actual water comes out.
