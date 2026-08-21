"""Garden config loader for the Pi-side server.

Reads the same ``farmbot.yaml`` schema as the legacy ``growmate_voice`` package
but is intentionally smaller — Pi doesn't need the LLM-facing helpers like
``get_plant_list()`` since classification happens on the client side.

This module is the single source of plant coordinates, aliases, and
workspace bounds on the Pi. Action nodes use ``resolve_target()`` to turn an
intent's ``target`` string into ``(x, y, z, water_quantity)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


# Words that carry no identity of their own. Dropped before token matching so
# "the lettuce bed" and "lettuce" resolve alike, and so a filler-only target
# ("the bed") resolves to nothing rather than to whichever key happens to
# contain the word. Same idea as the _FILLERS set in bt/builder.py.
_FILLERS = frozenset({
    "the", "a", "an", "my", "our", "some", "that", "this", "there",
    "please", "bed", "beds", "patch", "patches", "row", "rows",
    "section", "plant", "plants", "position", "area",
})


def _tokens(s: str, extra_fillers: frozenset = frozenset()) -> frozenset:
    """Lowercase content tokens of ``s``, punctuation and fillers removed."""
    drop = _FILLERS | extra_fillers
    return frozenset(
        t for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in drop
    )


# Generic words that name no particular head. Dropping them lets "the watering
# tool" reach watering_nozzle, while a bare "the tool" reduces to nothing and
# resolves to None — better a refusal than an arbitrary head on the UTM.
_TOOL_FILLERS = frozenset({"tool", "tools", "head", "attachment", "thing", "one"})

# Spoken names for the tool heads. The config ``tools:`` blocks are keyed by the
# canonical name and carry no aliases today, so these defaults live here rather
# than in YAML — gh2's config file has live uncommitted edits and must not be
# touched just to teach the robot a synonym. A config may still add its own via
# ``tools: <name>: aliases: [...]``, which takes precedence.
_TOOL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "watering_nozzle": ("watering nozzle", "nozzle", "water nozzle",
                        "watering head", "watering can", "sprayer",
                        "spray head", "hose", "watering tool", "water tool"),
    "soil_sensor": ("soil sensor", "soil probe", "probe", "moisture sensor",
                    "moisture probe", "soil moisture sensor", "sensor",
                    "soil tool"),
    "weeder": ("weeder", "weeding tool", "weed tool", "weed puller"),
    "rotating_weeder": ("rotating weeder", "rotary weeder", "spinning weeder"),
    "seeder": ("seeder", "seed injector", "seeding tool", "planter",
               "seed tool"),
}


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
        self.tools: Dict = self.config.get("tools", {})
        self.soil: Dict = self.config.get("soil", {})

        self._lookup: Dict[str, Tuple[Dict, str]] = {}
        for p in self.plants:
            self._lookup[p["name"].lower()] = (p, "plant")
            for a in p.get("aliases", []):
                self._lookup[a.lower()] = (p, "plant")
        for loc in self.locations:
            self._lookup[loc["name"].lower()] = (loc, "location")
            for a in loc.get("aliases", []):
                self._lookup[a.lower()] = (loc, "location")

        # Spoken tool name -> canonical key, built only from heads actually
        # configured on THIS robot: gh2 has no rotating_weeder, so "rotating
        # weeder" must resolve to None there and let the tree refuse rather
        # than mount some other head.
        self._tool_lookup: Dict[str, str] = {}
        for canon in self.tools_by_name():
            self._tool_lookup[canon] = canon
            self._tool_lookup[canon.replace("_", " ")] = canon
            for a in _TOOL_ALIASES.get(canon, ()):
                self._tool_lookup.setdefault(a, canon)
            for a in (self.tools.get(canon) or {}).get("aliases", []) or []:
                self._tool_lookup[str(a).lower().strip()] = canon

    def resolve_tool(self, name: Optional[str]) -> Optional[str]:
        """Resolve a spoken tool name ("the watering nozzle") to a config key.

        None when the name matches no head configured on this robot, which is
        what lets ``_tree_mount_tool`` refuse cleanly instead of mounting the
        wrong thing. Exact names and aliases win; otherwise the same
        token-boundary rule as ``resolve_target``, with generic words like
        "tool" stripped so a bare "the tool" resolves to nothing.
        """
        if not name or not self._tool_lookup:
            return None
        key = name.lower().strip()
        if key in self._tool_lookup:
            return self._tool_lookup[key]
        want = _tokens(key, _TOOL_FILLERS)
        if not want:
            return None
        best: Optional[str] = None
        best_score = 0.0
        for spoken, canon in self._tool_lookup.items():
            have = _tokens(spoken.replace("_", " "), _TOOL_FILLERS)
            if not have or not (want <= have or have <= want):
                continue
            score = len(want & have) / len(want | have)
            if score > best_score:
                best, best_score = canon, score
        return best

    def _fuzzy(self, key: str) -> Optional[Tuple[Dict, str]]:
        """Token-boundary fallback for a name that is not an exact key/alias.

        This used to be a bare substring test (``key in k or k in key``), which
        was the mechanism behind the refusal misses: an unknown target that
        shared any substring with a config key resolved to that key's
        coordinates, so the caller drove the gantry to a stale alias position
        instead of refusing. Whole-token matching keeps "the lettuce bed" ->
        "lettuce" while letting an unknown species fall through to None, which
        is what lets the tree refuse cleanly.

        Explicit ``aliases:`` are unaffected — they are exact keys and are
        matched before this runs. Best overlap wins rather than first hit, so
        the result no longer depends on dict insertion order.
        """
        want = _tokens(key)
        if not want:
            return None
        best: Optional[Tuple[Dict, str]] = None
        best_score = 0.0
        for k, v in self._lookup.items():
            have = _tokens(k)
            if not have or not (want <= have or have <= want):
                continue
            # Jaccard: an exact token match (1.0) beats a partial one, so
            # "pepper" prefers "pepper" over "sweet pepper".
            score = len(want & have) / len(want | have)
            if score > best_score:
                best, best_score = v, score
        return best

    def resolve_target(self, name: Optional[str]) -> Optional[ResolvedTarget]:
        """Resolve a plant/location name (or alias) to coordinates.

        Returns None if the name is not found. The condition node
        ``CheckPlantFound`` is what turns this None into a tree FAILURE.
        """
        if not name:
            return None
        key = name.lower().strip()
        match = self._lookup.get(key) or self._fuzzy(key)
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

    def tool_verify_pin(self) -> bool:
        """True when the UTM seat pin (63) actually reflects tool presence.

        gh1's pin is hardware-modified to read 0 (mounted) permanently, so pin
        observations can't confirm anything there. When False, tool-change
        nodes confirm by choreography evidence only (>=1 busy cycle since the
        command + busy quiescence) and say so — never claiming a verified
        seat — and the boot-time preflight is skipped (it would always trip).
        """
        return bool(self.config.get("tool_verify_pin", True))

    def soil_thresholds(self) -> Tuple[float, float]:
        """(dry_above, wet_below) raw-ADC thresholds for the soil sensor.

        Defaults mirror the action_nodes constants; per-greenhouse values are
        written by the on-hardware calibration (runbook B6) into ``soil:``.
        """
        return (
            float(self.soil.get("dry_above", 600)),
            float(self.soil.get("wet_below", 350)),
        )

    def soil_calibrated(self) -> bool:
        """True once measured thresholds exist for THIS greenhouse (audit F4).

        Until then the polarity/scale of the raw reading is an assumption:
        check_sensor speaks the raw number without a dry/wet verdict, and
        water_smart refuses cleanly rather than watering on a guess.
        """
        return bool(self.soil.get("calibrated", False))

    def position_verify_mm(self) -> Optional[float]:
        """Tolerance (mm) for post-move position verification, or None when
        disabled. A verified MoveTo only reports SUCCESS once the live R82
        position matches the target within this tolerance (audit F3) —
        ``safety.position_verify: false`` switches it off live on hardware,
        ``safety.position_tolerance_mm`` tunes it. Defaults: on, 5 mm.
        """
        if not bool(self.safety.get("position_verify", True)):
            return None
        return float(self.safety.get("position_tolerance_mm", 5.0))

    def tools_by_name(self) -> Dict[str, int]:
        """Map logical tool name -> UTM index, for MountTool / EnsureTool."""
        out: Dict[str, int] = {}
        for name, cfg in (self.tools or {}).items():
            idx = (cfg or {}).get("index")
            if idx is not None:
                out[name] = int(idx)
        return out
