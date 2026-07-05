# Sim-to-real gap audit — static contract analysis before hardware Run 2

**Date:** 2026-07-05. **Method:** side-by-side read of the growmate_pi sim
bridge against the real AURA command chain, tracing every command the BT can
publish through `panel_controller → farmbot_controller → {movement, devices,
sequencer/map_handler} → UART_controller → Farmduino`, and every feedback
signal back (`/busy_state`, `/uart_receive`). Every claim below carries a
file:line citation. No code was changed; this is the analysis the fixes
should be reviewed against.

**Headline:** the sim bridge is a *more permissive* acceptor than the real
stack, and it resolves state *synchronously* that the real stack resolves
*asynchronously*. Those two properties produce two demo-critical findings
(F1, F2), one thesis-claim finding (F3), and a tail of protocol items. All
are analyzable — and most are fixable — before the robot is powered on.

---

## 0. How the real chain actually works (facts the sim must match)

Traced from source:

1. **Topic path.** The bridge publishes to `keyboard_topic`
   (`farmbot_ros2_bridge.py:64-69`). `panel_controller` re-publishes every
   keyboard command verbatim onto `input_topic`
   (`panel_controller.py:88`), where `farmbot_controller.cmd_interp_callback`
   interprets it in a Python `match` **with no default arm** — any
   unrecognized string is **silently dropped**
   (`farmbot_controller.py:51-208`).
2. **Moves are direct; tools are sequenced.** `M x y z` →
   `move_gantry_abs` → exactly one `G00 …` on `/uart_transmit`
   (`movement.py:115-125`, `motor_cmd_handler.py:33-48`). `H_0` → one `G28`
   (`motor_cmd_handler.py:56-57`). Tool mount/unmount go through the
   map_handler service, which returns a **multi-step sequence** that the
   sequencer feeds **one step per 1 s tick, gated on busy**
   (`sequencer.py:152-256`).
3. **The UART controller owns busy.** Commands queue in an unbounded FIFO
   (`UART_controller.py:50,118`); one command transmits at a time; busy is
   raised at transmit and lowered when:
   - a blocking cmd (`G00 G01 G28 F11-F16 F20 F44`) gets **R02 *or R03***
     — i.e. *finished* or *finished-with-error* both lower busy
     (`UART_controller.py:148-167`);
   - a request cmd (`F42 F21`) gets its `R41/R21` response;
   - any other cmd gets its **R08 echo** — receipt, not execution.
4. **The mount choreography** (`tool_sequencer.py:25-56`): approach above
   slot → **descend to slot (tool seats here — pin 63 → 0)** → slide 100 mm
   in the release direction → raise to safe z → `CHECK 0` (pin-63 read at
   the very end). Unmount is the mirror image ending `CHECK 1`. So **pin 63
   confirms the *seat*, two motion steps before the choreography ends.**
   A failed CHECK clears the whole sequence (`sequencer.py:191-199`).
5. **Workspace dims for tool moves** come from the Pi's live
   `active_map.yaml` (`map_controller.py:52-55`), not from the growmate
   config — the gh1 bay slots (y=1760–2160, z=−285.04) pass the exchanger's
   own bounds check given the 5691.2 × 2734.0 × −380.08 map.
6. **E-stop is sound end-to-end**: keyboard `e` → priority `E` straight to
   serial + queue cleared + busy dropped (`panel_controller.py:71-79`,
   `UART_controller.py:104-114`) and the sequencer's queue is cleared too
   (`farmbot_controller.py:55-57`). This transfers as designed.

What the sim does instead: one fake 0.2 s busy cycle per tracked publish
(`farmbot_ros2_bridge.py:30,385-389`), position snaps to the M target
(`:280-296`), pin 63 flips **synchronously inside `publish()`** for **any**
string shaped like `T<digits>_1/2` (`:312-317`), and `D_S_C` invents a
uniform random soil value (`:298-311`). Nothing ever errors, nothing is ever
mid-flight.

---

## F1 — BLOCKER: the tool-command wire format the BT publishes is not the format the controller accepts

**Evidence.**
- `EnsureTool`/`_ToolChange` publish `f"T{index}_1"` / `_2` → `T2_1`,
  `T4_1`… (`bt/action_nodes.py:657,755-757`).
