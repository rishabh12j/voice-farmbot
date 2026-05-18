"""Garden config loader for the Pi-side server.

Reads the same ``farmbot.yaml`` schema as the legacy ``growmate_voice`` package
but is intentionally smaller — Pi doesn't need the LLM-facing helpers like
``get_plant_list()`` since classification happens on the client side.

This module is the single source of plant coordinates, aliases, and
workspace bounds on the Pi. Action nodes use ``resolve_target()`` to turn an
intent's ``target`` string into ``(x, y, z, water_quantity)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


@dataclass
class ResolvedTarget:
    name: str
    x: float
    y: float
    z: float
    water_quantity: int = 6  # seconds; default if not specified
    kind: str = "plant"  # "plant" or "location"


class GardenConfig:
    def __init__(self, config_path: str | Path):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.robot: Dict = self.config.get("robot", {})
        self.workspace: Dict = self.robot.get("workspace", {})
        self.plants: List[Dict] = self.config.get("garden", {}).get("plants", [])
        self.locations: List[Dict] = self.config.get("garden", {}).get("locations", [])
        self.safety: Dict = self.config.get("safety", {})
        self.schedule: Dict = self.config.get("schedule", {})

        self._lookup: Dict[str, Tuple[Dict, str]] = {}
        for p in self.plants:
            self._lookup[p["name"].lower()] = (p, "plant")
            for a in p.get("aliases", []):
                self._lookup[a.lower()] = (p, "plant")
        for loc in self.locations:
            self._lookup[loc["name"].lower()] = (loc, "location")
            for a in loc.get("aliases", []):
                self._lookup[a.lower()] = (loc, "location")

    def resolve_target(self, name: Optional[str]) -> Optional[ResolvedTarget]:
        """Resolve a plant/location name (or alias) to coordinates.

        Returns None if the name is not found. The condition node
        ``CheckPlantFound`` is what turns this None into a tree FAILURE.
        """
        if not name:
            return None
        key = name.lower().strip()
        match = self._lookup.get(key)
        if not match:
            # Fall back to fuzzy substring match (matches legacy GardenConfig)
            for k, v in self._lookup.items():
                if key in k or k in key:
                    match = v
                    break
        if not match:
            return None
        item, kind = match
        pos = item["position"]
        return ResolvedTarget(
            name=item["name"],
            x=float(pos["x"]),
            y=float(pos["y"]),
            z=float(pos["z"]),
            water_quantity=int(item.get("water_quantity", 6)),
            kind=kind,
        )

    def in_bounds(self, x: float, y: float, z: float) -> bool:
        """True if (x, y, z) is inside the configured workspace."""
        x_max = float(self.workspace.get("x_max", 5691.2))
        y_max = float(self.workspace.get("y_max", 2734.0))
        z_min = float(self.workspace.get("z_min", -500))
        z_max = float(self.workspace.get("z_max", 0))
        return 0 <= x <= x_max and 0 <= y <= y_max and z_min <= z <= z_max

    def emergency_phrases(self) -> List[str]:
        return list(self.safety.get("emergency_phrases", []))

    def watering_time(self) -> str:
        return str(self.schedule.get("watering_time", "08:00"))
