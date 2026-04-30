# GrowMate Voice — Claude Code Guide (Windows dev)

**Read this before making any change.** This file is the handshake between me (the human, Rishabh) and you (Claude in VS Code). It tells you what I'm actually running, what the code is for, and what rules to follow. If a user request conflicts with anything in this file, stop and ask before proceeding.

---

## 1. Who I am and what I'm doing

I'm an MSc student at Maynooth University building a voice-controlled interface for the FarmBot Genesis XL agricultural robot, for elderly and disabled users. The research contribution is a **framework** (working name VoiceBT, positioned in the paper) that restricts an on-device LLM to flat intent classification and assembles inspectable behaviour trees in deterministic code. **GrowMate** is the FarmBot-specific instantiation — the proof-of-concept that validates the framework.

When I ask for code, I'm usually trying to do one of four things:
1. Make the Gradio app work end-to-end on my machine (this is the current focus).
2. Reproduce the paper's 29-utterance evaluation corpus.
3. Prepare for demo day on **9 June 2026** with the Dundalk focus-group participants.
4. Write or rewrite sections of the thesis / paper — in which case I'll be explicit and you shouldn't touch code.

If my request is ambiguous, assume the first one.

---

## 2. My development environment (concrete)

- **OS:** Windows (not WSL, not Linux VM — plain Windows with PowerShell / Command Prompt).
- **Python:** Anaconda, environment name **`moderation`**. Activate with `conda activate moderation`.
- **Ollama:** installed and running locally. `ollama serve` binds to `http://localhost:11434`. The model tag I use is **`gemma3:4b`** (4-billion-parameter Gemma 3). If I say "Gemma 4" I still mean `gemma3:4b` — there is no Gemma 4 yet.
- **No ROS2 on this machine.** This is important: `rclpy` will not import, and nothing in `src/growmate_voice/` that touches ROS2 will work as a publish-to-real-robot path. The code already handles this gracefully — `ROS2Publisher(ros2_enabled=False)` runs in simulation mode and prints commands instead. The Gradio app takes `--no-ros2` which flips this flag.
- **FarmBot:** lives on a Raspberry Pi elsewhere on my network. I currently SSH into it to run `ros2 launch farmbot_bringup standard.launch.py`. The merged repo has the AURA `FarmBot_ROS2` source tree inside it at `src/` so that one day I can build and deploy the voice package there too, but **on this Windows machine I only run the simulation path**.
- **Editor:** VS Code with the Claude extension (you).

### Gotchas specific to this setup

- `pyaudio` does not install cleanly on Windows via `pip` in most cases. Do **not** suggest `pip install pyaudio` unless I ask — the Gradio app doesn't need it. Browser mic is the audio input path.
- Paths are Windows-style. When you write or edit file paths, use forward slashes (`src/growmate_voice/...`) which Python handles fine on Windows, rather than backslashes which escape badly.
- The `colcon build` commands in the package README **do not apply on this machine**. Those are for the Pi. Ignore them when helping me work here.
- Ollama on Windows runs as a background service. If `curl http://localhost:11434/api/tags` in PowerShell returns a JSON list of models, Ollama is up. If it times out, I need to start it from the Start Menu.

---

## 3. What's in the repo

```
voice-farmbot/
├── README.md                        ← Fork notice + upstream FarmBot README
├── documentation/                   ← Upstream AURA docs (unchanged)
└── src/
    ├── camera_handler/              ┐
    ├── farmbot_bringup/             │
    ├── farmbot_command_handler/     │  Upstream AURA FarmBot ROS2 tree.
    ├── farmbot_controllers/         │  Unchanged. Do NOT edit anything here.
    ├── farmbot_interfaces/          │  Treat as read-only.
    ├── hot_water_sprayer/           │
    ├── map_handler/                 │
    ├── multicam_pointcloud/         ┘
    └── growmate_voice/              ← My package. Edit anything here.
        ├── README.md                (install + run for Pi deployment)
        ├── requirements.txt         (pip deps for this env)
        ├── package.xml              (ROS2 manifest, ignored on Windows)
        ├── setup.py                 (colcon entry points, ignored on Windows)
        ├── setup.cfg
        ├── resource/growmate_voice  (empty marker file, ament wants it)
        ├── config/
        │   └── farmbot.yaml         (garden, plants, commands, safety)
        ├── launch/
        │   └── growmate_voice.launch.py  (ros2 launch file, ignored on Windows)
        └── growmate_voice/
            ├── __init__.py
            ├── ai_core.py           (LLM intent classifier + deterministic tree builder)
            ├── bt_engine.py         (BT executor: robot actions, fn calls, reasoning, safety)
            ├── ros2_publisher.py    (publishes to keyboard_topic; sim mode on Windows)
            ├── speech.py            (STT: faster-whisper; TTS: pyttsx3/Piper)
            ├── app.py               (Gradio web app — THIS is what I run on Windows)
            ├── voice_controller_node.py  (headless ROS2 node — Pi only)
            └── cli.py               (desktop CLI, forces --no-ros2)
```