- `farmbot_controller` accepts **only** `'T_1_1' | 'T_1_2' | 'T_2_1' |
  'T_2_2' | 'T_3_1' | 'T_3_2'` — underscored, indices 1–3
  (`farmbot_controller.py:134-135`); registration likewise only
  `'T_1_0' | 'T_2_0' | 'T_3_0'` (`:131`). No default arm → silent drop.
- gh1 config assigns **weeder index 4, rotating_weeder index 5**
  (`config/gh1.yaml:190-194`) — out of the controller's range even in the
  right format. Same for the HANDOVER runbook's `T_4_0`/`T_5_0`
  registration lines (they would silently no-op).
- `git log` shows `farmbot_controller.py` untouched since the initial
  commit — no growmate patch in this repo.

**The contradiction, resolved.** HANDOVER says auto-mount was validated on
gh1, and `demo/july8_voice_test.md:14` pins the Pi build to commit
`ed6179b` ("bt: auto-mount watering nozzle") — a hash that **no longer
exists here** (the `.git` 789 MB→1 MB shrink rewrote history). So the Pi is
running code this repo cannot reproduce, and the plausible explanation for
the validated mount is a **Pi-local patch (or pre-rewrite commit) that
never made it back to the repo**. Either way the current repo, deployed as
per the Run-2 runbook step 1 (`git pull` + rebuild), **breaks auto-mount**:
every `T2_1` is dropped, the immediate `D_C` reads pin 63 = 1, and
`EnsureTool` fails in under a second — an honest failure, no unsafe motion,
but watering / water_smart / clear_weeds / check_sensor all abort. The
July 8 headline test (C1) dies at step 2.

**Why sim can't see it.** The sim's acceptor is *wider* than the real one:
`c.startswith("T") and c[1:-2].isdigit()` accepts `T2_1`, `T4_1`, `T99_1`
(`farmbot_ros2_bridge.py:312`). verify_sim asserts a `T*_1` appears before
the first move (`verify_sim.py:121-128`) — the assertion passes on a string
the robot would drop.

**Pre-hardware fix.**
1. **First, diff the Pi.** Before any `git pull` on the Pi, capture
   `diff -ru ~/Rishabh_Growmate_FarmBot/src <repo>/src` (at minimum
   `farmbot_controllers/`). If a local T-handling patch exists, that's the
   missing piece — port it into the repo as a labelled AURA bugfix instead
   of letting the pull destroy it.
2. In-repo: extend the controller match to `T_4_*`/`T_5_*` (+ registration
   `T_4_0`/`T_5_0`) as a **minimal, documented AURA bugfix**, and emit the
   underscored format from `action_nodes.py` (three call sites).
3. **Tighten the sim acceptor to the real grammar** (exact strings, exact
   index range) so this bug class can never pass verify_sim again.
4. Add a **wire-grammar contract test**: build every tree the builder can
   emit (reuse the verify_sim scenarios + tool paths), collect every
   published string, and assert each against a small acceptor function
   copied from the controller's match arms. This is a static test — no
   robot, no ROS — and it turns "the sim accepted it" into "the *controller
   grammar* accepted it".

---

## F2 — CRITICAL (physical safety): EnsureTool can confirm against a stale pin-63 state and interleave BT motion with the mount choreography

**Evidence.** `EnsureTool.initialise` publishes *(optional unmount) + mount
+ one `D_C`* in a single burst (`bt/action_nodes.py:753-760`), then
`update()` acts on the **first fresh pin-63 reading**
(`:776-783`):

- **Swap path (probe→nozzle in water_smart; any tool change):** the old
  tool is still seated, so that immediate `D_C` returns **0** → EnsureTool
  returns SUCCESS **instantly**, while the sequencer still holds ~8
  choreography steps (4 unmount + 4 mount) that will execute over the next
  ~30–40 s. The tree proceeds: its verified `MoveTo` publishes `M` → one
  `G00` that enters the UART FIFO **between** choreography `G00`s (moves
  bypass the sequencer, `farmbot_controller.py:59-63`). The gantry departs
  the bay mid-engagement, then the choreography's remaining steps drag it
  back to bay coordinates at z=−285 **across the planted bed**, and pump
  commands can fire anywhere in between. This is exactly the USC class the
  thesis promises cannot happen — reachable on hardware, structurally
  unreachable in sim (the sim flips pin 63 synchronously per publish in
  order, so the final `D_C` always reads the end state:
  `farmbot_ros2_bridge.py:312-321`).
