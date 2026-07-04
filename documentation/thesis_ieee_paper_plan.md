# Combined IEEE Paper = Thesis Final Report — Plan

Per supervisor (Majid): merge the two companion papers into **one extended
IEEE-style paper (~13–14 pages)** that also serves as the MSc thesis final
report. This document is the build plan: narrative, section-by-section outline
with page budget, rubric mapping, experiments, and the honesty adjustments that
follow from what will actually be run.

---

## 1. Decisions locked (2026-07)

- **Experiments that WILL run before submission:**
  - **Misclassification stress test** — force N wrong intents, confirm USC = 0.
    Hardware-free. *This is now the headline empirical result.*
  - **Pin final corpus numbers** — run the 29-utterance corpus, lock
    DBSR/SNSR/USC/latency (replaces the ~97% placeholder).
- **NOT running:** the classify-vs-generate ablation, and an older-adult user
  study. Consequences handled in §4 (framing) below.
- **Retrospective (rubric D, 10%)** goes in **Appendix A** (confirm the
  department accepts rubric-marked content in an appendix; fallback is a titled
  §XI "Reflections" in-body).
- **Framing decision — LOCKED to (a), the constrained-LLM framing.** Verified
  against the code: [ai_core.py](../src/growmate_voice/growmate_voice/ai_core.py)
  runs Gemma 3:4b (Ollama, temp 0.2, grounded in the live plant list) for the
  classification step, so there *is* a real on-device LLM in the loop. The
  central claim is therefore the **division of labour**, not the absence of an
  LLM. Canonical sentence for title/abstract/§IV:
  > *"We use a small on-device LLM for intent classification only, and replace
  > LLM-based plan/structure generation with deterministic, template-driven
  > behaviour-tree synthesis."*
  The "lightweight / template-driven" vocabulary applies to **stage 2**
  (entity grounding + tree synthesis, which is genuinely LLM-free), **not** to
  the whole pipeline. Framing (b) — "a lightweight alternative to LLM-based
  command parsing" — is **rejected**: it reads as "no LLM," which is false (the
  demo shows `gemma3:4b`), creates a viva credibility hole, and severs the paper
  from its own LLM-robotics related work.

---

## 2. The merge narrative (one story, two results)

One system, one corpus, one architecture. The through-line:

> **Constrain the LLM to intent classification; let a deterministic, inspectable
> behaviour tree own all structure and safety.**

From that single principle flow two results:

1. **Safety + accessibility** for vulnerable users — a misclassification becomes
   a visible, safe no-op (USC = 0), directly answering the failure modes that
   empirical older-adult voice-AI studies document.
2. **A portable, on-device framework** — flat-classify → compile-to-tree over a
   reused node library → tick-with-verify; additive to an existing robot stack,
   running on a 2–4B model on a Raspberry Pi.

One thesis, two contributions — not two papers stapled together.

---

## 3. Section outline + page budget (IEEE two-column, ~13.5 pp)

