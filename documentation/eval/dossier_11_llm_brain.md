# Dossier #11 — LLM-BRAIn (Front 1: early canonical LLM→BT system)

**Dossier status: VERIFIED against full text (ar5iv render). ACCEPT as
protocol-contrast citation only — its evaluation is subjective by design.**

Read 2026-07-04 from arXiv 2305.19352 (v1, 2023-05-30).

## 1. Identity — VERIFIED

- Lykov, [Tsetserukou] (Skolkovo Institute of Science and Technology),
  "LLM-BRAIn: AI-driven Fast Generation of Robot Behaviour Tree based on
  Large Language Model", arXiv:2305.19352. Venue: none on arXiv — preprint.

## 2. What it is — VERIFIED

Alpaca-7B fine-tuned (LoRA) on 8.5k self-instruct-style demonstrations
generated with text-davinci-003, to emit BTs from text descriptions; small
enough to run on the robot's onboard computer (claimed).

## 3. Evaluation — VERIFIED (the important part)

- The quantitative experiment is a **human indistinguishability study**:
  participants see robot-behaviour descriptions + BT pairs (one LLM-BRAIn,
  one human-authored) and must say which is which. Mean **4.53/10 correct ≈
  random chance**; t-test against null of 5/10 at 5% significance.
- **No task-success, no execution metric, no safety metric.** Dataset-scale
  ablations are qualitative (1000 samples → node formatting only; more data
  → fewer logical/formatting errors).

## 4. Seed-list claim check

- "evaluated by human indistinguishability, not task success … 4.53/10 …
  likely NOT number-comparable; document as protocol contrast" — CONFIRMED
  on every point. Verdict matches the seed's prediction: NOT number-
  comparable, KEEP as protocol contrast.

## 5. Use for us

- §II framing: the earliest small-LLM→BT paper measured *plausibility to
  humans*, not execution or safety — evidence for the Related Work claim
  that the LLM×BT generation line initially lacked execution-grounded
  evaluation, which later work (Dossiers #10, #12) added. Also a clean
  example of why our eval leads with execution-grounded metrics.
- Self-instruct data generation (model trained on LLM-generated data) is
  the data-bias weakness BTGenBot explicitly criticizes (Dossier #9, §2) —
  the two citations chain nicely in one Related Work sentence.

## 6. Deviations ledger entries seeded

- **D17**: no numeric cell for LLM-BRAIn in any comparison table; its row is
  protocol-descriptive only (eval type = "human discrimination, 4.53/10 ≈
  chance").

## 7. Not verified / open

- Second author + affiliation details from metadata at citation time.
- On-board deployment claim is asserted, not benchmarked (no latency table)
  — do not cite LLM-BRAIn for on-device feasibility numbers.
