"""GrowMate scheduler — failproof daily command runner for the FarmBot.

Failure modes addressed:

  1. Atomic state writes — temp-file rename prevents JSON corruption on
     power loss, eliminating double-fires after reboot.
  2. Pre-flight bringup check — verifies the MapController node is alive
     on keyboard_topic before firing; skips (without marking fired) if not.
  3. keyboard_topic subscriber check — confirms at least one subscriber
     is listening before publishing; logs a warning if not.
  4. Conflict detection — warns if the upstream autonomous_controller
     (which publishes to the wrong topic) is detected running.
  5. Idempotent per day with catch-up window — each slot fires at most
     once per calendar day; Pi reboot within the window still fires.
  6. Timezone-aware time — uses local clock regardless of Pi locale.
  7. --status CLI flag — prints state, next slot, time-to-fire,
     and bringup liveness so you can confirm it's working.
  8. --force flag — fires a named slot immediately for testing.
  9. Systemd unit generation — --install-service writes the unit file.

Schedule in farmbot.yaml::

    schedule:
      - time: "08:00"
        command: "P_4"
        label: "Morning watering"
      - time: "20:00"
        command: "H_0"
        label: "Return home for the night"

Run::

    python -m growmate_voice.scheduler --status
    python -m growmate_voice.scheduler --list
    python -m growmate_voice.scheduler --force "08:00:P_4"
    python -m growmate_voice.scheduler                      # production
    python -m growmate_voice.scheduler --no-ros2 --dry-run  # dev/Windows
    python -m growmate_voice.scheduler --install-service    # systemd setup
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .history import History
from .logger import log
from .ros2_publisher import ROS2Publisher


_STATE_PATH = Path.home() / ".growmate_voice" / "scheduler_state.json"
_DEFAULT_CATCHUP_MIN = 30
_DEFAULT_INTERVAL_S = 30
_BRINGUP_CHECK_NODES = ["MapController", "FarmbotController", "DeviceCmdHandler"]

# Commands that activate the water pump — substituted in --no-water test mode.
_WATER_COMMANDS = {"P_4", "P_5", "D_W_1"}
_WATER_SUBSTITUTE = "I_1"  # take a photo — safe, visible, confirms gantry + camera work


# ─── schedule entry ────────────────────────────────────────────────────────
@dataclass
class Slot:
    time_str: str
    command: str
    label: str
    hour: int = 0
    minute: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Slot":
        t = str(d.get("time") or "").strip()
        try:
            hh, mm = t.split(":")
        except ValueError:
            raise ValueError(f"time must be HH:MM, got '{t}'")
        return cls(
            time_str=t,
            command=str(d.get("command") or "").strip(),
            label=str(d.get("label") or d.get("command") or t),
            hour=int(hh),
            minute=int(mm),
        )

    def slot_id(self) -> str:
        return f"{self.time_str}:{self.command}"

    def next_fire(self, from_dt: datetime) -> datetime:
        """Return the next wall-clock datetime this slot is due."""
        candidate = from_dt.replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        if candidate < from_dt:
            candidate += timedelta(days=1)
        return candidate


def _load_schedule(config_path: Path) -> List[Slot]:
    if not config_path.exists():
        log.warning("Scheduler: config not found at %s", config_path)
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    slots: List[Slot] = []
    for i, entry in enumerate(data.get("schedule") or []):
        try:
            slots.append(Slot.from_dict(entry))
        except Exception as exc:  # noqa: BLE001
            log.error("Scheduler: bad schedule entry #%d (%r): %s", i, entry, exc)
    slots.sort(key=lambda s: (s.hour, s.minute))
    return slots


# ─── atomic state ─────────────────────────────────────────────────────────
def _load_state() -> Dict[str, Any]:
    if not _STATE_PATH.exists():
        return {"date": "", "fired": []}
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {"date": "", "fired": []}
    except Exception:  # noqa: BLE001
        log.warning("Scheduler: state file corrupt — resetting")
        return {"date": "", "fired": []}


def _save_state(state: Dict[str, Any]) -> None:
    """Atomic write: write to tmp, rename — safe against power-loss corruption."""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_STATE_PATH.parent, prefix=".sched_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, _STATE_PATH)
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _roll_if_new_day(state: Dict[str, Any]) -> Dict[str, Any]:
    today = date.today().isoformat()
    if state.get("date") != today:
        log.info("Scheduler: new day %s — clearing fired list", today)
        state = {"date": today, "fired": []}
        _save_state(state)
    return state


# ─── pre-flight checks ────────────────────────────────────────────────────
def _bringup_alive(ros2_enabled: bool) -> bool:
    """Return True if the FarmBot bringup nodes appear to be running."""
    if not ros2_enabled:
        return True  # simulation — skip the check
    try:
        result = subprocess.run(
            ["ros2", "node", "list"],
            capture_output=True, text=True, timeout=4,
        )
        nodes = result.stdout.lower()
        return any(n.lower() in nodes for n in _BRINGUP_CHECK_NODES)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _keyboard_topic_has_subscribers(ros2_enabled: bool) -> bool:
    """Return True if keyboard_topic has at least one subscriber."""
    if not ros2_enabled:
        return True
    try:
        result = subprocess.run(
            ["ros2", "topic", "info", "/keyboard_topic"],
            capture_output=True, text=True, timeout=4,
        )
        for line in result.stdout.splitlines():
            if "subscription count" in line.lower():
                count = int("".join(c for c in line if c.isdigit()) or "0")
                return count > 0
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return False


def _autonomous_controller_running() -> bool:
    """Warn if the upstream autonomous_controller (wrong topic) is running."""
    try:
        result = subprocess.run(
            ["ros2", "node", "list"],
            capture_output=True, text=True, timeout=4,
        )
        return "command_sender" in result.stdout.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ─── core tick ────────────────────────────────────────────────────────────
def _due_slots(slots: List[Slot], now: datetime, catchup_min: int) -> List[Slot]:
    cutoff = now - timedelta(minutes=catchup_min)
    out = []
    for s in slots:
        slot_dt = now.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
        if cutoff <= slot_dt <= now:
            out.append(s)
    return out


def _tick(
    slots: List[Slot],
    publisher: ROS2Publisher,
    history: History,
    state: Dict[str, Any],
    catchup_min: int,
    dry_run: bool,
    ros2_enabled: bool,
    no_water: bool = False,
) -> None:
    state = _roll_if_new_day(state)
    fired = set(state.get("fired") or [])
    now = datetime.now()

    due = _due_slots(slots, now, catchup_min)
    if not due:
        return

    # Pre-flight: bringup alive?
    if not _bringup_alive(ros2_enabled):
        log.warning(
            "Scheduler: %d slot(s) due but FarmBot bringup is not running — "
            "will retry next tick (slots NOT marked fired)", len(due)
        )
        return

    # Pre-flight: keyboard_topic subscribed?
    if ros2_enabled and not _keyboard_topic_has_subscribers(ros2_enabled):
        log.warning(
            "Scheduler: keyboard_topic has no subscribers — "
            "bringup may still be starting up, will retry"
        )
        return

    # Conflict check
    if _autonomous_controller_running():
        log.warning(
            "Scheduler: upstream autonomous_controller is running alongside this "
            "scheduler — commands may interleave on /input_topic. "
            "Kill it with: pkill -f autonomous_controller"
        )

    for slot in due:
        sid = slot.slot_id()
        if sid in fired:
            continue

        # Substitute water commands in test mode
        actual_cmd = slot.command
        if no_water and actual_cmd in _WATER_COMMANDS:
            log.warning(
                "Scheduler [NO-WATER] substituting %s -> %s for slot '%s'",
                actual_cmd, _WATER_SUBSTITUTE, slot.label,
            )
            actual_cmd = _WATER_SUBSTITUTE

        if dry_run:
            log.info("Scheduler [DRY] would fire %s -> %s (%s)",
                     slot.time_str, actual_cmd, slot.label)
            fired.add(sid)
        else:
            log.info("Scheduler: firing %s -> %s (%s)",
                     slot.time_str, actual_cmd, slot.label)
            records = publisher.execute([actual_cmd])
            r = records[0]
            log.info("Scheduler: published %s -> %s", slot.command, r.status)
            history.append(
                source="scheduler",
                action=f"sched:{slot.label}",
                emitted=[actual_cmd],
                status=r.status,
                position={"x": 0, "y": 0, "z": 0},
                note=f"{slot.time_str} {actual_cmd}  [{r.status}]"
                     + (" [NO-WATER substitute]" if no_water and slot.command in _WATER_COMMANDS else ""),
            )
            fired.add(sid)

    state["fired"] = sorted(fired)
    _save_state(state)


# ─── CLI helpers ──────────────────────────────────────────────────────────
def _default_config_path() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "config" / "farmbot.yaml"


def _print_status(slots: List[Slot], ros2_enabled: bool) -> None:
    state = _load_state()
    now = datetime.now()
    fired = set(state.get("fired") or [])
    alive = _bringup_alive(ros2_enabled)
    subscribed = _keyboard_topic_has_subscribers(ros2_enabled) if ros2_enabled else True
    conflict = _autonomous_controller_running()

    sep = "-" * 54
    print(f"\n{sep}")
    print(f"  GrowMate Scheduler  status at {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)
    print(f"  FarmBot bringup alive  : {'YES' if alive else 'NO  <- start it first'}")
    print(f"  keyboard_topic subbed  : {'YES' if subscribed else 'NO  <- bringup may still be loading'}")
    if conflict:
        print("  WARNING: autonomous_controller running — CONFLICT on /input_topic")
    print(f"  State file             : {_STATE_PATH}")
    print(f"  State date             : {state.get('date') or '(none)'}")
    print(f"  Fired today            : {state.get('fired') or []}")
    print()

    if not slots:
        print("  (no schedule entries)")
    else:
        print("  Schedule:")
        for s in slots:
            sid = s.slot_id()
            nf = s.next_fire(now)
            delta = nf - now
            h, rem = divmod(int(delta.total_seconds()), 3600)
            m = rem // 60
            status = "fired today" if sid in fired else f"next in {h}h {m:02d}m"
            print(f"    {s.time_str}  {s.command:<8}  {s.label:<26} [{status}]")
    print()


def _generate_systemd_unit(config_path: str, ros2_enabled: bool) -> str:
    python = sys.executable
    module = "growmate_voice.scheduler"
    flags = f"--config {config_path}"
    if not ros2_enabled:
        flags += " --no-ros2"
    return f"""[Unit]
