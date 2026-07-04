# GrowMate / VoiceBT — Thesis Report: Working Answers

> Working draft. Each section gives the rubric brief (in quotes) followed by
> answers organised as raw material to write up. Reference citations are
> *leads to verify*, not final citations — confirm every author/year/venue
> before pasting into the report.

---

# 1. Introduction

*Marked under B Background (15%, report + interview). Broader-impact content also feeds A2 Ethics (10%).*

## 1.1 Motivation and problem domain

> Introduce the topic and the broader problem area. Why should anyone care about this problem and these results? Cite appropriate references. State the high-level, abstract problem the project addresses, leading naturally into the next subsection.

**Topic / broader problem area**

- Ageing and disabled populations are growing, and lose the physical ability (bending, lifting, reaching, fine motor control) to do everyday physical tasks — including gardening.
- Gardening matters: it's therapeutic (physical, mental, social health benefits) and ties to food self-sufficiency — not just a hobby.
- Agricultural/horticultural robots (precision ag, and small-scale platforms like FarmBot) can do that physical labour autonomously: water, seed, weed, image.
- **The mismatch:** the people who'd benefit most from these robots are locked out of them, because the robots are operated through desktop apps, coordinate maps, and command sequences that assume a tech-literate user at a screen.

**Why anyone should care**

- It's an accessibility + independence issue: the assistive tech exists, but its interface defeats its purpose for the intended user.
- Voice is the natural fix — but a voice interface to a physical robot is harder than to a phone: a misunderstood command can physically crash the gantry, run the pump on the wrong plant, move a tool wrongly.
- Current voice assistants / end-to-end LLM agents are (a) opaque — the user can't see what was understood or what's about to happen — and (b) unreliable — LLMs hallucinate, and a confidently-wrong output drives real hardware.
- The result matters because it shows you *can* have natural-language robot control that's safe and transparent for non-expert, vulnerable users — not just plausible-sounding.

**References to cite (real — verify before using)**

- Ageing population stat → WHO *World report on ageing and health* (2015) or UN *World Population Prospects*.
- Gardening & health → Soga, Gaston & Yamaura (2017), *Preventive Medicine Reports*.
- Agricultural robotics / precision ag → a survey (e.g. Duckett et al. 2018 agri-robotics white paper).
- FarmBot platform → FarmBot official documentation / Genesis specs.
- Older adults & technology barrier → an HCI accessibility / digital-divide source.
- LLM hallucination / unreliability → Ji et al. (2023), *ACM Computing Surveys*.

**High-level, abstract problem the project addresses**

- How to build a natural-language (voice) interface to a physical robot that is: (1) accessible to non-expert users, (2) safe to run unsupervised, and (3) transparent — the user can verify what the system will do before it does it.

**Lead into next subsection**

- The general problem (safe + transparent NL control of a physical robot) is stated; §1.2 narrows it to the specific setting — voice-controlled agricultural robotics on FarmBot — and states the concrete project aims.

## 1.2 Scope and technical problem

> State the specific TECHNICAL problem you set out to solve (the motivation above covers the high-level problem). Define the scope explicitly — what you chose to include and what you deliberately excluded.

**The specific technical problem**

- Map free-form spoken commands onto a **safe, verified sequence of physical robot actions**, using a language model small enough to run **on-device** (Raspberry Pi, no cloud), while guaranteeing the model can never directly cause an unsafe action.
- The crux: the obvious approach — let an LLM generate the action plan / structured tree end-to-end — **fails on small on-device (2–4B) models**, which produce valid structured output ~0% of the time and hallucinate. Big cloud models would help but break the on-device, private, low-cost constraint.
- So the real technical problem is an **architecture/division-of-labour problem**: use an unreliable small model for the one thing it *is* good at (understanding intent), while making *structure and safety* the responsibility of deterministic code — and prove the result is transparent (inspectable before execution) and verified (a "done" reflects real firmware completion, not a guess).
- Sub-problems that fall out of this:
  - Constraining the LLM output so it can't emit unsafe/structurally-invalid plans (flat intent classification, fixed enum).
  - Guaranteeing every robot action is gated by safety checks (availability, bounds, target-found) in code, not by the model.
  - Keeping emergency-stop outside the model entirely (no LLM in the safety-critical path).
  - Closing the loop on real hardware: a success only counts when the firmware confirms it (tick-and-verify).
  - Running the whole speech→intent→tree→robot→speech pipeline locally, fast enough to be usable.

**In scope (deliberately included)**

- The framework (VoiceBT) + its instantiation on FarmBot (GrowMate).
- On-device pipeline end-to-end: speech-to-text, flat intent classification via a small local LLM, deterministic behaviour-tree construction, execution, and spoken confirmation.
- Safety architecture: in-code safety prefix on every action, LLM-free emergency stop, bounds/availability/target checks.
- Tick-and-verify against real FarmBot firmware; inspectable trees.
- A defined action set actually wired and demonstrated (move/jog/home, water single + all, lights, photo, panorama, weed detection, soil/moisture queries, emergency stop).
- Deployment on real hardware (two FarmBot installations) and a quantitative evaluation (29-utterance corpus; desired-behaviour success, single-node success, unsafe-state count, latency).

**Out of scope (deliberately excluded)**

- **Not** general open-ended conversation or a general-purpose assistant — only the gardening/robot command domain.
- **Not** letting the LLM generate trees/plans — explicitly rejected as the thing that doesn't work.
- **Not** cloud LLMs / large models — on-device constraint kept on purpose.
- **Not** novel STT/TTS or perception models — those are off-the-shelf components; the contribution is the architecture, not the recognisers or the vision.
- **Capabilities demonstrated only to detection/query level, not full physical action** — moisture-aware "smart" watering, acting on detected plant/weed coordinates, physical weed removal, and seeding are **roadmap, not claimed results** (be explicit so the abstract/claims stay honest).
- **Not** a formal clinical/user study with the target elderly/disabled population — usability framing and design-for-accessibility are in scope; a controlled human-subjects trial is not. *(If a focus group / SUS questionnaire actually ran, move it into scope here.)*

## 1.3 Solution approach (overview)

> Overview of the analysis techniques and the general design/solution approach (e.g. control-theoretic vs data-driven/RL; classical CV vs ML; model-based engineering). Is your solution an extension of prior work or (unusually) new? Keep it high-level here — detail goes in the main body.

**General design/solution approach — the paradigm**

- **Hybrid neuro-symbolic / model-based**, deliberately *not* end-to-end data-driven. A learned component (the LLM) is used only for perception of intent; all decision structure and control is **deterministic, symbolic engineering** (behaviour trees + coded safety logic).
- The division:
  - **Data-driven, only at the edges:** off-the-shelf neural speech-to-text, a small pretrained LLM for *classification only*, neural text-to-speech. None trained or fine-tuned — used as black boxes.
  - **Classical / model-based engineering at the core:** behaviour trees generated by deterministic Python from a typed action library; a hand-specified safety prefix; a fixed intent enum; a tick-and-verify execution gate tied to firmware state.
- Controlling philosophy: **constraint, not capability** — restrict the model to the narrow task it does reliably (flat intent classification) and push everything safety-relevant into inspectable code.

**Alternative paradigms considered / positioned against**

- **End-to-end LLM agent (LLM generates the plan/tree directly)** — rejected: small on-device models can't reliably produce structured output and can hallucinate unsafe actions.
- **End-to-end / RL policy mapping speech to action** — not appropriate: no reward/data regime for a safety-critical low-volume domain, and it would be opaque (fails transparency).
- **Pure classical command-grammar / fixed keyword parser** — too brittle for natural, indirect phrasing ("the carrots look thirsty"); the LLM buys the natural-language flexibility a rigid grammar can't.
- This approach sits **between** these: LLM flexibility for understanding, classical determinism for safety.

**Analysis / evaluation techniques**

- Quantitative evaluation on a fixed utterance corpus using an **established metric set from the behaviour-tree literature** (desired-behaviour success rate, single-node success rate, unsafe-state count), plus latency — measured against published metrics, not ad-hoc ones.
- Deployment-based validation on real hardware (two FarmBot installations), not simulation alone.

**Extension of prior work, or new?**

- **Both, framed honestly:** the building blocks are prior work — behaviour trees in robotics (Colledanchise & Ögren), LLMs for intent classification, the Gugliermo et al. evaluation metrics.
- The **contribution is the architectural combination and the constraint principle**: a small *on-device* LLM strictly as a flat intent classifier feeding a deterministic, safety-prefixed, inspectable behaviour tree for a *physical* robot — demonstrated safe and transparent on real hardware. The framing (transparency + provable in-code safety + on-device, for accessibility) is the novel synthesis, not a brand-new algorithm.
- State it as **synthesis, not invention** — a novel integration/framework, not a new learning method or control theorem. (The interview will probe this.)

**Reference anchors (verify)** — Colledanchise & Ögren (2018); Gugliermo et al. (2024); an LLM-task-planning source (e.g. SayCan / Ahn et al. 2022) to position what you *didn't* do.

## 1.4 Evaluation approach (overview)

> Briefly, how did you evaluate the solution — real-world or simulation? How did you make the evaluation trustworthy and replicable? What did you control, ignore, or measure?

**Real-world or simulation?**

