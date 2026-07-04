# Dossier #9 — BTGenBot (Front 1: LLM×BT generation, the architecture contrast)

**Dossier status: VERIFIED against full text (arXiv HTML v2). ACCEPT as the
primary [BTGenBot-24] contrast citation; POLIMI attribution confirmed.**

Read 2026-07-04 from arXiv 2403.12761 (v2, 2025-01-07).

## 1. Identity — VERIFIED

- Izzo, Bardaro, Matteucci — **AIRLab, Politecnico di Milano** (repo:
  github.com/AIRLab-POLIMI/BTGenBot). "BTGenBot: Behavior Tree Generation for
  Robotic Tasks with Lightweight LLMs", arXiv:2403.12761 v2. Venue: not in
  arXiv comments (widely cited as IROS 2024) — **pin venue before citing**.
- Resolves the paper plan §6 open item: "I attributed [BTGenBot-24] to the
  POLIMI group from memory; confirm" → CONFIRMED.

## 2. What it is — VERIFIED

Fine-tunes ≤7B open LLMs (Llama-2-7b→Alpaca, Llama-2-7b-chat, CodeLlama-7b-
Instruct; LoRA/PEFT) on an instruction-following dataset built from BTs
harvested from open-source robotics projects (GPT-3.5 writes the natural-
language descriptions; <1000 samples). The LLM EMITS the whole BT in XML.

## 3. Evaluation protocol — VERIFIED

- Nine task descriptions (navigation + manipulation).
- Phase 1: **syntactic correctness** = the XML is accepted by Groot2;
  compared zero-shot vs one-shot, base vs fine-tuned (Table II). Base models
  zero-shot are poor; one-shot or fine-tuning needed for consistent syntax.
- A semantic evaluation (description↔tree match) done "for the sake of
  completeness".
- Phase 2 (Table V): validation of generated BTs per task with a custom
  validator; results reported as per-task check-marks for LlamaChat/CodeLlama
  under ZS / OS / OS+SA (self-alignment correction) — NOT as a single
  percentage.
- Also shows Llama-2-13B-chat un-fine-tuned fails most tasks: "even larger
  LLMs may struggle to generate syntactically and semantically correct
  behavior trees without an accurate prompt or fine-tuning".

## 4. Seed-list claim check

- "lightweight-LLM BT generation … they fine-tune the LLM to EMIT trees" —
  CONFIRMED.
- "Their success-rate definition, task set, robot platform" — task set: 9
  described tasks; success = per-task validator pass (binary per task, per
  prompting mode); platform: evaluation is generation-level (Groot2/
  validator), not a physical-robot success campaign in this paper.

## 5. Why this is the paper plan's exact evidence — VERIFIED

The plan (§4) needs: "free-form structured generation is unreliable — it
needs one-shot prompting even at 7B [BTGenBot-24]". SUPPORTED with locators:
base-model zero-shot syntax failures (Table II) + the 13B-chat result +
their own conclusion that fine-tuning/one-shot is required. Quote-shape to
use: at 7B, syntactically valid BT emission required either an in-prompt
example or task-specific fine-tuning; zero-shot base models largely failed.

## 6. Comparability bridge to GrowMate/VoiceBT

- Same model class (small LLMs), opposite division of labour: they invest
  the model's capacity in emitting structure and then must *validate* the
  structure after the fact (Groot2 + validator + self-alignment repair);
  we never let structure exist un-validated because it is compiled from
  templates. Their eval layers (syntax → semantics → validation) measure
  exactly the failure surface our design deletes; our corresponding surface
  is intent-classification accuracy only (DBSR-under-classification).
- No number of theirs maps onto DBSR/SNSR/USC. The comparison row for
  Table I (technical axes) is categorical: LLM output type (tree vs flat
  intent), failure surface (syntax/logic vs misclassification), guarantee
  (post-hoc validation vs by-construction safety prefix).

## 7. Deviations ledger entries seeded

- **D15**: their "success" is generation validity per task, not execution
  success on a robot in this paper; any prose implying robot task success
  for BTGenBot must be struck (BTGenBot-2 has the executable benchmark —
  see Dossier #10).

## 8. Not verified / open

- Venue (IROS 2024 believed, unpinned here).
- Table V exact per-task tick pattern quoted only partially above; re-check
  if a specific per-task claim is made in the paper text.