- **Fresh-mount path:** nothing mounted → the immediate `D_C` reads 1 →
  FAILURE in <1 s, before the choreography even starts (feature broken,
  though honestly).
- `MountTool`/`UnmountTool` (`_ToolChange`) are better — they re-poll every
  3 s and treat the wrong value as "keep waiting" (`:665-698`) — but they
  confirm at the **seat**, which per the choreography (§0.4) happens **two
  motion steps before the sequence ends**, so a few-second interleave window
  exists there too.

**Pre-hardware fix.** Make tool-change confirmation mean "choreography
finished", not "pin flipped":
1. Rewrite `EnsureTool` to the `_ToolChange` poll pattern (it currently
   duplicates a worse version of it) — for swaps, wait for pin 63 to pass
   through the *unmounted* state (1) before accepting the mounted state (0).
2. After the pin confirms, require **busy quiescence** before SUCCESS:
   `busy_state` low and no new completions for ≥3 s (the sequencer feeds at
   1 Hz, so quiet ≥3 s ⇒ its queue is drained). Cheap to implement against
   the existing `completion_count()`.
3. **Sim fidelity upgrade so the flow suite can catch this class:** model
   the choreography asynchronously in the sim bridge — on `T…_1`, schedule
   N fake busy cycles over several seconds and flip pin 63 at the *seat*
   step (not at the end), exactly as §0.4 describes. Re-run the flow suite
   + stress harness against that; the current EnsureTool would fail it.

---

## F3 — HIGH (thesis claim): a busy-state completion is not success — R03 firmware errors count as confirmations

**Evidence.** The UART controller lowers busy on **R02 or R03** for
blocking commands (`UART_controller.py:150-151,160-167`); R03 is the
firmware's *error* report (stall, obstruction, e-stop'd axis). For
non-blocking commands busy drops on the **R08 echo** — receipt, not
execution. The bridge counts every True→False edge as a completion
(`farmbot_ros2_bridge.py:176-183`) and `_VerifiedCommand` treats any
counted edge as "firmware confirmed it" (`bt/action_nodes.py:127-129`).
On hardware, a move that stalls against an obstacle reports R03 → busy
falls → the tree **speaks "done"**. The honest-or-blank rule holds in sim
(nothing can fail) and silently breaks exactly when hardware misbehaves —
the case the rule exists for.

**Pre-hardware fix (small, high-leverage).**
1. Parse `R03` in the bridge's `_on_uart` (it already parses R82/R41) and
   record an error flag against the current busy cycle; `_VerifiedCommand`
   returns FAILURE ("firmware reported error") when its counted cycle
   carries the flag.
2. For moves specifically, add **position verification**: after the busy
   completion, compare the next fresh R82 against the M target within a
   tolerance (e.g. 5 mm) before SUCCESS. R82 parsing and live position
   already exist and were hardware-validated via `tools/calibrate.py`. This
   single change upgrades the verified-SUCCESS semantics from "firmware
   finished processing" to "the gantry is where we claimed" — a materially
   stronger statement for the paper, measured not asserted.
3. Note for the wedge case: if the firmware never answers (reset
   mid-command), busy sticks high and the FIFO stalls; every verified node
   times out honestly. Recovery is the e-stop/reset pair — worth one line
   in the demo recovery notes.

---

## F4 — HIGH (feature validity): soil readings will be air readings; thresholds and polarity are uncalibrated code constants

**Evidence.** `check_sensor` and water_smart's sensing pass read at the
plant's stored z (0.0 — `bt/builder.py:436,469` and config plant rows), so
the probe tip is far above the soil when `D_S_C` fires. The label
thresholds (`SOIL_DRY_ABOVE=600`, `SOIL_WET_BELOW=350`) and the
"higher = drier" polarity are explicit *field-calibration TODOs* in code
comments (`bt/action_nodes.py:45-49`), and the sim generates uniform
250–750 noise (`farmbot_ros2_bridge.py:35`) — so sim exercises all three
labels while hardware will produce one near-constant air value. water_smart
will then water everything or nothing; `CheckDry`'s unknown→water default
only covers timeouts, not systematic bias. SNSR/appendix aside, this is the
demo's D1/D2 rows.

