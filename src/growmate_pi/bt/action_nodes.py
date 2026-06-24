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
from growmate_pi.tool_state import get_tool_state


# ---------- Per-command verify timeouts (tick-and-verify gate) ----------------

# Generous backstops for a firmware that never reports completion, not tight
# SLAs. On real hardware /busy_state resolves well inside these; they only bite
# when the firmware handler isn't running (and then --no-verify is the fix).
MOVE_TIMEOUT_S = 90.0
PUMP_TIMEOUT_S = 15.0
HOME_TIMEOUT_S = 120.0

# Depth (mm, negative = into soil) the weeder plunges to uproot a weed. Needs
# FIELD CALIBRATION per bed/tool; conservative default. clear_weeds: move over
# the weed at z=0 -> plunge to this -> raise back to z=0.
WEED_PLUNGE_Z = -150.0

# Soil sensor — Farmduino analog pin 59. The raw 0–1023 reading's polarity/scale
# need FIELD CALIBRATION; default assumption here is "higher = drier". Tune
# SOIL_DRY_ABOVE / SOIL_WET_BELOW once measured on gh1.
SOIL_PIN = 59
SOIL_DRY_ABOVE = 600
SOIL_WET_BELOW = 350
SENSOR_TIMEOUT_S = 8.0


def soil_label(value: float) -> str:
    """Coarse moisture label from a raw analog reading (calibrate the bounds)."""
    if value >= SOIL_DRY_ABOVE:
        return "dry"
    if value <= SOIL_WET_BELOW:
        return "wet"
    return "moist"


# ---------- Generic verified command publisher --------------------------------


class _VerifiedCommand(py_trees.behaviour.Behaviour):
    """Base for nodes that publish one command and optionally wait for the
    firmware to confirm it via the bridge's busy-state tracking.

    Lifecycle:
      * ``initialise`` builds the command string (subclass hook), publishes it
        once, and snapshots the bridge completion counter.
      * ``update`` returns SUCCESS immediately when verification is inactive
        (verify=False, or the bridge can't verify) — the legacy
        fire-and-forget path. When active it returns RUNNING until the counter
        advances (the command's busy cycle finished -> SUCCESS), the
        per-command timeout elapses (-> FAILURE, "unconfirmed"), or the
        operator e-stops (-> FAILURE, same one-tick contract as ``Wait``).

    Subclasses implement ``_build_command() -> Optional[str]``; returning None
    means "couldn't build a valid command" and yields FAILURE without
    publishing (the subclass should set ``feedback_message`` to say why).
    """

    def __init__(self, bridge, verify: bool = False,
                 timeout_s: float = MOVE_TIMEOUT_S, name: str = "Command"):
        super().__init__(name)
        self._bridge = bridge
        self._verify = verify
        self._timeout_s = float(timeout_s)
        self._task_state = get_task_state()
        self._cmd: Optional[str] = None
        self._publish_ok = False
        self._count0 = 0
        self._start: Optional[float] = None

    def _build_command(self) -> Optional[str]:
        raise NotImplementedError

    def initialise(self):
        self._start = time.monotonic()
        self._cmd = self._build_command()
        if self._cmd is None:
            self._publish_ok = False
            return
        record = self._bridge.publish(self._cmd, track=self._verify)
        self._publish_ok = record.status in ("sent", "simulated")
        self.feedback_message = (
            record.description if self._publish_ok
            else f"publish failed: {record.error}"
        )
        # Snapshot AFTER publish: on real hardware the firmware hasn't raised
        # busy yet, so this baseline is pre-cycle; we then wait for it to grow.
        self._count0 = self._bridge.completion_count()

    def update(self):
        if self._cmd is None or not self._publish_ok:
            return py_trees.common.Status.FAILURE

        # Legacy fast path: verification off for this node or unavailable.
        if not self._verify or not self._bridge.verify_active():
            return py_trees.common.Status.SUCCESS

        # Verified path — poll the bridge for a completed busy cycle.
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid-command"
            return py_trees.common.Status.FAILURE
        if self._bridge.completion_count() > self._count0:
            self.feedback_message = f"{self._cmd} confirmed"
            return py_trees.common.Status.SUCCESS
        if self._start is not None and (
            time.monotonic() - self._start >= self._timeout_s
        ):
            self.feedback_message = (
                f"unconfirmed: no busy-state completion for {self._cmd}"
            )
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        self._start = None


