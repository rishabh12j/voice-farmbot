# GrowMate Voice — Project Plan

Simple checklist. No timelines. Mark things off as we go.

Legend: `[x]` done · `[ ]` remaining · `[~]` partial / needs review · `[?]` untested (not broken, just not verified yet)

---

## Build

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

---

## Tested — Windows (simulation, no real robot)

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

---

## Tested — Actual hardware (Pi + FarmBot)

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
| `[?]` | LED on/off (`D_L_1` / `D_L_0`) |
| `[?]` | Water pump direct (`D_W_1` / `D_W_0`) |
| `[?]` | Soil sensor read (`D_S_C`) |
| `[?]` | Photo / panorama / weed scan (`I_1` / `I_2` / `I_4`) |
| `[x]` | Scheduler on Pi — once-per-day watering at 08:00 |
| `[ ]` | End-to-end: voice → STT → LLM → BT → FarmBot moves |
| `[ ]` | Phone mic over LAN (needs HTTPS first) |
