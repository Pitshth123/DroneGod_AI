"""Mission boundary and per-plan authority selector for the safety fast-track.

The module freezes the Python waypoint plan into the Core MissionPlan contract.
In shadow/legacy ownership it is additive and never suppresses Python flight
behavior.  For the explicitly migrated single-drone GROUPED scope it performs
Start/Query/Cancel authority handoff and fail-closed recovery.  Unsupported old
capabilities stay Python-owned until their Go semantics are separately migrated.

This module itself never emits GOTO/HOLD/TAKEOFF/SERVO/RTL; Core-owned command
intents are executed by the Go API adapter through command.Service/safety.
"""
from __future__ import annotations

import os
import threading
import uuid

from . import rpc


def _truthy_env(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in ("1", "true", "yes", "on")


def authority_requested() -> bool:
    # Must match Core's explicit SITL-only tokens. Generic truthy values are
    # deliberately not enough to request a flight-authority cutover.
    return os.getenv("SWARMGOD_MISSION_AUTHORITY", "").strip().lower() in (
        "core-single", "core-single-wait")


def enabled() -> bool:
    # Authority implies the mission RPC boundary even when shadow was disabled.
    if authority_requested():
        return True
    return not os.getenv("SWARMGOD_MISSION_SHADOW", "1").strip().lower() in (
        "0", "false", "no", "off")


def authority_slot_idle(window) -> bool:
    """Confirm no active Core-authority run before any mission-side flight prep.

    This check runs before legacy WAVE/waypoint execution and, importantly,
    before auto-TAKEOFF.  A configured authority token means an unknown/active
    Core slot must fail closed; otherwise a Python prep command could start while
    another Core-owned mission already exists.
    """
    window._mission_legacy_ownership_blocked = False
    if not authority_requested():
        return True
    state_call = getattr(getattr(window, "client", None), "get_mission_state", None)
    if not callable(state_call):
        window._mission_legacy_ownership_blocked = True
        return False
    try:
        state = state_call()
    except Exception:
        window._mission_legacy_ownership_blocked = True
        return False
    if bool(getattr(state, "recovery_required", False)):
        window._mission_legacy_ownership_blocked = True
        return False
    if bool(getattr(state, "active", False)) and bool(
            getattr(state, "authority_active", False)):
        window._mission_legacy_ownership_blocked = True
        return False
    return True


def legacy_ownership_allowed(window) -> bool:
    """Prove a legacy Python-owned mission may start without Core overlap."""
    return authority_slot_idle(window)


def authority_eligible(window, routes, ids, swarm_head=0) -> bool:
    """Return whether *this plan* belongs to the currently enabled Core scope.

    The authority token is intentionally global configuration, but ownership is
    selected per plan.  Unsupported legacy capabilities (multi-drone GROUPED,
    SEPARATE, SWARM leader, payload A/B, and WAIT under ``core-single``) remain
    Python-owned until their Go semantics are migrated.  This is not an RPC
    failure fallback: ineligible plans never call StartMission in authority mode.

    For an eligible plan, StartMission is still fail-closed.  A lost/ambiguous
    reply must never cause Python to send GOTO because Core may already own it.
    """
    token = os.getenv("SWARMGOD_MISSION_AUTHORITY", "").strip().lower()
    if token not in ("core-single", "core-single-wait"):
        return False
    if bool(swarm_head) or bool(getattr(window, "_wp_separate", False)):
        return False

    participants = sorted({int(d) for d in ids})
    if len(participants) != 1:
        return False

    shared = getattr(window, "_waypoint_route", None)
    if shared is None:
        shared = next(iter(routes.values()), None) if routes else None
    points = list(getattr(shared, "points", []) or [])
    if not points:
        return False
    for wp in points:
        if str(getattr(wp, "action", "") or "").strip().lower():
            return False
        if token == "core-single" and int(getattr(wp, "wait_seconds", 0) or 0) != 0:
            return False
    return True


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

    ``participant_altitudes`` preserves the old per-drone altitude behavior even
    though each shared waypoint also carries an altitude for compatibility.  The
    Core single-drone path resolves through that same frozen participant contract;
    broader legacy scopes remain Python-owned until their command geometry moves.
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

    participant_altitudes = {
        int(did): float(window._last_alt.get(did) or window._alt_for(did))
        for did in participants
    }

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
        arrival_radius_m=3.0, rtl_after=False,
        participant_altitudes=participant_altitudes)


