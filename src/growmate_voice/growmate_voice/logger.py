"""Centralised logging for GrowMate.

Single call: ``from growmate_voice.logger import log``

Writes to:
  - Console  (INFO+ with colour coding)
  - Rotating file at  ~/.growmate_voice/logs/growmate.log  (DEBUG+)

Keeps 3 × 2 MB files.  Read the latest with:
  tail -f ~/.growmate_voice/logs/growmate.log
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


# ─── ANSI colours for console (disabled on Windows if not supported) ─────────
_RESET  = "\033[0m"
_COLOURS = {
    "DEBUG":    "\033[36m",    # cyan
    "INFO":     "\033[32m",    # green
    "WARNING":  "\033[33m",    # yellow
    "ERROR":    "\033[31m",    # red
    "CRITICAL": "\033[41m",    # red background
}

_WIN_ANSI_ENABLED = False
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        _WIN_ANSI_ENABLED = True
    except Exception:
        pass

_USE_COLOUR = (os.name != "nt") or _WIN_ANSI_ENABLED


class _ColourFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if _USE_COLOUR:
            colour = _COLOURS.get(record.levelname, _RESET)
            return f"{colour}{msg}{_RESET}"
        return msg


def get_logger(name: str = "growmate") -> logging.Logger:
    """Return a named logger, configuring it on first call."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    _fmt = "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s"
    _date_fmt = "%H:%M:%S"

    # ── file handler ─────────────────────────────────────────────────────────
    log_dir = Path.home() / ".growmate_voice" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "growmate.log"

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_fmt, datefmt=_date_fmt))

    # ── console handler ───────────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(_ColourFormatter(_fmt, datefmt=_date_fmt))

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False

    logger.debug("Logger initialised — file: %s", log_path)
    return logger


def log_path() -> Path:
    """Return the path to the current log file."""
    return Path.home() / ".growmate_voice" / "logs" / "growmate.log"


def tail_log(n: int = 60) -> str:
    """Return the last *n* lines of the log file as a single string."""
    p = log_path()
    if not p.exists():
        return "(log file not yet created)"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as exc:
        return f"(error reading log: {exc})"


# Module-level default logger
log = get_logger("growmate")