Description=GrowMate FarmBot Scheduler
After=network.target

[Service]
Type=simple
User={os.environ.get('USER', 'pi')}
WorkingDirectory={Path(__file__).resolve().parents[3]}
ExecStart={python} -m {module} {flags}
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="GrowMate FarmBot scheduler")
    ap.add_argument("--config", default=str(_default_config_path()))
    ap.add_argument("--no-ros2", action="store_true",
                    help="simulation mode — print instead of publishing")
    ap.add_argument("--dry-run", action="store_true",
                    help="log decisions, don't publish")
    ap.add_argument("--catchup-min", type=int, default=_DEFAULT_CATCHUP_MIN,
                    help=f"catch-up window in minutes (default {_DEFAULT_CATCHUP_MIN})")
    ap.add_argument("--interval", type=int, default=_DEFAULT_INTERVAL_S,
                    help=f"seconds between checks (default {_DEFAULT_INTERVAL_S})")
    ap.add_argument("--once", action="store_true", help="one tick then exit")
    ap.add_argument("--list", action="store_true", help="print schedule and exit")
    ap.add_argument("--status", action="store_true",
                    help="print status (bringup, fired slots, next fire) and exit")
    ap.add_argument("--force", metavar="SLOT_ID",
                    help="fire a slot immediately by its id (HH:MM:CMD), e.g. '08:00:P_4'")
    ap.add_argument("--install-service", action="store_true",
                    help="print a systemd unit file for this scheduler")
    ap.add_argument("--no-water", action="store_true",
                    help="test mode: replace P_4/P_5/D_W_1 with I_1 (photo) "
                         "so the scheduler runs fully but never fires the pump")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    config_path = Path(args.config)
    slots = _load_schedule(config_path)
    ros2_enabled = not args.no_ros2

    if args.list:
        if not slots:
            print(f"(no schedule entries in {config_path})")
            return 0
        for s in slots:
            print(f"  {s.time_str}  {s.command:<8}  {s.label}")
        return 0

    if args.status:
        _print_status(slots, ros2_enabled)
        return 0

    if args.install_service:
        print(_generate_systemd_unit(args.config, ros2_enabled))
        return 0

    if args.force:
        target_id = args.force
        match = next((s for s in slots if s.slot_id() == target_id), None)
        if match is None:
            print(f"[error] slot '{target_id}' not in schedule", file=sys.stderr)
            print("Known slots:", [s.slot_id() for s in slots])
            return 2
        publisher = ROS2Publisher(ros2_enabled=ros2_enabled)
        history = History()
        state = _load_state()
        state = _roll_if_new_day(state)
        fired = set(state.get("fired") or [])
        log.info("Scheduler [FORCE] firing %s -> %s", match.time_str, match.command)
        records = publisher.execute([match.command])
        r = records[0]
        history.append(source="scheduler", action=f"sched:FORCE:{match.label}",
                       emitted=[match.command], status=r.status,
                       position={"x": 0, "y": 0, "z": 0},
                       note=f"FORCED {match.command} [{r.status}]")
        fired.add(target_id)
        state["fired"] = sorted(fired)
        _save_state(state)
        publisher.shutdown()
        return 0

    if not slots:
        log.warning("Scheduler: no slots loaded — exiting")
        return 0

    if args.no_water:
        log.warning("Scheduler: NO-WATER mode — %s will be replaced with %s",
                    _WATER_COMMANDS, _WATER_SUBSTITUTE)

    log.info(
        "Scheduler: %d slots, catchup=%dm, interval=%ds, ros2=%s, dry_run=%s, no_water=%s",
        len(slots), args.catchup_min, args.interval, ros2_enabled,
        args.dry_run, args.no_water,
    )

    publisher = ROS2Publisher(ros2_enabled=ros2_enabled)
    history = History()
    state = _load_state()

    try:
        while True:
            try:
                _tick(slots, publisher, history, state,
                      args.catchup_min, args.dry_run, ros2_enabled,
                      no_water=args.no_water)
                state = _load_state()
            except Exception:  # noqa: BLE001
                log.exception("Scheduler tick failed")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Scheduler stopped (Ctrl-C)")
    finally:
        publisher.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
