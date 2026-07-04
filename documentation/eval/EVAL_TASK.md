# Task: Project evaluation strategy for GrowMate / VoiceBT

You are working in the voice-farmbot repository (GrowMate: voice control of a
FarmBot Genesis XL for elderly/disabled users; VoiceBT: the underlying
framework — a small on-device LLM does *intent classification only*, and
deterministic template-driven code does grounding, behaviour-tree synthesis,
and safety). Execute your Evaluation Archaeologist workflow for this project.

## Scope — read this carefully

There is **ONE paper**: the two companion papers were merged (per supervisor)
into a single extended IEEE paper that doubles as the thesis final report —
see `documentation/thesis_ieee_paper_plan.md` (the merge decision and section
budget table; §VI = evaluation methodology, §VII = results) and
`documentation/thesis_paper_skeleton.md` (the section-by-section scaffold,
framing (a) LOCKED, the social spine, and the honesty boundary).

The evaluation you design is **project-wide, not paper-specific**: one
strategy that simultaneously backs (a) the paper's §VI/§VII, (b) the thesis
assessment of testing & evaluation, (c) the live-demo claims, and (d) the
pending hardware runs. Do not produce separate per-paper evals; produce one
strategy and map each element to where it is consumed.

Two constraints from the paper plan are binding on your design:
- **Honesty boundary:** the paper claims *safe-by-construction* (prevents
  documented harms); it must NEVER claim a measured older-adult outcome —
  there is no user study. Any prior-work comparison implying we measured
  user outcomes is illegitimate; the SUS instrument in `demo/questionnaire.md`
  exists but no study numbers may be claimed.
- **Planned experiment:** a misclassification stress test (inject wrong
  intents, show the BT guards hold USC = 0) is named in the plan as a
  running/pending experiment — treat its design as part of your strategy,
  anchored to however prior work evaluates robustness-under-wrong-input.

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
- `AGENTS.md` — the research contract. The headline invariant is USC = 0 under
  LLM misclassification. `demo/RUN_GUIDE.md` — how runs are produced.
- `documentation/thesis_ieee_paper_plan.md` + `thesis_paper_skeleton.md` —
  the merged paper's plan and scaffold; the Related Work fronts and citation
  keys there (e.g. [VA-Errors-24]-style) define the comparison-system
  candidates. `documentation/thesis_draft_answers.md` has framing history.
  If a comparison system you need is not named in these docs, STOP and ask
  before building a dossier for it.

## Your assignment, in order

1. **Dossier #1 — the metrics source.** Locate and read the actual
   Gugliermo et al. paper (BT evaluation metrics). Verify what DBSR / SNSR /
   USC actually mean there, exactly as defined, with locators. Then diff
   their definitions against our implementations in `tools/evaluate_v2.py`
   (DBSR = expected-command substrings in commands_published; SNSR = fraction
   of leaf nodes success; USC = count of out-of-bounds failures). Every
   mismatch goes in the deviations ledger. If our USC or SNSR differs from
   theirs, say so plainly — the headline invariant depends on USC's
   definition.
2. **Dossiers for each comparison system** named in the paper plan/skeleton
   (both Related Work fronts: assistive/older-adult voice-AI studies, and
   LLM+behaviour-tree / LLM-robot-control systems; plus on-device intent
   classification if the plan names it). Do not invent the list — mine the
   plan and skeleton for it.
3. **Synthesis** per your workflow: common core, comparison table with
   per-cell locators, deviations ledger, [EVAL-GAP] items, fairness check.
   Known gaps to carry, not hide: our Run 1 numbers are SIM-ONLY (hardware
   run on gh1 pending — the doc's Run 2 slot); ELC has no prior-work
   counterpart (project-specific); our corpus is self-authored (42 cases),
   not a shared benchmark; the model-size sweep (accuracy/latency vs model,
   only gemma3:4b measured so far) is an [EVAL-GAP] with the exact runs
   needed; the misclassification stress test is designed-but-pending.
4. **Critic pass** as a separate output, per the persona. Include the
   honesty-boundary check: does any comparison or claim drift into measured
   user outcomes? If so it must be struck.

## Ground rules for this repo

- Do not modify any code or configs. Your outputs are new markdown files
  under `documentation/eval/`: one dossier file per system, and one
  `eval_strategy.md` for the synthesis + critic pass, with a short section
  mapping each strategy element to its consumer (§VI/§VII, thesis
  assessment, demo, hardware runs).
- Our numbers must be cited from `demo/eval_v2_results.md` or regenerated
  via the documented commands — never from memory. There is no robot or
  hardware available to you; do not attempt hardware runs. The sim loop
  (RUN_GUIDE / eval doc "Fastest loop") is available if you need to verify
  a number is reproducible.
