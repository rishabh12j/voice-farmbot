"""py_trees action nodes for GrowMate V2.

Every node that touches the robot funnels through ``FarmBotROS2Bridge.publish``
so sim and real modes share one code path. Nodes are sync where possible —
``Wait`` and the eventual sensor-read action use RUNNING-based timing because
py_trees ticks should never block.

Common pattern:

    class FooAction(py_trees.behaviour.Behaviour):
        def __init__(self, bridge, ...): ...
        def update(self) -> Status: ...

Action nodes do not enforce safety on their own — the builder is responsible
for prepending ``CheckAvailable`` / ``CheckBounds`` / ``CheckPlantFound``
before any action that needs them.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import py_trees

from growmate_pi.task_state import get_task_state


# ---------- Generic command publisher -----------------------------------------


class PublishCmd(py_trees.behaviour.Behaviour):
    """Publish one FarmBot command string. SUCCESS unless the bridge errored.

    Used for fixed string commands like ``D_W_1``, ``D_L_0``, ``H_0``, ``P_4``,
    ``I_1`` etc. Commands that need parameters (M x y z) have their own nodes.
    """

    def __init__(self, command: str, bridge, name: Optional[str] = None):
        super().__init__(name or f"Publish({command})")
        self._command = command
        self._bridge = bridge

    def update(self):
        record = self._bridge.publish(self._command)
        if record.status in ("sent", "simulated"):
            self.feedback_message = record.description
            return py_trees.common.Status.SUCCESS
        self.feedback_message = f"publish failed: {record.error}"
        return py_trees.common.Status.FAILURE


# ---------- Movement ----------------------------------------------------------


class MoveTo(py_trees.behaviour.Behaviour):
    """Publish ``M x y z`` from blackboard ``plant_data`` (default) or
    explicit coords passed at construction time."""

    def __init__(
        self,
        bridge,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        name: str = "MoveTo",
    ):
        super().__init__(name)
        self._bridge = bridge
        self._static = (x, y, z) if x is not None else None
        if self._static is None:
            self.blackboard = self.attach_blackboard_client(name=name)
            self.blackboard.register_key(
                "plant_data", access=py_trees.common.Access.READ
            )

    def update(self):
        if self._static:
            x, y, z = self._static
        else:
            try:
                data = self.blackboard.plant_data
                x, y, z = data["x"], data["y"], data["z"]
            except (KeyError, TypeError):
                self.feedback_message = "no coords on blackboard"
                return py_trees.common.Status.FAILURE
        cmd = f"M {x} {y} {z}"
        record = self._bridge.publish(cmd)
        if record.status in ("sent", "simulated"):
            self.feedback_message = record.description
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


# ---------- Timing ------------------------------------------------------------


class Wait(py_trees.behaviour.Behaviour):
    """Non-blocking wait. Returns RUNNING for ``seconds`` then SUCCESS.

    Tier B: also checks ``task_state.estop_requested`` every tick and
    returns FAILURE within one tick (~100 ms) when the operator presses
    the stop button. Without this, a 30-second pump pulse would hold the
    tree hostage even though /estop has already published 'e' to the
    bridge.

    Uses ``monotonic`` so wall-clock changes don't trip the timer.
    """

    def __init__(self, seconds: float, name: Optional[str] = None):
        super().__init__(name or f"Wait({seconds}s)")
        self._duration = float(seconds)
        self._start: Optional[float] = None
        self._task_state = get_task_state()

    def initialise(self):
        self._start = time.monotonic()
        self.feedback_message = f"waiting {self._duration}s"

    def update(self):
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid-wait"
            return py_trees.common.Status.FAILURE
        if self._start is None:
            self._start = time.monotonic()
        elapsed = time.monotonic() - self._start
        if elapsed >= self._duration:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        self._start = None


# ---------- Estop-checkpoint (use between leaves in long sequences) ----------


class CheckEstop(py_trees.behaviour.Behaviour):
    """A leaf that fails fast if the operator has pressed Stop.

    Drop one of these between every plant in a multi-plant sequence so a
    Stop press doesn't have to wait for the current plant's Wait node to
    next-tick — it can short-circuit at the boundary instead. SUCCESS if
    nothing's been requested, FAILURE the moment the flag flips.
    """

    def __init__(self, name: str = "CheckEstop"):
        super().__init__(name)
        self._task_state = get_task_state()

    def update(self):
        if self._task_state.estop_requested():
            self.feedback_message = "operator pressed stop"
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.SUCCESS


# ---------- Task-state progress notifier --------------------------------------


class StepNotify(py_trees.behaviour.Behaviour):
    """Bump task_state to advertise which step is now starting.

    Used as the first leaf inside each plant's sub-sequence in the
    multi-plant water tree so the /status poller can render "Plant 3 of
    8 — Lettuce_little_gem #12" to the blocking overlay. Always SUCCESS.
    """

    def __init__(self, step_index: int, step_label: str,
                 name: Optional[str] = None):
        super().__init__(name or f"Step({step_index})")
        self._step_index = int(step_index)
        self._step_label = step_label
        self._task_state = get_task_state()

    def update(self):
        self._task_state.step(self._step_index, self._step_label)
        self.feedback_message = f"step {self._step_index}: {self._step_label}"
        return py_trees.common.Status.SUCCESS


# ---------- Task-state lifecycle markers --------------------------------------


class TaskBoundary(py_trees.behaviour.Behaviour):
    """Mark task start (``start``) or end (``end``) on the task-state
    singleton.

    Wrap a long-running sequence with ``TaskBoundary("start", ...)`` at
    the front and ``TaskBoundary("end")`` at the back so the /status
    poller knows when to show the blocking overlay and when to hide it.
    Always SUCCESS — the marker itself can never fail.
    """

    def __init__(self, mode: str, task_id: Optional[str] = None,
                 label: str = "", total_steps: int = 0,
                 name: Optional[str] = None):
        super().__init__(name or f"Task({mode})")
        if mode not in ("start", "end"):
            raise ValueError(f"TaskBoundary mode must be 'start' or 'end' (got {mode!r})")
        self._mode = mode
        self._task_id = task_id or ""
        self._label = label
        self._total_steps = int(total_steps)
        self._task_state = get_task_state()

    def update(self):
        if self._mode == "start":
            self._task_state.start(self._task_id, self._label, self._total_steps)
            self.feedback_message = f"task started: {self._label}"
        else:
            self._task_state.end()
            self.feedback_message = "task ended"
        return py_trees.common.Status.SUCCESS


# ---------- Per-leaf event-log writer -----------------------------------------


class LogPlantEvent(py_trees.behaviour.Behaviour):
    """Append one row to the per-plant event log after a leaf completes.

    Tier B's "honest log" piece. Instead of writing one tree-summary row
    at the end of /intent (which lied when the tree was only partial),
    each watered plant gets its own row at the moment it finishes —
    so the future ``last_watered_ts`` query is grounded in real ticks.

    The actual write is delegated to a callable passed in by the
    builder, so this node stays decoupled from the event_log module's
    import surface.
    """

    def __init__(self, log_fn: Callable[[], None], name: str = "LogEvent"):
        super().__init__(name)
        self._log_fn = log_fn

    def update(self):
        try:
            self._log_fn()
            self.feedback_message = "event logged"
        except Exception as exc:
            # Logging is best-effort — never let a logging hiccup fail
            # a successful action.
            self.feedback_message = f"log failed: {exc}"
        return py_trees.common.Status.SUCCESS


# ---------- Reply / TTS aggregator -------------------------------------------


class Respond(py_trees.behaviour.Behaviour):
    """Append a TTS message to the blackboard. SUCCESS on tick.

    The executor reads ``tts_text`` after the tree finishes to build the
    ``tts_text`` field in ``IntentResponse``.
    """

    def __init__(self, message: str, name: str = "Respond"):
        super().__init__(name)
        self._message = message
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key(
            "tts_text", access=py_trees.common.Access.WRITE
        )

    def update(self):
        try:
            existing = self.blackboard.tts_text
        except KeyError:
            existing = ""
        if existing:
            self.blackboard.tts_text = f"{existing} {self._message}"
        else:
            self.blackboard.tts_text = self._message
        self.feedback_message = self._message
        return py_trees.common.Status.SUCCESS


# ---------- Emergency stop ---------------------------------------------------


class EmergencyStop(py_trees.behaviour.Behaviour):
    """Publish the ``e`` command directly via the bridge. Always SUCCESS
    (e-stop must never report failure — the publish has already happened).
    """

    def __init__(self, bridge, name: str = "EmergencyStop"):
        super().__init__(name)
        self._bridge = bridge

    def update(self):
        self._bridge.emergency_stop()
        self.feedback_message = "estop published"
        return py_trees.common.Status.SUCCESS


# ---------- Sensor read (placeholder for real read path) ----------------------


class ReadSensor(py_trees.behaviour.Behaviour):
    """Publish ``D_S_C`` to read the soil sensor.

    The real FarmBot replies on a separate topic — for now we just publish
    the command and record SUCCESS. A future change will subscribe to the
    reply topic and stash the value on the blackboard for ``llm_reason`` /
    downstream reasoning.
    """

    def __init__(self, bridge, name: str = "ReadSensor"):
        super().__init__(name)
        self._bridge = bridge
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key(
            "sensor_result", access=py_trees.common.Access.WRITE
        )

    def update(self):
        record = self._bridge.publish("D_S_C")
        if record.status in ("sent", "simulated"):
            self.blackboard.sensor_result = {"raw": "pending-subscription"}
            self.feedback_message = "D_S_C published"
            return py_trees.common.Status.SUCCESS
        self.feedback_message = f"publish failed: {record.error}"
        return py_trees.common.Status.FAILURE
