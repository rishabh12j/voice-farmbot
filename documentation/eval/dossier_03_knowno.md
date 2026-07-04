# Dossier #3 — KnowNo / "Robots That Ask For Help" (Front 2: refusal/uncertainty)

**Dossier status: VERIFIED against full text (ar5iv render of arXiv 2307.01928
v2). ACCEPT as protocol contrast for the confirm-gate; NOT number-comparable.**

Read 2026-07-04.

## 1. Identity — VERIFIED

- Ren, Dixit, Bodrova, Singh, Tu, Brown, Xu, [et al.] (Princeton + Google
  DeepMind), "Robots That Ask For Help: Uncertainty Alignment for Large
  Language Model Planners", **CoRL 2023, Oral** (arXiv comments field),
  arXiv:2307.01928 v2 (2023-09-04). Project: robot-help.github.io.

## 2. What it is — VERIFIED

Conformal prediction (CP) over LLM-planner options: the LLM proposes candidate
next steps as multiple-choice; CP (calibrated on a held-out set of scenarios)
yields a prediction set at user-specified error level ε. Singleton set →
execute; larger set → ask the human to disambiguate. Provides a *statistical
guarantee*: task success ≥ 1−ε (with human help), while minimizing help.

## 3. Metrics & protocol — VERIFIED

- Target error rate ε (user-set; varied 0.25→0.01 in sim); measured deviation
  of empirical success from target (Fig. 3).
- Task success rate vs **human help rate** curves; prediction-set size.
- Hardware multi-step tabletop (Table 1): KnowNo Plan Succ 0.75/Task Succ
  0.74-ish, help-step 0.58, help-trial 0.92 vs Simple Set help-step 0.72
  (KnowNo −14% step-wise, −8% trial-wise at matched plan success; 50 trials).
- Hardware mobile manipulator kitchen (Table 2): Task Succ 0.76 (PaLM-2L),
  help 0.67; No Help baseline Task Succ 0.51.
- Baselines: Simple Set, Ensemble Set, Prompt Set, Binary ("Certain/
  Uncertain" prompting), No Help.
- Guarantee is robust to LLM choice (PaLM-2L, PaLM-2L-IF, GPT-3.5): weaker
  LLM ⇒ CP compensates with more human help.
- Scope note (their App.): guarantee covers *planning*; low-level control
  failures (~86% success in mobile setting) sit outside the bound.

## 4. Seed-list claim check

- "conformal-prediction help-asking … calibrated uncertainty → ask a human" —
  CONFIRMED.
- "their help-rate/success-guarantee protocol" — CONFIRMED as above.

## 5. Comparability bridge to GrowMate/VoiceBT

KnowNo is the *principled probabilistic* version of the same design instinct
behind our confirm gate — but the mechanisms are disjoint:

| Axis | KnowNo | Ours |
|---|---|---|
| Uncertainty handling | calibrated CP set over LLM options; statistical 1−ε success guarantee | none probabilistic: grounding either resolves the intent or the pipeline refuses/confirms deterministically |
| Asking for help | triggered by non-singleton prediction set | confirm gate + clean refusal utterances; e-stop words bypass LLM entirely |
| Guarantee type | statistical (needs calibration set, distributional assumption) | by construction (guards hold regardless of LLM confidence) |
| What is guaranteed | task completion w/ help | no unsafe motion (USC=0); wrong intents degrade to safe no-ops |

Honest use in §II/§VII: cite as the strongest representative of
"uncertainty-aware asking" and state plainly that we trade their coverage
guarantee (which requires calibration data and admits ε failures) for a
deterministic, narrower guarantee (no unsafe action ever enters execution;
correctness is *not* guaranteed, safety is). No number from Table 1/2 is
comparable to our DBSR (their tasks: tabletop/kitchen manipulation with
ambiguity; ours: fixed-verb garden commands).

Their protocol IS a legitimate anchor for the misclassification stress test:
they explicitly evaluate under injected ambiguity with the metric pair
(success, help-rate). Our stress test mirrors this with (USC, refusal/no-op
rate) under injected wrong intents — see eval_strategy.md §stress-test.

## 6. Deviations ledger entries seeded

- **D8**: "confirm-gate ≈ KnowNo" must never be written as equivalence; ours
  has no calibration and no statistical guarantee — different claim class.
  (This cuts both ways and the text must say so.)

## 7. Not verified / open

- Exact per-table numbers transcribed from the ar5iv render; if a number is
  quoted verbatim in the paper, re-check against the CoRL PDF at citation
  time (formatting in HTML dropped some symbols, e.g. ε values in prose).
