# Dossier #12 — LLM-as-BT-Planner + companion (Front 1: BT generation with in-context learning & SFT)

**Dossier status: VERIFIED against full texts (arXiv HTML). ACCEPT as one
system/group with two papers; the seed's two entries are NOT independent
evidence — logged.**

Read 2026-07-04. Covers BOTH seed entries:
- arXiv 2409.10444 v3 (2025-06-18) — "LLM-as-BT-Planner: Leveraging LLMs for
  Behavior Tree Generation in Robot Task Planning" — **ICRA 2025** (arXiv
  comments: "presented in ICRA 2025").
- arXiv 2409.09435 v1 (2024-09-14) — "Behavior Tree Generation using Large
  Language Models for Sequential Manipulation Planning with Human
  Instructions and Feedback" — venue not stated; same group, same testbed,
  same Table I numbers. Companion/earlier version of the same line of work.

## 1. What it is — VERIFIED

BT generation for **robotic assembly** (Siemens Robot Assembly Challenge
gearset; Franka Panda + tool-changer). Four in-context generation schemes:
one-step, iterative (simulation feedback), human-in-the-loop, recursive; plus
supervised fine-tuning experiments on smaller LLMs (GPT-3.5, Llama2-13B-chat,
Mistral-7B) vs GPT-4. Real-robot validation of the pipeline.

## 2. Metrics — VERIFIED (both papers, §V-C / §III-B)

- **SR**: "a generation can be taken as a success only if the generated BT is
  executable, logically coherent, and can achieve the goal state."
- **LC (Logical Coherence)**: execution order matches the equivalent action
  sequence without precondition violation (format errors ignored).
- **Exec (Executability)**: follows the regulated format and can be executed
  — explicitly: "an incorrect BT plan is still considered executable, e.g.,
  using a wrong tool" (semantic wrongness ≠ inexecutable).
- **GD** generation duration, **TC** token consumption.

## 3. Key numbers — VERIFIED (Table I/II, GPT-4 unless stated)

- One-step: SR **12/17 (70.58%)**, LC 12/17, Exec 17/17 — "the unsuccessful
  generations are due to the logical incoherence of the BTs"; failures from
  insufficient tree depth and ill-defined actions.
- Iterative (non-specific sim feedback): 12/17 — feedback didn't help.
- Human-in-the-loop: **16/17** SR (best; cost: generation time).
- Recursive: LC 17/17 but SR 13/17, Exec 13/17; 231 s and 50k tokens per BT.
- Fine-tuning small LLMs: improves executability, **not** success — one-step
  LC after fine-tuning still 0/10–1/10 for Llama2-13B-chat/Mistral-7B/GPT-3.5
  ("minimal effect on boosting the success rate … for smaller LLMs").

## 4. Seed-list claim check

- Seed (2409.09435): "70.58% SR, failures from logical incoherence — exactly
  the failure mode our architecture excludes by construction" — CONFIRMED:
  12/17 = 70.58%, and the failure attribution is verbatim.
- Seed (2409.10444): "Metrics, seeds, task suite" — metrics above; task
  suite = 17 assembly tasks (+10 one-step / unit-tree fine-tuning tasks);
  random seeds not reported.

## 5. Use for us — the sharpest quantitative contrast sentence available

Two independent-sounding seed rows collapse into one finding that serves
framing (a) twice:

1. Even GPT-4, one-step, produces logically incoherent trees in ~30% of
   assembly tasks — perfectly executable XML that does the wrong thing. The
   failure class our compiler cannot produce.
2. **Fine-tuning does not buy small models logical coherence** (0–1/10) —
   direct, measured support for the plan's §4 honesty-adjusted claim that
   free-form structure generation is unreliable at small scale (pairs with
   [BTGenBot-24] one-shot@7B; note BTGenBot-2's 2026 counterpoint at 1B with
   validators — cite all three, in tension, honestly: Dossier #10 §5).

Their Exec-vs-LC split is also the cleanest external vocabulary for
explaining why our "executable" is never in doubt and only intent
correctness (our DBSR) can fail.

## 6. Deviations ledger entries seeded

- **D18**: the two arXiv entries are one research line (identical Table I);
  never cite them as two independent data points for the same claim.
- **D19**: their SR is generation-level in a simulated assembly world with a
  17-task suite; no mapping onto our DBSR beyond regime contrast.

## 7. Not verified / open

- 2409.09435's venue (workshop?) — pin or cite only the ICRA 2025 paper.
- Exact overlap/differences between the two papers' experiments beyond
  Table I (the ICRA version adds fine-tuning + real-robot validation).
