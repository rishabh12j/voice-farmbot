#!/usr/bin/env python3
"""Jog-and-capture helper for FarmBot tool-bay positions.

Jog the EMPTY UTM onto each tool sitting in its bay (app d-pad,
keyboard_controller, or `ros2 topic pub /keyboard_topic ... 'M x y z'`), press
Enter here, and this grabs the live gantry position from the intent server's
``/status`` (the R82 position the bridge parses). At the end it prints:

  * the one-time ``T_n_0`` registration commands (publish each to write the tool
    into the active map), and
  * the ``farmbot.yaml`` ``tools:`` block to paste into config.

Stdlib only (urllib) so it runs with any python, venv or system.

Run on the Pi, with bringup + the intent server up and the gantry homed::

    python3 tools/calibrate_tools.py
    python3 tools/calibrate_tools.py --tools soil_sensor:2,watering_nozzle:1
    python3 tools/calibrate_tools.py --pi-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def _get_position(base_url: str):
    with urllib.request.urlopen(base_url + "/status", timeout=5) as r:
        body = json.load(r)
    return body.get("position")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Capture FarmBot tool-bay positions")
    ap.add_argument("--pi-url", default="http://localhost:8000",
                    help="intent server base URL (default http://localhost:8000)")
    ap.add_argument("--tools",
                    default="watering_nozzle:1,soil_sensor:2,seeder:3,weeder:4",
                    help="comma list of name:index to calibrate")
    args = ap.parse_args(argv)

    base = args.pi_url.rstrip("/")
    if base.endswith("/intent"):
        base = base[: -len("/intent")]

    tools = []
    for item in args.tools.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, idx = item.partition(":")
        tools.append((name.strip(), int(idx) if idx.strip() else len(tools) + 1))

    print("Tool-bay calibration — jog the EMPTY UTM onto each tool, then Enter.")
    print("Position is read from the intent server (R82). Ctrl-C to abort.\n")

    captured = []
    for name, idx in tools:
        print(f"--- {name} (T{idx}) ---")
        ans = input(f"  Jog onto {name}'s seated position, then Enter "
                    f"(s = skip): ").strip().lower()
        if ans == "s":
            print("  skipped\n")
            continue
        try:
            pos = _get_position(base)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! couldn't read /status: {exc}\n")
            continue
        if not pos or pos.get("x") is None:
            print("  ! no position yet — move the gantry once so it reports, "
                  "then retry\n")
            continue
        x, y, z = pos["x"], pos["y"], pos["z"]
        print(f"  captured  x={x}  y={y}  z={z}")
        rd_raw = input("  release_dir (1=-x  2=+x  3=-y  4=+y): ").strip()
        try:
            rd = int(rd_raw)
            if rd not in (1, 2, 3, 4):
                raise ValueError
        except ValueError:
            print("  ! invalid release_dir, defaulting to 1")
            rd = 1
        captured.append((name, idx, x, y, z, rd))
        print()

    if not captured:
        print("Nothing captured.")
        return 0

    print("\n===== register (publish each line on the Pi) =====")
    for name, idx, x, y, z, rd in captured:
        print("ros2 topic pub --once /keyboard_topic std_msgs/msg/String "
              f"\"{{data: 'T_{idx}_0 {name} {x} {y} {z} {rd}'}}\"")

    print("\n===== farmbot.yaml  tools:  block =====")
    print("tools:")
    for name, idx, x, y, z, rd in captured:
        print(f"  {name}: {{index: {idx}, x: {x}, y: {y}, z: {z}, release_dir: {rd}}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
