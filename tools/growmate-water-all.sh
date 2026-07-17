#!/usr/bin/env bash
#
# growmate-water-all.sh — trigger a cold-start watering cycle on EVERY
# greenhouse, from a controller box that can SSH to them.
#
# This script does no robot work itself. It kicks off `growmate-water-run.sh`
# on each Pi *detached from the SSH session*, then polls for the result. That
# split is deliberate: if the network drops mid-run, the Pi carries on watering
# and tears its own stack down. You lose visibility, never the watering.
#
# Both greenhouses run in PARALLEL — sequential would push the second one past
# 09:00 and into everyone else's working day.
#
# Requires: passwordless SSH (key auth) to each Pi, and growmate-water-run.sh
# present on each Pi. See SCHEDULING.md §2.
#
# Usage:
#   ./growmate-water-all.sh              # all greenhouses
#   ./growmate-water-all.sh gh1          # just one
#
# Exit: 0 only if every greenhouse watered. Non-zero otherwise (and the
# per-greenhouse verdict is printed + logged).
#
set -uo pipefail

# name | ssh target | repo path on that Pi | expected plants | config (blank = launch default)
GREENHOUSES=(
  "gh1|gh1@192.168.0.38|/home/gh1/Rishabh_Growmate_FarmBot|56|"
  "gh2|farmbotdev@192.168.0.53|/home/farmbotdev/Rishabh_Growmate_FarmBot|35|src/growmate_pi/config/farmbotdev.yaml"
)

LOG="${GM_CONTROLLER_LOG:-$HOME/growmate-controller.log}"
POLL_S="${GM_POLL_S:-60}"
OVERALL_TIMEOUT_S="${GM_OVERALL_TIMEOUT_S:-7200}"   # 2 h — a full garden is ~45 min
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15)

only="${1:-}"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG" ; }

log "=== CONTROLLER RUN START ${only:+(only: $only)} ==="

declare -A PENDING=()

# ---------------------------------------------------------------- kick off ---
for row in "${GREENHOUSES[@]}"; do
    IFS='|' read -r name target repo expect config <<<"$row"
    [ -n "$only" ] && [ "$only" != "$name" ] && continue

    remote_env="GM_REPO=$repo GM_EXPECT_PLANTS=$expect"
    [ -n "$config" ] && remote_env="$remote_env GM_CONFIG=$repo/$config"

    # setsid + nohup + </dev/null so the run outlives this SSH connection.
    if ssh "${SSH_OPTS[@]}" "$target" \
        "setsid nohup env $remote_env $repo/tools/growmate-water-run.sh \
         >/dev/null 2>&1 </dev/null & echo started" >/dev/null 2>&1
    then
        log "$name: kicked off on $target"
        PENDING[$name]="$target"
    else
        log "$name: FAILED to reach $target over SSH — not watered"
    fi
done

if [ ${#PENDING[@]} -eq 0 ]; then
    log "=== CONTROLLER RUN END: nothing started ==="
    exit 1
fi

# Give each run a moment to write its RUNNING marker before we start reading it,
# or we would read the previous run's verdict and return instantly.
sleep 15

# ------------------------------------------------------------------- poll ----
declare -A VERDICT=()
deadline=$((SECONDS + OVERALL_TIMEOUT_S))

while [ ${#PENDING[@]} -gt 0 ] && (( SECONDS < deadline )); do
    for name in "${!PENDING[@]}"; do
        target="${PENDING[$name]}"
        st=$(ssh "${SSH_OPTS[@]}" "$target" \
             "cat ~/.growmate_run/last.status 2>/dev/null" 2>/dev/null \
             | awk '{print $NF}')

        if [ -z "$st" ]; then
            continue                       # unreachable right now; try again
        elif [ "$st" = "RUNNING" ]; then
            continue
        else
            VERDICT[$name]="$st"
            unset 'PENDING[$name]'
            log "$name: $st"
        fi
    done
    [ ${#PENDING[@]} -gt 0 ] && sleep "$POLL_S"
done

for name in "${!PENDING[@]}"; do
    VERDICT[$name]="TIMEOUT_WATCHING"
    log "$name: still running after ${OVERALL_TIMEOUT_S}s — the Pi may still be watering"
done

# ----------------------------------------------------------------- report ----
rc=0
log "--- results ---"
for name in "${!VERDICT[@]}"; do
    v="${VERDICT[$name]}"
    case "$v" in
        OK)               log "  $name: watered" ;;
        SKIPPED_*)        log "  $name: SKIPPED ($v) — robot was in use"; rc=1 ;;
        TIMEOUT_WATCHING) log "  $name: UNKNOWN ($v)"; rc=1 ;;
        *)                log "  $name: FAILED ($v)"; rc=1 ;;
    esac
done

# Pull the tail of each Pi's own log — the detail lives there, not here.
for name in "${!VERDICT[@]}"; do
    for row in "${GREENHOUSES[@]}"; do
        IFS='|' read -r n target _ _ _ <<<"$row"
        [ "$n" = "$name" ] || continue
        [ "${VERDICT[$name]}" = "OK" ] && continue
        log "--- $name last log lines ---"
        ssh "${SSH_OPTS[@]}" "$target" "tail -12 ~/growmate-watering.log" 2>/dev/null \
            | sed 's/^/    /' | tee -a "$LOG" >/dev/null
    done
done

log "=== CONTROLLER RUN END (exit $rc) ==="
exit "$rc"
