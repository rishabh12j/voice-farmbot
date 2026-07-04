# Dossier #4 — SafeGate / Pre-Execution Safety Gate & Task Safety Contracts (Front 2)

**Dossier status: VERIFIED against full text (arXiv HTML v1). ACCEPT as the
closest architectural cousin; number-comparable only at the level of
"unsafe-authorization = 0" style claims, with bridge.**

Read 2026-07-04 from arXiv 2604.05427 (v1, 2026-04-07). CC-BY 4.0.

## 1. Identity — VERIFIED

- Obi, Venkatesh, Wang, Wang, Suh, Amosa, Jo, Min — Purdue SMART Lab +
  Incheon National University. "Pre-Execution Safety Gate & Task Safety
  Contracts for LLM-Controlled Robot Systems", arXiv:2604.05427, Apr 2026.
  arXiv-only as of dossier date (no venue in comments) — pin before citing.

## 2. What it is — VERIFIED

Six-stage neurosymbolic gate in front of an LLM-controlled robot pipeline,
explicitly grounded in ISO 13482 / ISO 12100: (1) LLM-driven Hazard Analysis
Matrix (physical/psychological/operational/consequential), (2) Hazard Binding
Layer against a human-curated Hazard Template Library, (3) **deterministic
Decision Gate** — three-way authorize / defer(-to-human) / reject via a
priority rule cascade (R1 unpreventable severe hazard → reject; R2 unmapped
hazard → terminal defer; R3 critical unknown → defer w/ targeted question; R4
default authorize), (4) compilation of Task Safety Contracts (invariants,
guards, abort conditions), (5) static plan verification via Z3 SMT, (6)
runtime contract monitoring that can halt execution.

## 3. Metrics & protocol — VERIFIED (§V-D)

- **A-safe**: % of known-safe tasks authorized. **A-unsafe**: % of
  known-unsafe tasks authorized (their "safety failure" rate).
- **Deferral rate**; confusion matrix (TP = unsafe correctly blocked, FN =
  unsafe authorized, FP = safe blocked = over-caution, TN); precision /
  recall / F1.
- **Crash rate** (AI2-THOR executions), **task completion rate**.
- Benchmark: 230 expert-curated synthetic tasks (assistive, navigation,
  manipulation × simple/medium/complex); 30 AI2-THOR scenarios; real-robot
  runs for stages 4–6. Baselines: GPT-4o, Gemini 2.5-Flash (prompted
  classifiers), SELP, RoboGuard (constraint-based). McNemar's test for
  paired significance.

## 4. Key numbers — VERIFIED (Tables I, II)

- SafeGate: **A-unsafe 0.0%**, A-safe 92.8%, defer 9.2%; F1 97.1%
  (precision 94.3%, recall 100%); 0.06 false blocks per true block.
- LLM baselines match recall 100% but over-defer: GPT-4o defers 40.6% of
  safe tasks; Gemini 51.5% (A-safe only 12.0%).
- Constraint baselines under-block: SELP authorizes 26%, RoboGuard 39% of
  unsafe commands.
- AI2-THOR: SafeGate blocked all 30 hazardous tasks; RoboGuard authorized
  28/30.

## 5. Seed-list claim check

- "gate before execution … architecturally the closest safety cousin" —
  CONFIRMED, and stronger than the seed suggests: their Decision Gate is
  *deterministic by design*, same design value as our guard chain.
- "What they measure for the gate (block rate? false-block rate?)" —
  ANSWERED: A-safe/A-unsafe + FP/FN confusion + deferral + crash + completion.

## 6. Comparability bridge to GrowMate/VoiceBT

Closest cousin, but gate placement and scope differ:

| Axis | SafeGate | Ours |
|---|---|---|
| Where the LLM sits | LLM analyzes hazards; deterministic gate decides on the LLM's hazard report | LLM only classifies intent; safety logic never consumes LLM safety judgments |
| Gate granularity | per-command, pre-planning + runtime contract monitor | per-action safety prefix inside the compiled tree (CheckAvailable → [CheckToolMounted] → [CheckBounds] → [CheckPlantFound]) + firmware verify gate |
| Standards grounding | explicit ISO 13482/12100 mapping | none yet — **adoptable**: §VIII (ethics/standards) should mirror their ISO mapping for our hazard classes |
| Deferral | first-class three-way decision | confirm gate + refusal; no formal defer taxonomy |
| Metrics | A-unsafe=0 as headline | USC=0 as headline |

Their "A-unsafe 0.0%" and our "USC 0" are the same *shape* of claim
(zero unsafe authorizations/attempts) in different universes — legitimate to
cite side-by-side as convergent evidence that deterministic gating beats
LLM-judged safety, ILLEGITIMATE to tabulate as the same metric.

Adoptable protocol elements for us (feeds §VI + stress test): (a) report a
false-block analogue (over-refusal rate on safe utterances — our DBSR on
non-refusal categories already bounds this; make it explicit); (b) their
FP-per-TP framing; (c) safe-control set mirroring the hazardous set (our
corpus already pairs refusal ghosts with real species — say so).

## 7. Deviations ledger entries seeded

- **D9**: SafeGate's unsafe set = ISO-derived domestic hazards; ours =
  bounds/tool/species violations in one garden domain. "Zero" claims from
  both are not the same denominator; both papers' phrasing must carry the
  denominators.

## 8. Not verified / open

- v1 preprint, 2 months old at dossier time; no peer review yet. Some Table I
  cells rendered with symbols stripped in HTML (α/β thresholds) — re-check
  exact symbol definitions against the PDF before quoting formulas.
