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

from swarmgod_gui.core import rpc  # noqa: E402
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


class ShadowFakeClient(FakeClient):
    def __init__(self, *, fail_start=False, start_gate=None):
        super().__init__()
        self.fail_start = fail_start
        self.start_gate = start_gate

    def start_mission(self, plan_proto, operation_id):
        self.calls.append(("start_mission", plan_proto, str(operation_id)))
        if self.start_gate is not None:
            self.start_gate.wait(timeout=2.0)
        if self.fail_start:
            raise RuntimeError("shadow core unavailable")
        return types.SimpleNamespace(ok=True, run_id=77)

    def cancel_mission(self, run_id, request_id=None):
        self.calls.append(("cancel_mission", int(run_id), str(request_id or "")))
        return types.SimpleNamespace(ok=True)

    def get_mission_state(self):
        self.calls.append(("get_mission_state",))
        return types.SimpleNamespace(active=False)


class ShadowBase(Base):
    def setUp(self):
        super().setUp()
        self._old_shadow_env = os.environ.get("SWARMGOD_MISSION_SHADOW")
        os.environ["SWARMGOD_MISSION_SHADOW"] = "1"
        self.fake = ShadowFakeClient()
        self.win.client = self.fake

    def tearDown(self):
        if self._old_shadow_env is None:
            os.environ.pop("SWARMGOD_MISSION_SHADOW", None)
        else:
            os.environ["SWARMGOD_MISSION_SHADOW"] = self._old_shadow_env
        super().tearDown()

    def _build_grouped(self):
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)
        self.assertTrue(self.win._wp_set_wait(self.win._wp_key, 0, 60))

    def _calls(self, kind):
        return [c for c in self.fake.calls if c[0] == kind]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
