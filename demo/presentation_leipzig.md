# GrowMate / VoiceBT — Leipzig research talk (Draft 1)

**Audience:** Leipzig University — robotics / HRI faculty + peers
**Format:** 8 slides, ~25 min talk + ~10 min Q&A
**Speaker:** Rishabh Jain (Maynooth University, MSc Robotics & Embedded AI)
**Supervisor:** Dr Majid Sorouri

---

## Slide 1 — Title

```
VoiceBT
Inspectable Voice Control for Agricultural Robots
through LLM-Constrained Behaviour Trees

Rishabh Jain  ·  Maynooth University
Supervisor: Dr Majid Sorouri
[venue, date]

  github.com/rishabh12j/voice-farmbot
```

**Speaker notes (~30 s):** Frame the talk: a framework for **using on-device LLMs in safety-critical robot control without giving the LLM the steering wheel**. The instantiation is a FarmBot for elderly users; the contribution is the framework. Acknowledge MU + supervisor up front.

---

## Slide 2 — Why this problem, why now

```
Two pressures, one gap:

· Edge LLMs are now small enough to live on a Pi
  or a phone (Gemma 3n, Phi-3.5, Qwen 2.5 ≤ 4 B params)

· Agricultural robots increasingly target untrained
  users — gardening as accessible activity for the
  elderly and the disabled

  → tempting to glue an LLM to a robot end-to-end

  → but: LLMs hallucinate, robots break things or people

We need the natural-language UX without the trust gap.
```

**Speaker notes (~2 min):** Concrete framing: there's a real adoption story here that has nothing to do with research papers — voice control of personal-scale robots is one of the few HRI scenarios that's about to be both technically feasible and commercially salient at once. The unsolved part is what guarantees you give the user. State the audience: 9 June 2026 demo with Dundalk focus group, n=10 elderly. Honest moment: I don't know yet whether elders will *trust* the inspectable-tree feedback any more than they'd trust a black box; we'll measure that.

---

## Slide 3 — Related work, brief

```
Voice → action pipelines
  · Alexa-style end-to-end intent: opaque, no recourse
  · Tool-calling LLMs (ReAct / function-calling):
    structure leaks into the LLM, hard to verify

Behaviour trees in robotics
  · Colledanchise & Ögren 2017 — modular, inspectable,
    well-suited to discrete action sets
  · Gugliermo et al. 2024 — evaluation framework
    (DBSR / SNSR / USC) used here

Edge LLMs
  · LiteRT-LM, MediaPipe Tasks, llama.cpp; Gemma 3n
    benchmarks on phone-class hardware

Gap: a framework that uses an LLM where it shines
(intent recognition over noisy speech) without
letting it generate the *structure* that drives
safety-critical actuators.
```

**Speaker notes (~3 min):** Don't dwell on each citation — the audience knows BTs and LLMs. Spend time on the *gap*. Make explicit why neither extreme works: end-to-end LLM means no audit trail; tool-calling means the LLM still chooses the action graph and you're back to verifying its output. Mention Gugliermo et al. specifically because the eval metrics come from there.

---

## Slide 4 — VoiceBT, the framework

```
   audio
     ↓
   STT  (faster-whisper / Moonshine / Vosk)
     ↓
   ┌──────────────────── LLM ─────────────────────┐
   │  Flat intent classifier — JSON only           │
   │  {action ∈ FIXED_SET, target, response}       │
   │  No nested structure.  No tree generation.    │
   └──────────────────────────────────────────────┘
     ↓
   ┌────────── deterministic Python ──────────────┐
   │  intent → tree (safety prefix → action       │
   │  → respond), looked up by action name        │
   └──────────────────────────────────────────────┘
     ↓
   BT executor — robot_action / fn_call / reason
     ↓
   ROS2 publisher → keyboard_topic
     ↓
   TTS  (Piper / Kokoro)

Claim 1: LLM never produces tree structure → no
         hallucinated branches.
Claim 2: Every robot action is preceded by safety
         conditions in code → bounded blast radius.
Claim 3: User sees the tree before it executes →
         transparency for the human-in-the-loop.
```

**Speaker notes (~4 min):** The single most important slide. Walk the diagram top to bottom. The "no nested structure" line is the contribution — early-version code had the LLM emit JSON trees and on-device 2-4B models produced valid JSON ~0% of the time. Switching to *flat* intent + deterministic templating is what made this reliable. Stress that "safety prefix" is non-negotiable: every move-to is preceded by `check_available`, `check_bounds`, `check_plant_found` — not because the LLM might forget to ask, but because the LLM doesn't get to put them there in the first place.

---

## Slide 5 — Implementation: GrowMate on FarmBot Genesis XL

```
Hardware:    FarmBot Genesis XL  (5691 × 2734 × 380 mm)
             Raspberry Pi 5 (4 GB) — co-located on the bot

Models:      Gemma 3:4b           — Ollama, on Pi
             faster-whisper tiny.en — CTranslate2 int8
             Piper en_US-lessac-medium — ONNX
             Kokoro 82M (optional, dev box)

Stack:       AURA FarmBot ROS 2 (upstream, read-only)
             + growmate_voice ROS 2 package (this work)
             + FastAPI web UI (browser mic, no native app)

Topic discipline: keyboard_topic only — drop-in
             replacement for AURA's keyboard_controller,
             so the rest of the stack doesn't change.
```

