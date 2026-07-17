# SCHEDULING — two waterings a day, hands off

Water every plant in both greenhouses at **08:00 and 17:00**, from a controller
box that is always on, leaving the robots **free by default** for whoever else
wants them.

Nobody has to remember anything. There is no session to keep alive and no
evening ritual — each run brings its own stack up from cold, waters, and puts
the robot back.

For bringing a stack up by hand see [RUNBOOK.md](RUNBOOK.md). For why the system
is built this way see [README.md](README.md).

---

## The model

```
controller box (always on)
  cron 08:00 / 17:00
    └─ tools/growmate-water-all.sh
         ├── ssh gh1@192.168.0.38 ─┐   (detached — survives an SSH drop)
         └── ssh farmbot@…0.54 ────┤
                                   └─ tools/growmate-water-run.sh   ON EACH PI
                                        1. is the robot free?   no -> SKIP, log, exit 10
                                        2. launch stack (scheduler:=false)
                                        3. wait for bridge_mode=ros2 + plant count
                                        4. HOME
                                        5. water_all + go_home
                                        6. wait for motion to stop
                                        7. tear the stack down — robot free again
```

The controller does no robot work. It triggers each Pi and collects verdicts.
**The real work runs on the Pi, detached from the SSH session** — so if your
network drops at 08:20, the robot keeps watering and still tears itself down.
You lose visibility, never the watering.

Both greenhouses run **in parallel**. Sequential would put gh2 past 09:00 and
into everyone else's day.

| Time | Robot state |
|---|---|
| 08:00–~08:45 | GrowMate has it: launch, home, water, teardown |
| 09:00–16:00 | **free** — anyone, either stack, nothing of ours is running |
| 17:00–~17:45 | GrowMate has it again, same cycle |
| overnight | **free** — nothing running |

The times are outside 09:00–16:00 on purpose: a cold start needs the robot free,
and that's when it reliably is.

**It will never fight the other team for the robot.** If a `ros2 launch` is
already running, the script skips, logs loudly, and exits non-zero. It does not
kill anyone's session.

---

## 1. One-time setup

### 1a. SSH keys, controller → both Pis

The cron job cannot type a password. From the controller:

```bash
ssh-keygen -t ed25519 -C growmate-controller     # if you don't have a key
ssh-copy-id gh1@192.168.0.38
ssh-copy-id farmbot@192.168.0.54

# must both print 'ok' with no prompt:
ssh -o BatchMode=yes gh1@192.168.0.38 'echo ok'
ssh -o BatchMode=yes farmbot@192.168.0.54 'echo ok'
```

`BatchMode=yes` is what the script uses. If that command prompts or hangs, cron
will hang too.

### 1b. The run script, on each Pi

It ships in this repo, so a pull is enough — but it needs the executable bit:

```bash
ssh gh1@192.168.0.38
cd ~/Rishabh_Growmate_FarmBot && git pull origin main
chmod +x tools/growmate-water-run.sh
ls -l ~/use-rishabh.sh || cp tools/use-rishabh.sh ~/use-rishabh.sh   # §1c of RUNBOOK
```

Repeat on gh2 (`farmbot@192.168.0.54`).

### 1c. The controller script

```bash
chmod +x tools/growmate-water-all.sh
```

Check the greenhouse table at the top matches reality — ssh target, repo path on
that Pi, and expected plant count:

```bash
GREENHOUSES=(
  "gh1|gh1@192.168.0.38|/home/gh1/Rishabh_Growmate_FarmBot|56|"
  "gh2|farmbot@192.168.0.54|/home/farmbot/Rishabh_Growmate_FarmBot|35|src/growmate_pi/config/farmbotdev.yaml"
)
```

The plant count is a **guard, not a label**: if the map doesn't have exactly that
many plants, the run aborts before moving rather than watering a stale or wrong
garden. Set it to `0` to only require "more than zero".

### 1d. Timezone — on the controller

Cron fires there, so its clock is the one that decides when watering happens.
The Pis' clocks no longer matter for scheduling (`--now` ignores
`schedule.watering_time` entirely) — only for log timestamps.

```bash
timedatectl                                  # want: Europe/Dublin
sudo timedatectl set-timezone Europe/Dublin
```

### 1e. The crontab, on the controller

```bash
crontab -e
```

```cron
SHELL=/bin/bash
MAILTO=""

# GrowMate — cold-start watering of every greenhouse at 08:00 and 17:00.
0  8 * * * /path/to/voice-farmbot/tools/growmate-water-all.sh >> $HOME/growmate-controller.log 2>&1
0 17 * * * /path/to/voice-farmbot/tools/growmate-water-all.sh >> $HOME/growmate-controller.log 2>&1
```

`crontab -l` to confirm.

**If the controller is Windows**, run it under WSL: Task Scheduler → daily at
08:00 and 17:00 → `wsl.exe -e bash -lc '/path/to/tools/growmate-water-all.sh'`.
The SSH keys then need to live in WSL's `~/.ssh`, not Windows'.

---

## 2. Prove it works

Do not wait for 08:00 to find out. From the controller, at a time nobody is
using the robot:

```bash
./tools/growmate-water-all.sh gh1        # one greenhouse, right now
```

It blocks for the whole cycle (~45 min) and prints a verdict per greenhouse.
Watch the detail live on the Pi:

```bash
ssh gh1@192.168.0.38 'tail -f ~/growmate-watering.log'
```

Expect, in order: `map_handler -> [rishabh] ok`, `stack ready — plants: 56`,
`homed`, `watering confirmed (status=success)`, `teardown clean — robot is free`,
`RUN END: OK (exit 0)`.

