# GrowMate-BT — voice control for a FarmBot garden robot

GrowMate-BT is a voice-control layer for a FarmBot Genesis XL garden robot, built
for older and disabled users who can describe what they want but may not be
able to use a keyboard, touchscreen, or coordinate map comfortably.

The research contribution is **GrowMate-BT**: constrain a small on-device LLM to
flat intent classification, then let deterministic, inspectable behaviour trees
own all structure, sequencing, and safety.

> The LLM proposes; the behaviour tree disposes.

## Architecture

```text
browser/phone
  mic/text -> STT -> LLM flat classify -> IntentRequest JSON
                                      -> POST /intent

Raspberry Pi
  intent_server.py -> bt/builder.py -> py_trees executor
                   -> FarmBotROS2Bridge -> keyboard_topic
                   -> AURA FarmBot ROS2 -> Farmduino -> robot
                   -> terminal task outcome -> browser/TTS
```

The LLM output is deliberately small:

```json
{"action": "water", "target": "tomato", "params": {}, "response": "Watering the tomatoes."}
```

It never emits command strings, code, behaviour-tree structure, or plans.

## Safety Contract

Every robot-touching action is built in Python with the guard chain:

```text
CheckAvailable -> [CheckToolMounted] -> [CheckBounds] -> [CheckPlantFound] -> action
```

Other invariants:

- Emergency words such as "stop" are matched before the LLM.
- GrowMate publishes only to `keyboard_topic`, the existing AURA command path.
- A task is reported as complete only after the Pi records the terminal,
  firmware/task outcome.
- The tree is pure data and inspectable before execution.
- USC (Unsafe State Count) must remain 0 even under LLM misclassification.

These are invariants, not preferences. A new capability is a new
`schemas.Action` value plus a `_tree_*` builder carrying the full guard chain —
never new LLM freedom. Adding an action without its guards breaks the research
claim, not just the code.

## Repository Layout

| Path | Purpose |
|---|---|
| `src/growmate_pi/` | Pi-side intent server, schemas, BT builder/nodes, ROS2 bridge, scheduler, greenhouse configs. |
| `src/growmate_voice/` | Browser app, STT/TTS, LLM classifier, Pi client, manual UI. |
| `src/farmbot_*`, `src/map_handler/`, `src/camera_handler/` | AURA FarmBot ROS2 stack. GrowMate edits here are minimal bugfixes only. |
| `launch/greenhouse.launch.py` | One-command greenhouse launch. |
| `tools/` | Calibration, eval harness + corpus, stress test, contract tests, per-greenhouse seed maps. |
| `documentation/` | Thesis argument, evaluation record, related-work dossiers, submitted paper, AURA command references. |

## Current Capabilities

- Move, jog, go home
- Water by species, water all, smart-water by soil reading
- Lights on/off
- Photo, panorama, weed scan
- Scan bed, find plants, label plants into the map
- Clear weeds with tool handling
- Check soil sensor / moisture
- Tool mount/unmount through `EnsureTool`
- Emergency stop and reset
- General/data-grounded questions without robot motion

## Evaluation Snapshot

The corpus and the behaviour tree are the same in simulation and on hardware;
what differs is motion timing and firmware verification.

| Run | Result |
|---|---|
| 2,500-case command corpus (sim, guarded code, trace-level scorer) | **DBSR 94.2%** (93.6% strict), **USC 0**, ELC 100%, 0 harness artifacts |
| Misclassification stress test (sim) | 120 forced-wrong intents below the classifier: **unsafe-motion 0**, honesty violations 0 |
| Hardware validation (physical gh1 FarmBot) | 55 even-mix commands end-to-end: **DBSR 92.78%, USC 0** |

The headline safety result is **USC 0** across 2,500 adversarial commands and 120
forced misclassifications: no command ever entered an unsafe state. Task success
is reported both as DBSR (94.2%) and a stricter DBSR that counts whole-garden
over-actions as failures (93.6%). Reproduce with the harness in
`tools/evaluate_v2.py` against the corpus in `tools/corpus/`. The full per-run
record and the thesis write-up are kept outside this repo.

## Quick Start - Local Sim

```powershell
# from the repo root - Terminal 1: the intent server (brain)
$env:PYTHONPATH = "src"
python -m growmate_pi.intent_server --no-ros2 --port 8123
```

In another terminal:

```powershell
# Terminal 2: the voice app (needs src AND the nested package)
$env:PYTHONPATH = "src;src\growmate_voice"
python -m growmate_voice.app --no-ros2 --pi-url http://localhost:8123/intent
```

Open `http://localhost:7860`. Running the server under WSL, and phone access via a
Cloudflare tunnel, are covered in [RUNBOOK.md](RUNBOOK.md).

## Quick Start - hardware

On the Pi (already built and migrated):

```bash
cd <pi-repo>
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
ros2 launch ./launch/greenhouse.launch.py \
  config:=src/growmate_pi/config/<greenhouse>.yaml scheduler:=false
```

On the PC (Windows), from the repo root:

```powershell
$env:PYTHONPATH = "src;src\growmate_voice"
python -m growmate_voice.app --no-ros2 --pi-url http://<pi-host>:8000/intent
```

That is the short version and it assumes a Pi that is already built and migrated.
The full procedure — clone, update, build, state-dir migration, launch args, how to
confirm real mode vs sim, phone access via a Cloudflare tunnel, and what to do when
it misbehaves — is [RUNBOOK.md](RUNBOOK.md).

## Development Gates

```bash
PYTHONPATH=src python3 -m growmate_pi.verify_sim
python tools/test_wire_grammar.py
python tools/stress_misclassification.py --pi-url http://localhost:8123/intent --n 20 --seed 42
python tools/evaluate_v2.py --pi-url http://localhost:8123/intent --skip-long
```

Run the gates that match the change. BT/schema/node changes require at least
`verify_sim`; prompt/classifier changes require corpus evaluation.

## Key Docs

- [RUNBOOK.md](RUNBOOK.md): the one operational doc — update, build, launch, debug.
- [src/growmate_pi/README.md](src/growmate_pi/README.md): the Pi brain — intent server, BT, bridge.
- [src/growmate_voice/README.md](src/growmate_voice/README.md): the client — STT, classifier, TTS, UI.
- [documentation/High Level Commands.md](documentation/High%20Level%20Commands.md) and
  [Low Level Sequencing Commands.md](documentation/Low%20Level%20Sequencing%20Commands.md):
  the AURA FarmBot command vocabulary this layer speaks.

This repo carries the code, what it is, and how to run it. The thesis write-up,
evaluation records, and roadmap are the author's working material and are kept
out of it deliberately.

## Upstream / License

The FarmBot control stack in `src/` is based on the Maynooth AURA FarmBot ROS2
project. GrowMate is additive on top of it. See [LICENSE.md](LICENSE.md).
