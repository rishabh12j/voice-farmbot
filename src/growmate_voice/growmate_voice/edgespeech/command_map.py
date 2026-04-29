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
    (["water", "water plant", "water the plants", "start watering"],     "water"),
    (["take photo", "photo", "capture", "take picture"],                 "photo"),
]


TTS_PHRASES: Dict[str, str] = {
    "estop":   "Emergency stop activated",
    "reset":   "Emergency stop cleared",
    "home":    "Returning to home position",
    "y_plus":  "Moving forward",
    "y_minus": "Moving backward",
    "x_minus": "Moving left",
    "x_plus":  "Moving right",
    "z_plus":  "Raising the arm",
    "z_minus": "Lowering the arm",
    "water":   "Watering plants",
    "photo":   "Taking a photo",
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

    for candidate in candidates:
        for variants, action in COMMAND_MAP:
            for v in variants:
                if candidate == v or v in candidate:
                    return action, "exact"

    best_action: Optional[str] = None
    best_score = 0.0
    for candidate in candidates:
        for variants, action in COMMAND_MAP:
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
