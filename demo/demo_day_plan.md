# GrowMate — Demo Day Plan

**Date:** 9 June 2026
**Venue:** Dundalk focus group
**Audience:** 10 elders, split into 3 groups (3-4 per group)
**Per-group budget:** 30 minutes
**Researcher present:** Rishabh + 1 facilitator (recommended)

The plan optimises for *meaningful contact time per elder*, not feature coverage.
We are testing whether a voice-controlled FarmBot is **safe**, **understandable**,
and **usable** for an older adult — not whether it can do everything.

---

## Per-group run of show (30 min)

| Min   | Activity                                | Driver       | Notes |
|-------|-----------------------------------------|--------------|-------|
| 0–3   | Welcome + ethics reminder; hand out questionnaires (don't fill yet) | Facilitator | Set expectations: "this is research, things may go wrong, that's useful for us" |
| 3–7   | What is FarmBot? Why voice? (1 page, large print, on table) | Rishabh | Photo of bot + 4 bullets. Show the emergency stop FIRST |
| 7–10  | Researcher live demo: 2 utterances ("take a photo", "go home") | Rishabh | Talk through what the screen shows: tree, robot commands, spoken reply |
| 10–22 | **Hands-on** — each elder tries 2 commands from the script | Each elder, facilitator coaching | 3 min × 4 elders = 12 min. Researcher records per-utterance log |
| 22–28 | Questionnaire — read aloud if needed, assist with writing | Facilitator | SUS (10 items) + 5 custom + 2 open |
| 28–30 | Quick Q&A, thank, small token (e.g. seed packet) | Both | Reinforce: their answers shape the next version |

**If a group runs short:** swap "knowledge question" demo in at minute 20.
**If a group runs long:** drop the second utterance per elder; keep the questionnaire intact (it's the research output).

---

## Presentation — 4 talking pages, printed handout

Use **18-point sans-serif minimum**. Print and place on the table; the screen is for the demo, not the slides.

### Page 1 — What is GrowMate?

- Photo of FarmBot Genesis XL with caption "the robot"
- Photo of laptop with the GrowMate UI
- One sentence: **"A garden robot that listens to your voice and shows you what it's about to do, before it does it."**
- Maynooth University + supervisor name in the footer

### Page 2 — What we'd like from you today

- "Try talking to the robot — there are no wrong answers."
- "Tell us when something feels confusing or worrying."
- "Fill in a short questionnaire at the end (we'll help)."
- "You can stop or step away at any time."

### Page 3 — Safety first

- Big red **EMERGENCY STOP** image, with caption "press this if anything looks wrong"
- "The robot will not move outside its bed."
- "It will ask you to confirm before doing anything to a whole garden."

### Page 4 — What happens with your answers

- "Anonymised — no names attached to data."
- "Used in a Maynooth MSc thesis and one academic paper."
- "You can withdraw at any time before [date]."
- Contact email + supervisor name.

> Bring 12 printed sets (10 + 2 spare). One set per group is fine — they share.

---

## Demo script — utterances graded easy → harder

Researcher says the utterance once. Elder repeats. The expected behaviour is in
brackets. **Always say "stop" works** — model that first if anyone is hesitant.

### Tier 1 — must-work (use these for the first try)

1. **"Take a photo"** → tree shows `photo` action, screen flashes confirmation
2. **"Go home"** → tree shows `go_home`, gantry returns to 0,0,0 (sim or real)
3. **"Stop"** → emergency stop, every other action is dropped

### Tier 2 — named target (use for second try)

4. **"Water the tomatoes"** → tree shows safety-checks → move → water → respond
5. **"Move to the herbs"** → tree shows safety-checks → move → respond

### Tier 3 — showpiece (researcher demos at minute 7-10; not for elders)

6. **"Water the lettuce and then go home"** — multi-intent; renders a long sequence
7. **"When should I plant basil?"** — knowledge path; shows the LLM-reasoning node

> **Why this order:** elders gain confidence on tier 1 (binary verbs they already use), then attempt tier 2 (substitutable noun). Tier 3 is researcher-driven because the recovery cost is high if the LLM stumbles in front of the participant.

For each utterance the researcher logs (on paper, transcribed later):

| Field | Example |
|-------|---------|
| Participant ID | P03 |
| Utterance attempted | "water the tomatoes" |
| What was transcribed | "water the tomato" |
| Action taken | water (tomatoes) |
| Success? | Y / partial / N |
| Time-to-first-action (s) | 5.4 |
| Recovery on failure | researcher repeated → success |
| Notable participant comment | "I expected it to confirm before watering" |

---

## Pre-day checklist (run morning-of)

### 24h before

- [ ] Laptop fully charged + spare charger
- [ ] Confirm Ollama is the right model: `ollama list` shows `gemma3:4b`
- [ ] Pre-warm all models: run one classify and one whisper transcribe, so first-on-the-day call isn't 8 s
- [ ] Print 12 questionnaires + 12 consent forms
- [ ] Print 1 set of 4 presentation pages per group (3 sets total)
- [ ] Prepare 10 small thank-you tokens (seed packets, biscuits, etc.)

### On-site, 15 min before first group

- [ ] Plug in laptop
- [ ] Mic test — both built-in and USB; pick whichever has lower noise floor
- [ ] Open `http://localhost:7860` in a fresh browser window (NOT https; mic permission is localhost-only)
- [ ] Run one full pipeline ("take a photo") to warm caches
- [ ] Set the laptop screen brightness for the room
- [ ] Quiet the room as much as possible (mic picks up everything)
- [ ] Start audio capture if doing per-utterance recordings (consent must be already collected for this)

### Failure modes — what to do if…

| If… | Do this |
|-----|---------|
| Mic gives no audio | Switch to manual jog tab; log in field notes "voice unavailable for P_xx" |
| Ollama times out | Restart `ollama serve`; fall back to flat-mode (main app) for the rest of the slot |
| LLM produces nonsense tree | Acknowledge it, click EMERGENCY STOP, move to next utterance |
| Elder freezes / overwhelmed | Pause, offer the manual buttons, ask them to point instead of speak |
| Battery dies | Plug in. If no power, finish the questionnaire (it's the research output) |

---

## Data capture

- **Per utterance:** WAV file already saved by the workbench/main app to `~/.growmate_voice/`. Copy these to `demo_recordings/<participant_id>/` after each group.
- **Pipeline trace:** already in `~/.growmate_voice/history.jsonl`. Tag each line by participant ID before the next group runs (so traces don't merge across groups).
- **Questionnaires:** scan/photograph all 10 sheets at the end of the day; transcribe into one CSV the same evening.
- **Field notes:** physical notebook for unstructured observations (body language, hesitations, surprised reactions). These often become the most quoted bits in the paper.

---

## What we are measuring (so the questionnaire makes sense)

Map each questionnaire item back to a research claim. If a claim has no item, drop the claim or add an item.

| Claim | Item(s) on questionnaire | Validated instrument |
|-------|--------------------------|----------------------|
| The system is usable by older adults | SUS items 1-10 | SUS (Brooke 1996) |
| Users feel safe | Custom Q1 ("felt confident the robot would stop") | — |
| Users prefer voice over manual | Custom Q2 ("preferred talking to pressing buttons") | — |
| The system tolerates memory lapses (a thesis-level concern raised earlier) | Custom Q3 ("if I forgot what I said, the robot still helped me") | — |
| Users would adopt it | Custom Q4 ("would use this in my own garden") | adapted from TAM (Davis 1989) |
| The transparent-tree feedback is reassuring | Custom Q5 ("I understood what the robot was about to do") | — |
| Per-utterance objective success | Researcher log (not a questionnaire item) | DBSR-style metric, paper-aligned |

The numerical SUS gives a single score (0–100) comparable to other elderly-tech studies. The custom items + open questions are where the *thesis story* gets told.

---

## After all 3 groups

- [ ] Aggregate SUS scores → mean, SD, min/max (n=10)
- [ ] Cross-tab Custom Q1–Q5 with per-utterance success log
- [ ] Pull 4-6 illustrative open-ended quotes for the paper
- [ ] Back up everything to a second drive *before* leaving the venue
- [ ] Email participants a one-page summary of findings ~2 weeks later (good ethics practice, helps with future studies)

---

## Risks the supervisor will ask about

| Risk | Mitigation |
|------|------------|
| Tiny n=10 → no statistical power | Pre-announce: this is a *qualitative usability study*, not an effect-size study. SUS gives a comparable benchmark; the open-ended responses do the heavy lifting. |
| Researcher bias (Rishabh runs the demo and the questionnaire) | Have the facilitator collect questionnaires; researcher leaves the room while elders fill them in |
| Memory-loss concern under-tested | One questionnaire item is dedicated to it (Q3); consider following up 1-2 elders for a 30-min remote interview a week later |
| First-time-with-voice-tech confound | Demographics item asks about Alexa/Siri use → split SUS scores by familiarity in the writeup |