**On Windows, the only thing I actually run is `app.py` (via `voice_app` entry point or `python -m growmate_voice.app`) and sometimes `cli.py` for quick tests.**

---

## 4. Day-one commands (exactly what I type)

### First-time setup

```powershell
# 1. I've already extracted voice-farmbot.tar.gz somewhere.
#    Let's call that folder %REPO%. In my case it's:
cd C:\path\to\voice-farmbot

# 2. Activate the conda environment
conda activate moderation

# 3. Install the Python deps for this package
pip install -r src\growmate_voice\requirements.txt

# 4. Make sure Ollama has the model
ollama pull gemma3:4b

# 5. Make sure Ollama is serving
curl http://localhost:11434/api/tags
```

### Running the Gradio app (simulation mode — no real robot)

```powershell
conda activate moderation
cd C:\path\to\voice-farmbot\src\growmate_voice
python -m growmate_voice.app --no-ros2 --model gemma3:4b
```

Then open `http://localhost:7860` in a browser. The mic will work because it's localhost.

### Running the headless CLI (text only, no browser)

```powershell
cd C:\path\to\voice-farmbot\src\growmate_voice
python -m growmate_voice.cli --model gemma3:4b
```

---

## 5. What I most often want help with in VS Code

In rough order of frequency:

**Fixing imports and paths.** When I move a module or add a new one, imports break. You should read the whole file (not just the error) before proposing a fix, because some modules are deliberately lazy-imported (`rclpy`, `faster_whisper`, `gradio`) and should stay that way — see §7.

**Adding or modifying tree builders.** Most new FarmBot actions are added by writing a `_tree_xxx` method in `ai_core.py`, adding the action string to `AICore.ACTIONS`, and adding an example to `_classify_prompt`. Safety nodes go in first (`check_available`, `check_bounds`, `check_plant_found`), then the action nodes. See `_tree_water` for the canonical pattern.

**Tuning the LLM prompt.** `AICore._classify_prompt` is the single biggest lever for classification accuracy. When I ask to "make it handle X better," I usually mean adding examples to that prompt, not changing the model.

**Evaluation.** The paper's 29-utterance corpus is defined inline in a separate `evaluate_bt.py` that I haven't ported into the ROS2 package yet. If I ask to "run the eval," we probably need to port it over first.

**The Gradio UI.** Layout tweaks, fixing the confirmation flow, making the tree visualisation clearer. `app.py` is the only file for this.

---

## 6. Architectural rules (non-negotiable)

These are the rules the research argument depends on. Do not violate them even if a user request seems to ask for it — ask me to clarify first.

1. **The LLM is used only for flat intent classification.** It never generates nested JSON tree structures. Deterministic Python code in `AICore._intent_to_tree` and friends builds the tree. We tried the other way early in development and on-device 2–4B models produced valid JSON roughly zero percent of the time.
2. **Every robot-action node is preceded by safety nodes.** At minimum `check_available`; for movement also `check_bounds`; for plant-targeted actions also `check_plant_found`. Adding a new action without the safety prefix is a research-claim violation, not just a bug.
3. **Emergency stop never goes through the LLM.** The phrases `stop`, `halt`, `emergency`, `emergency stop`, `freeze`, `abort` are string-matched in `AICore._is_emergency` *before* any LLM call. The Gradio e-stop button calls `ROS2Publisher.emergency_stop()` directly, bypassing the BT engine.
4. **`keyboard_topic` is the only ROS2 topic we publish to.** This is how GrowMate stays a drop-in replacement for the existing `keyboard_controller`. Do not add new topics without a discussion.
5. **Plant names, aliases, and coordinates live in `config/farmbot.yaml`, not in Python.** The prompt and the alias lookup are built from this file at runtime.
6. **The upstream AURA code in `src/` (everything except `growmate_voice/`) is read-only.** If you think it needs to change, propose the change to me first.

---

## 7. Coding conventions

- Python 3.10+. Type hints on all public functions.
- Standard library first; the only hard deps are `pyyaml`, `gradio`, `faster-whisper`, `pyttsx3`. HTTP calls to Ollama use `urllib.request`, not `requests`.
- Lazy-import heavy optional deps inside methods (`rclpy`, `faster_whisper`, `pyttsx3`, `piper`). The package must import on Windows without any of them.
- Result objects for expected failures (see `NodeResult`, `TreeResult`). Exceptions only for programming errors.
- Black-style formatting, 88-char lines, Google docstrings.
- Do not use emojis in Python code unless rendering to the Gradio UI (icons in `_walk_tree` are OK). Terminal output should use ASCII box-drawing or plain text.

