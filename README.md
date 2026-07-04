# GrowMate — Voice Control for Agricultural Robots via Inspectable Behaviour Trees

[![ROS2](https://img.shields.io/badge/ROS2-Humble%20%7C%20Jazzy-blue)](https://docs.ros.org/en/humble/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow)](LICENSE.md)

GrowMate is a voice-control interface for the [FarmBot Genesis XL](https://farm.bot/)
agricultural robot, built for elderly and disabled users who can describe what they
want in natural language but cannot operate a keyboard or touchscreen comfortably.

This repository contains:

- **GrowMate** — the voice-control package (`src/growmate_voice/`).
- **VoiceBT** — the underlying framework: LLM-constrained behaviour trees for
  safe robot control.
- **AURA FarmBot ROS2** — the upstream robot stack (`src/` except `growmate_voice/`),
  unchanged. GrowMate is additive: it publishes to the same `keyboard_topic` the
  existing `keyboard_controller` uses, so nothing downstream needed to change.

> MSc thesis: *"GrowMate: Transparent Voice-Robot Interaction through
> LLM-Constructed Behaviour Trees for Accessible Agricultural Robotics"*
> Rishabh Jain · Maynooth University · Supervisor: Dr Majid Sorouri · 2026

---

## Problem

Agricultural robots like FarmBot are capable of watering, photographing, and
tending plants autonomously — but today their interfaces assume a technically
literate user sitting at a desktop. Elderly and disabled users, who stand to
benefit most from autonomous gardening, are effectively locked out.

Voice interfaces exist (Alexa, Siri) but are not designed for robot control:

- They are opaque — the user cannot see what the system understood or what it
  is about to do.
- End-to-end LLM pipelines hallucinate — a single wrong output can drive the
  gantry into the bed wall or activate the pump at the wrong plant.
- On-device 2–4 B parameter models, small enough to run on a Raspberry Pi,
  are not reliable enough to generate structured action plans directly.

**The gap:** a voice interface that accepts natural language, is safe to run
unsupervised on a physical robot, and is inspectable — the user (and the
researcher) can verify what the system will do before it does it.

---

## Approach — VoiceBT

VoiceBT is the framework this project contributes. Its key architectural
decision is to **restrict the LLM to flat intent classification** and leave all
structural decisions to deterministic Python code.

```
   spoken utterance
         |
   STT  (faster-whisper / Moonshine / Vosk)
         |
   ┌─────────── LLM (Gemma 3:4b via Ollama) ─────────────┐
   │  Flat intent classification — one JSON object only    │
   │  { action: <FIXED_ENUM>, target: <plant|null>,        │
   │    question: <str|null>, response: <str> }            │
   │  The LLM never produces nested structure.             │
   └──────────────────────────────────────────────────────┘
         |
   Deterministic tree builder (ai_core.py)
   • looks up action in a typed node library
   • prefixes every robot action with safety conditions:
       check_available → [check_bounds] → [check_plant_found] → action
   • tree is pure Python data, inspectable before execution
         |
   BT engine (bt_engine.py)
   • executes nodes: robot_action / function_call / llm_reason / respond
   • safety conditions can abort the tree at any node
         |
   ROS2 publisher → keyboard_topic → FarmBot
         |
   TTS confirmation (Piper / Kokoro)
```

**Why not let the LLM generate the tree?**
Early versions did. On-device 2–4 B models produced valid JSON tree structures
roughly 0% of the time. Flat classification is a task these models handle
reliably; structured recursive generation is not.

**Why behaviour trees?**
BTs are modular, inspectable, and fail-safe. Each node returns
`SUCCESS / FAILURE / RUNNING`. A safety condition that returns `FAILURE` stops
the entire sequence before any physical action occurs.

---

## Implementation

### Repository layout

```
voice-farmbot/
├── src/
│   ├── growmate_voice/           ← GrowMate package (this work)
│   │   ├── growmate_voice/
│   │   │   ├── app.py            FastAPI web UI + voice pipeline
│   │   │   ├── ai_core.py        LLM classifier + tree builder
│   │   │   ├── bt_engine.py      BT executor
│   │   │   ├── ros2_publisher.py keyboard_topic publisher
│   │   │   ├── scheduler.py      Daily watering scheduler
│   │   │   ├── history.py        Persistent command log
│   │   │   ├── logger.py         Rotating file + console logger
│   │   │   ├── stt_test.py       Voice-pipeline workbench (port 7870)
│   │   │   └── edgespeech/       STT/TTS backends
│   │   │       ├── stt/          faster-whisper · vosk · moonshine
│   │   │       └── tts/          piper · kokoro
│   │   └── config/
│   │       └── farmbot.yaml      Garden map, schedule, safety bounds
│   ├── farmbot_bringup/          ┐
│   ├── farmbot_controllers/      │  Upstream AURA FarmBot ROS2.
│   ├── farmbot_command_handler/  │  Unchanged. Read-only.
│   ├── farmbot_interfaces/       │
│   ├── map_handler/              │
│   └── camera_handler/           ┘
├── tools/
│   ├── build_active_map.py       Generate active_map.yaml from CSV / FarmBot API
│   ├── calibrate.py              Capture real plant positions via the GrowMate UI
│   ├── evaluate_v2.py            29-utterance corpus eval against the Pi intent server
│   ├── placements.csv            Current garden's plant list (species, x, y, z)
│   └── maps/                     Saved garden snapshots
└── demo/
    ├── INTEGRATION_GUIDE.md      How to drive the FarmBot from your own code
    ├── presentation_leipzig.md   8-slide academic talk (VoiceBT framework)
    ├── presentation_dundalk.md   10-slide elder-facing focus-group deck
    ├── questionnaire.md          SUS + custom items + per-utterance log
    └── demo_day_plan.md          Operational plan for 9 June 2026
```

### Safety guarantees

1. **Emergency stop never goes through the LLM.** The words `stop`, `halt`,
   `emergency`, `freeze`, `abort` are string-matched before any LLM call.
   The UI's red button calls `ROS2Publisher.emergency_stop()` directly.
2. **Every robot action is preceded by safety nodes in code** — not by the LLM.
   At minimum `check_available`; for movement also `check_bounds`; for
   plant-targeted actions also `check_plant_found`. Adding an action without the
   safety prefix is a research-claim violation, not just a bug.
3. **`keyboard_topic` is the only ROS2 topic published to.** GrowMate is a
   drop-in, not a fork of the control stack.

### Evaluation

The framework was evaluated on a 29-utterance corpus covering single actions,
multi-intent utterances, indirect speech, and safety triggers, using the
Gugliermo et al. (2024) metric set (names + intent verified against the full
text; operationalized in `tools/evaluate_v2.py`, deviations disclosed in
`documentation/eval/dossier_01_gugliermo_metrics.md` §9, emergent-eval branch):

| Metric | Result |
|---|---|
| DBSR (Desired Behaviour Success Rate) | 96.6 % (28 / 29) |
| SNSR (Single Node Success Rate) | 98.8 % (162 / 164) |
| USC (Unsafe State Count) | 0 |
| Mean end-to-end latency | 5,456 ms |

The single DBSR miss was an indirect-speech utterance mapped to a wrong (but
bounded, safe) action — USC stayed at zero because the safety prefix held.

---

## Requirements

### On the Pi (robot side)

- Raspberry Pi 4 or 5, Ubuntu 22.04 / 24.04
- ROS2 Humble or Jazzy (full install)
- Python ≥ 3.10, venv
- Ollama (`ollama serve`, model `gemma3:4b`)

```bash
pip install -r src/growmate_voice/requirements-pi.txt
```

### On a dev machine (Windows / Linux, no robot)

- Python ≥ 3.10, Anaconda recommended
- Ollama installed and running locally

```bash
pip install -r src/growmate_voice/requirements.txt
```

---

## Quick start

### Simulation (Windows / Linux, no robot)

```bash
cd src/growmate_voice
python -m growmate_voice.app --no-ros2
```

Open `http://localhost:7860`. The mode tag in the header shows `simulation`.
All commands are printed to the terminal instead of published to ROS2.

### Real robot (Pi, ROS2 sourced)

```bash
# Terminal 1 — FarmBot stack
ros2 launch farmbot_bringup standard.launch.py

# Terminal 2 — GrowMate
cd ~/voice-farmbot/src/growmate_voice
python -m growmate_voice.app

# Daily watering scheduler (optional, separate terminal)
python -m growmate_voice.scheduler
```

### Voice-pipeline workbench (iterate on STT/LLM without the robot)

```bash
python -m growmate_voice.stt_test   # opens at http://localhost:7870
```

Type or record an utterance. The workbench shows the raw transcript, the
AICore-constructed behaviour tree in ASCII, and the FarmBot commands the tree
would emit — without publishing anything to the robot.

### Evaluate the framework

Against the V2 Pi intent server (42-case corpus; metrics defined in the
harness itself):

```bash
python tools/evaluate_v2.py --pi-url http://<pi>:8000/intent
```

---

## Map calibration

The map of plant positions lives in
`install/map_handler/share/map_handler/config/active_map.yaml` on the Pi
(runtime) and is authored with the tools in `tools/`.

```bash
# 1. Start the GrowMate app (app.py) in one terminal.
# 2. In a second terminal, run the calibrator:
python tools/calibrate.py --url http://localhost:7860

# Jog the gantry over each plant in the browser, type the species name.
# When done, build and deploy:
python tools/build_active_map.py --mode csv --csv tools/placements.csv \
       --out tools/active_map.yaml

cp tools/active_map.yaml \
   install/map_handler/share/map_handler/config/active_map.yaml
```

---

## Replicating this on a different robot

VoiceBT is not FarmBot-specific. To adapt it:

1. **Implement a publisher** — replace `ros2_publisher.py` with whatever your
   robot's command interface is (MQTT, HTTP, serial, etc.).
2. **Edit `ai_core.py` → `ACTIONS`** — the fixed action enum. Add or remove
   actions to match your robot's capabilities.
3. **Add tree builders** — add a `_tree_<action>` method in `AICore` for each
   new action, following the canonical pattern:
   ```python
   def _tree_myaction(self, target, resp):
       return {"type": "sequence", "label": "My action", "children": [
           {"type": "condition", "name": "check_available"},
           # add check_bounds / check_plant_found as needed
           {"type": "robot_action", "name": "myaction", "params": {}},
           {"type": "respond", "message": resp},
       ]}
   ```
4. **Edit `config/farmbot.yaml`** — add your plant names, aliases, positions,
   and workspace bounds.
5. **Run `evaluate_bt.py`** with a corpus of utterances for your domain to
   measure DBSR / SNSR / USC before deploying.

The web UI (`app.py`) and BT executor (`bt_engine.py`) require no changes.

---

## Citing this work

If you use VoiceBT or GrowMate in your research, please cite:

```
Jain, R. (2026). GrowMate: Transparent Voice-Robot Interaction through
LLM-Constructed Behaviour Trees for Accessible Agricultural Robotics.
MSc Thesis, Maynooth University. Supervisor: Dr Majid Sorouri.
```

---

## Upstream

The robot control stack in `src/` (except `src/growmate_voice/` and
`src/growmate_pi/`) is the
[AURA FarmBot ROS2](https://github.com/PetriJF/FarmBot_ROS2) project,
developed at Maynooth University. See `documentation/` for the upstream docs.
GrowMate is additive and does not re-architect the control stack. The one
exception is a single upstream **bug fix**: `map_handler/tool_sequencer.py`
`__get_release_direction` checked `dir == 1` four times, so any tool with a
release direction other than 1 crashed the mount sequence — corrected to map
1/2/3/4 (worth reporting upstream).
