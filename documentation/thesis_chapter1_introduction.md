# 1. Introduction

## 1.1 Motivation and problem domain

**Q: What is the topic / broader problem area?**

- Ageing and disabled populations are growing, and with age and disability comes a loss of the physical ability (bending, lifting, reaching, fine motor control) needed for everyday physical tasks — including gardening.
- Gardening matters: it is therapeutic, with physical, mental and social health benefits, and it ties to food self-sufficiency — it is not just a hobby.
- Agricultural and horticultural robots (precision agriculture, and small-scale platforms such as the FarmBot) can perform that physical labour autonomously: watering, seeding, weeding and imaging.
- The mismatch is that the people who would benefit most from these robots are locked out of them, because the robots are operated through desktop apps, coordinate maps and command sequences that assume a technically literate user at a screen.

**Q: Why should anyone care about this problem and these results?**

- It is an accessibility and independence issue: the assistive technology already exists, but its interface defeats its own purpose for the intended user.
- Voice is the natural fix, but a voice interface to a physical robot is harder than to a phone: a misunderstood command can crash the gantry into the bed wall, run the pump on the wrong plant, or move a tool unexpectedly.
- Current voice assistants and end-to-end language-model agents are (a) opaque — the user cannot see what was understood or what is about to happen — and (b) unreliable — language models hallucinate, and a confidently wrong output drives real hardware.
- The results matter because they show that natural-language robot control can be made genuinely safe and transparent for non-expert, vulnerable users, not merely plausible-sounding.

**Q: Which references support this?**

- Ageing population statistics — WHO, *World Report on Ageing and Health* (2015) / UN, *World Population Prospects*.
- Gardening and health — Soga, Gaston & Yamaura (2017), *Preventive Medicine Reports*.
- Agricultural robotics / precision agriculture — a survey (e.g. Duckett et al., 2018, agricultural-robotics white paper).
- FarmBot platform — FarmBot official documentation / Genesis specifications.
- Older adults and the technology barrier — an HCI accessibility / digital-divide source.
- Language-model hallucination and unreliability — Ji et al. (2023), *ACM Computing Surveys*.

**Q: What is the high-level, abstract problem the project addresses?**

- How to build a natural-language (voice) interface to a physical robot that is, simultaneously, (1) accessible to non-expert users, (2) safe to run unsupervised, and (3) transparent — so the user can verify what the system will do before it does it.

## 1.2 Problem statement and scope

**Q: What is the specific technical problem you set out to solve?**

- To map free-form spoken commands onto a safe, verified sequence of physical robot actions, using a language model small enough to run on-device (Raspberry Pi, no cloud), while guaranteeing the model can never directly cause an unsafe action.
- The crux is that the obvious approach — letting a language model generate the action plan or structured tree end-to-end — fails on small on-device (2–4B) models, which produce valid structured output only a small fraction of the time and hallucinate. Large cloud models would help but break the on-device, private, low-cost constraint.
- So the real problem is one of architecture and division of labour: how to use an unreliable small model for the one thing it does well (understanding intent), while making structure and safety the responsibility of deterministic code, and proving the result transparent (inspectable before execution) and verified (a reported "done" reflects real firmware completion, not a guess).
- Sub-problems that follow:
  - constraining the model's output so it cannot emit unsafe or structurally invalid plans (flat intent classification over a fixed enum);
  - guaranteeing every robot action is gated by safety checks (availability, bounds, target-found) in code, not by the model;
  - keeping emergency stop entirely outside the model (no language model in the safety-critical path);
  - closing the loop on real hardware so a success counts only when the firmware confirms it (tick-and-verify);
  - running the whole speech → intent → tree → robot → speech pipeline locally, fast enough to be usable.

**Q: What is in scope (deliberately included)?**

- The framework (VoiceBT) and its instantiation on the FarmBot (GrowMate).
- The on-device pipeline end-to-end: speech-to-text, flat intent classification via a small local language model, deterministic behaviour-tree construction, execution, and spoken confirmation.
- The safety architecture: an in-code safety prefix on every action, a language-model-free emergency stop, and bounds/availability/target checks.
- Tick-and-verify against real FarmBot firmware, with inspectable trees.
- A defined action set actually wired and demonstrated (move/jog/home, water single species and water all, lights, photo, panorama, weed detection, soil/moisture queries, emergency stop).
- Deployment on real hardware (two FarmBot installations) and a quantitative evaluation (29-utterance corpus; desired-behaviour success, single-node success, unsafe-state count, latency).

**Q: What is out of scope (deliberately excluded)?**

