# GrowMate — Leipzig presentation
# Advances in Robotics & AI Workshop in Sustainable Agriculture
# 12 May 2026

**Audience:** Robotics & AI researchers, sustainable agriculture practitioners
**Format:** 8 slides, ~20–25 min
**Speaker:** Rishabh Jain (Maynooth University, MSc Robotics & Embedded AI)
**Supervisor:** Dr Majid Sorouri

---

## Slide 1 — Title

```
[PHOTO: elderly person tending a garden, or FarmBot in a
 productive bed.  Warm, human, agricultural.]

    GrowMate
    Transparent Voice-Robot Interaction through
    LLM-Constructed Behaviour Trees
    for Accessible Agricultural Robotics

    Rishabh Jain  ·  Maynooth University
    Supervisor: Dr Majid Sorouri

    Advances in Robotics & AI Workshop
    in Sustainable Agriculture  ·  12 May 2026
```

**Speaker notes (~30 s):** Introduce yourself and the project in one sentence — GrowMate is a voice interface for agricultural robots designed for elderly users, where the LLM classifies intent and deterministic code constructs the safety-validated behaviour tree. Acknowledge supervisor and Maynooth.

---

## Slide 2 — Why Elderly Users Can't Garden

```
[DIAGRAM: three bulleted barriers, then three callout cards
 below, then a closing line]

  Aging population barrier
    Reduced strength, reach, and balance —
    lifting pots and watering cans becomes impossible.

  Safety concerns
    Fall risk when bending, reaching, or
    working close to the ground.

  Cognitive load
    Memory challenges make watering schedules
    and task sequences difficult to keep up with.

[THREE CALLOUT CARDS — equal width, distinct colour]

  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
  │ Demographic    │  │ Intuitive      │  │ Research Gap   │
  │ Need           │  │ Interaction    │  │                │
  │                │  │                │  │ Current voice  │
  │ 21% of the     │  │ 85% of elderly │  │ robots are     │
  │ European       │  │ users prefer   │  │ rigid and      │
  │ population is  │  │ voice over     │  │ platform-      │
  │ 65+, facing    │  │ buttons.       │  │ specific.      │
  │ mobility, not  │  │                │  │                │
  │ cognitive,     │  │                │  │ No generalis-  │
  │ decline.       │  │                │  │ able framework │
  │                │  │                │  │ exists.        │
  └────────────────┘  └────────────────┘  └────────────────┘

  A perfect problem for an LLM-powered voice interface:
  speak naturally, the robot does the physical work.
```

**Speaker notes (~2.5 min):** Three barriers, three reasons this is the right moment. The barriers are physical (strength, reach), safety (falling), and cognitive (forgetting what needs doing). The three cards underneath turn this into a sized opportunity — 21 percent of Europe is over 65, 85 percent of elderly users prefer voice when asked, and the existing voice-robot literature does not yet offer a generalisable framework. The closing line is the bridge to the proposal: speech for the user, physical work for the robot, an inspectable plan in between.

---

## Slide 3 — What we propose

```
[DIAGRAM: simple left-to-right flow]

   Person speaks          GrowMate              FarmBot
   naturally         ─────────────────►    does the work
                          on a Pi

  "Water the tomatoes"   →  moves to plant, opens pump,
                             waters for 6 s, closes pump

  "The herbs look dry"   →  checks moisture, decides,
                             asks user to confirm, then waters

  "Stop"                 →  emergency stop, < 1 ms,
                             bypasses all AI

  Voice preferred by participants over buttons or apps.
  "Would it be like speaking to Alexa?"
```

**Speaker notes (~2 min):** The core proposal: the user speaks naturally, GrowMate interprets the intent, and FarmBot acts. Three examples show the range — a direct command, an indirect observation that triggers a sensor read and a confirmation step, and a safety trigger that bypasses everything. The voice preference came out in our Dundalk focus group unprompted; several participants drew the Alexa comparison themselves. What GrowMate adds beyond Alexa is that it controls a physical robot, which means safety has to be built in — you cannot just pass the speech to an LLM and hope it does the right thing.

---

## Slide 4 — The behaviour tree: from speech to action