def start(window, routes, ids, swarm_head=0, flow_run_id=None):
    """Start the Core mission boundary.

    Shadow mode remains fire-and-forget and never affects legacy execution.
    Authority mode is intentionally synchronous + fail-closed: Python may stop
    sending mission GOTO only after Core explicitly confirms authority_active.
    A timeout/error is resolved with GetMissionState; it never falls back to a
    Python GOTO because Core may have accepted the Start before the reply was lost.
    Returns True only when Core authority is confirmed for this run.
    """
    if not enabled():
        return False
    client = window.client
    start_call = getattr(client, "start_mission", None)
    requested = authority_requested()
    eligible = authority_eligible(window, routes, ids, swarm_head)
    window._mission_legacy_ownership_blocked = False

    # A configured Core authority token must not disable the legacy capabilities
    # that are outside its explicit migration scope. These plans remain under
    # the old Python executor by design and never call authoritative StartMission.
    # First prove there is no already-active Core-authority run: otherwise a
    # legacy mission could overlap a prior Core mission after cancel/reconnect.
    if requested and not eligible:
        window._mission_core_authority = False
        window._mission_shadow_run_id = 0
        window._mission_shadow_operation_id = ""
        window._mission_shadow_cancel_pending = False
        legacy_ownership_allowed(window)
        return False

    if not callable(start_call):
        return False

    operation_id = _operation_id(window, flow_run_id)
    try:
        plan = build_plan(window, routes, ids, swarm_head, operation_id)
    except Exception:
        return False

    window._mission_shadow_operation_id = operation_id
    window._mission_shadow_run_id = 0
    window._mission_shadow_cancel_pending = False
    if not requested:
        window._mission_core_authority = False

    if requested:
        response = None
        try:
            response = _dispatch_mutation(
                window, "MISSION START", lambda: start_call(plan, operation_id),
                targets=ids, operation_id=operation_id)
        except Exception:
            # The request may have reached Core even if the reply was lost. Query
            # by immutable plan identity before deciding whether Python may act.
            state_call = getattr(client, "get_mission_state", None)
            if callable(state_call):
                try:
                    state = state_call()
                    if (bool(getattr(state, "active", False))
                            and bool(getattr(state, "authority_active", False))
                            and str(getattr(state, "plan_id", "")) == operation_id):
                        window._mission_shadow_run_id = int(
                            getattr(state, "run_id", 0) or 0)
                        window._mission_core_authority = True
                        return window._mission_shadow_run_id > 0
                except Exception:
                    pass
            window._mission_core_authority = False
            return False

        run_id = int(getattr(response, "run_id", 0) or 0)
        active = bool(getattr(response, "authority_active", False))
        ok = bool(getattr(response, "ok", False))
        if ok and active and run_id > 0:
            window._mission_shadow_run_id = run_id
            window._mission_core_authority = True
            return True
        window._mission_core_authority = False
        return False

    def worker():
        try:
            response = _dispatch_mutation(
                window, "MISSION START [shadow]",
                lambda: start_call(plan, operation_id), targets=ids,
                operation_id=operation_id)
            run_id = int(getattr(response, "run_id", 0) or 0)
            if run_id <= 0:
                return
            stale = (getattr(window, "_mission_shadow_operation_id", None) != operation_id
                     or bool(getattr(window, "_mission_shadow_cancel_pending", False)))
            if stale:
                cancel_call = getattr(window.client, "cancel_mission", None)
                if callable(cancel_call):
                    try:
                        _dispatch_mutation(
                            window, "CANCEL MISSION [stale-start]",
                            lambda: cancel_call(run_id, operation_id + ":cancel"),
                            targets=ids, operation_id=operation_id)
                    except Exception:
                        pass
                return
            window._mission_shadow_run_id = run_id
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
    return False


def refresh_state(window, rebuild=False):
    """Best-effort authoritative state refresh for the restartable cockpit.

    This path never calls StartMission and never sends a flight command.  The
    query runs off the Qt thread; applying the returned snapshot is marshalled
    back through window.ui_call when available.
    """
    query = getattr(getattr(window, "client", None), "get_mission_state", None)
    if not callable(query) or bool(getattr(window, "_mission_state_query_busy", False)):
        return
    window._mission_state_query_busy = True

    def worker():
        try:
            state = query()
        except Exception:
            state = None
        finally:
            window._mission_state_query_busy = False
        if state is None:
            return

        def apply():
            fn = getattr(window, "_apply_mission_core_state", None)
            if callable(fn):
                fn(state, rebuild=bool(rebuild))

        signal = getattr(window, "ui_call", None)
        emit = getattr(signal, "emit", None)
        if callable(emit):
            emit(apply)
        else:
            apply()

    threading.Thread(target=worker, daemon=True).start()


def recover(window):
    """Startup/reopen query required by F6; never starts a mission."""
    refresh_state(window, rebuild=True)


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
            _dispatch_mutation(
                window, "CANCEL MISSION",
                lambda: cancel_call(run_id, operation_id + ":cancel"),
                targets=getattr(window, "_wp_target_ids", ()) or (),
                operation_id=operation_id)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _dispatch_mutation(window, label, invoke, *, targets=(), operation_id=None):
    """Dispatch a mission mutation through GroundStation's command gateway.

    ``mission_shadow`` is also exercised by small standalone/headless harnesses
    that deliberately do not construct GroundStation.  Those compatibility
    harnesses retain the old direct call; every application path supplies
    ``_dispatch_core`` and therefore receives correlation and observability.
    Mission orchestration disables frontend dedup so the gateway cannot alter
    the authority state machine.
    """
    dispatch = getattr(window, "_dispatch_core", None)
    if callable(dispatch):
        return dispatch(
            label, invoke, source="mission", targets=targets,
            operation_id=operation_id, enforce_dedup=False)
    return invoke()
