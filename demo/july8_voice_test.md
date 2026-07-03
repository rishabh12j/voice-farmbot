# GrowMate — July 8 Demo Voice Test Sheet

**Voice-driven** acceptance pass on **gh1 (192.168.0.38)**. Say each phrase to the
app; mark **PASS/FAIL**; note anything off. Failures go in the log at the bottom —
we fix, redeploy, re-test. The point of the sheet is the **honesty contract**:
nothing is spoken/logged as done before the firmware confirms it (honest-or-blank),
and the BT guards catch misclassification before motion (**USC = 0**).

| | |
|---|---|
| Date | __________ |
| Operator | __________ |
| App build (commit) | `345ae9e` (voice app: honest-or-blank + no premature "done") |
| Pi build (commit) | `ed6179b` (bt: auto-mount watering nozzle) — confirm with `git -C ~/Rishabh_Growmate_FarmBot log --oneline -1` |
| Species used (set **after Phase 0** — must exist in the rebuilt map) | __________ (re-planted garden, e.g. basil / geranium / lily) |

---

## 0. Preconditions (tick before starting)

- [ ] Pi stack up: `ros2 launch ./launch/greenhouse.launch.py config:=…/gh1.yaml` (scheduler off is fine).
- [ ] Intent server running **in real mode** — `GET /status` shows `bridge_mode: ros2` (NOT sim) and `verify_enabled: true`.
- [ ] Windows app started with `--pi-url http://192.168.0.38:<port>/intent`.
- [ ] App header/status shows the robot reachable; a soil-sensor or nozzle is **not** pre-mounted (so the auto-mount is actually exercised).
- [ ] `/busy_state` is live: `ros2 topic echo /busy_state` shows True→False cycles on a test move.
- [ ] Water supply on; a towel/tray under the nozzle for the pump tests.
- [ ] **Emergency stop** physically reachable.

---

## Phase 0 — Build the map (vision voice-label) ⟵ DO THIS FIRST

The garden was re-planted, so the map is rebuilt by **looking** at the plants —
no CSV. `scan_bed → find_plants → label_plants`. Every species-dependent test
below (A4, C, D) depends on this passing. Smoke-verified in the lab
(`tools/smoke_map_builder_flow.py`, `Failures: 0`); hardware accuracy is the unknown.

**Phase-0 preconditions**
- [ ] Camera calibrated (`I_0` succeeded → `camera_calibration.yaml` present). Re-run if unsure.
- [ ] No tool mounted — scanning uses the **camera**, not a tool head.
- [ ] You know roughly which region (left/middle/right/front/back) each species is in, to voice-label.

| ID | Say this / do | Expect | Result | Notes |
|---|---|---|---|---|
| P0.0 | (optional) cap the scan extent | Full bed ≈ **703 spots (~1–1.5 h)**. To shorten, set `SCAN_MAX_X_MM`/`SCAN_MAX_Y_MM` to the planted region (or raise `SCAN_STEP_MM`) in `bt/builder.py`, then restart the intent server. | ☐ done ☐ skip | one-time setup |
| P0.1 | "scan the bed" | Gantry snake-walks the grid, detecting (`I_4`) at each spot; ends *"I've scanned the bed. Say 'find the plants'."* | ☐ PASS ☐ FAIL | watch it actually detects |
| P0.2 | "find the plants" | *"I found N plants. Tell me what they are…"* — **N ≈ the real plant count**. | ☐ PASS ☐ FAIL | is N sane vs reality? |
| P0.3 | "the left bed is basil" (repeat per region + species) | *"Added K basil. M plants left to label."* Each adds a plant via `P_1`. | ☐ PASS ☐ FAIL | repeat per species |
| P0.4 | keep labelling until pending drains | Final label: *"That's everything — map updated."* | ☐ PASS ☐ FAIL | |
| P0.5 | verify the map | `GET /plants` (or ask "how many plants") shows the new plants; the species you labelled now resolve in C/D. | ☐ PASS ☐ FAIL | **gates C/D** |

> If detection accuracy is poor on hardware (wrong N, missed plants), that's the
> item to log and work — it blocks the species tests. Fallback for the demo:
> build the map from `tools/maps/` CSV via `tools/build_active_map.py` (verified
> to produce a valid map) and deploy it to the Pi's `active_map.yaml`.

---

## A. Safety first

| ID | Say this | Expect (honest behavior) | Result | Notes |
|---|---|---|---|---|
| A1 | "stop" | Instant halt; spoken "Stopped. The robot is halted." Matched **before** the LLM. | ☐ PASS ☐ FAIL | |
| A2 | "reset" | "All clear. Ready to go again."; robot re-armed. | ☐ PASS ☐ FAIL | |
| A3 | (gibberish, e.g. "banana helicopter") | Clean "not recognised / I don't understand" — **no motion**. | ☐ PASS ☐ FAIL | |
| A4 | "water the bananas" (species NOT in map) | Clean refusal: "I don't see any bananas in this garden." **No motion, no tool mount.** | ☐ PASS ☐ FAIL | USC check |

