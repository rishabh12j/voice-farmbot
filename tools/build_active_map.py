"""Build ``active_map.yaml`` from a real planted layout.

Replaces the rectangle-and-spacing grid generator with a workflow that can
describe any layout:

  * **explicit**  — a hand-curated point list (``PLACEMENTS``)
  * **csv**       — ``placements.csv`` next to this script
  * **farmbot**   — pull live plant points from the FarmBot REST API

The same per-species metadata (canopy radius, water quantity, …) lives in a
single ``SPECIES`` table and is stamped onto each point at build time, so you
never duplicate metadata per plant.

Usage::

    # Default: build from the inline PLACEMENTS list
    python tools/build_active_map.py

    # From CSV
    python tools/build_active_map.py --mode csv --csv tools/placements.csv

    # From FarmBot
    export FARMBOT_URL="https://my.farm.bot"
    export FARMBOT_TOKEN="eyJhbGciOi..."
    python tools/build_active_map.py --mode farmbot

    # Visualise an existing map
    python tools/build_active_map.py --visualize active_map.yaml

CSV columns (z optional)::

    species,x,y,z
    Scallion,200,200,
    Scallion,350,200,
    Tomato,3100,200,-240
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml


# ─── per-species metadata  ──────────────────────────────────────────────────
# Extend / edit this to match the plants you actually grow.
# The defaults below match the canopy/water values found in the AURA stack's
# CONF-generated active_map.yaml on the Pi (see tools/active_map.from_pi.yaml).
SPECIES: Dict[str, Dict[str, float]] = {
    # ── Edibles ────────────────────────────────────────────────────────────
    "Tomato":             {"canopy_radius": 30.0, "max_height": 100.0, "plant_radius": 30.0, "water_quantity": 5.0, "z": -290.0},
    "Lettuce_little_gem": {"canopy_radius": 12.5, "max_height":  25.0, "plant_radius":  2.5, "water_quantity": 5.0, "z": -290.0},
    "Scallion":           {"canopy_radius":  7.5, "max_height":  30.0, "plant_radius":  2.5, "water_quantity": 2.0, "z": -290.0},
    "Mixed Pepper":       {"canopy_radius": 22.5, "max_height":  75.0, "plant_radius":  2.5, "water_quantity": 3.0, "z": -290.0},
    # ── Herbs ──────────────────────────────────────────────────────────────
    "Basil":              {"canopy_radius": 12.0, "max_height":  35.0, "plant_radius":  3.0, "water_quantity": 2.0, "z": -290.0},
    "Spearmint":          {"canopy_radius": 15.0, "max_height":  50.0, "plant_radius":  3.0, "water_quantity": 2.0, "z": -290.0},
    # ── Companion + ornamental flowers ────────────────────────────────────
    "Marigold":           {"canopy_radius": 12.5, "max_height":  20.0, "plant_radius":  2.5, "water_quantity": 2.0, "z": -290.0},
    "Lily":               {"canopy_radius": 20.0, "max_height":  80.0, "plant_radius":  3.0, "water_quantity": 3.0, "z": -290.0},
    "Geranium":           {"canopy_radius": 25.0, "max_height":  40.0, "plant_radius":  4.0, "water_quantity": 2.0, "z": -290.0},
    "Cardinal Flower":    {"canopy_radius": 15.0, "max_height":  80.0, "plant_radius":  3.0, "water_quantity": 4.0, "z": -290.0},
    "Dianthus":           {"canopy_radius": 15.0, "max_height":  20.0, "plant_radius":  2.5, "water_quantity": 2.0, "z": -290.0},
    "Euonymus":           {"canopy_radius": 40.0, "max_height": 100.0, "plant_radius": 10.0, "water_quantity": 4.0, "z": -290.0},
    "Petunia":            {"canopy_radius": 20.0, "max_height":  25.0, "plant_radius":  3.0, "water_quantity": 2.0, "z": -290.0},
    "Begonia":            {"canopy_radius": 20.0, "max_height":  30.0, "plant_radius":  3.0, "water_quantity": 2.0, "z": -290.0},
}


# ─── your actual planted layout  ────────────────────────────────────────────
# Each row: (species, x_mm, y_mm)   or   (species, x_mm, y_mm, z_mm)
# Replace this list with the real positions you planted.
PLACEMENTS: List[Tuple] = [
    # ("Scallion",            200.0,  200.0),
    # ("Scallion",            350.0,  200.0),
    # ("Lettuce_little_gem",  200.0,  600.0),
    # ("Tomato",             3100.0,  200.0),
]


# For FarmBot pull: translate openfarm_slug → species name used in SPECIES.
# Anything not in this map is skipped.
SLUG_TO_SPECIES: Dict[str, str] = {
    # Edibles
    "scallion":           "Scallion",
    "green-onion":        "Scallion",
    "spring-onion":       "Scallion",
    "lettuce-little-gem": "Lettuce_little_gem",
    "lettuce":            "Lettuce_little_gem",
    "tomato":             "Tomato",
    "pepper":             "Mixed Pepper",
    "mixed-pepper":       "Mixed Pepper",
    # Herbs
    "basil":              "Basil",
    "spearmint":          "Spearmint",
    "mint":               "Spearmint",
    # Flowers
    "marigold":           "Marigold",
    "lily":               "Lily",
    "asiatic-lily":       "Lily",
    "geranium":           "Geranium",
    "pelargonium":        "Geranium",
    "cardinal-flower":    "Cardinal Flower",
    "lobelia-cardinalis": "Cardinal Flower",
    "dianthus":           "Dianthus",
    "carnation":          "Dianthus",
    "sweet-william":      "Dianthus",
    "euonymus":           "Euonymus",
    "petunia":            "Petunia",
    "begonia":            "Begonia",
}


DEFAULT_DATE = {"day": 0, "month": 0, "year": 0}     # matches AURA CONF default
DEFAULT_STAGE = "Seedling"                            # matches AURA CONF default
DEFAULT_BED = {"x_len": 5691.2, "y_len": 2734.0, "z_len": 380.08}   # FarmBot Genesis XL


# ────────────────────────────────────────────────────────────────────────────
# Build
# ────────────────────────────────────────────────────────────────────────────
def build_from_points(
    placements: Sequence[Tuple],
    species: Dict[str, Dict[str, float]] = SPECIES,
    date: Dict[str, int] = DEFAULT_DATE,
    stage: str = DEFAULT_STAGE,
    bed: Dict[str, float] = DEFAULT_BED,
) -> Dict[str, Any]:
    """Build an AURA-schema active-map dict from ``(species, x, y[, z])`` rows."""
    active_map: Dict[str, Any] = {
        "map_reference": {
            "tools": {f"T{i}": None for i in range(1, 7)},
            "trays": None,
            "x_len": float(bed["x_len"]),
            "y_len": float(bed["y_len"]),
            "z_len": float(bed["z_len"]),
        },
        "plant_details": {"plant_count": 0, "plants": {}, "weeds": None},
    }

    for idx, row in enumerate(placements, start=1):
        if len(row) < 3:
            raise ValueError(f"placement #{idx} needs at least (species, x, y): got {row!r}")
        name, x, y = row[0], row[1], row[2]
        if name not in species:
            raise KeyError(
                f"placement #{idx} uses unknown species '{name}'. "
                f"Add it to SPECIES at the top of {Path(__file__).name}."
            )
        z = row[3] if len(row) > 3 else species[name]["z"]
        meta = species[name]
        active_map["plant_details"]["plants"][idx] = {
            "identifiers": {"index": idx, "plant_name": name},
            "plant_details": {
                "canopy_radius":   float(meta["canopy_radius"]),
                "max_height":      float(meta["max_height"]),
                "plant_radius":    float(meta["plant_radius"]),
                "soil_moisture":   0.0,
                "water_quantity":  float(meta["water_quantity"]),
            },
            "position": {"x": float(x), "y": float(y), "z": float(z)},
            "status": {"growth_stage": stage, "plant_date": dict(date)},
        }

    active_map["plant_details"]["plant_count"] = len(placements)
    return active_map


# ────────────────────────────────────────────────────────────────────────────
# Sources
# ────────────────────────────────────────────────────────────────────────────
def placements_from_csv(path: str | Path) -> List[Tuple]:
    out: List[Tuple] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            name = (row.get("species") or "").strip()
            try:
                x = float(row["x"]); y = float(row["y"])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path}: row {i} missing/bad x,y: {exc}") from exc
            z_raw = (row.get("z") or "").strip()
            if z_raw:
                out.append((name, x, y, float(z_raw)))
            else:
                out.append((name, x, y))
    return out


def placements_from_yaml(path: str | Path) -> List[Tuple]:
    """Load an existing active_map.yaml and return its plants as placements."""
    data = load_yaml(path)
    plants = data.get("plant_details", {}).get("plants", {}) or {}
    out: List[Tuple] = []
    # Sort by integer index when keys are ints/strings of ints
    for _idx, p in sorted(plants.items(), key=lambda kv: int(kv[0])):
        name = p["identifiers"]["plant_name"]
        x = p["position"]["x"]; y = p["position"]["y"]; z = p["position"]["z"]
        out.append((name, float(x), float(y), float(z)))
    return out


def placements_to_csv(placements: Sequence[Tuple], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["species", "x", "y", "z"])
        for row in placements:
            name, x, y = row[0], row[1], row[2]
            z = row[3] if len(row) > 3 else ""
            w.writerow([name, x, y, z])


def placements_from_farmbot(
    api_url: str,
    token: str,
    slug_to_species: Dict[str, str] = SLUG_TO_SPECIES,
) -> List[Tuple]:
    """Pull plant points from the FarmBot REST API."""
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/points?pointer_type=Plant",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        points = json.loads(resp.read().decode())

    out: List[Tuple] = []
    unknown: Dict[str, int] = {}
    for p in points:
        slug = (p.get("openfarm_slug") or "").lower()
        species = slug_to_species.get(slug)
        if species is None:
            unknown[slug or "(no slug)"] = unknown.get(slug or "(no slug)", 0) + 1
            continue
        x, y = float(p.get("x", 0)), float(p.get("y", 0))
        z = float(p.get("z", SPECIES[species]["z"]))
        out.append((species, x, y, z))

    if unknown:
        print("  [farmbot] skipped points for unknown slugs:")
        for slug, n in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}  '{slug}'   (add to SLUG_TO_SPECIES)")
    return out


# ────────────────────────────────────────────────────────────────────────────
# I/O
# ────────────────────────────────────────────────────────────────────────────
def save_yaml(active_map: Dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(active_map, f, sort_keys=False)


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ────────────────────────────────────────────────────────────────────────────
# Visualise
# ────────────────────────────────────────────────────────────────────────────
def visualize(active_map: Dict[str, Any]) -> None:
    import matplotlib
    import matplotlib.pyplot as plt

    plants = active_map["plant_details"]["plants"]
    if not plants:
        print("No plants to visualise.")
        return

    positions = [(p["position"]["x"], p["position"]["y"]) for p in plants.values()]
    names = [p["identifiers"]["plant_name"] for p in plants.values()]
    radii = [p["plant_details"].get("canopy_radius", 5.0) for p in plants.values()]

    unique = sorted(set(names))
    cmap_name = "tab20" if len(unique) > 10 else "tab10"
    cmap = matplotlib.colormaps.get_cmap(cmap_name).resampled(max(len(unique), 1))
    colour_of = {n: cmap(i) for i, n in enumerate(unique)}

    xs = [p[0] for p in positions]; ys = [p[1] for p in positions]
    x_range = max(xs) - min(xs) or 1
    y_range = max(ys) - min(ys) or 1
    scale = 10 / max(x_range, y_range)
    fig_w = max(6.0, x_range * scale)
    fig_h = max(4.0, y_range * scale)

    _, ax = plt.subplots(figsize=(fig_w, fig_h))
    seen: set = set()
    for (x, y), name, r in zip(positions, names, radii):
        label = name if name not in seen else ""
        seen.add(name)
        # scatter s= is in points², approximate the canopy visually
        ax.scatter(x, y, s=max(20.0, r * 4), color=colour_of[name],
                   alpha=0.85, edgecolor="black", linewidth=0.5, label=label)

    ax.set_aspect("equal")
    ax.set_title(f"Planted layout — {active_map['plant_details']['plant_count']} plants",
                 fontsize=15)
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Species", loc="upper left", fontsize=9)
    plt.tight_layout()
    plt.show()


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["explicit", "csv", "farmbot", "yaml"], default="explicit",
                    help="where to read placements from "
                         "(yaml = round-trip an existing active_map.yaml)")
    ap.add_argument("--csv", default="tools/placements.csv",
                    help="CSV path when --mode csv (or --export-csv target)")
    ap.add_argument("--in-yaml", default="tools/active_map.from_pi.yaml",
                    help="input YAML when --mode yaml")
    ap.add_argument("--out", default="tools/active_map.yaml",
                    help="output YAML path")
    ap.add_argument("--export-csv", action="store_true",
                    help="write placements to --csv (combine with --mode yaml or farmbot)")
    ap.add_argument("--no-visualize", action="store_true",
                    help="skip the scatter plot after building")
    ap.add_argument("--visualize", metavar="YAML",
                    help="load this map and just visualise it (no build)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    if args.visualize:
        visualize(load_yaml(args.visualize))
        return 0

    if args.mode == "explicit":
        if not PLACEMENTS:
            print("PLACEMENTS is empty. Edit the list at the top of this script or "
                  "run with --mode csv / --mode farmbot / --mode yaml.", file=sys.stderr)
            return 2
        placements = PLACEMENTS
    elif args.mode == "csv":
        placements = placements_from_csv(args.csv)
    elif args.mode == "farmbot":
        url = os.environ.get("FARMBOT_URL", "")
        token = os.environ.get("FARMBOT_TOKEN", "")
        if not url or not token:
            print("Set FARMBOT_URL and FARMBOT_TOKEN env vars first.", file=sys.stderr)
            return 2
        placements = placements_from_farmbot(url, token)
    elif args.mode == "yaml":
        placements = placements_from_yaml(args.in_yaml)
    else:
        raise AssertionError("unreachable")

    if args.export_csv:
        placements_to_csv(placements, args.csv)
        print(f"Exported {len(placements)} placements to {args.csv}")
        return 0

    print(f"Building map with {len(placements)} plants via mode={args.mode} …")
    active_map = build_from_points(placements)
    save_yaml(active_map, args.out)
    print(f"Wrote {args.out}  ({active_map['plant_details']['plant_count']} plants)")

    if not args.no_visualize:
        visualize(active_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
