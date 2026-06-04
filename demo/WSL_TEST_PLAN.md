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
python -m growmate_voice.app --pi-url http://localhost:8000/intent
```

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
