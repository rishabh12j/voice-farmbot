"""Persistent command history for GrowMate.

Every command (button, voice, direct API) appends one JSON record per line to
``~/.growmate_voice/history.jsonl``. The in-memory list is the authoritative
view at runtime; the file is replayed on startup so history survives restarts.

Record schema (all fields always present)::

    {
      "ts":          "2026-04-22T14:03:11.042Z",
      "source":      "button" | "voice" | "api",
      "action":      "x_plus" | "estop" | ... | null,
      "emitted":     ["M 100 0 0"],
      "status":      "sent" | "simulated" | "error",
      "transcript":  "move right",          # voice only
      "confidence":  "exact" | "fuzzy" | "",
      "position":    {"x": 100, "y": 0, "z": 0},
      "note":        "human-readable summary"
    }
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


HISTORY_PATH = Path.home() / ".growmate_voice" / "history.jsonl"
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

_MAX_IN_MEMORY = 500


@dataclass
class HistoryEntry:
    ts: str
    source: str
    action: Optional[str]
    emitted: List[str]
    status: str
    position: Dict[str, int]
    note: str = ""
    transcript: str = ""
    confidence: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class History:
    def __init__(self, path: Path = HISTORY_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._entries: List[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        self._entries.append(HistoryEntry(**raw))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            pass
        self._entries = self._entries[-_MAX_IN_MEMORY:]

    def append(
        self,
        *,
        source: str,
        action: Optional[str],
        emitted: List[str],
        status: str,
        position: Dict[str, int],
        note: str = "",
        transcript: str = "",
        confidence: str = "",
    ) -> HistoryEntry:
        entry = HistoryEntry(
            ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            source=source,
            action=action,
            emitted=list(emitted),
            status=status,
            position=dict(position),
            note=note,
            transcript=transcript,
            confidence=confidence,
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > _MAX_IN_MEMORY:
                self._entries = self._entries[-_MAX_IN_MEMORY:]
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(entry.to_json() + "\n")
            except OSError:
                pass
        return entry

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            tail = self._entries[-limit:] if limit > 0 else list(self._entries)
        return [asdict(e) for e in reversed(tail)]

    def clear(self) -> int:
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
        return removed
