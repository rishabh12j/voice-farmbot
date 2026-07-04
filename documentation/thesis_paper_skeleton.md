# Paper Skeleton — Section-by-Section Question Scaffold

Drafting scaffold for the combined IEEE paper (= thesis final report). We draft
each section by answering its questions below. Governing decisions live in
[thesis_ieee_paper_plan.md](thesis_ieee_paper_plan.md):

- **Framing (a) LOCKED:** small on-device LLM (Gemma 3:4b) does *intent
  classification only*; deterministic template code does grounding + tree
  synthesis + safety. Canonical sentence: *"We use a small on-device LLM for
  intent classification only, and replace LLM-based plan/structure generation
  with deterministic, template-driven behaviour-tree synthesis."*
- **Social spine:** *for a vulnerable user, safety and transparency are the
  social requirement.* The framework is derived FROM that requirement; FarmBot is
  the testbed, VoiceBT is the transferable contribution.
- **Honesty boundary:** claim *safe-by-construction* (prevents documented harms);
  never claim a measured older-adult outcome (no user study).
- **Experiments running:** misclassification stress test + pinned corpus numbers.

Target: ~14 pp IEEE double-column. Page budgets are targets, not limits.

---

## Title · Abstract · Index Terms — 0.4 pp

1. What single line states the **social problem**, and what single line states the
   **framework claim** — both must appear in the title/abstract?
2. Which headline **numbers** go in the abstract (DBSR, USC = 0, latency), and are
   they pinned or still placeholder?
3. What **generalisation** sentence closes the abstract (need is general → portable
   framework → FarmBot is the testbed)?
4. Which **index terms** span both indexings (assistive robotics, HRI, behaviour
   trees, on-device LLM, accessibility, voice interface, safety)?

## I. Introduction — 1.25 pp — [B, +A2 hook]

1. What is the **social thesis** — that the population most in need of assistive
   robots is the least protected by current interfaces?
2. Why do **current interfaces fail on both ends** — manual app/keyboard excludes
   these users; naive voice/LLM introduces new harms?
3. What are the **documented harms** of voice AI for older adults (escalating
   errors, abandonment, transparency gap, hallucination on a population less able
   to detect it)? [VA-Errors-24, VA-Health-21]
4. Why is commanding a **physical robot** higher-stakes than a phone assistant (a
   wrong output causes physical action near a vulnerable person)?
5. How does the **social requirement translate into engineering constraints**
   (safe-when-wrong, inspectable-before-acting, on-device/private, honest
   feedback)?
6. What is the **framework** we contribute, in one sentence (framing (a))?
7. Why does the social need being **general** justify a **portable** framework —
   FarmBot as validation, not scope?
8. What are the **explicit contributions** (bulleted, 4–5)?
9. What is the **honesty boundary** (safe-by-construction, not a measured social
   outcome)?

## II. Related Work — 2.5 pp — [B] (priority: point-by-point grounding)

**Front A — accessibility / assistive & older-adult AI**
1. What has assistive / socially-assistive robotics done for older and disabled
   users, and what does it not address (physical-action safety)?
2. What do **empirical older-adult voice-AI studies** find about errors, trust,
   and abandonment? [VA-Errors-24, VA-Health-21]
