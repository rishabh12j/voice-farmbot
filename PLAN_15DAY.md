# GrowMate — 15-Day Elderly UX Sprint

Goal: by day 15, demo-ready system that handles elderly speech reliably,
answers gardening questions with real context, and tracks per-plant care
history so users with weak memory always know what's been done and what's
due.

Working day = 1 calendar day. Buffer is baked into days 14–15.

---

## Phase 1 — LLM context layer (Days 1–3)

### Day 1 — Debug + fix general_question response flow

**Why first:** It should already work. Quickest win, makes the system feel
alive for the rest of the sprint.

**Trace path:**
- `ai_core.py:_classify` returns intents with `action: general_question`
- Pi `build_subtree` → `_tree_general_question` → `Respond(intent.response)`
- The `response` here is just the LLM's pre-baked friendly reply, NOT the
  actual answer to the question. Bug found: we never call the reasoning
  layer.

**Fix:**
- On the **client** side (Windows app, `_dispatch_via_aicore`): when an
  intent is `general_question`, also call `AICore.reason()` with the
  question to get the real answer, then put it into `intent.response`
  before POSTing to Pi.
- The Pi already speaks `intent.response` via TTS — no Pi-side change.

**Tests:**
- 5 manual questions through the mic:
  - "When should I plant basil?"
  - "How often should I water tomatoes?"
  - "What's the best fertilizer for marigolds?"
  - "Are scallions perennial?"
  - "Do dianthus need full sun?"
- Each should get a coherent, friendly TTS response within ~5 s.

**Deliverable:** `general_question` works end-to-end, TTS speaks real answers.

---

### Day 2 — Add weather context

**Why:** Elderly users ask "should I water today?" and "is it going to be
warm?" The LLM has no real-time data without external help.

**Implementation:**
- New file: `src/growmate_voice/growmate_voice/weather.py`
- Use **Open-Meteo** (free, no API key, ISO standard): `https://api.open-meteo.com/v1/forecast?latitude=53.385&longitude=-6.601&hourly=temperature_2m,precipitation,relative_humidity_2m&forecast_days=3`
- Cache result for 30 minutes (one weather query covers many user questions).
- Pass parsed summary into `AICore.reason()`'s context dict:
  ```
  "weather_today": "12C, 70% humidity, 2mm rain expected",
  "weather_3day": "...",
  ```

**Where it lives in the prompt:** prepend to the `Context:` block in
`AICore.reason()`. The LLM already accepts context dicts.

**Tests:**
- "Should I water today?" → response mentions today's rain forecast
- "Is it warm enough to plant basil outside?" → response references temperature
- "What's the weather like?" → factual report

**Deliverable:** Weather data flows into LLM responses automatically.

---

### Day 3 — Per-plant context in the prompt

**Why:** "How are the tomatoes doing?" should pull live state (last watered,
last sensed moisture) not just regurgitate generic gardening advice.

**Implementation:**
- New helper in `pi_client.py`: `get_plant_summary(target)` → calls Pi
  `/plants` plus a not-yet-existing `/plants/{name}/state` endpoint.
- For now (before SQLite is in), the state is what we can infer from the
  current `active_map.yaml`: position, water_quantity, growth_stage.
- Prepend the plant summary to `context` when the question or intent has a
  `target`.

**Tests:**
- "How are the tomatoes looking?" → mentions tomato bed coords + species
- "What stage is my basil at?" → mentions Seedling/Growing/Mature
- "Is my dianthus thirsty?" → speculates based on growth stage + weather

**Deliverable:** Plant-targeted questions get plant-specific answers.

---

## Phase 2 — STT robustness for elderly speech (Days 4–6)

### Day 4 — Whisper prompt biasing + model upgrade

**Why:** Whisper's default behaviour mis-hears uncommon words. Plant names
like "Begonia", "Euonymus", "Dianthus" are vulnerable. An "initial prompt"
biases Whisper toward those words.

