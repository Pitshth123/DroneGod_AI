"""
เทสต์ WAIT ราย Waypoint — context menu / popup / render (spec T2)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest tests/test_waypoint_wait.py -q

ครอบคลุม:
  - เมนูคลิกขวามี WAIT (ตั้งเวลา / ยกเลิก) แยกจาก A/B เดิม
  - popup กรอก 1–10 นาที; cancel ไม่แก้ state
  - marker/side-label แสดง WAIT
  - จุดที่ 6 ถูก block; แก้จุดเดิมได้แม้ครบ 5
  - clear WAIT ล้าง marker metadata
  - ระหว่าง EXECUTE ห้ามแก้ WAIT
  - A/B เดิมไม่ regression (WAIT อยู่ร่วมกับ action ได้)
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

from PyQt5.QtWidgets import QMenu  # noqa: E402

from swarmgod_gui.core import waypoint_logic as WP  # noqa: E402
from tests.test_waypoint_ui import Base, _pump  # noqa: E402

_GETINT = "swarmgod_gui.app.QInputDialog.getInt"


class WaitBase(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        for i in range(3):
            self.win._on_waypoint_click(14.0 + 0.01 * i, 100.0 + 0.01 * i)
        self.js_calls.clear()
        self.route_key = self.win._wp_key   # GROUPED → 0

    def _submenu_texts(self):
        menu = QMenu(self.win)
        wp = self.win._waypoint_route.points[0]
        self.win._wp_action_submenu(menu, self.route_key, wp)
        return [a.text() for a in menu.actions()]


class TestWaitMenu(WaitBase):
    def test_menu_has_wait_entry(self):
        texts = self._submenu_texts()
        self.assertTrue(any("รอ" in t for t in texts),
                        "เมนูคลิกขวาต้องมีรายการ WAIT")

    def test_menu_still_has_action_entries(self):
        """A/B เดิมต้องยังอยู่ในเมนู (ไม่ regression)"""
        texts = self._submenu_texts()
        self.assertTrue(any("A (CH7)" in t for t in texts))
        self.assertTrue(any("B (CH8)" in t for t in texts))

    def test_menu_shows_current_wait_and_clear_when_set(self):
        self.win._wp_set_wait(self.route_key, 0, 180)
        texts = self._submenu_texts()
        self.assertTrue(any("3" in t and "นาที" in t for t in texts))
        self.assertTrue(any("ยกเลิก WAIT" in t for t in texts))

    def test_menu_hides_clear_when_no_wait(self):
        texts = self._submenu_texts()
        self.assertFalse(any("ยกเลิก WAIT" in t for t in texts))


class TestWaitPopup(WaitBase):
    def test_popup_bounds_one_to_ten_minutes(self):
        captured = {}

        def fake_getint(parent, title, label, value, minv, maxv, step):
            captured.update(min=minv, max=maxv, value=value)
            return (5, True)

        with mock.patch(_GETINT, side_effect=fake_getint):
            self.win._wp_prompt_wait(self.route_key, 0)
        self.assertEqual(captured["min"], 1)
        self.assertEqual(captured["max"], 10)
        self.assertEqual(self.win._waypoint_route.points[0].wait_seconds, 300)

    def test_popup_cancel_does_not_change_state(self):
        with mock.patch(_GETINT, return_value=(7, False)):
            self.win._wp_prompt_wait(self.route_key, 0)
        self.assertEqual(self.win._waypoint_route.points[0].wait_seconds, 0)
        self.assertEqual(self.js_matching("setWaypointWait("), [])

    def test_popup_default_is_current_when_set(self):
        self.win._wp_set_wait(self.route_key, 0, 240)
        captured = {}

        def fake_getint(parent, title, label, value, minv, maxv, step):
            captured["value"] = value
            return (value, True)

        with mock.patch(_GETINT, side_effect=fake_getint):
            self.win._wp_prompt_wait(self.route_key, 0)
        self.assertEqual(captured["value"], 4)


class TestWaitRender(WaitBase):
    def test_set_wait_updates_marker(self):
        self.win._wp_set_wait(self.route_key, 0, 180)
        calls = self.js_matching("setWaypointWait(")
        self.assertTrue(calls)
        self.assertIn("180", calls[-1])

    def test_side_label_shows_wait(self):
        self.win._wp_set_wait(self.route_key, 1, 300)
        self.assertIn("WAIT 5m", self.win.lbl_wp_points.text())

    def test_clear_wait_updates_marker_and_label(self):
        self.win._wp_set_wait(self.route_key, 0, 180)
        self.js_calls.clear()
        self.win._wp_clear_wait(self.route_key, 0)
        calls = self.js_matching("setWaypointWait(")
        self.assertTrue(calls)
        self.assertIn(", 0)", calls[-1])
        self.assertNotIn("WAIT", self.win.lbl_wp_points.text())


class TestWaitLimit(WaitBase):
    def setUp(self):
        super().setUp()
        # ขยายเป็น 6 จุด
        for i in range(3, 6):
            self.win._on_waypoint_click(14.0 + 0.01 * i, 100.0 + 0.01 * i)

    def test_sixth_wait_is_blocked(self):
        for i in range(5):
            self.assertTrue(self.win._wp_set_wait(self.route_key, i, 60))
        blocked = self.win._wp_set_wait(self.route_key, 5, 60)
        self.assertFalse(blocked)
        self.assertEqual(self.win._waypoint_route.wait_count(), 5)

    def test_edit_existing_allowed_when_full(self):
        for i in range(5):
            self.win._wp_set_wait(self.route_key, i, 60)
        self.assertTrue(self.win._wp_set_wait(self.route_key, 2, 300))
        self.assertEqual(self.win._waypoint_route.points[2].wait_seconds, 300)

    def test_prompt_blocks_sixth_before_opening(self):
        for i in range(5):
            self.win._wp_set_wait(self.route_key, i, 60)
        with mock.patch(_GETINT, return_value=(3, True)) as g:
            self.win._wp_prompt_wait(self.route_key, 5)
        g.assert_not_called()
        self.assertEqual(self.win._waypoint_route.points[5].wait_seconds, 0)


class TestWaitExecuteGuard(WaitBase):
    def test_cannot_set_wait_during_execute(self):
        self.win._waypoint_executing = True
        ok = self.win._wp_set_wait(self.route_key, 0, 120)
        self.assertFalse(ok)
        self.assertEqual(self.win._waypoint_route.points[0].wait_seconds, 0)

    def test_prompt_blocked_during_execute(self):
        self.win._waypoint_executing = True
        with mock.patch(_GETINT, return_value=(3, True)) as g:
            self.win._wp_prompt_wait(self.route_key, 0)
        g.assert_not_called()


class TestWaitActionCoexist(WaitBase):
    def test_wait_does_not_clear_action(self):
        self.win._wp_set_action(self.route_key, 0, "servo_a")
        self.win._wp_set_wait(self.route_key, 0, 120)
        wp = self.win._waypoint_route.points[0]
        self.assertEqual(wp.action, "servo_a")
        self.assertEqual(wp.wait_seconds, 120)

    def test_action_does_not_clear_wait(self):
        self.win._wp_set_wait(self.route_key, 0, 120)
        self.win._wp_set_action(self.route_key, 0, "servo_b")
        wp = self.win._waypoint_route.points[0]
        self.assertEqual(wp.wait_seconds, 120)
        self.assertEqual(wp.action, "servo_b")


class TestWaitSummary(WaitBase):
    def _rows(self):
        return dict(self.win._cmd_summary.rows())

    def test_wait_row_present_with_values(self):
        self.win._wp_set_wait(self.route_key, 1, 180)   # WP2 = 3 นาที
        rows = self._rows()
        self.assertIn("WAIT", rows)
        self.assertIn("#2=3m", rows["WAIT"])

    def test_wait_row_after_ab(self):
        self.win._wp_set_action(self.route_key, 0, "servo_a")
        self.win._wp_set_wait(self.route_key, 1, 60)
        labels = [label for label, _ in self.win._cmd_summary.rows()]
        self.assertIn("PAYLOAD A/B", labels)
        self.assertIn("WAIT", labels)
        self.assertLess(labels.index("PAYLOAD A/B"), labels.index("WAIT"),
                        "WAIT ต้องอยู่ถัดจาก PAYLOAD A/B")

    def test_wait_row_removed_when_cleared(self):
        self.win._wp_set_wait(self.route_key, 1, 60)
        self.win._wp_clear_wait(self.route_key, 1)
        self.assertNotIn("WAIT", self._rows())

    def test_wait_frozen_in_run_snapshot(self):
        self.win._wp_set_wait(self.route_key, 1, 180)
        self.win._wp_execute()
        _pump(0.3)
        run = self.win._flight_run
        self.assertIsNotNone(run)
        self.assertIn("WAIT", run.plan_snapshot)
        self.assertIn("#2=3m", run.plan_snapshot["WAIT"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
