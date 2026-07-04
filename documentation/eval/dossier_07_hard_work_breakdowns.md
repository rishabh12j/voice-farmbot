# Dossier #7 — "It feels like hard work trying to talk to it" (Front 3: breakdown/repair anchor)

**Dossier status: VERIFIED against full text (arXiv HTML v1). ACCEPT as second
harms anchor (breakdown taxonomy + repair burden + trust/abandonment link).**

Read 2026-07-04 from arXiv 2510.06690 (v1, 2025-10-08).

## 1. Identity — VERIFIED

- Mathur (Georgia Tech), Zubatiy (Northeastern), Rozga (Georgia Tech), Mynatt
  (Northeastern), "'It feels like hard work trying to talk to it':
  Understanding Older Adults' Experiences of Encountering and Repairing
  Conversational Breakdowns with AI Systems", arXiv:2510.06690. Venue: ACM
  format, copyright block still template ("Woodstock" placeholder) — likely
  CHI 2026 submission/camera-ready; **venue UNPINNED, verify before citing.**

## 2. What it is — VERIFIED

Secondary analysis of a **20-week in-home deployment** of a Google-Home-based
voice agent ("MATCHA") for **medication management**, with **7 older-adult
dyads (14 participants)**; **844 recorded interactions** analyzed for
breakdowns and user-initiated repair; post-deployment interviews. Framed via
Vertesi's "seams" concept.

Correction to the seed list: seed said "14 older adults" — precisely 7 dyads
= 14 participants. Substantively equivalent; log the dyad structure (spouse/
caregiver collaboration is part of their story).

## 3. Findings that anchor our constraint table — VERIFIED

- **Four breakdown types** (§7.1): (1) Language & Semantics, (2) Flow,
  (3) Historical Understanding, (4) Explanations.
- Repair strategies are user-driven, ad hoc, and burdensome — "invisible
  labor in mitigating conversational breakdowns" (§7.3.3).
- Trust mechanism: "recurrence of breakdowns such as misinterpretation of
  user intent, failure to recover errors, lack of explanations … can erode
  trust and lead to system abandonment by older adults" (§2.3.1, citing
  Orlofsky & Wozniak et al.).
- Age-specific attribution: older adults attribute breakdowns to the system
  failing to understand *them*, younger users to functional limits (§2.3.1).
- Context: medication management = safety-adjacent task where "reliability,
  trust, and communication are paramount".

## 4. Seed-list claim check

- "20-week deployment, breakdown/repair" — CONFIRMED.
- "trust erosion → abandonment" — CONFIRMED (with the nuance that the causal
  claim is cited prior work synthesized by them; attribute accordingly).
- "medication-management context transferability" — their domain is
  reminder/check-in dialogue, not physical actuation; transfer to us is the
  *interaction-failure* layer only, and it transfers well: garden care is
  likewise a routine, safety-adjacent, delegated task.

## 5. Mapping to our design (for §VIII, complementing Dossier #6)

| Their breakdown type | Our structural response |
|---|---|
| Language & semantics | fixed verb enum + live-map grounding; hard-category corpus evidence (STT noise, hedges) |
| Flow | single-utterance → single tree; no multi-turn dialogue state to derail; confirm gate is one fixed exchange |
| Historical understanding | session memory is explicit and narrow (pronoun resolution, "water them again" — app-flow suite NLP 6/6); we do not claim personalization |
| Explanations | the tree IS the explanation (inspectable before run); spoken confirmation only after firmware verify (honest-or-blank) |

Repair-burden framing is the strongest social justification for
refuse-cleanly-rather-than-guess: every wrong guess creates repair work that
this population experiences as "hard work"; a clean refusal costs one retry.

Honesty boundary: their evidence is about *conversational agents without
actuation*. Our claim: the same failure classes get *higher stakes* with a
physical robot; prevention is by construction. No measured-outcome language.

## 6. Deviations ledger entries seeded

- **D13**: breakdown taxonomy is qualitative (no per-type rates comparable to
  our metrics); use as design-requirement evidence, not benchmark.

## 7. Not verified / open

- Venue + camera-ready status (template artifacts in the arXiv HTML).
- Their prior ASSETS paper (Mathur et al. 2022, MATCHA usability) is the
  primary source for deployment logistics — cite the pair correctly.
