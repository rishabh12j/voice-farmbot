# GrowMate — Project Plan

Single source of truth for the work ahead. The goal of this phase is to grow
GrowMate from a **watering-only** voice interface into one that drives the
**full FarmBot tool-head capability set** — sense, water-smart, seed, weed,
and see — while keeping the thesis contribution intact.

> Prior planning notes (V1→V2 migration, the 15-day elderly-UX sprint) are in
> the git history of this file; they're implemented and no longer the working
> plan.

---

## 1. North star

Make every physical thing a FarmBot Genesis/XL can do reachable by a single
spoken sentence, safely and inspectably:

> "check the soil", "water the dry ones", "plant the lettuce bed",
> "clear the weeds", "find the plants" — each becomes a flat intent the LLM
> classifies, which a deterministic behaviour tree turns into a safe,
> firmware-verified action sequence.

Timeline: ~5–6 weeks. Phased so each phase ships a working, demoable capability.

---

## 2. Architecture invariants (the thesis contract — do not break)

Every new capability MUST preserve these, or it undermines the contribution:

1. **The LLM only does flat intent classification.** New capabilities are new
   entries in `schemas.Action` + a tree builder — never new LLM structure.
   ([schemas.py](src/growmate_pi/schemas.py), [builder.py](src/growmate_pi/bt/builder.py))
2. **All structure + safety live in deterministic Python (the BT).** Every
   robot action is prefixed by the safety chain, in code:
   `CheckAvailable → [CheckToolMounted] → [CheckBounds] → [CheckPlantFound] → action`.
   Adding an action without its guards is a research-claim violation, not a typo.
3. **A SUCCESS tick means the firmware confirmed it.** Everything goes through
   the tick-and-verify gate (busy_state) before it's logged or spoken as done.
   ([farmbot_ros2_bridge.py](src/growmate_pi/farmbot_ros2_bridge.py), [action_nodes.py](src/growmate_pi/bt/action_nodes.py))
4. **The tree is inspectable** — pure data, viewable before it runs.
5. **`keyboard_topic` stays the only thing we publish.** GrowMate remains
   additive to the AURA stack; new low-level needs go through the existing
   command vocabulary (see [documentation/High Level Commands.md](documentation/High%20Level%20Commands.md)).

---

## 3. Where we are now (done)

- **Voice → intent → BT → FarmBot** pipeline (13 actions), sim + hardware.
- **Tick-and-verify gate** — verified move/pump/home wait on `/busy_state`.
- **Async execution** — `/intent` runs the tree in the background; client polls
  `/intent_status`; no HTTP timeouts on long waters.
- **Two-voice speech** serialized (single queue, milestone progress, no overlap).
- **Live map** — gantry marker tracks the robot via `/uart_receive` R82
  (target-snap in sim).
- **Multi-plant watering** with progress overlay + e-stop, honest per-plant log.

Capabilities today: move, jog, water `<species>`, water_all, go_home, lights,
photo, panorama, scan_weeds (detect only), check_sensor (publishes, no value),
check_moisture, emergency_stop.

---

## 4. The plan — phases

Each phase: deliverable, files, and exit criteria. Tags: 🔌 bridge plumbing,
🌳 BT, 📜 schema, 🖥 app, 🤖 firmware/stack, 👁 vision.

