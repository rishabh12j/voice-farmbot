# Dossier #6 — Situated Understanding of Errors in Older Adults' VA Use (Front 3: documented-harms anchor)

**Dossier status: VERIFIED against full text (arXiv HTML v3). ACCEPT as the
primary [VA-Errors-24] empirical anchor for the social spine.**

Read 2026-07-04 from arXiv 2403.02421 (v3, 2024-09-23).

## 1. Identity — VERIFIED

- Mahmood, Wang, [Tran?], Huang (Johns Hopkins), "Situated Understanding of
  Errors in Older Adults' Interactions with Voice Assistants: A Month-Long,
  In-Home Study", arXiv:2403.02421 v3. Journal: **ACM TACCESS** (seed claim;
  independently consistent with a TACCESS ToC index listing (Vol. 19(1),
  2026) — verify volume/pages at citation time).

## 2. What it is — VERIFIED

Four-week in-home deployment of Amazon Echo Dot + custom full-audio recorder
in **15 older adults' homes**; ChatGPT-powered VA deployed mid-study (week 3).
Data: 2,552 user-query/VA-response pairs, 20 h 40 min audio. Focus: error
taxonomy, resolution, reactions, recovery, compounding.

## 3. Findings that anchor our constraint table — VERIFIED

- **True error rate 24.76%** of one-turn queries ("almost one in every four
  queries failing"); **98.10% of errors manifested as conversational
  breakdowns**. Explicitly higher than log-based prior work because
  activation errors are usually invisible in logs.
- **Error taxonomy (Table 2)**, counts + resolution-on-next-retry:
  - Human errors 72 (38.9% resolved): wrong wake word 49, partial query 23.
  - Speech errors: mis-trigger 15 (6.7% resolved), partially-listened 29
    (27.6%), interruption 1, transcription 40 (35%).
  - **VA errors 406 (20.44% resolved)** — failures processing accurately
    captured speech (system failures, intent errors, …).
- **Error escalation ("snowball effect")**: correction attempts "typically
  failed to address the initial misunderstanding and resulted in numerous
  additional unresolved errors".
- Intent-recognition errors driven by VA not accommodating older adults'
  needs (forgetfulness, natural speech style).
- LLM-powered VA section: barriers to adoption, breakdowns, learning curve —
  LLMs absorb some speech-recognition noise but introduce their own issues.

## 4. Seed-list claim check

- "month-long in-home study" — CONFIRMED (4 weeks, 15 homes).
- "error taxonomy … empirical basis of the social thesis" — CONFIRMED; the
  taxonomy is concrete and citable per row.

## 5. Mapping to our by-construction preventions (the §VIII loop-closing table)

| Their documented harm | Our structural response (code-verified) |
|---|---|
| Intent errors on accurately heard speech (406 events) | flat classification over fixed enum, grounded in live map; unknown target ⇒ clean refusal, zero commands (Run 1 refusal 4/4) |
| Escalation when correcting | no dialogue-state to corrupt: each utterance re-classified stateless(+session pronouns); failed intent = safe no-op, retry costs nothing physical |
| Wrong wake word / activation (49+15+29) | e-stop words string-matched pre-LLM; command channel is push-to-talk app (activation-error class largely designed out — SAY THIS CAREFULLY: we shift, not solve, activation) |
| Errors invisible to users | inspectable tree + spoken honest-or-blank confirmation only after firmware verify |
| Hard: STT noise on older-adult speech | corpus "hard" category (fillers, politeness, STT noise, hedges) — 10/10 in Run 1 |

Boundary discipline: these rows argue *prevention of documented failure
modes by design*. The paper measures VA errors, not our users. No sentence may
morph this into "older adults experience fewer errors with GrowMate".

## 6. Deviations ledger entries seeded

- **D12**: their denominators are in-the-wild conversational queries; ours is
  a 42-case authored corpus. Their 24.76% error rate and our DBSR are not on
  the same axis; never tabulate them together.

## 7. Not verified / open

- Full author list + TACCESS volume/issue/pages for the reference entry.
- The mid-study ChatGPT-VA findings are qualitative; do not cite numbers from
  that section without re-reading (HTML extraction focused on the error
  analysis).