- **Both, layered:** a deterministic simulation harness runs the *entire* pipeline (intent → tree build → tick-and-verify → command emission) with no robot or ROS, so behaviour and safety can be checked repeatably; then the same stack is validated on **real FarmBot hardware** (two installations).
- The split is deliberate: sim gives **repeatable, robot-free regression testing**; hardware gives **external validity** (real motors, pump, firmware, UART, timing).
- Be precise about which numbers are which: headline corpus metrics come from the intent-server evaluation; the hardware run validates that the gate/firmware loop behaves identically. *(Only claim numbers you've actually run — the V2 hardware results table is still being filled in.)*

**Trustworthy and replicable**

- **Fixed, published corpus:** a frozen 29-utterance test set covering single actions, multi-intent utterances, indirect/natural phrasing, queries, and safety triggers — same inputs every run.
- **Established metrics, not ad-hoc:** desired-behaviour success rate, single-node success rate, unsafe-state count, plus end-to-end latency — comparable to other work.
- **Scripted, one-command evaluation:** `tools/evaluate_v2.py` drives the corpus against the real intent server over HTTP and emits per-case results; `--json` dumps per-case detail for the appendix.
- **Success criteria defined up front** (expected command substring must appear), removing subjective judgement.
- **Inspectable trees + event log:** every decision is pure data viewable before execution; care events log only after firmware confirmation — a pass is auditable, not asserted.

**Controlled** — the corpus, the action enum, the safety prefix, the garden map, the workspace bounds, the STT model, the LLM model (gemma3:4b via Ollama), the prompt, and the success criteria — all pinned.

**Measured** — desired-behaviour success, single-node success, **unsafe-state count (key safety metric, target = 0)**, end-to-end latency, and (V2 only) event-log coverage.

**Ignored / not controlled / threats to validity**

- **STT/TTS quality** — off-the-shelf, not isolated or benchmarked; transcription error folded into end-to-end behaviour.
- **LLM nondeterminism** — same utterance can classify differently; for indirect/query utterances "correct" is genuinely ambiguous (e.g. `general_question` vs `check_moisture`), a known source of variance.
- **No human-subjects measurement** of accessibility/usability in the core eval — corpus is researcher-authored.
- **Corpus size** — 29 utterances is small; this measures feasibility and safety, not population-level accuracy.
- **Hardware variation** — timing/firmware idiosyncrasies between the two FarmBots not formally factored out.
- One-line framing: *the evaluation tests safety and feasibility under controlled, replicable conditions — it does not claim a large-scale accuracy benchmark or a clinical usability study.*

## 1.5 Ethical, societal and environmental impact (framing)

> Discuss ethical considerations and the project's relationship to society, the environment, and the relevant UN Sustainable Development Goals. Frame the impact here; the detailed responsible-engineering analysis goes in Section 4.

**Relationship to society**

- **Positive:** restores independence and access to gardening (and its physical/mental/social health benefits) for elderly and disabled people excluded from assistive farming robots by their interfaces. Supports ageing-in-place and dignity/autonomy.
- **Inclusion/digital-divide:** voice + on-device design lowers the tech-literacy barrier and needs no always-on connectivity, reaching users and locations a cloud/app system can't.
- **Risk side (acknowledge):** automating a vulnerable user's robot carries safety risk if it misacts; risk of over-reliance/de-skilling; a duty not to over-promise to a vulnerable group — which is why the safety/transparency architecture is the ethical core, not just a feature.

**Relationship to the environment**

- **Resource efficiency:** targeted, sensor-aware watering and precise per-plant action reduce water/input waste vs broadcast/manual gardening. *(State as design intent / partially-built capability, not a measured saving.)*
- **Local food production:** supports small-scale, low-input home/community growing.
- **On-device compute:** small local model avoids the energy/carbon cost of repeated cloud LLM inference — modest but real.
- **Honest counterweight:** the robot has manufacturing/embodied-energy and electricity costs; net environmental benefit is plausible but not measured here.

**UN Sustainable Development Goals**

- **SDG 3 (Good Health & Well-being)** — therapeutic gardening, healthy ageing.
- **SDG 10 (Reduced Inequalities)** — accessibility for disabled and older users; closing the digital divide.
- **SDG 2 (Zero Hunger)** — local/home food production and precision growing.
- **SDG 12 (Responsible Consumption & Production)** — efficient use of water/inputs.
- *(Weaker, optional: SDG 11 communities via community gardens; SDG 9 innovation/accessible infrastructure.)*

**Core ethical considerations (high-level here)**

- **Safety of a vulnerable user** — dominant concern; an unsupervised machine acting physically for someone who may not be able to intervene. Direct response: in-code guards, LLM-free emergency stop, firmware-verified "done".
- **Transparency / honesty** — never claim it did something it didn't ("honest-or-blank"); confidently-wrong feedback is itself a harm.
- **Autonomy & consent** — user stays in control; robot acts only on explicit command, with an always-available stop.
- **Privacy** — on-device speech and inference keep voice data local.
- **Non-maleficence vs over-promising** — building for a vulnerable population obliges conservative claims and fail-safe defaults.

**Here vs Section 4** — Here: frame the impact (society/environment/SDG + high-level ethical stakes). Section 4: detailed responsible-engineering analysis (specific hazards + mitigations, standards, regulatory bodies, stakeholder breakdown). Signpost forward in one sentence; don't do that analysis here.

## 1.6 Contributions and attribution

> List your main achievements/contributions (normally 3–5). Clearly flag any that are contributions to the state of the art. Then clearly identify every resource that is NOT your own work. There must be no ambiguity about which work is yours.

**Main contributions**

- **The VoiceBT framework — a constraint-based architecture for safe natural-language robot control.** Restricting a small on-device language model to flat intent classification and delegating all structure, sequencing and safety to a deterministic, inspectable behaviour tree. **[State-of-the-art contribution — headline of the framework paper in preparation; "the LLM proposes, the behaviour tree disposes".]**
- **An in-code safety architecture with a provable safety property.** Every robot action gated by a coded safety prefix (availability → bounds → target-found); emergency stop matched before the model is called; "done" is firmware-verified (tick-and-verify). Holds **even under language-model misclassification** — unsafe-state count of zero across the evaluation. **[State-of-the-art contribution — core of the accessibility/safety paper.]**
- **GrowMate — a working on-device voice-control system for the FarmBot, deployed on real hardware.** Full local pipeline (speech-to-text → intent → behaviour tree → robot → spoken confirmation) on a Raspberry Pi, with a demonstrated action set (move/jog/home, watering single + all, lights, photo, panorama, weed detection, soil/moisture queries, emergency stop), run on two physical FarmBot installations.
- **A reproducible safety-focused evaluation.** Fixed 29-utterance corpus, scripted one-command evaluation, reporting against established behaviour-tree metrics — ~97% desired-behaviour success with zero unsafe states.
- *(Optional 5th)* **Portability/generality of the framework** — robot-agnostic (publisher + action enum + tree builders are the only robot-specific parts); additive integration onto the existing FarmBot stack without forking it. *(Keep as a sub-claim of contribution 1 unless a second robot is demonstrated.)*

**Not my own work (unambiguous)**

- **AURA FarmBot ROS2 stack** — the entire upstream robot control stack (`farmbot_bringup`, `farmbot_controllers`, `farmbot_command_handler`, `farmbot_interfaces`, `map_handler`, `camera_handler`, etc.), developed at Maynooth. Used as-is; my work is additive and publishes only through the existing `keyboard_topic`. **One exception I authored:** a single bug-fix in `map_handler` tool-sequencer release-direction logic — a minor fix to others' code, not a contribution.
- **FarmBot Genesis XL hardware + firmware (Farmduino)** — commercial product; not my design.
- **Speech-to-text models/engines** — faster-whisper / Whisper, Vosk, Moonshine: off-the-shelf, pretrained, unmodified.
- **Text-to-speech engines** — Piper, Kokoro: off-the-shelf, unmodified.
- **Language model + runtime** — Gemma 3 (4B) via Ollama: pretrained third-party model; I wrote the prompt/classifier wrapper but did not train or fine-tune it.
- **Core libraries** — `py_trees`, ROS2, FastAPI/Starlette/uvicorn, pydantic, etc.: standard third-party frameworks.
- **Evaluation metrics** — DBSR/SNSR/USC definitions from Gugliermo et al. (2024); the corpus, harness, and application are mine, the definitions are not.
- **Theoretical foundations** — behaviour-tree formalism (Colledanchise & Ögren) is prior art.
- **Supervisor/others** — note anything Dr Sorouri or technicians provided (greenhouse access, hardware setup, AURA stack origin).

**Unambiguously mine** — the VoiceBT architecture and its principle; the intent schema and fixed action enum; the behaviour-tree builders and safety prefix; the tick-and-verify gate integration; the intent server, ROS2 bridge, event log, scheduler; the on-device pipeline wiring; the map/calibration tools; the evaluation corpus + harness; and the GrowMate application as a whole.

## 1.7 Structure of this report

> One or two lines per remaining chapter outlining its content.

- **Chapter 2 — Technical and Related Background.** Reviews the work this project builds on: behaviour trees as a robotics control architecture, language models for intent classification and the limits of end-to-end LLM planning on small on-device models, voice-interface and accessibility research, the FarmBot platform and the AURA control stack, and the evaluation metrics adopted from the behaviour-tree literature.
- **Chapter 3 — Design and Implementation.** The core of the work: requirements and problem analysis, the VoiceBT architecture and key design decisions (flat intent classification, the in-code safety prefix, inspectable trees, tick-and-verify), the implemented GrowMate system and pipeline, and the testing, evaluation and results — including interpretation of the desired-behaviour-success and unsafe-state findings against prior approaches.
- **Chapter 4 — Ethics, Societal and Environmental Impact, and Responsible Engineering.** The detailed responsible-engineering analysis: health-and-safety hazards and mitigations, applicable standards and regulatory bodies, and stakeholder, societal and environmental impacts.
- **Chapter 5 — Conclusions and Future Work.** What the results imply for the central claim that constraining a language model to classification yields safe, transparent robot control; the contribution to the state of the art; how far it generalises; and genuinely-motivated future work (smart watering, perception-driven action, weeding and seeding, and the on-device classification benchmark).
- **Chapter 6 — Project Retrospective.** A reflective account of the process against the original plan: what went well, the challenges (hardware bring-up, on-device model limits, honest feedback), lessons in research and project management, and what would be done differently.

---

# 2. Technical and Related Background

*Marked under B Background (15%, report + interview).*

> Review the important background. Show broad and deep understanding of the problem domain. Include related academic material from reputable venues; cover the techniques/algorithms essential to your work; keep motivation/non-technical background in the Introduction.

**Theme 1 — LLMs for robot task planning and instruction following (the work you position against)**

- *What others did:* "LLM-as-planner" systems map natural language to robot action — SayCan grounds LLM suggestions in learned affordances; Code-as-Policies / ProgPrompt have the LLM emit executable code/programs; Inner Monologue adds feedback loops; LLM+P offloads to a classical planner (PDDL).
- *What worked / didn't:* large cloud models produce impressive plans, but (a) they hallucinate and can emit unsafe/ungrounded actions, (b) approaches that make the LLM *generate structure* degrade sharply on small models, (c) most assume cloud-scale compute. This is the gap exploited: keep LLM flexibility, remove its structural authority.
- *Refs:* Ahn et al. 2022 (SayCan); Liang et al. 2023 (Code-as-Policies); Singh et al. 2023 (ProgPrompt); Huang et al. 2022 (Inner Monologue); Liu et al. 2023 (LLM+P).

**Theme 2 — Behaviour trees as a robot control architecture (your core technique)**

- *Essential mechanics to explain:* BT node types (sequence/fallback/parallel, condition, action), the `SUCCESS / FAILURE / RUNNING` return semantics, ticking, and why BTs are modular, reactive and fail-safe vs finite-state machines — a failed condition aborts the sequence before any action. This is the one algorithm you must actually explain, because the safety claim rests on it.
- *What others did:* BTs originated in game AI, now a mainstream robotics control formalism; recent work explicitly combines LLMs with behaviour trees.
- *Refs:* Colledanchise & Ögren 2018 (*Behavior Trees in Robotics and AI* — canonical); Iovino et al. 2022 (*Robotics and Autonomous Systems*, BT survey); a 2023–2024 LLM-generates-BT paper to contrast with the *don't-let-the-LLM-generate-the-tree* stance.

**Theme 3 — Evaluating BT-based / language-to-action systems (your metrics)**

- *What to cover:* define the metric set used — desired-behaviour success rate, single-node success rate, unsafe-state count — and why they suit a safety-critical command system (unsafe-state count is the headline safety measure).
- *Ref:* Gugliermo et al. 2024. Add a sentence on adopting published metrics for comparability.

**Theme 4 — On-device / small language models and constrained decoding**

- *What to cover:* feasibility and limits of running 2–4B-parameter models locally (Gemma, Qwen, Llama variants); quantisation; constrained/structured decoding (forcing valid JSON/enum output).
- *What worked/didn't:* small models reliable at *classification*, poor at *long structured generation* — the empirical fact the architecture is built on. Cite a capability/benchmark source rather than asserting it.
- *Refs:* Gemma technical report (Google DeepMind 2024); a small-LM survey; an Ollama/llama.cpp or constrained-decoding (e.g. Outlines/grammar-constrained) reference.

**Theme 5 — Voice interface stack (STT/TTS) as enabling components**

- *What to cover (briefly — tools, not contribution):* modern speech-to-text (Whisper / faster-whisper, lightweight on-device Vosk, Moonshine) and neural text-to-speech (Piper, Kokoro). One paragraph: what they are, why on-device matters (privacy, latency, no connectivity).
- *Refs:* Radford et al. 2023 (Whisper, ICML); Vosk/Moonshine/Piper project docs.

**Theme 6 — FarmBot platform and the AURA ROS2 stack (system context)**

- *What to cover:* FarmBot Genesis XL as an open-source CNC-style agricultural robot (gantry, tool-head, G-code-like firmware command set); ROS2 as middleware; the AURA stack built on. State plainly this is third-party context.
- *Refs:* FarmBot documentation; the AURA FarmBot ROS2 repository/paper if one exists.

**Theme 7 — (Light touch) accessibility & voice for assistive robotics**

- *What to cover:* enough to show the domain is studied — voice control for assistive/service robots, HCI accessibility for older adults — then defer motivation to Chapter 1. One short paragraph, technical framing only.

**How to pitch for marks**

- **Depth** → Themes 1–3 (LLM-planning landscape, BT mechanics, evaluation metrics): explain techniques properly and critique prior work.
- **Breadth** → Themes 4–7: show awareness, cite, point to references for detail.
- Anchor every theme on at least one 2022–2024 peer-reviewed source, not blogs/slides.
- Key analytical move: "prior LLM-to-action work makes the model generate structure; small on-device models can't do this reliably; therefore this project inverts the responsibility." That sentence makes the background a *positioning argument*, not a list.

---

# 3. Companion paper abstracts (drafts)

*Two papers in preparation, intended to become extended reports feeding the
thesis. Same system + evaluation, two distinct contributions: Paper 1 is the
safety/accessibility angle (HRI/assistive venue); Paper 2 is the portable
framework (robotics/systems venue). All metrics below are **placeholders — pin
the final corpus numbers (DBSR/SNSR/USC, latency) before submission**, same
caution as §1.4/§1.6.*

**Key related work to position against (real papers, verify before final cite):**

| Tag | Paper | What it does | Why it matters to us |
|---|---|---|---|
| **[InterpBT-25]** | Izzo et al., *Interpretable Robot Control via Structured Behaviour Trees and LLMs*, arXiv 2508.09621 (2025) | LLM **selects pre-existing BT modules** (not generation); BT executes; "interpretable"; 94% on Tello + Spot | **Our closest mirror.** But cloud GPT-4o, **text-only**, **no safety guarantees**, non-assistive — we are on-device + voice + provable safety + vulnerable-user |
| **[BTGenBot-24]** | Izzo et al., *BTGenBot*, IROS 2024 (POLIMI) | LLM **generates full BT (XML)** with **lightweight ~7B** models (Llama-2 / Code-Llama 7B), deployable on-robot; needs one-shot prompting | The "let the small model generate the tree" camp — the **foil for our classify-vs-generate ablation** |
| **[BETR-XP-24]** | *Automatic Behaviour Tree Expansion with LLMs*, arXiv 2409.13356 (2024) | LLM does goal interpretation + failure repair; a **PDDL planner builds the tree**; GPT-4 | Structure-building delegated away from the raw LLM (same instinct as ours, via a planner not a node library) |
| **[SayCan-22]** | Ahn et al., *Do As I Can, Not As I Say*, CoRL 2022 | LLM + learned affordances; 84% planning / 74% execution; needs an affordance model per skill; cloud-scale | The end-to-end grounding lineage we depart from (opaque, large model, per-skill training) |
| **[CodePolicies-22]** | Liang et al., *Code-as-Policies*, ICRA 2023 | LLM emits **executable code** composing motion primitives | Powerful but arbitrary code = no inspectable safety; brittle on small models |
| **[RoboInspector-25]** | *RoboInspector*, arXiv 2508.21378 (2025) | Empirically shows **LLM-generated policy code is unreliable** for manipulation | External evidence for our "don't let the LLM emit structure" premise |
| **[InteLiPlan-24]** | *InteLiPlan*, arXiv 2409.14506 (2024) | Interactive **lightweight** LLM planner for domestic robots | Another on-device/lightweight data point to position against |
| **[VA-Errors-24]** | *Situated Understanding of Errors in Older Adults' Interactions with Voice Assistants*, arXiv 2403.02421 (CHI 2024) | Month-long in-home study, **15 older adults (66–94)**; intent-recognition the primary error; errors **escalate when corrected**; users **abandon**; transparency gap | **Empirical anchor for Paper 1** — documents the exact failure modes our architecture prevents by construction |
| **[VA-Health-21]** | *An Empirical Study of Older Adults' VA Use for Health Information Seeking*, ACM TiiS (201 older adults) | VAs give misleading/inaccurate info; users distrust | Backs the "wrong output is harmful to this population" premise |
| **[SAR-Older-25]** | LLM-powered socially assistive robots for older adults, CHI 2025 / JMIR 2025; hybrid rule+LLM assistant **GRACE**, JMIR Aging 2025 | LLM/hybrid assistants for older adults; flag hallucination, safety, privacy as open risks | Closest on the older-adult + hybrid axis, but **conversational, not physical-action-safe** |

## 3.1 Paper 1 — Safety & Accessibility

**Working title:** *Safe by Construction: Constraining LLMs with Behaviour Trees
for Voice-Controlled Assistive Robots for Older Adults.*

**Target venue:** ACM ASSETS, IEEE RO-MAN, or ACM/IEEE HRI (assistive/safety track).

**Abstract (draft).**
Voice is the most accessible interface for older adults and people with reduced
mobility or memory, but pairing it with large language models (LLMs) and
physical robots introduces a safety hazard: LLMs hallucinate, and a single wrong
output can drive a robot into an unsafe state near a vulnerable user. Empirical
studies of older adults using commercial voice assistants already document the
core failure modes — intent-recognition errors that *escalate* when the user
tries to correct them, abandonment after repeated unexplained failures, and a
transparency gap in which the user never learns what the system understood
[VA-Errors-24, VA-Health-21]. We present a *safe-by-construction* architecture
that answers these failure modes structurally: the LLM is deliberately
constrained to **flat intent classification** and all decision-making,
sequencing, and safety enforcement is delegated to a **deterministic,
inspectable behaviour tree (BT)**. We instantiate it in GrowMate, a voice
interface to a FarmBot Genesis XL garden robot co-designed for elderly
gardeners, and evaluate it on an utterance corpus spanning single, multi-intent,
indirect, and safety-trigger commands. The system achieves a desired-behaviour
success rate of **~97%** while maintaining **zero unsafe states (USC = 0)** even
under intent misclassification, because the BT's safety preconditions abort
unsafe actions before any motion — turning a misunderstanding into a visible,
safe no-op rather than an escalating, opaque failure. We argue that *which
capability you withhold from the LLM* is the central safety-design decision for
assistive HRI, and contrast our approach with end-to-end LLM-to-robot pipelines
[SayCan-22, CodePolicies-22], interpretable-but-cloud LLM+BT control
[InterpBT-25], conversational assistive robots for older adults [SAR-Older-25],
commercial voice assistants, and conventional button/app interfaces.

**Contributions.** (1) A safety argument for assistive voice-robotics — the LLM
supplies *meaning*, a deterministic BT supplies *behaviour + safety*. (2) An
explicit, code-level safety contract (`CheckAvailable → [CheckToolMounted] →
[CheckBounds] → [CheckPlantFound] → action`) plus a tick-and-verify gate (honest
event logs for memory-impaired users). (3) An evaluation showing **USC = 0 under
misclassification**, separating *safe-but-unhelpful* failures from *unsafe* ones.
(4) Co-design insights from the elderly assistive-gardening context.

**Positioning vs the older-adults + AI literature.** Prior empirical work
*identifies* the harms but offers no architectural fix; we provide the fix.
Each documented failure mode maps to a design response:

| Documented failure (older adults + commercial voice AI) | Our structural response |
|---|---|
| Intent-recognition failure is the *primary* error type [VA-Errors-24] | Intent isolated into one constrained classification step (the task small models do reliably); no free-form understanding in the loop |
| Errors *escalate* when users try to correct them [VA-Errors-24] | A misclassification fails *safe and bounded* (USC = 0) — a wrong intent aborts at a guard rather than acting and forcing a fight to undo |
| Users *abandon* after repeated unexplained failures [VA-Errors-24] | "Honest-or-blank" feedback + inspectable trees: the user sees what was understood and what will happen, not a silent guess |
| Transparency gap — user never learns what was understood [VA-Errors-24] | The tree is **inspectable before it runs**; the system never silently acts on a misunderstanding |
| Hallucinated/inaccurate output harms a population less able to spot it [VA-Health-21, SAR-Older-25] | LLM kept out of the safety-critical path; "done" verified against firmware, so confidently-wrong feedback is structurally prevented |

*Honesty note:* this is a **failure-mode / design-principle** comparison, not a
like-for-like benchmark ([VA-Errors-24] is a qualitative study of a
*conversational* assistant, not a robot-control task on our corpus). Claim
"safe-by-construction prevents these failure modes," **not** a measured
older-adult outcome unless the focus-group/SUS data is run and reported.

**Comparison axis (draft table).**

| Approach | Accessible (voice) | Safe physical action | Inspectable plan | Runs on-device |
|---|---|---|---|---|
| Commercial voice assistant — Alexa [VA-Errors-24] | ✅ | ❌ | ❌ | ❌ |
| Conversational assistive robot for elders [SAR-Older-25] | ✅ | ➖ (no manipulation) | ❌ | ❌ |
| End-to-end LLM→robot [SayCan-22, CodePolicies-22] | ✅ | ⚠️ | ⚠️ | ⚠️ |
| Interpretable LLM+BT control [InterpBT-25] | ⚠️ (text-only) | ⚠️ (no guarantees) | ✅ | ❌ (cloud GPT-4o) |
| Button / app interface (e.g. FarmBot web UI) | ❌ | ✅ | ✅ | ✅ |
| **Ours (LLM-constrained BT)** | ✅ | ✅ (USC 0) | ✅ | ✅ |

**Evals to strengthen:** quantify the misclassification stress test (N forced
wrong intents → USC); a small **user study** with older adults (task success,
SUS, trust) — the biggest credibility lift, and the only way to convert the
positioning table above from "prevents by construction" to "measured outcome";
accessibility/latency metrics (time-to-action, words-to-rephrase).

## 3.2 Paper 2 — Framework / Methods

**Working title:** *VoiceBT: A Reusable Framework for Voice-to-Action Robotics
via LLM Intent Classification and Behaviour-Tree Synthesis over an Existing Node
Library.*

**Target venue:** IROS/ICRA (or an LLMs-in-robotics workshop); the framework
itself could also suit a systems/open-source venue.

**Abstract (draft).**
We present **VoiceBT**, a framework for turning natural speech into safe robot
action without fine-tuning or large on-board models. The method has three
deterministic stages: (1) an LLM performs **flat intent classification only** —
mapping an utterance to a fixed `{action, target}` enum; (2) a compiler maps each
intent to a **behaviour-tree subtree** assembled from a library of reusable,
pre-existing robot action/condition nodes; (3) the tree is ticked to completion
with **per-action firmware verification**. Because the LLM never emits structure,
the approach runs reliably on a small on-device model (Gemma-class, 2–4B) where
free-form plan/code generation fails. We demonstrate VoiceBT by layering it
additively on the AURA FarmBot ROS2 stack — reusing its existing command
vocabulary and node library — to expose a growing action set (water, smart-water,
weeding, vision-based map building, etc.) via voice, and evaluate intent→tree
correctness on a multi-category utterance corpus (DBSR ~97%, USC = 0). Unlike
approaches that ask the model to emit the structure itself — full behaviour-tree
XML [BTGenBot-24], executable policy code [CodePolicies-22], or an action plan
[SayCan-22] — VoiceBT moves all structure-building into deterministic code, so it
does not inherit the unreliability of small-model structured generation
[RoboInspector-25]. The recent module-selection system closest to ours
[InterpBT-25] shares the "select over a library" instinct but runs a cloud
GPT-4o and is text-only and unverified; we are on-device, voice, and
firmware-verified. We argue that *constrained classification + compile-to-tree
over a reused node library* is a practical, portable recipe for adding a safe
natural-language interface to any robot with a fixed command set.

**Contributions.** (1) The **VoiceBT recipe**: flat-classify → compile-to-BT-from-
an-existing-node-library → tick-with-verify; portable to any fixed-vocabulary
robot. (2) **Additive integration** — sits on top of an existing stack, publishing
only through its existing command interface, no fork of the base robot code.
(3) A **reliability argument for constrained classification on small models** —
an empirical contrast between flat classification (reliable at 2–4B) and
free-form tree/plan generation, which prior work shows needs one-shot prompting
even at 7B [BTGenBot-24] and produces unreliable policy code [RoboInspector-25];
our ablation measures where generation degrades as the model shrinks below that.
(4) **Capabilities as data, not model behaviour** — each new skill is one enum
value + one tree builder over existing nodes.

**Comparison axis (draft table).**

| Approach | LLM output | On-device / small-model | Safety / inspectability | New-skill cost |
|---|---|---|---|---|
| End-to-end plan [SayCan-22] | action sequence | ❌ cloud-scale | ⚠️ opaque | prompt + per-skill affordance model |
| Code-as-Policies [CodePolicies-22] | executable code | ❌ on small models [RoboInspector-25] | ❌ arbitrary code | prompt + handcrafted API |
| Planner-built BT [BETR-XP-24] | goal conds + repair (GPT-4) | ❌ (untested small) | ✅ tree (planner-built) | prompt + PDDL domain |
| LLM-generates-BT [BTGenBot-24] | full tree XML | ⚠️ ~7B, one-shot needed | ✅ tree / ⚠️ if malformed | prompt + example |
| Module-select + BT [InterpBT-25] | module choice | ❌ cloud GPT-4o | ✅ interpretable / ⚠️ no guarantee | add a module |
| **VoiceBT (ours)** | flat `{action,target}` | ✅ Gemma 2–4B on Pi | ✅ deterministic tree + firmware verify | 1 enum + 1 builder |

**Headline experiment to run:** the **classification-vs-generation ablation** —
the *same* small model asked to (a) classify vs (b) emit a valid BT/plan,
reporting valid-output rate + DBSR across model sizes (0.5B/1B/4B), ideally
**on-device** (LiteRT-LM on the phone/Pi). This is the experiment that directly
tests where [BTGenBot-24]'s 7B generation breaks down as the model shrinks, and
turns our "small models can't reliably generate structure" claim from an
assertion into a measured result. Plus a **portability** demonstration (apply the
recipe to a second robot/command set) to support the framework claim.

## 3.3 How the two relate (for the thesis)

One system and one evaluation corpus, **two contributions**: Paper 1 leads on
*safety + accessibility for vulnerable users* (USC, the misclassification stress
test, and a user study); Paper 2 leads on *a portable engineering framework* (the
classify-vs-generate ablation and portability). Minimal self-overlap if each
leads with its own related-work, contribution, and eval emphasis. The two
hardware-free experiments that most strengthen **both** are (i) the
classification-vs-generation ablation and (ii) extending the eval corpus to the
new verbs (sense/weed/map-build) and re-reporting DBSR/SNSR/USC.

---

## Open items to confirm

- Did a **focus group / SUS questionnaire** with the target population actually run? If yes, move it into §1.2 scope and §1.4 evaluation; if planned-only, keep it excluded.
- Confirm **AURA stack authorship** (supervisor / another student / research group) and name explicitly in §1.6.
- Decide **four vs five contributions** in §1.6 (portability as standalone or sub-claim).
- Pin down which **evaluation numbers are final** (V1 interim vs V2 hardware run) before quoting figures in the Abstract / §1.4 / §1.6.
- **Verify every citation** (author/year/venue) before pasting — the references above are leads, not confirmed.
