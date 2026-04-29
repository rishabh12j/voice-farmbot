"""Live calibration helper — capture FarmBot gantry position into a CSV.

Workflow (with the GrowMate FastAPI app running on http://localhost:7860):

  1. In the GrowMate UI, jog the gantry over the centre of a real plant.
  2. In a second terminal, run this script.
  3. When prompted, type the species name and press Enter.
  4. The script reads the live position from /api/status and appends one row
     to ``tools/placements.csv``. Repeat for every plant you want to record.
  5. Stop with Ctrl-C, then build the map:

        python tools\\build_active_map.py --mode csv --csv tools\\placements.csv

Usage::

    python tools\\calibrate.py
    python tools\\calibrate.py --url http://growmate-1.local:7860 --csv per_bot/farmbot1.csv

The species you type must exist in SPECIES at the top of build_active_map.py
(case-sensitive). Tab-complete is on by default; press TAB to autocomplete.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List


# Pulled in lazily so we don't crash if SPECIES is reorganised.
def _load_species_names() -> List[str]:
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    try:
        from build_active_map import SPECIES  # type: ignore
        return sorted(SPECIES.keys())
    except Exception:
        return []


def _completer(options: List[str]):
    def _complete(text, state):
        matches = [o for o in options if o.lower().startswith(text.lower())]
        return matches[state] if state < len(matches) else None
    return _complete


def _enable_tab_complete(options: List[str]) -> None:
    try:
        import readline
        readline.set_completer(_completer(options))
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass  # pyreadline3 needed on Windows for tab; fine without it


def _fetch_position(url: str, timeout: float = 4.0) -> dict:
    req = urllib.request.Request(f"{url.rstrip('/')}/api/status")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json
        data = json.loads(resp.read().decode())
    return data.get("position", {})


def _ensure_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["species", "x", "y", "z"])


def _append_row(path: Path, species: str, pos: dict) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([species, pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live FarmBot calibration recorder")
    ap.add_argument("--url", default="http://localhost:7860",
                    help="GrowMate app URL (default: %(default)s)")
    ap.add_argument("--csv", default="tools/placements.csv",
                    help="output CSV (will be created if missing)")
    args = ap.parse_args(argv)

    out = Path(args.csv)
    _ensure_header(out)

    species = _load_species_names()
    if species:
        _enable_tab_complete(species)
        print(f"Known species ({len(species)}): {', '.join(species)}")
    else:
        print("(could not load SPECIES list — tab-complete disabled)")

    print(f"Recording to {out}")
    print(f"Talking to    {args.url}")
    print("Drive the gantry in the UI, then enter species here. Ctrl-C to stop.\n")

    count = 0
    try:
        while True:
            try:
                pos = _fetch_position(args.url)
            except urllib.error.URLError as exc:
                print(f"[error] cannot reach {args.url}: {exc}")
                time.sleep(2)
                continue

            here = f"X {int(pos.get('x', 0))} mm  Y {int(pos.get('y', 0))} mm  Z {int(pos.get('z', 0))} mm"
            try:
                line = input(f"  {here}  → species (or Enter to refresh, 'q' to quit): ")
            except EOFError:
                break
            line = line.strip()
            if not line:
                continue
            if line.lower() in ("q", "quit", "exit"):
                break

            if species and line not in species:
                close = [s for s in species if s.lower().startswith(line.lower())]
                if len(close) == 1:
                    line = close[0]
                else:
                    print(f"  [warn] '{line}' not in SPECIES; "
                          f"add it to build_active_map.py first or pick from: "
                          f"{', '.join(close) or species}")
                    continue

            _append_row(out, line, pos)
            count += 1
            print(f"  ✓ {line}  ({count} recorded)\n")
    except KeyboardInterrupt:
        print()

    print(f"\nDone. {count} rows appended to {out}")
    print(f"Build the map:\n  python tools/build_active_map.py --mode csv --csv {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
