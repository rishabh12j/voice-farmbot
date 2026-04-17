# growmate_voice

Voice control for the AURA FarmBot ROS2 stack, via LLM-constructed behaviour
trees. This package is a drop-in companion to `farmbot_controllers`: it sits
alongside `keyboard_controller` and publishes to the same `keyboard_topic`,
so the downstream FarmBot controller does not need any changes.

Three ways to run it:

| Mode | Entry point | What you get |
|---|---|---|
| **Gradio web app** | `ros2 run growmate_voice voice_app` | Browser mic, plan preview, confirmation gate, emergency stop. The recommended path. |
| **Headless node** | `ros2 run growmate_voice voice_controller` | Terminal prompt. Drop-in for `keyboard_controller` on display-less Pis. |
| **Desktop CLI** | `ros2 run growmate_voice voice_cli` *(or)* `python -m growmate_voice.cli` | Simulation mode, no ROS2 needed. For prompt iteration and paper evaluation. |

---

## How it fits with the FarmBot stack

The system the original AURA repo ships assumes you SSH into the Pi, run
`ros2 launch farmbot_bringup standard.launch.py` in one terminal, and run
`ros2 run farmbot_controllers keyboard_controller` in another to type
commands like `M 400 200 -100`. The `keyboard_controller` publishes those
raw strings to `keyboard_topic`, and the downstream controller stack
interprets them.

`growmate_voice` replaces just that last step. You still run the bringup
launch file exactly as before. Instead of the keyboard controller, you run
`voice_app` (or `voice_controller`), and that node publishes to the same
`keyboard_topic` — except the strings are synthesised by a behaviour tree
built from natural-language speech. Nothing else in the FarmBot repo is
touched. If voice control breaks, you can stop this node and start
`keyboard_controller` again in its place without rebuilding anything.

---

## Prerequisites

**ROS2** — Humble (Ubuntu 22.04) or Jazzy (Ubuntu 24.04), same as the
rest of the repo. The `farmbot_bringup`, `farmbot_controllers`,
`farmbot_command_handler`, `farmbot_interfaces`, `map_handler`, and
`camera_handler` packages must already build and run on your machine.

**Ollama** — the on-device LLM runtime.

```bash
# Install Ollama (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model. gemma3:4b is what the paper evaluated on.
ollama pull gemma3:4b

# Leave it running in the background. By default it binds to localhost:11434.
ollama serve
```

The model string is a launch argument — pass `model:=gemma3n:e2b` to use
Gemma 3n E2B for mobile-grade deployments, or any other tag you have pulled.

**Python packages** — the voice app needs a few things on top of the
rosdep-installed set:

```bash
# Minimum (headless node only)
pip3 install pyyaml

# For the Gradio web app
pip3 install gradio faster-whisper

# Optional: better TTS. Install one of these.
pip3 install pyttsx3          # cross-platform system voices
# ... or install Piper from https://github.com/rhasspy/piper and
#     set the PIPER_VOICE env var to a downloaded .onnx voice file.
```

`faster-whisper` will download its model the first time you run the app
(the default `tiny.en` is 39 MB).

---

## Build

From the workspace root that contains `src/growmate_voice`:

```bash
colcon build --packages-select growmate_voice
source install/setup.bash
```

---

## Run — option A: Gradio web app

One terminal brings up the FarmBot stack. Another runs the voice app.

```bash
# Terminal 1: FarmBot bringup (unchanged from the existing instructions)
ros2 launch farmbot_bringup standard.launch.py

# Terminal 2: Voice app
ros2 run growmate_voice voice_app --model gemma3:4b
```

Open a browser at `http://<pi-ip>:7860`. Click the mic, say
"water the tomatoes," and the app will:

1. Transcribe the audio with faster-whisper.
2. Show you the transcript.
3. Build a behaviour tree with the LLM-classified intent and the typed
   node library — you see the tree in the UI **before** anything hits
   the robot.
4. Execute the tree node by node. Safety checks run first; if one fails,
   no FarmBot commands are published.
5. Publish the resulting command strings to `keyboard_topic`.
6. Speak the response back through the browser.

The emergency-stop button at the top of the page is wired directly to
`ROS2Publisher.emergency_stop`. It bypasses the LLM, the behaviour tree,
and the safety validator — it just publishes `e` to `keyboard_topic`. This
is the Tier 3 path from the paper.

### Where should the Gradio app run?

Two deployment topologies, both valid:

**On the FarmBot Pi itself.** Simpler. The browser connects over WiFi to
port 7860 on the Pi. LLM inference happens on the Pi (a Pi 5 handles
`gemma3n:e2b` comfortably; `gemma3:4b` is slow but works). No network
hop between the voice controller and the ROS2 topics.

**On a laptop or desktop on the same network.** Faster LLM inference, at
the cost of needing to match `ROS_DOMAIN_ID` across machines. On both
the Pi and the laptop, before any `ros2` command:

```bash
export ROS_DOMAIN_ID=42   # any integer 0-101, same on both
```

You can confirm the laptop sees the Pi's topics with `ros2 topic list` —
`keyboard_topic` should appear once the Pi has started the bringup launch.
Then run the voice app on the laptop; it will publish to the same topic
the Pi is listening on.

### Launch-file shortcut

The same effect in one command, and with the option to bring up the
FarmBot stack from the same launch file:

