# growmate_voice

A FastAPI web app that gives the AURA FarmBot a browser-based jog d-pad and
an offline voice pipeline. Voice commands are matched against a fixed FarmBot
vocabulary (no LLM in the runtime path) and published to `keyboard_topic` —
making this a drop-in companion to `keyboard_controller`.

## What's in the package

```
growmate_voice/
├── app.py                    FastAPI app + embedded HTML/JS UI (entry point: voice_app)
├── ros2_publisher.py         Publishes std_msgs/String to keyboard_topic
├── logger.py                 Rotating-file + ANSI console logger
├── stt_test.py               Terminal CLI for STT/TTS sanity checks
└── edgespeech/
    ├── audio_utils.py        WAV decode/encode, padding, resample
    ├── command_map.py        FarmBot vocabulary, normalisation, fuzzy matcher
    ├── stt/                  Vosk, Faster-Whisper, Moonshine backends
    └── tts/                  Piper, Kokoro backends
```

## Architecture

- **Frontend** (HTML embedded in `app.py`): browser records mono PCM at 16 kHz
  via the Web Audio API, encodes a WAV client-side, POSTs it to `/api/voice`
  as multipart form data.
- **Backend** (`/api/voice`): decodes WAV → runs STT → matches command →
  publishes the corresponding FarmBot command(s) to `keyboard_topic` →
  synthesises a TTS confirmation → returns JSON with the result and a
  base64-encoded TTS WAV.
- **TTS playback**: inlined as `data:audio/wav;base64,...` in an `<audio>` tag —
  no HTTP streaming, no static file serving.

The same UI exposes a manual jog d-pad, FarmBot bringup controls
(`Power ON / OFF / Check Status`), and a hardware-level emergency stop that
bypasses STT entirely.

## Voice vocabulary

| Action     | Spoken variants                                | FarmBot emission |
|------------|------------------------------------------------|------------------|
| `estop`    | stop, emergency, halt, freeze, abort            | `e`              |
| `reset`    | reset, resume, clear stop                       | `E`              |
| `home`     | home, go home, return home                      | `H_0`            |
| `x_plus`   | right, x plus, x+                               | `M …` (jog +X)   |
| `x_minus`  | left, x minus, x-                               | `M …` (jog −X)   |
| `y_plus`   | forward, ahead, y plus, y+                      | `M …` (jog +Y)   |
| `y_minus`  | back, backward, y minus, y-                     | `M …` (jog −Y)   |
| `z_plus`   | up, raise, arm up, z plus                       | `M …` (jog +Z)   |
| `z_minus`  | down, lower, arm down, z minus                  | `M …` (jog −Z)   |
| `water`    | water, water plant, water the plants            | `P_4`            |
| `photo`    | take photo, capture, take picture               | `I_1`            |

Matching is exact-substring first, then fuzzy via `difflib.SequenceMatcher`
with a 0.70 threshold. Common homophones (`why plus → y plus`,
`zee plus → z plus`) are normalised before matching.

## Running on Windows (development)

```powershell
conda activate moderation
pip install -r src\growmate_voice\requirements.txt
cd src\growmate_voice
python -m growmate_voice.app --no-ros2
```

Then open `http://127.0.0.1:7860`. The header tag shows
`simulation` (commands are printed) or `ROS2 live`.

### Optional STT / TTS backends

Faster-Whisper auto-downloads `tiny.en` on first use. The other backends need
a one-time model download:

```powershell
# Vosk small EN (~40 MB)
mkdir C:\Users\risha\edge_models
curl -L -o C:\Users\risha\edge_models\vosk-small-en.zip ^
  https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
powershell -c "Expand-Archive C:\Users\risha\edge_models\vosk-small-en.zip -DestinationPath C:\Users\risha\edge_models\"
setx VOSK_MODEL_PATH "C:\Users\risha\edge_models\vosk-model-small-en-us-0.15"

# Piper voice (~60 MB)
curl -L -o C:\Users\risha\edge_models\en_US-lessac-medium.onnx ^
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o C:\Users\risha\edge_models\en_US-lessac-medium.onnx.json ^
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
setx PIPER_VOICE_MODEL "C:\Users\risha\edge_models\en_US-lessac-medium.onnx"
setx PIPER_VOICE_CONFIG "C:\Users\risha\edge_models\en_US-lessac-medium.onnx.json"
```

Reopen the terminal so `setx` takes effect.

## Running on the Pi (real robot)

```bash
cd ~/farmbot_ws
colcon build --packages-select growmate_voice
source install/setup.bash

# Start the FarmBot stack first
ros2 launch farmbot_bringup standard.launch.py
# In a second terminal:
ros2 launch growmate_voice growmate_voice.launch.py
# or, both in one shot:
ros2 launch growmate_voice growmate_voice.launch.py with_farmbot:=true
```

## HTTP API

| Method | Path                  | Purpose                                              |
|--------|-----------------------|------------------------------------------------------|
| GET    | `/`                   | Single-page HTML UI                                  |
| GET    | `/api/status`         | Position + FarmBot status + ros2 mode                |
| GET    | `/api/commands`       | Full vocabulary as JSON                              |
| POST   | `/api/jog`            | `axis,direction,step` → execute move                 |
| POST   | `/api/estop`          | Hardware emergency stop                              |
| POST   | `/api/reset`          | Clear emergency stop                                 |
| POST   | `/api/farmbot/power`  | `action=on|off|status` for farmbot_bringup           |
| POST   | `/api/voice`          | Multipart `audio` WAV + `stt`, `tts`, `enable_tts`   |

## CLI sanity check

```powershell
python -m growmate_voice.stt_test --stt whisper  --mode mic
python -m growmate_voice.stt_test --stt vosk     --mode file --audio sample.wav
python -m growmate_voice.stt_test --stt moonshine --tts piper --mode mic
```

Logs go to `~/.growmate_voice/logs/growmate.log` (rotating, 2 MB × 3).
