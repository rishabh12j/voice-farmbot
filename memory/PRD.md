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

## What's been implemented (2026-07-04)
- 16 dossiers under `documentation/eval/` (dossier_01…dossier_16), covering
  Front 0 (metrics source), Front 2 (SafeAgentBench, KnowNo, SafeGate),
  Front 3 (VoicePilot, VA-Errors/TACCESS, "hard work", Baughan CHI'23),
  Front 1 (BTGenBot, BTGenBot-2, LLM-BRAIn, LLM-as-BT-Planner + companion),
  Front 4 (Edge-LLM, Small-Models-Big-Tasks, SLM leader-follower),
  Front 5 (agriculture absence + RO-MAN'22).
- `documentation/eval/eval_strategy.md`: common core, §VII regime-comparison
  table with per-cell locators, deviations ledger D1–D26, [EVAL-GAP]
  register, misclassification stress-test design (with the D4 USC-semantics
  resolution), consumer mapping, fairness check, critic pass.
- Key findings: Gugliermo full text inaccessible from this environment
  (ScienceDirect Cloudflare + DiVA outage — access log in dossier_01);
  DBSR/SNSR/USC naming attribution doubtful; USC counts guard-blocked
  attempts (stress test must split unsafe-motion vs guard-blocked); corpus
  is 43 cases, Run 1 executed 42 (`--skip-long`); several seed-snippet
  errors corrected (RG locator → CHI'23; BTGenBot-2 ER-variant numbers;
  2602.23312 model claim).
- No code or configs modified. No hardware runs attempted.

## Backlog / next tasks
- P0: author obtains Gugliermo PDF (institutional access) → close ledger
  D1–D3, update dossier_01 §4/§5.
- P0: user decision on Merino-Fidalgo et al. (RAS 2025) addition
  (eval_strategy §4 item 7 — beyond seed list, needs approval).
- P1: build + run misclassification stress harness (sim) per eval_strategy §5.
- P1: SNSR footnote-vs-appendix decision; full sim pass without --skip-long.
- P2: phase-2 dossiers for skeleton citation keys ([SayCan-22],
  [CodePolicies-22], [BETR-XP-24], [InterpBT-25], [RoboInspector-25],
  [InteLiPlan-24], [VA-Health-21], [SAR-Older-25]).
- P2: venue-pinning sweep (VoicePilot, BTGenBot, "hard work", preprints).
- Run 2 (hardware, gh1) — not available to the agent.
