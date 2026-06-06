# GrowMate run guide

Single reference for bringing the whole stack up — Pi (bringup +
keyboard + intent server), Windows (voice app), and the V2 evaluation
harness. Covers everything built through Day 12 of the elderly-UX
sprint. Day 13 (demo-day script + recovery) lives in
[demo_day_run.md](demo_day_run.md) once written.

If you're new to the project, read this front-to-back once. If you
already know the layout, jump to the section you need:

1. [On the Pi — bring the FarmBot online](#1-on-the-pi--bring-the-farmbot-online)
2. [Launch the intent server (Pi)](#2-launch-the-intent-server-pi)
3. [Launch the voice app (Windows)](#3-launch-the-voice-app-windows)
4. [Test order (smallest risk first)](#4-test-order-smallest-risk-first)
5. [Verifying Day 5 / 9 / 10 / 11 UX features](#5-verifying-day-5--9--10--11-ux-features)
6. [Running the V2 evaluation (Day 12)](#6-running-the-v2-evaluation-day-12)
7. [Demo day procedure (Day 13)](#7-demo-day-procedure-day-13)
8. [Common gotchas](#8-common-gotchas)

---

## 1. On the Pi — bring the FarmBot online

Same workflow on a fresh Pi as we used on `gh1` — initialize git on
the existing folder and pull. Skip the clone, everything else is the
same.

### 1.1 Find the existing code and snapshot it

```bash
cd ~/Rishabh_Growmate_FarmBot   # or wherever it lives

# one-shot tarball backup before we touch anything
tar czf ~/backup-$(date +%Y%m%d-%H%M).tar.gz .
ls -lh ~/backup-*.tar.gz
```

### 1.2 Connect to git and pull

If it's already a git repo:

```bash
git status
git pull origin main
```

If `fatal: not a git repository`:

```bash
git init
git remote add origin https://github.com/rishabh12j/voice-farmbot.git
git fetch origin main
git checkout -f main          # -f overwrites local files with the repo version
```

If `git pull` says
`Your local changes would be overwritten by merge: tools/placements.csv`:

```bash
git checkout -- tools/placements.csv
git pull origin main
```

### 1.3 Rebuild and re-source

**The rule: build OUTSIDE the venv, run the intent server INSIDE it.**
The venv has fastapi / py_trees / pydantic / httpx for the runtime, but
its Python is missing the ROS 2 build-time deps (`empy`, `lark`,
`catkin_pkg`). If you build inside the venv, the rosidl message
generation fails with `ModuleNotFoundError: No module named 'em'` and
none of the four overriding packages get produced — which then
silently falls back to the upstream `FarmBot_ROS2` install at runtime.
That's where the "H_0 works but P_4 doesn't" trap from section 8 lives.

So:

```bash
deactivate 2>/dev/null                    # IMPORTANT — out of venv for build
which python3                             # should be /usr/bin/python3, not venv

# One-time per Pi: install ROS 2 message-generation deps if missing
sudo apt update
sudo apt install -y python3-empy python3-lark python3-catkin-pkg python3-numpy

# If this is a returning Pi, wipe stale install/build/log first so
# colcon starts from a clean slate.
rm -rf install build log

source /opt/ros/$ROS_DISTRO/setup.bash
# Source upstream FIRST so colcon knows what it's overriding:
source ~/FarmBot_ROS2/install/setup.bash 2>/dev/null || true

colcon build --symlink-install \
  --allow-overriding farmbot_command_handler farmbot_controllers farmbot_interfaces map_handler

# Source Rishabh LAST so its prefix beats FarmBot_ROS2 on AMENT_PREFIX_PATH:
source install/setup.bash
```

Then verify with section 1.3a's for-loop — every package must end in
`~/Rishabh_Growmate_FarmBot/install/...`.

If the venv doesn't exist yet (first-time on this Pi), create it
AFTER the build succeeds:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install pydantic fastapi 'uvicorn[standard]' httpx py_trees pyyaml
touch venv/COLCON_IGNORE                  # stop colcon from scanning into it
deactivate                                # keep the venv off the build path
```

The venv is needed only by the intent server (section 2). Bringup and
the keyboard controller (sections 1.6, 1.7) don't need it.

### 1.3a Verify which workspace each package is loading from

This is the one that bit us on gh1: if you accidentally source the
standalone upstream FarmBot_ROS2 workspace AFTER the Rishabh repo
(instead of BEFORE), every ROS 2 lookup returns the upstream copy and
none of your overrides apply. The keyboard controller runs, motors
move, but `P_4` falls flat because the upstream `map_handler` is
loading a stale or empty map.

Two cheap checks. Run both AFTER all your `source ...` lines:

**a. Which prefix is each FarmBot package loading from?**

```bash
for pkg in farmbot_bringup farmbot_controllers farmbot_command_handler farmbot_interfaces map_handler; do
  printf "%-30s -> %s\n" "$pkg" "$(ros2 pkg prefix $pkg 2>/dev/null || echo NOT-FOUND)"
done
```

You want every line ending in `~/Rishabh_Growmate_FarmBot/install/...`
(or whatever your path is). If any line ends in
`/home/farmbotdev/FarmBot_ROS2/install/...` (or any other standalone
location), that package is wrong — the upstream copy is shadowing your
edits. Re-source in the right order:

```bash
# Source upstream FIRST (if you have it sourced at all), Rishabh LAST
source /opt/ros/$ROS_DISTRO/setup.bash
# only if you previously sourced the standalone workspace:
# source ~/FarmBot_ROS2/install/setup.bash
source ~/Rishabh_Growmate_FarmBot/install/setup.bash
```

Re-run the for-loop and confirm every package now points at the
Rishabh path.

**Gotcha that bit us on gh1:** if `~/.bashrc` auto-sources
`~/FarmBot_ROS2/install/setup.bash` on every new shell (very common
on the Maynooth Pis where a separate person works on the standalone
upstream FarmBot_ROS2 install), Rishabh's `setup.bash` re-chain-sources
FarmBot_ROS2 internally — putting FarmBot_ROS2 BACK at the front of
`AMENT_PREFIX_PATH` after you sourced Rishabh, no matter what order
you typed. You can confirm by running `grep FarmBot ~/.bashrc`.

**Recommended fix (zero impact on the standalone person's work):**
use [tools/use-rishabh.sh](../tools/use-rishabh.sh), a per-session
script that does the reorder for THIS shell only. Doesn't touch
`~/.bashrc`, so the other person's shell sessions are completely
unaffected. Install once:

```bash
cp ~/Rishabh_Growmate_FarmBot/tools/use-rishabh.sh ~/use-rishabh.sh
chmod +x ~/use-rishabh.sh
```

Then in every terminal where you want to work on Rishabh:

```bash
source ~/use-rishabh.sh         # MUST be `source`, not `bash`
```

The script prints a `[rishabh]` / `[standalone]` / `[missing]` tag
next to each FarmBot package so you can verify at a glance that
`map_handler` resolves to Rishabh BEFORE you run the section 1.4
map copy (otherwise the copy lands in the standalone install and
overwrites the other person's `active_map.yaml`).

The other person never runs `~/use-rishabh.sh`, so their
`AMENT_PREFIX_PATH` stays whatever `.bashrc` gave them — pure
FarmBot_ROS2, no Rishabh entries. Their work is untouched.

**Heavier-handed fix (only if you don't share the `gh1` user):**
comment out the `.bashrc` auto-source and source manually each
session:

```bash
sed -i 's|^source ~/FarmBot_ROS2/install/setup.bash|# &    # manual; see RUN_GUIDE 1.3a|' ~/.bashrc
exec $SHELL                                                # clean shell
source ~/FarmBot_ROS2/install/setup.bash                   # underlay first
source ~/Rishabh_Growmate_FarmBot/install/setup.bash       # overlay last
```

Don't do this if another person works on this Pi under the same
account — you'll silently break their FarmBot_ROS2 launches the
next time they open a terminal.

**b. Where is the active python module coming from?**

```bash
python3 -c "import growmate_pi; print('growmate_pi:', growmate_pi.__file__)"
python3 -c "import py_trees;    print('py_trees:   ', py_trees.__file__)"
python3 -c "import rclpy;       print('rclpy:      ', rclpy.__file__)" 2>/dev/null
```

Expected: `growmate_pi` comes from
`~/Rishabh_Growmate_FarmBot/src/growmate_pi/...` (because of
`PYTHONPATH=src`), `py_trees` from your venv, `rclpy` from
`/opt/ros/$ROS_DISTRO/...`.

If `py_trees` reports `/opt/ros/$ROS_DISTRO/lib/python3.X/site-packages/py_trees`,
the venv didn't get picked up — `pip install --force-reinstall py_trees`
inside the venv to get a local copy.

**c. (One-liner sanity dump for the bug ticket)**

When something feels off and you want to capture the whole environment
in a paste-able blob:

```bash
echo "--- ROS_DISTRO=$ROS_DISTRO ---"
echo "--- AMENT_PREFIX_PATH ---"
echo "$AMENT_PREFIX_PATH" | tr ':' '\n'
echo "--- key packages ---"
for pkg in farmbot_bringup farmbot_controllers farmbot_command_handler farmbot_interfaces map_handler; do
  printf "%-30s -> %s\n" "$pkg" "$(ros2 pkg prefix $pkg 2>/dev/null || echo NOT-FOUND)"
done
echo "--- python modules ---"
python3 -c "import sys, growmate_pi, py_trees; print('growmate_pi:', growmate_pi.__file__); print('py_trees:', py_trees.__file__); print('venv:', sys.prefix)"
```

Paste that into the issue or message before debugging — saves twenty
minutes of "wait what shell did I source from".

### 1.4 Load the right map for this garden

```bash
# for the new greenhouse (35 plants)
cp ~/Rishabh_Growmate_FarmBot/tools/maps/new_greenhouse.yaml \
   "$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml"

# verify
python3 -c "
import yaml
d = yaml.safe_load(open('$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml'))
p = d['plant_details']['plants']
pc = d['plant_details']['plant_count']
print('plant_count:', pc, '| OK' if p and pc > 0 else '| EMPTY MAP — re-copy')
"
```

Must say `| OK`. If it says `EMPTY MAP` re-copy the YAML — every
`colcon build` may wipe the install-share dir.

For the 54-plant Maynooth garden use
`tools/maps/maynooth_54.yaml` instead.

### 1.5 If `activeConfig.yaml` is missing in install dir

```bash
ls "$(ros2 pkg prefix farmbot_controllers)/share/farmbot_controllers/config/activeConfig.yaml" 2>/dev/null \
  || echo "MISSING — copy from standalone or run C_0 + CONF after first bringup"
```

If missing, find a working copy and bring it in:

```bash
find ~ -name "activeConfig.yaml" 2>/dev/null
# then for example:
cp /home/farmbotdev/FarmBot_ROS2/install/farmbot_controllers/share/farmbot_controllers/config/activeConfig.yaml \
   "$(ros2 pkg prefix farmbot_controllers)/share/farmbot_controllers/config/activeConfig.yaml"
```

If no working copy exists, first-time bringup needs **C_0 calibration**
followed by **CONF** to capture real workspace dimensions — preset
alone isn't enough.

### 1.6 Launch and verify

```bash
ros2 launch farmbot_bringup no_camera.launch.py 2>&1 | tee /tmp/bringup.log
```

Wait for **both** lines:

- `R99 ARDUINO STARTUP COMPLETE`
- `Initialized with active config from previous run`

And **no** `map_controller` Traceback. If you see a traceback:

```bash
grep -E "map_controller|Traceback" /tmp/bringup.log
# fix the active_map.yaml (section 1.4) and relaunch
```

### 1.7 Keyboard controller smoke test

Second terminal:

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
source ~/Rishabh_Growmate_FarmBot/install/setup.bash
ros2 run farmbot_controllers keyboard_controller
```

At the prompt:

```
E            # reset e-stop
H_0          # basic motion first — gantry should home
P_4          # water all plants
```

If `H_0` moves but `P_4` doesn't, the map didn't load — re-copy the
YAML (section 1.4) and restart bringup.

One-liner to send a keyboard command from any terminal:

```bash
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'P_4'}"
```

---

## 2. Launch the intent server (Pi)

Third terminal on the Pi:

```bash
cd ~/Rishabh_Growmate_FarmBot
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python -m growmate_pi.intent_server --port 8000
```

Expected: `Bridge: connected, publishing to 'keyboard_topic'`.

Confirm from any terminal (the Pi itself, another Pi, or the laptop):

```bash
curl -s http://<pi-ip>:8000/plants \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('plants:', d.get('count'), '| source:', d.get('source'))"
```

Should print the expected plant count + which YAML it pulled from
(e.g. `plants: 35 | source: active_map.yaml`).

Other useful endpoints once it's up:

```bash
curl -s http://<pi-ip>:8000/plants/needs_attention | python3 -m json.tool
curl -s "http://<pi-ip>:8000/events?limit=10" | python3 -m json.tool
curl -s "http://<pi-ip>:8000/events?plant=tomato&event_type=watered&limit=5" | python3 -m json.tool
```

For sim mode (no real motor moves — for the V2 eval or local dev):

```bash
PYTHONPATH=src:$PYTHONPATH python -m growmate_pi.intent_server --no-ros2 --port 8000
```

---

## 3. Launch the voice app (Windows)

```cmd
cd C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice
set PYTHONPATH=C:\Users\risha\growmate-bt\voice-farmbot\src
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.137.161:8000/intent

python -m growmate_voice.app --pi-url http://192.168.0.54:8000/intent
```

Look for `V2 mode: dispatching to Pi at ...` and `Pi ready: ...` in
the console.
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.137.161:8000/intent


Open `http://localhost:7860` in the browser. Hard-refresh after every
restart (Ctrl+Shift+R) so the new HTML/JS picks up.

---

## 3a. Access GrowMate from a phone (full functionality)

The web UI works fine over LAN HTTP, but the **microphone button**
needs an HTTPS origin per the browser security policy. `localhost` is
the one exception. Two paths to give a phone the full experience.

### 3a.1 Cloudflared quick tunnel — recommended for demo day

Free, no signup, **one command per session**, real HTTPS. Internet
required.

**One-time install:**

```cmd
winget install --id Cloudflare.cloudflared
```

**Every session, third terminal (Windows app keeps running on default
host/port):**

```cmd
cloudflared tunnel --url http://localhost:7860
```

The output includes a line like:

```
Your quick Tunnel has been created! Visit it at (it may take some time
to be reachable):
https://strange-purple-bird-1234.trycloudflare.com
```

Open that URL on the phone. The mic button works. The tunnel URL is
random and rotates each launch — fine for a researcher-driven demo,
not great if you're emailing the link to focus-group participants
ahead of time.

For a **stable** URL (handy for the focus group), set up a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
with a free Cloudflare account and a domain you own. The tunnel runs
the same way; the URL just doesn't rotate.

### 3a.2 mkcert + local CA — offline / stable LAN URL

For demos with no internet at the venue, or when you want the same
LAN URL every time. Uses a local certificate authority and a
self-signed cert your phone trusts after a one-time install.

**One-time setup (Windows):**

```cmd
winget install FiloSottile.mkcert
mkcert -install
:: Replace 192.168.1.42 with your Windows LAN IP (run `ipconfig`).
:: 'pc-name.local' is your hostname; check with `hostname`.
mkcert localhost 127.0.0.1 192.168.1.42 pc-name.local
```

That writes `localhost+3.pem` and `localhost+3-key.pem` to the
current directory. Move them somewhere sensible, e.g.
`C:\Users\risha\.growmate_voice\certs\`.

**One-time per phone:** install the mkcert root CA. On the Windows
machine:

```cmd
mkcert -CAROOT
:: Prints something like C:\Users\risha\AppData\Local\mkcert
:: That folder contains rootCA.pem. Email it to yourself or AirDrop
:: it to the phone, then:
::   - iOS: open the .pem in Safari -> Settings -> General -> VPN
::     & Device Management -> trust the cert.
::   - Android: Settings -> Security -> Encryption & credentials ->
::     Install a certificate -> CA certificate.
```

**Every session (Windows app):**

```cmd
cd C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice
set PYTHONPATH=C:\Users\risha\growmate-bt\voice-farmbot\src
python -m growmate_voice.app --no-ros2 ^
  --pi-url http://192.168.0.54:8000/intent ^
  --host 0.0.0.0 ^
  --ssl-keyfile C:\Users\risha\.growmate_voice\certs\localhost+3-key.pem ^
  --ssl-certfile C:\Users\risha\.growmate_voice\certs\localhost+3.pem
```

Console prints `GrowMate at https://0.0.0.0:7860`. From the phone:
`https://192.168.1.42:7860` — no warning, full mic, no internet
required.

Don't forget the Windows firewall:

```cmd
netsh advfirewall firewall add rule name="GrowMate 7860" ^
  dir=in action=allow protocol=TCP localport=7860
```

### 3a.3 LAN HTTP, no mic — fallback if HTTPS isn't an option

```cmd
python -m growmate_voice.app --no-ros2 --host 0.0.0.0 ^
  --pi-url http://192.168.0.54:8000/intent
```

Phone visits `http://192.168.1.42:7860`. Everything works **except
the microphone button** — the browser refuses `getUserMedia` over
HTTP from a non-localhost origin. Buttons, jog controls, the
blocking overlay, text input, EMERGENCY STOP, RESET all work fine.

Useful for confirming the UI renders correctly on a small screen
before bothering with TLS.

### 3a.4 What works from the phone — sanity check matrix

| Feature | Cloudflared HTTPS | mkcert HTTPS | LAN HTTP |
|---|---|---|---|
| Web UI / layout | ✓ | ✓ | ✓ |
| Button presses (lights, home, jog, photo) | ✓ | ✓ | ✓ |
| Text-input commands | ✓ | ✓ | ✓ |
| Mic button (voice input) | ✓ | ✓ | ✗ (browser blocks) |
| Kokoro TTS playback (audio out) | ✓ | ✓ | ✓ |
| Browser TTS announcements (Tier B) | ✓ | ✓ | ✓ |
| Tier B blocking overlay + EMERGENCY STOP | ✓ | ✓ | ✓ |
| Today's care panel (when re-armed) | ✓ | ✓ | ✓ |
| Stable URL across sessions | ✗ (rotates) | ✓ | ✓ |
| Works without internet | ✗ | ✓ | ✓ |

### 3a.5 The shortcut for demo day, 9 June 2026

```cmd
:: terminal 1 — Pi (already running per section 2)
:: terminal 2 — Windows app (already running per section 3)
:: terminal 3 — tunnel
cloudflared tunnel --url http://localhost:7860
```

Copy the printed `https://*.trycloudflare.com` URL. Open it on your
phone. Done.

---

## 4. Test order (smallest risk first)

Run these in order — each one validates a more complex layer than the
last:

1. **Lights** quick-action → publishes `D_L_1`, then again → `D_L_0`
   — _hardware run: PASS_
2. **Home** quick-action → gantry to (0, 0, 0)
   — _hardware run: PASS_
3. **Step 100 mm** + jog forward → `M 0 100 0`, gantry moves
   — _hardware run: PASS_
4. **Photo** quick-action → `I_1`
   — _hardware run: PASS_
5. Mic → say *"go home"* (pattern path) → fast, no LLM
6. Mic → say *"water the basil"* (LLM path) → AICore resolves the
   plant via the Pi's loaded map → gantry to basil's coord → pump → done
   — _first hardware run: misrouted to water-everything via matcher
   substring bug. Fixed (see "Hardware run findings" below)._
7. **Stop** button → `e` immediately, gantry halts. Press **Reset**
   to re-arm before the next command.
   — _first hardware run: Stop worked, but Reset needed multiple
   presses to take. Fixed: `/reset_estop` now publishes `E` three
   times with 180 ms gaps._

If any step misbehaves, the pipeline log on screen (bottom of the
page) shows what route was chosen (`🤖`, `🧠`, `⚡`, `❓`) and what
phrase the Pi reported back. Note the new Pi's IP and which step
first misbehaves.

---

## 5. Verifying Day 5 / 9 / 10 / 11 UX features

Each sprint day added a UX layer — quick verification per day so you
know they survived a rebuild:

### Day 5 — soft-confirm gate (water everything / all)

- Mic or text → *"water everything"* → modal appears with a clear
  Yes/No (64 px tap targets), large fonts.
  - _First hardware run: "can you help me water all the plants?"
    slipped past the gate because the trailing "?" was treated as a
    knowledge query. Fixed by separating "polite imperative" from
    "knowledge question" — see "Hardware run findings" below._
- **No / no response within 10 s** → cancelled, `Cancelled.` spoken.
- **Yes** → executes `water_all`.
- Emergency phrases (*stop*, *halt*, *freeze*, *emergency stop*) **never**
  pass through the gate — they fire immediately.

### Day 9 — care card (tap a plant on the map)

- Tap any plant on the map view → slide-up card with the plant's
  name, species, last-watered timestamp, "Water this plant" button,
  and a footer note that photo / soil-moisture aren't active yet.

### Day 10 — Today's care panel — PARKED

**Status:** archived from the UI as of the gh1/farmbotdev hardware run.
The panel polled `/api/plants/needs_attention` and showed an "N plants
need watering" nudge, but the underlying event log had two flaws the
hardware run exposed:

- `P_4 / watered_all` rows were being logged even when the BT didn't
  actually finish (the tree returned `partial` or `success` based on
  publish, not on the FarmBot completing the sequence).
- Result: the panel cheerfully reported "all plants watered" when the
  robot had only finished part of the cycle.

The fix needs a **tick-and-verify gate** in the BT — only log
`watered_all` after the FarmBot reports the sequence complete. That's a
real piece of work, not a UI tweak, so the panel is hidden behind
`_MEMORY_FEATURES_ENABLED = False` in `app.py` until it lands.

Flip the flag back to `True` (Python const + the matching JS
`MEMORY_FEATURES_ENABLED` in the inline script) to re-enable both Day
10 and Day 11 at once.

### Day 11 — voice plant queries — PARKED

**Status:** same flag, same reason. Falls through to AICore (LLM) now.

The fast-path was reading `last_watered_ts` from the same event log
Day 10 used, so the "P_4 logged but not actually finished" bug would
have given the user a confidently-wrong answer ("you watered the
tomatoes 2 hours ago" when in reality the gantry got 30% in and
stopped). Better to defer to the LLM until the event log is honest.

If/when re-armed, fall-through is signalled by `🧠 AICore: ...` in the
pipeline log (Pi unreachable mid-query) and a fast-path hit by
`⚡ Fast plant query: ...`.

---

## Hardware run findings (gh1 / farmbotdev, Jun 2026)

First end-to-end test on a real FarmBot surfaced four bugs. All four
are fixed in code; this section is the post-mortem so the symptoms
don't get re-debugged from scratch later.

### 1. E-stop reset wasn't taking — `/reset_estop` only published `E` once

Symptom: pressing the stop button halted the gantry, but pressing
Reset (and even pressing it again) didn't re-arm the system. The Pi
HTTP log showed `POST /reset_estop 200 OK` so the bridge ran, but
follow-up `/intent` calls had no visible effect.

Cause: `bridge.reset_emergency_stop()` published `E` exactly once.
`panel_controller` translates that to `F09` on `uart_transmit` which
the firmware needs for the actual reset — but a single publish was
racing the panel_controller / sequencer state on this Pi and getting
dropped.

Fix: `/reset_estop` now publishes `E` three times with 180 ms gaps,
and `/estop` publishes `e` twice. Both endpoints log the per-publish
status to the Pi console so you can see exactly what happened.

### 2. "water the basil" was misrouted to water-everything

Symptom: saying *"water the basil"* triggered the soft-confirm modal
with the question *"Should I water all the plants?"* — wrong target,
wrong action.

Cause: the matcher in `edgespeech/command_map.py` used
`v in candidate` for variant matching. The variant `"water"` was a
substring of `"water the basil"`, so it matched the bare-water action
(P_4). The same trap was firing on Day 11 phrasings like *"when did i
water the marigold"* — `"water"` substring → action=water → confirm
gate.

Fix: introduced `STRICT_MATCH_ACTIONS = {"water", "photo"}`. Variants
for those actions only match on **exact equality** after
filler/normalisation — `water`, `water plants`, `water the plants`,
`water all the plants`, `start watering` all still resolve, but
anything with a target or question wrapper falls through to the LLM,
which classifies properly.

### 3. "Can you help me water all the plants?" slipped past the confirm gate

Symptom: the destructive water-all phrasing executed immediately,
no Yes/No modal.

Cause: the AICore-route confirm gate was checking for any
question-starter word (`is`, `should`, `can`, `could`, `?`) — if
present, the gate was disarmed on the assumption "this is a
knowledge query". But *"can you help me …?"* is a polite imperative,
not a question.

Fix: split the question heuristic into `polite_imperatives` (which
do NOT disarm the gate — `can you`, `could you`, `would you`,
`please`, `help me`) and `knowledge_starters` (`when`, `why`, `how`,
`what`, …). A transcript with both `everything`/`all the plants`
AND any polite-imperative phrase now defers for confirm.

### 4. Today's-care panel / fast-path plant queries — parked

Symptom: the panel reported *"all plants watered"* when the BT had
only partially executed `P_4`; voice queries gave confidently-wrong
timestamps for the same reason.

Cause: the per-plant event log records `watered_all` on tree
publish-success, not on real FarmBot sequence completion. Until the
BT gets a tick-and-verify wrapper (work item in PLANS.md follow-up),
both features are off behind `_MEMORY_FEATURES_ENABLED = False` in
`app.py` and `MEMORY_FEATURES_ENABLED = false` in the inline JS.

To re-arm: flip both flags to true, hard-refresh the browser.

---

## Original Windows-app log (for reference, the run that produced these findings)

<details>
<summary>Click to expand</summary>

```
(moderation) C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice>python -m growmate_voice.app --pi-url http://192.168.0.54:8000/intent
(moderation) C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice>python -m growmate_voice.app --pi-url http://192.168.0.54:8000/intent
10:53:53  INFO      [growmate]  V2 mode: dispatching to Pi at http://192.168.0.54:8000/intent
10:53:53  INFO      [growmate]  Pi ready: {'ok': True, 'schema_version': '1.0.0', 'bridge_mode': 'ros2', 'bridge_ready': True, 'topic': 'keyboard_topic', 'config': '/home/farmbotdev/Rishabh_Growmate_FarmBot/src/growmate_pi/config/farmbot.yaml'}
10:53:53  INFO      [growmate]  === GrowMate startup ros2=True pi_url=http://192.168.0.54:8000/intent 127.0.0.1:7860 ===
10:53:53  INFO      [growmate]  Initialising — ros2=True
[growmate_voice] Connected to ROS2; publishing to 'keyboard_topic'
10:53:53  INFO      [growmate]  GrowMate backend ready.  Log: C:\Users\risha\.growmate_voice\logs\growmate.log
10:53:53  INFO      [growmate]  GrowMate at http://127.0.0.1:7860  (log: C:\Users\risha\.growmate_voice\logs\growmate.log)
INFO:     Started server process [33944]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:7860 (Press CTRL+C to quit)
INFO:     127.0.0.1:60141 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:60141 - "GET /api/plants HTTP/1.1" 200 OK
INFO:     127.0.0.1:51116 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
10:53:57  INFO      [growmate]  Launched farmbot_bringup pid=37284
INFO:     127.0.0.1:60141 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:60141 - "GET /api/plants HTTP/1.1" 200 OK
INFO:     127.0.0.1:64608 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
10:54:00  INFO      [growmate]  Launched farmbot_bringup pid=22712
10:54:01  INFO      [growmate]  App state -> READY (ros2=True)
10:54:03  WARNING   [growmate]  FarmBot process exited (rc=1)
10:54:04  INFO      [growmate]  App state -> READY (ros2=True)
INFO:     127.0.0.1:64608 - "POST /api/farmbot/power HTTP/1.1" 200 OK
INFO:     127.0.0.1:64608 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:64608 - "POST /api/action HTTP/1.1" 200 OK
10:54:19  INFO      [growmate]  Whisper prompt biased with 800 chars
10:54:31  INFO      [growmate]  AICore ready (model=gemma3:4b, config=C:\Users\risha\growmate-bt\voice-farmbot\src\growmate_voice\config\farmbot.yaml)
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
C:\Users\risha\.conda\envs\moderation\Lib\site-packages\torch\nn\modules\rnn.py:1013: UserWarning: dropout option adds dropout after all but last recurrent layer, so non-zero dropout expects num_layers greater than 1, but got dropout=0.2 and num_layers=1
  super().__init__("LSTM", *args, **kwargs)
C:\Users\risha\.conda\envs\moderation\Lib\site-packages\torch\nn\utils\weight_norm.py:144: FutureWarning: `torch.nn.utils.weight_norm` is deprecated in favor of `torch.nn.utils.parametrizations.weight_norm`.
  WeightNorm.apply(module, name, dim)
INFO:     127.0.0.1:59122 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:59122 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:61386 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:61386 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:63705 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:63705 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:63705 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:52395 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:52395 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:61612 - "GET /api/plants/30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:56882 - "GET /api/plants/8 HTTP/1.1" 200 OK
INFO:     127.0.0.1:56882 - "POST /api/text HTTP/1.1" 200 OK
INFO:     127.0.0.1:57168 - "POST /api/confirm HTTP/1.1" 200 OK
INFO:     127.0.0.1:57168 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:49900 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:49900 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:59401 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:57754 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:62632 - "POST /api/confirm HTTP/1.1" 200 OK
INFO:     127.0.0.1:62632 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:53481 - "POST /api/reset HTTP/1.1" 200 OK
INFO:     127.0.0.1:56714 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:56714 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:65142 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:50622 - "POST /api/reset HTTP/1.1" 200 OK
INFO:     127.0.0.1:55616 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:55616 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:55616 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:55616 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:56822 - "POST /api/action HTTP/1.1" 200 OK
INFO:     127.0.0.1:63512 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
INFO:     127.0.0.1:62973 - "POST /api/voice HTTP/1.1" 200 OK
INFO:     127.0.0.1:62973 - "GET /api/plants/needs_attention HTTP/1.1" 200 OK
```

### Pi-side intent server log

```
farmbotdev@ubuntu:~/Rishabh_Growmate_FarmBot$ cd ~/Rishabh_Growmate_FarmBot
source /opt/ros/$ROS_DISTRO/setup.bash
source install/setup.bash
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python -m growmate_pi.intent_server --port 8000
[growmate_pi] Bridge: connected, publishing to 'keyboard_topic'
INFO:     Started server process [40032]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     192.168.0.127:60135 - "GET /status HTTP/1.1" 200 OK
INFO:     192.168.0.127:55758 - "GET /plants HTTP/1.1" 200 OK
INFO:     192.168.0.127:55757 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:50821 - "GET /plants HTTP/1.1" 200 OK
INFO:     192.168.0.127:50822 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:55377 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:55378 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:64085 - "GET /status HTTP/1.1" 200 OK
INFO:     192.168.0.127:51117 - "GET /plants HTTP/1.1" 200 OK
INFO:     192.168.0.127:51118 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:64610 - "GET /plants HTTP/1.1" 200 OK
INFO:     192.168.0.127:64609 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:59144 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:59145 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:50116 - "GET /plants/needs_attention?limit=20 HTTP/1.1" 200 OK
INFO:     192.168.0.127:50120 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:49292 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:51014 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:51015 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:63706 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:57044 - "POST /estop HTTP/1.1" 200 OK
INFO:     192.168.0.127:57045 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:52399 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:52400 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:61613 - "GET /plants/30?history_limit=30 HTTP/1.1" 200 OK
INFO:     192.168.0.127:56883 - "GET /plants/8?history_limit=30 HTTP/1.1" 200 OK
INFO:     192.168.0.127:57169 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:57170 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:49904 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:49905 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:59402 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:62633 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:62634 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:53482 - "POST /reset_estop HTTP/1.1" 200 OK
INFO:     192.168.0.127:51555 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:51556 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:65143 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:50623 - "POST /reset_estop HTTP/1.1" 200 OK
INFO:     192.168.0.127:55617 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:50624 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:59685 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:59686 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:56823 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:63513 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
INFO:     192.168.0.127:62974 - "POST /intent HTTP/1.1" 200 OK
INFO:     192.168.0.127:62975 - "GET /plants/needs_attention?limit=200 HTTP/1.1" 200 OK
```

</details>

---

## 6. Running the V2 evaluation (Day 12)

Full 29-utterance corpus against the Pi. Metrics:
**DBSR / SNSR / USC / ELC / latency** — see
[eval_v2_results.md](eval_v2_results.md) for what each one means and
the V1 baseline to compare against.

### 6.1 Sim-mode run (no wet pumps)

Pi:

```bash
ssh gh1@<pi-ip>
cd ~/Rishabh_Growmate_FarmBot
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python3 -m growmate_pi.intent_server --no-ros2 --port 8000
```

Windows:

```powershell
$env:PYTHONPATH = "src"
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent
```

Expected output: table per case + summary line including ELC.
Exit code 0 if DBSR ≥ 80 %, else 1.

### 6.2 Capture per-case detail for the appendix

```powershell
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent --json > eval_results.json
```

Then paste the `summary` block into
[eval_v2_results.md](eval_v2_results.md) under a new `### Run N`
block, fill the Δ column vs V1, and flag any DBSR/ELC misses with a
one-line reason.

### 6.3 Pi-only smoke (no LLM)

If Ollama isn't running on Windows, you can still smoke-test the Pi
side — uses canned intents instead of real classification:

```powershell
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent --no-llm
```

DBSR will be lower (canned intents are placeholders), but ELC still
tells you the Pi event log is wired up.

---

## 7. Demo day procedure (Day 13)

To be written — `demo/demo_day_run.md`. Will contain:

- Pre-demo checklist (bringup running, intent server up, mic
  permission, Ollama warm)
- 10 scripted scenarios in increasing complexity
- Recovery steps for each likely failure mode (mic denied, Pi
  disconnected, Ollama down, water pump stuck, soft-confirm misfires)

Until that's written, fall back to
[demo_day_plan.md](demo_day_plan.md) for the focus-group run-of-show.

---

## 8. Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Bringup tracebacks in `map_controller` | `active_map.yaml` was wiped by `colcon build` (install-share dir is rebuilt) | Re-copy the YAML (section 1.4), restart bringup |
| `H_0` moves but `P_4` doesn't | Map controller cached an empty map at startup | Same as above |
| `H_0` moves but `P_4` doesn't, AND `active_map.yaml` is fine | Standalone upstream `FarmBot_ROS2` workspace is shadowing the Rishabh overlay — `ros2` picks up the upstream `map_handler` which knows nothing about the Maynooth garden | Run the for-loop in section 1.3a and verify every `farmbot_*` and `map_handler` resolves to `~/Rishabh_Growmate_FarmBot/install/...`; re-source upstream first and Rishabh last |
| A Python module is the wrong copy (e.g. `py_trees` from `/opt/ros/...` instead of the venv) | venv created without `--system-site-packages` OR a `pip install` reported "already satisfied" by finding the ROS-installed copy | Same diagnosis from section 1.3a (`python3 -c "import X; print(X.__file__)"`); `pip install --force-reinstall <pkg>` inside the venv to pin a local copy |
| `colcon build` fails with `ModuleNotFoundError: No module named 'em'` (rosidl_adapter) | You're building INSIDE the venv. ROS 2's message generator uses the active Python; the venv lacks `empy`/`lark`/`catkin_pkg` even with `--system-site-packages` if Python versions don't line up | `deactivate` first, install `python3-empy python3-lark python3-catkin-pkg` via `apt`, rebuild outside the venv (section 1.3) |
| `fatal: not a git repository` after pull | Folder predates git on this Pi | `git init` + `remote add` + `fetch` + `checkout -f main` (section 1.2) |
| `ModuleNotFoundError: rclpy` inside the venv | Venv created without `--system-site-packages` | `python3 -m venv --system-site-packages venv` (recreate) |
| `colcon build` picks up venv files | Missing `COLCON_IGNORE` marker | `touch venv/COLCON_IGNORE`, rebuild |
| `PYTHONPATH=src` hides ROS 2 modules | `src` overrides ROS site-packages | Use `PYTHONPATH=src:$PYTHONPATH` (prepend, don't replace) |
| Intent server returns `Pi not reachable` to UI | Pi's intent_server isn't running OR firewall blocks port 8000 | Confirm with `curl http://<pi-ip>:8000/plants` from the laptop |
| Whisper picks `tomatoes` as `tomato is`, etc. | Whisper's default vocab doesn't bias to garden words | Already on by default since Day 4 — uses `small.en` + `initial_prompt`. Check the prompt with `tools/_smoke_whisper_prompt.py` |
| Soft-confirm modal never appears for "water all" | Phrase didn't pattern-match into `water_all` | Look at pipeline log — if route was `pattern` and action was `water_all`, gate **must** have fired. If route was `aicore`, the LLM took it — same gate fires from `_CONFIRM_AICORE_ACTIONS` |
| Today's-care panel stuck on old count | UI hasn't polled yet (5-min interval) | It refreshes immediately after any successful action — just send one command |
| Fast-path *"when did I water X"* falls through to LLM every time | Pi unreachable → fast path returns `None` so LLM gets a chance | Confirm `curl http://<pi-ip>:8000/events?limit=1` from Windows |

---

## Reference: where things live

- **Pi code:** `~/Rishabh_Growmate_FarmBot/src/growmate_pi/`
- **Pi config (per-garden):** `tools/maps/{maynooth_54.yaml,new_greenhouse.yaml}`
  → copied into `$(ros2 pkg prefix map_handler)/share/map_handler/config/active_map.yaml`
- **Pi event log (SQLite, WAL):** `~/.growmate_pi/events.db`
- **Windows app:** `src/growmate_voice/growmate_voice/app.py`
- **Garden config (LLM-side):** `src/growmate_voice/config/farmbot.yaml`
- **V2 eval:** `tools/evaluate_v2.py`
- **V2 eval log:** `demo/eval_v2_results.md`
- **Sprint plan:** `PLANS.md` §4 (15-day elderly UX sprint)
- **Integration guide (for collaborators integrating their own code):**
  `demo/INTEGRATION_GUIDE.md`
