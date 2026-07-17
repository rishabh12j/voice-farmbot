#!/usr/bin/env bash
#
# growmate-water-run.sh — one cold-start watering cycle for ONE greenhouse.
#
# Brings the GrowMate stack up, homes, waters every plant in the active map,
# then tears the stack back down so the robot is free for whoever wants it.
# Meant to be fired at a time when nobody is using the robot (08:00 / 17:00),
# either by this Pi's own cron or over SSH from a controller box.
#
# Contract:
#   * Never fights another stack for the Farmduino. If anything is already
#     running, it SKIPS and says so — it does not kill someone else's session.
#   * Never tears down while the robot is still moving: it waits for
#     /status intent_active to go false first, whatever the watering step
#     claimed. `scheduler --now` reports a client-poll timeout as "ok
#     (status=partial)" while the gantry is still going, so its verdict is not
#     safe to kill on.
#   * Always tears down what it started, including on failure or interrupt.
#   * Exits NON-ZERO when the garden did not get watered. (`scheduler --now`
#     exits 0 unconditionally; that blindness is the whole reason this exists.)
#   * Homes before watering: a fresh launch reopens the serial port, which
#     resets the Farmduino, so position is unknown and absolute moves would be
#     against a phantom origin.
#
# Usage:
#   ./growmate-water-run.sh                                   # gh1 defaults
#   GM_CONFIG=$HOME/Rishabh_Growmate_FarmBot/src/growmate_pi/config/farmbotdev.yaml \
#     GM_EXPECT_PLANTS=35 ./growmate-water-run.sh             # gh2
#
# Exit codes (grep the log for these):
#   0  watered ok             30  homing failed
#   10 skipped, robot in use  40  watering failed
#   20 stack never came up    50  watered, but teardown left something running
#
set -uo pipefail

REPO="${GM_REPO:-$HOME/Rishabh_Growmate_FarmBot}"
CONFIG="${GM_CONFIG:-}"                       # empty -> launch default (gh1.yaml)
PORT="${GM_PORT:-8000}"
LOG="${GM_LOG:-$HOME/growmate-watering.log}"
EXPECT_PLANTS="${GM_EXPECT_PLANTS:-0}"        # 0 = just require > 0
READY_TIMEOUT="${GM_READY_TIMEOUT:-240}"      # s to wait for bringup + intent server
HOME_TIMEOUT="${GM_HOME_TIMEOUT:-600}"        # s for the homing move
WATER_TIMEOUT="${GM_WATER_TIMEOUT:-5400}"     # s for the whole garden
IDLE_TIMEOUT="${GM_IDLE_TIMEOUT:-2400}"       # s to wait for motion to stop before teardown

BASE="http://localhost:${PORT}"
STATUS_DIR="$HOME/.growmate_run"
STATUS_FILE="$STATUS_DIR/last.status"
LOCK_FILE="$STATUS_DIR/run.lock"
LAUNCH_LOG="$STATUS_DIR/launch-$(date +%F).log"
LAUNCH_PGID=""

mkdir -p "$STATUS_DIR"

