"""Bridge to the GrowMate LLM + behaviour-tree pipeline.

Provides one function — ``classify_and_render(text)`` — that returns the
LLM-derived behaviour tree as JSON + ASCII for display in the workbench.

Two sources, tried in order:

  1. **Local package copy** (``growmate_voice.ai_core`` / ``bt_engine``) —
     the thesis research modules that now live in this package. This is the
     default so edits-in-place show up immediately.
  2. **Fallback to ``growmate-bt/`` source tree** — the standalone checkout
     at ``<repo>/growmate-bt/``, for cases where the local copy has been
     removed or the user wants to A/B against the upstream thesis code.

Loading is lazy and cached. If neither source is importable, the bridge
reports the error and the workbench still works for STT/TTS experiments.

Efficiency notes:
  * AICore + BTEngine are instantiated once per process.
  * The tree is *constructed only* — never executed. That skips ROS2 publish,
    FarmBot REST calls, LLM reasoning, and wait blocks, keeping the
    round-trip bounded by Ollama's single classify call.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ─── locate possible sources ────────────────────────────────────────────────
# bt_bridge.py lives at:
#   …/voice-farmbot/src/growmate_voice/growmate_voice/bt_bridge.py
# parents[0] = growmate_voice/      parents[1] = growmate_voice/ (package)
# parents[2] = src/                 parents[3] = voice-farmbot/
_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parents[1]                          # …/src/growmate_voice/growmate_voice/
_VOICE_FARMBOT_ROOT = _HERE.parents[3]                # …/voice-farmbot/

# Config paths — first existing wins.
_CANDIDATE_CONFIGS = [
    _HERE.parents[2] / "config" / "farmbot.yaml",     # src/growmate_voice/config/farmbot.yaml
    _VOICE_FARMBOT_ROOT / "growmate-bt" / "config" / "farmbot.yaml",
]

_GROWMATE_BT_SRC = _VOICE_FARMBOT_ROOT / "growmate-bt"


_lock = threading.Lock()
_state: Dict[str, Any] = {
    "loaded": False,
    "available": False,
    "source": "",              # "local" | "growmate-bt" | ""
    "config_path": "",
    "ai": None,
    "engine": None,
    "error": "",
}


def _resolve_config() -> Optional[Path]:
    for p in _CANDIDATE_CONFIGS:
        if p.exists():
            return p
    return None


def _ensure_loaded(model: str = "gemma3:4b") -> Dict[str, Any]:
    with _lock:
        if _state["loaded"]:
            return _state

        _state["loaded"] = True

        config_path = _resolve_config()
        if config_path is None:
            _state["error"] = (
                "farmbot.yaml not found. Looked in: "
                + " ; ".join(str(p) for p in _CANDIDATE_CONFIGS)
            )
            return _state
        _state["config_path"] = str(config_path)

        # --- 1. local package first ----------------------------------------
        AICore = None
        BTEngine = None
        try:
            from .ai_core import AICore as _AICore
            from .bt_engine import BTEngine as _BTEngine
            AICore, BTEngine = _AICore, _BTEngine
            _state["source"] = "local"
        except Exception as exc_local:  # noqa: BLE001
            local_err = f"local import failed: {exc_local}"
            # --- 2. fall back to growmate-bt/ -------------------------------
            if _GROWMATE_BT_SRC.exists():
                if str(_GROWMATE_BT_SRC) not in sys.path:
                    sys.path.insert(0, str(_GROWMATE_BT_SRC))
                try:
                    from growmate.ai_core import AICore as _AICore  # type: ignore
                    from growmate.bt_engine import BTEngine as _BTEngine  # type: ignore
                    AICore, BTEngine = _AICore, _BTEngine
                    _state["source"] = "growmate-bt"
                except Exception as exc_bt:  # noqa: BLE001
                    _state["error"] = (
                        f"{local_err}; growmate-bt fallback also failed: {exc_bt}"
                    )
                    return _state
            else:
                _state["error"] = (
                    f"{local_err}; no growmate-bt fallback at {_GROWMATE_BT_SRC}"
                )
                return _state

        try:
            ai = AICore(config_path=str(config_path), model=model)
        except Exception as exc:  # noqa: BLE001
            _state["error"] = f"AICore init failed: {exc}"
            return _state

        engine = BTEngine(ai.garden, robot_controller=None)
        _state["ai"] = ai
        _state["engine"] = engine
        _state["available"] = True
        return _state


def status(model: str = "gemma3:4b") -> Dict[str, Any]:
    s = _ensure_loaded(model)
    ai = s["ai"]
    return {
        "source": s["source"] or "(none)",
        "config_path": s["config_path"],
        "loaded": s["loaded"],
        "available": s["available"],
        "error": s["error"],
        "ollama_reachable": bool(ai and ai.is_available()) if ai else False,
        "model": model,
        "candidate_configs": [str(p) for p in _CANDIDATE_CONFIGS],
    }


def classify_and_render(
    text: str,
    model: str = "gemma3:4b",
) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Run AICore.construct_tree and render the result.

    Returns ``(tree_json, tree_ascii, error)``.
    On any failure returns ``(None, "", error_message)``.
    """
    s = _ensure_loaded(model)
    if not s["available"]:
        return None, "", s["error"] or "BT pipeline not available"

    ai = s["ai"]
    engine = s["engine"]

    if not ai.is_available():
        return None, "", (
            f"Ollama not reachable for model '{model}'. "
            f"Start it with `ollama serve` and `ollama pull {model}`."
        )

    if not text.strip():
        return None, "", "empty transcript"

    try:
        tree = ai.construct_tree(text)
    except Exception as exc:  # noqa: BLE001
        return None, "", f"construct_tree failed: {exc}"

    try:
        ascii_view = engine._describe_tree(tree)
    except Exception as exc:  # noqa: BLE001
        ascii_view = f"(render failed: {exc})"

    return tree, ascii_view, ""


def extract_robot_commands(tree: Dict[str, Any]) -> list[str]:
    out: list[str] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "robot_action":
            name = node.get("name", "?")
            params = node.get("params") or {}
            if params:
                out.append(f"{name}({params})")
            else:
                out.append(name)
        for child in node.get("children", []) or []:
            _walk(child)

    _walk(tree)
    return out


def extract_responses(tree: Dict[str, Any]) -> list[str]:
    out: list[str] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "respond":
            msg = node.get("message", "")
            if msg:
                out.append(msg)
        for child in node.get("children", []) or []:
            _walk(child)

    _walk(tree)
    return out


def extract_node_summary(tree: Dict[str, Any]) -> Dict[str, int]:
    """Count node types in the tree — useful for paper-style tree metrics."""
    counts: Dict[str, int] = {}

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        t = node.get("type", "?")
        counts[t] = counts.get(t, 0) + 1
        for child in node.get("children", []) or []:
            _walk(child)

    _walk(tree)
    return counts
