# HANDOVER — session state as of 2026-07-05

**How to use:** in a fresh Claude session, say "read HANDOVER.md and continue".
This file complements `AGENTS.md`/`CLAUDE.md` (the stable research contract —
read those too) with the CURRENT state: what is done, the measured numbers,
branch topology, and exactly what remains. Auto-memory also exists at the
Claude project memory dir (`project_app_brain_refinement.md` has the same
story, condensed).

---

## 1. Where the project stands (one paragraph)

The robot/executor side is DONE and validated on gh1 hardware (camera
calibrated with the supplier card, tool-verify pin63 reads V0, watering
auto-mounts the nozzle). The voice app ("the brain") went through a full
hardening phase and is DONE: grounded + memory-informed intent classification,
single-pipeline dispatch, honest-or-blank everywhere, all measured (below).
The evaluation foundation for the merged IEEE paper (= thesis final report)
is DONE: metrics verified against the primary source, 17 dossiers, an eval
strategy with a deviations ledger, and a misclassification stress test that
found and fixed a real safety hole. Remaining: **hardware Run 2 on gh1**,
then **paper writing**.

## 2. Branch topology

| Branch | Role | Tip (2026-07-05) |
|---|---|---|
| `main` | Code-canonical. All app/BT/tool code + measured results docs. | `d6bdea9` |
| `emergent-eval` | Paper/eval workspace (Emergent AI works here). Has everything main has PLUS: `documentation/eval/` (17 dossiers, eval_strategy, §VI draft, stress spec, the Gugliermo PDF under `sources/`), and the four `documentation/thesis_*.md` docs (deliberately NOT on main). | `17c05f8` |
| `conflict_040726_2203` | Emergent's stale-base branch, fully merged into emergent-eval — safe to delete. | merged |

Local gotcha: the thesis docs + `documentation/eval/` exist in the MAIN
working tree as **untracked copies** (so the user can edit them daily without
polluting main). When switching to emergent-eval, the session must: check
which local copies differ from the branch (user edits!), preserve those,
delete identical ones, checkout, work, checkout main, `git restore
--source=emergent-eval --worktree -- <paths>` to bring copies back. This
dance was done several times; differing files get committed to the branch.

Uncommitted on main: `src/growmate_pi/config/farmbotdev.yaml` (user's own
in-progress edits — never commit without asking).

## 3. The measured numbers (all sim, 56-plant real-garden map, gemma3:4b)

| Gate | Result |
|---|---|
| verify_sim (BT contract) | 0/12 failures |
| Flow suite `tools/test_app_flows.py` (13 scenarios, app in-process vs sim Pi) | FLOW 42/42, NLP 6/6 |
| Eval `tools/evaluate_v2.py` Run 1 (42 cases, --skip-long) | DBSR 100.0, SNSR 91.2*, USC 0, ELC 100.0 (n=27) |
| Eval Run 1b (FULL 43 cases) | DBSR 100.0, SNSR 92.5*, USC 0, ELC 100.0 (n=28) |
| Stress test `tools/stress_misclassification.py` (120 injections, seed 42) | **unsafe-motion 0**, guard-blocked 20, refused-clean 39, wrong-but-bounded 57, failed-safe 4, honesty violations 0 |

*SNSR is depressed by water_smart CheckDry leaves failing BY DESIGN under a
Selector — decision: SNSR goes to the paper appendix only; headline tables
carry DBSR/USC/ELC. All results + narratives: `demo/eval_v2_results.md`.

**Two real safety holes were found by the tests and fixed:**
1. Duplicate `const todayCard` in the embedded UI JS silently bricked every
   browser handler → fixed + a `node --check` gate in the flow suite.
