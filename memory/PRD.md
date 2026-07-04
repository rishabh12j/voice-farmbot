# PRD — GrowMate / VoiceBT Evaluation Archaeology (documentation-only task)

## Original problem statement
Existing repo (voice-farmbot, branch emergent-eval). No app building. Execute
`documentation/eval/EVAL_TASK.md`: dossier every seeded comparison candidate
(or reject with logged reason), verify the Gugliermo et al. metrics
attribution against `tools/evaluate_v2.py`, build one project-wide
`eval_strategy.md` (synthesis + deviations ledger + [EVAL-GAP]s + fairness +
misclassification stress-test design + consumer mapping + critic pass).
Markdown outputs only under `documentation/eval/`; never modify code/configs;
our numbers only from `demo/eval_v2_results.md` (Run 1, SIM-ONLY). Honesty
boundary: safe-by-construction only, no measured older-adult outcomes.

## User choices
- Web retrieval allowed for source papers.
- Deviations ledger location: per EVAL_TASK (inside eval_strategy.md).
- Continue through the full work order in one session.

## What's been implemented (2026-07-04 → 2026-07-05)
- 17 dossiers under `documentation/eval/` (dossier_01…dossier_17): Front 0
  (metrics source — **fully verified 2026-07-05 from the author-supplied
  PDF**, archived in `documentation/eval/sources/`), Fronts 1–5 per the seed
  list, plus approved addition Merino-Fidalgo et al. (RAS Dec 2025,
  dossier_17).
- `documentation/eval/eval_strategy.md`: common core, §VII regime-comparison
  table with per-cell locators, deviations ledger D1–D26, [EVAL-GAP]
  register, misclassification stress-test design (with the D4 USC-semantics
  resolution), consumer mapping, fairness check, critic pass.
- Key findings/decisions: Gugliermo verified — names+intent theirs, USC is
  their "(NEW)" metric+acronym, DBSR/SNSR acronyms ours; confirmed deviations
  (USC nU/nT entered-states vs our raw blocked-attempt count; SNSR per-node
  vs pooled); D1/D3 closed, D5 confirmed; SNSR moved to appendix (author
  decision); stress harness confirmed absent from git; USC blocked-attempt
  semantics (D4) still drives the stress-test split; corpus is 43 cases, Run
  1 executed 42 (`--skip-long`); seed-snippet errors corrected (RG locator →
  CHI'23; BTGenBot-2 ER-variant numbers; 2602.23312 model claim).
- No code or configs modified. No hardware runs attempted.

## Backlog / next tasks
- P0: author applies Dossier #1 §7 wording to `thesis_chapter1_introduction.md`
  L175 and the §VI draft (agent may not modify those files under the eval
  task's ground rules).
- P0: build + run misclassification stress harness (sim) per eval_strategy §5
  — confirmed NOT in git as of 2026-07-05; needs explicit user go-ahead since
  it means writing code.
- P1: full sim pass without --skip-long (43/43).
- P2: phase-2 dossiers for skeleton citation keys ([SayCan-22],
  [CodePolicies-22], [BETR-XP-24], [InterpBT-25], [RoboInspector-25],
  [InteLiPlan-24], [VA-Health-21], [SAR-Older-25]) — deferred per user.
- P2: venue-pinning sweep (VoicePilot, BTGenBot, "hard work", preprints) —
  deferred per user. (LLM-as-BT-Planner pinned: ICRA 2025,
  10.1109/icra55743.2025.11128454.)
- Run 2 (hardware, gh1) — not available to the agent.
