"""Thin client for posting intents to a running Pi intent server.

Used by:

* ``growmate_voice.app`` when launched with ``--pi-url`` (web UI as phone
  stand-in during development).
* The headless eval harness (``port of evaluate_bt.py``).
* The future browser-based phone client — same wire format, different
  language; this module's JSON output is the reference shape.

This module has no py_trees / rclpy dependencies and is safe to import
on Windows.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from growmate_pi.schemas import (
    SCHEMA_VERSION,
    Intent,
    IntentRequest,
    IntentResponse,
)


log = logging.getLogger("growmate_pi.client")


# ---------- app.py action-string -> V2 Intent mapping -------------------------


def app_action_to_intent(action: str) -> Optional[Intent]:
    """Translate ``edgespeech.command_map`` action names to V2 Intent objects.

    The web app uses short action names ("home", "y_plus", "water", ...). V2
    uses richer intent names ("go_home", "move", "water", ...). This map
    bridges them. Jog directions become ``move`` intents with
    ``params={"axis": "x", "direction": +1}`` — the Pi resolves these via
    a small handler not yet shipped (the current builder only supports
    coordinate moves).

    Returns None for unhandled actions; the caller should fall back to the
    legacy local-execution path in that case.
    """
    if action == "estop":
        return Intent(action="emergency_stop", response="Emergency stop.")
    if action == "home":
        return Intent(action="go_home", response="Returning home.")
    if action == "water":
        return Intent(action="water_all", response="Watering all plants.")
    if action == "photo":
        return Intent(action="photo", response="Taking a photo.")
    if action == "light_on":
        return Intent(action="light_on", response="Lights on.")
    if action == "light_off":
        return Intent(action="light_off", response="Lights off.")
    if action in {"x_plus", "x_minus", "y_plus", "y_minus", "z_plus", "z_minus"}:
        axis, sign = action.split("_")
        return Intent(
            action="move",
            params={
                "axis": axis,
                "direction": +1 if sign == "plus" else -1,
                "step_mm": 100,
            },
            response=f"Jogging {axis}{'+' if sign == 'plus' else '-'}.",
        )
    if action == "reset":
        # Reset estop bypasses the BT — caller handles via /reset_estop
        return None
    return None


# ---------- HTTP client -------------------------------------------------------


def post_intent(
    pi_url: str,
    intents: List[Intent],
    raw_text: str = "",
    emergency: bool = False,
    client_id: str = "growmate_voice.app",
    timeout_s: float = 30.0,
) -> IntentResponse:
    """POST an IntentRequest to the Pi and return the parsed reply.

    Raises ``RuntimeError`` if httpx isn't installed or the request fails.
    """
    if httpx is None:
        raise RuntimeError("httpx is not installed; pip install httpx")

    req = IntentRequest(
        intents=intents,
        raw_text=raw_text,
        emergency=emergency,
        client_id=client_id,
        timestamp=datetime.utcnow(),
    )
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(pi_url, json=req.model_dump(mode="json"))
            r.raise_for_status()
            return IntentResponse.model_validate(r.json())
    except Exception as exc:
        raise RuntimeError(f"POST {pi_url} failed: {exc}") from exc


def post_estop(pi_base_url: str, timeout_s: float = 5.0) -> Dict:
    """Hit the Pi's ``/estop`` directly (no tree, no LLM)."""
    if httpx is None:
        raise RuntimeError("httpx is not installed")
    url = pi_base_url.rstrip("/") + "/estop"
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url)
        r.raise_for_status()
        return r.json()


def post_reset_estop(pi_base_url: str, timeout_s: float = 5.0) -> Dict:
    if httpx is None:
        raise RuntimeError("httpx is not installed")
    url = pi_base_url.rstrip("/") + "/reset_estop"
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url)
        r.raise_for_status()
        return r.json()


def ping(pi_base_url: str, timeout_s: float = 3.0) -> Optional[Dict]:
    """GET /status. Returns None on connection failure (no raise)."""
    if httpx is None:
        return None
    url = pi_base_url.rstrip("/") + "/status"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.debug("ping %s failed: %s", url, exc)
        return None