**Speaker notes (~2.5 min):** Note the architectural decision: this is *not* a fork of FarmBot. It's a sibling node that publishes the same command strings the existing keyboard controller emits. That makes regression risk minimal and lets the upstream maintainer ignore us. Mention the FastAPI UI replaces an earlier Gradio version; we ran into BrotliMiddleware/pathsend bugs in Gradio 6 that broke browser audio uploads. Brief moment of uncertainty: I'm still unsure whether the Pi will hit thermal limits during the 30-min demo with all three robots running concurrently — measuring this week.

---

## Slide 6 — Evaluation: 29-utterance corpus + Gugliermo metrics

```
Evaluation corpus
  29 hand-authored utterances covering:
    · single robot actions ("water the lettuce")
    · multi-intent       ("water the tomatoes
                           and then go home")
    · indirect speech    ("the herbs look thirsty")
    · knowledge queries  ("when should I plant basil")
    · safety triggers    ("stop", "halt")

Metrics (Gugliermo et al. 2024)
  DBSR   Desired Behaviour Success Rate   96.6 %  (28/29)
  SNSR   Single Node Success Rate         98.8 %  (162/164)
  USC    Unsafe State Count                  0
  Latency mean                          5,456 ms
         (STT 1.1 s · LLM 3.8 s · BT < 50 ms · TTS 0.5 s)

Forthcoming (9 Jun 2026, Dundalk)
  System Usability Scale, n=10 elderly users,
  3 groups × 30 min, semi-structured per-utterance log
```

**Speaker notes (~3 min):** Don't restate numbers — let them sit on the slide. Talk to the *interesting* failure modes: the one DBSR miss was an indirect-speech utterance the LLM mapped to a wrong (but bounded) action — and *because the safety prefix held*, USC stayed at zero. That's the framework working as designed: the LLM made a recoverable mistake, not an unsafe one. The single SNSR miss was a transient FarmBot REST timeout, unrelated to the framework. The Dundalk study isn't the headline number — it's the qualitative material that goes into the paper's discussion section.

---

## Slide 7 — Limitations + the phone-local track

```
Honest gaps as of today:

· No session memory — "no, the herbs" after watering
  the tomatoes is treated as a new utterance
· No WiFi heartbeat / deadman — if the Pi loses
  network mid-action, only the next safety check
  catches it
· LLM latency dominates — 3.8 s on a Pi 5; user
  studies need TTFA < 2 s to feel responsive
· n=10 user study limits statistical claims to
  qualitative + descriptive

Phone-local track (in progress)
  · LiteRT-LM + Gemma 3n E2B int4 on Pixel 8
  · Same flat-intent prompt, same tree builder
    (ported to Kotlin, mechanical)
  · Pi side becomes a 30-line MQTT/HTTP shim
  · Goal: TTFT measurements by mid-July, not a
    deployed product
```

**Speaker notes (~3 min):** Limitations stay unresolved on this slide — don't pair each one with a fix. The phone-local track is the strongest answer to "what's next" but it's *future work*, not a deliverable. State the deadline split: 9 June demo runs on the Pi; phone-local prototype is a thesis chapter, not a demo artefact. Genuine moment of uncertainty: I don't yet know whether the LiteRT-LM stack will hold up the prefill assumptions Google publishes once we throw a 1500-token system prompt at it.

---

## Slide 8 — What we'd like from this room

```
Questions worth your time:

· Have you seen voice-control deployments where
  the LLM was kept this far from the actuator?
  What pitfalls did you hit?

· The 29-utterance corpus is small. What evaluation
  shape would you find more convincing?

· For the Dundalk study, is SUS the right instrument
  for elderly HRI, or should we add NASA-TLX /
  trust-in-automation scales?

· If the framework ports cleanly to a phone, what's
  the next domain you'd take it to?

Thank you.

  Rishabh Jain · rishabh.jain.2025@mumail.ie
  github.com/rishabh12j/voice-farmbot
```

**Speaker notes:** End on questions back to the room rather than a summary slide — turns the Q&A into a conversation rather than an interrogation. The repo URL is real; if anyone wants to skim the code afterwards they should be able to find the BT builders in `src/growmate_voice/growmate_voice/ai_core.py`.

---

## Notes for the speaker

- **Total time budget:** 30 s + 2 m + 3 m + 4 m + 2.5 m + 3 m + 3 m + 1.5 m ≈ 19.5 min. Leaves 10 min for Q&A in a 30-min slot, 5 min for slack in a 25-min slot.
- **Slide 4 is the one to rehearse.** Everything else is window dressing on the diagram.
- **Bring a backup PDF on a USB stick.** Conference projectors often refuse Markdown-rendered HTML.
- **If you're given a 15-min slot:** drop slide 3 (related work) and compress slide 7 to two bullets.
- **If you're given a 45-min slot:** add a live demo between slides 5 and 6 (not before, the audience needs the framework picture first); budget 6-8 minutes for it.
- **Banned phrases the talk avoids:** "we propose a different approach", "the key finding is", "in conclusion", "seamlessly", "plays a crucial role".

## Production checklist

- [ ] Replace `[venue, date]` on slide 1
- [ ] Verify the architecture diagram renders cleanly when exported (ASCII-art tends to fall apart in PowerPoint — replace with a real diagram before the talk)
- [ ] If presenting before the Dundalk study, soften slide 6's "Forthcoming" block to "Planned"
- [ ] Pre-register the `[venue, date]` slide title with the host so it appears on their schedule correctly
- [ ] Decide once: "VoiceBT framework" vs "GrowMate" — the framework is the contribution, GrowMate is one instantiation; keep that hierarchy consistent across all 8 slides