| # | Section | Pages | Rubric | Feeds from |
|---|---|---|---|---|
| — | Abstract + Index Terms | 0.25 | A1 | §1.x abstract drafts |
| I | Introduction — motivation, older-adults problem, abstract problem, contributions list | 1.25 | B (+A2 hook) | §1.1–1.3, §1.6 |
| II | Related Work — the 10-paper table narrativised (LLM-planning, LLM+BT, on-device, older-adults+AI) | 2.0 | B | §3 related-work table |
| III | Background primer — BT node types + SUCCESS/FAILURE/RUNNING semantics; small-LLM structured-generation limits | 1.0 | B | §2 Themes 2, 4 |
| IV | System Design — VoiceBT: flat classify → compile-to-tree → tick-verify; the in-code safety prefix; inspectability | 2.5 | C1 | §1.3, builder.py, schemas.py |
| V | Implementation — GrowMate on the AURA/FarmBot stack: action set, pipeline, additive integration | 1.75 | C2 | README, PLANS, source |
| VI | Evaluation methodology — corpus, metrics, misclassification stress test design, replicability | 1.25 | C3 | §1.4, eval_v2_results.md |
| VII | Results & Analysis — DBSR/SNSR/USC, latency, misclassification result, interpretation vs prior work | 2.0 | C3 | pinned corpus run + stress test |
| VIII | Ethics, Safety & Responsible Engineering — hazards+mitigations, standards/regulatory, stakeholders, SDGs | 1.25 | **A2 (10%)** | §1.5 + new Chapter-4 detail |
| IX | Discussion, Limitations & Future Work | 1.0 | D + C3 | §3.3, scope caveats |
| X | Conclusion | 0.3 | D | — |
| A | **Appendix A — Project Retrospective** (process, plan-vs-actual, lessons, what I'd do differently) | 0.75–1.0 | **D (10%)** | new |
| — | References | ~1.0 | — | §3 tags |

Total ≈ 13.5 pp incl. references + retrospective appendix. If A2 depth needs
more room, take 0.5 pp from Related Work (§II).

---

## 4. Framing adjustments forced by "no ablation, no user study"

These keep the claims honest given what is actually measured:

- **Framework/reliability claim (was Paper 2's headline).** Do **not** claim a
  measured "small models fail at generation" result. Instead: *prior work shows
  free-form structured generation is unreliable — it needs one-shot prompting
  even at 7B [BTGenBot-24] and yields unreliable policy code [RoboInspector-25];
  VoiceBT sidesteps this class of failure by construction by never asking the
  model to emit structure.* Argument from design + cited evidence, not our curve.
- **Framework contribution now rests on:** (i) the architecture itself, (ii)
  **on-device deployment** on a 2–4B model where cloud systems like [InterpBT-25]
  and [SayCan-22] cannot go, (iii) **additive portability** onto an existing
  stack. Portability is currently an *architectural* argument (one publisher +
  enum + builders); only claim a demonstrated second robot if one is actually
  run.
- **Safety/accessibility claim (now the empirical headline).** The
  **misclassification stress test** is what makes USC = 0 a *result* rather than
  an assertion — lead the Results section with it.
- **Older-adults comparison stays a failure-mode / design-principle argument**,
  not a measured outcome. "Safe-by-construction prevents the documented failure
  modes [VA-Errors-24, VA-Health-21]" — never "older adults experienced fewer
  errors" (no user study to support that).
- **Future Work** legitimately absorbs the two dropped experiments: the
  classify-vs-generate ablation across model sizes (on-device via LiteRT-LM) and
  the older-adult user study. These are *genuinely valid* future work, not
  "objectives I didn't finish" — which is what the rubric rewards.

---

## 5. Rubric coverage check (nothing orphaned)

| Rubric item | % | Home in the paper |
|---|---|---|
| A1 Written communication | 10 | whole document quality + abstract |
| A2 Ethics & responsible engineering | 10 | §VIII (dedicated, detailed) |
| B Background | 15 | §I, §II, §III |
| C1 Analysis & design | 15 | §IV |
| C2 Implementation & artifacts | 15 | §V (+ live demo) |
| C3 Testing & evaluation | 15 | §VI, §VII (+ demo) |
| D Project retrospective | 10 | §IX (future work) + Appendix A |

All seven marked components have an unambiguous home. **A2 and D are the two
that a normal IEEE paper would drop — both are explicitly placed.**

---

## 6. Build order (suggested)

1. **Run the two experiments** (misclassification stress test + pinned corpus
   numbers). Everything downstream quotes these — do them first so no section
   holds a placeholder.
2. **§IV System Design + §V Implementation** — the core; most material already
   exists across README/PLANS/source.
3. **§II Related Work** — narrativise the §3 ten-paper table; verify author
   names/years/venues while writing (esp. [InterpBT-25], [BTGenBot-24] — I
   attributed those to the POLIMI group from memory; confirm).
4. **§VI/§VII Evaluation + Results** — plug in the real numbers.
5. **§VIII Ethics** — expand §1.5 to top-band detail (hazards + mitigations +
   standards + regulatory bodies + stakeholders).
6. **§I Intro, §IX Discussion, §X Conclusion, Appendix A Retrospective.**
7. **Reference list** — resolve every `[Tag]` to a full IEEE citation.

---

## 7. Open items (carried forward)

- Confirm department allows rubric-marked content (Retrospective) in an
  **appendix** vs requiring an in-body section.
- Confirm **§VIII at 1.25 pp is enough A2 depth**, or rebalance from §II.
- **Verify all citations** — author/year/venue for every `[Tag]` in §3.
- Confirm the **AURA stack authorship** wording for §V + acknowledgements.
- Decide **which corpus run is the reported one** (V1 interim vs V2 hardware)
  once the pinned run is done.
- Did a **focus group / SUS questionnaire** ever run? If yes, it upgrades the
  older-adults comparison from "prevents by construction" to a measured signal
  and should feed §VII.
