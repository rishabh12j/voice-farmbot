# Dossier #10 — BTGenBot-2 (Front 1: SLM BT generation, strongest current contrast numbers)

**Dossier status: VERIFIED against full text (arXiv HTML v1). ACCEPT; seed
numbers correct but belong to the error-recovery variant — precision matters.**

Read 2026-07-04 from arXiv 2602.01870 (v1, 2026-02-02).

## 1. Identity — VERIFIED

- Izzo, Bardaro, Matteucci (POLIMI, same group as Dossier #9), "BTGenBot-2:
  Efficient Behavior Tree Generation with Small Language Models",
  arXiv:2602.01870, Feb 2026. arXiv-only at dossier date.

## 2. What it is — VERIFIED

1B-parameter SLM (base: Llama-3.2-1B-Instruct) fine-tuned on a synthetic
dataset of **5,204** instruction→BT pairs; emits executable ROS2-compatible
BTs zero-shot; optional error-recovery (ER) loop with two validators
(inference-time + runtime) that regenerate on failure. Introduces the
**first standardized benchmark for LLM-based BT generation: 52 tasks**
(navigation + manipulation, easy 18 / medium 18 / hard 16) in NVIDIA Isaac
Sim; real-robot spot-checks (AgileX Scout, SO-ARM 101).

## 3. Metrics — VERIFIED (§IV, citing [14] for functional/non-functional split)

- **SR (Success Rate): "a BT is successful if it is executable and achieves
  the goal state."** ← the seed's "verify first" question, ANSWERED: SR is
  executability AND task completion in sim.
- Pass@3; inference time; non-functional: Action Coherency, XML Syntax,
  Semantic Correctness (3 human experts, binary, majority vote).

## 4. Key numbers — VERIFIED (Table I)

- Zero-shot avg SR: BTGenBot-2 **84.61%**; with ER **90.38%**.
- One-shot avg SR: BTGenBot-2 **92.38%**; with ER **98.07%**.
- Baselines (zero-shot avg SR): GPT-5 Thinking 71.15%, GPT-5 Instant 65.38%,
  Claude Opus 4.1 65.38% (76.92% w/ ER), BTGenBot-1 57.69%.
- Up to 16× faster inference than BTGenBot-1; hard tasks remain the weak
  spot (12/16 zero-shot even with ER).
- Seed-list correction: the seed's "SR 90.38% zero-shot / 98.07% one-shot"
  are the **BTGenBot-2-ER** numbers; plain BTGenBot-2 is 84.61 / 92.38.
  Always name the variant.

## 5. Comparability bridge to GrowMate/VoiceBT

- Strongest available evidence that the generation route *can* be made to
  work at small scale — the paper Related Work must not strawman "small
  models can't generate trees". The honest contrast: even at state of the
  art, generation yields 84.6–98.1% SR **on its own benchmark**, with the
  residual failures being structural/logical (hard tasks), and requires
  validators + regeneration loops to approach the ceiling; our architecture
  makes the structural failure class empty by construction and pays instead
  with a fixed verb vocabulary (no novel task compositions). That is the
  real trade: open-vocabulary competence vs closed-vocabulary safety.
- Their SR ≠ our DBSR: different environments (Isaac Sim benchmark vs live
  56-plant map), different fail surfaces. Regime comparison only.
- Their non-functional metrics (action coherency / syntax) have NO analogue
  for us — by design those cannot fail; the paper can state this as a
  metric-that-vanishes, which is a crisp way to render the contrast in §VII.
- Their 1B-on-consumer-GPU deployment claim also touches Front 4 (on-device)
  — cross-reference in the strategy's coverage matrix.

## 6. Deviations ledger entries seeded

- **D16**: any quoted SR must name variant (base/ER) and prompting mode
  (ZS/OS) and the fact that it is their own 52-task benchmark. The seed's
  unqualified numbers are exactly the kind of drift the ledger exists for.

## 7. Not verified / open

- arXiv-only; no peer review yet.
- Real-robot success figures rendered with missing values in HTML ("achieved
  a success rate of [؟] with Pass@3") — DO NOT quote real-robot numbers
  until read from the PDF.
