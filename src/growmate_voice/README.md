# growmate_voice

Browser/desktop client for GrowMate. It provides the web UI, microphone/STT,
LLM intent classification, TTS, manual controls, and the HTTP bridge to the Pi
intent server.

The current runtime path **does include an LLM**:

```text
browser mic/text -> STT -> AICore (gemma3:4b via Ollama)
                 -> flat IntentRequest JSON -> Pi /intent
                 -> task polling / honest terminal outcome -> TTS
```

The research boundary is that the LLM only classifies a flat intent:
`{action, target, params, response}`. It does not emit behaviour trees, plans,
code, command strings, or control flow.

## What Lives Here

```text
growmate_voice/
  app.py              FastAPI app and embedded browser UI
  ai_core.py          LLM classifier and prompt rules
  pi_client.py        HTTP client for growmate_pi
  ros2_publisher.py   Legacy/direct publisher path
  scheduler.py        Legacy scheduler path
  logger.py           Rotating-file and console logger
  stt_test.py         STT/TTS workbench
  edgespeech/
    command_map.py    Emergency/pattern fast paths and normalisation
    stt/              Vosk, Faster-Whisper, Moonshine backends
    tts/              Piper, Kokoro backends
```

## Runtime Modes

| Mode | Command shape | Use |
|---|---|---|
| Pi-backed V2 | `--no-ros2 --pi-url http://<pi>:8000/intent` | Normal GrowMate architecture: this app classifies, Pi builds/ticks BTs. |
| Local sim | Pi intent server running with `--no-ros2` | Development/eval without a robot. |
| Legacy/direct ROS2 | no `--pi-url` and ROS sourced | Older path; keep for manual testing, not the thesis architecture. |

## Running On Windows

```powershell
cd C:\Users\risha\growmate-bt\voice-farmbot
$env:PYTHONPATH = "C:\Users\risha\growmate-bt\voice-farmbot\src;" + $env:PYTHONPATH
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.0.38:8000/intent
```

Open `http://127.0.0.1:7860`.

For local sim, start the Pi server in another terminal:

```powershell
$env:PYTHONPATH = "src"
python -m growmate_pi.intent_server --no-ros2 --port 8123
python -m growmate_voice.app --no-ros2 --pi-url http://localhost:8123/intent
```

## LLM Contract

`AICore.ACTIONS` mirrors `src/growmate_pi/schemas.py`:

- `move`, `water`, `water_all`, `water_smart`
- `go_home`, `light_on`, `light_off`
- `photo`, `panorama`, `scan_weeds`, `clear_weeds`
- `scan_bed`, `find_plants`, `label_plants`
- `check_sensor`, `check_moisture`
- `emergency_stop`, `general_question`

Emergency words such as "stop" are matched before the LLM and go straight to
the Pi emergency endpoint.

## HTTP Surface

| Method | Path | Purpose |
|---|---|---|
| `GET /` | Browser UI |
| `GET /api/status` | App/Pi/robot status and task outcome |
| `POST /api/voice` | Audio upload -> STT -> classify -> dispatch |
| `POST /api/text` | Text command -> classify -> dispatch |
| `POST /api/confirm` | Confirm destructive queued action |
| `POST /api/estop` | Emergency stop |
| `POST /api/reset` | Reset emergency stop |
| `POST /api/jog` | Manual jog from UI |

The final spoken result should come from the Pi's terminal task outcome, not
from a hopeful client-side "done" string.

## STT / TTS Backends

The app supports Faster-Whisper, Vosk, and Moonshine for STT, plus Piper and
Kokoro for TTS. Model download details are intentionally kept out of this
README; use [RUNBOOK.md](../../RUNBOOK.md) when setting up a demo machine.

## Development Rule

Changing prompt rules or `AICore.ACTIONS` is evaluation-sensitive. Re-run at
least the relevant corpus slice, and for broad prompt changes re-run the full
evaluation or extended corpus before quoting numbers.