log()    { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG" ; }
status() { printf '%s %s\n' "$(date '+%F %T')" "$1" > "$STATUS_FILE" ; }

# Read one field out of a JSON endpoint. Empty string if anything goes wrong.
json_field() {  # $1 = path, $2 = key
    curl -sf --max-time 5 "$BASE$1" 2>/dev/null \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" 2>/dev/null
}

# --------------------------------------------------------------- wait idle ---
# The robot must be standing still before we kill the stack. Do NOT trust the
# watering step's verdict for this: post_intent gives up polling at 3600s and
# reports status=partial, which scheduler.py logs as "Watering ok", while the
# gantry is still working its way down the bed.
wait_idle() {
    local deadline=$((SECONDS + IDLE_TIMEOUT)) active
    while (( SECONDS < deadline )); do
        active=$(json_field /status intent_active)
        [ -z "$active" ] && return 0            # server gone; nothing to wait for
        [ "$active" = "False" ] && return 0
        sleep 5
    done
    log "WARNING: robot still active after ${IDLE_TIMEOUT}s — tearing down anyway"
    return 1
}

# ---------------------------------------------------------------- teardown ---
teardown() {
    [ -z "$LAUNCH_PGID" ] && return 0
    wait_idle
    log "tearing down stack (pgid $LAUNCH_PGID)"

    # ros2 launch does a clean shutdown on SIGINT — give it that first.
    kill -INT -"$LAUNCH_PGID" 2>/dev/null
    for _ in $(seq 1 30); do
        kill -0 -"$LAUNCH_PGID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 -"$LAUNCH_PGID" 2>/dev/null; then
        log "stack ignored SIGINT, sending TERM"
        kill -TERM -"$LAUNCH_PGID" 2>/dev/null
        sleep 5
        kill -KILL -"$LAUNCH_PGID" 2>/dev/null
    fi

    # Bracketed pattern so it can never match its own command line (an
    # unbracketed pkill -f exits 255 for exactly that reason).
    pkill -f "[g]rowmate_pi.intent_server" 2>/dev/null
    sleep 2

    if curl -sf --max-time 3 "$BASE/status" >/dev/null 2>&1; then
        log "WARNING: something is still answering on :$PORT after teardown"
        return 1
    fi
    log "teardown clean — robot is free"
    return 0
}

# Single exit path: tear down exactly once, then record the verdict. A dirty
# teardown downgrades a success, because "watered but still holding the robot"
# is not a success from anyone else's point of view.
finish() {
    local code="$1" word="$2"
    trap - EXIT
    if ! teardown && [ "$code" -eq 0 ]; then
        code=50; word=TEARDOWN_DIRTY
    fi
    status "$word"
    log "=== RUN END: $word (exit $code) ==="
    exit "$code"
}
trap 'finish 1 INTERRUPTED' EXIT INT TERM

# ------------------------------------------------------------------- start ---
exec 9>"$LOCK_FILE"
flock -n 9 || { log "another run is already in progress — skipping"; trap - EXIT; exit 10; }

log "=== RUN START: ${CONFIG:-gh1 (launch default)} ==="
status RUNNING   # controller polls this; every exit path overwrites it

# 1. Is the robot free? Never take it from someone mid-session.
if pgrep -f "[r]os2 launch" >/dev/null 2>&1; then
    log "SKIPPED: a ros2 launch is already running — the robot is in use:"
    pgrep -af "[r]os2 launch" | sed 's/^/    /' | tee -a "$LOG"
    finish 10 SKIPPED_IN_USE
fi
if curl -sf --max-time 3 "$BASE/status" >/dev/null 2>&1; then
    log "SKIPPED: something is already serving :$PORT (leftover stack, or someone using voice)"
    finish 10 SKIPPED_PORT_BUSY
fi

# 2. ROS env. cron does not run .bashrc, so nothing is sourced for us.
if [ -z "${ROS_DISTRO:-}" ]; then
    ros_setup=$(ls -1 /opt/ros/*/setup.bash 2>/dev/null | head -1)
    [ -z "$ros_setup" ] && { log "FATAL: no /opt/ros/*/setup.bash"; finish 20 NO_ROS; }
    # shellcheck disable=SC1090
    source "$ros_setup"
fi

# use-rishabh.sh, not install/setup.bash: .bashrc auto-sources ~/FarmBot_ROS2 and
# our own setup chain puts it back at the FRONT of AMENT_PREFIX_PATH, so a plain
# source silently leaves us on the upstream map_handler — which knows nothing
# about this garden. The robot then homes fine and waters nothing.
if   [ -f "$HOME/use-rishabh.sh" ];       then source "$HOME/use-rishabh.sh" >/dev/null 2>&1
elif [ -f "$REPO/tools/use-rishabh.sh" ]; then source "$REPO/tools/use-rishabh.sh" >/dev/null 2>&1
else log "FATAL: use-rishabh.sh not found"; finish 20 NO_OVERLAY
fi

# Prove the overlay actually won before we move a motor.
prefix=$(ros2 pkg prefix map_handler 2>/dev/null || echo NOT-FOUND)
case "$prefix" in
    */Rishabh_Growmate_FarmBot/install/*) log "map_handler -> [rishabh] ok" ;;
    *) log "FATAL: map_handler resolves to '$prefix', not our overlay"
       finish 20 WRONG_OVERLAY ;;
esac

# 3. Launch, detached into its own process group so teardown can kill the tree.
launch_args=(scheduler:=false)
[ -n "$CONFIG" ] && launch_args+=("config:=$CONFIG")
log "launching: ros2 launch ./launch/greenhouse.launch.py ${launch_args[*]}"

cd "$REPO" || finish 20 NO_REPO
setsid bash -c "exec ros2 launch ./launch/greenhouse.launch.py ${launch_args[*]}" \
    >> "$LAUNCH_LOG" 2>&1 < /dev/null &
sleep 3
launch_pid=$(pgrep -f "[g]reenhouse.launch.py" | head -1)
if [ -z "$launch_pid" ]; then
    log "FATAL: launch did not start — see $LAUNCH_LOG"
    finish 20 LAUNCH_FAILED
fi
LAUNCH_PGID=$(ps -o pgid= -p "$launch_pid" 2>/dev/null | tr -d ' ')
log "launch pid $launch_pid, pgid $LAUNCH_PGID (log: $LAUNCH_LOG)"

# 4. Wait for it to be genuinely ready — not just listening.
log "waiting up to ${READY_TIMEOUT}s for bridge_mode=ros2 ..."
deadline=$((SECONDS + READY_TIMEOUT))
ready=""
while (( SECONDS < deadline )); do
    mode=$(json_field /status bridge_mode)
    [ "$mode" = "ros2" ] && { ready=1; break; }
    [ -n "$mode" ] && log "  bridge_mode=$mode, waiting ..."
    sleep 3
done
[ -z "$ready" ] && { log "FATAL: stack never reached bridge_mode=ros2 — see $LAUNCH_LOG"
                     finish 20 NOT_READY; }

count=$(json_field /plants count)
[ -z "$count" ] && count=0
log "stack ready — plants: $count"
if [ "$count" -eq 0 ]; then
    log "FATAL: map is empty — refusing to 'water' nothing and call it success"
    finish 20 EMPTY_MAP
fi
if [ "$EXPECT_PLANTS" -gt 0 ] && [ "$count" -ne "$EXPECT_PLANTS" ]; then
    log "FATAL: expected $EXPECT_PLANTS plants, map has $count — wrong garden or stale map"
    finish 20 PLANT_COUNT_MISMATCH
fi

# 5. Home. Nothing in the BT safety prefix checks that position is known —
#    CheckAvailable only checks the bridge is up — so this is on us.
log "homing before watering ..."
home_out=$(PYTHONPATH="$REPO/src" "$REPO/venv/bin/python" - <<PY 2>&1
from growmate_pi.pi_client import post_intent
from growmate_pi.schemas import Intent
r = post_intent("$BASE/intent",
                [Intent(action="go_home", response="Homing before the scheduled watering.")],
                raw_text="(scheduled) home before watering",
                client_id="cron-water-run",
                wait_for_completion=True,
                overall_timeout_s=float($HOME_TIMEOUT))
print("home:", r.status, r.error or "")
raise SystemExit(0 if r.status in ("success", "partial") else 1)
PY
)
home_rc=$?
printf '%s\n' "$home_out" | sed 's/^/    /' | tee -a "$LOG" >/dev/null
if [ "$home_rc" -ne 0 ]; then
    log "FATAL: homing failed — not watering against an unknown position"
    printf '%s\n' "$home_out" | tail -3 | sed 's/^/    /' | tee -a "$LOG"
    finish 30 HOME_FAILED
fi
log "homed"

# 6. Water. scheduler --now posts [water_all, go_home] and blocks. It exits 0
#    no matter what, so read its log line instead of its exit code.
log "watering (up to ${WATER_TIMEOUT}s) ..."
water_out=$(PYTHONPATH="$REPO/src" timeout "$WATER_TIMEOUT" \
    "$REPO/venv/bin/python" -m growmate_pi.scheduler --now --intent-url "$BASE" 2>&1)
printf '%s\n' "$water_out" | sed 's/^/    /' | tee -a "$LOG" >/dev/null

# status=partial means the client stopped polling, NOT that the robot stopped.
# Treat it as unproven: teardown's wait_idle will let the run actually finish.
if grep -q "Watering ok (status=success" <<<"$water_out"; then
    log "watering confirmed (status=success)"
    finish 0 OK
elif grep -q "status=partial" <<<"$water_out"; then
    log "watering UNPROVEN (status=partial — client poll cap hit; robot may still be running)"
    printf '%s\n' "$water_out" | tail -3 | sed 's/^/    /' | tee -a "$LOG"
    finish 40 WATER_PARTIAL
else
    log "FATAL: watering did not report ok:"
    printf '%s\n' "$water_out" | tail -5 | sed 's/^/    /' | tee -a "$LOG"
    finish 40 WATER_FAILED
fi
