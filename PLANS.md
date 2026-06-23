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
- **Snake (boustrophedon) traversal** — `water_all`/species water walk plants
  band-by-X, alternating Y, for the shortest deterministic gantry path.
- **Per-greenhouse scheduler + single launch** (`greenhouse.launch.py`,
  `scheduler:=false` for tool work).
- **Hardware bringup on two FarmBots** (gh1 + farmbotdev): fixed the sim-vs-real
  bridge fallback (PYTHONPATH), the UART UTF-8 decode crash, and the
  tool-sequencer release-direction bug.
- **Tool-head layer** built (MountTool/UnmountTool/EnsureTool/CheckToolMounted)
  and **gh1 tool bays calibrated + registered** (soil_sensor, watering_nozzle).
- **Camera working on gh1**: `I_0` circle-grid calibration succeeds
  (`camera_calibration.yaml`), `I_1`/`I_2` panorama stitches at a sane
  downscaled map resolution (was a gigapixel canvas).

Capabilities today: move, jog, water `<species>`, water_all, go_home, lights,
photo, panorama (working), scan_weeds (detect only), check_sensor, check_moisture,
emergency_stop, tool mount/unmount (sim + bays registered).

---

## 4. The plan — phases

Working order (agreed): **water → camera → weeding → seeding.** Soil sensing
folds into *water* (smart watering needs it); tool management folds into the
front of *weeding* (first non-default tool, reused by seeding).
Tags: 🔌 bridge plumbing, 🌳 BT, 📜 schema, 🖥 app, 🤖 firmware/stack, 👁 vision.

### Phase 0 — Close out the foundation on hardware (~2–3 days)
The gate/map were sim-verified; make them honest on gh1.
- Validate `/busy_state` on gh1 (see [demo/verify_gate_hardware.md](demo/verify_gate_hardware.md) §1). If absent, fix the firmware-handler launch — don't ship `--no-verify` as the permanent state.
- Confirm `R82` cadence; tune the live-map poll if it streams.
- **Flip `_MEMORY_FEATURES_ENABLED` ON** once a watered row only appears after firmware completion (today's-care + plant-query fast-path re-armed). 🖥
- **Exit:** event log is provably honest; memory features back on.

> **⬅ CURRENT FOCUS (2026-06):** Phase 1b `water_smart` is being built + sim-
> verified now (hardware-free). Phase 2 camera is largely done on gh1
> (calibration + panorama); remaining: confirm `I_4` weed-detect returns
> coordinates. Phase 3 tool layer is built + gh1 bays registered; remaining:
> confirm `T_1_1` physically mounts on hardware (test on farmbotdev). Next
> convergence point is Phase 3 `clear_weeds` (needs camera detect + tool mount).

### Phase 1 — Water, made smart (~1.5 weeks)  ← NEXT
Two sub-steps; the sensor plumbing here is the foundation everything later
reuses (it's the same bridge-subscription pattern as the gate).
- **1a — Real soil sensing.** 🔌 bridge parses pin-read replies (`D_S_C` → firmware `R41 P<pin> V<val>`) off `/uart_receive`, exposes `last_reading(pin)`; 🌳 `ReadSensor` captures the value (replace the `"pending-subscription"` stub); 📜 `SoilReading` field; 🖥 "check the soil on the tomatoes" speaks a real number.
- **1b — Moisture-aware watering.** 🌳 `water_smart`: per matched plant, read soil → water only if below threshold → report skips; 🖥 "water the dry ones."
- **Files:** farmbot_ros2_bridge.py, bt/action_nodes.py, bt/builder.py, intent_server.py, schemas.py.
- **Exit:** real soil value spoken; "water the thirsty lettuce" reads each plant and waters selectively ("watered 3 of 8, the rest were moist").

### Phase 2 — Camera / vision (~1.5 weeks)
The vision foundation; weeding depends on it.
- 👁 Wire the camera path ([camera_handler/](src/camera_handler/camera_handler/): `luxonis_camera`, `panorama`, `plant_detection`).
- Capabilities: photo/panorama (have), **weed detection** (`I_4` → coordinates), **plant detection** (auto-find plants), **soil-height** measure.
- 🖥 "find the plants" / "scan for weeds" → returns structured detections (coords) the BT can act on; optionally update the live map.
- **Exit:** a scan returns plant/weed coordinates that a later phase can consume.

### Phase 3 — Weeding (~1.5 weeks)  [builds tool management first]
First non-default tool → build tool management here and reuse it for seeding.
- 🌳 **Tool management:** `CheckToolMounted` condition + `MountTool`/`UnmountTool` (`T_n_1`/`T_n_2`, `D_C`); tool slots/positions in config (`T_n_0`); safety prefix extended so tool actions gate on the right tool.
- 🌳 `clear_weeds`: scan (Phase 2) → mount weeder → per weed: move → lower → remove → raise → unmount.
- **Exit:** "clear the weeds in bed 2" → detects + physically removes, tool verified.

### Phase 4 — Seeding (~1 week)
Reuses tool management; adds plant lifecycle.
- 📜🌳 `seed <species>`: mount seeder → per Planning slot: move → vacuum pick from tray (`D_V`) → inject → release; wrap `P_3` first, then per-plant.
- Plant lifecycle: Planning → Sprouted via map_handler.
- **Exit:** "plant the lettuce bed" seeds the Planning plants.

### Phase 5 — Autonomy, evaluation, docs (ongoing)
- Regimens via [scheduler.py](src/growmate_voice/growmate_voice/scheduler.py): daily smart-water, weekly weed-scan.
- Extend the V2 eval corpus to the new verbs (sense/weed/seed); track DBSR / SNSR / **USC=0** ([demo/eval_v2_results.md](demo/eval_v2_results.md)).
- Keep [documentation/](documentation/) + [demo/RUN_GUIDE.md](demo/RUN_GUIDE.md) current per phase.

---

## 5. Capability scoreboard

| Capability | Hardware | Stack | Voice/BT | Plan |
|---|---|---|---|---|
| Move / jog / home | ✅ | ✅ | ✅ | done |
| Water (rigid) | ✅ | ✅ | ✅ | done |
| Lights / photo / panorama | ✅ | ✅ | ✅ | done |
| Weed **detect** | ✅ | ✅ | ✅ | done |
| Soil moisture **read** | ✅ | ✅ | ⚠️ sim (R41 parsed) | **Phase 1a** |
| Moisture-aware watering | ✅ | ✅ (`P_5`) | 🔨 building (`water_smart`) | **Phase 1b** |
| Camera calibration | ✅ | ✅ | ✅ (`I_0`) | done |
| Panorama stitch | ✅ | ✅ | ✅ (downscaled) | done |
| Plant detection / soil height | ✅ | ⚠️ partial | ❌ | **Phase 2** |
| Tool mount / swap | ✅ | ✅ (`T_n`) | ⚠️ sim + gh1 bays registered | **Phase 3** |
| Weed **removal** | ✅ | ❌ | ❌ | **Phase 3** |
| Seeding | ✅ | ✅ (`P_3`) | ❌ | **Phase 4** |
| Scheduled regimens | ✅ | ⚠️ basic | ⚠️ basic | **Phase 5** |

---

## 6. Per-phase definition of done (applies to every phase)

1. New action added to `schemas.Action` + a `_tree_*` builder with the full safety prefix.
2. Robot-touching steps go through the tick-and-verify gate.
3. Sim-verified (sim fakes the new firmware signal where needed), then hardware-verified.
4. One line added to the eval corpus; USC stays 0.
5. RUN_GUIDE / scoreboard updated.
