"""V2 F4 Stage A2 — best-effort Python → Go mission shadow bridge.

This module is intentionally non-authoritative.  It freezes the current Python
waypoint plan into the F3 MissionPlan proto and submits/cancels a Core shadow run
on daemon threads.  It never reads mission state to drive flight behavior and it
never issues GOTO/HOLD/TAKEOFF/SERVO/RTL.
"""
from __future__ import annotations

import os
import threading
import uuid

from . import rpc


def enabled() -> bool:
    value = os.getenv("SWARMGOD_MISSION_SHADOW", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def _operation_id(window, flow_run_id=None) -> str:
    session = getattr(window, "_mission_shadow_session_id", "")
    if not session:
        session = uuid.uuid4().hex
        window._mission_shadow_session_id = session
    run_id = int(flow_run_id if flow_run_id is not None
                 else (getattr(window, "_flight_run_id", 0) or 0))
    return f"cockpit:{session}:{run_id}"


def _action_proto(action):
    pb = rpc.mission_pb2
    return {
        "servo_a": pb.MISSION_WP_ACTION_SERVO_A,
        "servo_b": pb.MISSION_WP_ACTION_SERVO_B,
    }.get(str(action or "").strip().lower(), pb.MISSION_WP_ACTION_NONE)


def build_plan(window, routes, ids, swarm_head, operation_id):
    """Freeze the current Python waypoint route into a MissionPlan proto.

    Shared GROUPED altitude is representative only during A2 SHADOW.  The Python
    executor currently supports per-drone altitude while MissionWaypoint has one
    altitude value; Stage B authority cutover is blocked until that compatibility
    contract is explicit.
    """
    pb = rpc.mission_pb2
    if swarm_head:
        participants = sorted({int(d) for d in routes.keys()})
        mode = pb.MISSION_MODE_SWARM_LEADER
        shared = window._waypoint_route or routes.get(int(swarm_head))
        if shared is None:
            shared = next(iter(routes.values()), None)
        route_specs = [(0, shared, int(swarm_head))]
    elif window._wp_separate:
        participants = sorted({int(d) for d in ids})
        mode = pb.MISSION_MODE_SEPARATE
        route_specs = [(did, routes.get(did), did) for did in participants]
    else:
        participants = sorted({int(d) for d in ids})
        mode = pb.MISSION_MODE_GROUPED
        shared = window._waypoint_route or next(iter(routes.values()), None)
        alt_owner = participants[0] if participants else 0
        route_specs = [(0, shared, alt_owner)]

    if not participants or any(route is None for _, route, _ in route_specs):
        raise ValueError("mission shadow plan has no participants/route")

    proto_routes = []
    for route_drone_id, route, alt_owner in route_specs:
        alt = float(window._last_alt.get(alt_owner) or window._alt_for(alt_owner))
        points = [
            pb.MissionWaypoint(
                seq=int(wp.index), lat=float(wp.lat), lon=float(wp.lon), alt=alt,
                wait_seconds=int(getattr(wp, "wait_seconds", 0) or 0),
                action=_action_proto(getattr(wp, "action", "")))
            for wp in route.points
        ]
        proto_routes.append(pb.MissionRoute(
            drone_id=int(route_drone_id), points=points))

    return pb.MissionPlan(
        plan_id=str(operation_id), mode=mode, participants=participants,
        routes=proto_routes, leader_id=int(swarm_head or 0),
        arrival_radius_m=3.0, rtl_after=False)


def start(window, routes, ids, swarm_head=0, flow_run_id=None):
    """Fire-and-forget StartMission.  Failure must not affect Python execution."""
    if not enabled():
        return
    client = window.client
    start_call = getattr(client, "start_mission", None)
    if not callable(start_call):
        return  # old/mock client: preserve legacy execution exactly

    operation_id = _operation_id(window, flow_run_id)
    try:
        plan = build_plan(window, routes, ids, swarm_head, operation_id)
    except Exception:
        return  # pure shadow: plan conversion cannot block flight

    window._mission_shadow_operation_id = operation_id
    window._mission_shadow_run_id = 0
    window._mission_shadow_cancel_pending = False

    def worker():
        try:
            response = start_call(plan, operation_id)
            run_id = int(getattr(response, "run_id", 0) or 0)
            if run_id <= 0:
                return
            stale = (getattr(window, "_mission_shadow_operation_id", None) != operation_id
                     or bool(getattr(window, "_mission_shadow_cancel_pending", False)))
            if stale:
                cancel_call = getattr(window.client, "cancel_mission", None)
                if callable(cancel_call):
                    try:
                        cancel_call(run_id, operation_id + ":cancel")
                    except Exception:
                        pass
                return
            window._mission_shadow_run_id = run_id
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def cancel(window):
    """Best-effort stale-safe shadow cancel, including Cancel racing slow Start."""
    if not enabled():
        return
    operation_id = getattr(window, "_mission_shadow_operation_id", "")
    if not operation_id:
        return

    # Set first: if StartMission is still in flight, its worker sees this and
    # immediately cancels the returned Core run_id instead of leaving it active.
    window._mission_shadow_cancel_pending = True
    run_id = int(getattr(window, "_mission_shadow_run_id", 0) or 0)
    cancel_call = getattr(window.client, "cancel_mission", None)
    if run_id <= 0 or not callable(cancel_call):
        return

    def worker():
        try:
            cancel_call(run_id, operation_id + ":cancel")
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