**Implementation:**
- Modify `edgespeech/stt/whisper_backend.py` to accept and apply
  `initial_prompt` from a config string. The string is built at startup from
  the loaded `farmbot.yaml` plant + alias list.
- Upgrade default model from current (`base` or `small`?) to `medium`. Test
  `large-v3` as an option — significant accuracy gain for low-volume speech.
- Add CLI flag `--whisper-model medium` to `app.py`.

**Tests:**
- Record 30 utterances from at least 2 elderly volunteers (or use simulated
  broken speech with intentional pauses, hesitations, soft consonants).
- Write `tools/eval_stt.py` that runs each WAV through Whisper-old vs
  Whisper-new and prints:
  ```
  utterance        | old transcript        | new transcript    | match
  "Water Begonia" | "water bug onya"     | "water Begonia"   | ✓
  ```
- Track word-error-rate (WER) before/after. Target: 30%+ reduction.

**Deliverable:** Plant-name accuracy materially improved on test corpus.

---

### Day 5 — Soft-confirm layer for destructive actions

**Why:** Even with better STT, mishears happen. "Water everything" must not
fire on a 50% confidence transcript. Elderly users feel safer with a
confirmation step.

**Implementation:**
- Add to the response shape: `requires_confirm: bool` for actions in
  `farmbot.yaml`'s `always_confirm` list (water_all, calibrate) AND
  whenever the LLM classifier confidence is < threshold.
- When the UI receives a `requires_confirm` response, show a big
  confirmation modal: "I heard: water everything. Tap YES to do it, NO to
  cancel." Speak the question via TTS.
- 10-second timeout → auto-cancel.
- "Stop" / "halt" still bypasses confirm because emergency must be
  instant.

**Tests:**
- Voice "water everything" → confirmation appears, speaks request
- Voice "stop" → no confirmation, fires immediately
- Voice "water the tomatoes" + low STT confidence → confirmation
- Voice "water the tomatoes" + high STT confidence → no confirmation

**Deliverable:** Destructive commands always go through a clear confirm
unless explicitly safe.

---

### Day 6 — Simplified language + verbal feedback every step

**Why:** Elderly users with cognitive load benefit from continuous,
predictable feedback. Silent processing feels broken.

**Implementation:**
- Add TTS "thinking" phrases the UI can speak while waiting for the LLM:
  "Let me look that up", "Just a moment". Random pick from a short list to
  avoid robotic repetition.
- After every successful action, speak the result (already partially done).
  Standardize copy: "I watered the tomatoes" not "Action complete".
