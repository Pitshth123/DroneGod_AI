"""
เทสต์ runtime ของ WAIT ราย Waypoint — cancellable state + run/generation guard
(spec T3 GROUPED, T4 SEPARATE)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest tests/test_waypoint_wait_runtime.py -q

หลักการ: ห้ามรอเวลาจริงเป็นนาที — inject monotonic clock ผ่าน win._wp_clock
แล้วเลื่อนเวลาเองก่อนเรียก _wp_wait_tick()
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from tests.test_waypoint_ui import Base, _pump  # noqa: E402

_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"


class GroupedWaitBase(Base):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.win._wp_clock = lambda: self.now

    def _build(self, waits=None, actions=None):
        """สร้าง route 3 จุดของ Drone 2; waits/actions = {index: value}"""
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)
        self.win._on_waypoint_click(14.30, 100.30)
        key = self.win._wp_key
        for idx, sec in (waits or {}).items():
            self.assertTrue(self.win._wp_set_wait(key, idx, sec))
        for idx, act in (actions or {}).items():
            self.win._wp_set_action(key, idx, act)

    def _gotos(self):
        return [(round(c[2], 2), round(c[3], 2)) for c in self.calls("goto")]

    def _advance_clock(self, secs):
        self.now += secs
        self.win._wp_wait_tick()
        _pump(0.9)


class TestGroupedNoWaitUnchanged(GroupedWaitBase):
    def test_no_wait_advances_normally(self):
        self._build()
        self.win._wp_execute()
        _pump(0.3)
        for _ in range(3):
            self.win._on_target_reached(2)
            _pump(0.9)
        self.assertEqual(self._gotos(),
                         [(14.10, 100.10), (14.20, 100.20), (14.30, 100.30)])
        self.assertFalse(self.win._waypoint_executing)
        self.assertFalse(self.win._wp_waits)


class TestGroupedWaitHolds(GroupedWaitBase):
    def setUp(self):
        super().setUp()
        self._build(waits={0: 120})   # WAIT 2 นาที ที่จุดแรก
        self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)     # ถึงจุดแรก → เข้า WAIT
        _pump(0.2)

    def test_wait_is_active_after_arrival(self):
        self.assertIn(0, self.win._wp_waits)

    def test_holds_position_during_wait(self):
        self.assertIn("hold", [c[0] for c in self.fake.calls])

    def test_does_not_advance_before_deadline(self):
        self.now += 60      # ยังไม่ถึง deadline (120s)
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(self._gotos(), [(14.10, 100.10)],
                         "WAIT ยังไม่ครบ — ห้ามไปจุดถัดไป")
        self.assertIn(0, self.win._wp_waits)

    def test_advances_after_deadline(self):
        self._advance_clock(130)   # เกิน 120s
        self.assertIn((14.20, 100.20), self._gotos(),
                      "WAIT ครบแล้วต้องไปจุดถัดไป")
        self.assertFalse(self.win._wp_waits)

    def test_status_shows_countdown(self):
        self.now += 40
        self.win._wp_wait_tick()
        self.assertIn("WAITING", self.win.lbl_wp_status.text())
        self.assertIn("remaining", self.win.lbl_wp_status.text())


class TestGroupedWaitBeforeAction(GroupedWaitBase):
    def setUp(self):
        super().setUp()
        self._build(waits={0: 120}, actions={0: "servo_a"})
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)
        _pump(0.2)

    def test_action_not_fired_during_wait(self):
        self.assertNotIn("servo_set", [c[0] for c in self.fake.calls],
                         "action A/B ต้องรอ WAIT ให้จบก่อน")

    def test_action_fires_after_wait(self):
        self._advance_clock(130)
        _pump(0.5)
        self.assertIn("servo_set", [c[0] for c in self.fake.calls],
                      "WAIT จบแล้วต้องทำ action A/B")


class TestGroupedWaitCancellable(GroupedWaitBase):
    def _enter_wait(self):
        self._build(waits={0: 300})
        self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)
        _pump(0.2)
        self.assertIn(0, self.win._wp_waits)

    def test_cancel_nav_during_wait_no_next_callback(self):
        self._enter_wait()
        self.win._cancel_navigation()
        _pump(0.3)
        n = len(self.calls("goto"))
        # เลยเวลา deadline ไปแล้ว callback เก่าห้ามยิง waypoint ถัดไป
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(len(self.calls("goto")), n,
                         "callback WAIT เก่าห้ามพา route ไปต่อหลัง Cancel Nav")
        self.assertFalse(self.win._waypoint_executing)
        self.assertFalse(self.win._wp_waits)

    def test_estop_during_wait_no_next_callback(self):
        self._enter_wait()
        self.win._do_estop([2], "Drone 2")
        _pump(0.3)
        n = len(self.calls("goto"))
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(len(self.calls("goto")), n,
                         "E-STOP ระหว่าง WAIT — ห้ามมี waypoint callback กลับมา")
        self.assertFalse(self.win._waypoint_executing)

    def test_stale_wait_callback_cannot_touch_new_mission(self):
        """WAIT ค้างของ run เก่า ต้องไม่พา mission ใหม่ให้ advance"""
        self._enter_wait()
        stale = dict(self.win._wp_waits[0])   # entry ของ run เก่า
        # ยกเลิก mission เดิม แล้วเริ่มใหม่
        self.win._cancel_navigation()
        _pump(0.3)
        self._build(waits={0: 300})
        self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(2)
        _pump(0.2)
        n = len(self.calls("goto"))
        # จำลอง callback เก่ากลับมา (generation ไม่ตรงแล้ว)
        self.assertFalse(self.win._wp_wait_valid(stale))
        self.now += 400
        # entry ปัจจุบันของ run ใหม่ยังไม่ถึง deadline — ต้องไม่ advance จาก stale
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertGreaterEqual(len(self.calls("goto")), n)


class SeparateWaitBase(Base):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.win._wp_clock = lambda: self.now
        self.win._on_fleet_toggled(True)
        self.win._wp_set_separate(True)
        self.win._wp_toggle(True)
        # D1: 2 จุด (WAIT 5 นาทีที่จุดแรก); D2: 2 จุด (ไม่มี WAIT) — คนละพื้นที่
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.11, 100.11)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.50, 100.50)
        self.win._on_waypoint_click(14.51, 100.51)
        self.win._wp_set_wait(1, 0, 300)   # SEPARATE: route_key = drone_id

    def _d(self, did):
        return [(round(c[2], 2), round(c[3], 2))
                for c in self.calls("goto") if c[1] == did]


class TestSeparateWaitIndependent(SeparateWaitBase):
    def test_d1_wait_does_not_block_d2(self):
        self.win._wp_execute()
        _pump(0.4)
        self.win._on_target_reached(1)     # D1 เข้า WAIT
        _pump(0.3)
        self.win._on_target_reached(2)     # D2 ไม่มี WAIT → เดินหน้าเอง
        _pump(0.9)
        self.assertIn(1, self.win._wp_waits, "D1 ต้องกำลัง WAIT อยู่")
        self.assertIn((14.51, 100.51), self._d(2), "D2 ต้องเดินหน้าได้แม้ D1 กำลังรอ")
        self.assertNotIn((14.11, 100.11), self._d(1),
                         "D1 กำลัง WAIT — ยังไม่ควรไปจุดถัดไป")

    def test_d1_advances_after_own_deadline(self):
        self.win._wp_execute()
        _pump(0.4)
        self.win._on_target_reached(1)
        _pump(0.3)
        self.now += 320
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertIn((14.11, 100.11), self._d(1), "WAIT ครบ — D1 ต้องไปจุดถัดไป")

    def test_cancel_invalidates_all_waits(self):
        self.win._wp_execute()
        _pump(0.4)
        self.win._on_target_reached(1)
        _pump(0.3)
        self.assertIn(1, self.win._wp_waits)
        self.win._cancel_navigation()
        _pump(0.3)
        self.assertFalse(self.win._wp_waits, "Cancel ต้องล้าง WAIT ของทุกลำ")
        n = len(self.calls("goto"))
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        self.assertEqual(len(self.calls("goto")), n,
                         "callback WAIT เก่าห้ามพา route ไปต่อหลัง Cancel")


class TestSeparateWaitLimitPerRoute(SeparateWaitBase):
    def test_each_route_has_own_five_wait_budget(self):
        # ขยาย D2 เป็น 6 จุด แล้วตั้ง WAIT ครบ 5 — ไม่ยุ่งกับโควตาของ D1
        self.win._on_fleet_click(2, False)
        for i in range(2, 6):
            self.win._on_waypoint_click(14.50 + 0.01 * i, 100.50 + 0.01 * i)
        for i in range(5):
            self.assertTrue(self.win._wp_set_wait(2, i, 60))
        self.assertFalse(self.win._wp_set_wait(2, 5, 60),
                         "จุดที่ 6 ของ D2 ต้องถูก block")
        # D1 ยังตั้ง WAIT ของตัวเองได้ (โควตาแยกกัน)
        self.assertEqual(self.win._wp_routes[1].wait_count(), 1)
        self.assertEqual(self.win._wp_routes[2].wait_count(), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
