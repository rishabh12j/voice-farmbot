# Dossier #17 — Merino-Fidalgo et al. 2025 (Front 1/3 bridge: LLM-generated BTs on a social robot for elderly-targeted use)

**Dossier status: identity VERIFIED (Crossref); content verified from the
UVaDoc open-access copy via tool-based extraction (2026-07-05). Approved
addition beyond the seed list (author approval given 2026-07-05).**

## 1. Identity — VERIFIED (Crossref)

- Merino-Fidalgo, Sánchez-Girón, Zalama, Gómez-García-Bermejo, Duque-Domingo
  (Universidad de Valladolid), "Behavior tree generation and adaptation for a
  social robot control with LLMs", **Robotics and Autonomous Systems, Dec
  2025**, DOI 10.1016/j.robot.2025.105165. Open-access copy: UVaDoc
  (uvadoc.uva.es/bitstream/handle/10324/78652/Behavior-tree-generation.pdf).

## 2. What it is — VERIFIED

ChatGPT (cloud) interprets natural-language commands and **generates BTs**
for a **Temi social robot** in domestic/assistive settings, explicitly
targeting **elderly users**; a Clarifier module handles ambiguous/wrong
input via clarification requests, and a **Failure Interpreter** lets the LLM
**adapt the BT at runtime** after execution failures. Predefined emergency
tasks carry highest priority.

## 3. Evaluation — VERIFIED (their §4.2, Table 2)

- **125 tests** = 25 trials × 5 instruction types (Simple, Complex,
  Ambiguous, Wrong, Impossible). **Overall success rate 89.6%** (Simple
  25/25; Complex 21/25; per-type in their Table 2, incl. clarified/adapted
  splits).
- User study: **10 participants aged 25–65**, university-educated, no prior
  experience; task-execution success 88%; **QUESI** usability questionnaire
  (subjective mental workload M=4.47, goals M=4.17, learning effort M=4.47,
  familiarity M=4.37, perceived error rate M=4.05).
- Latency: acknowledged qualitatively as a challenge (plan-revision delays);
  **no numeric latency reported**.
- Safety: no deterministic pre-execution safety structure; robustness comes
  from LLM-side clarification/adaptation + prompt-enforced structural
  validity + priority emergency tasks.

## 4. Why it was added (and the two-front role)

- **Front 1**: the most GrowMate-adjacent generation system — LLM emits AND
  runtime-adapts trees for an assistive domestic robot. The runtime-
  adaptation loop is the maximal version of what our architecture forbids:
  the tree can change mid-execution on LLM judgment. Contrast sentence for
  §II: they buy open-ended flexibility (89.6% over five instruction types,
  incl. handling Wrong/Impossible via clarification) at the price of
  LLM-in-the-safety-loop and cloud dependence; we buy determinism, offline
  privacy and by-construction safety at the price of a fixed verb set.
- **Front 3 (boundary twin)**: the paper *targets* elderly users but its
  study population is **aged 25–65** — elderly-targeted, not
  elderly-evaluated. Same honesty-boundary shape as ours; when citing, never
  describe their study as an older-adult study. Their QUESI usage joins
  VoicePilot's SUS as instrument precedent for our future-work study.

## 5. Comparability bridge

| Axis | Merino-Fidalgo | Ours |
|---|---|---|
| LLM role | generates + runtime-adapts trees (ChatGPT, cloud) | flat intent classification only (on-device 4B) |
| Wrong/ambiguous input | LLM clarifier dialogue | deterministic refusal + confirm gate |
| Safety | prompt-level validity + priority emergency tasks | guard chain before any motion; e-stop pre-LLM |
| Numbers | 89.6% over 125 trials on their 5-type protocol | DBSR 100% (42/42, sim) on our 43-case corpus |
| Comparable? | regime contrast only — their trial types ≠ our categories; cloud ≠ edge | |

Their Wrong/Impossible instruction types are the closest published analogue
to our refusal/negation categories — cite when motivating those corpus
categories (they too test deliberately bad input; they resolve by dialogue,
we resolve by refusal).

## 6. Deviations ledger entries seeded

- **D27**: their 89.6%/88% are cloud-LLM, dialogue-assisted, 5-type protocol
  numbers; regime contrast only. Their participants are 25–65 — any Front 3
  use must carry "elderly-targeted, not elderly-evaluated".

## 7. Not verified / open

- Full text read via tool extraction, not page-by-page; before quoting Table
  2 sub-cells (clarified/adapted splits) verbatim, re-check against the PDF
  (UVaDoc was intermittently unreachable from this environment; retry or use
  the DOI).
