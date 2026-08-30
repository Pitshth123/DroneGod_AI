"""F4 Stage A2 — Python submits the current waypoint plan to Go SHADOW only.

The legacy Python waypoint executor remains flight authority in this phase.  These
checks make sure shadow Start/Cancel is additive, best-effort, and cannot suppress
or duplicate the existing GOTO command path.
"""
import os
import sys
import threading
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import mission_shadow, rpc, waypoint_logic  # noqa: E402
from swarmgod_gui.core.grpc_client import CoreClient  # noqa: E402
from tests.test_ui_selection import FakeClient  # noqa: E402
from tests.test_waypoint_ui import Base, _pump  # noqa: E402


class _MissionStub:
    def __init__(self):
        self.calls = []

    def StartMission(self, req, timeout=None):
        self.calls.append(("start", req, timeout))
        return types.SimpleNamespace(ok=True, run_id=11)

    def CancelMission(self, req, timeout=None):
        self.calls.append(("cancel", req, timeout))
        return types.SimpleNamespace(ok=True)

    def GetMissionState(self, req, timeout=None):
        self.calls.append(("state", req, timeout))
        return types.SimpleNamespace(active=False)


class TestCoreClientMissionBoundary(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(CoreClient)
        self.client.stub = _MissionStub()

    def test_start_cancel_query_build_expected_requests(self):
        pb = rpc.mission_pb2
        plan = pb.MissionPlan(
            plan_id="plan-1", participants=[2],
            routes=[pb.MissionRoute(
                drone_id=0,
                points=[pb.MissionWaypoint(seq=0, lat=14.1, lon=100.1, alt=20.0)])])
        self.client.start_mission(plan, "op-1")
        self.client.cancel_mission(11, "cancel-1")
        self.client.get_mission_state()

        start = self.client.stub.calls[0]
        self.assertEqual(start[0], "start")
        self.assertEqual(start[1].operation_id, "op-1")
        self.assertEqual(start[1].plan.plan_id, "plan-1")
        self.assertEqual(start[2], 10)

        cancel = self.client.stub.calls[1]
        self.assertEqual(cancel[0], "cancel")
        self.assertEqual(cancel[1].run_id, 11)
        self.assertEqual(cancel[1].request_id, "cancel-1")
        self.assertEqual(cancel[2], 10)

        state = self.client.stub.calls[2]
        self.assertEqual(state[0], "state")
        self.assertEqual(state[2], 5)


class TestMissionShadowPlanCompatibility(unittest.TestCase):
    def test_grouped_plan_freezes_per_drone_altitudes(self):
        route = waypoint_logic.WaypointRoute([1, 2])
        route.add(14.10, 100.10)
        window = types.SimpleNamespace(
            _wp_separate=False,
            _waypoint_route=route,
            _last_alt={1: 18.5, 2: 27.0},
            _alt_for=lambda did: 20.0,
        )
        plan = mission_shadow.build_plan(
            window, {1: route, 2: route}, [1, 2], 0, "op-alt")

        self.assertEqual(dict(plan.participant_altitudes), {1: 18.5, 2: 27.0})
        # Shared waypoint alt remains compatibility metadata only; Stage B must
        # resolve command altitude by participant_altitudes, not collapse here.
        self.assertAlmostEqual(plan.routes[0].points[0].alt, 18.5)


class ShadowFakeClient(FakeClient):
    def __init__(self, *, fail_start=False, start_gate=None,
                 authority_active=False, state_override=None):
        super().__init__()
        self.fail_start = fail_start
        self.start_gate = start_gate
        self.authority_active = authority_active
        self.state_override = state_override

    def start_mission(self, plan_proto, operation_id):
        self.calls.append(("start_mission", plan_proto, str(operation_id)))
        if self.start_gate is not None:
            self.start_gate.wait(timeout=2.0)
        if self.fail_start:
            raise RuntimeError("shadow core unavailable")
        return types.SimpleNamespace(
            ok=True, run_id=77, authority_active=self.authority_active)

    def cancel_mission(self, run_id, request_id=None):
        self.calls.append(("cancel_mission", int(run_id), str(request_id or "")))
        return types.SimpleNamespace(ok=True)

    def get_mission_state(self):
        self.calls.append(("get_mission_state",))
        if self.state_override is not None:
            return self.state_override
        return types.SimpleNamespace(active=False, authority_active=False,
                                     plan_id="", run_id=0)


class ShadowBase(Base):
    def setUp(self):
        super().setUp()
        self._old_shadow_env = os.environ.get("SWARMGOD_MISSION_SHADOW")
        self._old_authority_env = os.environ.get("SWARMGOD_MISSION_AUTHORITY")
        os.environ["SWARMGOD_MISSION_SHADOW"] = "1"
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "0"
        self.fake = ShadowFakeClient()
        self.win.client = self.fake

    def tearDown(self):
        if self._old_shadow_env is None:
            os.environ.pop("SWARMGOD_MISSION_SHADOW", None)
        else:
            os.environ["SWARMGOD_MISSION_SHADOW"] = self._old_shadow_env
        if self._old_authority_env is None:
            os.environ.pop("SWARMGOD_MISSION_AUTHORITY", None)
        else:
            os.environ["SWARMGOD_MISSION_AUTHORITY"] = self._old_authority_env
        super().tearDown()

    def _build_grouped(self):
        self._build_plain_grouped()
        self.assertTrue(self.win._wp_set_wait(self.win._wp_key, 0, 60))

    def _build_plain_grouped(self):
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)

    def _calls(self, kind):
        return [c for c in self.fake.calls if c[0] == kind]

    def _start_core_plain_authority(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._wp_execute()
        _pump(0.2)
        self.assertTrue(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(self._calls("goto"), [])


class TestAuthorityOwnershipSelector(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("SWARMGOD_MISSION_AUTHORITY")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SWARMGOD_MISSION_AUTHORITY", None)
        else:
            os.environ["SWARMGOD_MISSION_AUTHORITY"] = self._old

    @staticmethod
    def _window(route, *, separate=False):
        return types.SimpleNamespace(_wp_separate=separate, _waypoint_route=route)

    def test_core_single_is_only_plain_single_grouped(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        route = waypoint_logic.WaypointRoute([1])
        route.add(14.1, 100.1)
        self.assertTrue(mission_shadow.authority_eligible(
            self._window(route), {1: route}, [1], 0))

        self.assertFalse(mission_shadow.authority_eligible(
            self._window(route), {1: route, 2: route}, [1, 2], 0),
            "multi-drone GROUPED must stay on the proven legacy executor")
        self.assertFalse(mission_shadow.authority_eligible(
            self._window(route, separate=True), {1: route}, [1], 0),
            "SEPARATE is not in the Core authority scope yet")
        self.assertFalse(mission_shadow.authority_eligible(
            self._window(route), {1: route}, [1], 1),
            "SWARM leader path is not in the Core authority scope yet")

    def test_wait_and_payload_select_legacy_until_supported(self):
        route = waypoint_logic.WaypointRoute([1])
        route.add(14.1, 100.1, wait_seconds=60)
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.assertFalse(mission_shadow.authority_eligible(
            self._window(route), {1: route}, [1], 0))

        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.assertTrue(mission_shadow.authority_eligible(
            self._window(route), {1: route}, [1], 0))
        route.points[0].action = "servo_a"
        self.assertFalse(mission_shadow.authority_eligible(
            self._window(route), {1: route}, [1], 0),
            "payload A/B remains Python-owned until payload authority is migrated")


class TestMissionShadowSubmit(ShadowBase):
    def test_execute_submits_plan_once_and_legacy_goto_still_runs(self):
        self._build_grouped()
        self.win._wp_execute()
        _pump(0.4)

        starts = self._calls("start_mission")
        self.assertEqual(len(starts), 1)
        plan, operation_id = starts[0][1], starts[0][2]
        self.assertEqual(plan.plan_id, operation_id)
        self.assertEqual(plan.mode, rpc.mission_pb2.MISSION_MODE_GROUPED)
        self.assertEqual(list(plan.participants), [2])
        self.assertEqual(len(plan.routes), 1)
        self.assertEqual(plan.routes[0].drone_id, 0)
        self.assertEqual(len(plan.routes[0].points), 2)
        self.assertEqual(plan.routes[0].points[0].wait_seconds, 60)
        self.assertAlmostEqual(plan.routes[0].points[0].alt, 20.0)

        # A2 is shadow-only: Python still sends exactly the first legacy GOTO.
        gotos = self._calls("goto")
        self.assertEqual(len(gotos), 1)
        self.assertEqual(gotos[0][1], 2)
        self.assertEqual((round(gotos[0][2], 2), round(gotos[0][3], 2)),
                         (14.10, 100.10))
        self.assertEqual(getattr(self.win, "_mission_shadow_run_id", 0), 77)

    def test_shadow_start_failure_does_not_block_python_executor(self):
        self.fake = ShadowFakeClient(fail_start=True)
        self.win.client = self.fake
        self._build_grouped()
        self.win._wp_execute()
        _pump(0.4)

        self.assertEqual(len(self._calls("start_mission")), 1)
        self.assertEqual(len(self._calls("goto")), 1,
                         "shadow RPC failure must not suppress legacy waypoint GOTO")
        self.assertTrue(self.win._waypoint_executing)

    def test_cancel_nav_best_effort_cancels_known_shadow_run(self):
        self._build_grouped()
        self.win._wp_execute()
        _pump(0.4)
        self.assertEqual(getattr(self.win, "_mission_shadow_run_id", 0), 77)

        self.win._cancel_navigation()
        _pump(0.4)
        cancels = self._calls("cancel_mission")
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0][1], 77)
        self.assertFalse(self.win._waypoint_executing)

    def test_cancel_racing_slow_start_is_closed_when_start_returns(self):
        gate = threading.Event()
        self.fake = ShadowFakeClient(start_gate=gate)
        self.win.client = self.fake
        self._build_grouped()
        self.win._wp_execute()
        _pump(0.1)
        self.assertEqual(len(self._calls("start_mission")), 1)

        # Cancel before StartMission has returned a Core run_id.
        self.win._cancel_navigation()
        gate.set()
        _pump(0.5)

        cancels = self._calls("cancel_mission")
        self.assertEqual(len(cancels), 1,
                         "late Start response must be cancelled instead of leaving a stale shadow run")
        self.assertEqual(cancels[0][1], 77)
        self.assertFalse(self.win._waypoint_executing)

    def test_shadow_flag_off_leaves_legacy_execution_untouched(self):
        os.environ["SWARMGOD_MISSION_SHADOW"] = "0"
        self._build_grouped()
        self.win._wp_execute()
        _pump(0.4)
        self.assertEqual(self._calls("start_mission"), [])
        self.assertEqual(len(self._calls("goto")), 1)

    def test_legacy_plan_is_blocked_if_core_already_has_active_authority_run(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(
            authority_active=True,
            state_override=types.SimpleNamespace(
                active=True, authority_active=True, plan_id="other-core-run", run_id=900))
        self.win.client = self.fake
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)

        self.win._wp_execute()
        _pump(0.3)

        self.assertEqual(self._calls("start_mission"), [])
        self.assertGreaterEqual(len(self._calls("get_mission_state")), 1)
        self.assertEqual(self._calls("goto"), [],
                         "legacy executor must not overlap an active Core-owned run")
        self.assertFalse(self.win._waypoint_executing)

    def test_core_active_blocks_auto_takeoff_before_any_mission_prep(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(
            authority_active=True,
            state_override=types.SimpleNamespace(
                active=True, authority_active=True, plan_id="other-core-run", run_id=901))
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._last_telem[2].armed = False

        self.win._wp_execute()
        _pump(0.3)

        self.assertGreaterEqual(len(self._calls("get_mission_state")), 1)
        self.assertEqual(self._calls("takeoff"), [],
                         "Core slot must be checked before auto-TAKEOFF")
        self.assertEqual(self._calls("start_mission"), [])
        self.assertEqual(self._calls("goto"), [])
        self.assertFalse(self.win._waypoint_executing)

    def test_core_idle_preserves_legacy_auto_takeoff_prep(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(
            authority_active=True,
            state_override=types.SimpleNamespace(
                active=False, authority_active=False, plan_id="", run_id=0))
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._last_telem[2].armed = False

        # auto=True models the already-confirmed Tablet path and avoids opening
        # a native confirmation dialog in the headless test process.
        self.win._wp_execute(auto=True)
        _pump(0.4)

        self.assertGreaterEqual(len(self._calls("get_mission_state")), 1)
        self.assertGreaterEqual(len(self._calls("takeoff")), 1,
                                "idle Core slot must not break the old auto-TAKEOFF gate")
        self.assertEqual(self._calls("goto"), [],
                         "route GOTO still waits for airborne telemetry confirmation")

    def test_legacy_plan_is_blocked_if_core_slot_cannot_be_queried(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        def fail_query():
            self.fake.calls.append(("get_mission_state",))
            raise RuntimeError("Core query unavailable")
        self.fake.get_mission_state = fail_query
        self.win.client = self.fake
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)

        self.win._wp_execute()
        _pump(0.3)

        self.assertEqual(self._calls("start_mission"), [])
        self.assertEqual(self._calls("goto"), [])
        self.assertFalse(self.win._waypoint_executing)

    def test_core_token_keeps_multi_grouped_on_legacy_executor(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)

        self.win._wp_execute()
        _pump(0.5)

        self.assertEqual(self._calls("start_mission"), [],
                         "unsupported multi-GROUPED plan must not enter Core authority RPC")
        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        gotos = self._calls("goto")
        self.assertEqual(len(gotos), 2,
                         "legacy GROUPED executor must remain available until Go ports formation geometry")
        self.assertEqual({c[1] for c in gotos}, {1, 2})

    def test_core_token_multi_grouped_preserves_per_drone_altitude(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self.win._last_alt[1] = 18.5
        self.win._last_alt[2] = 27.0
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)

        self.win._wp_execute()
        _pump(0.5)

        gotos = {c[1]: c for c in self._calls("goto")}
        self.assertEqual(set(gotos), {1, 2})
        self.assertAlmostEqual(gotos[1][4], 18.5)
        self.assertAlmostEqual(gotos[2][4], 27.0)

    def test_core_token_keeps_separate_on_legacy_executor(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self.win._wp_set_separate(True)
        self.win._wp_toggle(True)
        # Put the routes on different altitude layers so the legacy collision
        # gate deliberately allows both independent routes.
        self.win._last_alt[1] = 20.0
        self.win._last_alt[2] = 30.0
        self.win._drone_alt[1] = 20.0
        self.win._drone_alt[2] = 30.0
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.50, 100.50)

        self.win._wp_execute()
        _pump(0.4)

        self.assertEqual(self._calls("start_mission"), [])
        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(sorted(c[1] for c in self._calls("goto")), [1, 2],
                         "SEPARATE must keep the old independent Python executor")

    def test_core_token_keeps_swarm_leader_path_on_legacy_executor(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self.win._swarm_active = True
        self.win._head_id = 1
        self.win._leader_id = 1
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)

        self.win._wp_execute()
        _pump(0.3)

        self.assertEqual(self._calls("start_mission"), [])
        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        gotos = self._calls("goto")
        self.assertEqual(len(gotos), 1)
        self.assertEqual(gotos[0][1], 1,
                         "SWARM leader path must keep commanding only the old Head")

    def test_core_token_keeps_payload_action_on_legacy_executor(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._waypoint_route.points[0].action = "servo_a"

        # Tablet/auto path means payload confirmation was already acknowledged;
        # this test is only about authority ownership, not the confirmation UI.
        self.win._wp_execute(auto=True)
        _pump(0.3)

        self.assertEqual(self._calls("start_mission"), [],
                         "payload mission must remain legacy-owned until action authority migrates")
        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(len(self._calls("goto")), 1)
        self.win._on_target_reached(2)
        _pump(0.4)
        self.assertTrue(self._calls("hold"),
                        "legacy payload action must still HOLD before servo release")
        self.assertTrue(any(c[0] == "servo_set" and c[1] == 2 and c[2] == 7
                            for c in self.fake.calls),
                        "legacy payload A must still drive CH7 under a Core token")

    def test_core_single_token_keeps_wait_plan_on_legacy_executor(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self._build_grouped()

        self.win._wp_execute()
        _pump(0.3)

        self.assertEqual(self._calls("start_mission"), [])
        self.assertEqual(len(self._calls("goto")), 1)
        self.assertTrue(self.win._waypoint_executing)
        self.win._on_target_reached(2)
        _pump(0.3)
        self.assertTrue(self._calls("hold"),
                        "WAIT under core-single must retain the legacy Python HOLD path")
        self.assertIn(0, self.win._wp_waits,
                      "legacy GROUPED WAIT scope must remain active until its Python deadline")

    def test_core_single_wait_token_owns_wait_plan(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self._build_grouped()

        self.win._wp_execute()
        _pump(0.2)

        self.assertEqual(len(self._calls("start_mission")), 1)
        self.assertTrue(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(self._calls("goto"), [])

    def test_authority_confirmed_disables_python_goto_and_browser_progression(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(authority_active=True)
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._wp_execute()
        _pump(0.2)

        self.assertEqual(len(self._calls("start_mission")), 1)
        self.assertTrue(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(self._calls("goto"), [],
                         "Core authority must suppress the initial Python GOTO")
        before = self.win._wp_current_index
        self.win._on_target_reached(2)
        _pump(0.1)
        self.assertEqual(self.win._wp_current_index, before,
                         "browser arrival must not drive Python progression under Core authority")
        self.assertEqual(self._calls("goto"), [])

    def test_manual_goto_entrypoints_block_while_core_owns_mission(self):
        self._start_core_plain_authority()

        self.win._goto_armed = True
        self.win._on_map_click(14.30, 100.30)
        _pump(0.1)
        self.assertEqual(self._calls("goto"), [],
                         "cockpit map GOTO must not overlap active Core authority")

        ok, msg = self.win._web_goto({"lat": 14.31, "lon": 100.31})
        self.assertFalse(ok)
        self.assertIn("Core mission", msg)
        self.assertEqual(self._calls("goto"), [],
                         "Field Tablet GOTO must not overlap active Core authority")

        # Defense against a stale delayed callback queued before authority was acquired.
        self.win._goto_one(2, 14.32, 100.32, 20.0)
        _pump(0.1)
        self.assertEqual(self._calls("goto"), [],
                         "stale _goto_one callback must fail closed under Core authority")
        self.assertTrue(self.win._mission_core_authority)

    def test_manual_move_blocks_while_core_owns_mission(self):
        self._start_core_plain_authority()
        before = len(self._calls("rc_move"))
        ok, msg = self.win._web_move({"dir": "FWD", "speed": 1.0})
        _pump(0.1)
        self.assertFalse(ok)
        self.assertIn("Core mission", msg)
        self.assertEqual(len(self._calls("rc_move")), before,
                         "Field Tablet RC movement must not overlap Core authority")
        self.assertIsNone(getattr(self.win, "_rc_dir", None))

    def test_arm_takeoff_and_swarm_start_block_while_core_owns_mission(self):
        self._start_core_plain_authority()
        self.win._cmd_arm()
        self.win._cmd_takeoff()
        self.win._swarm_start()
        self.win._card_arm(2)
        _pump(0.2)

        self.assertEqual(self._calls("arm"), [],
                         "ARM entrypoints must not overlap active Core authority")
        self.assertEqual(self._calls("takeoff"), [],
                         "TAKEOFF must not overlap active Core authority")
        self.assertEqual(self._calls("swarm_start"), [],
                         "Swarm formation must not start over a Core-owned mission")
        self.assertEqual(self._calls("swarm_config"), [],
                         "blocked Swarm START must not even mutate formation config")
        self.assertTrue(self.win._mission_core_authority)

    def test_disarm_is_explicit_operator_takeover(self):
        self._start_core_plain_authority()
        self.win._confirm = lambda *args, **kwargs: True
        self.win._cmd_disarm()
        _pump(0.4)

        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        self.assertFalse(self.win._waypoint_executing)
        self.assertEqual(len(self._calls("cancel_mission")), 1,
                         "DISARM takeover must cancel the Core mission first")
        self.assertTrue(self._calls("disarm"),
                        "DISARM must still be sent after Core ownership is cancelled")

    def test_quick_hold_is_explicit_operator_takeover(self):
        self._start_core_plain_authority()
        self.win._cmd_hold()
        _pump(0.4)

        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        self.assertFalse(self.win._waypoint_executing)
        self.assertEqual(len(self._calls("cancel_mission")), 1,
                         "HOLD takeover must cancel the Core mission first")
        self.assertTrue(self._calls("stop_all") or self._calls("hold"),
                        "HOLD takeover must issue a stopping command after cancellation")

    def test_selected_card_hold_is_explicit_operator_takeover(self):
        self._start_core_plain_authority()
        self.win._card_hold(2)
        _pump(0.4)

        self.assertFalse(getattr(self.win, "_mission_core_authority", False))
        self.assertFalse(self.win._waypoint_executing)
        self.assertEqual(len(self._calls("cancel_mission")), 1,
                         "Selected Drone HOLD must cancel the owned Core run first")
        self.assertTrue(self._calls("hold"),
                        "Selected Drone HOLD must be sent after Core cancellation")

    def test_land_rtl_and_goalt_are_explicit_operator_takeovers(self):
        cases = (
            ("LAND", lambda: self.win._cmd_land(), "land"),
            ("RTL", lambda: self.win._cmd_rtl(), "rtl"),
            ("GO ALT", lambda: self.win._cmd_goalt(), "change_alt"),
        )
        for label, action, call_kind in cases:
            with self.subTest(label=label):
                self._start_core_plain_authority()
                action()
                _pump(0.4)
                self.assertFalse(getattr(self.win, "_mission_core_authority", False))
                self.assertFalse(self.win._waypoint_executing)
                self.assertEqual(len(self._calls("cancel_mission")), 1,
                                 f"{label} must cancel Core mission before takeover")
                self.assertTrue(self._calls(call_kind),
                                f"{label} command must be sent after cancellation")

    def test_mode_change_is_blocked_while_core_owns_mission(self):
        self._start_core_plain_authority()
        before = len(self._calls("set_mode"))
        self.win._cmd_mode("FLIGHT_MODE_LOITER")
        _pump(0.1)
        self.assertEqual(len(self._calls("set_mode")), before,
                         "cockpit MODE must not invalidate active Core mission")
        self.assertTrue(self.win._mission_core_authority)

        ok, msg = self.win._web_set_mode({"mode": "LOITER"})
        self.assertFalse(ok)
        self.assertIn("Core mission", msg)
        self.assertEqual(len(self._calls("set_mode")), before,
                         "Field Tablet MODE must not invalidate active Core mission")

        self.win._leader_mode("LOITER")
        _pump(0.1)
        self.assertEqual(len(self._calls("set_mode")), before,
                         "LEADER mode toggle must not invalidate active Core mission")
        self.assertTrue(self.win._mission_core_authority)

    def test_rc_repeat_timer_drops_stale_move_when_core_takes_authority(self):
        # Model a local RC repeat that was already armed before a Core run became
        # authoritative; _rc_tick must stop locally without emitting another move.
        self.win._rc_dir = rpc.command_pb2.RC_DIR_FWD
        self.win._mission_core_authority = True
        before = len(self._calls("rc_move"))
        self.win._rc_tick()
        _pump(0.1)
        self.assertEqual(len(self._calls("rc_move")), before)
        self.assertIsNone(self.win._rc_dir)
        self.assertFalse(self.win._rc_timer.isActive())

    def test_authority_not_confirmed_fails_closed_without_legacy_fallback(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(authority_active=False)
        self.win.client = self.fake
        self._build_plain_grouped()
        self.win._wp_execute()
        _pump(0.2)

        self.assertEqual(len(self._calls("start_mission")), 1)
        self.assertEqual(self._calls("goto"), [])
        self.assertFalse(self.win._waypoint_executing)
        self.assertFalse(getattr(self.win, "_mission_core_authority", False))

    def test_restart_rebinds_active_core_run_without_start_or_goto(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single-wait"
        pb = rpc.mission_pb2
        plan = pb.MissionPlan(
            plan_id="persisted-plan", mode=pb.MISSION_MODE_GROUPED,
            participants=[2],
            routes=[pb.MissionRoute(drone_id=0, points=[
                pb.MissionWaypoint(seq=0, lat=14.10, lon=100.10, alt=20),
                pb.MissionWaypoint(seq=1, lat=14.20, lon=100.20, alt=20,
                                   wait_seconds=60),
            ])])
        self.fake = ShadowFakeClient(state_override=pb.MissionStateResponse(
            active=True, authority_active=True, run_id=501,
            plan_id="persisted-plan", mode=pb.MISSION_MODE_GROUPED,
            state=pb.MISSION_STATE_RUNNING, participants=[2], current_index=1,
            plan=plan))
        self.win.client = self.fake

        mission_shadow.recover(self.win)
        _pump(0.3)

        self.assertTrue(self.win._mission_core_authority)
        self.assertTrue(self.win._waypoint_executing)
        self.assertEqual(self.win._mission_shadow_run_id, 501)
        self.assertEqual(self.win._wp_current_index, 1)
        self.assertEqual(self.win._wp_target_ids, [2])
        self.assertIsNotNone(self.win._waypoint_route)
        self.assertEqual(len(self.win._waypoint_route.points), 2)
        self.assertEqual(self.win._waypoint_route.points[1].wait_seconds, 60)
        self.assertEqual(self._calls("start_mission"), [],
                         "restart query must never StartMission")
        self.assertEqual(self._calls("goto"), [],
                         "restart/rebuild must never send Python GOTO")

    def _assert_recovery_emitted_no_navigation(self):
        for kind in ("start_mission", "goto", "hold", "servo_set",
                     "servo_release", "swarm_start", "swarm_stop"):
            self.assertEqual(self._calls(kind), [],
                             f"recovery must emit zero {kind} commands")

    def test_restart_rebuilds_separate_routes_and_indices_without_commands(self):
        pb = rpc.mission_pb2
        plan = pb.MissionPlan(
            plan_id="persisted-separate", mode=pb.MISSION_MODE_SEPARATE,
            participants=[1, 2],
            routes=[
                pb.MissionRoute(drone_id=1, points=[
                    pb.MissionWaypoint(seq=0, lat=14.1, lon=100.1, alt=20),
                    pb.MissionWaypoint(seq=1, lat=14.2, lon=100.2, alt=20)]),
                pb.MissionRoute(drone_id=2, points=[
                    pb.MissionWaypoint(seq=0, lat=15.1, lon=101.1, alt=25),
                    pb.MissionWaypoint(seq=1, lat=15.2, lon=101.2, alt=25)]),
            ])
        self.fake = ShadowFakeClient(state_override=pb.MissionStateResponse(
            active=True, authority_active=True, run_id=601,
            plan_id=plan.plan_id, mode=pb.MISSION_MODE_SEPARATE,
            state=pb.MISSION_STATE_RUNNING, participants=[1, 2],
            sep_index={1: 1, 2: 0}, plan=plan))
        self.win.client = self.fake
        mission_shadow.recover(self.win)
        _pump(0.3)
        self.assertTrue(self.win._wp_separate)
        self.assertEqual(sorted(self.win._wp_routes), [1, 2])
        self.assertEqual(self.win._wp_sep_index, {1: 1, 2: 0})
        self.assertEqual(len(self.win._wp_routes[1].points), 2)
        self.assertEqual(len(self.win._wp_routes[2].points), 2)
        self._assert_recovery_emitted_no_navigation()

    def test_restart_rebuilds_swarm_leader_ownership_without_starting_swarm(self):
        pb = rpc.mission_pb2
        plan = pb.MissionPlan(
            plan_id="persisted-swl", mode=pb.MISSION_MODE_SWARM_LEADER,
            participants=[1, 2, 3], leader_id=2,
            routes=[pb.MissionRoute(drone_id=0, points=[
                pb.MissionWaypoint(seq=0, lat=14.1, lon=100.1, alt=20),
                pb.MissionWaypoint(seq=1, lat=14.2, lon=100.2, alt=20)])])
        self.fake = ShadowFakeClient(state_override=pb.MissionStateResponse(
            active=True, authority_active=True, run_id=701,
            plan_id=plan.plan_id, mode=pb.MISSION_MODE_SWARM_LEADER,
            state=pb.MISSION_STATE_RUNNING, participants=[1, 2, 3],
            current_index=1, plan=plan))
        self.win.client = self.fake
        mission_shadow.recover(self.win)
        _pump(0.3)
        self.assertFalse(self.win._wp_separate)
        self.assertEqual(self.win._head_id, 2)
        self.assertEqual(self.win._mission_core_ownership["mission_owned"], [2])
        self.assertEqual(self.win._mission_core_ownership["swarm_owned"], [1, 3])
        self.assertEqual(len(self.win._waypoint_route.points), 2)
        self._assert_recovery_emitted_no_navigation()

    def test_terminal_reconnect_never_reconstructs_or_emits_navigation(self):
        pb = rpc.mission_pb2
        self.win._mission_shadow_run_id = 801
        self.win._mission_core_authority = True
        self.win._waypoint_executing = True
        self.fake = ShadowFakeClient(state_override=pb.MissionStateResponse(
            active=False, authority_active=False, run_id=801,
            plan_id="terminal", mode=pb.MISSION_MODE_GROUPED,
            state=pb.MISSION_STATE_CANCELLED, terminal_reason="operator cancel"))
        self.win.client = self.fake
        mission_shadow.recover(self.win)
        _pump(0.3)
        self.assertFalse(self.win._mission_core_authority)
        self.assertFalse(self.win._waypoint_executing)
        self._assert_recovery_emitted_no_navigation()

    def test_authority_lost_start_reply_recovers_by_query_without_python_goto(self):
        os.environ["SWARMGOD_MISSION_AUTHORITY"] = "core-single"
        self.fake = ShadowFakeClient(fail_start=True)
        self.win.client = self.fake
        self._build_plain_grouped()
        # operation id is created by _wp_execute; state query needs the same plan
        # id, so the fake resolves it dynamically from the recorded Start call.
        original_state = self.fake.get_mission_state
        def state_after_lost_reply():
            self.fake.calls.append(("get_mission_state",))
            starts = [c for c in self.fake.calls if c[0] == "start_mission"]
            if not starts:
                # New no-overlap contract: Query once before auto-TAKEOFF/Start.
                return types.SimpleNamespace(active=False, authority_active=False,
                                             plan_id="", run_id=0)
            op = starts[-1][2]
            return types.SimpleNamespace(active=True, authority_active=True,
                                         plan_id=op, run_id=91)
        self.fake.get_mission_state = state_after_lost_reply

        self.win._wp_execute()
        _pump(0.2)
        self.assertTrue(getattr(self.win, "_mission_core_authority", False))
        self.assertEqual(getattr(self.win, "_mission_shadow_run_id", 0), 91)
        self.assertEqual(self._calls("goto"), [])
        self.assertGreaterEqual(len(self._calls("get_mission_state")), 1)
        self.fake.get_mission_state = original_state


if __name__ == "__main__":
    unittest.main(verbosity=2)