2. The explicit-coordinate move path published out-of-bounds `M` commands
   unguarded (the stress test's first run caught it) → `CheckBounds` gained
   an explicit-coords mode; the coordinate-move tree now carries it.

## 4. The Gugliermo metrics story (settled — do not reopen)

The metric names ARE from Gugliermo et al. 2024 ("Evaluating behavior trees",
RAS 178:104714; CC-BY PDF archived at `documentation/eval/sources/` on
emergent-eval). Verified nuances: "USC" is the paper's own acronym/metric;
"DBSR"/"SNSR" acronyms are OUR abbreviations of their full metric names.
Real deviations (always disclose): their USC = normalized frequency nU/nT of
ENTERED unsafe states; ours = raw per-case count of guard-BLOCKED attempts
(both 0 — nothing is ever entered, by construction). Their SNSR is per-node;
ours pools. Full audit: `documentation/eval/dossier_01_gugliermo_metrics.md`.
Bonus: the paper PRESCRIBES fault-injection safety evaluation — our stress
harness implements their recommended practice.

## 5. What remains (in order)

### A. Hardware Run 2 on gh1 (192.168.0.38 — IP changed from .39)
**SUPERSEDED 2026-07-05:** follow `demo/run2_runbook.md` instead — the
sim-to-real audit (`documentation/sim2real_gap_audit.md`) found blockers
that reorder this list (diff the Pi BEFORE `git pull`; back up the live
active_map before rebuild; registration only after bringup; T_4/T_5 need a
controller fix first). Condensed original for reference:
1. Pi: `git pull` + colcon rebuild (camera_handler/farmbot_bringup changed).
2. Deploy the 56-plant map: **update ONLY `plant_details`** in the Pi's
   active map — the generated `tools/active_map.yaml` has `tools: null` and
   would WIPE the registered tool bays if copied verbatim.
3. Register the 7-slot tool bay (T_1_0 Soil_Sensor 10.0 1760.0 -285.04 2;
   T_2_0 Watering_Nozzle @1860; T_4_0 Weeder @1960; T_5_0 Rotating_Weeder
   @2060; T_3_0 Seeder @2160; then `CONF M`). Spot-check the +100mm
   extrapolated slots before first automated mount. Trays: S_n_0, y=2260/2360,
   z unverified.
4. Launch: `ros2 launch ./launch/greenhouse.launch.py scheduler:=false`
   (camera included by default now). App: `python -m growmate_voice.app
   --no-ros2 --pi-url http://192.168.0.38:8000/intent` (PYTHONPATH must
   include repo `src`; Ollama with gemma3:4b running).
5. Voice test sheet: `demo/july8_voice_test.md` sections A–F (Phase 0 map
   build = done via hand capture, note it).
6. Run 2 eval: `python tools\evaluate_v2.py --pi-url
   http://192.168.0.38:8000/intent --skip-long --json > run2.json` (REAL
   water + motion; 1–2 h; e-stop in reach).
7. Stress subset: `python tools\stress_misclassification.py --pi-url
   http://192.168.0.38:8000/intent --n 4 --seed 42` (24 injections, ≥20
   required by the strategy).
8. Fill the Run 2 slot in `demo/eval_v2_results.md` — measured numbers only.

### B. Paper writing (the July 8 demo is also imminent — demo assets ready)
- User paste-task: `documentation/eval/section_vi_metrics_draft.md` →
  thesis §1.6 L175 bullet + §VI metrics subsection (user had
  thesis_chapter1_introduction.md open — may be partially done).
- Drafting scaffold: `documentation/thesis_paper_skeleton.md` (merged single
  IEEE paper = thesis final report; framing (a) LOCKED: small on-device LLM
  does intent classification ONLY; social spine; honesty boundary =
  safe-by-construction, NO measured older-adult outcomes may ever be claimed).
- Plan + section budgets: `documentation/thesis_ieee_paper_plan.md`.
- Comparison material: `documentation/eval/eval_strategy.md` (§2 regime
  table with per-cell locators, §3 deviations ledger D1–D27, §5 stress
  design, §9 resolution log) + 17 dossiers.
- Deferred chores: venue-pinning sweep (at reference writing), fresh Front-5
  agriculture-absence sweep (just before submission).

### C. Emergent (app.emergent.sh) — the eval agent
Works on `emergent-eval` with the "Evaluation Archaeologist" persona; task
brief in-repo at `documentation/eval/EVAL_TASK.md`. Next kickoff line for it:
sync from tip `17c05f8`; archaeology is COMPLETE; do NOT build the stress
harness (it exists on main with results); remaining = nothing commissioned.

## 6. Hard-won process rules (violating these burned us)

1. **Measured numbers only, timestamps from `date`** — never estimate a
   timestamp or a result; the user caught a fabricated timestamp once and it
   badly damaged trust. Every reported number must come from an output file.
2. **Honest-or-blank** is the project's soul (AGENTS.md): nothing is spoken/
   logged as done before firmware confirms; same rule applies to OUR
   reporting.
3. Run verify_sim (`wsl -d Ubuntu-22.04 -- bash -lc "cd /mnt/c/... &&
   PYTHONPATH=src ./venv-wsl/bin/python3 -m growmate_pi.verify_sim"`) before
   committing ANY builder/node/schema change — expect Failures: 0/12.
4. Long runs: use detached background (`(cmd > log; echo DONE > sentinel) &`)
   — bash tool timeout caps at 10 min.
5. The full hardware-free test loop: sim server
   (`PYTHONPATH=src python -m growmate_pi.intent_server --no-ros2 --port
   8123`), then flow suite / eval / stress against `localhost:8123`. Windows
   python has all deps (incl. py_trees, uvicorn); Ollama serves gemma3:4b.
6. PYTHONPATH is PREPENDED, never replaced (else rclpy is lost and the Pi
   silently falls back to sim).
7. The eval fallback must NEVER fabricate intents (it once watered the whole
   sim garden repeatedly when Ollama died mid-run — fixed; keep it fixed).

## 7. Key files quick map

- App (brain): `src/growmate_voice/growmate_voice/app.py` (5.6k lines; the
  single dispatch pipeline `_dispatch_pipeline`/`_pipeline_response`),
  `ai_core.py` (ONE prompt = the whole NLP surface; rules 1–16; its
  `construct_tree`/`_tree_*` are dead V1 code).
- Pi (executor): `src/growmate_pi/` — `intent_server.py` (async /intent,
  /plants/*, /events, care_summary), `bt/builder.py` (safety prefix),
  `bt/condition_nodes.py` (CheckBounds w/ explicit-coords mode),
  `event_log.py` (the (action, when, entity, effect) memory).
- Tests: `tools/test_app_flows.py`, `tools/evaluate_v2.py`,
  `tools/stress_misclassification.py`, `src/growmate_pi/verify_sim.py`.
- Tools: `tools/calibrate.py` (jog-capture plants via live R82),
  `tools/calibrate_tools.py` (tool bays), `tools/build_active_map.py`
  (CSV→map), `tools/gh1_plants.csv` (the 56-plant capture, source of truth).
- Docs: `demo/eval_v2_results.md` (ALL results), `demo/july8_voice_test.md`
  (hardware sheet), `demo/RUN_GUIDE.md` (bringup), `PLANS.md` (roadmap —
  somewhat stale vs this file).

## 8. Demo (July 8) readiness

Voice-driven demo on gh1. Everything demoable is sim-verified; hardware
validation happens during Run 2 (section 5A above doubles as demo prep).
Recovery plans are in the test sheet. The demo speaks only sim-verified
claims until Run 2 lands ("sim-verified" phrasing; honesty boundary).
