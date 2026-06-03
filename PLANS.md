# GrowMate — Plans archive

Consolidated planning notes from V2 design through the current 15-day
elderly-UX sprint. Sections in order of when they were authored:

1. [V2 architecture](#1-v2-architecture) — phone-side intent + Pi-side BT split
2. [V2 migration plan](#2-v2-migration-plan) — file-by-file change inventory
3. [Build / test status](#3-build--test-status) — running checklist
4. [15-day elderly UX sprint](#4-15-day-elderly-ux-sprint) — current work plan

Most decisions in §1 and §2 are now implemented; they live here as a
record of the choices that got us to V2. The current source of truth for
day-to-day work is §4.

---

## 1. V2 architecture

### What we changed

Before (V1): everything ran on the Windows machine.

```
Browser mic → WAV → FastAPI (Windows)
  → STT (Whisper)
  → LLM (Ollama, gemma3:4b) → intent JSON
  → AICore._intent_to_tree() → BT dict (pure Python)
  → BTEngine.execute() → command strings
  → ROS2Publisher → keyboard_topic → FarmBot Pi
```

After (V2): client and Pi split.

```
Phone / Browser
  → mic → STT
  → LLM call (Ollama on Windows, over LAN) → intent JSON
  → HTTP POST intent JSON to Pi

FarmBot Pi
  → receives intent JSON
  → py_trees BT construction
  → BT tick / execute
  → keyboard_topic → FarmBot hardware
```

### Key decisions

| # | Decision | Outcome |
|---|----------|---------|
| 1 | Where does STT run? | Windows (Whisper). Phone WAV → POST to Windows. |
| 2 | Where does LLM run? | Windows Ollama. Pi stays LLM-free. |
| 3 | What does the Pi receive? | Intent JSON: `{"action": "water", "target": "tomatoes", ...}` |
| 4 | BT library on Pi | py_trees (EE650 pattern). PlanSys2 + PDDL kept as optional layer for multi-step planning. |
| 5 | HTTPS for phone mic | Needed for Safari/Chrome on LAN. Ngrok / caddy when we get there. |

### Confirmed decisions (later refinements)

| Question | Answer |
|----------|--------|
| Who calls the Pi? | Phone calls Pi directly — no Windows in the request path |
| py_trees on Pi? | Not in apt for Humble — `pip install py_trees` |
| PlanSys2 / PDDL? | Keep — useful for multi-step planning layer later |
| Where does the LLM ultimately run? | Option B — Pi is single endpoint, calls Windows Ollama over LAN |

### Wire format (frozen at V2)

```json
{
  "intents": [
    {
      "action": "water",
      "target": "tomatoes",
      "params": {"duration_s": 6},
      "response": "Watering the tomatoes."
    }
  ],
  "raw_text": "water the tomatoes please",
  "emergency": false,
  "client_id": "phone-abc123",
  "timestamp": "2026-05-18T14:32:00Z",
  "schema_version": "1.0.0"
}
```

Source of truth: `src/growmate_pi/schemas.py`.

---

## 2. V2 migration plan

### Files that landed during V2

| Path | What it does |
|---|---|
| `src/growmate_pi/schemas.py` | Wire format (Pydantic) |
| `src/growmate_pi/farmbot_ros2_bridge.py` | ROS2 publisher, sim + real modes |
| `src/growmate_pi/garden_config.py` | Plant/location resolver, workspace bounds |
| `src/growmate_pi/bt/condition_nodes.py` | py_trees safety conditions |
| `src/growmate_pi/bt/action_nodes.py` | py_trees actions (water, move, photo, LED, …) |
| `src/growmate_pi/bt/builder.py` | Intent JSON → py_trees tree |
| `src/growmate_pi/bt/executor.py` | Tick loop, blackboard, result aggregation |
| `src/growmate_pi/intent_server.py` | FastAPI: `/intent`, `/estop`, `/plants`, `/status` |
| `src/growmate_pi/pi_client.py` | Thin httpx client used by app + eval |
| `src/growmate_pi/scheduler.py` | Daily watering daemon (HTTP, not direct ROS2) |
| `src/growmate_pi/mission/plansys2_controller.py` | Stubbed — for future multi-step planning |
| `src/growmate_pi/pddl/farmbot_domain.pddl` | PDDL domain for the planner |

### Files retired or repurposed

| Path | Status |
|---|---|
| `bt_engine.py` (Windows) | Retired — Pi runs py_trees now |
| `bt_bridge.py` (Windows workbench) | Retired |
| `voice_controller_node.py` (Pi headless) | Replaced by `intent_server.py` |
| `ros2_publisher.py` | Moved to Pi as `farmbot_ros2_bridge.py` |

### Files kept on Windows

| Path | Why |
|---|---|
| `ai_core.py` | LLM intent classifier — still produces intent JSON |
| `edgespeech/` | STT/TTS backends |
| `app.py` | FastAPI web app; rewritten to dispatch via `--pi-url` |
| `cli.py`, `stt_test.py` | Dev tools |
| `history.py`, `logger.py` | Both sides have their own copies |

### Risks called out at the time

- Emergency stop latency over LAN. Hardware e-stop must stay available.
- CORS for Ollama from browser if we ever go phone-only.
- py_trees on Pi needs version pinning against Humble.
- PlanSys2 install footprint (~300 MB) — defer until we actually use it.
- 29-utterance eval re-baseline because HTTP roundtrips add ~hundreds of ms.

---

## 3. Build / test status

Simple checklist. Mark things off as we go.

Legend: `[x]` done · `[ ]` remaining · `[~]` partial / needs review · `[?]` untested (not broken, just not verified yet)

### Build

| Status | Item |
|--------|------|
| `[x]` | FastAPI web app — jog panel + voice pipeline (port 7860) |
| `[x]` | STT backends — Whisper, Vosk, Moonshine (`edgespeech/stt/`) |
| `[x]` | TTS backends — Piper, Kokoro (`edgespeech/tts/`) |
| `[x]` | LLM intent classifier (`ai_core.py`, gemma3:4b via Ollama) |
| `[x]` | Behaviour tree engine (`bt_engine.py`) |
| `[x]` | Tree builders — water plant, water all, move, sensor, photo, LED, query |
| `[x]` | Safety nodes — `check_available`, `check_bounds`, `check_plant_found` |
| `[x]` | Emergency stop — string-matched, bypasses LLM, `/estop` endpoint |
| `[x]` | ROS2 publisher — `keyboard_topic`, sim mode on Windows |
| `[x]` | Daily watering scheduler — `P_4` once/day (`scheduler.py`) |
| `[x]` | STT/TTS/BT workbench — no-robot experimentation UI (port 7870) |
| `[x]` | Pattern-based command shortcuts (`edgespeech/command_map.py`) |
| `[x]` | Persistent command history (`history.py`) |
| `[x]` | Structured logging (`logger.py`, `~/.growmate_voice/`) |
| `[x]` | 29-utterance evaluation corpus (`growmate-bt/evaluate_bt.py`) |
| `[ ]` | Port `evaluate_bt.py` into `src/growmate_voice/` (update imports) |
| `[ ]` | HTTPS setup for phone mic over LAN (nginx / caddy / ngrok) |
| `[~]` | Confirmation flow — re-run safety nodes on confirm (currently skipped) |
| `[ ]` | Session memory — handle corrections like "no, the herbs" |
| `[ ]` | WiFi heartbeat / deadman switch |
| `[ ]` | `tests/` directory |

### Tested — Windows (simulation, no real robot)

| Status | Item |
|--------|------|
| `[x]` | Web app starts cleanly (`--no-ros2 --model gemma3:4b`) |
| `[x]` | STT transcription — Whisper backend |
| `[x]` | LLM classification — gemma3:4b via Ollama |
| `[x]` | BT construction + execution in sim mode |
| `[x]` | Emergency stop path (string match → direct publish) |
| `[x]` | Jog controls (sim prints) |
| `[x]` | Scheduler fires `P_4` in sim mode |
| `[ ]` | STT backends — Vosk, Moonshine |
| `[ ]` | TTS backends — Kokoro |
| `[ ]` | Workbench (`stt_test.py`) full walkthrough |
| `[ ]` | All 29 eval utterances through the ROS2 package pipeline |

### Tested — Actual hardware (Pi + FarmBot)

| Status | Item |
|--------|------|
| `[x]` | ROS2 package deploys on Pi |
| `[x]` | `keyboard_topic` publish — FarmBot responds |
| `[x]` | Water all (`P_4`) — physical watering runs |
| `[x]` | Water by moisture (`P_5`) |
| `[x]` | Move gantry (`M x y z`) — physical movement |
| `[x]` | Home position (`H_0`) |
| `[x]` | Emergency stop (`e`) — physical stop confirmed |
| `[x]` | Reset estop (`E`) |
| `[x]` | LED on/off (`D_L_1` / `D_L_0`) — V2 BT pipeline confirmed on real hardware |
| `[x]` | Water pump direct (`D_W_1` / `D_W_0`) |
| `[?]` | Soil sensor read (`D_S_C`) |
| `[?]` | Photo / panorama / weed scan (`I_1` / `I_2` / `I_4`) |
| `[x]` | Scheduler on Pi — once-per-day watering at 08:00 |
| `[ ]` | End-to-end: voice → STT → LLM → BT → FarmBot moves |
| `[ ]` | Phone mic over LAN (needs HTTPS first) |

---

## 4. 15-day elderly UX sprint

Goal: by day 15, demo-ready system that handles elderly speech reliably,
answers gardening questions with real context, and tracks per-plant care
history so users with weak memory always know what's been done and what's
due.

Working day = 1 calendar day. Buffer is baked into days 14–15.

### Phase 1 — LLM context layer (Days 1–3)

**Day 1 — Debug + fix general_question response flow** ✅ done

- Bug: `_dispatch_via_aicore` put the classifier's filler ("Let me look that up") into `response`, never calling `reason()` for the actual answer.
- Fix: when an intent is `general_question`, call `AICore.reason()` with `(location + plant)` context, replace `response` with the real answer.
- Tightened the `reason()` system prompt: no emojis, no greetings, no follow-up questions, 3–5 sentence comprehensive answers, max_tokens 350.

**Day 2 — Open-Meteo weather context** ✅ done

- New `weather.py`: free API, no key, 30 min cache, urllib only, WMO code → human label, graceful stale-cache fallback on network failure.
- `GardenConfig` exposes latitude/longitude from `farmbot.yaml`.
- `AICore.reason()` auto-fetches weather when context lacks it and coords are available.
- System prompt now forbids inventing temperatures or rainfall — must use the numbers provided.

**Day 3 — Per-plant context in the prompt** ✅ done

- New helper `_summarise_plants_for_target()` in `app.py`. Uses `api_plants()`'s Pi-first / local-fallback chain.
- Filters live plant list by target (with plural handling: tomatoes → tomato, lilies → lily, begonias → begonia).
- Returns aggregated `count, growth_stage, water_seconds_per_plant, position_x_mm range, position_y_mm range, sample_names`.
- Falls back to `GardenConfig.find()` (single-plant entry from config) when the plant isn't in the current map.

### Phase 2 — STT robustness for elderly speech (Days 4–6)

**Day 4 — Whisper prompt biasing + model upgrade**

- Pass an initial prompt to Whisper containing the plant names from `farmbot.yaml`. Biases the model toward those words. Catches "marrygold" → "marigold" and "bag onya" → "begonia" reliably.
- Upgrade default model from `base`/`small` to `medium` or `large-v3`. Elderly speech gets a meaningful boost with larger models.
- Add CLI flag `--whisper-model medium`.
- Tests: record 30–50 utterances from focus-group participants. `tools/eval_stt.py` runs each through Whisper-old vs Whisper-new and prints word-error-rate and intent-match-rate. Target: 30%+ WER reduction.

**Day 5 — Soft-confirm layer for destructive actions**

- Response shape adds `requires_confirm: bool` for actions in `farmbot.yaml`'s `always_confirm` list AND when LLM classifier confidence is low.
- UI: confirmation modal speaks the question via TTS. 10 s timeout → auto-cancel.
- "Stop" / "halt" bypasses confirm — emergency must be instant.
- Tests: "water everything" → confirms. "Stop" → immediate. "Water the tomatoes" at high confidence → no confirm.

**Day 6 — Simplified language + verbal feedback every step**

- Random-pick "thinking" phrases while waiting for LLM.
- Standardise post-action speech: "I watered the tomatoes" not "Action complete".
- Microcopy audit. Replace technical strings with plain English.
- 24 px minimum font on phone screens.
- Accessibility audit: screen-reader walk-through of the whole UI.

### Phase 3 — Plant care history + reminders (Days 7–11)

**Day 7 — SQLite event log on the Pi**

- New `src/growmate_pi/event_log.py`. Single table `events(id, ts, plant_index, plant_name, event_type, payload_json)`.
- DB at `~/.growmate_pi/events.db`. Single writer, single reader.
- Hook into `action_nodes.py`: every per-plant action's `update()` ends with `_log_event(...)` on success.
- Tests: 50 commands in a row → DB stays under 1 MB, queries return in < 5 ms.

**Day 8 — `GET /plants/{idx}` endpoint with derived state**

- Endpoints: `GET /plants`, `GET /plants/{idx}`, `GET /plants/{idx}/history`, `GET /plants/needs_attention`.
- Derived state: `last_watered_ts`, `last_sensed_moisture`, `last_photo_ts`, `days_since_watered`, `attention_flag` (species-specific overdue rules).

**Day 9 — UI: tap a plant → care card**

- Click a plant on the SVG map → slide-up panel showing position, last watered, last sensed, last photo, and `[Water] [Photo] [Read sensor]` buttons.
- Card actions POST through `/api/voice` with a synthetic intent — reuses the existing pipeline.

**Day 10 — "Today's tasks" panel on home view**

- New UI section above the chat feed: overdue watering count, plants without recent photos, sensor checks needed.
- Each line tappable → expands to plant list → tap any → opens the care card.
- Poll `/plants/needs_attention` on page load and every 5 minutes.

**Day 11 — Voice-driven plant queries**

- Classifier prompt extended with state-query patterns: "When did I last water X" → action: `general_question`, target: X.
- For high-confidence date queries, bypass the LLM and answer deterministically: "You watered the tomatoes 2 days ago."

### Phase 4 — Demo polish (Days 12–13)

**Day 12 — End-to-end test on hardware**

- Run the full 29-utterance eval against the new pipeline.
- Update `tools/evaluate_v2.py` to also assert event log entries after each utterance.
- Compare metrics to V1 baseline (DBSR 96.6%, SNSR 98.8%, USC 0, latency 5,456 ms mean).

**Day 13 — Demo script + recovery procedures**

- `demo/demo_day_run.md`: pre-demo checklist, 10 scenarios, recovery steps for each likely failure.
- Practice the full demo end-to-end twice.

### Phase 5 — User testing + final fixes (Days 14–15)

**Day 14 — Focus-group simulation**

- 1–2 elderly volunteers, 15 min each. Recorded with consent.
- Watch for: STT misfires the user didn't notice, UI elements they had trouble finding, speech patterns the system didn't expect.

**Day 15 — Buffer + final demo dress rehearsal**

- Reserve for fixing whatever surfaced on day 14.
- Final dress rehearsal of the demo script.
- Update thesis chapter 4 (Evaluation) with new metrics.

### Success criteria

- **STT**: word-error-rate on the elderly corpus ≤ 60% of the V1 baseline
- **General Q**: 80% of test gardening questions get sensible, on-topic answers
- **Care tracking**: any plant's last-watered date queryable in < 1 s via voice; "today's care" panel shows correct overdue list
- **Demo**: full 10-scenario run-through completes without operator intervention twice in a row

### Risks + mitigations

| Risk | Mitigation |
|---|---|
| Whisper-large is too slow | Stay on `medium`, accept some accuracy loss |
| Open-Meteo rate-limited | Cache 30 min, fall back gracefully if no response |
| Elderly volunteers unavailable | Use voice recordings from public datasets + simulated speech |
| Demo day Ollama crashes | Pre-load model, run a watchdog that restarts on failure |
| Pi storage fills up with events.db | Daily prune; events older than 90 days archived to YAML |

### What we won't do this sprint

- Cloud sync of event log (single-Pi only)
- Multi-user accounts
- New robot actions beyond what's in V2 today
- Refactor the BT framework

### Day-by-day deliverables list

| Day | Deliverable | Status |
|---|---|---|
| 1 | general_question end-to-end | ✅ |
| 2 | Weather context | ✅ |
| 3 | Per-plant context | ✅ |
| 4 | Whisper prompt + larger model | |
| 5 | Soft-confirm layer | |
| 6 | Microcopy + accessibility | |
| 7 | SQLite event log | |
| 8 | Per-plant endpoints | |
| 9 | Care card UI | |
| 10 | Today's care panel | |
| 11 | Voice plant queries | |
| 12 | Hardware eval | |
| 13 | Demo script | |
| 14 | Focus-group session | |
| 15 | Buffer + dress rehearsal | |
