# Task: Evaluation section for the GrowMate / VoiceBT papers

You are working in the voice-farmbot repository (GrowMate: voice control of a
FarmBot Genesis XL for elderly/disabled users; VoiceBT: the underlying
LLM-classifies / behaviour-tree-executes framework). Two papers are in
progress: (1) safety + accessibility for vulnerable users, (2) VoiceBT as a
portable framework. Your job is to produce the evaluation strategy for both,
per your Evaluation Archaeologist workflow.

## Where OUR evaluation lives (read these first, they are your locators)

- `tools/evaluate_v2.py` — the eval harness: 42-case corpus (direct/indirect/
  query/general/emergency/multi/safety/refusal/hard/negation categories) and
  OUR implementations of the metrics: DBSR, SNSR, USC, ELC, latency. The
  module docstring attributes the metric family to "Gugliermo et al." — that
  attribution is UNVERIFIED until you read the source paper.
- `demo/eval_v2_results.md` — V1 baseline table + Run 1 (2026-07-04, sim,
  56-plant live map, gemma3:4b): DBSR 100.0 (42/42), SNSR 91.2, USC 0,
  ELC 100.0 (n=27), mean latency 9331 ms. Read its "Regressions or surprises"
  notes: SNSR is depressed by design (water_smart CheckDry condition leaves
  fail under a Selector for moist plants) — any cross-paper SNSR comparison
  must account for this.
- `tools/test_app_flows.py` — app-level behavioural suite (FLOW 42/42,
  NLP 6/6), including negation safety and session-memory pronoun resolution.
- `src/growmate_pi/verify_sim.py` — BT-level sim harness (12 scenarios).
- `AGENTS.md` — the research contract. The thesis invariant is USC = 0 under
  LLM misclassification. `demo/RUN_GUIDE.md` — how runs are produced.
- `documentation/thesis_ieee_paper_plan.md` and
  `documentation/thesis_paper_skeleton.md` — the intended related-work /
  comparison systems (committed on this branch). `documentation/
  thesis_draft_answers.md` has the paper abstracts and framing. If a
  comparison system you need is not named there, STOP and ask before
  building a dossier for it.

## Your assignment, in order

1. **Dossier #1 — the metrics source.** Locate and read the actual
   Gugliermo et al. paper (BT evaluation metrics). Verify what DBSR / SNSR /
   USC actually mean there, exactly as defined, with locators. Then diff
   their definitions against our implementations in `tools/evaluate_v2.py`
   (DBSR = expected-command substrings in commands_published; SNSR = fraction
   of leaf nodes success; USC = count of out-of-bounds failures). Every
   mismatch goes in the deviations ledger. If our USC or SNSR differs from
   theirs, say so plainly — the thesis headline depends on USC's definition.
2. **Dossiers for each comparison system** named in the paper plan
   (LLM+behaviour-tree robot control, voice-controlled agriculture/assistive
   robots, on-device intent classification — whatever the plan actually
   lists; do not invent the list).
3. **Synthesis** per your workflow: common core, comparison table with
   per-cell locators, deviations ledger, [EVAL-GAP] items, fairness check.
   Known gaps to carry, not hide: our Run 1 numbers are SIM-ONLY (hardware
   run pending on gh1); ELC has no prior-work counterpart (V2-specific);
   our corpus is self-authored (42 cases), not a shared benchmark.
4. **Critic pass** as a separate output, per the persona.

## Ground rules for this repo

- Do not modify any code or configs. Your outputs are new markdown files
  under `documentation/eval/` : one dossier file per system, and one
  `eval_strategy.md` for the synthesis + critic pass.
- Our numbers must be cited from `demo/eval_v2_results.md` or regenerated
  via the documented commands — never from memory. There is no robot or
  hardware available to you; do not attempt hardware runs. The sim loop
  (RUN_GUIDE / eval doc "Fastest loop") is available if you need to verify
  a number is reproducible.
- The two papers need partially different evals: Paper 1 leans on safety
  metrics (USC, refusal/negation behaviour, confirm-gate) and accessibility
  protocol (SUS focus-group instrument in `demo/questionnaire.md`); Paper 2
  leans on the framework claim (model-size vs accuracy/latency benchmark —
  currently only gemma3:4b is measured; mark the model-sweep as [EVAL-GAP]
  with the exact runs needed). Keep the mapping explicit.