```
[DIAGRAM: two-part visual]

[LEFT HALF — how a tree is built]

  User says: "Water the tomatoes"
         ↓
  LLM classifies intent:
  { action: "water_plant", target: "tomatoes" }
         ↓
  Code builds the tree:

  SEQUENCE
  ├── check_available          ← is the system ready?
  ├── get_plant("tomatoes")    ← fetch coordinates from API
  ├── check_plant_found        ← does this plant exist?
  ├── check_bounds(x, y, z)   ← is the location safe?
  ├── move_to(x, y, z)        ← move gantry
  ├── pump_on()               ← open water pump
  ├── wait(6 s)
  ├── pump_off()              ← close pump
  └── respond("Watering the tomatoes!")

[RIGHT HALF — three rules, large type]

  Rule 1   LLM classifies.
           Code builds.
           No hallucinated branches.

  Rule 2   Safety nodes go first — always.
           In code, not in the LLM's output.
           If any check fails, the sequence stops.

  Rule 3   User sees this tree before
           the robot moves.
```

**Speaker notes (~3 min):** Walk the left side top to bottom with the "water the tomatoes" example — it is concrete and the audience can follow every step. The key thing to stress is where the LLM stops: it returns a flat label ("water_plant") and a target ("tomatoes"). Everything below that line — the safety checks, the coordinate lookup, the pump sequence — is written by code, not by the LLM. The safety nodes (check_available, check_plant_found, check_bounds) are inserted by the tree builder unconditionally. The LLM cannot leave them out because it does not write them. Right side: three rules that follow from that design. Rule 2 is what keeps USC at zero — even when the LLM misclassifies, it produces a wrong action label, and the safety checks fail gracefully rather than sending a wrong command to the robot.

---

## Slide 5 — System architecture

```
[DIAGRAM: reproduce the three-layer architecture from the
 interim report — User Interaction Layer (left), AI Processing
 Core (centre), Robot Control Layer (right). Use the same
 colour bands: orange / teal / yellow.]

  ┌──────────────────┐ ┌─────────────────────────────────┐ ┌─────────┐
  │ USER INTERACTION │ │ AI PROCESSING CORE              │ │ ROBOT   │
  │     LAYER        │ │                                 │ │ CONTROL │
  │                  │ │  ┌──────┐  ┌──────┐  ┌──────┐  │ │ LAYER   │
  │  ┌────────────┐  │ │  │Speech│→ │Intent│→ │ BT   │  │ │         │
  │  │   Voice    │──┼─┼─►│Engine│  │Class-│  │Engine│──┼─┼──►      │
  │  │ Interface  │  │ │  │      │  │ifier │  │      │  │ │  ROBOT  │
  │  └────────────┘  │ │  └──────┘  └──────┘  └──┬───┘  │ │         │
  │                  │ │                          │      │ │         │
  │     [phone]      │ │  ┌──────────────┐    ┌───▼───┐  │ │         │
  │                  │ │  │   General    │◄───│Safety │──┼─┼──────  │
  │  ┌────────────┐  │ │  │  Assistant   │    │Valid- │  │ │         │
  │  │  Feedback  │◄─┼─┤  │ (LLM Reason) │    │ ator  │  │ │         │
  │  │  Display   │  │ │  └──────┬───────┘    └───────┘  │ │         │
  │  └────────────┘  │ │         │                       │ │         │
  │                  │ │  ┌──────▼─────────────────┐     │ │         │
  │       ◄──────────┼─┼──│ Feedback Engine        │     │ │         │
  │                  │ │  │ Text to Audio          │     │ │         │
  │                  │ │  └────────────────────────┘     │ │         │
  └──────────────────┘ └─────────────────────────────────┘ └─────────┘

  Sits on top of AURA FarmBot ROS 2 — keyboard_topic only.
  Nothing in the upstream stack changes.
```

**Speaker notes (~3 min):** Three layers. The User Interaction Layer is just a browser microphone and an audio output — no app to install, works on a phone over the local network. The AI Processing Core is everything we built: the speech engine transcribes, the intent classifier (a small on-device LLM) extracts what the user wants, the behaviour tree engine assembles a plan from a typed node library, and the safety validator gates every action against preconditions before it reaches the robot. The general assistant is an LLM reasoning node available inside the tree for knowledge questions — "when should I plant basil" — without giving the LLM any control of the actuator. The Robot Control Layer is the existing AURA FarmBot ROS 2 stack, unmodified. GrowMate publishes to `keyboard_topic`, the same topic the existing keyboard controller uses. That decision means upstream updates do not break us, and we do not break upstream.

---

## Slide 6 — Evaluation

```
[DIAGRAM: three large-number callouts, centred]

         0              96.6 %           98.8 %
    Unsafe states        DBSR              SNSR
      (USC)             28 / 29         162 / 164

  29 test utterances across 7 categories
  direct · indirect · sensor queries · knowledge ·
  multi-step · safety-critical · emergency stops

[DIAGRAM: latency bar, proportional]

  Total mean: 5,456 ms
  STT  ████░░░░░░░░░░░░░  1.1 s
  LLM  ░░░░█████████████  3.8 s  ← dominates
  BT   ░░░░░░░░░░░░░░░░█  <50 ms
  TTS  ░░░░░░░░░░░░░░░░█  0.5 s

  The one miss: LLM misclassified an indirect utterance.
  Safety checks held.   USC stayed at 0.
```

