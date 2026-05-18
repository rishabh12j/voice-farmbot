# GrowMate V2 — Concrete Change Plan

Working from the confirmed architecture:

- **Phone (browser)** — captures audio, runs STT, calls LLM, emits intent JSON, POSTs to Pi
- **Pi** — receives intent JSON, builds py_trees BT, ticks tree, publishes to `keyboard_topic`
- **Windows** — out of the request path (still useful as dev/sim host, optional Ollama backend)

---

## Clarification needed first

"Intent classification on phone" is ambiguous. Three concrete interpretations:

| Variant | Where STT runs | Where LLM runs | Network calls |
|---------|----------------|----------------|---------------|
| **V1: Fully phone-side** | Browser (whisper.cpp WASM, ~40MB) | Browser fetch → Ollama on LAN host | 2 hops: phone→Ollama, phone→Pi |
| **V2: Phone STT, remote LLM** | Browser WASM | Ollama on Windows/cloud (called from phone) | 2 hops same as V1 |
| **V3: Phone is just UI** | Pi or remote service | Same | 1 hop: phone→Pi (Pi handles STT+LLM internally) |

V1 is the purest "phone-side" but Whisper WASM is heavy and CORS to Ollama needs config.
V3 contradicts the stated goal but is what's currently closest to working.

**Recommendation: V2 with progressive fallback** — start with browser sending WAV to a small `/transcribe` endpoint on Pi (V3-ish), then move STT to browser later. The Pi `/intent` contract stays identical, so it's a clean phone-side change with no Pi rework.

---

## File-by-file change inventory

### NEW — Pi package (call it `growmate_pi`)

| File | Purpose | Reference |
|------|---------|-----------|
| `pi_intent_server.py` | FastAPI: `POST /intent`, `POST /estop`, `GET /status`. Owns the BT executor lifecycle. | New |
| `bt/condition_nodes.py` | py_trees conditions: `IsAvailable`, `InBounds`, `PlantFound` | Port EE650 `bt_nodes.py` pattern |
| `bt/action_nodes.py` | py_trees actions: `PublishCmd`, `MoveTo`, `WaterOn`, `WaterOff`, `Home`, `Light`, `Photo`, `ReadSensor`, `EmergencyStop` | Each just publishes to `keyboard_topic` |
| `bt/builder.py` | `build_tree(intent: dict) → py_trees.composites.Sequence` — replaces `AICore._intent_to_tree` | Routes action string to subtree builder |
| `bt/executor.py` | Tick loop, blackboard mgmt, result aggregation. Returns `TreeResult` to API. | Pattern from EE650 `mission_controller._execute_plan` |
| `pddl/farmbot_domain.pddl` | Actions: `move_to`, `water`, `check_sensor`, `light_on/off`. Predicates: `at_plant`, `needs_water`, `light_state` | New, based on EE650 `energy_domain.pddl` |
| `mission/plansys2_controller.py` | For multi-step intents only — sequences plant visits, watering priorities | Adapt EE650 `mission_controller.py` |
| `farmbot_ros2_bridge.py` | Wraps `rclpy` publish to `keyboard_topic`. Single ROS2 node, py_trees nodes call into it. | Extract from current `ros2_publisher.py` |
| `config/farmbot.yaml` | Copy of current config — Pi needs plants, bounds, commands | Copied from `growmate_voice/config/` |
| `requirements-pi.txt` | Add: `py_trees`, `py_trees_ros`, `fastapi`, `uvicorn`. PlanSys2 from apt: `ros-humble-plansys2-*` | Extend existing |

### NEW — Phone client

If we go HTML/JS (lowest friction since current `app.py` already serves a browser UI):

| File | Purpose |
|------|---------|
| `client/index.html` | Single-page UI: mic button, jog pad, status, history, plant list |
| `client/main.js` | `MediaRecorder` → WAV → `fetch('/transcribe')` (on Pi) → text → `fetch('/intent')` to LLM host → intent JSON → POST to Pi `/intent` |
| `client/styles.css` | Extract from current `app.py` inline styles |
| `client/intent_client.js` | Calls Ollama via fetch (`POST /api/chat`), uses prompt from `ai_core._classify_prompt` |

**Reuse path:** The HTML in `app.py` (look around lines 100-500 — it generates the UI inline) can be extracted to static files. The current FastAPI app serves the UI; just point it at the Pi for execution instead of doing it locally.

### MODIFY — Windows side (`src/growmate_voice/`)

