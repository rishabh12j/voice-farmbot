# Dossier #5 — VoicePilot (Front 3: assistive voice robots, older adults)

**Dossier status: VERIFIED against full text (arXiv HTML v2). ACCEPT as the
closest assistive-domain comparator; instruments adoptable; numbers citable as
context, not head-to-head.**

Read 2026-07-04 from arXiv 2404.04066 (v2, 2024-07-17).

## 1. Identity — VERIFIED

- Padmanabha et al. (Carnegie Mellon), "VoicePilot: Harnessing LLMs as Speech
  Interfaces for Physically Assistive Robots", arXiv:2404.04066 v2. Venue:
  ACM (likely ASSETS/UIST 2024) — **NOT PINNED from the arXiv page; verify
  venue before citing** (plan §7 "verify all citations" applies).

## 2. What it is — VERIFIED

Iteratively developed (3 versions) framework for LLM speech interfaces on
physically assistive robots, instantiated on the Obi commercial feeding robot
with GPT-3.5 Turbo + Whisper STT. Final evaluation: human study with **11
non-disabled older adults, ages 72–91 (mean 81.1, SD 5.9)** at an independent
living facility (Providence Point, Pittsburgh; CMU IRB). Output: final
framework + 5 design guidelines (Customization; Multi-Step Instruction;
Comparable Time to Caregiver; Consistency; Social Capability).

## 3. Evaluation protocol & measures — VERIFIED

- 5 predefined tasks + practice + open feeding session.
- **Task success = participant self-report** ("Did the robot adequately
  complete the intended task?"), logged per attempt, up to 3 attempts
  (Fig. 5). Most participants succeeded within 3 attempts; Task 1 (speed
  modifier): only 6/11 succeeded on attempt 1.
- Six 7-point Likert items; NASA-TLX (low workload across categories);
  **SUS mean 73.0 (SD 18.6, min 50, max 95)**.
- Thematic analysis of audio recordings → design guidelines.
- Bite-timing metrics appendix (mean time between bites 45 s, SD 10).
- Safety handling: predefined function library, robot speed clamped to
  conservative ranges, validity checks on LLM-emitted code variables, and
  spoken stop/pause/start commands (obi.stop() permanently halts).

## 4. Seed-list claim check

- "study with 11 older adults (72–91) at an independent-living facility" —
  CONFIRMED exactly.
- "What they measure objectively vs subjectively" — ANSWERED: everything
  user-facing is subjective (self-reported success, Likert, TLX, SUS); no
  command-level objective success metric, no latency reporting.
- "their safety handling" — ANSWERED (see above): guardrails on code output,
  NOT a deterministic pre-execution structure; the LLM still emits code.

## 5. Comparability bridge to GrowMate/VoiceBT

- **Architecture contrast**: VoicePilot's LLM writes robot code (sequenced
  primitives); ours is forbidden from emitting structure. Their observed
  failure modes (wrong function combination — skipping the scoop before
  feeding; not pausing between bites; "didn't know what mix meant") are
  exactly the class our compile-from-enum design excludes; their fix was
  prompt engineering — ours is structural. This is the sharpest assistive-
  domain instance of the generate-vs-classify contrast and belongs in §II
  Front A *and* the constraint table.
- **Honesty boundary interaction (CRITICAL)**: VoicePilot HAS a measured
  older-adult study; we DO NOT. Any sentence placing us next to VoicePilot
  must compare *system properties* (on-device vs cloud GPT-3.5; deterministic
  safety vs prompt guardrails; firmware-verified feedback vs none reported)
  and may cite their SUS 73.0 only as the field's reference point — never as
  a bar we beat or match. Their instruments (SUS + Likert + TLX + per-attempt
  self-report + thematic analysis) are the natural template for OUR
  future-work user study; `demo/questionnaire.md` already contains SUS —
  cite VoicePilot as the protocol anchor for that instrument choice.
- Their "Comparable Time to Caregiver" guideline gives our latency numbers a
  user-meaning frame (§VII question 5): 9.3 s mean command latency vs their
  45 s inter-bite time — cite as context for what latency budget assistive
  interactions tolerate (carefully: different task rhythm).

## 6. Deviations ledger entries seeded

- **D10**: their success metric is self-reported per-attempt task adequacy;
  ours is substring-verified command emission. Not the same construct — no
  numeric comparison of "success rates" across the two.
- **D11**: venue unpinned (arXiv comments empty). Resolve before submission.

## 7. Not verified / open

- Venue (above). Author list beyond first author truncated in HTML render —
  take the full list from the arXiv metadata when writing references.
