"""Daily watering scheduler — one greenhouse (this Pi's local intent server).

Each FarmBot/greenhouse runs its own Pi, so the scheduler is per-greenhouse:
it talks to the *local* intent server. It's just a daily trigger for watering —
once per day at the scheduled time it POSTs ``[water_all, go_home]`` to
``/intent`` (``water_all`` is the BT-safe path to P_4 — waters every plant its
configured amount, logged honestly via the tick-and-verify gate; ``go_home`` is
H_0 afterwards), then records that it watered today so a restart can't
double-water.

Correctness:
* single source for the time (``farmbot.yaml`` → ``schedule.watering_time``);
* 30-minute catch-up window so a reboot near the time still fires;
* at most once per day.

Usage::

    python -m growmate_pi.scheduler                      # daily, local server
    python -m growmate_pi.scheduler --intent-url http://localhost:8000
    python -m growmate_pi.scheduler --now                # water now, once
    python -m growmate_pi.scheduler --dry-run            # show the plan only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Tuple

import yaml

from growmate_pi.pi_client import post_intent
from growmate_pi.schemas import Intent

_STATE_FILE = Path.home() / ".growmate_pi" / "last_watered.txt"
_CATCHUP_MIN = 30
_CHECK_INTERVAL_S = 60
DEFAULT_BASE_URL = "http://localhost:8000"

log = logging.getLogger("growmate_pi.scheduler")


def _watering_time(config_path: Path) -> Tuple[int, int]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t = str(data.get("schedule", {}).get("watering_time", "08:00")).strip()
        hh, mm = t.split(":")
        return int(hh), int(mm)
    except Exception:
        log.warning("Could not read watering_time from %s, using 08:00", config_path)
        return 8, 0


def _watered_today() -> bool:
    try:
        return _STATE_FILE.exists() and _STATE_FILE.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def _mark_watered() -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(date.today().isoformat())


def _is_due(hour: int, minute: int) -> bool:
    """Due only within the catch-up window *after* the scheduled time:
    [scheduled, scheduled + _CATCHUP_MIN]. So a reboot a few minutes late still
    fires, but launching hours later does NOT trigger a surprise watering."""
    now = datetime.now()
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window_end = scheduled + timedelta(minutes=_CATCHUP_MIN)
    return scheduled <= now <= window_end


def _fire(base_url: str, dry_run: bool) -> bool:
    """Water all plants then go home (the BT-safe path to P_4). True on ok."""
    if dry_run:
        log.info("DRY RUN: would water_all then go_home via %s/intent", base_url)
        return True

    try:
        reply = post_intent(
            f"{base_url}/intent",
            [
                Intent(action="water_all", response="Scheduled daily watering."),
                Intent(action="go_home", response="Watering done, heading home."),
            ],
            raw_text="(scheduled) water all then home",
            client_id="scheduler",
            wait_for_completion=True,   # block until the whole run finishes
            overall_timeout_s=3600.0,
        )
    except Exception as exc:
        log.error("Watering POST failed: %s", exc)
        return False

    ok = reply.status in ("success", "partial")
    log.info("Watering %s (status=%s, %d commands)",
             "ok" if ok else "FAILED", reply.status,
             len(reply.commands_published or []))
    return ok


def _default_config() -> Path:
    return Path(__file__).resolve().parent / "config" / "farmbot.yaml"


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="GrowMate daily watering scheduler (one greenhouse)")
    ap.add_argument("--intent-url", default=DEFAULT_BASE_URL,
                    help="this greenhouse's intent-server base URL "
                         "(default http://localhost:8000)")
    ap.add_argument("--config", default=str(_default_config()),
                    help="farmbot.yaml for schedule.watering_time")
    ap.add_argument("--now", action="store_true",
                    help="water immediately, once, then exit (ignores schedule + once-per-day)")
    ap.add_argument("--dry-run", action="store_true",
                    help="log the plan (water needed) without watering")
    args = ap.parse_args(argv)

    base_url = args.intent_url.rstrip("/")
    if base_url.endswith("/intent"):
        base_url = base_url[: -len("/intent")]
    hour, minute = _watering_time(Path(args.config))

    if args.now:
        log.info("--now: watering immediately via %s", base_url)
        _fire(base_url, args.dry_run)
        return 0

    log.info("Watering daily at %02d:%02d (catch-up %d min) via %s",
             hour, minute, _CATCHUP_MIN, base_url)
    try:
        while True:
            if _is_due(hour, minute) and not _watered_today():
                log.info("Scheduled watering run starting")
                if _fire(base_url, args.dry_run):
                    if not args.dry_run:
                        _mark_watered()
                else:
                    log.warning("Fire failed — will retry next check")
            time.sleep(_CHECK_INTERVAL_S)
    except KeyboardInterrupt:
        log.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