- Not general open-ended conversation or a general-purpose assistant — only the gardening/robot command domain.
- Not letting the language model generate trees or plans — explicitly rejected as the failure mode this work avoids.
- Not cloud or large language models — the on-device constraint is kept on purpose.
- Not novel speech-recognition, speech-synthesis or perception models — these are off-the-shelf components; the contribution is the architecture, not the recognisers or the vision.
- Capabilities demonstrated only to the detection/query level, not full physical action: moisture-aware "smart" watering, acting on detected plant/weed coordinates, physical weed removal, and seeding are roadmap, not claimed results.
- Not a formal clinical or human-subjects study with the target elderly/disabled population — usability framing and design-for-accessibility are in scope, but a controlled user trial is not.

## 1.3 Approach

**Q: What is the general design/solution approach — which paradigm?**

- Hybrid neuro-symbolic / model-based, deliberately not end-to-end data-driven. The learned component (the language model) is used only for perception of intent; all decision structure and control is deterministic, symbolic engineering (behaviour trees plus coded safety logic).
- The division is:
  - Data-driven, only at the edges: off-the-shelf neural speech-to-text, a small pretrained language model for classification only, and neural text-to-speech. None of these are trained or fine-tuned in this work — they are used as black-box components.
  - Classical / model-based engineering at the core: behaviour trees (a well-established robotics control-architecture formalism) generated by deterministic Python from a typed action library; a hand-specified safety prefix; a fixed intent enum; and a tick-and-verify execution gate tied to firmware state.
- The controlling philosophy is constraint, not capability: rather than make the model more powerful, I restrict it to the narrow task it does reliably (flat intent classification) and push everything safety-relevant into inspectable code.

**Q: Which alternative paradigms were considered and positioned against?**

- End-to-end language-model agent (the model generates the plan/tree directly) — rejected: small on-device models cannot reliably produce structured output and can hallucinate unsafe actions.
- End-to-end / reinforcement-learning policy mapping speech to action — not appropriate: there is no reward or data regime for a safety-critical, low-volume domain, and it would be opaque (failing the transparency requirement).
- A purely classical command-grammar / fixed keyword parser — too brittle for natural, indirect phrasing ("the carrots look thirsty"); the language model is what buys the natural-language flexibility a rigid grammar cannot.
- The chosen approach sits between these: language-model flexibility for understanding, classical determinism for safety.

**Q: What analysis / evaluation techniques are used?**

- Quantitative evaluation on a fixed utterance corpus using an established metric set from the behaviour-tree literature (desired-behaviour success rate, single-node success rate, unsafe-state count), plus latency — measured against published metrics, not ad-hoc ones.
- Deployment-based validation on real hardware (two FarmBot installations), not simulation alone.

**Q: Is the solution an extension of prior work, or new?**

- Both. The building blocks are prior work: behaviour trees in robotics (Colledanchise & Ögren, 2018), language models used as intent classifiers, and the evaluation metrics of Gugliermo et al. (2024).
- The contribution is the architectural combination and the constraint principle: using a small on-device language model strictly as a flat intent classifier feeding a deterministic, safety-prefixed, inspectable behaviour tree for a physical robot, and demonstrating it safe and transparent on real hardware. That framing — transparency, provable in-code safety, and on-device operation for accessibility — is the novel synthesis.
- It is a novel integration and framework rather than a new learning method or a new control theorem.

## 1.4 Evaluation approach

**Q: Real-world or simulation?**

- Both, layered. A deterministic simulation harness runs the entire pipeline (intent → tree build → tick-and-verify → command emission) with no robot or ROS, so behaviour and safety can be checked repeatably; the same stack is then validated on real FarmBot hardware (two installations).
- The split is deliberate: simulation gives repeatable, robot-free regression testing (the harness fakes firmware signals where needed); hardware gives external validity (real motors, pump, firmware, serial link, timing).
- I am careful about which numbers come from which setting: the corpus metrics come from the intent-server evaluation, while the hardware runs confirm the gate/firmware loop behaves identically.

**Q: How did you make the evaluation trustworthy and replicable?**

- A fixed, frozen corpus of 29 utterances covering single actions, multi-intent utterances, indirect/natural phrasing, queries, and safety triggers — the same inputs every run.
- Established metrics rather than ad-hoc ones: desired-behaviour success rate, single-node success rate, unsafe-state count, plus end-to-end latency — drawn from the behaviour-tree evaluation literature so results are comparable with other work.
- A scripted, one-command evaluation: `tools/evaluate_v2.py` drives the corpus against the real intent server over HTTP and emits per-case results; a JSON option dumps per-case detail for the appendix, so the run can be re-run and audited.
- Command-level success criteria defined up front (the expected command must appear), removing subjective judgement of "correct."
- Inspectable trees and an event log: every decision is pure data viewable before execution, and care events are logged only after firmware confirmation — so a pass is auditable, not asserted.