```bash
# Voice app only (FarmBot already running elsewhere)
ros2 launch growmate_voice growmate_voice.launch.py

# Voice app + full FarmBot stack in one shot
ros2 launch growmate_voice growmate_voice.launch.py with_farmbot:=true

# Different model
ros2 launch growmate_voice growmate_voice.launch.py model:=gemma3n:e2b
```

---

## Run — option B: headless text node

A pure terminal prompt. Use this on a headless Pi, or over SSH, when you
don't want a browser in the loop.

```bash
ros2 launch farmbot_bringup standard.launch.py         # terminal 1
ros2 run growmate_voice voice_controller               # terminal 2
```

It accepts exactly the same natural-language commands as the web app.
Special commands: `tree` dumps the last behaviour tree as JSON, `estop`
publishes `e` directly, `quit` exits. When a plan triggers the
confirmation gate (e.g. "water everything"), the node pauses and waits
for `yes` or `no` before publishing.

This node is the true drop-in for `keyboard_controller`. If you want to
fall back to the keyboard node, just `Ctrl+C` this one and run
`ros2 run farmbot_controllers keyboard_controller` in its place.

---

## Run — option C: desktop CLI without ROS2

For developing on a laptop that doesn't have ROS2 installed, or for
reproducing the paper's 29-utterance evaluation corpus.

```bash
cd src/growmate_voice
python3 -m growmate_voice.cli --model gemma3:4b
```

This forces simulation mode: the publisher prints the command strings it
*would* have sent, instead of publishing them. Everything else — the LLM
classification, the tree builder, the safety validator, the BT engine —
runs exactly as it does against the real robot.

---

## Configuration

The garden configuration lives in `config/farmbot.yaml`. It defines:

- The physical workspace bounds (`robot.workspace`) used by the
  `check_bounds` safety node.
- The list of plants (with names, aliases, positions, and watering
  durations) and garden locations.
- The FarmBot command vocabulary (for the LLM prompt).
- The emergency stop phrases.
- The location (for weather lookups in general-knowledge queries).

Edit this file to match your own bed layout. The new config is picked up
on the next restart of the node. You do not need to rebuild.

---

## Troubleshooting

**`ollama: command not found`** — Install it (see Prerequisites) and make
sure `ollama serve` is running. Check with `curl http://localhost:11434/api/tags`.

**The web app starts but classification fails silently** — Usually means
the Ollama model isn't pulled. Run `ollama pull gemma3:4b`.

**`rclpy unavailable — falling back to simulation mode`** — The Python
`rclpy` binding can't be imported. Either your ROS2 workspace isn't
sourced (`source /opt/ros/humble/setup.bash` then
`source install/setup.bash`), or you're on a machine without ROS2
installed. The node still builds and runs trees, it just doesn't publish
to a real robot.

**Transcription returns empty strings** — Install faster-whisper:
`pip3 install faster-whisper`. Check the console log on app startup: it
prints which STT backend was initialised.

**Browser mic doesn't appear** — Gradio needs HTTPS for mic access on
non-localhost domains. If you're hitting the Pi from a phone over the LAN,
either use `http://<pi-ip>:7860` directly (some browsers allow LAN
HTTP mic access) or use `--share` to get a Gradio tunnel URL.

**FarmBot doesn't move after I say a command** — Look at the web UI's
execution log accordion. If the commands are listed under "Commands
published" and the status is `sent`, the voice node is doing its job;
the issue is downstream. Run `ros2 topic echo keyboard_topic` in another
terminal to confirm messages are arriving. If they are, check the
`farmbot_controller` node's log.

**`water everything` didn't do anything** — It triggers the confirmation
gate. You need to click **Confirm** (web app) or type `yes` (headless)
after the plan is shown. This is deliberate, per the Tier 2 policy in
the paper.

---

## Files

```
growmate_voice/
├── growmate_voice/
│   ├── __init__.py
│   ├── ai_core.py              # LLM intent classifier + tree builder
│   ├── bt_engine.py            # BT executor (robot actions, fn calls, reasoning, safety)
│   ├── ros2_publisher.py       # Drop-in keyboard_topic publisher, sim fallback
│   ├── speech.py               # STT (faster-whisper), TTS (pyttsx3/Piper)
│   ├── app.py                  # Gradio web app entry point (voice_app)
│   ├── voice_controller_node.py# Headless terminal node (voice_controller)
│   └── cli.py                  # Desktop CLI wrapper (voice_cli, --no-ros2)
├── config/
│   └── farmbot.yaml            # Garden, plants, commands, safety
├── launch/
│   └── growmate_voice.launch.py
├── package.xml
├── setup.py
├── setup.cfg
└── README.md
```

---

## Safety notes

The BT engine enforces safety through four node types that are inserted
*before* any robot-action node by the tree builder:

- `check_available` — verify the FarmBot controller is reachable.
- `check_bounds` — coordinates must be inside the workspace box.
- `check_plant_found` — target plant must exist in the FarmBot database.
- `confirm` — pause and ask the user before broad-scope operations.

If any of these returns `Failure`, the enclosing `sequence` halts and
nothing reaches `keyboard_topic`. This is enforced by the engine, not
by convention.

The emergency stop bypasses the BT pipeline entirely. String-matched
phrases (`stop`, `halt`, `emergency`, `freeze`, `abort`) and the UI button
both call `ROS2Publisher.emergency_stop()` directly. Latency budget: under
1 ms between button click and `e` on the wire.

---

*Part of the MSc thesis "GrowMate: Transparent Voice-Robot Interaction
through LLM-Constructed Behaviour Trees for Accessible Agricultural
Robotics," Maynooth University, 2026.*
