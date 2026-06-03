# Adding GrowMate's intent server to your FarmBot project

You already publish raw command strings to `/keyboard_topic` from your own code.
This guide adds a semantic API layer **next to** your existing publisher — no
removal needed. Once the new endpoint works, you can migrate your app one
call at a time, or never.

After this you'll be able to do:

```bash
curl -X POST http://localhost:8000/intent \
  -H 'Content-Type: application/json' \
  -d '{"intents":[{"action":"water","target":"tomatoes","response":"Watering"}],
       "raw_text":"water tomatoes","client_id":"my-app"}'
```

and the intent server will publish the right sequence (`M 400 200 -100`,
`D_W_1`, `D_W_0`) for you, with bounds-checking and safety guards in place.

---

## 0. Prerequisites

On the Pi:

- ROS 2 (Humble or Jazzy — whatever your existing setup uses) sourced and working
- The AURA FarmBot stack installed and running (you already have this)
- Python ≥ 3.10
- `git` and `python3-venv`

If you're not sure ROS 2 is sourced:

```bash
echo $ROS_DISTRO        # should print humble, jazzy, etc.
ros2 topic list         # should show /keyboard_topic among others
```

---

## 1. Pull just the Pi server (sparse checkout)

We only need one folder out of the GrowMate repo — `src/growmate_pi/`. This
clones the whole repo metadata but only checks out that subdirectory.

```bash
cd ~/
git clone --filter=blob:none --no-checkout \
  https://github.com/rishabh12j/voice-farmbot.git growmate
cd growmate
git sparse-checkout init --cone
git sparse-checkout set src/growmate_pi
git checkout main
```

After this you have only:

```
~/growmate/
└── src/
    └── growmate_pi/
        ├── intent_server.py
        ├── farmbot_ros2_bridge.py
        ├── garden_config.py
        ├── schemas.py
        ├── pi_client.py
        ├── bt/
        ├── config/
        │   └── farmbot.yaml          ← edit this for your plants
        └── requirements.txt
```

Updates flow in with `git pull` — your edits to `farmbot.yaml` won't conflict
with upstream code changes.

---

## 2. Create a venv and install deps

`growmate_pi` needs four Python packages on top of your system ROS 2. The venv
inherits ROS 2 via `--system-site-packages`, so `rclpy` and `std_msgs` are
visible without any PYTHONPATH gymnastics.

```bash
cd ~/growmate
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
```

Quick check (still in the activated venv):

```bash
python -c "import rclpy, py_trees, pydantic, fastapi, uvicorn, httpx; print('OK')"
```

---

## 3. Configure your garden

Edit `src/growmate_pi/config/farmbot.yaml`. The relevant bits:

```yaml
robot:
  workspace:
    x_max: 2900        # your bed's long axis in mm
    y_max: 1400        # short axis
    z_min: -300        # negative = into soil
    z_max: 0

garden:
  plants:
    - name: tomatoes
      aliases: [the tomatoes, tomato bed]
      position: {x: 400, y: 200, z: -100}
      water_quantity: 6   # seconds of pump pulse
    - name: lettuce
      aliases: [the lettuce, salad]
      position: {x: 1200, y: 200, z: -100}
      water_quantity: 5
    # …add the rest of your plants here

safety:
  workspace_bounds:
    x: [0, 2900]
    y: [0, 1400]
    z: [-300, 0]
```

Plant names from this file are what you'll send as `target` in intent JSON
(`"water the tomatoes"`).

If you already have a full AURA `active_map.yaml` with 30+ plants and don't
want to re-type them here, you can keep this file small (one representative
plant per species, with aliases) — the intent server only needs the names you
plan to refer to by voice/HTTP.

---

## 4. Run the intent server

In one terminal:

```bash
cd ~/growmate
source /opt/ros/$ROS_DISTRO/setup.bash       # source ROS 2
source venv/bin/activate                      # activate venv
PYTHONPATH=src python -m growmate_pi.intent_server --port 8000
```

Expected first lines:

```
[growmate_pi] Bridge: connected, publishing to 'keyboard_topic'
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

If you see `rclpy unavailable — running in simulation mode`, the venv didn't
get system site packages. Recreate with `--system-site-packages` (step 2) or
prepend `PYTHONPATH=src:/opt/ros/$ROS_DISTRO/lib/python3.10/site-packages` to
the launch command.

**Your existing publisher keeps running unchanged.** Both publishers post to
`/keyboard_topic` and the downstream AURA stack accepts either source.

---

## 5. Verify it works

In a second terminal, sanity check the API:

```bash
curl -s http://localhost:8000/status | python3 -m json.tool
```

Should return:

```json
{
  "ok": true,
  "bridge_mode": "ros2",
  "bridge_ready": true,
  "topic": "keyboard_topic"
}
```

Watch what gets published while you POST commands:

```bash
ros2 topic echo /keyboard_topic
```

Now test the actual flow. From a third terminal, water all plants:

```bash
curl -s -X POST http://localhost:8000/intent \
  -H 'Content-Type: application/json' \
  -d '{"intents":[{"action":"water_all","response":"Watering everything."}],
       "raw_text":"water everything","client_id":"test"}'