| File | Change | Reason |
|------|--------|--------|
| `app.py` | Strip BT execution + ROS2 publish. New mode: `--client` runs UI + STT + LLM only, POSTs intent to Pi. Keep `--sim` for current Windows-only dev flow. | Becomes a phone-stand-in for development |
| `ai_core.py` | No code change — still emits intent JSON. Just used by the client now instead of feeding `BTEngine`. | Reused as-is |
| `bt_engine.py` | **Retire** once Pi `bt/executor.py` works. Keep around during transition for parity tests. | Replaced by py_trees executor on Pi |
| `bt_bridge.py` | **Retire** — was for the workbench. If we want a client-side tree preview, rewrite in JS. | Dead with `bt_engine.py` |
| `ros2_publisher.py` | **Move to Pi** (becomes `farmbot_ros2_bridge.py`). Delete from Windows. | Pi-only concern now |
| `voice_controller_node.py` | **Retire or replace** with `pi_intent_server.py`. The headless ROS2 node is no longer the right shape — we want HTTP-first on Pi. | Replaced |
| `scheduler.py` | **Move to Pi.** Scheduler hits the same `/intent` endpoint with `{"action": "water_all"}` | Pi-resident daemon |
| `stt_test.py` | Keep on Windows for STT/TTS experimentation — no change | Dev tool |
| `cli.py` | Update to POST to Pi `/intent` instead of calling `BTEngine` | Thin client |
| `edgespeech/` | **Stays on Windows** for the dev client. Pi gets its own minimal STT (just one backend). | Heavy deps stay on Windows |
| `history.py`, `logger.py` | Pi gets copies — both sides log independently | Duplicated |

---

## What dies, what lives, what moves

```
RETIRE (after Pi parity verified):
  bt_engine.py
  bt_bridge.py
  voice_controller_node.py

MOVE to Pi:
  ros2_publisher.py → farmbot_ros2_bridge.py
  scheduler.py     → pi/scheduler.py (calls /intent locally)
  config/farmbot.yaml → pi/config/ (copy)

STAY on Windows (dev/client only):
  ai_core.py
  edgespeech/
  stt_test.py
  cli.py (modified to POST to Pi)
  history.py, logger.py (kept; Pi gets its own copies)
  app.py (stripped to client-only)

NEW on Pi:
  pi_intent_server.py
  bt/{condition_nodes,action_nodes,builder,executor}.py
  farmbot_ros2_bridge.py
  mission/plansys2_controller.py
  pddl/farmbot_domain.pddl
```

---

## The intent JSON contract (frozen first thing — everything else depends on it)

```json
{
  "intents": [
    {
      "action": "water",
      "target": "tomatoes",
      "params": { "duration_s": 6 },
      "response": "Watering the tomatoes!"
    }
  ],
  "raw_text": "water the tomatoes please",
  "emergency": false,
  "client_id": "phone-abc123",
  "timestamp": "2026-05-18T14:32:00Z"
}
```

Pi reply:
```json
{
  "status": "success",
  "tree": { "label": "Water tomatoes", "nodes": [...], "result_per_node": [...] },
  "commands_published": ["M 400 200 -100", "D_W_1", "D_W_0"],
  "tts_text": "Watering the tomatoes!",
  "duration_ms": 8421
}
```

---

## Revised build order

| # | Step | Where | Blocks |
|---|------|-------|--------|
| 1 | Freeze intent JSON schema (above) | Doc | Everything |
| 2 | Install `py_trees`, `py_trees_ros`, PlanSys2 on Pi | Pi | Steps 3+ |
| 3 | Port `ros2_publisher.py` → `farmbot_ros2_bridge.py` on Pi, verify it still publishes to `keyboard_topic` | Pi | Step 4 |
| 4 | Implement py_trees action + condition nodes (one minimal: water) | Pi | Step 5 |
| 5 | Implement `bt/builder.py` for `water` action only — end-to-end smallest slice | Pi | Step 6 |
| 6 | Implement `pi_intent_server.py` with `POST /intent`, only `water` works | Pi | Step 7 |
| 7 | Modify Windows `app.py`: after LLM classify, POST intent JSON to Pi instead of running BTEngine | Windows | Step 8 |
| 8 | End-to-end: "water the tomatoes" voice → phone STT/LLM → Pi → keyboard_topic (sim mode) | Both | — |
| 9 | Port remaining actions: move, water_all, photo, sensor, light, home | Pi | — |
| 10 | Port emergency stop path: client `POST /estop` → Pi directly publishes `e` | Pi | — |
| 11 | Move scheduler to Pi, point at `/intent` | Pi | — |
| 12 | Add PDDL domain + PlanSys2 mission controller for multi-intent | Pi | — |
| 13 | Run 29-utterance eval against the new pipeline | Both | — |
| 14 | Test on real FarmBot hardware | Hardware | — |

---

## Risks / things to flag now

1. **Emergency stop latency.** Currently `/estop` publishes `e` directly in <50ms. New path: phone → Pi `/estop` over LAN. If LAN drops, e-stop fails. Keep a hardware e-stop available; don't rely only on the network path.

2. **CORS for Ollama from browser.** Ollama doesn't enable CORS by default. Set `OLLAMA_ORIGINS=*` (dev) or run Ollama behind a thin proxy that adds CORS headers.

3. **py_trees on Pi.** `pip install py_trees` works but ROS2-integrated `py_trees_ros` needs careful version pinning against Humble. Verify it imports clean before writing nodes.

4. **PlanSys2 install footprint.** It's heavy (~300MB). If Pi storage is tight, install only if/when we add multi-step planning. Step 2 can be split: install py_trees first, PlanSys2 only when needed (step 12).

5. **The 29-utterance eval corpus assumes synchronous, in-process execution.** Adapting it to HTTP roundtrips will add ~hundreds of ms latency per utterance — re-baseline the 5,456 ms paper number.

6. **Two `history.py` and two `logger.py` copies.** Acceptable, but accept that the Windows-side history won't reflect Pi-side execution unless we add a `/history` endpoint on the Pi.
