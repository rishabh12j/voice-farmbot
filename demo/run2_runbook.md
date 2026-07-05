# Run 2 plan — pre-hardware fixes + the gh1 hardware day

**Date:** 2026-07-05. This supersedes the Run-2 section of `HANDOVER.md` §5A.
It merges the original runbook with the findings of the sim-to-real audit
(`documentation/sim2real_gap_audit.md`, F1–F8). Phase A is desk work that
must land **before** the robot session — with the current repo, the headline
auto-mount demo fails on hardware (F1) and tool swaps can physically
interleave with BT motion (F2). Phase B is the corrected day-of runbook.

Timeline: demo is **Wed 2026-07-08**; Phase A fits Sun–Tue (today = Sun 07-05).

---

## Phase A — before hardware (desk, ordered)

Process rules apply to every item (CLAUDE.md): plan mode for
safety-prefix/AURA edits, `verify_sim` = `Failures: 0/N` under WSL before
each commit, long runs in background, measured numbers only.

### A0. Pi reconnaissance — do first, needs only SSH (~30 min)
The July 8 sheet pins the Pi to commit `ed6179b` ("bt: auto-mount watering
nozzle") — a **pre-history-rewrite hash this repo no longer contains**. The
Pi may carry the T-command patch this repo is missing (audit F1). Before
anything else, from Windows:

```bash
ssh gh1@192.168.0.38 "cd ~/Rishabh_Growmate_FarmBot && git log --oneline -5 && git status --short"
# capture the full divergence (run from the repo root):
ssh gh1@192.168.0.38 "cd ~/Rishabh_Growmate_FarmBot && git diff HEAD --stat" 
ssh gh1@192.168.0.38 "cat ~/Rishabh_Growmate_FarmBot/src/farmbot_controllers/farmbot_controllers/farmbot_controller.py" > /tmp/pi_farmbot_controller.py
diff /tmp/pi_farmbot_controller.py src/farmbot_controllers/farmbot_controllers/farmbot_controller.py
# also grab the LIVE active map (registered tool bays live here):
ssh gh1@192.168.0.38 "python3 -c \"from ament_index_python.packages import get_package_share_directory as g; print(g('map_handler'))\" 2>/dev/null || ros2 pkg prefix map_handler"
ssh gh1@192.168.0.38 "cat <share>/config/active_map.yaml" > backups/pi_active_map_$(date +%Y%m%d).yaml
```

Outcomes:
- **Pi has a T-handling patch** → port it into the repo as a labelled AURA
  bugfix; that *is* the A1 fix, already field-tested.
- **Pi has no patch** → the HANDOVER "auto-mount validated" claim needs a
  caveat (it may have been validated manually / at ed6179b state we can't
  reproduce); write A1 fresh.
- If the Pi is unreachable before the session, A0 becomes step B0 — but A1
  still gets written now so the fix is ready either way.

### A1. F1 fix — tool-command wire format + controller index range (small)
- `bt/action_nodes.py` (3 sites: 657, 755, 757): publish `T_{n}_1` /
  `T_{n}_2` (underscored) instead of `T{n}_…`.
- `farmbot_controller.py`: extend the match arms to `T_4_0|T_5_0`
  (registration) and `T_4_1|T_4_2|T_5_1|T_5_2` (mount/unmount). Minimal,
  commit-labelled AURA bugfix.
- Tighten the **sim acceptor** (`farmbot_ros2_bridge.py:_sim_maybe_fake_reading`)
  to exactly the grammar the real controller accepts — underscored, index
  1–5 — so verify_sim can never again pass on a string the robot drops.
- Update the verify_sim mount assertion to the new format.

### A2. Wire-grammar contract test (small; pairs with A1)
New `tools/test_wire_grammar.py`: build every builder-emittable tree
(reuse the verify_sim scenarios + tool paths + label_plants P_1 case),
collect `bridge.command_log`, assert **every** published string against an
acceptor function that replicates `farmbot_controller`'s match arms
(M/M_S/H_*/T_*/D_*/I_*/P_*/CONF/e/E). Also assert every configured tool
index (gh1 + farmbotdev yaml) is in the controller's accepted range.
Static — no ROS, no robot. Add to the standard gate list.

### A3. F2 fix — tool-change confirmation means "choreography finished" (medium; plan mode)
- Rewrite `EnsureTool` on the `_ToolChange` poll pattern (3 s `D_C` polls)
  instead of the current single-shot read; for swaps require the observed
  pin-63 sequence 0 → 1 (old head released) → 0 (new head seated), not just
  a fresh 0.
- After the pin confirms, hold RUNNING until **busy quiescence**: no new
  busy completion and `is_busy()` false for ≥3 s (sequencer feeds at 1 Hz,
  so 3 s quiet ⇒ its queue is drained). Apply to `MountTool`/`UnmountTool`
  too (they currently confirm at the *seat*, two choreography moves early).
- Keep the 120 s overall timeout; e-stop checks stay per-tick.

### A4. Sim fidelity: async choreography in the sim bridge (medium; pairs with A3)
Minimal scope for this week: on an accepted `T_n_1/_2`, the sim bridge
schedules the choreography as a timeline (4 busy cycles ≈ 1 s apart) and
flips pin 63 at the **seat step** (mount: cycle 2; unmount: back to 1 at
the release step) rather than synchronously in `publish()`. Add a flow-suite
scenario asserting **no `M` is published between choreography start and
quiescence** — with the old EnsureTool this test fails; with A3 it passes.
(Fault injection — R03 probability, stuck pin 63 — is the follow-up, not
this week; see audit F3/§plan item 6.)

### A5. F3 fix — firmware errors are not confirmations (medium; plan mode)
- Bridge: parse `R03` in `_on_uart`, bump an `error_count`;
  `_VerifiedCommand` snapshots it at publish and returns FAILURE
  ("firmware reported an error") if it grew when the completion arrives.
- `MoveTo` position-verify: after the busy completion, compare
  `bridge.position()` (R82) against the target within a config tolerance
  (default 5 mm) before SUCCESS. Config-gated (`position_verify: true` in
  the garden yaml) so it can be disabled live if R82 cadence on real
  firmware surprises us (instrumented in Phase B).
- **Trim line if time runs short:** ship the R03 flag; defer
  position-verify to post-Run-2.

### A6. F4/F5 config + preflight (small)
- Move soil thresholds into the per-greenhouse yaml:
  `soil: {dry_above: 600, wet_below: 350, calibrated: false}`; plumb
  through `GardenConfig` → nodes. While `calibrated: false`:
  `check_sensor` speaks the raw number without a dry/wet verdict, and
  `water_smart` refuses cleanly ("the soil sensor isn't calibrated yet —
  say water all, or run the calibration") — Q-design, no silent semantics.
- Boot-time tool preflight (real mode): one `D_C` at server start; if
  pin 63 reads 0 while ToolState is None → tool-requiring trees refuse with
  spoken guidance until the operator clears it (hand-unmount, or a
  `POST /tool_state` override). Closes the restart-drift collision case.

### A7. Repo hygiene (tiny)
- Delete or regenerate the stale 54-plant
  `src/growmate_pi/config/active_map.yaml` (the sim server prefers the
  56-plant `src/map_handler/.../config/active_map.yaml`; a stale fallback
  copy is exactly the wrong-map trap).
- `src/growmate_pi/config/farmbotdev.yaml` still has uncommitted local
  edits — decide (commit or stash) before any branch/pull work on this
  machine. Never auto-commit it.

### A8. Full gate sweep (background, ~evening run)
1. `verify_sim` (WSL) — Failures: 0/N.
2. `node --check` gate + full flow suite vs sim server
   (`tools/test_app_flows.py`) — FLOW all-pass.
3. `tools/test_wire_grammar.py` (new) — all strings accepted.
4. Stress `--n 20 --seed 42` vs sim server — unsafe-motion 0,
   honesty violations 0.
5. Sim eval `evaluate_v2.py --skip-long --json > runs/pre_run2_sim.json`
   — DBSR/USC/ELC unchanged vs Run 1 (results are the regression bar, only
   update `demo/eval_v2_results.md` if something moves).

**Suggested day split:** Sun = A0 + A1 + A2 (+ gates); Mon = A3 + A4
(+ gates); Tue = A5 + A6 + A7 + A8 + update the July 8 sheet with the
Phase-B additions below. Demo + Run 2 = Wed.

### Decisions taken (defaults — override if you disagree)
1. **water_smart while uncalibrated → clean refusal** (not water-all
   fallback): matches the honest-or-blank soul; D2 runs only after the
   on-day calibration.
2. **Sim fault-injection scope = choreography timing only this week**; R03
   / stuck-pin injection is post-Run-2 (it hardens tests, not the demo).
3. **Commit style = one commit per fix on main** (repo works on main),
   each behind its own verify_sim gate, AURA edits labelled as bugfixes.

---

## Phase B — the gh1 hardware day (192.168.0.38)

Changes vs the previous runbook are marked **[NEW]** / **[REORDERED]** with
the audit finding that motivates them.

### B0. [NEW — skip if A0 done] Diff before you pull (F1)
Run the A0 capture. **Do not `git pull` until the diff is read.** Port any
Pi-local patch first; at minimum save `/tmp/pi_farmbot_controller.py` and
the live active_map backup.

### B1. Deploy (~10 min)
```bash
ssh gh1@192.168.0.38
cd ~/Rishabh_Growmate_FarmBot
# [NEW] back up the LIVE active map BEFORE pulling (F6 / wipe hazard):
#   with --symlink-install the share config may symlink into src/, so a pull
#   can rewrite the live map (tools:null in the repo copy!) — and a locally
#   modified tracked map can also make the pull refuse. Backup, then deal.
cp $(python3 -c "from ament_index_python.packages import get_package_share_directory as g; print(g('map_handler'))")/config/active_map.yaml ~/active_map.backup.$(date +%Y%m%d_%H%M).yaml
git stash   # if the pull complains about local changes — inspect them first (they may include the live map and the ed6179b-era patch!)
git pull origin main
source /opt/ros/$ROS_DISTRO/setup.bash
colcon build --symlink-install --allow-overriding \
  farmbot_command_handler farmbot_controllers farmbot_interfaces map_handler camera_handler farmbot_bringup
source install/setup.bash
```

### B2. Restore/merge the map — ⚠️ still the one dangerous step (F6)
Rebuild may have replaced the share `active_map.yaml` with the repo copy
(`tools: null`). Reconstruct the live map: start from the **backup** (keeps
`map_reference` with the registered bays), replace only its
`plant_details:` block with the repo's 56-plant one
(`src/map_handler/map_handler/config/active_map.yaml` — same content as
`tools/active_map.yaml`). Put the merged file at the share path. Verify:

```bash
grep -A2 "T2:" <share>/config/active_map.yaml   # expect Watering_Nozzle, not null
grep "plant_count" <share>/config/active_map.yaml  # expect 56
```

### B3. [REORDERED] Bring the stack up BEFORE registering tools
Registration is `ros2 topic pub` → it needs `farmbot_controller` +
`map_controller` running (messages to nobody are silently lost), and the
map file edit (B2) must precede map_controller's start (it loads at init).

```bash
ros2 launch ./launch/greenhouse.launch.py scheduler:=false   # camera default on
# verify: curl http://localhost:8000/status → bridge_mode: ros2, verify_enabled: true
```

**[NEW] Start the black box before any motion** (audit §instrumentation):
```bash
ros2 bag record -o ~/run2_bus /uart_transmit /uart_receive /busy_state /keyboard_topic /input_topic &
```

### B4. Register the tool bay (needs the A1 controller fix for T_4/T_5)
As before (T_1_0 Soil_Sensor @1760, T_2_0 Watering_Nozzle @1860,
T_4_0 Weeder @1960, T_5_0 Rotating_Weeder @2060, T_3_0 Seeder @2160, all
`10.0 … -285.04 2`; then `CONF M`).
**[NEW] Read the map back** before trusting it (F6): expect five `T*`
entries with the right coordinates:
```bash
grep -B1 -A6 "name:" <share>/config/active_map.yaml | head -50
```
Jog-spot-check the three +100 mm extrapolated slots (weeder / rotating /
seeder) before the first automated mount.

### B5. [NEW] Tool-change validation block — before any watering (F2)
Hand on the physical e-stop. One full mount+unmount observed end-to-end:
```bash
ros2 topic echo /uart_transmit &   # watch the choreography G00s live
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'T_2_1'}"
# WAIT for all motion to stop (~20 s) — pin 63 confirms the seat two moves
# before the sequence ends; do not D_C early.
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'D_C'}"   # expect R41 P63 V0
ros2 topic pub --once /keyboard_topic std_msgs/msg/String "{data: 'T_2_2'}" # unmount; wait; D_C → V1
```
Then the same via the BT (one-plant water command from the app): confirm on
the `/uart_transmit` echo that **no `M` appears between choreography
G00s** (this is the A3 quiescence fix working). Note mount duration from
the bag → timeout recalibration later (F8).

### B6. [NEW] Soil-sensor calibration (F4) — unlocks the D2 sheet row
Mount the probe (voice or T_1_1). Take manual readings: probe in air, in
dry soil, in freshly watered soil (`D_S_C` × 3 each, note the R41 values).
Set `soil: {dry_above: …, wet_below: …, calibrated: true}` in the Pi's
gh1.yaml from the measured values; restart the intent server (pure-python:
no rebuild). Record the numbers on the test sheet — they go in the paper's
setup section. **[NEW] Note the R82 cadence** during one long move while
you're watching topics (F3 position-verify tolerance depends on it).

### B7. Windows app up
```powershell
$env:PYTHONPATH = "C:\Users\risha\growmate-bt\voice-farmbot\src;" + $env:PYTHONPATH   # PREPEND
python -m growmate_voice.app --no-ros2 --pi-url http://192.168.0.38:8000/intent
# Ollama serving gemma3:4b
```

### B8. The July 8 sheet — demo/july8_voice_test.md, sections A–F
Phase 0 (map build) done via hand capture — note it on the sheet. C1 is the
headline (auto-mount → water → honest "All done"); C2 idempotence; D1/D2
only after B6; F4 (failed-seat abort) if a slot allows staging it safely.

### B9. Run 2 eval — the paper's hardware numbers
Really moves the robot, really pumps water: supply on, e-stop in reach,
stay present.
```powershell
$env:PYTHONPATH = "src;src/growmate_voice"
python tools\evaluate_v2.py --pi-url http://192.168.0.38:8000/intent --skip-long --json > runs\run2.json
```
Expect 1–2 h for 42 cases. If time + water allow, drop `--skip-long` for
the full 43 (the 56-plant walk ≈ 30–45 min; it is just watering the
garden).

### B10. Stress subset on hardware (strategy floor: ≥20 injections)
```powershell
python tools\stress_misclassification.py --pi-url http://192.168.0.38:8000/intent --n 4 --seed 42 --json runs\stress_run2.json
```
24 injections. Expect **unsafe-motion 0** — the thesis headline, now with
the explicit-coords guard AND the F2 fix in the loop.

### B11. Record
- Fill the Run 2 slot in `demo/eval_v2_results.md` — measured numbers only,
  timestamps from `date`.
- Stop and save the rosbag; pull it to Windows.
- Post-day chores (not on the clock): recalibrate the four timeout
  constants from the bag's per-command busy-cycle p95s (F8); file the R82
  cadence observation against the position-verify design (A5); back-port
  any value changed on the Pi (gh1.yaml soil block) into the repo.

### Recovery notes (unchanged + one addition)
- Nozzle won't auto-mount → hand-mount, re-issue the command (EnsureTool
  no-ops via ToolState).
- **[NEW]** Firmware wedge (busy stuck, everything timing out) → physical
  e-stop, then reset: the priority path clears the UART queue and the
  sequencer; then re-home (F3 audit note).
- Anything weird mid-choreography → e-stop first, diagnose from the bag
  after.