3. What do **hybrid / LLM assistants for elders** do — and crucially not do (they
   converse, they don't safely command a physical robot)? [SAR-Older-25]
4. Point-by-point: **how do we differ** (physical-action-safe, transparent,
   on-device)?

**Front B — LLM / voice robot control**
5. What do **end-to-end LLM planners** do and where do they fail? [SayCan-22,
   CodePolicies-22]
6. What do **LLM + behaviour-tree** systems do — generate vs select the tree — and
   at what compute scale? [BTGenBot-24, BETR-XP-24, InterpBT-25]
7. What is the **evidence that small-model structured generation is unreliable**?
   [RoboInspector-25, BTGenBot-24 one-shot@7B]
8. What about **on-device / lightweight** robot LLMs? [InteLiPlan-24]
9. Point-by-point: **how do we differ** (classify-not-generate, on-device,
   deterministic safety, additive)?

**Synthesis**
10. What two **comparison tables** anchor the section — Table I (technical axes),
    Table II (accessibility axes)?
11. What is the **one-sentence gap** no prior work fills (the union cell:
    voice + physical-action-safe + transparent + on-device + assistive)?

## III. Background & Preliminaries — 0.75 pp — [B]

1. What is a **behaviour tree** (node types: sequence/fallback/parallel,
   condition, action; ticking; SUCCESS/FAILURE/RUNNING)?
2. Why do BTs give **fail-safe abort** — the exact property the safety claim
   depends on (a failed condition halts the sequence before any action)?
3. What is the distinction between **intent classification and plan generation**
   (the line the whole paper rests on)?
4. What are the **resource limits of small on-device LLMs** — why 2–4B, why not
   cloud, what fails as they shrink?
5. What **minimal FarmBot / ROS2** background does a reader need (gantry,
   tool-head, command vocabulary, keyboard_topic)?

## IV. System Design & Architecture (VoiceBT) — 2.5 pp — [C1] (technical core)

1. What is the **end-to-end pipeline** (speech → STT → classify → ground →
   compile-to-tree → tick-verify → TTS)? *(Figure 1: architecture.)*
2. Exactly what does the **LLM do and not do** (flat intent over a fixed enum;
   canonical sentence)?
3. What is the **intent schema / wire contract** (`{action, target, params,
   response}`)?
4. How does **entity grounding** work (species/alias → live map coordinates)?
5. How does **deterministic tree synthesis** work (template `_tree_*` builders
   over a reusable node library)? *(Figure 2: an example compiled tree.)*
6. What is the **safety prefix**, precisely (`CheckAvailable → [CheckToolMounted]
   → [CheckBounds] → [CheckPlantFound] → action`)?
7. Why is **emergency stop outside the LLM** (string-matched before any model
   call)?
8. What is **tick-and-verify** (busy_state / pin reads; honest-or-blank; nothing
   logged/spoken as done pre-confirmation)?
9. Why is the tree **inspectable**, and what does that buy **socially**
   (transparency the user/carer can check before it runs)?
10. What makes the framework **portable** (publisher + action enum + tree builders
    are the only robot-specific parts)?
11. Which **alternatives were considered and rejected**, and why (LLM-generates-
    tree, RL policy, fixed keyword grammar)?

## V. Implementation (GrowMate on FarmBot) — 1.25 pp — [C2] (also live demo)

1. What is the **concrete action set** actually built and demonstrated?
2. What **components/models** (Gemma 3:4b via Ollama; STT: faster-whisper / Vosk /
   Moonshine; TTS: Piper / Kokoro)?
3. How is it **additive to AURA** (publishes only to keyboard_topic; no fork)?
4. What **runs where** (browser/phone client vs Pi) — the on-device claim,
   specifically?
5. What is **mine vs upstream vs third-party** (unambiguous attribution)?
6. What **hardware** (two FarmBot installations, Pi, tool bays)?
7. What is the **honest maturity line** (which capabilities are detection/query
   only, not full physical action)?

## VI. Evaluation Methodology — 1.0 pp — [C3]

1. What **question does the evaluation answer** (safety + feasibility under
   controlled conditions — not a large-scale accuracy benchmark)?
2. What is the **corpus** (29 utterances; categories: single / multi-intent /
   indirect / query / safety-trigger) and why those categories?
3. What **metrics**, and why (DBSR, SNSR, USC, latency — established from the BT
   literature for comparability)?
4. How is the **misclassification stress test** designed (force N wrong intents →
   measure USC)? *(The safety headline.)*
5. What is **controlled**, and what are the **threats to validity** (STT/TTS
   folded in, LLM nondeterminism, corpus size, no user study, hardware variation)?
6. How is it **replicable** (scripted one-command run, JSON per-case dumps)?
7. **Sim vs hardware** — what does each validate?

## VII. Results & Analysis — 1.5 pp — [C3]

1. What are the **pinned headline numbers** (DBSR / SNSR / USC / latency)?
2. What is the **stress-test result** — USC = 0 under forced misclassification —
   and why it is the empirical core of the safety claim?
3. How do the results **interpret against prior work** (regime comparison, not a
   head-to-head benchmark — e.g. SayCan 74% execution is a different regime)?
4. What **failed or fell short**, and why (the DBSR miss; genuinely ambiguous
   indirect/query utterances)?
5. What does **latency** mean for a real user (usability, not just a number)?
6. What can and **cannot** be concluded from these numbers (honesty)?

## VIII. Social Impact, Accessibility & Ethics — 2.0 pp — [A2, 10%] (the dual-focus payoff)

1. How does **each documented harm map to a structural response** (the
   loop-closing table: intent-error / escalation / abandonment / transparency-gap
   / hallucination → our answer)?
2. **Accessibility & independence** — what does the design restore, and for whom?
3. **Autonomy, consent, dignity** — how preserved (explicit command, always-on
   stop, user stays in control)?
4. **Human-robot trust** — how does inspectability build *justified* trust rather
   than blind trust?
5. **Health & safety hazards** (mechanical, electrical, behavioural) and their
   **mitigations**?
6. **Applicable standards and regulatory bodies** — which, and how they apply?
7. **Privacy** — what does on-device speech/inference buy the user?
8. **Stakeholder analysis** — who is affected and how (primary users, carers,
   family, clinicians, manufacturers)?
9. **Deployment risks** — over-reliance, de-skilling, over-promising to a
   vulnerable group — and mitigations?
10. **SDG mapping** (3 health, 10 inequalities, 2 hunger, 12 responsible
    consumption)?
11. **Environmental impact** — targeted-action intent, on-device energy vs cloud,
    with the honest embodied-cost counterweight?
12. What is the **honesty boundary restated** (safe-by-construction; measured
    social outcomes are future work)?

## IX. Discussion, Limitations & Future Work — 0.75 pp — [D + C3]

1. What are the **real limitations** (corpus size, no user study, one robot
   family, LLM nondeterminism, detection-only capabilities)?
2. **How far does it generalise** (assistive-robot classes; any fixed-command
   robot)?
3. What is **genuinely valid future work** (classify-vs-generate ablation across
   model sizes on-device; older-adult user study; new verbs; a second robot) — not
   just "objectives I didn't finish"?
4. What would you **do differently** (bridge to the retrospective)?

## X. Conclusion — 0.3 pp — [D]

1. What is the **single takeaway** (a social requirement, turned into an
   architectural invariant, delivered as a validated portable framework)?
2. What is the **contribution to the state of the art**, in one sentence?

## References — ~1.0 pp

1. Are all `[Tag]`s resolved to full IEEE citations, with **verified**
   author/year/venue?
2. Is the ~30–40 reference count balanced across the two fronts (accessibility +
   LLM-robotics)?

## Appendix A — Project Retrospective — 0.75 pp — [D, 10%] (thesis version only)

1. **Plan vs actual** — what changed from the original project plan, and why?
2. What **went well**, and what **challenges** arose (hardware bring-up, on-device
   model limits, the honest-or-blank discipline)?
3. **Lessons** — technical, research, and project-management?
4. How did your **approach/understanding evolve** over the project?
5. What are the **limitations of your methods/decisions**, and what would you do
   differently if repeating it?

---

## Cross-cutting checklist (applies to every section)

- Does it serve the **social spine** (requirement → architecture → validated
  framework)?
- Is every **major claim grounded** against a cited prior work?
- Does it respect the **honesty boundary** (no unmeasured social outcomes; no "no
  LLM" implication)?
- Are **placeholder numbers** flagged until the corpus run is pinned?
- **Figures/tables budget:** Fig 1 architecture, Fig 2 example tree, Fig 3
  stress-test/results; Table I technical comparison, Table II accessibility
  comparison, Table III results.
