"""
เทสต์ Battery/Link failsafe แทรก WAIT/route (spec T7 frontend)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest tests/test_waypoint_failsafe.py -q

Core มี failsafe เดิม (battery/link) เป็น source of truth · cockpit แค่ยกเลิก
route progression ฝั่งตัวเอง โดยไม่ยิงคำสั่งทับ failsafe RTL ของ Core
"""
import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import rpc  # noqa: E402
from tests.test_waypoint_ui import Base, _pump  # noqa: E402

_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"
_ALARM = rpc.telemetry_pb2.EVENT_LEVEL_ALARM
_WARN = rpc.telemetry_pb2.EVENT_LEVEL_WARN


def _event(drone_id, category, level=_ALARM, message="failsafe"):
    return types.SimpleNamespace(
        drone_id=int(drone_id), category=category, level=level, message=message)


class FailsafeBase(Base):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.win._wp_clock = lambda: self.now

    def _grouped_wait(self, seconds=300):
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)
        self.win._on_waypoint_click(14.30, 100.30)
        self.win._wp_set_wait(self.win._wp_key, 0, seconds)
        self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)   # ถึงจุดแรก → เข้า WAIT
        _pump(0.2)
        self.assertIn(0, self.win._wp_waits)

    def _flight_cmds(self):
        return [c[0] for c in self.fake.calls]


class TestBatteryFailsafeDuringWait(FailsafeBase):
    def test_wait_cancelled(self):
        self._grouped_wait()
        self.win._on_event(_event(2, "battery"))
        self.assertFalse(self.win._wp_waits, "battery ALARM ต้องยกเลิก WAIT")
        self.assertFalse(self.win._waypoint_executing)

    def test_no_next_waypoint(self):
        self._grouped_wait()
        n = len(self.win.fleet_items) and len([c for c in self.fake.calls if c[0] == "goto"])
        self.win._on_event(_event(2, "battery"))
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        gotos = [c for c in self.fake.calls if c[0] == "goto"]
        self.assertEqual(len(gotos), n, "หลัง failsafe ห้ามมี waypoint ถัดไป")

    def test_handler_injects_no_commands(self):
        """safety interrupt ห้ามยิงคำสั่งที่อาจทับ failsafe RTL ของ Core"""
        self._grouped_wait()
        before = list(self.fake.calls)
        self.win._on_event(_event(2, "battery"))
        _pump(0.3)
        new = self.fake.calls[len(before):]
        for kind in ("stop_all", "hold", "goto", "rtl", "swarm_stop", "servo_set"):
            self.assertFalse(any(c[0] == kind for c in new),
                             f"handler failsafe ห้ามส่ง {kind}")

    def test_stale_wait_callback_inert(self):
        self._grouped_wait()
        stale = dict(self.win._wp_waits[0])
        self.win._on_event(_event(2, "battery"))
        self.assertFalse(self.win._wp_wait_valid(stale),
                         "callback WAIT เก่าต้องใช้ไม่ได้หลัง failsafe")

    def test_mission_does_not_auto_resume(self):
        self._grouped_wait()
        self.win._on_event(_event(2, "battery"))
        # แม้เวลาผ่านไปนาน + telemetry ปกติ mission เก่าห้ามกลับมาเดินเอง
        self.now += 1000
        self.win._wp_wait_tick()
        _pump(1.0)
        self.assertFalse(self.win._waypoint_executing)

    def test_timeline_marked_failed(self):
        self._grouped_wait()
        self.win._on_event(_event(2, "battery"))
        run = self.win._flight_run
        self.assertIsNotNone(run)
        statuses = {s.status.name for s in run.steps}
        self.assertIn("FAILED", statuses, "timeline ต้องแสดงสถานะ failsafe (FAILED)")


class TestLinkFailsafeDuringWait(FailsafeBase):
    def test_link_alarm_cancels_wait(self):
        self._grouped_wait()
        self.win._on_event(_event(2, "link"))
        self.assertFalse(self.win._wp_waits)
        self.assertFalse(self.win._waypoint_executing)


class TestFailsafeTiming(FailsafeBase):
    def test_failsafe_one_tick_before_deadline(self):
        self._grouped_wait(seconds=120)
        self.now += 119          # ก่อน deadline 1 วินาที
        self.win._on_event(_event(2, "battery"))
        n = len([c for c in self.fake.calls if c[0] == "goto"])
        self.now += 5            # ข้าม deadline เดิม
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "goto"]), n,
                         "failsafe ก่อน deadline — ห้าม advance")

    def test_failsafe_at_exact_deadline(self):
        self._grouped_wait(seconds=120)
        self.now += 120          # ถึง deadline พอดี
        # failsafe มาก่อน tick ประมวลผล → ต้องไม่ advance
        self.win._on_event(_event(2, "battery"))
        n = len([c for c in self.fake.calls if c[0] == "goto"])
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "goto"]), n)


class TestFailsafeNonParticipant(FailsafeBase):
    def test_failsafe_of_uninvolved_drone_does_not_interrupt(self):
        self._grouped_wait()          # ภารกิจของ Drone 2
        self.win._on_event(_event(4, "battery"))   # ลำที่ไม่อยู่ในภารกิจ
        self.assertTrue(self.win._waypoint_executing,
                        "failsafe ของลำนอกภารกิจไม่ควรตัด mission")
        self.assertIn(0, self.win._wp_waits)

    def test_warn_level_does_not_interrupt(self):
        self._grouped_wait()
        self.win._on_event(_event(2, "battery", level=_WARN))
        self.assertTrue(self.win._waypoint_executing,
                        "แค่ WARN (ไม่ถึง ALARM) ไม่ตัด mission")


class TestFailsafeBeforeActionStarts(FailsafeBase):
    def test_pending_action_cancelled(self):
        """battery ALARM ตอน WAIT — action A/B ที่ยังไม่เริ่มต้องถูกยกเลิก"""
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)
        self.win._wp_set_wait(self.win._wp_key, 0, 300)
        self.win._wp_set_action(self.win._wp_key, 0, "servo_a")
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)
        _pump(0.2)
        self.win._on_event(_event(2, "battery"))
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertNotIn("servo_set", self._flight_cmds(),
                         "action ที่ยังไม่เริ่มต้องถูกยกเลิกหลัง failsafe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
