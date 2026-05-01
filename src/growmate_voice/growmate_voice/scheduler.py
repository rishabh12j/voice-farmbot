"""Daily watering scheduler for GrowMate.

Fires P_4 (water all plants) once per day at the configured time.
Publishes to keyboard_topic so the full AURA pipeline picks it up.

Three fixes over the upstream autonomous_controller.py:
  1. Publishes to keyboard_topic (not /input_topic — wrong topic).
  2. 30-minute catch-up window — Pi reboot within 30 min of the scheduled
     time still fires the command.
  3. Fires at most once per day — process restart won't double-water.

Edit the time in farmbot.yaml under schedule.watering_time (HH:MM).
Default is 08:00.

Usage::

    python -m growmate_voice.scheduler           # production
    python -m growmate_voice.scheduler --no-ros2 # test without robot
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from .logger import log
from .ros2_publisher import ROS2Publisher


_STATE_FILE = Path.home() / ".growmate_voice" / "last_watered.txt"
_CATCHUP_MIN = 30
_CHECK_INTERVAL_S = 60


def _watering_time(config_path: Path) -> tuple[int, int]:
    """Read HH:MM from farmbot.yaml schedule.watering_time, default 08:00."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t = str(data.get("schedule", {}).get("watering_time", "08:00")).strip()
        hh, mm = t.split(":")
        return int(hh), int(mm)
    except Exception:  # noqa: BLE001
        log.warning("Scheduler: could not read watering_time — using 08:00")
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
    return cutoff <= now >= scheduled


def _default_config() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "farmbot.yaml"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GrowMate daily watering scheduler")
    ap.add_argument("--no-ros2", action="store_true",
                    help="simulation — print command instead of publishing")
    ap.add_argument("--config", default=str(_default_config()))
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    hour, minute = _watering_time(config_path)
    log.info("Scheduler: will water daily at %02d:%02d (catchup %d min)",
             hour, minute, _CATCHUP_MIN)

    publisher = ROS2Publisher(ros2_enabled=not args.no_ros2)

    try:
        while True:
            if _is_due(hour, minute) and not _already_watered_today():
                log.info("Scheduler: firing P_4 (water all plants)")
                publisher.execute(["P_4"])
                _mark_watered()
                log.info("Scheduler: done — will water again tomorrow at %02d:%02d",
                         hour, minute)
            time.sleep(_CHECK_INTERVAL_S)
    except KeyboardInterrupt:
        log.info("Scheduler stopped")
    finally:
        publisher.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
