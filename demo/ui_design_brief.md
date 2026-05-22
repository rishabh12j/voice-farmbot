# GrowMate — UI Design Brief

## Project context

**GrowMate** is a voice-controlled interface for the FarmBot Genesis XL agricultural robot, built for an MSc thesis at Maynooth University. The research contribution is a framework (working name **VoiceBT**) that restricts an on-device LLM to flat intent classification and assembles inspectable behaviour trees in deterministic code. GrowMate is the FarmBot-specific instantiation — the proof-of-concept that validates the framework.

**Target users:** Elderly and disabled adults who want to garden but find the physical labour difficult. Demo day on **9 June 2026** with the Dundalk focus-group participants. Thesis submission 19 August 2026.

**Hardware:** FarmBot Genesis XL — a cartesian gantry robot mounted over a 5.7m × 2.7m raised bed. It can move (x/y/z), water, photograph, sense soil moisture, and toggle LEDs across a 42-plant garden of tomatoes, lettuce, marigolds, scallions, peppers.

## Current architecture

```
Phone/Browser (Windows or LAN)
  ─ mic record ──► STT (Whisper) ──► AICore router
                                       │
                  ┌────────────────────┴───────────────────┐
                  │  pattern (fast, ~50ms)                  │
                  │  emergency / home / lights / jog        │
                  └────────────────────┬───────────────────┘
                  │  AICore (LLM, ~3-5s)                    │
                  │  plant-targeted, gardening questions    │
                  └────────────────────┬───────────────────┘
                                       │
                                       ▼ POST intent JSON
              Raspberry Pi (LAN, http://<pi>:8000)
                                       │
              Builds py_trees BT with safety guards
              (check_available, check_bounds, check_plant_found)
                                       │
                                       ▼
              keyboard_topic ──► FarmBot moves
```

## What's running today

The current web UI is a single-page FastAPI app (`http://localhost:7860`) with:

1. A **"Power ON" gate** that the user must click before any other control appears.
2. Two tabs:
   - **Controls** — D-pad (X/Y), Z+/-, home, water, photo, reset, e-stop
   - **Voice** — large "Tap to record" button, STT/TTS backend pickers, transcript view, BT trace, audio playback
3. **Command history** panel below the tabs — running log of every action.
4. An always-visible **emergency stop** button (red, top of every screen).

The function is solid. The design is utilitarian — dark theme, geometric, looks like a developer tool. We need it to look like something a 70-year-old would feel safe and confident using.

## What we want to build

A clean, accessible, gardening-warm web UI that fits the same FastAPI backend. Same endpoints, same flow — only the front-end HTML/CSS/JS changes.

### Core requirements

1. **Voice-first.** The mic is the primary input. It should dominate visually. A big, obvious record button — the user shouldn't hunt for it.
2. **One-screen design.** No tab switching. Mic, history, emergency stop, and the current robot state all visible at once on a tablet/phone screen.
3. **Accessibility.** Large fonts (18px minimum), high contrast, clear focus states, no decorative icons that confuse screen readers. Voice feedback after every action.
4. **Calm aesthetic.** Earth tones — greens, soft beige, warm white — not the current dark developer theme. Suggests garden, not server room.
5. **State is obvious at a glance.** The user always sees: am I connected? where is the robot? what was the last thing I said? what did it do?
6. **Emergency stop is sacred.** Always reachable, always red, never below the fold, never requires scrolling. Big enough to slam with a palm.

### Sections of the page

| Section | Purpose | Notes |
|---------|---------|-------|
| **Header** | Brand + connection status | "GrowMate" wordmark, sprout icon, small "● Connected to your garden" or "● Reconnecting…" indicator |
| **Garden state strip** | Where is the robot, what's it doing | Position (x, y, z mm), last action, last spoken response. One line, large text. |
| **Voice button** | The primary control | Massive circular mic button, centre of screen. Pulses gently while listening. Three states: idle / recording / processing. |
| **Quick actions** | Frequent non-voice commands | Big buttons: "Water everything", "Go home", "Take a photo", "Lights on/off". Each ~120×120px, friendly icon + label. |
| **Jog controls** | Manual move | Cardinal arrows for X/Y, separate Z+/- buttons. Larger than typical desktop controls. |
| **Command history** | Trust + replay | Last 5–10 actions, newest top. Each entry shows: timestamp (e.g. "2 min ago"), what was said, what happened, success/failure icon. Tap any entry to repeat. |
| **Emergency stop** | Safety | Always-visible red button. Top-right or bottom-right of every screen at all times. |
| **Settings** (collapsible) | STT/TTS backends, model | Hidden by default — current view exposes too much. Move to a gear icon dropdown. |

