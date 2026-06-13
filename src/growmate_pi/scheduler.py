"""Daily watering scheduler — one greenhouse (this Pi's local intent server).

Each FarmBot/greenhouse runs its own Pi, so the scheduler is per-greenhouse:
it talks to the *local* intent server. Once per day at the scheduled time it:

1. reads this greenhouse's map (``GET /plants``) and works out the water needed
   today — the sum of per-plant ``water_quantity`` (seconds);
2. waters every plant their configured amount and sends the gantry home, by
   POSTing ``[water_all, go_home]`` to ``/intent`` (``water_all`` applies the
   per-plant amounts — the BT-safe path to P_4 — and is logged honestly via the
   tick-and-verify gate; ``go_home`` is H_0 afterwards);
3. records that it watered today so a restart can't double-water.

Correctness:
* single source for the time (``farmbot.yaml`` → ``schedule.watering_time``);
* 30-minute catch-up so a reboot near the time still fires;
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
from typing import Dict, Optional, Tuple

import yaml

try:
    import httpx
except ImportError:  # safe to import without httpx (dry-run/tests)
    httpx = None  # type: ignore[assignment]

from growmate_pi.pi_client import post_intent
from growmate_pi.schemas import Intent

_STATE_FILE = Path.home() / ".growmate_pi" / "last_watered.txt"
_CATCHUP_MIN = 30
_CHECK_INTERVAL_S = 60
_DEFAULT_WATER_Q = 6  # seconds, if a plant row has no water_quantity
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
    now = datetime.now()
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    cutoff = scheduled - timedelta(minutes=_CATCHUP_MIN)
    return cutoff <= now and now >= scheduled


def _water_needed(base_url: str) -> Optional[Dict]:
    """GET /plants and total the per-plant water_quantity. None on failure."""
    if httpx is None:
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{base_url}/plants")
            r.raise_for_status()
            body = r.json()
    except Exception as exc:
        log.error("Couldn't read /plants from %s: %s", base_url, exc)
        return None
    plants = body.get("plants") or []
    total = 0.0
    for p in plants:
        try:
            total += float(p.get("water_quantity") or _DEFAULT_WATER_Q)
        except (TypeError, ValueError):
            total += _DEFAULT_WATER_Q
    return {"count": len(plants), "total_seconds": round(total, 1),
            "source": body.get("source")}


def _fire(base_url: str, dry_run: bool) -> bool:
    """Water all plants (their configured amounts) then go home. True on ok."""
    need = _water_needed(base_url)
    if need is not None:
        log.info("%d plants, ~%.0fs of water today (map: %s)",
                 need["count"], need["total_seconds"], need.get("source"))
        if need["count"] == 0:
            log.info("No plants in the map — skipping")
            return True
    else:
        log.warning("Couldn't read the map; watering anyway")

    if dry_run:
        log.info("DRY RUN: would water_all then go_home via %s/intent", base_url)
        return True
    if httpx is None:
        log.error("httpx not installed — cannot fire. pip install httpx")
        return False

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
