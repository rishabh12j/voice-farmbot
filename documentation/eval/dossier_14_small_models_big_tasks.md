# Dossier #14 — Small Models, Big Tasks (Front 4: SLM function calling ≈ flat intent classification)

**Dossier status: VERIFIED against full text (arXiv HTML v1). ACCEPT as the
protocol anchor for our corpus design and the model-size sweep.**

Read 2026-07-04 from arXiv 2504.19277 (v1, 2025-04-27).

## 1. Identity — VERIFIED

- Kavathekar, Donakanti, Kumaraguru, Vaidhyanathan (IIIT-Hyderabad), "Small
  Models, Big Tasks: An Exploratory Empirical Study on Small Language Models
  for Function Calling", arXiv:2504.19277. **Accepted at EASE 2025, AI Models
  and Data Evaluation track** (arXiv comments).

## 2. What it is — VERIFIED

Empirical study of 5 SLMs generating structured function calls from function
descriptions + user query, across domains; three inference regimes
(zero-shot, few-shot, fine-tuned), with and without prompt injection;
**edge-device deployment experiments measuring latency and memory**;
Goal-Question-Metric design; fine-tuned models released.

## 3. Metrics — VERIFIED (§4.5 headline; granular formulas not transcribed)

- Syntactic correctness of emitted calls; semantic accuracy (right function,
  right parameters); adherence to structured output format; robustness under
  prompt injection (slight decline reported); edge latency + memory footprint
  vs model size.
- Headline finding: SLMs show potential but "struggle … with adhering to the
  given output format" — format adherence is a first-class failure mode.

## 4. Seed-list claim check

- "Function calling ≈ our flat intent classification; their accuracy
  protocol may anchor our corpus design" — CONFIRMED as the right framing.
  Their task (query + schema → structured call) is the general form of our
  utterance + fixed enum + live species → {action, target, response}.

## 5. Use for us

- **Protocol anchor for §VI**: multi-regime evaluation (zero/few-shot ×
  model), format-adherence as an explicit metric, edge latency+memory as
  first-class — this is the published template closest to our pending
  model-size sweep. The sweep [EVAL-GAP] specification in eval_strategy.md
  inherits these axes: {model ∈ 2–4B candidates} × {accuracy on the 42-case
  corpus} × {schema-adherence rate} × {on-Pi latency, memory}.
- Their format-adherence finding independently motivates our constrained
  decoding / strict schema parse step: emitting valid structure is a
  known SLM weakness even for flat calls — our design keeps the required
  structure minimal (flat JSON), which is a stated design justification,
  and their study is the citation for why.

## 6. Deviations ledger entries seeded

- **D21**: their domains are API-style tasks, not robot control; corpus
  sizes and per-metric formulas were not transcribed into this dossier —
  extract the exact metric definitions from the PDF before quoting any of
  their numbers (none are quoted in our docs yet).

## 7. Not verified / open

- The 5 SLM identities + per-model results (not needed yet; fetch on demand).
