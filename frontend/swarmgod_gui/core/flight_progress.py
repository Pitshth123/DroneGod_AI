"""Pure, headless state model for the PRE-FLIGHT SUMMARY V2 execution flow.

The cockpit owns the real command/telemetry transitions.  This module only
records those transitions and makes the resulting run safe to render in Qt.
It deliberately has no Qt, timer, protobuf, or RPC imports.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from typing import Any, Dict, Iterable, List, Optional


class StepStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


_TERMINAL = {StepStatus.DONE, StepStatus.FAILED, StepStatus.CANCELLED, StepStatus.SKIPPED}
_run_ids = count(1)


@dataclass
class FlightStep:
    id: str
    title: str
    detail: str = ""
    status: StepStatus = StepStatus.PENDING
    group_id: Optional[str] = None


@dataclass
class FlightRun:
    """One frozen operation and its current, auditable execution state."""
    kind: str
    plan_snapshot: Dict[str, Any]
    steps: List[FlightStep]
    run_id: int = field(default_factory=lambda: next(_run_ids))
    started_at: Optional[float] = None
    active_step_id: Optional[str] = None

    def __post_init__(self) -> None:
        # A run snapshot must never be modified by later planning UI actions.
        self.plan_snapshot = deepcopy(self.plan_snapshot)
        self._by_id = {step.id: step for step in self.steps}
        if len(self._by_id) != len(self.steps):
            raise ValueError("Flight step ids must be unique")

    def step(self, step_id: str) -> FlightStep:
        try:
            return self._by_id[step_id]
        except KeyError as exc:
            raise KeyError("unknown flight step: %s" % step_id) from exc

    def active(self, step_id: str, detail: Optional[str] = None) -> bool:
        """Make one step active; returns False when it is no longer valid.

        This method never advances a timer-derived state.  The adapter calls it
        only after a real command, RPC, or telemetry transition.
        """
        target = self.step(step_id)
        if target.status in _TERMINAL:
            return False
        if self.active_step_id and self.active_step_id != step_id:
            current = self.step(self.active_step_id)
            if current.status == StepStatus.ACTIVE:
                current.status = StepStatus.PENDING
        target.status = StepStatus.ACTIVE
        if detail is not None:
            target.detail = str(detail)
        self.active_step_id = step_id
        return True

    def complete(self, step_id: str, detail: Optional[str] = None) -> bool:
        target = self.step(step_id)
        if target.status in _TERMINAL:
            return False
        target.status = StepStatus.DONE
        if detail is not None:
            target.detail = str(detail)
        if self.active_step_id == step_id:
            self.active_step_id = None
        return True

    def fail(self, step_id: str, detail: Optional[str] = None, *, skip_downstream: bool = True) -> bool:
        target = self.step(step_id)
        if target.status in _TERMINAL:
            return False
        target.status = StepStatus.FAILED
        if detail is not None:
            target.detail = str(detail)
        if self.active_step_id == step_id:
            self.active_step_id = None
        if skip_downstream:
            self._finish_after(step_id, StepStatus.SKIPPED)
        return True

    def cancel(self, detail: Optional[str] = None) -> None:
        """Cancel the live step and skip all remaining work."""
        if self.active_step_id:
            active = self.step(self.active_step_id)
            if active.status == StepStatus.ACTIVE:
                active.status = StepStatus.CANCELLED
                if detail is not None:
                    active.detail = str(detail)
        self.active_step_id = None
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                step.status = StepStatus.SKIPPED

    def skip(self, step_id: str, detail: Optional[str] = None) -> bool:
        target = self.step(step_id)
        if target.status in _TERMINAL:
            return False
        target.status = StepStatus.SKIPPED
        if detail is not None:
            target.detail = str(detail)
        if self.active_step_id == step_id:
            self.active_step_id = None
        return True

    def set_detail(self, step_id: str, detail: str) -> bool:
        target = self.step(step_id)
        if target.status in _TERMINAL and target.status != StepStatus.DONE:
            return False
        target.detail = str(detail)
        return True

    def _finish_after(self, step_id: str, status: StepStatus) -> None:
        seen = False
        for step in self.steps:
            if step.id == step_id:
                seen = True
                continue
            if seen and step.status == StepStatus.PENDING:
                step.status = status

    @property
    def active_index(self) -> int:
        if not self.active_step_id:
            return 0
        return next((i + 1 for i, step in enumerate(self.steps)
                     if step.id == self.active_step_id), 0)


def _steps(items: Iterable[tuple[str, str, Optional[str]]]) -> List[FlightStep]:
    return [FlightStep(step_id, title, group_id=group_id)
            for step_id, title, group_id in items]


def build_takeoff_flow(plan_snapshot: Dict[str, Any], mode: str = "all") -> FlightRun:
    send = "SEND TAKEOFF" if (mode or "").lower() == "all" else "SEQUENTIAL TAKEOFF"
    wait = "WAIT AIRBORNE" if send == "SEND TAKEOFF" else "WAIT ALL AIRBORNE"
    return FlightRun("takeoff", plan_snapshot, _steps([
        ("plan_confirmed", "PLAN CONFIRMED", None),
        ("preflight_gate", "PRE-FLIGHT GATE", None),
        ("send_takeoff", send, None),
        ("wait_airborne", wait, None),
        ("target_alt", "REACH TARGET ALT", None),
        ("flight_ready", "FLIGHT READY", None),
    ]))


def build_swarm_takeoff_flow(plan_snapshot: Dict[str, Any], mode: str = "all") -> FlightRun:
    takeoff = "TAKEOFF" if (mode or "").lower() == "all" else "SEQUENTIAL TAKEOFF"
    return FlightRun("swarm_takeoff", plan_snapshot, _steps([
        ("plan_confirmed", "PLAN CONFIRMED", None),
        ("preflight_gate", "PRE-FLIGHT GATE", None),
        ("takeoff", takeoff, None),
        ("wait_airborne", "WAIT AIRBORNE", None),
        ("form_up", "FORM UP", None),
        ("swarm_active", "SWARM ACTIVE", None),
    ]))


def build_waypoint_flow(plan_snapshot: Dict[str, Any], *, auto_takeoff: bool = True,
                        has_actions: bool = False) -> FlightRun:
    steps = _steps([
        ("validate_route", "VALIDATE ROUTE", None),
        ("confirm_mission", "CONFIRM MISSION", None),
        ("auto_takeoff", "AUTO TAKEOFF", None),
        ("wait_airborne", "WAIT AIRBORNE", None),
        ("fly_waypoint", "FLY WAYPOINT", None),
    ])
    if not auto_takeoff:
        steps[2].status = StepStatus.SKIPPED
        steps[3].status = StepStatus.SKIPPED
        steps[2].detail = "All targets already airborne"
    if has_actions:
        steps.append(FlightStep("waypoint_action", "WAYPOINT ACTION"))
    steps.append(FlightStep("route_complete", "ROUTE COMPLETE"))
    return FlightRun("waypoint", plan_snapshot, steps)


def build_wave_flow(plan_snapshot: Dict[str, Any], groups: Iterable[int]) -> FlightRun:
    steps = [FlightStep("validate_wave", "VALIDATE WAVE")]
    for group in groups:
        gid = "g%s" % int(group)
        steps.append(FlightStep("%s_block" % gid, "GROUP %s" % int(group), group_id=gid))
    steps.append(FlightStep("wave_complete", "WAVE COMPLETE"))
    return FlightRun("wave", plan_snapshot, steps)
