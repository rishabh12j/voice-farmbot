"""Process-wide task state for long-running BT executions.

A single ``TaskState`` instance lives on the Pi-side intent server. It
captures:

  * what task (if any) is currently executing on the FarmBot
  * how far through it we are (current_step / total_steps)
  * the user-visible label of the current step ("Lettuce_little_gem #12")
  * a per-process E-stop flag that long-running leaves read every tick

This is what makes Tier B honest. With it:

  - The Windows UI can poll ``/status`` at 1 Hz and render a blocking
    overlay during multi-plant waters instead of leaving the user
    staring at a frozen screen for 9 minutes.
  - The ``Wait`` BT node checks ``estop_requested`` every 100 ms, so
    pressing the stop button halts the tree within a tick instead of
    spinning out the remaining water-duration.
  - Each leaf that completes writes its own event-log row, so the log
    finally tells the truth ("plant #12 watered at ts=X") rather than
    one tree-summary row at the end.

Thread safety: every read/write is guarded by a single ``RLock``. FastAPI
serves requests on a thread pool, so /estop and the intent thread both
touch this object concurrently — without the lock the snapshot can tear
mid-update.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class TaskState:
    """Snapshot-able task tracker. One instance per intent-server process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: bool = False
        self._task_id: Optional[str] = None
        self._label: str = ""
        self._total_steps: int = 0
        self._current_step: int = 0
        self._current_label: str = ""
        self._started_ts: Optional[float] = None
        self._estop_requested: bool = False
        # We bump this each time _anything_ changes; the /status poller can
        # use it to short-circuit cheap renders when nothing's new.
        self._revision: int = 0

    # ------------------------------------------------------------------ task lifecycle

    def start(self, task_id: str, label: str, total_steps: int) -> None:
        """Mark a task as just started.

        Does NOT clear the estop_requested flag — that's the operator's
        latch, cleared only by ``/reset_estop``. If the flag is still
        set, the new task's first ``CheckEstop`` will fail anyway; we
        rely on the /intent endpoint to refuse non-emergency calls
        outright while the latch is set, so the user sees a clear
        "press reset first" rather than a half-rendered running panel
        that immediately flips back to stopped.
        """
        with self._lock:
            self._active = True
            self._task_id = task_id
            self._label = label
            self._total_steps = max(0, int(total_steps))
            self._current_step = 0
            self._current_label = ""
            self._started_ts = time.time()
            self._revision += 1

    def step(self, step_index: int, step_label: str = "") -> None:
        """Advance the visible progress. step_index is 1-based."""
        with self._lock:
            if not self._active:
                return
            self._current_step = max(0, int(step_index))
            self._current_label = step_label or ""
            self._revision += 1

    def end(self) -> None:
        """Mark the task complete (success, failure, or aborted alike)."""
        with self._lock:
            self._active = False
            self._task_id = None
            self._label = ""
            self._total_steps = 0
            self._current_step = 0
            self._current_label = ""
            self._started_ts = None
            # Deliberately keep estop_requested as-is. It's the operator's
            # latch to clear (via /reset_estop), not the task's.
            self._revision += 1

    # ------------------------------------------------------------------ estop flag

    def request_estop(self) -> None:
        with self._lock:
            self._estop_requested = True
            self._revision += 1

    def clear_estop(self) -> None:
        with self._lock:
            self._estop_requested = False
            self._revision += 1

    def estop_requested(self) -> bool:
        # Hot path for the Wait node — keep this branchless and cheap.
        return self._estop_requested

    # ------------------------------------------------------------------ readers

    def is_active(self) -> bool:
        return self._active

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-safe dict for the /status endpoint."""
        with self._lock:
            elapsed = (
                round(time.time() - self._started_ts, 2)
                if self._started_ts is not None
                else None
            )
            return {
                "task_active": self._active,
                "task_id": self._task_id,
                "task_label": self._label,
                "total_steps": self._total_steps,
                "current_step": self._current_step,
                "current_label": self._current_label,
                "elapsed_s": elapsed,
                "estop_requested": self._estop_requested,
                "revision": self._revision,
            }


# ---------- Module-level singleton -------------------------------------------

_INSTANCE: Optional[TaskState] = None


def get_task_state() -> TaskState:
    """Lazy singleton. Constructed on first call; reused thereafter."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TaskState()
    return _INSTANCE


def reset_task_state_for_tests() -> None:
    """Wipe the singleton — used by smoke tests to avoid cross-test leakage."""
    global _INSTANCE
    _INSTANCE = None
