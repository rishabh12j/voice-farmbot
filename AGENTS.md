# AGENTS.md — GrowMate / VoiceBT

Context for any AI/coding agent working in this repo. Read this first; it
encodes the **research contract** that must not be broken and the workflow that
keeps changes safe. For depth: [README.md](README.md) (thesis framing),
[PLANS.md](PLANS.md) (live roadmap), [demo/RUN_GUIDE.md](demo/RUN_GUIDE.md) (run
on hardware).

---

## 1. What this is

GrowMate is a **voice-control interface for a FarmBot Genesis XL** garden robot,
built for **elderly/disabled users** who can say what they want but can't use a
keyboard/touchscreen. MSc thesis (Maynooth, 2026).

The underlying framework is **VoiceBT**: turn speech into safe robot action by
**constraining the LLM to flat intent classification** and delegating *all*
structure, sequencing, and safety to a **deterministic, inspectable behaviour
tree (BT)**. One-liner: *the LLM proposes, the behaviour tree disposes.*

GrowMate is **additive** to the upstream **AURA FarmBot ROS2** stack — it adds a
voice/BT layer on top and publishes only through the existing command interface.

---

## 2. Architecture invariants — the research contract (DO NOT BREAK)

Every change must preserve these, or it undermines the thesis:

1. **The LLM only does flat intent classification** → `{action ∈ fixed enum,
   target, response}`. It never emits trees, plans, code, or control flow. New
   capabilities = a new `schemas.Action` value + a tree builder, *never* new LLM
   structure.
2. **All structure + safety live in deterministic Python (the BT).** Every
   robot-touching action is prefixed by the safety chain, in code:
   `CheckAvailable → [CheckToolMounted] → [CheckBounds] → [CheckPlantFound] → action`.
   Adding an action without its guards is a research-claim violation, not a typo.
3. **A SUCCESS tick means the firmware confirmed it** (tick-and-verify gate via
   `/busy_state`; sensor/tool actions verify via pin reads). Nothing is logged or
   spoken as "done" before confirmation — the "honest-or-blank" rule.
4. **The tree is inspectable** — pure data, viewable before it runs.
5. **Additive to AURA**: GrowMate publishes only to `keyboard_topic` (the same
   topic the AURA `keyboard_controller` uses). Don't fork the base robot code.
   Upstream bugfixes are allowed but must be minimal + documented in the commit.

The eval invariant for the thesis is **USC (Unsafe State Count) = 0** even under
LLM misclassification — the BT guards catch wrong intents before any motion.

---

## 3. The pipeline

```
phone/browser:  mic → STT → LLM (flat classify) → IntentRequest JSON
                → POST /intent →
Pi (this repo):  build_tree(intent) → tick-with-verify → keyboard_topic
                → AURA stack → Farmduino → robot ; replies on /uart_receive, /busy_state
                → spoken confirmation (TTS)
```

Emergency words ("stop", "halt") are matched **before** the LLM is ever called.

---

## 4. Repo layout

| Path | Role |
|---|---|
| `src/growmate_pi/` | **The brain.** Intent server (`intent_server.py`), BT builder (`bt/builder.py`), nodes (`bt/action_nodes.py`, `bt/condition_nodes.py`), the ROS2 bridge (`farmbot_ros2_bridge.py`), wire schema (`schemas.py`), scheduler, per-greenhouse configs (`config/gh1.yaml`, `config/farmbotdev.yaml`). |
| `src/growmate_voice/` | The **client** (currently a desktop/browser web app): STT, LLM classification (`ai_core.py`), TTS, map UI. The end goal moves classification **on-device** (phone browser, LiteRT-LM). |
| `src/farmbot_controllers`, `farmbot_command_handler`, `map_handler`, `camera_handler`, `farmbot_bringup`, `farmbot_interfaces`, `hot_water_sprayer` | **Upstream AURA stack** — additive overlay, edit only for documented bugfixes. |
| `tools/` | helpers (`calibrate_tools.py` tool-bay calibration, eval). |
| `launch/greenhouse.launch.py` | one-command per-greenhouse bringup + intent server (+ scheduler). |
| `demo/`, `documentation/`, `PLANS.md`, `README.md` | docs / roadmap. |

### Key files to know
- `schemas.py` — the wire contract (`Action`, `Intent`, `IntentRequest`). Both
  sides import it; changing it is changing the API.
- `bt/builder.py` — `build_subtree()` maps each action to a `_tree_*` builder.
  This is where the safety prefix lives.
- `farmbot_ros2_bridge.py` — sim vs real ROS2; parses `R82` (position), `R41`
  (pin reads), counts `/busy_state` completions for the verify gate.