```

You should see `P_4` appear in the `ros2 topic echo` window. The FarmBot
controller picks it up exactly as if your old publisher had sent it.

Try a plant-targeted command:

```bash
curl -s -X POST http://localhost:8000/intent \
  -H 'Content-Type: application/json' \
  -d '{"intents":[{"action":"water","target":"tomatoes",
                   "params":{"duration_s":3},"response":"Watering tomatoes!"}],
       "raw_text":"water the tomatoes","client_id":"test"}'
```

Watching `/keyboard_topic` you'll see four messages in order:

```
data: M 400 200 -100
data: D_W_1
data: D_W_0
```

(With the `Wait(3s)` between the two pump messages.) This is exactly the
sequence you'd hand-write in raw commands, but the intent server also runs
`CheckAvailable / CheckBounds / CheckPlantFound` before publishing the `M`,
so a malformed target or out-of-bounds plant fails cleanly rather than
sending the gantry the wrong way.

**Emergency stop** is its own endpoint that bypasses the BT entirely:

```bash
curl -X POST http://localhost:8000/estop          # publishes 'e' instantly
curl -X POST http://localhost:8000/reset_estop    # publishes 'E' to clear
```

---

## 6. Browse the auto-generated API docs

Open this in a browser (replace `<pi>` with the Pi's hostname or IP):

```
http://<pi>:8000/docs
```

You'll see every endpoint with its expected JSON shape and a "Try it out"
button. Use this as the source of truth — it's generated from the schemas
file, so it can't drift.

---

## 7. Migrate your app (incremental)

In your existing code, find places where you publish raw commands. Replace
them one at a time. Examples:

**Watering all plants**

```python
# Before
publisher.publish(String(data="P_4"))

# After
import httpx
httpx.post("http://localhost:8000/intent", json={
    "intents":[{"action":"water_all","response":"Watering all."}],
    "raw_text":"water all","client_id":"myapp"
})
```

**Moving to a specific plant**

```python
# Before (you had to know the coordinates already)
publisher.publish(String(data="M 400 200 -100"))

# After (just name the plant — config has the coordinates)
httpx.post("http://localhost:8000/intent", json={
    "intents":[{"action":"move","target":"tomatoes","response":"Moving."}],
    "raw_text":"move to tomatoes","client_id":"myapp"
})
```

**Moving to arbitrary coordinates**

```python
# Same as before — the intent server accepts explicit x/y/z if the target
# can't (or shouldn't) be resolved from the garden config.
httpx.post("http://localhost:8000/intent", json={
    "intents":[{"action":"move","params":{"x":1500,"y":800,"z":-200},
                "response":"Moving."}],
    "raw_text":"move","client_id":"myapp"
})
```

**Multi-step sequences** (the BT runs them all in order, with safety checks
between each):

```python
httpx.post("http://localhost:8000/intent", json={
    "intents":[
        {"action":"go_home","response":"Heading home."},
        {"action":"light_on","response":"Lights on."},
        {"action":"photo","response":"Taking a panorama."}
    ],
    "raw_text":"home, lights on, then photo","client_id":"myapp"
})
```

You don't have to migrate everything at once. As long as you don't
e-stop from both sides simultaneously, mixing raw publishes and intent
posts is safe — the downstream controller doesn't care which process sent
the string.

---

## 8. (Optional) Run as a service

Once you've validated the workflow, drop this into `/etc/systemd/system/growmate-intent.service`
so it starts on boot:

```ini
[Unit]
Description=GrowMate intent server
After=network.target

[Service]
Type=simple
User=YOUR_PI_USER
WorkingDirectory=/home/YOUR_PI_USER/growmate
Environment="PYTHONPATH=/home/YOUR_PI_USER/growmate/src"
ExecStart=/bin/bash -lc 'source /opt/ros/$ROS_DISTRO/setup.bash && \
  source /home/YOUR_PI_USER/growmate/venv/bin/activate && \
  python -m growmate_pi.intent_server --port 8000'
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now growmate-intent
sudo systemctl status growmate-intent
```

---

## What you've got now

- A semantic API endpoint at `http://<pi>:8000/intent` running alongside
  your existing code
- A garden config (`farmbot.yaml`) where plant names map to coordinates
- Built-in safety guards: bounds check, plant-found check, availability check
- Auto-generated docs at `/docs`
- Tree-trace responses (every reply includes the BT execution path, so you
  can debug what actually happened)
- Endpoint to bypass the BT for e-stop (`POST /estop`)

The framework itself is robot-agnostic — only `farmbot_ros2_bridge.py` and
the action node strings (`D_W_1`, `P_4`, etc.) are FarmBot-specific. If you
ever switch hardware, swap that one file and the rest still works.

---

## Help & gotchas

- **Bridge says `simulation mode`?** The venv can't see `rclpy`. See step 4.
- **Plant target not found?** It must match a `name:` or one of its `aliases:`
  in `farmbot.yaml` (case-insensitive). The Pi logs a clear `unknown target`
  message in the BT trace.
- **Two e-stops sent at once?** Whoever publishes `e` first wins; the second
  is a no-op. Safe but check the logs to confirm the Farmduino received it.
- **Move command publishes `M 0.0 0.0 0.0`?** The plant's `position` has no
  `x/y/z` or your config didn't load — check `farmbot.yaml` paths and
  YAML indentation.
- **Want the full GrowMate web UI too?** Pull `src/growmate_voice` the same
  way (`git sparse-checkout add src/growmate_voice`) and follow its README.
  It's a phone-style web app that handles voice → intent → this server.

Original repo: https://github.com/rishabh12j/voice-farmbot