### Phase 0 — Close out the foundation on hardware (~2–3 days)
The gate/map were sim-verified; make them honest on gh1.
- Validate `/busy_state` on gh1 (see [demo/verify_gate_hardware.md](demo/verify_gate_hardware.md) §1). If absent, fix the firmware-handler launch — don't ship `--no-verify` as the permanent state.
- Confirm `R82` cadence; tune the live-map poll if it streams.
- **Flip `_MEMORY_FEATURES_ENABLED` ON** once a watered row only appears after firmware completion (today's-care + plant-query fast-path re-armed). 🖥
- **Exit:** event log is provably honest; memory features back on.

### Phase 1 — Real soil sensing (~1 week)
Turn the placeholder soil sensor into a real reading. Reuses the exact
bridge-subscription pattern from the gate.
- 🔌 Bridge parses pin-read replies (`D_S_C` → firmware `R41 P<pin> V<val>`) off `/uart_receive`; exposes `last_reading(pin)`.
- 🌳 `ReadSensor` captures the value onto the blackboard (replace the `"pending-subscription"` stub).
- 📜 `SoilReading` field on the response; 🖥 `check_sensor` speaks a real number and the LLM reasons on it ("the soil is dry").
- **Files:** farmbot_ros2_bridge.py, bt/action_nodes.py (`ReadSensor`), intent_server.py, schemas.py.
- **Exit:** "check the soil on the tomatoes" → reports an actual moisture value.

### Phase 2 — Moisture-aware watering (~3–4 days)
- 🌳 New action `water_smart` (or `water_if_dry`): per matched plant, read soil → water only if below threshold → report what it skipped.
- 🖥 "water the dry ones" / "water the thirsty plants".
- **Exit:** "water the thirsty lettuce" → reads each, waters selectively, "watered 3 of 8, the rest were already moist."

### Phase 3 — Tool management + safety (~1 week)
The prerequisite for every non-watering tool.
- 🌳 New `CheckToolMounted` condition node; `MountTool`/`UnmountTool` actions (`T_n_1`/`T_n_2`, `D_C`).
- Tool slots + positions in config (`T_n_0` from farmbot.yaml).
- Extend the safety prefix: tool actions gate on the right tool being mounted.
- **Exit:** the system can swap seeder ↔ sensor ↔ weeder, verify the mount, and refuse a tool action when the wrong/no tool is present.

### Phase 4 — Seeding (~1 week)
First brand-new verb. Stack already supports `P_3`.
- 📜🌳 `seed <species>` action: mount seeder → (per Planning-stage slot) move → vacuum pick from tray (`D_V`) → inject → release; start by wrapping `P_3`, then per-plant.
- Plant lifecycle: Planning → Sprouted stage transitions via map_handler.
- **Exit:** "plant the lettuce bed" seeds the Planning plants.

### Phase 5 — Weeding (~1 week)
Biggest integration: vision + tool + motion. Hardware-capable; stack only
*detects* today.
- 👁 Weed detection via `I_4` / [camera_handler/plant_detection.py](src/camera_handler/camera_handler/plant_detection.py) → weed coordinates.
- 🌳 `clear_weeds` action: scan → per weed: mount weeder → move → lower → remove → raise.
- **Exit:** "clear the weeds in bed 2" → detects + physically removes.

### Phase 6 — Vision-driven map building (stretch, ~1 week)
- 👁 Use the camera + `plant_detection.py` to auto-detect plants / measure soil height → populate the map.
- 🖥 "scan the bed and find the plants" → updates the live map.
- **Exit:** the map can be (re)built from the camera, not just hand-authored YAML.

### Phase 7 — Autonomy, evaluation, docs (ongoing)
- Regimens via [scheduler.py](src/growmate_voice/growmate_voice/scheduler.py): daily smart-water, weekly weed-scan.
- Extend the V2 eval corpus to the new verbs (seed/weed/sense); track DBSR / SNSR / **USC=0** ([demo/eval_v2_results.md](demo/eval_v2_results.md)).
- Keep [documentation/](documentation/) + [demo/RUN_GUIDE.md](demo/RUN_GUIDE.md) current per phase.

---

## 5. Capability scoreboard

| Capability | Hardware | Stack | Voice/BT | Plan |
|---|---|---|---|---|
| Move / jog / home | ✅ | ✅ | ✅ | done |
| Water (rigid) | ✅ | ✅ | ✅ | done |
| Lights / photo / panorama | ✅ | ✅ | ✅ | done |
| Weed **detect** | ✅ | ✅ | ✅ | done |
| Soil moisture **read** | ✅ | ✅ | ⚠️ no value | **Phase 1** |
| Moisture-aware watering | ✅ | ✅ (`P_5`) | ❌ | **Phase 2** |
| Tool mount / swap | ✅ | ✅ (`T_n`) | ❌ | **Phase 3** |
| Seeding | ✅ | ✅ (`P_3`) | ❌ | **Phase 4** |
| Weed **removal** | ✅ | ❌ | ❌ | **Phase 5** |
| Plant detection / soil height | ✅ | ⚠️ partial | ❌ | **Phase 6** |
| Scheduled regimens | ✅ | ⚠️ basic | ⚠️ basic | **Phase 7** |

---

## 6. Per-phase definition of done (applies to every phase)

1. New action added to `schemas.Action` + a `_tree_*` builder with the full safety prefix.
2. Robot-touching steps go through the tick-and-verify gate.
3. Sim-verified (sim fakes the new firmware signal where needed), then hardware-verified.
4. One line added to the eval corpus; USC stays 0.
5. RUN_GUIDE / scoreboard updated.
