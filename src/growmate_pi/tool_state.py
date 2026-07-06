"""Process-wide record of which tool head is currently mounted.

The FarmBot UTM can only report *that* a tool is seated (pin 63), not *which*
one — so we track the mounted tool in software: set it when a ``MountTool``
verifies, clear it on ``UnmountTool``. ``EnsureTool``/``CheckToolMounted`` read
it so an operation can swap to the right head only when needed (sensor ↔
nozzle ↔ seeder ↔ weeder) instead of blindly re-mounting every time.

One instance per intent-server process; guarded by a lock since the BT
executor thread and HTTP handlers may both touch it.
"""

from __future__ import annotations

import threading
from typing import Optional


# Sentinel for "pin 63 says a head is seated, but this process doesn't know
# which" — the state after an intent-server restart with a tool still on the
# UTM (audit F5). Tool-requiring trees refuse until an operator resolves it
# (hand-unmount, or POST /tool_state with the actual name).
UNKNOWN_TOOL = "unknown"


class ToolState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[str] = None

    def set(self, tool_name: str) -> None:
        with self._lock:
            self._current = tool_name or None

    def clear(self) -> None:
        with self._lock:
            self._current = None

    def mark_unknown(self) -> None:
        """Record that *some* head is physically mounted but unidentified."""
        with self._lock:
            self._current = UNKNOWN_TOOL

    def is_unknown(self) -> bool:
        with self._lock:
            return self._current == UNKNOWN_TOOL

    def current(self) -> Optional[str]:
        with self._lock:
            return self._current

    def is_mounted(self, tool_name: str) -> bool:
        with self._lock:
            return self._current == tool_name


_INSTANCE: Optional[ToolState] = None


def get_tool_state() -> ToolState:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ToolState()
    return _INSTANCE


def reset_tool_state_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None