**Pre-hardware fix.**
1. Move `SOIL_DRY_ABOVE` / `SOIL_WET_BELOW` / a `sense_z` (probe depth) into
   the per-greenhouse YAML; plumb through `GardenConfig`.
2. Decide whether Run 2 *claims* smart watering: minimum honest path is a
   **calibration step in the runbook** — mount the probe, take manual
   `D_S_C` readings in air / dry soil / watered soil, set thresholds from
   the measured values, and only then run D2. If a plunge move is added,
   bound it by a config `sense_z` and keep `CheckBounds` in front (z_min
   already −500).
3. Until calibrated, have `check_sensor` speak the raw number without a
   dry/wet verdict (the number is honest; the label is not yet).

---

## F5 — MEDIUM (state drift): ToolState is process memory; a restart forgets what's physically on the UTM

**Evidence.** `ToolState` lives in the intent-server process
(`tool_state.py:19-48`). Restart the server with the nozzle mounted →
state is None → the next water blindly runs a fresh mount: the choreography
descends into a bay slot with a tool already on the UTM (collision class if
the slot is occupied; wrong-geometry engagement even if not). Sim always
boots clean, so this never appears in any suite.

**Pre-hardware fix.** On real-mode startup, publish one `D_C`; if pin 63
reads 0 while ToolState is None, mark the state "occupied-unknown" and have
tool-requiring trees refuse with a spoken "there's already a tool on the
head — unmount it first or tell me which it is" until an operator sets it
(tiny `/tool` endpoint or a config default). Optionally persist ToolState
to a state file next to the event log. The demo sheet's "start with no tool
mounted" checkbox already hints at this; make the software enforce it.

---

## F6 — MEDIUM (ops): the Run-2 runbook's 7-slot registration partially no-ops

Same root cause as F1: `T_4_0`/`T_5_0` registration lines fall through the
controller match, so weeder and rotating_weeder never enter the Pi's active
map; even with formats fixed, mounting index 4 would KeyError inside
`map_controller.tool_cmd_interpreter` (`map_controller.py:531-535` indexes
`tools['T4']`). **Fix:** the F1 controller patch covers registration too;
after registering, *read the Pi's active_map back* and verify five `T*`
entries with correct coordinates before the first automated mount (one
`ssh` + `cat`, or the `/plants`-style debug endpoint if present).

---

## F7 — LOW/MEDIUM: unverified camera commands still speak success

`I_1`/`I_2`/`I_4` are dispatched to camera services and gate the
*sequencer*, not `busy_state` (`sequencer.py:163-164,312-356`), and the BT
publishes them unverified (`builder.py:522,531,540`) — so "photo taken" /
panorama TTS fires on publish, not completion. Known limitation on both sim
and real (no busy cycle exists to verify against); on hardware the stitch
takes seconds-to-minutes. Acceptable for Run 2 if the spoken text stays
forward-tense ("taking a photo…"), which is worth a one-line check of the
response strings. scan_bed's 2 s dwell (`builder.py:66`) and the SCAN_Z ↔
I_0 calibration-height coupling (`builder.py:62-65`) are hardware-day
checks on the sheet.

---

## F8 — LOW: timeout constants are sim-blind guesses — collect the data to set them

`MOVE_TIMEOUT_S=90 / PUMP_TIMEOUT_S=15 / HOME_TIMEOUT_S=120 /
TOOL_CHANGE_TIMEOUT_S=120` (`bt/action_nodes.py:35-37,625`) are plausible
(5.4 m at 400 mm/s ≈ 14 s; G28 similar; choreography ~20 s + 1 Hz sequencer
overhead) but unmeasured. During Run 2, record the real per-command busy
cycle times (see instrumentation below) and re-derive the constants from
p95 × margin instead of feel. No pre-hardware change needed.

---

## What transfers cleanly (checked, no action)

- **The guard chain** (`CheckAvailable / CheckBounds / CheckPlantFound /
  CheckToolMounted`) is pure software state — identical behavior sim vs
  real (`bt/condition_nodes.py`). The USC=0 mechanism itself has no
  sim-to-real surface *except* via F2's confirmation semantics.