### Visual style cues

- **Palette:** Mossy green primary (~#4a7c59), soft cream background (~#faf6ee), warm clay accent (~#c97c5d), tomato red for emergency (~#c1392b).
- **Typography:** Rounded sans-serif (Nunito, Quicksand, Inter — large weights), avoid hairline weights.
- **Imagery:** Botanical illustrations or simple line-drawn sprouts. Avoid stock photos and 3D renders.
- **Microcopy:** Friendly, present tense, no jargon. "Watering the tomatoes…" not "Executing P_4 sequence".
- **Sound:** Subtle confirmation chime on successful action (optional, gated by user setting).

### Accessibility checklist (non-negotiable)

- [ ] All interactive elements ≥ 48×48px tap target
- [ ] WCAG AA contrast ratios on every text/background pair
- [ ] Screen-reader friendly: every button has an explicit `aria-label`
- [ ] Visible keyboard focus states
- [ ] No reliance on colour alone — icons + text everywhere
- [ ] Works offline-friendly (no spinners forever if Pi disconnects — graceful degraded mode)
- [ ] Mic permission flow has a fallback to text input ("Type instead")

### What the user might say (so the UI matches the language)

Examples the system handles:
- "Water the tomatoes" → moves gantry, waters one plant
- "Water everything" → fires the full 42-plant sequence
- "Move forward" / "Move left" → jog
- "Go home" → return to (0,0,0)
- "How are the tomatoes looking?" → moves, photographs, reports
- "When should I plant basil?" → gardening Q&A, no robot motion
- "Stop" / "Halt" / "Emergency" → immediate e-stop, bypasses everything

The UI copy and any sample-prompt hints should mirror this — phrase suggestions in the user's own language, not in command syntax.

## Technical constraints (so designs don't go off-piste)

- Static HTML + CSS + vanilla JS, served by FastAPI on port 7860. **No build step**, no React/Vue framework — the existing app inlines all assets in a single HTML response. Keep it that way.
- Mobile-first; the target device is a phone or tablet in the greenhouse, with Windows/laptop as a fallback.
- Audio: browser `MediaRecorder` captures mono PCM @ 16 kHz, POSTs WAV to `/api/voice`. The server returns JSON with a base64-encoded WAV for TTS playback.
- All control endpoints already exist: `/api/voice`, `/api/jog`, `/api/action`, `/api/estop`, `/api/reset`, `/api/status`, `/api/history`, `/api/history/clear`, `/api/farmbot/power`.

## What we want from Claude design

1. A complete `index.html` (single file, inline `<style>` and `<script>` are fine) replacing the current UI at [src/growmate_voice/growmate_voice/app.py:432-…](src/growmate_voice/growmate_voice/app.py).
2. An ASCII / wireframe mockup of the layout for sign-off before committing the HTML.
3. Two colour scheme options to choose from (palette swatches).
4. A list of all API calls the new HTML makes, so we can verify it covers every existing endpoint.

## Out of scope (don't redesign these)

- Backend API shape — endpoints, payloads, response schemas all stay
- Behaviour-tree execution and the safety contract on the Pi
- Voice pipeline (STT → router → AICore → Pi) — we just need a new face for it
- The "Power ON" gate logic — keep the concept, redesign its presentation

## Hand-off notes

If anything is ambiguous, default to the simpler, larger, more legible option. This UI will be used by people who are nervous about voice assistants and unsure whether a robot will actually do what they ask. Every visual cue should reassure, not impress.
