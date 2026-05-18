# growmate_pi

Pi-side HTTP server for GrowMate V2.

The phone (or any client) sends an `IntentRequest` JSON to `POST /intent`.
The server builds a [py_trees](https://py-trees.readthedocs.io/) behaviour
tree from the intents, ticks it, and publishes FarmBot command strings to
`keyboard_topic` — the same wire format the upstream AURA
`keyboard_controller` uses.

## Architecture at a glance

```
phone/browser  ──▶  POST /intent  ──▶  intent_server.py
                                          │
                                          ▼
                                       builder.py  (Intent JSON → py_trees tree)
                                          │
                                          ▼
                                       executor.py  (tick to completion)
                                          │
                                          ▼
                              FarmBotROS2Bridge  ──▶  keyboard_topic  (or SIM stdout)
```

## Layout

```
growmate_pi/
├── schemas.py                 frozen wire-format (Intent, IntentRequest, IntentResponse)
├── farmbot_ros2_bridge.py     single ROS2 publisher (sim or real)
├── garden_config.py           reads farmbot.yaml; resolves plant names → coords
├── intent_server.py           FastAPI app: /intent, /estop, /reset_estop, /status, /history
├── pi_client.py               thin HTTP client (used by growmate_voice.app and the eval)
├── scheduler.py               daily watering daemon — POSTs water_all once a day
├── verify_sim.py              run end-to-end sim without rclpy or FarmBot
├── bt/
│   ├── condition_nodes.py     CheckAvailable, CheckBounds, CheckPlantFound, ResolveTarget
│   ├── action_nodes.py        PublishCmd, MoveTo, Wait, Respond, EmergencyStop, ReadSensor
│   ├── builder.py             Intent JSON → py_trees subtree
│   └── executor.py            tick loop + TreeResult aggregation
├── mission/
│   └── plansys2_controller.py PDDL-backed multi-step planner (stubbed; enable when needed)
├── pddl/
│   └── farmbot_domain.pddl    domain for the PlanSys2 mission controller
├── config/
│   └── farmbot.yaml           garden config (plants, coords, bounds, schedule)
└── requirements.txt
```

## Running

### Pi-side install

```bash
sudo apt install ros-humble-py-trees ros-humble-py-trees-ros python3-pip
source /opt/ros/humble/setup.bash
python3 -m pip install --user -r src/growmate_pi/requirements.txt
```

### Sim mode (Windows/WSL dev, no robot)

```bash
PYTHONPATH=src python3 -m growmate_pi.intent_server --no-ros2
```

### Real mode (on the Pi, with FarmBot bringup running)

```bash
source /opt/ros/humble/setup.bash
source ~/farmbot_ws/install/setup.bash       # FarmBot ROS2 workspace
PYTHONPATH=src python3 -m growmate_pi.intent_server
```

The server listens on `0.0.0.0:8000` by default.

### End-to-end sim (no network)

```bash
PYTHONPATH=src python3 -m growmate_pi.verify_sim
```

Walks 8 representative intents through the full build → tick path with
the bridge in sim mode. Prints the FarmBot commands that *would* be
published. Exit code 0 on success.

### Calling the server

```bash
curl -X POST http://localhost:8000/intent -H 'Content-Type: application/json' -d '{
  "intents": [{"action": "water", "target": "tomatoes", "response": "Watering tomatoes!"}],
  "raw_text": "water the tomatoes",
  "client_id": "demo"
}'
```

`/estop` and `/reset_estop` are POST-only; no body required.

## Architectural rules (mirrors CLAUDE.md §6)

1. **The LLM never runs here.** Intent classification is the client's job —
   we only consume the structured `IntentRequest`.
2. **Every robot action is preceded by safety nodes.** `CheckAvailable` →
   `ResolveTarget` → `CheckPlantFound` → `CheckBounds` → ... → action. The
   builder is the only place this prefix lives; do not bypass it.
3. **Emergency stop never goes through the BT.** `/estop` calls
   `FarmBotROS2Bridge.emergency_stop()` directly. The `emergency_stop`
   intent type, when it does flow through `build_tree`, publishes `e`
   without other side effects.
4. **`keyboard_topic` is the only ROS2 topic published.** The whole point
   of V2 is to remain a drop-in replacement for the existing
   `keyboard_controller`.
5. **Plant names, aliases, and coords live in `config/farmbot.yaml`.**
   Never hard-code coordinates in Python.

## Development status

| Component                  | Status |
|----------------------------|--------|
| Schema, bridge, BT nodes   | Done — runs in sim |
| Intent server endpoints    | Done — `/intent`, `/estop`, `/reset_estop`, `/status`, `/history` |
| Scheduler                  | Done — fires `water_all` via HTTP |
| `verify_sim.py`            | Done |
| `--pi-url` wiring in `growmate_voice.app` | Done |
| `tools/evaluate_v2.py`     | Done — needs running Pi |
| PlanSys2 mission controller| Stubbed — wiring deferred until first multi-step demo |
| Real-hardware test         | Pending physical FarmBot |
