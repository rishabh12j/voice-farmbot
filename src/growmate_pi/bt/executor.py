"""Tick a py_trees tree to completion and return a ``TreeResult``.

The executor:

1. Resets the blackboard for the keys we care about (no cross-request leak).
2. Calls ``setup_with_descendants()`` so every node is initialised.
3. Ticks at a fixed cadence until the root reaches SUCCESS or FAILURE, or
   the per-tree timeout elapses.
4. Walks the tree post-hoc to build a flat ``List[NodeResult]`` for the
   response and reads ``tts_text`` off the blackboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

import py_trees

from growmate_pi.schemas import NodeResult, TreeResult


TICK_HZ = 10.0
# Tier B: 30-minute ceiling. Long enough for a full-bed water sequence
# (18 scallions x 30 s = 9 min, doubled for safety + jog time) while
# still being a backstop against a genuinely runaway tree. Estop is the
# fast path; this timeout is the slow guardrail.
DEFAULT_TIMEOUT_S = 1800.0


# Blackboard keys this executor clears between requests so state doesn't leak.
_RESET_KEYS = ("plant_data", "sensor_result", "tts_text", "bridge_ready")


def _reset_blackboard() -> None:
    bb = py_trees.blackboard.Blackboard
    for k in _RESET_KEYS:
        try:
            bb.unset(k)
        except KeyError:
            pass


def _py_trees_status_to_str(s) -> str:
    return {
        py_trees.common.Status.SUCCESS: "success",
        py_trees.common.Status.FAILURE: "failure",
        py_trees.common.Status.RUNNING: "running",
        py_trees.common.Status.INVALID: "skipped",
    }.get(s, "skipped")


def _walk_for_results(node) -> List[NodeResult]:
    """Flatten the tree into a list of NodeResult, in tick order."""
    results: List[NodeResult] = []

    def visit(n):
        # Leaves (no children) become NodeResult entries; composites are
        # implicit in the labels of their children.
        if not getattr(n, "children", None):
            results.append(
                NodeResult(
                    name=n.name,
                    status=_py_trees_status_to_str(n.status),
                    message=getattr(n, "feedback_message", "") or None,
                )
            )
            return
        for c in n.children:
            visit(c)

    visit(node)
    return results


@dataclass
class ExecutionContext:
    """Carries the result-aggregation state across the tick loop."""

    started_at: float
    timeout_s: float


def execute_tree(
    root: py_trees.behaviour.Behaviour,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    tick_hz: float = TICK_HZ,
) -> TreeResult:
    """Tick ``root`` to completion. Returns a TreeResult regardless of outcome.

    Args:
        root: A py_trees tree (any Behaviour or composite).
        timeout_s: Hard ceiling for the whole tree. If exceeded the root is
            terminated and the result is marked ``partial``.
        tick_hz: Tick frequency. 10 Hz matches the EE650 bt_runner default.
    """
    _reset_blackboard()
    root.setup_with_descendants()

    period = 1.0 / max(tick_hz, 1.0)
    ctx = ExecutionContext(started_at=time.monotonic(), timeout_s=timeout_s)

    while True:
        root.tick_once()
        status = root.status

        if status in (py_trees.common.Status.SUCCESS, py_trees.common.Status.FAILURE):
            break

        elapsed = time.monotonic() - ctx.started_at
        if elapsed >= ctx.timeout_s:
            try:
                # Tell the tree it's being cancelled so RUNNING nodes clean up.
                root.stop(py_trees.common.Status.INVALID)
            except Exception:
                pass
            return _result_from_root(root, partial=True)

        time.sleep(period)

    return _result_from_root(root, partial=False)


def _result_from_root(
    root: py_trees.behaviour.Behaviour, partial: bool
) -> TreeResult:
    status_str = _py_trees_status_to_str(root.status)
    if partial:
        status_str = "partial"
    elif status_str == "running":
        status_str = "partial"
    return TreeResult(
        label=root.name,
        status=status_str,  # type: ignore[arg-type]
        node_results=_walk_for_results(root),
    )


def read_tts_text() -> str:
    """Read the accumulated TTS text after a tree finishes."""
    bb = py_trees.blackboard.Blackboard
    try:
        return str(bb.get("tts_text"))
    except KeyError:
        return ""