Then confirm the robot really was released:

```bash
ssh gh1@192.168.0.38 'pgrep -af "[r]os2 launch" || echo "robot free"'
```

## 3. Reading the results

The controller log gives you one line per greenhouse per run. The Pi's
`~/growmate-watering.log` has the detail.

| Verdict | Exit | Means |
|---|---|---|
| `OK` | 0 | Watered, confirmed by the firmware, stack torn down |
| `SKIPPED_IN_USE` | 10 | Someone had a `ros2 launch` running — correct behaviour, not a bug |
| `SKIPPED_PORT_BUSY` | 10 | Something was already serving `:8000` — usually a voice session left up (§4) |
| `NOT_READY` / `LAUNCH_FAILED` | 20 | Stack never reached `bridge_mode=ros2`; see `~/.growmate_run/launch-*.log` |
| `EMPTY_MAP` / `PLANT_COUNT_MISMATCH` | 20 | Map wrong or stale — **refuses to water nothing and call it a success** |
| `WRONG_OVERLAY` | 20 | `map_handler` resolved to upstream, not ours |
| `HOME_FAILED` | 30 | Wouldn't water against an unknown position |
| `WATER_PARTIAL` | 40 | Client stopped polling at its 3600s cap; the robot may have finished anyway — check the Pi log |
| `WATER_FAILED` | 40 | Firmware never confirmed |
| `TEARDOWN_DIRTY` | 50 | **Watered, but may still be holding the robot** — go look |

Weekly, on the controller:

```bash
grep -E "RUN END|FAILED|SKIPPED" ~/growmate-controller.log | tail -20
```

## 4. Using voice

Unchanged, with one new thing to remember.

The stack is **down by default** now, so a voice session means bringing it up
yourself ([RUNBOOK §4](RUNBOOK.md) + §6), and **tearing it down when you're
done** — `Ctrl-C` in the launch terminal. If you leave it up, the next scheduled
run finds `:8000` busy and skips.

That failure is loud (`SKIPPED_PORT_BUSY`, exit 10, in the log) rather than
silent, which is the point. But it is still a skipped watering, so: **close the
launch when you finish talking to the robot.**

You never need to stop a schedule to use voice. Outside 08:00 and 17:00 there is
nothing running to stop.

---

## 5. What this cannot protect you from

**A power cut or a rebooted Pi.** Cron on the controller still fires, SSH fails,
the log says so, and nothing waters until the Pi is back. Loud, not silent — but
not self-healing.

**The other team holding the robot at 08:00 or 17:00.** By design it skips rather
than fights. If it starts happening regularly the log will show it, and it's a
conversation, not a config change.

**A latched estop.** If someone stops the robot and doesn't reset, `/intent`
refuses everything. The run fails at the homing step (`HOME_FAILED`) rather than
silently doing nothing — but it still doesn't water. Clear it with
`curl -X POST http://localhost:8000/reset_estop` while a stack is up.

**A stale map.** GrowMate reads `~/.farmbot/active_map.yaml`; the standalone
workspace reads its own install share. If the other team adds or moves plants
with their stack, we may water the old layout. `GM_EXPECT_PLANTS` catches a
count change but not a moved plant. Worth confirming `/plants` against the
physical greenhouse before a long unattended stretch.

**Hardware truth.** None of this has run on a robot yet. In particular the
homing-on-cold-start reasoning (serial reopen resets the Farmduino, hence `R99`
on every launch) is inference from the code and RUNBOOK, not something measured
on gh1. The first real run is §2, watched.

---

## 6. When it misbehaves

| Symptom | Cause | Fix |
|---|---|---|
| Cron does nothing, log empty | Job not firing | `crontab -l`; `grep CRON /var/log/syslog` |
| `FAILED to reach … over SSH` | Key auth not set up for a non-interactive shell | `ssh -o BatchMode=yes <target> 'echo ok'` must not prompt (§1a) |
| Every run `SKIPPED_PORT_BUSY` | A stack was left running — usually after a voice session | `ssh <pi> 'pgrep -af "[r]os2 launch"'`, then Ctrl-C it or kill the pgid |
| `WRONG_OVERLAY` | `.bashrc` auto-sources `~/FarmBot_ROS2`, which wins | Ensure `~/use-rishabh.sh` exists on the Pi (§1b) |
| `NOT_READY` every time | Bringup failing, or firmware not up | `~/.growmate_run/launch-*.log` on the Pi; expect `R99 ARDUINO STARTUP COMPLETE` |
| `EMPTY_MAP` | `active_map.yaml` missing or wiped | Re-seed from `tools/maps/gh1.yaml` into `~/.farmbot` ([RUNBOOK §3](RUNBOOK.md)) |
| `PLANT_COUNT_MISMATCH` | Map changed, or pointed at the wrong garden | Check `/plants`; update `GM_EXPECT_PLANTS` if the garden really changed |
| `HOME_FAILED` | Estop latched, or homing genuinely failing | `reset_estop`; then home by hand to see the real error |
| `WATER_PARTIAL` repeatedly | A full garden takes >3600s | Real; the robot likely finishes. Confirm on the Pi log |
| `TEARDOWN_DIRTY` | Stack ignored SIGINT/TERM | `ssh <pi> 'pgrep -af "[r]os2 launch"'` and kill by pgid |
| Waters at the wrong hour | Controller on UTC | `timedatectl set-timezone Europe/Dublin` (§1d) |
| `pkill -f <pattern>` exits 255 | Pattern matched its own command line | Bracket it: `pkill -f "[r]os2 launch"` |