**Speaker notes (~2.5 min):** Lead with USC=0 — that is the safety claim, and for an audience in sustainable agriculture with real plants and real equipment, it is the number that matters. The one failure in DBSR was a classification error on an indirect utterance ("the herbs look dry" mapped to the wrong sensor check), but because the safety layer sits in deterministic code rather than in the LLM output, no unsafe command reached the robot. The latency is honest — 5.4 seconds is borderline. The LLM alone takes 3.8 seconds on the Pi. That is the main engineering challenge going forward.

---

## Slide 7 — Next steps

```
[DIAGRAM: simple timeline or three-column roadmap]

  NOW                   JUNE 2026             AUG 2026
  ────────────          ────────────          ────────────
  System running        Demo day              Thesis
  in simulation         Dundalk focus         submission
  on Windows            group  n=10           19 Aug

  Evaluation            SUS usability         Physical
  complete              study, elderly        deployment
  (29 utterances)       participants          on Pi

                        Ethics approval       Phone-local
                        in progress           track:
                                              small LLM
                                              on phone
                                              (< 2 s latency)
```

**Speaker notes (~2 min):** Two parallel tracks. The near-term track is the Dundalk demo on 9 June — ten older adults, three sessions, System Usability Scale plus a per-utterance log of what worked and what didn't. That is the qualitative evidence that the interface actually serves the people it is designed for. The longer-term track is the phone-local path: moving inference off the Pi and onto a phone using a smaller mobile-optimised LLM, which should bring latency below 2 seconds and remove the need for the Pi co-location entirely. That becomes a thesis chapter, not a June deliverable.

---

## Slide 8 — Thank you

```
[PHOTO: FarmBot Genesis XL in a productive garden bed —
 something that feels like the end-goal: a garden cared for.]

        GrowMate is open source.

        github.com/rishabh12j/voice-farmbot

        Rishabh Jain
        rishabh.jain.2025@mumail.ie
        Maynooth University
        Supervisor: Dr Majid Sorouri

  Questions welcome.
```

**Speaker notes:** End with the photo rather than a bullet list — the garden is what this is for. Leave repo URL on screen through Q&A. If anyone asks to see the BT builders, `ai_core.py` under `growmate_voice/` is the right file.

---

## Notes for the speaker

- **Story spine:** elderly people lose gardening → voice + robot can give it back → here is the system → here is the evidence → here is what comes next.
- **Slide 4 (behaviour tree) is the one to rehearse.** Walk the "water the tomatoes" tree left-side top to bottom before talking about the rules on the right.
- **Slide 5 (architecture) is the technical anchor.** Use the actual three-layer diagram from the interim report — it has been reviewed and the boxes are the right level of detail.
- **Slide 5 leads with USC=0.** Agricultural audience cares about safety first.
- **Time budget:** 30 s + 2.5 m + 2 m + 3 m + 3 m + 2.5 m + 2 m + 1 m ≈ 16.5 min. Comfortable in a 20-min slot, leaves real Q&A room.
- **If given 15 min:** keep all slides, trim speaker notes; do not cut slide 2 (it is the why).
- **Banned phrases:** "we propose a different approach", "the key finding is", "in conclusion", "seamlessly", "plays a crucial role".

## Production checklist

**Photos**
- [ ] Slide 1: elderly person gardening, or FarmBot in a productive bed
- [ ] Slide 7: FarmBot in a productive garden — closing image

**Diagrams** (replace ASCII with real graphics — Figma, draw.io, or Keynote shapes)
- [ ] Slide 2: three barrier bullets + three callout cards (Demographic Need / Intuitive Interaction / Research Gap)
- [ ] Slide 3: left-to-right speech → GrowMate → FarmBot flow with three example utterances
- [ ] Slide 4: two-panel — left: annotated sequence tree for "water the tomatoes"; right: three rules in large type
- [ ] Slide 5: reproduce the three-layer architecture from the interim report (User Interaction / AI Processing Core / Robot Control) — same boxes, same colour bands
- [ ] Slide 6: three large-number callouts + proportional latency bar
- [ ] Slide 7: three-column timeline (Now / June / August)

**Content**
- [ ] Confirm 21 % / 85 % statistics on slide 2 are sourced and citable
- [ ] Confirm repo URL is public before the workshop
- [ ] Backup PDF on USB stick