- Microcopy audit: replace any remaining technical strings ("P_4
  published", "executing tree") with plain English.
- Larger default font (20px → 24px), already in the redesign but verify on
  a phone screen.

**Tests:**
- Issue 10 commands in a row, observe whether the user can always tell
  what's happening at every moment via audio alone (eyes closed test).
- Accessibility audit: screen reader walk-through of the whole UI.

**Deliverable:** No silent dead time, no technical jargon, all UI ≥ 24px.

---

## Phase 3 — Plant care history + reminders (Days 7–11)

### Day 7 — SQLite event log on the Pi

**Implementation:**
- New file: `src/growmate_pi/event_log.py`
- Single table:
  ```sql
  CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,       -- unix epoch ms
    plant_index INTEGER,        -- null for non-plant events (estop, lights)
    plant_name TEXT,
    event_type TEXT NOT NULL,   -- watered / sensed / photographed / moved / note
    payload_json TEXT           -- e.g. {"duration_s": 6} or {"moisture": 380}
  );
  CREATE INDEX idx_plant ON events(plant_index);
  CREATE INDEX idx_ts ON events(ts);
  ```
- DB lives at `~/.growmate_pi/events.db`. Single writer, single reader (the
  intent server). FastAPI dependency injection passes the connection.

**Hook into BT executor:**
- Every node that publishes a per-plant command writes an event. The
  cleanest spot: `action_nodes.py`. Each action's `update()` ends with
  `self._log_event(...)` when the action succeeds.

**Tests:**
- Run "water the tomatoes" → event row appears with `event_type='watered'`
  and `payload_json` containing duration
- Run a panorama → multiple `photographed` rows (one per plant in frame? or
  one for the whole bed — pick one and document)
- 50 commands in a row → DB stays under 1 MB, queries return in < 5 ms

**Deliverable:** Append-only event log running on the Pi, fed by every BT
execution.

---

### Day 8 — `GET /plants/{idx}` endpoint with derived state

**Implementation:**
- Add to `growmate_pi/intent_server.py`:
  ```
  GET /plants                  → existing — list of plants
  GET /plants/{idx}            → plant detail + last 30 days of events
  GET /plants/{idx}/history    → just the event timeline
  GET /plants/needs_attention  → list of plants overdue for care
  ```
- "Derived state" is computed from the event log:
  - `last_watered_ts`
  - `last_sensed_moisture`
  - `last_photo_ts`
  - `days_since_watered`
  - `attention_flag`: derived from species-specific rules (e.g. Tomato:
    overdue if > 2 days without water; Marigold: overdue if > 4 days)

**Tests:**
- `curl -s http://pi:8000/plants/5` → JSON includes derived state
- Water one plant, query its history → row appears immediately
- Don't water a thirsty plant for several "simulated" days (mock ts) →
  `attention_flag` flips true

**Deliverable:** Pi exposes per-plant care state via clean HTTP.

---

### Day 9 — UI: tap a plant → care card

**Implementation:**
- Modify `index.html`: clicking a plant on the SVG map shows a slide-up
  panel (use the existing tooltip code as a starting point) with:
  ```
  ┌─────────────────────────────┐
  │ Tomato #5                   │
  │ Position: x=4250, y=1150    │
  │                              │
  │ ✓ Watered 2 days ago         │
  │ • Last sensed: 380 (dry)     │
  │ • Last photo: 4 days ago     │
  │                              │
  │ [Water this plant] [Photo]   │
  │ [Read sensor]                │
  └─────────────────────────────┘
  ```
- All actions on the card POST through `/api/voice` with a synthetic intent
  (no audio needed) — re-uses the existing pipeline.
- "All good" / "Needs attention" badge based on the Pi's `attention_flag`.

**Tests:**
- Tap a plant on the map → card opens within 200 ms
- Card shows last watered time, refreshed from `GET /plants/{idx}`
- Tap "Water this plant" on the card → real watering happens, event logged

**Deliverable:** Per-plant tactile interface that elderly users can browse.

---

### Day 10 — "Today's tasks" panel on home view

**Why:** Proactive nudging. Elderly users with weak memory shouldn't have
to remember what's due — the system tells them.

**Implementation:**
- New UI section above the chat feed:
  ```
  Today's care
  ──────────────
  ❗ 3 plants need watering
  📷 5 plants haven't been photographed in a week
  💧 Soil sensor reading hasn't been taken for any pepper
  ```
- Each line tappable → expands to plant list → tap any → opens the care card.
- Poll `/plants/needs_attention` on page load and every 5 minutes.

**Tests:**
- Skip watering for a day → "Today's care" shows N plants overdue
- Tap a line → drill down works
- Voice command: "What needs my attention?" → reads the list aloud

**Deliverable:** Front-page "what to do today" view.

---

### Day 11 — Voice-driven plant queries

**Why:** "When did I last water the tomatoes?" should be a one-sentence
spoken question with a one-sentence spoken answer.

**Implementation:**
- Extend `ai_core.py`'s classifier prompt with these intent patterns:
  - "When did I last water X" → action: `general_question`, target: X,
    question rewritten as a state query
- AICore's `reason()` is already enhanced with plant state context — the
  LLM composes the answer from the structured context.
- For high-confidence date queries, bypass the LLM and answer
  deterministically: "You watered the tomatoes 2 days ago." Faster, more
  precise.

**Tests:**
- "When did I last water the marigolds?" → spoken answer with timestamp
- "How many days has it been since I checked the peppers?" → count
- "Which plant needs water most urgently?" → top of attention list spoken

**Deliverable:** Voice queries of plant history work end-to-end.

---

## Phase 4 — Demo polish (Days 12–13)

### Day 12 — End-to-end test on hardware

- Run the full 29-utterance eval against the new pipeline (with Whisper-new
  + AICore + Pi + event log).
- Update `tools/evaluate_v2.py` to also assert event log entries after
  each utterance.
- Compare metrics to the V1 baseline in the thesis (DBSR 96.6%, SNSR 98.8%,
  USC 0, latency 5456 ms mean).
- Document any regressions in `demo/eval_v2_results.md`.

### Day 13 — Demo script + recovery procedures

- Write `demo/demo_day_run.md`:
  - Pre-demo checklist (bringup running, intent server up, mic permission)
  - 10 demo scenarios in increasing complexity
  - Recovery steps for each likely failure (mic denied, Pi disconnected,
    Ollama down, water pump stuck)
- Practice the full demo end-to-end twice.

---

## Phase 5 — User testing + final fixes (Days 14–15)

### Day 14 — Focus-group simulation

- If possible, run 1-2 elderly volunteers through the system for 15 min
  each.
- Record sessions (with consent). Watch for:
  - STT misfires the user didn't notice
  - UI elements they had trouble finding
  - Speech patterns the system didn't expect
- Make targeted fixes only — no scope creep.

### Day 15 — Buffer + final demo dress rehearsal

- Reserve for fixing whatever surfaced on day 14.
- Final dress rehearsal of the demo script.
- Update thesis chapter 4 (Evaluation) with new metrics from day 12.

---

## Success criteria (what "done" means)

- **STT**: word-error-rate on the elderly corpus ≤ 60% of the V1 baseline
- **General Q**: 80% of test gardening questions get sensible, on-topic
  answers
- **Care tracking**: any plant's last-watered date queryable in < 1 s via
  voice; "today's care" panel shows correct overdue list
- **Demo**: full 10-scenario run-through completes without operator
  intervention twice in a row

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Whisper-large is too slow on the Windows machine | Stay on `medium`, accept some accuracy loss |
| Open-Meteo rate-limited | Cache 30 min, fall back gracefully if no response |
| Elderly volunteers unavailable | Use voice recordings from public datasets + simulated speech |
| Demo day Ollama crashes | Pre-load model, run a watchdog that restarts on failure |
| Pi storage fills up with events.db | Daily prune; events older than 90 days archived to YAML |

## What I won't do this sprint

- Cloud sync of event log (single-Pi only)
- Multi-user accounts
- New robot actions beyond what's in V2 today
- Refactor the BT framework

These would all be welcome but blow past 15 days.

---

## Day-by-day deliverables list

| Day | Deliverable | Files touched |
|---|---|---|
| 1 | general_question works end-to-end | `app.py`, `ai_core.py` |
| 2 | Weather context | `weather.py` (new), `ai_core.py` |
| 3 | Per-plant context | `pi_client.py`, `intent_server.py` |
| 4 | Whisper prompt + larger model | `whisper_backend.py`, `app.py`, `tools/eval_stt.py` (new) |
| 5 | Soft-confirm layer | `app.py`, `index.html`, `schemas.py` |
| 6 | Microcopy + accessibility | `index.html`, audit |
| 7 | SQLite event log | `event_log.py` (new), `action_nodes.py` |
| 8 | Per-plant endpoints | `intent_server.py` |
| 9 | Care card UI | `index.html`, `app.py` |
| 10 | Today's care panel | `index.html`, `intent_server.py` |
| 11 | Voice plant queries | `ai_core.py`, `intent_server.py` |
| 12 | Hardware eval | `tools/evaluate_v2.py`, `demo/eval_v2_results.md` |
| 13 | Demo script | `demo/demo_day_run.md` |
| 14 | Focus-group session | recordings + notes |
| 15 | Buffer + dress rehearsal | whatever surfaces |