---

## 8. The Gemma model, clearly

I've said "Gemma 4" informally a few times. To be precise:

- **The model string is `gemma3:4b`** (Gemma 3, 4 billion parameters). This is what Ollama recognises. This is what the paper evaluates on.
- There are other tags I might try: `gemma3n:e2b` (mobile-optimised, smaller, faster) and eventually whatever Google releases next. When I ask you to "try a different model," confirm the Ollama tag string with me before running `ollama pull`.
- **Do not assume "Gemma 4" means some newer model you haven't heard of.** Ask.

---

## 9. What I expect from you

- **Read the whole file before you change it.** `ai_core.py` is 347 lines; `bt_engine.py` is 412. These aren't too long to read.
- **Propose small diffs, not rewrites.** If a function is 20 lines, don't rewrite 80 lines around it.
- **Run the smoke test path in your head before handing me code.** The smoke test is: `python -m growmate_voice.app --no-ros2 --model gemma3:4b` starts and shows `Listening on http://0.0.0.0:7860` without exceptions. If a change could break this, flag it.
- **When you're uncertain about my environment, ask.** Especially paths, conda vs system Python, and which terminal I'm in (PowerShell vs conda prompt behave differently).
- **Don't touch `src/camera_handler`, `src/farmbot_*`, `src/map_handler`, `src/multicam_*`, `src/hot_water_sprayer`.** Those are upstream.
- **Don't add dependencies casually.** If you want a new library, explain why the stdlib can't do it.

---

## 10. Things I know are broken or unfinished

So you don't propose "fixes" for things that are deliberately pending:

- **No WiFi heartbeat / deadman yet.** The paper mentions it, my thesis will, but it isn't coded. If I ask about "what happens when WiFi dies," the honest answer today is: nothing detects it except the next `check_available` call.
- **Phone mic over LAN needs HTTPS.** Safari and Chrome block mic access on plain `http://<ip>:7860` from non-localhost origins. Workaround for demo day TBD — likely Gradio `--share` or an HTTPS proxy.
- **No tests/ directory yet.** I should add one; low priority until after demo day.
- **No port of `evaluate_bt.py` into the new package structure.** The original lived at the root of `growmate-bt/`. When I ask to "run the eval" we need to copy it across and update imports.
- **The confirmation flow in the Gradio app re-publishes commands but doesn't re-run safety nodes.** This is a small correctness gap I should close eventually — it means `confirm` only gates broad-scope actions at the *intent* level, not re-validating bounds after the user says yes.
- **No session memory.** Each utterance is classified independently. "No, the herbs" after "water the tomatoes" is treated as a new independent utterance, not a correction.

---

## 11. Quick reference: the AURA FarmBot command vocabulary

This is the string protocol that ends up on `keyboard_topic`. It is **not** G-code.

| Code | Meaning |
|---|---|
| `M x y z` | Move gantry to absolute position (mm) |
| `M_S x y z s` | Move with speed % |
| `H_0` | Go to home position |
| `e` | **Emergency stop** (lowercase) |
| `E` | Reset emergency stop (uppercase) |
| `P_4` | Water all plants |
| `P_5` | Water plants by moisture |
| `P_9` | Check moisture levels |
| `D_W_1` / `D_W_0` | Water pump on / off |
| `D_L_1` / `D_L_0` | LED strip on / off |
| `D_S_C` | Read soil sensor |
| `I_1` / `I_2` / `I_4` | Photo / panorama / weed scan |

FarmBot Genesis XL workspace bounds (defined in `config/farmbot.yaml`):
- X: 0 to 5691.2 mm  (long axis)
- Y: 0 to 2734.0 mm  (short axis)
- Z: -500 to 0 mm    (Z is negative downward)

---

## 12. When I'm working on the paper or thesis

Say "I'm working on the paper" or "I'm writing thesis chapter N" at the start of the request. In that mode:

- No code changes unless I explicitly ask for one.
- Match the voice guidelines: mostly "we," occasional "I noticed," at least one moment of genuine uncertainty per major section.
- Limitations stay unresolved — don't pair every one with an immediate fix.
- Evaluation numbers: DBSR 96.6% (28/29), SNSR 98.8% (162/164), USC 0, latency 5,456 ms mean. Don't restate any single number more than once per section.
- Banned phrases: "we propose a different approach," "the key finding is," "In conclusion," "Furthermore/Moreover as paragraph openers," "seamlessly," "plays a crucial role."

---

*MSc Robotics and Embedded AI, Maynooth University. Supervisor: Dr Majid Sorouri. Thesis submission: 19 August 2026. Demo day: 9 June 2026.*
