"""Per-plant care event log — append-only SQLite store.

Day 7 of the elderly-UX sprint: every plant-touching BT execution writes a
row here so the system can later answer "when did I last water the
tomatoes?" or "which plants need attention?".

Schema is intentionally narrow — one ``events`` table, two indexes. Any
extra detail goes in ``payload_json``. That keeps queries fast and lets
us evolve what we record without migrations.

Design notes
------------

* **Append-only by convention.** ``EventLog`` exposes only ``log``, ``recent``,
  ``for_plant`` and a ``prune`` helper. No update / delete API surface.
* **Single writer, single reader.** The intent server is the only thing
  touching this DB; we still enable WAL mode so a future stats endpoint
  could read concurrently without blocking writes.
* **Auto-creates directory + schema on first use.** No setup ritual.
* **Thread-safe via a per-instance lock.** FastAPI may dispatch handlers
  on different threads (especially via ``run_in_threadpool``).
* **Path default**: ``~/.growmate_pi/events.db`` — same family as the
  scheduler's ``last_watered.txt`` so it's discoverable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = Path.home() / ".growmate_pi" / "events.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,        -- unix epoch ms
    plant_index  INTEGER,                 -- null for non-plant events
    plant_name   TEXT,                    -- denormalised for readability
    event_type   TEXT NOT NULL,           -- watered / sensed / photographed / ...
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_plant ON events(plant_index);
CREATE INDEX IF NOT EXISTS idx_ts    ON events(ts);
CREATE INDEX IF NOT EXISTS idx_type  ON events(event_type);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    payload_raw = row["payload_json"] or "{}"
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {"raw": payload_raw}
    return {
        "id":          row["id"],
        "ts":          row["ts"],
        "plant_index": row["plant_index"],
        "plant_name":  row["plant_name"],
        "event_type":  row["event_type"],
        "payload":     payload,
    }


class EventLog:
    """Append-only SQLite-backed plant care event log.

    Args:
        db_path: where to put the database file. Parent dir is created on
            first use. Pass an in-memory path (``":memory:"``) for tests.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self._path = Path(db_path) if db_path != ":memory:" else None
        self._lock = threading.RLock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                isolation_level=None,   # autocommit; we batch via tx-on-demand
            )
            # WAL gives us concurrent reads without blocking writes.
            self._conn.execute("PRAGMA journal_mode = WAL")
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False,
                                         isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ----- write -------------------------------------------------------

    def log(
        self,
        event_type: str,
        plant_index: Optional[int] = None,
        plant_name: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[int] = None,
    ) -> int:
        """Insert one event row. Returns the new row id."""
        if not event_type:
            raise ValueError("event_type is required")
        ts = ts if ts is not None else _now_ms()
        payload_json = json.dumps(payload or {}, default=str, separators=(",", ":"))
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, plant_index, plant_name, event_type, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, plant_index, plant_name, event_type, payload_json),
            )
            return cur.lastrowid

    # ----- read --------------------------------------------------------

    def recent(self, limit: int = 50,
               event_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Last ``limit`` events, most recent first. Optionally filter by type."""
        sql = "SELECT * FROM events"
        params: List[Any] = []
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" WHERE event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def for_plant(
        self,
        plant: int | str,
        limit: int = 50,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Events for one plant, by index (int) or by case-insensitive name (str)."""
        sql = "SELECT * FROM events WHERE "
        params: List[Any] = []
        if isinstance(plant, int):
            sql += "plant_index = ?"
            params.append(plant)
        else:
            sql += "LOWER(plant_name) = LOWER(?)"
            params.append(str(plant))
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def last_for_plant(self, plant: int | str,
                       event_type: str) -> Optional[Dict[str, Any]]:
        """Most recent ``event_type`` event for one plant, or None."""
        rows = self.for_plant(plant, limit=1, event_types=[event_type])
        return rows[0] if rows else None

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ----- housekeeping ------------------------------------------------

    def prune(self, older_than_days: int = 90) -> int:
        """Delete events older than ``older_than_days``. Returns rows removed."""
        cutoff = _now_ms() - older_than_days * 24 * 60 * 60 * 1000
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


__all__ = ["EventLog", "DEFAULT_DB_PATH"]