## B. Movement (verified moves)

| ID | Say this | Expect | Result | Notes |
|---|---|---|---|---|
| B1 | "go home" | Says "Heading home." (**forward-tense**), gantry homes; no "done" before it arrives. | ☐ PASS ☐ FAIL | |
| B2 | "move to the \<species\>" | "Moving to the \<species\>." Gantry moves to that plant. Forward-tense only. | ☐ PASS ☐ FAIL | |

## C. Watering — the headline (auto tool-mount)

| ID | Say this | Expect | Result | Notes |
|---|---|---|---|---|
| C1 | "water the \<species\>" | 1) announces "Watering N \<species\>" (forward). 2) **mounts the nozzle first** — watch pin 63 → **V0** through the ~20 s choreography. 3) waters each plant (snake order). 4) says **"All done." only after the last plant**. | ☐ PASS ☐ FAIL | **Core test.** Confirm mount precedes first move. |
| C2 | "water the \<species\>" **again** | Nozzle already on → **no re-mount** (idempotent), goes straight to watering. | ☐ PASS ☐ FAIL | |
| C3 | "water everything" | Confirm modal ("Shall I water all N?") → say/confirm **yes** → mounts nozzle (if needed), walks all plants, "All done" at the end. | ☐ PASS ☐ FAIL | |
| C4 | Watch the **event log / today's-care** after C1 | A "watered" row appears **only after** the pump cycle completed — not at dispatch. | ☐ PASS ☐ FAIL | honest log |

## D. Sensing / smart

| ID | Say this | Expect | Result | Notes |
|---|---|---|---|---|
| D1 | "check the soil on the \<species\>" | Mounts soil probe, reads, speaks a **real number** (not a canned phrase). | ☐ PASS ☐ FAIL | |
| D2 | "water the dry \<species\>" | Pass 1: probe each (announces readings). Pass 2: swaps to nozzle, waters **only the dry**; summary "watered X of N, the rest were moist." | ☐ PASS ☐ FAIL | tool swap probe→nozzle |

## E. Misc capabilities

| ID | Say this | Expect | Result | Notes |
|---|---|---|---|---|
| E1 | "turn the lights on" / "lights off" | "Lights on." / "Lights off." (status, not a completion claim). | ☐ PASS ☐ FAIL | |
| E2 | "take a photo" | "Taking a photo for you."; image captured. | ☐ PASS ☐ FAIL | |
| E3 | (a general question, e.g. "does the lettuce need water today?") | A real, data-driven answer (uses live plant state), not filler. | ☐ PASS ☐ FAIL | |

## F. Interrupt & recovery (the demo-safety drills)

| ID | Do this | Expect | Result | Notes |
|---|---|---|---|---|
| F1 | Start "water the \<species\>", then say **"stop"** mid-run | Halts within ~1 plant; overlay → "Robot stopped. Press reset to continue." **No "All done."** spoken. | ☐ PASS ☐ FAIL | |
| F2 | After F1, say/press **reset**, then re-issue the water | Resumes cleanly from a re-armed state. | ☐ PASS ☐ FAIL | |
| F3 | **Pull the Pi network / stop the intent server**, then say "water the \<species\>" | App **speaks** "I've lost the connection to the robot. Please check it's switched on, then try again." — **does NOT** claim it watered. | ☐ PASS ☐ FAIL | honest-or-blank (Task 2.1) |
| F4 | Mid-mount, if the nozzle fails to seat (won't reach V0) | Water **aborts before any pump**; honest failure spoken (no dry-pumping, no "done"). | ☐ PASS ☐ FAIL | fallback: hand-mount + retry |

---

## Results summary

- Phase 0 (map build): ___ / 5 &nbsp;&nbsp; Sections A–F: ___ / 19 &nbsp;&nbsp; PASS: ___ &nbsp;&nbsp; FAIL: ___
- Demo-blocking failures (must fix before July 8): ____________________
- Nice-to-have failures (can demo around): ____________________

## Failure log — work items

| ID | What happened | Suspected cause | Fix / owner | Re-test result |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |

---

### Recovery plan on stage (if something fails live)
- **Nozzle won't auto-mount:** hand-mount the nozzle, then say the water command again (idempotent EnsureTool will no-op and proceed). See C2.
- **Pi connection drops:** the app now says so honestly — restart the intent server, re-check `/status` = `ros2`, retry.
- **A capability fails:** fall back to the proven subset — `go home`, `move to <species>`, `water the <species>` — and skip the failing verb.
