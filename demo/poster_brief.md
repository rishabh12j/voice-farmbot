# GrowMate — A1 landscape poster brief (v2)

For Claude design. Goal: a single **A1 landscape** poster, printed and
stood at a demo / open day, that lets a general-public passer-by
understand GrowMate in 15–60 seconds.

This is **v2** of the brief — the first attempt produced a competent but
emotionally flat "corporate two-column landing page" with a dark left
rail of mostly empty green and the hero illustration tucked into the
bottom corner. That doesn't work for this audience. This version is
prescriptive about the layout because the layout is the problem.

---

## Format

- **Size**: A1 **landscape**, 841 mm wide × 594 mm tall
- **Bleed**: 3 mm on all sides
- **Print**: 300 dpi
- **Reading distance**: 1–2 m
- **Minimum body font**: 24 pt. Section headings 36 pt+. The main wordmark 120 pt+.
- **No QR code anywhere on this poster.**
- **No URLs, no email addresses, no social handles.** Credit lines only.

Output: a print-ready `index.html` at A1 landscape with `@page { size: A1
landscape; }` so the user can export to PDF directly from a browser.

---

## What changed since v1 (read this before designing)

The previous version produced this structure (rejected):

```
┌──────────────────────────┬───────────────────────────────────────┐
│                          │  How it works (3 cards stacked top-R) │
│   GrowMate               │                                         │
│   A garden you can       ├───────────────────────────────────────┤
│   talk to.               │  Try saying (6 chips in 3×2 grid)    │
│                          │                              ┌──────┐ │
│   [intro paragraph]      │                              │SAFETY│ │
│                          │                              └──────┘ │
│                          ├───────────────────────────────────────┤
│   [small hero in corner] │  Credit + logo                          │
└──────────────────────────┴───────────────────────────────────────┘
```

**What's wrong with that:**

1. The dark green left column is **40% of the poster's surface area
   doing almost nothing**. It just holds the title and a small
   illustration. That's a waste of premium space.
2. The hero illustration is shrunk into the bottom-left corner. It
   should be **the largest, most prominent element on the page** — it's
   what makes a passer-by stop walking.
3. "How it works" sits in three floating cards at top-right, visually
   disconnected from the hero. The reader's eye doesn't link "talk →
   robot does it" because they're not near each other.
4. The whole thing reads like a SaaS landing page. The brand should
   read **calm, hand-made, garden-warm** — not "Series A startup pitch".
5. The safety badge is small and tucked into the corner. It should
   feel reassuring and present, not hidden.

---

## What we want instead — the layout

**The hero illustration is the whole poster.** Everything else floats
around it. Picture an A1-landscape canvas with a single warm cream
background. The FarmBot gantry and a friendly older gardener (seated /
standing with a stick / in a chair) occupy the centre-bottom 60% of the
canvas. Speech bubbles from the gardener and visual cues from the robot
do the explaining.

Suggested wireframe (proportional, not pixel-accurate):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  🌱 GrowMate                                              "Try saying…"      │
│  A garden you can talk to.                                                   │
│  For people who love gardening but find        ┌─────────────────────────┐  │
│  bending, lifting and remembering hard.        │ "Water the tomatoes."   │  │
│                                                │ "Take a photo of the    │  │
│                                                │  lettuce."              │  │
│                                                │ "Go home."              │  │
│             ┌───────────────────────┐          │ "When should I plant    │  │
│             │   "Water the          │          │  basil?"                │  │
│             │    tomatoes please."  │          │ "What's the weather     │  │
│             └──────────┬────────────┘          │  like today?"           │  │
│                        ▼                       │ "Stop."                 │  │
│         ┌─────────────────────────────┐        └─────────────────────────┘  │
│         │                              │                                     │
│         │  [BIG hero illustration:     │       ┌────────────────────────┐   │
│         │   raised bed, FarmBot        │       │  🛑  Big red button.   │   │
│         │   gantry, gardener in        │       │      Or just say       │   │
│         │   a chair gesturing,         │       │      "stop". The       │   │
│         │   plants visible in the bed] │       │      robot cannot      │   │
│         │                              │       │      leave its bed.    │   │
│         └─────────────────────────────┘       └────────────────────────┘   │
│                                                                              │
│   1. You say what you want.   2. It repeats back what it heard.   3. It     │
│      "Water the basil."          "Water the basil — yes?"            does    │
│                                                                       it.    │
│  ───────────────────────────────────────────────────────────────────────    │
│  A postgraduate research project at Maynooth University.                     │
│  Rishabh Jain · Supervisor Dr Majid Sorouri · MSc Robotics and Embedded AI. │
│  Garden tested today: 54 plants — tomatoes, lettuce, basil, marigolds.       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Six placement rules (non-negotiable)

1. **Hero illustration covers ~40% of the poster's area**, centred and
   slightly left of middle. It is the first thing the reader sees.
2. **Wordmark and tagline sit top-left**, above the hero, on the cream
   background — not in a dark coloured panel. No background rail.
3. **"Try saying…" sample phrases hover to the right of the
   illustration**, presented as quote-bubble chips that visually feel
   like they could be more of the same kind of thing the cartoon
   gardener might say. Not as a 3×2 grid of business buttons.
4. **Safety reassurance sits below the chips** as a single warm-toned
   card — clay-bordered, with a real red stop-button icon and three
   plain sentences. Visible without being alarming.
5. **"How it works" is a thin three-step strip across the bottom**
   (not boxed cards) — numbered 1, 2, 3 with one sentence each, with
   thin connecting lines or arrows so the eye reads left-to-right.
6. **Credit lives in a single line at the very bottom**, smaller than
   everything else but still ≥18 pt. No box. No logo cluster.

---

