"""GrowMate voice command map — variants, FarmBot emissions, TTS phrases, matcher.

Each entry maps one or more spoken variants to:
  * ``action``     — internal key used by the app handler
  * ``emit``       — the FarmBot command string(s) to publish on keyboard_topic
                     (may be None for actions handled server-side such as jog)
  * ``tts``        — what the confirmation TTS should say
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple


COMMAND_MAP: List[Tuple[List[str], str]] = [
    (["stop", "emergency", "halt", "freeze", "abort", "emergency stop"], "estop"),
    (["reset", "resume", "clear stop", "clear", "reset stop"],           "reset"),
    (["home", "go home", "return home"],                                 "home"),
    (["forward", "ahead", "y plus", "y+", "move forward"],               "y_plus"),
    (["back", "backward", "backwards", "y minus", "y-", "move back"],    "y_minus"),
    (["left", "x minus", "x-", "move left", "go left"],                  "x_minus"),
    (["right", "x plus", "x+", "move right", "go right"],                "x_plus"),
    (["up", "z plus", "z+", "raise", "arm up", "lift"],                  "z_plus"),
    (["down", "z minus", "z-", "lower", "arm down", "drop"],             "z_minus"),
    (["water", "water plant", "water plants", "water the plants",
      "water all", "water all the plants", "start watering"],            "water"),
    (["take photo", "photo", "capture", "take picture"],                 "photo"),
    # Lights were only in TTS_PHRASES/_EMIT_FRIENDLY — no pattern variants at
    # all, so even "turn on the lights" fell through to the LLM. Filler
    # stripping drops "the", so "turn on the lights" -> "turn on lights".
    (["lights on", "light on", "turn on lights", "turn on the lights",
      "turn the lights on", "switch on the lights", "lights up"],        "light_on"),
    (["lights off", "light off", "turn off lights", "turn off the lights",
      "turn the lights off", "switch off the lights", "lights out"],     "light_off"),
]


# Actions whose variants must match **exactly** (after fillers/normalisation),
# never via substring or fuzzy fallback. Without this rule, "water the basil"
# matches the bare "water" variant via substring → the action becomes
# water_all (P_4) and the soft-confirm gate fires on the wrong target.
# Same trap for "when did i water the marigold" → also lands on water_all.
STRICT_MATCH_ACTIONS = {"water", "photo"}


TTS_PHRASES: Dict[str, str] = {
    # Day 6: friendlier, present-tense phrasing for elderly users.
    # Spoken back via TTS after every successful action so silence
    # never lasts long.
    "estop":     "Stopped. The robot is halted.",
    "reset":     "All clear. Ready to go again.",
    "home":      "Heading home.",
    "y_plus":    "Moving forward.",
    "y_minus":   "Moving back.",
    "x_minus":   "Moving left.",
    "x_plus":    "Moving right.",
    "z_plus":    "Lifting the arm up.",
    "z_minus":   "Lowering the arm.",
    "water":     "Watering all the plants.",
    "photo":     "Taking a photo for you.",
    "light_on":  "Lights on.",
    "light_off": "Lights off.",
}


NORMALISE: Dict[str, str] = {
    "why plus":  "y plus",
    "why minus": "y minus",
    "why+":      "y+",
    "why-":      "y-",
    "ex plus":   "x plus",
    "ex minus":  "x minus",
    "ex+":       "x+",
    "ex-":       "x-",
    "zee plus":  "z plus",
    "zee minus": "z minus",
    "zed plus":  "z plus",
    "zed minus": "z minus",
    "go forward": "forward",
    "go back":    "back",
    "go backward": "back",
    "emergency stop": "stop",
    "emergency halt": "halt",
}


_FILLERS = {
    "hey", "hi", "ok", "okay", "please", "could", "would", "you", "can",
    "growmate", "grow", "mate", "farmbot", "farm", "bot", "the", "a", "an",
    "now", "just", "kindly",
}


ALL_VARIANTS: List[str] = sorted({v for variants, _ in COMMAND_MAP for v in variants})


def _strip_fillers(text: str) -> str:
    tokens = [t for t in text.split() if t not in _FILLERS]
    return " ".join(tokens)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_command(transcript: str) -> Tuple[Optional[str], str]:
    """Returns (action_key or None, confidence_label).

    Confidence label is one of ``exact``, ``fuzzy``, ``none``.
    """
    if not transcript:
        return None, "none"

    text = transcript.strip().lower()
    text = re.sub(r"[^\w\s+\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for src, tgt in NORMALISE.items():
        if src in text:
            text = text.replace(src, tgt)

    stripped = _strip_fillers(text)
    candidates = [c for c in (text, stripped) if c]

    # Pass 1 — exact equality, all actions.
    for candidate in candidates:
        for variants, action in COMMAND_MAP:
            for v in variants:
                if candidate == v:
                    return action, "exact"

    # Pass 2 — substring match, but skip actions in STRICT_MATCH_ACTIONS so
    # "water the basil" doesn't get classified as bare "water" via "water" in
    # candidate. Those phrasings fall through to the LLM, which handles the
    # target plant properly.
    for candidate in candidates:
        for variants, action in COMMAND_MAP:
            if action in STRICT_MATCH_ACTIONS:
                continue
            for v in variants:
                if v in candidate:
                    return action, "exact"

    # Pass 3 — fuzzy similarity, also skipping the strict actions.
    best_action: Optional[str] = None
    best_score = 0.0
    for candidate in candidates:
        for variants, action in COMMAND_MAP:
            if action in STRICT_MATCH_ACTIONS:
                continue
            for v in variants:
                score = _similarity(candidate, v)
                if score > best_score:
                    best_score = score
                    best_action = action

    if best_score >= 0.70:
        return best_action, "fuzzy"
    return None, "none"


def get_tts_phrase(action: Optional[str]) -> str:
    if action is None:
        return "Command not recognised, please repeat"
    return TTS_PHRASES.get(action, "Command confirmed")