- `intent_server.py` — async `/intent` (+ `/intent_status` polling), `/status`,
  `/plants`; map/detection helpers (`find_plants_by_species`, `find_weeds`,
  `find_detected_plants`).

---

## 5. Capabilities (current)

move, jog, water `<species>`, water_all, **water_smart** (sense → water only the
dry), go_home, lights, photo, panorama, scan_weeds (detect), **clear_weeds**
(remove with the weeder), **scan_bed → find_plants → label_plants** (voice-
labelled map building from vision), check_sensor (real soil value),
check_moisture, tool mount/unmount (`EnsureTool`), emergency_stop,
general_question. Plant traversal is **snake (band-by-X, alternate-Y)** for the
shortest path.

---

## 6. How to work (dev loop)

**Sim-verify before hardware — always.** The sim harness runs the full pipeline
with no robot/ROS:

```bash
# from repo root (WSL or any python with py_trees/pydantic)
PYTHONPATH=src python3 -m growmate_pi.verify_sim     # expect Failures: 0/N
```
On Windows the sim runs under WSL: `wsl … ./venv-wsl/bin/python3 -m growmate_pi.verify_sim`.

**Build (on the Pi / ROS env):**
```bash
source /opt/ros/$ROS_DISTRO/setup.bash
colcon build --symlink-install [--allow-overriding ...]
source install/setup.bash
```

**Run one greenhouse:**
```bash
ros2 launch ./launch/greenhouse.launch.py                       # gh1 (default)
ros2 launch ./launch/greenhouse.launch.py config:=…/farmbotdev.yaml scheduler:=false
```

**Deploy to a Pi:** `git pull` on the Pi, rebuild changed packages. Pure-Python
changes (growmate_pi) only need a pull + restart of the intent server.

---

## 7. Adding a capability (the recipe)

1. Add the verb to `schemas.Action`.
2. Add `_tree_<verb>()` in `bt/builder.py` **with the full safety prefix** + new
   nodes in `bt/action_nodes.py` if needed; route it in `build_subtree()`.
3. Robot-touching steps go through the **tick-and-verify** gate (`verify=True`).
4. Add it to the **LLM vocabulary** in `ai_core.py` (ACTIONS + prompt rule +
   examples). Keep the LLM output **flat** — parse any structure in the builder,
   don't push nested params onto a small model.
5. Add a `verify_sim` scenario; keep **USC = 0**.
6. Sim-verify, then hardware-verify. Update PLANS/scoreboard.

---

## 8. Conventions & gotchas

- **Per-greenhouse config**: `config/gh1.yaml` (Maynooth) / `config/farmbotdev.yaml`
  selected by the launch `config:=` arg. Operational plant/tool data lives per-Pi
  in `active_map.yaml` (map_handler), not in the garden config.
- **PYTHONPATH must be *prepended***, not replaced (`src:$PYTHONPATH`), or the
  venv intent server loses `rclpy` and silently falls back to sim. The launch and
  RUN_GUIDE encode this.
- **Pin codes**: pin 59 = soil sensor (`D_S_C` → `R41 P59`); pin 63 = UTM tool
  detection (`D_C` → `R41 P63`; 0 = mounted, 1 = empty).
- **Tool change** is verified by polling pin 63 through the (~20 s) mount
  choreography, not a one-shot read.
- **Firmware params** are written via `F22 P<id> V<val>` (e.g. P36/P37 = second
  X-axis motor enable/invert on the XL).
- Commit messages: imperative, explain *why*; note any AURA-stack edit as a
  bugfix.

---

## 9. Hardware

Two greenhouses, each its own Raspberry Pi running this stack:
- **gh1** @ `192.168.0.39` — Maynooth, 54-plant map.
- **farmbotdev** @ `192.168.0.54` — second greenhouse (template config).

The Pi venv needs `--system-site-packages` so the intent server can import
`rclpy` (real mode). `bridge_mode: ros2` in `/status` confirms real (not sim).

---

## 10. Current direction (see PLANS.md for the live roadmap)

- Phases water → camera → weeding → seeding: water + camera + weeding are
  **code-complete + sim-verified**; remaining work is hardware validation
  (tool-mount + detection accuracy).
- **On-device LLM**: target architecture is **phone does intent classification
  on-device** (browser web app via **LiteRT-LM / WebGPU**, small Gemma/Qwen +
  constrained decoding), **everything else on the Pi**. The Pi `/intent` contract
  already supports this. First step is a pluggable LLM backend + a benchmark
  (accuracy/latency vs model size) — which is also the headline experiment for
  the framework paper.
- **Two papers** in progress: (1) safety + accessibility for vulnerable users;
  (2) VoiceBT as a portable framework. Both need the on-device + ablation evals.