**Q: What did you control?**

- The corpus (fixed inputs), the action enum, the safety prefix, the garden map, the workspace bounds, the speech-to-text model, the language model (Gemma 3:4B via Ollama), the prompt, and the success criteria — all pinned so a run is reproducible.

**Q: What did you measure?**

- Desired-behaviour success (whether the right commands were published), single-node success (the fraction of tree leaves that returned success), unsafe-state count (the key safety metric, target = 0), end-to-end latency, and event-log coverage (whether care events that should be logged actually were).

**Q: What did you ignore / not control / leave as threats to validity?**

- Speech-to-text and text-to-speech quality — used as off-the-shelf components, not isolated or benchmarked; transcription error is folded into end-to-end behaviour rather than measured separately.
- Language-model non-determinism — the small model can classify the same utterance differently across runs; for indirect/query utterances "correct" is genuinely ambiguous (e.g. a general question versus a moisture check, both defensible), a known source of variance in the desired-behaviour-success figure.
- No human-subjects measurement of accessibility/usability in the core evaluation — the corpus is researcher-authored, not collected from the target population.
- Corpus size — 29 utterances is small; this measures feasibility and safety, not population-level accuracy.
- Hardware variation — timing/firmware idiosyncrasies between the two FarmBots are not formally factored out.
- In short: the evaluation tests safety and feasibility under controlled, replicable conditions; it does not claim a large-scale accuracy benchmark or a clinical usability study.

## 1.5 Broader impact: ethics, society and the environment

**Q: What is the project's relationship to society?**

- Positive: it restores independence and access to gardening (and its physical, mental and social health benefits) for elderly and disabled people currently excluded from assistive farming robots by their interfaces, supporting ageing in place and dignity/autonomy.
- Inclusion / digital divide: the voice-driven, on-device design lowers the technical-literacy barrier and does not require always-on connectivity, so it can reach users and locations a cloud/app-based system cannot.
- The risk side, acknowledged rather than buried: automating a vulnerable user's robot carries safety risk if it misacts; there is a risk of over-reliance and de-skilling; and there is a duty not to over-promise to a vulnerable group — which is exactly why the safety/transparency architecture is the ethical core of the project, not just a feature.

**Q: What is the project's relationship to the environment?**

- Resource efficiency: targeted, sensor-aware watering and precise per-plant action reduce water and input waste compared with broadcast or manual gardening — stated as design intent and a partially built capability, not a measured saving, since smart watering remains on the roadmap.
- Local food production: supports small-scale, low-input home and community growing.
- On-device compute: a small local model avoids the energy and carbon cost of repeated cloud inference — a modest but real efficiency argument.
- Honest counterweight: the robot itself carries manufacturing, embodied-energy and electricity costs, so the net environmental benefit is plausible but not measured in this work.

**Q: Which UN Sustainable Development Goals does it map to?**

- SDG 3 (Good Health and Well-being) — therapeutic gardening, mental and physical health, healthy ageing.
- SDG 10 (Reduced Inequalities) — accessibility for disabled and older users; narrowing the digital divide.
- SDG 2 (Zero Hunger) — support for local and home food production and precision growing.
- SDG 12 (Responsible Consumption and Production) — efficient use of water and inputs through precise, sensor-aware action.
- Weaker links that could be mentioned but are not relied upon: SDG 11 (sustainable communities, via community gardens) and SDG 9 (innovation / accessible infrastructure).

**Q: What are the core ethical considerations (framed high-level here)?**

- Safety of a vulnerable user is the dominant concern — an unsupervised machine acting physically on behalf of someone who may be unable to intervene. The safety architecture (in-code guards, language-model-free emergency stop, firmware-verified "done") is the direct response.
- Transparency and honesty — the system must never claim it did something it did not ("honest-or-blank"); confidently wrong feedback is itself a harm to a trusting user.
- Autonomy and consent — the user stays in control; the robot acts only on explicit command, with an always-available stop.
- Privacy — on-device speech and inference keep voice data local rather than sending it to the cloud.
- Non-maleficence versus over-promising — building for a vulnerable population obliges conservative claims and fail-safe defaults.
- The detailed responsible-engineering analysis (specific hazards and mitigations, standards, regulatory bodies, stakeholder breakdown) is given in Chapter 4.

## 1.6 Original contributions and externally provided resources

**Q: What are the main achievements / contributions?**