class PublishCmd(_VerifiedCommand):
    """Publish one FarmBot command string. SUCCESS unless the bridge errored.

    Used for fixed string commands like ``D_W_1``, ``D_L_0``, ``H_0``, ``P_4``,
    ``I_1`` etc. Commands that need parameters (M x y z) have their own nodes.

    With ``verify=True`` the node holds RUNNING until the firmware reports the
    command finished (busy-state True->False) — used for pump on/off and home.
    """

    def __init__(self, command: str, bridge, verify: bool = False,
                 timeout_s: float = PUMP_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(bridge, verify=verify, timeout_s=timeout_s,
                         name=name or f"Publish({command})")
        self._command = command

    def _build_command(self) -> Optional[str]:
        return self._command


# ---------- Movement ----------------------------------------------------------


class MoveTo(_VerifiedCommand):
    """Publish ``M x y z`` from blackboard ``plant_data`` (default) or
    explicit coords passed at construction time.

    With ``verify=True`` the node holds RUNNING until the gantry move is
    confirmed via busy-state, so a downstream pump/log only runs once the
    robot has actually arrived."""

    def __init__(
        self,
        bridge,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        verify: bool = False,
        timeout_s: float = MOVE_TIMEOUT_S,
        name: str = "MoveTo",
    ):
        super().__init__(bridge, verify=verify, timeout_s=timeout_s, name=name)
        self._static = (x, y, z) if x is not None else None
        if self._static is None:
            self.blackboard = self.attach_blackboard_client(name=name)
            self.blackboard.register_key(
                "plant_data", access=py_trees.common.Access.READ
            )

    def _build_command(self) -> Optional[str]:
        if self._static:
            x, y, z = self._static
        else:
            try:
                data = self.blackboard.plant_data
                x, y, z = data["x"], data["y"], data["z"]
            except (KeyError, TypeError):
                self.feedback_message = "no coords on blackboard"
                return None
        return f"M {x} {y} {z}"


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


# ---------- Sensor read (real reading via the bridge) -------------------------


class ReadSensor(py_trees.behaviour.Behaviour):
    """Publish ``D_S_C`` and wait for the firmware's soil reading.

    The bridge parses ``R41 P<pin> V<val>`` off ``/uart_receive``; this node
    publishes once, then holds RUNNING until a reading *fresher* than the
    publish arrives (in sim the bridge fakes one immediately). It writes
    ``{pin, value, status}`` to the blackboard under ``sensor_result`` and
    appends a spoken summary to ``tts_text``. A timeout reports cleanly rather
    than failing the whole tree.
    """

    def __init__(self, bridge, pin: int = SOIL_PIN,
                 timeout_s: float = SENSOR_TIMEOUT_S, name: str = "ReadSensor"):
        super().__init__(name)
        self._bridge = bridge
        self._pin = int(pin)
        self._timeout_s = float(timeout_s)
        self._task_state = get_task_state()
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key("sensor_result", access=py_trees.common.Access.WRITE)
        self.blackboard.register_key("tts_text", access=py_trees.common.Access.WRITE)
        self._baseline: Optional[float] = None
        self._published = False

    def initialise(self):
        # Capture the baseline BEFORE publishing so the sim's fake reply (set
        # during publish) counts as fresh.
        self._baseline = time.monotonic()
        rec = self._bridge.publish("D_S_C")
        self._published = rec.status in ("sent", "simulated")

    def _append_tts(self, msg: str) -> None:
        try:
            existing = self.blackboard.tts_text
        except KeyError:
            existing = ""
        self.blackboard.tts_text = f"{existing} {msg}".strip()

    def update(self):
        if not self._published:
            self.feedback_message = "D_S_C publish failed"
            return py_trees.common.Status.FAILURE
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid-read"
            return py_trees.common.Status.FAILURE

        reading = self._bridge.last_reading(self._pin, newer_than=self._baseline)
        if reading is not None:
            value = reading[0]
            status = soil_label(value)
            self.blackboard.sensor_result = {
                "pin": self._pin, "value": value, "status": status,
            }
            self._append_tts(f"The soil reads {value:.0f} — that's {status}.")
            self.feedback_message = f"pin {self._pin} = {value:.0f} ({status})"
            return py_trees.common.Status.SUCCESS

        if self._baseline is not None and (
            time.monotonic() - self._baseline >= self._timeout_s
        ):
            self.blackboard.sensor_result = {
                "pin": self._pin, "value": None, "status": "unknown",
            }
            self._append_tts("I couldn't get a soil reading just now.")
            self.feedback_message = "sensor read timeout"
            return py_trees.common.Status.SUCCESS  # report cleanly; don't fail the tree
        return py_trees.common.Status.RUNNING


# ---------- Smart watering (sense per plant, water only the dry ones) ----------


class RecordSoil(py_trees.behaviour.Behaviour):
    """Read the soil sensor and store the value for a specific plant.

    Like :class:`ReadSensor` but writes the reading into a shared ``readings``
    dict keyed by plant index (so a later watering pass can decide per plant)
    and speaks a per-plant line. Used by ``water_smart``'s sensing pass. Holds
    RUNNING until a fresh reading arrives (sim fakes one on publish); a timeout
    records ``None`` and reports cleanly so one bad read doesn't fail the tree.
    """

    def __init__(self, bridge, readings: Dict[int, Optional[float]], key: int,
                 plant_name: str, pin: int = SOIL_PIN,
                 timeout_s: float = SENSOR_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(name or f"RecordSoil({plant_name})")
        self._bridge = bridge
        self._readings = readings
        self._key = int(key)
        self._plant_name = plant_name
        self._pin = int(pin)
        self._timeout_s = float(timeout_s)
        self._task_state = get_task_state()
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key("tts_text", access=py_trees.common.Access.WRITE)
        self._baseline: Optional[float] = None
        self._published = False

    def initialise(self):
        self._baseline = time.monotonic()
        rec = self._bridge.publish("D_S_C")
        self._published = rec.status in ("sent", "simulated")

    def _append_tts(self, msg: str) -> None:
        try:
            existing = self.blackboard.tts_text
        except KeyError:
            existing = ""
        self.blackboard.tts_text = f"{existing} {msg}".strip()

    def update(self):
        if not self._published:
            self.feedback_message = "D_S_C publish failed"
            return py_trees.common.Status.FAILURE
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid-read"
            return py_trees.common.Status.FAILURE
        reading = self._bridge.last_reading(self._pin, newer_than=self._baseline)
        if reading is not None:
            value = float(reading[0])
            self._readings[self._key] = value
            label = soil_label(value)
            self._append_tts(f"{self._plant_name} reads {value:.0f} — {label}.")
            self.feedback_message = f"{self._plant_name}: {value:.0f} ({label})"
            return py_trees.common.Status.SUCCESS
        if self._baseline is not None and (
            time.monotonic() - self._baseline >= self._timeout_s
        ):
            self._readings[self._key] = None  # unknown read
            self._append_tts(f"Couldn't read {self._plant_name}'s soil.")
            self.feedback_message = "read timeout"
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.RUNNING


class CheckDry(py_trees.behaviour.Behaviour):
    """Condition: SUCCESS if this plant's stored reading says it needs water.

    Dry (``value >= SOIL_DRY_ABOVE``) -> SUCCESS. Unknown (``None``, a failed
    read) -> SUCCESS too: don't leave a plant thirsty because a read glitched.
    Moist/wet -> FAILURE (the Selector falls through to :class:`NoteSkip`).
    """

    def __init__(self, readings: Dict[int, Optional[float]], key: int,
                 dry_above: float = SOIL_DRY_ABOVE, name: Optional[str] = None):
        super().__init__(name or f"CheckDry({key})")
        self._readings = readings
        self._key = int(key)
        self._dry_above = float(dry_above)

    def update(self):
        value = self._readings.get(self._key)
        if value is None:
            self.feedback_message = "unknown read — watering to be safe"
            return py_trees.common.Status.SUCCESS
        if value >= self._dry_above:
            self.feedback_message = f"dry ({value:.0f})"
            return py_trees.common.Status.SUCCESS
        self.feedback_message = f"moist enough ({value:.0f})"
        return py_trees.common.Status.FAILURE


class NoteSkip(py_trees.behaviour.Behaviour):
    """Record that a plant was skipped (moist enough). Counts; always SUCCESS."""

    def __init__(self, counters: Dict[str, Any], plant_name: str,
                 name: Optional[str] = None):
        super().__init__(name or f"Skip({plant_name})")
        self._counters = counters
        self._plant_name = plant_name

    def update(self):
        self._counters["skipped"] = self._counters.get("skipped", 0) + 1
        self.feedback_message = f"skipped {self._plant_name} (moist)"
        return py_trees.common.Status.SUCCESS


class SummariseSmart(py_trees.behaviour.Behaviour):
    """Speak the smart-water summary: how many were watered vs left moist."""

    def __init__(self, counters: Dict[str, Any], total: int, target: str,
                 name: str = "SummariseSmart"):
        super().__init__(name)
        self._counters = counters
        self._total = int(total)
        self._target = target
        self.blackboard = self.attach_blackboard_client(name=name)
        self.blackboard.register_key("tts_text", access=py_trees.common.Access.WRITE)

    def update(self):
        watered = int(self._counters.get("watered", 0))
        skipped = int(self._counters.get("skipped", 0))
        if watered == 0:
            msg = (f"All {self._total} {self._target} were moist enough — "
                   "none needed water.")
        elif skipped == 0:
            msg = f"Watered all {watered} {self._target} — they were dry."
        else:
            msg = (f"Watered {watered} of {self._total} {self._target}; "
                   f"the other {skipped} were moist enough.")
        try:
            existing = self.blackboard.tts_text
        except KeyError:
            existing = ""
        self.blackboard.tts_text = f"{existing} {msg}".strip()
        self.feedback_message = msg
        return py_trees.common.Status.SUCCESS


# ---------- Tool-head change (UTM mount / unmount) ----------------------------

# UTM tool-seated detection is on pin 63 (B–C bridge): 0 = a tool is mounted,
# 1 = nothing mounted. The AURA mount/unmount runs a multi-move sequence, so
# allow it plenty of time.
TOOL_PIN = 63
TOOL_CHANGE_TIMEOUT_S = 120.0
TOOL_POLL_INTERVAL_S = 3.0  # how often to re-read pin 63 during the choreography


class _ToolChange(py_trees.behaviour.Behaviour):
    """Mount or unmount a tool head and confirm it via the UTM pin.

    Publishes ``T<index>_1`` (mount) or ``T<index>_2`` (unmount) — the AURA
    stack runs the move choreography and its own pin-63 check — plus a ``D_C``
    that queues behind it to read pin 63 afterwards. Holds RUNNING until a
    fresh pin-63 reading shows the expected state (0 mounted / 1 free), then
    updates ``ToolState``. Sim fakes the pin so this resolves in dev.
    """

    _POLL_INTERVAL_S = TOOL_POLL_INTERVAL_S

    def __init__(self, bridge, index: int, tool_name: str, mount: bool,
                 timeout_s: float = TOOL_CHANGE_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(name or f"{'Mount' if mount else 'Unmount'}({tool_name})")
        self._bridge = bridge
        self._index = int(index)
        self._tool_name = tool_name
        self._mount = bool(mount)
        self._timeout_s = float(timeout_s)
        self._task_state = get_task_state()
        self._tool_state = get_tool_state()
        self._expected = 0.0 if mount else 1.0  # pin 63
        self._baseline: Optional[float] = None
        self._published = False

    def initialise(self):
        self._start = time.monotonic()
        rec = self._bridge.publish(f"T{self._index}_{'1' if self._mount else '2'}")
        self._published = rec.status in ("sent", "simulated")
        # Poll pin 63 with periodic D_C reads. The mount choreography takes
        # ~15-25 s, so an early "not seated" reading means "keep waiting", NOT
        # failure. _last_poll = 0 forces a read on the first tick.
        self._last_poll = 0.0
        self._poll_baseline = self._start

    def update(self):
        if not self._published:
            self.feedback_message = "tool command publish failed"
            return py_trees.common.Status.FAILURE
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid tool-change"
            return py_trees.common.Status.FAILURE

        now = time.monotonic()
        # Re-issue D_C every few seconds so we get a FRESH pin-63 reading as the
        # choreography progresses (the AURA sequence also self-checks at its end).
        if now - self._last_poll >= self._POLL_INTERVAL_S:
            self._poll_baseline = now
            self._bridge.publish("D_C")
            self._last_poll = now

        reading = self._bridge.last_reading(TOOL_PIN, newer_than=self._poll_baseline)
        if reading is not None and abs(reading[0] - self._expected) < 0.5:
            if self._mount:
                self._tool_state.set(self._tool_name)
            else:
                self._tool_state.clear()
            self.feedback_message = (
                f"{'mounted' if self._mount else 'unmounted'} {self._tool_name}"
            )
            return py_trees.common.Status.SUCCESS

        if now - self._start >= self._timeout_s:
            last = f" (pin63={reading[0]:.0f})" if reading is not None else ""
            self.feedback_message = (
                f"tool {'mount' if self._mount else 'unmount'} timed out{last}"
            )
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING


class MountTool(_ToolChange):
    def __init__(self, bridge, index: int, tool_name: str,
                 timeout_s: float = TOOL_CHANGE_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(bridge, index, tool_name, mount=True,
                         timeout_s=timeout_s, name=name)


class UnmountTool(_ToolChange):
    def __init__(self, bridge, index: int, tool_name: str,
                 timeout_s: float = TOOL_CHANGE_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(bridge, index, tool_name, mount=False,
                         timeout_s=timeout_s, name=name)


class EnsureTool(py_trees.behaviour.Behaviour):
    """Guarantee ``tool_name`` is the mounted head, swapping only if needed.

    Instant SUCCESS (no motion) when ToolState already says it's mounted —
    that's the point, no redundant swaps. Otherwise it unmounts the current
    head (if any) and mounts the target, confirming via pin 63 like MountTool.
    Prepend it to any tool-specific action. ``tools_by_name`` is the
    config name->index map (``GardenConfig.tools_by_name()``).
    """

    def __init__(self, bridge, tool_name: str, tools_by_name: Dict[str, int],
                 timeout_s: float = TOOL_CHANGE_TIMEOUT_S, name: Optional[str] = None):
        super().__init__(name or f"EnsureTool({tool_name})")
        self._bridge = bridge
        self._target = tool_name
        self._by_name = dict(tools_by_name or {})
        self._timeout_s = float(timeout_s)
        self._task_state = get_task_state()
        self._tool_state = get_tool_state()
        self._noop = False
        self._published = False
        self._baseline: Optional[float] = None
        self._err: Optional[str] = None

    def initialise(self):
        self._noop = False
        self._published = False
        self._err = None
        self._baseline = None
        current = self._tool_state.current()
        if current == self._target:
            self._noop = True
            return
        target_index = self._by_name.get(self._target)
        if target_index is None:
            self._err = f"no configured index for tool '{self._target}'"
            return
        self._baseline = time.monotonic()
        ok = True
        if current is not None and current in self._by_name:
            r = self._bridge.publish(f"T{self._by_name[current]}_2")  # unmount current
            ok = ok and r.status in ("sent", "simulated")
        rm = self._bridge.publish(f"T{target_index}_1")               # mount target
        rd = self._bridge.publish("D_C")
        self._published = (ok and rm.status in ("sent", "simulated")
                           and rd.status in ("sent", "simulated"))

    def update(self):
        if self._noop:
            self.feedback_message = f"{self._target} already mounted"
            return py_trees.common.Status.SUCCESS
        if self._err:
            self.feedback_message = self._err
            return py_trees.common.Status.FAILURE
        if not self._published:
            self.feedback_message = "tool command publish failed"
            return py_trees.common.Status.FAILURE
        if self._task_state.estop_requested():
            self.feedback_message = "estop requested mid tool-change"
            return py_trees.common.Status.FAILURE

        reading = self._bridge.last_reading(TOOL_PIN, newer_than=self._baseline)
        if reading is not None:
            if abs(reading[0]) < 0.5:  # pin 63 == 0 -> mounted
                self._tool_state.set(self._target)
                self.feedback_message = f"mounted {self._target}"
                return py_trees.common.Status.SUCCESS
            self.feedback_message = f"mount not confirmed (pin63={reading[0]:.0f})"
            return py_trees.common.Status.FAILURE
        if self._baseline is not None and (
            time.monotonic() - self._baseline >= self._timeout_s
        ):
            self.feedback_message = "tool change timeout"
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING
