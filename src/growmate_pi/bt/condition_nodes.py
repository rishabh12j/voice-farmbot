"""py_trees condition + resolution nodes for GrowMate V2.

Conditions are pure reads of blackboard state and never publish to ROS2.
``ResolveTarget`` is technically an action (writes the blackboard) but it
makes sense to group it here since the actual robot-touching action nodes
all depend on its output.

Blackboard keys these nodes touch:

* ``plant_data`` (dict {name, x, y, z, water_quantity, kind}) — written by
  ``ResolveTarget``, read by every node that targets a plant/location.
* ``bridge_ready`` (bool) — written by ``CheckAvailable`` for downstream
  diagnostics; not strictly needed but useful for the tree trace.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

import py_trees

from growmate_pi.garden_config import GardenConfig
from growmate_pi.tool_state import get_tool_state


# ---------- Conditions ---------------------------------------------------------


class CheckToolMounted(py_trees.behaviour.Behaviour):
    """SUCCESS iff ``tool_name`` is the tool ToolState believes is mounted.

    Software gate before a tool-specific action — pairs with ``MountTool`` /
    ``EnsureTool``. (The hardware can only confirm *a* tool is seated, not
    which, so the source of truth is what we last mounted.)
    """

    def __init__(self, tool_name: str, name: Optional[str] = None):
        super().__init__(name or f"CheckTool({tool_name})")
        self._tool_name = tool_name
        self._tool_state = get_tool_state()

    def update(self):
        if self._tool_state.is_mounted(self._tool_name):
            self.feedback_message = f"{self._tool_name} mounted"
            return py_trees.common.Status.SUCCESS
        self.feedback_message = (
            f"need {self._tool_name}, have {self._tool_state.current()}"
        )
        return py_trees.common.Status.FAILURE


class CheckAvailable(py_trees.behaviour.Behaviour):
    """SUCCESS iff the ROS2 bridge is ready (real or sim).

    First node of every robot-action subtree per the safety contract in
    CLAUDE.md §6 rule 2.
    """

    def __init__(self, bridge, name: str = "CheckAvailable"):
        super().__init__(name)
        self._bridge = bridge
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key("bridge_ready", access=py_trees.common.Access.WRITE)

    def update(self):
        ready = self._bridge.is_ready()
        self.blackboard.bridge_ready = ready
        return (
            py_trees.common.Status.SUCCESS
            if ready
            else py_trees.common.Status.FAILURE
        )


class CheckPlantFound(py_trees.behaviour.Behaviour):
    """SUCCESS iff ``plant_data`` is populated on the blackboard.

    Runs after ``ResolveTarget``. A FAILURE here means the LLM emitted a
    target the garden config doesn't know — the tree aborts before any
    movement is attempted.
    """

    def __init__(self, name: str = "CheckPlantFound"):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key("plant_data", access=py_trees.common.Access.READ)

    def update(self):
        try:
            data = self.blackboard.plant_data
        except KeyError:
            self.feedback_message = "no plant_data on blackboard"
            return py_trees.common.Status.FAILURE
        if not data or not isinstance(data, dict):
            self.feedback_message = "plant_data empty"
            return py_trees.common.Status.FAILURE
        self.feedback_message = f"found {data.get('name')}"
        return py_trees.common.Status.SUCCESS


class CheckBounds(py_trees.behaviour.Behaviour):
    """SUCCESS iff the target is inside the workspace bounds.

    Two modes:
      * blackboard (default) — validates ``plant_data.{x, y, z}`` written by
        ``ResolveTarget`` (the named-target paths).
      * explicit — pass ``x``/``y``/``z`` to guard a coordinate move directly.
        The explicit-coordinate move path used to publish UNGUARDED (the Pi
        trusted the client's clamping); the misclassification stress test
        proved an out-of-bounds `M` command sails through in sim. Per the
        research contract, the guard lives in the tree, not in trust.

    This is the last guard before a ``MoveTo`` is allowed to publish.
    """

    def __init__(self, garden: GardenConfig, name: str = "CheckBounds",
                 x: Optional[float] = None, y: Optional[float] = None,
                 z: Optional[float] = None):
        super().__init__(name)
        self._garden = garden
        self._static = (float(x), float(y), float(z)) if x is not None else None
        if self._static is None:
            self.blackboard = self.attach_blackboard_client(name=name)
            self.blackboard.register_key("plant_data", access=py_trees.common.Access.READ)

    def update(self):
        if self._static is not None:
            x, y, z = self._static
        else:
            try:
                data = self.blackboard.plant_data
            except KeyError:
                self.feedback_message = "no plant_data"
                return py_trees.common.Status.FAILURE
            if not data or not isinstance(data, dict):
                self.feedback_message = "plant_data empty"
                return py_trees.common.Status.FAILURE
            x, y, z = data.get("x"), data.get("y"), data.get("z")
            if x is None or y is None or z is None:
                self.feedback_message = "plant_data missing coords"
                return py_trees.common.Status.FAILURE
        if not self._garden.in_bounds(float(x), float(y), float(z)):
            self.feedback_message = f"out of bounds ({x}, {y}, {z})"
            return py_trees.common.Status.FAILURE
        self.feedback_message = "in bounds"
        return py_trees.common.Status.SUCCESS


# ---------- Target resolution (writes blackboard) -----------------------------


class ResolveTarget(py_trees.behaviour.Behaviour):
    """Resolve the intent's ``target`` string to coordinates from the garden
    config, then write a flat dict to the blackboard under ``plant_data``.

    A target of None or an unknown name results in FAILURE — the chained
    ``CheckPlantFound`` will pick this up and fail the subtree cleanly.
    """

    def __init__(
        self,
        garden: GardenConfig,
        target: Optional[str],
        name: Optional[str] = None,
    ):
        super().__init__(name or f"Resolve({target})")
        self._garden = garden
        self._target = target
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key("plant_data", access=py_trees.common.Access.WRITE)

    def update(self):
        if not self._target:
            self.feedback_message = "no target"
            return py_trees.common.Status.FAILURE
        resolved = self._garden.resolve_target(self._target)
        if resolved is None:
            self.feedback_message = f"unknown target '{self._target}'"
            return py_trees.common.Status.FAILURE
        self.blackboard.plant_data = asdict(resolved)
        self.feedback_message = f"resolved {resolved.name}"
        return py_trees.common.Status.SUCCESS
