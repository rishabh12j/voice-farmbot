"""Daily watering scheduler for GrowMate V2 — Pi-resident.

POSTs ``{"action": "water_all"}`` to the local intent server once per day at
the configured time. Same correctness properties as the legacy
``growmate_voice.scheduler``:

* Single source of truth for the schedule (``farmbot.yaml`` → ``schedule.watering_time``).
* 30-minute catch-up window — a Pi reboot within 30 min of the scheduled
  time still fires the command.
* At most once per day — restart can't double-water.

Why HTTP instead of publishing ROS2 directly?

The scheduler runs the *full* BT (safety nodes, command sequencing, history)
for free by going through ``/intent``. If we published ``P_4`` straight to
``keyboard_topic`` we'd skip the safety prefix.

Usage::

    python -m growmate_pi.scheduler                              # default
    python -m growmate_pi.scheduler --intent-url http://localhost:8000
    python -m growmate_pi.scheduler --dry-run                    # log only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

try:
    import httpx
except ImportError:  # httpx not installed yet in dev
    httpx = None  # type: ignore[assignment]


_STATE_FILE = Path.home() / ".growmate_pi" / "last_watered.txt"
_CATCHUP_MIN = 30
_CHECK_INTERVAL_S = 60
DEFAULT_INTENT_URL = "http://localhost:8000/intent"

log = logging.getLogger("growmate_pi.scheduler")


def _watering_time(config_path: Path) -> tuple[int, int]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t = str(data.get("schedule", {}).get("watering_time", "08:00")).strip()
        hh, mm = t.split(":")
        return int(hh), int(mm)
    except Exception:
        log.warning("Could not read watering_time from %s, using 08:00", config_path)
        return 8, 0


def _already_watered_today() -> bool:
    if not _STATE_FILE.exists():
        return False
    try:
        return _STATE_FILE.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def _mark_watered() -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(date.today().isoformat())


def _is_due(hour: int, minute: int) -> bool:
    now = datetime.now()
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    cutoff = scheduled - timedelta(minutes=_CATCHUP_MIN)
    return cutoff <= now and now >= scheduled


def _fire_water_all(intent_url: str, dry_run: bool) -> bool:
    """POST a water_all IntentRequest to the local Pi. Returns True on success."""
    payload = {
        "intents": [
            {
                "action": "water_all",
                "target": None,
                "params": {},
                "response": "Scheduled daily watering.",
                "question": None,
            }
        ],
        "raw_text": "(scheduled) water all plants",
        "emergency": False,
        "client_id": "scheduler",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "schema_version": "1.0.0",
    }
    if dry_run:
        log.info("DRY RUN: would POST %s", json.dumps(payload))
        return True
    if httpx is None:
        log.error("httpx not installed — cannot POST. pip install httpx")
        return False
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(intent_url, json=payload)
            r.raise_for_status()
            body = r.json()
        log.info(
            "Watering fired: status=%s commands=%s",
            body.get("status"),
            body.get("commands_published"),
        )
        return body.get("status") in ("success", "partial")
    except Exception as exc:
        log.error("Scheduler POST failed: %s", exc)
        return False


def _default_config() -> Path:
    return Path(__file__).resolve().parent / "config" / "farmbot.yaml"


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ap = argparse.ArgumentParser(description="GrowMate Pi daily watering scheduler")
    ap.add_argument("--config", default=str(_default_config()))
    ap.add_argument("--intent-url", default=DEFAULT_INTENT_URL)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be POSTed without actually firing",
    )
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    hour, minute = _watering_time(config_path)
    log.info(
        "Scheduler: will water daily at %02d:%02d (catchup %d min) via %s",
        hour, minute, _CATCHUP_MIN, args.intent_url,
    )

    try:
        while True:
            if _is_due(hour, minute) and not _already_watered_today():
                log.info("Scheduler: firing water_all intent")
                if _fire_water_all(args.intent_url, args.dry_run):
                    _mark_watered()
                else:
                    log.warning("Scheduler: fire failed — will retry next check")
            time.sleep(_CHECK_INTERVAL_S)
    except KeyboardInterrupt:
        log.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
