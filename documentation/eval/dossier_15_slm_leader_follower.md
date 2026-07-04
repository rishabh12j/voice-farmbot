# Dossier #15 — SLM Zero/One-Shot Leader–Follower Adaptation (Front 4)

**Dossier status: VERIFIED against full text (arXiv HTML v3). Seed description
partially WRONG (no LLaMA-vs-Qwen comparison). KEEP as a narrow supporting
citation for sub-1B on-device classification viability.**

Read 2026-07-04 from arXiv 2602.23312 (v3, 2026-06-02).

## 1. Identity — VERIFIED

- "Evaluating Zero-Shot and One-Shot Adaptation of Small Language Models in
  Leader-Follower Interaction", arXiv:2602.23312 (v1 2026-02-26, v3
  2026-06-02). Authors/affiliation not transcribed from HTML — take from
  metadata. Venue not stated — preprint.

## 2. What it is — VERIFIED

Binary leader/follower role classification for assistive dyadic HRI on edge
hardware. Base model **Qwen2.5-0.5B**; conditions: untrained baseline vs
prompt engineering vs fine-tuning, under zero-shot and one-shot interaction
modes; dataset derived from dialogue resources + synthetic augmentation;
100-phrase test set × 30 independent trials; metrics: accuracy, precision,
recall, F1, tokens/s, latency.

## 3. Key numbers — VERIFIED

- Zero-shot fine-tuned Qwen2.5-0.5B: **86.66% accuracy at 22.2 ms/sample**;
  throughput 432–1851 tokens/s; baseline zero-shot ≈ 55% accuracy.
- Fine-tuning beats prompt engineering on both accuracy and efficiency
  across 30 trials.

## 4. Seed-list claim check

- Seed: "LLaMA-vs-Qwen efficiency/precision trade-offs at the edge" —
  **WRONG**: the study uses only Qwen2.5-0.5B (LLaMA appears in related-work
  citations). Logged as a seed-snippet error.
- "Setting similarity" — moderate: single binary classification on-device
  for assistive interaction; our task is richer (multi-class + entity), but
  the deployment envelope (sub-second, edge) is the same conversation.

## 5. Use for us

- Citable existence proof that *sub-1B* models sustain useful classification
  accuracy at tens-of-ms latency on edge hardware — frames the lower end of
  our model-size sweep (our gemma3:4b mean end-to-end 9331 ms is dominated
  by tree execution + sim walks, not classification; the sweep must report
  classification latency separately to be comparable to numbers like these —
  design note carried into eval_strategy.md).
- Their 30-trial statistical repetition is a protocol element our single-run
  reporting lacks; adopt (N-run variance) for the pinned corpus runs where
  feasible.

## 6. Deviations ledger entries seeded

- **D22**: seed-snippet error (models); also binary task vs our multi-intent
  — accuracy numbers not comparable, only the latency envelope is.

## 7. Not verified / open

- Authors, affiliation, venue.
- Their dataset construction details (synthetic augmentation share).
