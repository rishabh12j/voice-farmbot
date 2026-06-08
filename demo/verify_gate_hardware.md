# Real-robot verification — tick-and-verify gate + full watering flow

Run these on the hardware (gh1 @ `192.168.0.39`, Windows app @ `192.168.0.127`)
to confirm the new **tick-and-verify gate** works and the full
ask → confirm → water-with-overlay → stop flow behaves end-to-end.

The gate makes verified move / pump / home nodes wait for the firmware's
`/busy_state` signal (True→False = "command finished") before reporting
SUCCESS, instead of the old fire-and-forget "success on publish". It is **on
by default**; `--no-verify` turns it off.

> Already verified in sim (app in conda → Pi in WSL): buttons, jog, water,
> the "Should I water all N tomatoes?" confirm, the Plant-n-of-N overlay, and
> e-stop all work, and the gate's RUNNING→SUCCESS path runs. The items below
> are the hardware-only checks that sim can't prove.

---

## 0. Pre-flight (must be true before any test)

```bash
# Bringup running in its own terminal; wait for the startup line:
ros2 launch farmbot_bringup standard.launch.py
#   ... wait for: R99 ARDUINO STARTUP COMPLETE

# Fresh Pi only: calibrate once so moves don't fail bounds (see memory note).
#   send C_0  (drives to endstops, writes axis params), then CONF (captures dims)

# Map actually has plants:
ros2 param get /map_handler plant_details.plant_count   # or inspect active_map.yaml
```

---

## 1. Confirm the gate's signal exists  ← the one new dependency

If `/busy_state` isn't published, every verified move/pump will time out to
`partial`. Prove it's there *before* trusting the gate.

```bash
# Terminal A — watch the signal:
ros2 topic list | grep -E 'busy_state|keyboard_topic|uart_receive'
ros2 topic echo /busy_state

# Terminal B — make the gantry do something:
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'H_0'}"
```

**PASS:** in Terminal A you see `data: true` then `data: false` as the home
move runs. **If you never see `/busy_state`**, skip to §8 (`--no-verify`).

---

## 2. Start the intent server (verify ON)

```bash
cd ~/Rishabh_Growmate_FarmBot          # or wherever the repo lives
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
source venv/bin/activate
PYTHONPATH=src python -m growmate_pi.intent_server --port 8000
```

**Expect** on startup:
`Bridge: connected, publishing to 'keyboard_topic', verifying via 'busy_state' (verify=on)`

```bash
curl -s localhost:8000/status | python3 -m json.tool
#   "bridge_mode": "ros2",  "verify_enabled": true
```

---

## 3. Gate on a single move — the core new behaviour

```bash
# Terminal A: ros2 topic echo /busy_state   (still running)
# Terminal B:
time curl -s -X POST localhost:8000/intent -H 'Content-Type: application/json' \
  -d '{"intents":[{"action":"move","params":{"x":400,"y":200,"z":-100},"response":"Moving."}],"raw_text":"move","client_id":"hw"}'
```

**PASS:** the `time` shows the call took roughly as long as the physical move
(seconds), AND `/busy_state` cycled true→false. The response `status` is
`success`.
**FAIL (old behaviour):** returns in milliseconds — the node isn't waiting.

---

## 4. Single-plant water — honest log only after real completion

Use a species with **1–2** plants for a quick test.

```bash
curl -s -X POST localhost:8000/intent -H 'Content-Type: application/json' \
  -d '{"intents":[{"action":"water","target":"<species>","response":"Watering."}],"raw_text":"water the <species>","client_id":"hw"}'

curl -s 'localhost:8000/events?limit=5' | python3 -m json.tool
```

**PASS:** `/busy_state` cycles once per step (move, pump-on, pump-off), and a
`"watered"` event row appears **only after** the pump cycle finished — not on
publish. That is the honest-log payoff.

---

## 5. Timeout = honest failure (optional)

If the firmware stalls or you physically block the gantry, a verified node
hits its timeout (move 90 s, pump 15 s, home 120 s) and returns FAILURE; the
tree status becomes `partial` and **no** watered row is written. This is the
intended "don't lie about what happened" behaviour.

---

## 6. E-stop mid-run

```bash
# Start a multi-plant water (a bigger species), then:
curl -s -X POST localhost:8000/estop          # gantry halts within ~1 tick
curl -s 'localhost:8000/events?limit=20'       # no rows past the stop point
curl -s -X POST localhost:8000/reset_estop      # re-enable before next command
```

**PASS:** motion stops at the next plant boundary, tree returns `partial`, the
event log has no watered row for the un-finished plant.

---

## 7. Full flow via the app (your scenario) — buttons + voice/text

On Windows in the `moderation` conda env:

```powershell
$env:PYTHONPATH = "C:\Users\risha\growmate-bt\voice-farmbot\src;C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice"
python -m growmate_voice.app --pi-url http://192.168.0.39:8000/intent
# open http://127.0.0.1:7860
```

- **Manual / buttons:** d-pad jog, Z±, Home, Photo, Lights → watch
  `curl localhost:8000/history` on the Pi to see the published commands.
- **Voice/Text:** say/type **"water all the tomatoes"** →
  prompt **"Should I water all 11 tomatoes?"** → say **Yes** →
  the overlay shows **Plant n of N**, the gantry moves to each plant and
  waters it, the gate confirms every step. (Heads-up: the confirm prompt
  expires after **10 s** — `_PENDING_TTL_S` in `app.py` — bump it if elderly
  users need longer.)
- **Red Stop** button halts immediately.

---

## 8. Fallback if `/busy_state` isn't available

```bash
PYTHONPATH=src python -m growmate_pi.intent_server --port 8000 --no-verify
curl -s localhost:8000/status    # "verify_enabled": false
```

Gate off → legacy fire-and-forget (moves/pumps report success on publish).
This is the safe demo fallback if §1 showed no `/busy_state`.

---

## What the gate does / does NOT do

- **DOES:** confirm the robot *finished* each move/pump/home (via `/busy_state`)
  before the BT reports success and before the event log records a watering.
- **DOES NOT yet:** verify the gantry reached the **exact target coordinates**,
  or that a **plant is physically present**. That is the position-based
  "is it there?" check we discussed. It's feasible with the same infra (have
  the bridge also subscribe to `/uart_receive`, parse `R82` position reports,
  and add a `CheckArrived` node comparing current pos to the plant's map coords
  within a tolerance). Before building it, confirm the firmware actually emits
  `R82` and how often:

  ```bash
  ros2 topic echo /uart_receive | grep --line-buffered R82
  # move the gantry and see whether/when R82 X.. Y.. Z.. lines appear
  ```

  If `R82` only appears on an explicit position request, the `CheckArrived`
  node will need to ask for it after each move. Tell me what you see and I'll
  build the position-arrival gate on top of this.
