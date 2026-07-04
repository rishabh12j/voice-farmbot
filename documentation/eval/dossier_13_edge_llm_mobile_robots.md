# Dossier #13 — Edge LLM Deployment for Mobile Robots (Front 4: on-device/edge)

**Dossier status: VERIFIED against full text (arXiv HTML v3). DOWNGRADE from
the seed's expectation: it is a thin two-model case study, not an
accuracy-latency protocol. KEEP as evidence of the cloud/edge accuracy gap.**

Read 2026-07-04 from arXiv 2405.17670 (v3, 2024-10-10).

## 1. Identity — VERIFIED

- Sikorski, Schrader, Yu, Billadeau, Meenakshi, Mutharasan, Esposito, [Ali?]
  (Saint Louis University; NSF #2201536), "Deployment of Large Language
  Models to Control Mobile Robots at the Edge", arXiv:2405.17670 v3. Venue
  not stated on arXiv — preprint status assumed; pin before citing.

## 2. What it is — VERIFIED

Case study: voice → offline STT → LLM converts NL to a fixed command string
format (e.g. "f,100") → wirelessly to a Raspberry Pi Pico W smart car with
ultrasonic sensor. Compares **GPT-4-Turbo (cloud)** against **LLaMA-2-7B
Q5_K_M quantized (offline, llama.cpp)** on the same instruction set.

## 3. Evaluation & numbers — VERIFIED

- Test instructions (~23 commands incl. multi-step; 3 trials each; Table II).
- **GPT-4-Turbo: 85% passing accuracy. Quantized LLaMA-2-7B: 13%.**
- Offline model failure modes: cluttered outputs, wrong magnitudes ("move
  forward 50 cm" → 450 cm) — i.e., unusable raw for control.
- No systematic latency table; no model-size sweep; no quantization ladder.

## 4. Seed-list claim check

- Seed: "Accuracy/latency trade-off protocol for edge LLM robot control —
  the axis our model-size sweep [EVAL-GAP] needs." — **PARTIALLY WRONG**:
  there is no trade-off *protocol* here (two models, accuracy only,
  anecdotal latency). What it DOES give: a measured, citable instance of the
  cloud-vs-edge accuracy cliff for direct command generation at 7B-quantized.

## 5. Use for us

- Supports the on-device row's difficulty premise: naive edge deployment of
  a quantized 7B for *command generation* collapsed to 13% — while our
  pipeline gets DBSR 100% (42/42, sim, gemma3:4b) by asking the on-device
  model for flat classification only, with grounding/synthesis downstream.
  That contrast is fair ONLY if stated as: different tasks (string command
  emission vs constrained classification), different rigs; the point is the
  *task design*, not the model.
- Our model-size sweep [EVAL-GAP] is NOT covered by prior work on this
  front; this dossier confirms the gap is real (the seed's hoped-for
  protocol does not exist here). The sweep design must be specified by us —
  see eval_strategy.md.

## 6. Deviations ledger entries seeded

- **D20**: do not describe this paper as an accuracy–latency study; it is an
  accuracy comparison with qualitative latency remarks.

## 7. Not verified / open

- Venue; final author list.
- Exact instruction count in Table II (transcribed as commands 1–23) —
  re-check PDF if the N is quoted.