## The hero illustration — describe this to your illustration tool

A hand-drawn, line-art-with-flat-colour scene in the GrowMate palette:

- A **raised wooden bed** (the FarmBot Genesis XL — 5.7 m × 2.7 m
  rectangular bed) seen at a gentle three-quarter angle, not straight-on
- The **FarmBot gantry** is the rectangular metal frame straddling the
  bed with two rails and a cross-beam carrying a small toolhead. Make
  it look like a friendly piece of farm machinery, not a sci-fi robot.
  No glowing LEDs, no antennae, no humanoid face.
- **Plants visible** in the bed: tomatoes (red dots on green), lettuce
  (small green rosettes), marigolds (yellow flowers). Maybe 12–15
  plants shown — enough to suggest a real garden, not so many that
  detail is lost.
- A **person**: an older adult (60s–70s), seated in a chair or holding a
  stick, beside the bed but not bending into it. Welcoming posture,
  open hand gesture toward a plant. Could be any gender; ideally
  ambiguous so different viewers can see themselves.
- A **speech bubble** from the person reading *"Water the tomatoes
  please."* Round corners, friendly typography.
- The **robot's response cue**: subtle. Maybe a small thought bubble
  from the toolhead with a checkmark, or the toolhead drawn moving
  toward the tomato bed with a small motion line.
- Sun, slight foliage at the edges, warm light. Mid-morning feel.

Style references: think Norman Rockwell warmth meets a modern flat
botanical illustration. **Not** a polished isometric robot diagram.
**Not** stock-photo realism. **Not** a children's storybook.

---

## Voice and tone

Calm, warm, plain. Not corporate, not cute.

Use:
> *"You don't need to know how it works. You just talk to it."*

> *"It always tells you what it heard before it does anything."*

> *"There's a button. There's also the word 'stop.' Both work."*

Don't use:
- AI, LLM, machine learning, framework, intent classifier, behaviour
  tree, pipeline, latency, deployment, edge, ROS, voice assistant,
  smart home, IoT
- Exclamation marks (one for "Stop!" is fine, nowhere else)
- Emojis (one or two purposeful icons are fine — stop button, sprout)
- "Empower," "leverage," "revolutionise," "seamless"

---

## Palette and typography

- **Cream background**: `#faf6ee` everywhere — no dark panels, no
  splits, no coloured rails
- **Moss green (primary)**: `#4a7c59` for wordmark, headings, line art
- **Moss green deep**: `#355a40` for accents and underlines
- **Warm clay (accent)**: `#c97c5d` for the safety card border, the
  numbers 1-2-3, and small highlights
- **Tomato red (safety only)**: `#c1392b` — used **only** on the
  emergency-stop icon and the word "Stop"
- **Ink for body text**: `#2b2a26`

Typography:
- One typeface throughout. Suggested: **Nunito** at 400/600/800
- Wordmark "GrowMate" in 800, very large (≥ 120 pt)
- Tagline in 600 italic-friendly weight, large (≥ 64 pt)
- Headings in 800
- Body in 400, 24 pt minimum, line height 1.45
- Sample phrase chips in 600, 28 pt+, in quote marks

---

## Concrete content — the exact words to use

### Title block (top-left)

> **GrowMate**
>
> *A garden you can talk to.*
>
> For people who love gardening but find bending, lifting and remembering hard.

### "Try saying…" chips (six, in this order)

1. *"Water the tomatoes."*
2. *"Take a photo of the lettuce."*
3. *"Go home."*
4. *"When should I plant basil?"*
5. *"What's the weather like today?"*
6. *"Stop."*

Above the chips: heading **TRY SAYING…** in moss green, 28 pt, letter-spaced.

Below the chips, in 20 pt regular: *Use your own words — it understands
plain English.*

### Safety card

Heading: **Always one press from stopping.**

Three sentences as a small list:

- *A big red button on the side stops everything at once.*
- *You can also just say the word "stop."*
- *The robot can never move outside its bed.*

Use the clay border, the red stop icon, and the same cream background.

### "How it works" strip (bottom, three columns)

Numbered 1, 2, 3 in clay-coloured circles, large enough to read at a glance:

1. **You say what you want.**
   *"Water the basil."*

2. **It tells you what it heard.**
   *"Water the basil — yes?"* You can correct it before anything moves.

3. **It does the gardening.**
   The robot rolls to the right plant and waters, photographs or checks it.

Each step takes one column. Thin moss-green arrows between them.

### Credit line (very bottom)

> *A postgraduate research project at Maynooth University. Rishabh Jain ·
> Supervisor Dr Majid Sorouri · MSc Robotics and Embedded AI. Today's
> test garden: 54 plants including tomatoes, lettuce, basil and
> marigolds. Supported by the Higher Education Authority's Technological
> Sector Advancement Fund.*

One single line, smaller text (18 pt), centred. No logo cluster, no
horizontal rule above it (use white space as the separator).

---

## What to deliver

1. **An ASCII or rough-box wireframe sketch first** so we can sign off
   on the layout before the visual polish.
2. **Final A1 landscape `index.html`** with embedded CSS, optimised for
   print export (one page exactly, no overflow).
3. **A black-and-white version** of the same file for accessibility /
   photocopy contingency.
4. **The hero illustration as a standalone SVG** so we can reuse it
   elsewhere (presentations, web).

---

## Definition of done — one sentence

If a passer-by glances at the poster, sees the warm scene of a person
relaxing in their garden while a small machine quietly waters a tomato
plant for them, and walks away able to tell a friend *"It's a robot for
a raised-bed garden that you just talk to. There's a big red stop
button. It's a Maynooth research project for older folks who can't bend
so easily,"* — the poster has worked.

If they think *"oh, another AI demo,"* — it hasn't.
