# GrowMate -- Behaviour Tree Voice Assistant for Agricultural Robots

Voice assistant for FarmBot where every interaction is an inspectable behaviour tree.

## Architecture

```
Voice -> STT -> AI Core (LLM classifies intent)
                    |
                    v
              BT Builder (constructs tree from node library)
                    |
                    v
              BT Engine (executes nodes: FarmBot API / weather / LLM reasoning)
                    |
                    v
              Robot Controller (publishes to FarmBot ROS2 keyboard_topic)
                    |
                    v
              TTS Feedback (spoken response to user)
```

## Quick Start

```bash
pip install pyyaml

ollama pull gemma3:4b

python main.py --text --model gemma3:4b

python evaluate_bt.py --model gemma3:4b

python evaluate_bt.py --model gemma3:4b --dump-trees bt_dump.txt
```

## Node Types

| Category        | Nodes                                                | Output            |
|-----------------|------------------------------------------------------|-------------------|
| Robot Actions   | move_to, water, go_home, read_sensor, light, photo   | FarmBot commands  |
| Function Calls  | fetch_weather, get_plant_info, get_farmbot_plant     | Stored variables  |
| LLM Reasoning   | reason, respond                                      | Text responses    |
| Safety          | check_available, check_bounds, check_plant_found     | Pass/Fail         |
| Control         | sequence, selector, confirm, wait                    | Flow control      |

## FarmBot Integration

Trees query the FarmBot REST API for live plant coordinates via get_farmbot_plant().
Falls back to config/farmbot.yaml if the API is unreachable.

## Evaluation Framework

Based on Gugliermo et al. (2024) "Evaluating Behavior Trees":
- Desired Behavior Success Rate (DBSR)
- Single Node Success Rate (SNSR)
- Unsafe State Count (USC)
- Tree Dimensions, Action Granularity
- Safety and Transparency Coverage