- **Single verified moves** are structurally in-phase: one `M` → one `G00`
  → one busy cycle, publish-then-baseline ordering is safe
  (`bt/action_nodes.py:99-113`).
- **E-stop**: priority path + FIFO clear + sequencer clear + tick-aware
  `Wait`/`CheckEstop` — the full chain is present in the real stack (§0.6).
- **ToolExchanger bounds** come from the live map dims, which match gh1
  (§0.5) — provided the runbook's "update ONLY plant_details" rule is
  followed (it's already flagged in HANDOVER).
- **Soil/tool pin reads** (`D_S_C`→F42 P59, `D_C`→F42 P63) are proper
  request commands with busy held until the R41 — the ReadSensor
  wait-for-fresh design maps onto the real semantics correctly.

---

## The pre-hardware work plan (ordered)

| # | Action | Effort | Buys |
|---|---|---|---|
| 1 | **Diff the Pi's src/ against repo HEAD** (esp. `farmbot_controllers/`) before any pull; port any local patch back as a labelled bugfix | 30 min on the Pi (can be done over SSH today) | Resolves the ed6179b mystery; protects the only known-working mount path from being overwritten by Run-2 step 1 |
| 2 | **F1 fix**: underscored T commands + controller `T_4/T_5` cases + registration; tighten the sim acceptor to the real grammar | small | Auto-mount works from this repo; weeder becomes mountable; runbook step 3 stops half-no-op'ing |
| 3 | **Wire-grammar contract test** (static: every builder-emitted string vs a controller-grammar acceptor) added to the suite | small | Makes F1's whole class impossible to reintroduce; a genuinely reportable method for the paper |
| 4 | **F2 fix**: EnsureTool → poll pattern + busy-quiescence after pin confirm (also added to MountTool) | medium | Closes the physical interleave hazard — the one finding that could damage the robot |
| 5 | **F3 fix**: R03 → verified FAILURE; position-check after verified moves | medium | Restores honest-or-blank under real firmware faults; strengthens the thesis's SUCCESS semantics |
| 6 | **Sim fidelity upgrade**: async choreography + seat-time pin63 + injectable R03/timeouts in the sim bridge; re-run flow + stress suites | medium | The sim can now *catch* F2/F3 regressions; doubles as the actuator-layer fault-injection story (Gugliermo prescribes exactly this) |
| 7 | **F4/F5 config + preflight**: thresholds & sense_z to YAML; boot-time D_C tool-state preflight; runbook calibration procedure for the probe | small | water_smart claims become measurable; restart-drift hazard closed |
| 8 | **Run-2 instrumentation** (below) | trivial | Data to calibrate timeouts and to validate the busy model empirically |

Items 2, 4, 5 touch the BT safety prefix / AURA stack → per CLAUDE.md they
go through plan mode + `verify_sim` (Failures: 0/N) before commit.

## Run-2 instrumentation (add to the hardware sheet)

Record the whole session; it converts the remaining unknowns into data:

```bash
# on the Pi, before the first voice command (bag everything relevant)
ros2 bag record -o run2_bus /uart_transmit /uart_receive /busy_state \
    /keyboard_topic /input_topic
```

- One mount + one unmount **observed end-to-end** with a hand on e-stop
  before any automated watering: watch `/uart_transmit` to confirm the BT
  never injects an `M` between choreography `G00`s (validates the F2 fix).
- Note the R82 cadence during a long move (are reports streamed or
  end-of-move? the position-verify tolerance depends on it).
- Manual probe calibration readings (air / dry / wet) → thresholds into
  gh1.yaml (F4).
- Per-command busy-cycle durations from the bag → recalibrate F8 timeouts.
- Confirm five `T*` tool entries in the Pi's active_map after registration
  (F6) before the first automated mount.

---

*Method note for the paper: this audit is a static conformance pass between
the executor's command grammar/timing model and the plant's actual
acceptor/feedback semantics — the BT's inspectability makes the emitted
command set finitely enumerable, which is what makes such an audit possible
at all. Worth two paragraphs in the evaluation chapter as the sim-to-real
bridge argument, alongside the fault-injection stress results.*