- **The VoiceBT framework — a constraint-based architecture for safe natural-language robot control.** The core contribution: restricting a small on-device language model to flat intent classification and delegating all structure, sequencing and safety to a deterministic, inspectable behaviour tree. This is a contribution to the state of the art — the headline of a framework paper in preparation; the principle that the language model proposes and the behaviour tree disposes.
- **An in-code safety architecture with a provable safety property.** Every robot action is gated by a coded safety prefix (availability → bounds → target-found), emergency stop is matched before the model is ever called, and "done" is firmware-verified (tick-and-verify). It is demonstrated to hold even under language-model misclassification — an unsafe-state count of zero across the evaluation. This safety claim and its evidence are the core of an accessibility/safety paper.
- **GrowMate — a working on-device voice-control system for the FarmBot, deployed on real hardware.** The full local pipeline (speech-to-text → intent → behaviour tree → robot → spoken confirmation) running on a Raspberry Pi, with a demonstrated action set (move/jog/home, watering single and all, lights, photo, panorama, weed detection, soil/moisture queries, emergency stop), deployed and run on two physical FarmBot installations.
- **A reproducible, safety-focused evaluation.** A fixed 29-utterance corpus, a scripted one-command evaluation, and reporting against established behaviour-tree metrics (desired-behaviour success rate, single-node success rate, unsafe-state count, latency) — showing approximately 97% desired-behaviour success with zero unsafe states.
- **A demonstration of the framework's portability and generality.** The architecture is robot-agnostic — the publisher, the action enum and the tree builders are the only robot-specific parts — shown by the additive integration onto the existing FarmBot stack without forking it.

**Q: What is not your own work (clearly identified)?**

- **AURA FarmBot ROS2 stack** — the entire upstream robot control stack (`farmbot_bringup`, `farmbot_controllers`, `farmbot_command_handler`, `farmbot_interfaces`, `map_handler`, `camera_handler`, and so on), developed at Maynooth University. Used as-is; my work is additive and publishes only through the existing `keyboard_topic`. One exception I authored: a single bug-fix to the `map_handler` tool-sequencer release-direction logic — a minor fix to others' code, not a contribution.
- **FarmBot Genesis XL hardware and Farmduino firmware** — a commercial product, not my design.
- **Speech-to-text engines** — faster-whisper / Whisper, Vosk, Moonshine: off-the-shelf, pretrained, unmodified.
- **Text-to-speech engines** — Piper, Kokoro: off-the-shelf, unmodified.
- **Language model and runtime** — Gemma 3 (4B) served via Ollama: a pretrained third-party model; I wrote the prompt and classifier wrapper but did not train or fine-tune the model.
- **Core libraries** — `py_trees`, ROS2, FastAPI/Starlette/uvicorn, pydantic, and others: standard third-party frameworks.
- **Evaluation metrics** — the DBSR/SNSR/USC metric definitions are from Gugliermo et al. (2024); the corpus, harness and their application are mine, but the definitions are not.
- **Theoretical foundations** — the behaviour-tree formalism (Colledanchise & Ögren, 2018) is prior art I build on.
- **Supervisor and technical staff** — any hardware setup, greenhouse access or stack origin provided by Dr Sorouri or by technicians is gratefully acknowledged as not my own work.

## 1.7 Structure of this report

- **Chapter 2 — Technical and Related Background.** Reviews the work this project builds on: behaviour trees as a robotics control architecture, language models for intent classification and the limits of end-to-end language-model planning on small on-device models, voice-interface and accessibility research, the FarmBot platform and the AURA control stack, and the evaluation metrics adopted from the behaviour-tree literature.
- **Chapter 3 — Design and Implementation.** The core of the work: requirements and problem analysis, the VoiceBT architecture and key design decisions (flat intent classification, the in-code safety prefix, inspectable trees, tick-and-verify), the implemented GrowMate system and pipeline, and the testing, evaluation and results — including interpretation of the desired-behaviour-success and unsafe-state findings against prior approaches.
- **Chapter 4 — Ethics, Societal and Environmental Impact, and Responsible Engineering.** The detailed responsible-engineering analysis: health-and-safety hazards and mitigations, applicable standards and regulatory bodies, and stakeholder, societal and environmental impacts.
- **Chapter 5 — Conclusions and Future Work.** What the results imply for the central claim that constraining a language model to classification yields safe, transparent robot control; the contribution to the state of the art; how far it generalises; and genuinely motivated future work (smart watering, perception-driven action, weeding and seeding, and an on-device classification benchmark).
- **Chapter 6 — Project Retrospective.** A reflective account of the process against the original plan: what went well, the challenges (hardware bring-up, on-device model limits, honest feedback), lessons in research and project management, and what would be done differently.
