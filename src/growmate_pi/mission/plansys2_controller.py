"""PlanSys2-backed mission controller for multi-step GrowMate intents.

This is the V2 equivalent of the EE650 ``mission_controller.py``: instead of
hard-coding a sequence of waypoints, we build a PlanSys2 problem from the
list of intents in an ``IntentRequest`` and let POPF produce the visit order.

When the planner returns, we walk the plan one action at a time and tick the
existing py_trees ``MoveTo + tier-action`` subtree per step. That keeps the
safety contract (CheckAvailable / CheckBounds) intact for every leg.

This module is **optional**. The intent server falls back to direct
``build_tree`` (no planner) when:

* the request has zero or one targeted intent,
* PlanSys2 is not reachable,
* the user explicitly disables planning via ``--no-planner``.

It requires ``ros-humble-plansys2-*`` installed and a running bringup, e.g.::

    ros2 launch plansys2_bringup plansys2_bringup_launch_distributed.py \\
        model_file:=$(pwd)/src/growmate_pi/pddl/farmbot_domain.pddl

The Python interface here is intentionally minimal — most of the heavy
lifting happens via ROS2 service calls to the PlanSys2 nodes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from growmate_pi.farmbot_ros2_bridge import FarmBotROS2Bridge
from growmate_pi.garden_config import GardenConfig
from growmate_pi.schemas import Intent, TreeResult


log = logging.getLogger("growmate_pi.mission")


DOMAIN_PATH = (Path(__file__).resolve().parent.parent / "pddl" / "farmbot_domain.pddl")


@dataclass
class PlanStep:
    """One step from the POPF plan."""

    action: str
    args: List[str]


def _classify_tier(intent: Intent) -> str:
    """Map intent + params into a PDDL action tier.

    The client may set ``params.priority`` to ``critical`` or ``high``;
    everything else defaults to ``normal``. We respect explicit overrides
    but otherwise route ``water`` to ``high`` and other reads/photos to
    ``normal``.
    """
    explicit = (intent.params or {}).get("priority")
    if explicit in ("critical", "high", "normal"):
        return explicit
    if intent.action == "water":
        return "high"
    return "normal"


class PlanSys2Controller:
    """Builds a PlanSys2 problem from intents, requests a plan, executes it.

    The controller is created on demand by ``intent_server`` when multi-step
    planning is desirable; it owns its own rclpy node and service clients.

    Construction is intentionally lazy — the rclpy / plansys2_msgs imports
    happen inside ``connect()`` so the rest of the package stays importable
    on machines without PlanSys2.
    """

    def __init__(
        self,
        bridge: FarmBotROS2Bridge,
        garden: GardenConfig,
        domain_path: Path = DOMAIN_PATH,
    ):
        self._bridge = bridge
        self._garden = garden
        self._domain_path = domain_path
        self._node = None
        self._connected = False

    # ------------------------------------------------------------------ wiring
    def connect(self) -> bool:
        """Lazy-import rclpy and the PlanSys2 message packages.

        Returns False (no exception) when PlanSys2 isn't available — the
        caller should then fall back to the direct ``build_tree`` path.
        """
        try:
            import rclpy
            from plansys2_msgs.srv import (  # noqa: F401
                AddProblem,
                AddProblemGoal,
                GetPlan,
                GetDomain,
                GetProblem,
            )
        except ImportError as exc:
            log.info("PlanSys2 unavailable (%s) — planner disabled", exc)
            return False

        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node("growmate_pi_mission")
        self._rclpy = rclpy
        self._connected = True
        return True

    # ------------------------------------------------------ problem assembly
    def build_problem(self, intents: List[Intent]) -> List[PlanStep]:
        """Translate the intent list into a PlanSys2 problem and request a plan.

        Returns an empty list when planning isn't useful (single intent, or
        no plant-targeted intents).

        This is a stub for now — the actual ROS2 service calls (AddProblem,
        AddProblemGoal, GetPlan) follow the same pattern as the EE650
        mission_controller. We'll fill those in once we wire the controller
        into the intent server.
        """
        targeted = [i for i in intents if i.target]
        if len(targeted) < 2:
            return []
        if not self._connected:
            return []

        # TODO: ROS2 calls — defer to integration step.
        # Outline:
        #   1. _clear_knowledge()
        #   2. _add_instance("robot1", "robot")
        #      for each plant: _add_instance(plant.name, "plant"); _add_predicate("in_bounds", [plant.name])
        #   3. mark current plant: _add_predicate("robot_at", ["robot1", start])
        #   4. _add_predicate("robot_available", [])
        #   5. for each targeted intent:
        #         tier = _classify_tier(intent)
        #         _add_predicate(f"{tier}_active", [])
        #         _add_predicate(f"{tier}_at", [intent.target])
        #         (mark non-priority plants as is_normal_plant)
        #   6. set goal: visited(p) for each targeted plant
        #   7. POPF -> plan steps
        log.warning("PlanSys2 wiring not yet implemented — returning empty plan")
        return []

    # ----------------------------------------------------------- execution
    def execute_plan(
        self,
        steps: List[PlanStep],
        timeout_s: float = 120.0,
    ) -> TreeResult:
        """Walk a plan one step at a time. Each step is realised as a small
        py_trees subtree (MoveTo + tier action) and executed via the existing
        ``execute_tree`` path.

        Returns an aggregated TreeResult. Stub for now — implementation
        lands when ``build_problem`` is wired up.
        """
        if not steps:
            return TreeResult(
                label="EmptyPlan",
                status="success",
                node_results=[],
            )
        log.warning("execute_plan called but PlanSys2 wiring not yet complete")
        return TreeResult(
            label="StubPlan",
            status="partial",
            node_results=[],
        )

    # ------------------------------------------------------------------ teardown
    def shutdown(self):
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
        self._node = None
