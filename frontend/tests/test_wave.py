import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])
_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"


class WaveBase(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        self.fake = FakeClient()
        self.win.client = self.fake
        # ด่าน PRE-FLIGHT จะเด้ง popup ถามก่อน TAKEOFF — เทสชุดนี้ไม่ได้ทดสอบด่านนั้น
        # (ดู tests/test_preflight.py) จึงตั้งว่าเทสผ่านแล้วให้ผ่านไปเงียบ ๆ
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        self.win.group_of.clear()
        for did in range(1, 6):
            item = FleetItem(did, f"Drone {did}", self.win._pixmap)
            self.win._wire_fleet_item(item)
            self.win.fleet_items[did] = item
            self.win.fleet_area.addWidget(item)
            self.win._last_seen[did] = time.monotonic()
            self.win._last_alt[did] = 20.0
            telem = _fake_telem(did, 14.0, 100.0 + did * 0.0003, alt_rel=20.0)
            telem.armed = True
            self.win._last_telem[did] = telem
        self.win.group_of.update({1: 1, 2: 1, 3: 2, 4: 2, 5: 3})
        self.win._refresh_group_ui()
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_waypoint_click(14.2, 100.2)
        self.win._staggered_rtl = lambda ids: self.fake.calls.append(("wave_rtl", tuple(ids)))

    def tearDown(self):
        self.win._wave_timer.stop()
        self.win._wave_executing = False
        self.win._waypoint_executing = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def enable_two_groups(self):
        self.win._wave_toggle(True)
        self.win.wave_group_checks[3].setChecked(False)

    def start_wave(self):
        self.enable_two_groups()
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.25)


class TestWaveSequence(WaveBase):
    def test_wave_off_execute_preserves_normal_behavior(self):
        self.assertFalse(self.win._wave_enabled)
        self.win._wp_execute()
        _pump(0.25)
        self.assertTrue(any(c[0] == "goto" for c in self.fake.calls))
        self.assertFalse(self.win._wave_executing)

    def test_group_one_flies_before_group_two(self):
        self.start_wave()
        goto_ids = {c[1] for c in self.fake.calls if c[0] == "goto"}
        self.assertEqual(goto_ids, {1, 2})
        self.assertTrue(self.win._wave_executing)

    def test_group_two_waits_until_first_group_disarmed(self):
        self.start_wave()
        self.win._wave_route_finished()
        self.win._wave_tick()
        self.assertEqual(self.win._wave_group_index, 0)
        self.assertFalse(any(c[0] == "goto" and c[1] in (3, 4) for c in self.fake.calls))
        for did in (1, 2):
            self.win._last_telem[did].armed = False
        self.win._wave_tick()
        _pump(1.15)
        self.assertEqual(self.win._wave_group_index, 1)

    def test_cancel_prevents_next_group(self):
        self.start_wave()
        self.win._abort_waypoint_execution()
        before = len(self.fake.calls)
        self.win._wave_start_next_group()
        _pump(0.2)
        self.assertFalse(self.win._wave_executing)
        self.assertEqual(len(self.fake.calls), before)

    def test_estop_stops_wave_and_prevents_next_group(self):
        self.start_wave()
        self.win._do_estop([1, 2], "TEST")
        _pump(0.25)
        self.assertFalse(self.win._wave_executing)
        before = len(self.fake.calls)
        self.win._wave_start_next_group()
        _pump(0.1)
        self.assertEqual(len(self.fake.calls), before)

    def test_timeout_stops_entire_wave(self):
        self.start_wave()
        self.win._wave_group_started_at = time.monotonic() - 301
        self.win._wave_tick()
        _pump(0.2)
        self.assertFalse(self.win._wave_executing)
        self.assertEqual(self.win._wave_group_index, -1)
        self.assertTrue(any(c[0] == "stop_all" for c in self.fake.calls),
                        "timeout must cancel the Core return sequence, not only paint HOLD")

    def test_return_phase_gets_a_fresh_timeout_budget(self):
        self.start_wave()
        self.win._wave_group_started_at = time.monotonic() - 299
        self.win._wave_route_finished()
        self.assertLess(time.monotonic() - self.win._wave_group_started_at, 1.0)
        self.win._wave_tick()
        self.assertTrue(self.win._wave_executing)
        self.assertEqual(self.win._wave_phase, "waiting_land")

    def test_separate_mode_rejects_wave(self):
        self.win._wp_set_separate(True)
        self.win._wave_toggle(True)
        self.assertFalse(self.win._wave_enabled)

    def test_one_group_rejects_wave(self):
        self.win.group_of = {1: 1, 2: 1}
        self.win._refresh_group_ui()
        self.win._wave_toggle(True)
        self.assertFalse(self.win._wave_enabled)


class TestWaypointPayloadActions(WaveBase):
    class _Rejected:
        ok = False
        message = "test rejection"

    def test_execute_with_action_requires_confirmation_and_cancel_sends_nothing(self):
        self.win._wp_set_action(self.win._wp_key, 0, "servo_a")
        with mock.patch(_CONFIRM, return_value=False) as confirm:
            self.win._wp_execute()
        self.assertTrue(confirm.called)
        self.assertEqual(self.fake.calls, [])

    def test_action_calls_hold_and_correct_servo_channel(self):
        self.win._wp_set_action(self.win._wp_key, 0, "servo_b")
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.2)
        self.win._on_target_reached(1)
        _pump(0.35)
        self.assertTrue(any(c[0] == "hold" for c in self.fake.calls))
        self.assertTrue(any(c[0] == "servo_set" and c[2] == 8 for c in self.fake.calls))

    def test_rejected_hold_never_sends_servo_and_never_marks_released(self):
        self.win._wp_set_action(self.win._wp_key, 0, "servo_a")
        self.fake.hold = mock.Mock(return_value=self._Rejected())
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.2)
        self.win._on_target_reached(1)
        _pump(0.3)
        self.assertFalse(any(c[0] == "servo_set" for c in self.fake.calls))
        step = self.win._flight_run.step("waypoint_action")
        self.assertEqual(step.status.value, "FAILED")
        self.assertNotIn("released", step.detail.lower())

    def test_rejected_servo_never_marks_payload_released(self):
        self.win._wp_set_action(self.win._wp_key, 0, "servo_b")
        original = self.fake.servo_set

        def reject_servo(drone_id, channel, pwm):
            original(drone_id, channel, pwm)
            return self._Rejected()

        self.fake.servo_set = reject_servo
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.2)
        self.win._on_target_reached(1)
        _pump(0.3)
        step = self.win._flight_run.step("waypoint_action")
        self.assertEqual(step.status.value, "FAILED")
        self.assertNotIn("released", step.detail.lower())

    def test_point_without_action_does_not_call_servo(self):
        self.win._wp_execute()
        _pump(0.2)
        self.win._on_target_reached(1)
        _pump(0.2)
        self.assertFalse(any(c[0] == "servo_set" for c in self.fake.calls))

    def test_wave_confirmation_reports_total_repetitions(self):
        self.win._wp_set_action(self.win._wp_key, 0, "servo_a")
        self.enable_two_groups()
        with mock.patch(_CONFIRM, return_value=False) as confirm:
            self.win._wp_execute()
        text = confirm.call_args.args[2]
        self.assertIn("รวมปล่อยทั้งหมด 2 ครั้ง", text)

    def test_wave_active_records_released_payload_with_checkmark(self):
        self.start_wave()
        self.win._wave_record_payload_release(1, "A", 0)
        step = self.win._flight_run.step("g1_block")
        self.assertIn("✓ ปล่อย A แล้ว", step.detail)
        self.win._wave_record_payload_release(1, "B", 1)
        self.assertIn("✓ ปล่อย A แล้ว", step.detail)
        self.assertIn("✓ ปล่อย B แล้ว", step.detail)

    def test_wave_active_does_not_show_checkmark_for_failed_payload(self):
        self.start_wave()
        self.win._wave_record_payload_failure(1, "A", 0)
        detail = self.win._flight_run.step("g1_block").detail
        self.assertIn("! ปล่อย A ไม่สำเร็จ", detail)
        self.assertNotIn("✓ ปล่อย A แล้ว", detail)

    def test_auto_next_group_skips_repeated_takeoff_confirmation(self):
        self.win.chk_wave_auto_next.setChecked(True)
        self.start_wave()
        self.win._wave_route_finished()
        for did in (1, 2, 3, 4):
            self.win._last_telem[did].armed = False
            self.win._last_telem[did].position.alt_rel = 0.0
            self.win._last_alt[did] = 0.0
        self.win._wave_tick()
        with mock.patch(_CONFIRM, return_value=False) as confirm:
            _pump(1.2)
        self.assertFalse(confirm.called)
        self.assertTrue(any(c[0] == "takeoff" and 3 in c[1] for c in self.fake.calls))


class TestRouteAutoTakeoff(WaveBase):
    def _ground(self, *ids):
        for did in ids:
            self.win._last_telem[did].armed = False
            self.win._last_telem[did].position.alt_rel = 0.0
            self.win._last_alt[did] = 0.0

    def _airborne(self, *ids):
        for did in ids:
            self.win._last_telem[did].armed = True
            self.win._last_telem[did].position.alt_rel = 3.0
            self.win._last_alt[did] = 3.0

    def test_grounded_route_confirms_takeoff_and_waits_before_goto(self):
        self._ground(1)
        with mock.patch(_CONFIRM, return_value=True) as confirm:
            self.win._wp_execute()
        _pump(0.25)
        self.assertTrue(confirm.called)
        self.assertTrue(any(c[0] == "takeoff" for c in self.fake.calls))
        self.assertFalse(any(c[0] == "goto" for c in self.fake.calls))

        self._airborne(1)
        _pump(0.85)
        self.assertTrue(any(c[0] == "goto" for c in self.fake.calls))

    def test_grounded_route_decline_sends_neither_takeoff_nor_goto(self):
        self._ground(1)
        logs = []
        orig_log = self.win._log
        self.win._log = lambda msg, **k: (logs.append(msg), orig_log(msg, **k))[1]
        with mock.patch(_CONFIRM, return_value=False):
            self.win._wp_execute()
        _pump(0.15)
        self.assertFalse(any(c[0] in ("takeoff", "goto") for c in self.fake.calls))
        # การยกเลิกถูกบันทึกลง Mission Log (ไม่ใช่ Pre-flight Summary อีกต่อไป)
        self.assertTrue(any("ยกเลิก" in m for m in logs))
        # และต้องไม่ค้างรกอยู่ใน Pre-flight Summary
        self.assertFalse(any(label == "ยกเลิก" for label, _ in self.win._cmd_summary.rows()))

    def test_second_wave_asks_and_takes_off_only_when_still_grounded(self):
        self.start_wave()
        self.win._wave_route_finished()
        self._ground(1, 2, 3, 4)
        self.win._wave_tick()  # กลุ่มแรกลงแล้ว → นัดเริ่มกลุ่ม 2
        with mock.patch(_CONFIRM, return_value=True) as confirm:
            _pump(1.2)
        self.assertTrue(confirm.called)
        self.assertTrue(any(c[0] == "takeoff" and 3 in c[1] for c in self.fake.calls))
        self.assertFalse(any(c[0] == "goto" and c[1] in (3, 4) for c in self.fake.calls))

        self._airborne(3, 4)
        _pump(0.85)
        self.assertTrue(any(c[0] == "goto" and c[1] in (3, 4) for c in self.fake.calls))


class TestWaveWait(WaveBase):
    """WAIT ราย Waypoint ในโหมด WAVE (spec T5) — ใช้ route template ร่วมกัน

    inject monotonic clock — ห้ามรอเวลาจริง
    """

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.win._wp_clock = lambda: self.now
        # WAIT 5 นาทีที่จุดแรกของ route template
        self.win._wp_set_wait(self.win._wp_key, 0, 300)

    def _group_arrives(self, *ids):
        for did in ids:
            self.win._on_target_reached(did)
        _pump(0.3)

    def test_wait_holds_current_group_before_next_point(self):
        self.start_wave()
        self._group_arrives(1, 2)
        self.assertIn(0, self.win._wp_waits, "กลุ่มปัจจุบันต้องกำลัง WAIT")
        pts = [(round(c[2], 2), round(c[3], 2))
               for c in self.fake.calls if c[0] == "goto"]
        self.assertNotIn((14.2, 100.2), pts, "WAIT ยังไม่จบ — ห้ามไปจุดถัดไป")

    def test_wait_does_not_start_next_group(self):
        self.start_wave()
        self._group_arrives(1, 2)
        self.assertEqual(self.win._wave_group_index, 0,
                         "ระหว่าง WAIT กลุ่มถัดไปต้องยังไม่เริ่ม")
        self.assertFalse(any(c[0] == "goto" and c[1] in (3, 4)
                             for c in self.fake.calls))

    def test_wait_completes_then_route_continues(self):
        self.start_wave()
        self._group_arrives(1, 2)
        self.now += 320
        self.win._wp_wait_tick()
        _pump(0.9)
        pts = [(round(c[2], 2), round(c[3], 2))
               for c in self.fake.calls if c[0] == "goto"]
        self.assertIn((14.2, 100.2), pts, "WAIT จบแล้ว route ต้องเดินต่อ")

    def test_template_retains_wait_for_each_group(self):
        """route template limit = 5 ไม่คูณตามกลุ่ม — WAIT คงอยู่ให้กลุ่มถัดไป"""
        self.start_wave()
        self.assertEqual(self.win._wave_route_template.points[0].wait_seconds, 300)
        self.assertEqual(self.win._wave_route_template.wait_count(), 1)

    def test_cancel_during_wave_wait_no_stale_callback(self):
        self.start_wave()
        self._group_arrives(1, 2)
        self.assertIn(0, self.win._wp_waits)
        self.win._abort_waypoint_execution()
        _pump(0.3)
        self.assertFalse(self.win._wp_waits, "Cancel WAVE ต้องล้าง WAIT ที่ค้าง")
        before = len([c for c in self.fake.calls if c[0] == "goto"])
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        after = len([c for c in self.fake.calls if c[0] == "goto"])
        self.assertEqual(before, after,
                         "callback WAIT เก่าห้ามพา route ไปต่อหลัง cancel WAVE")

    def test_timeout_during_wave_wait_no_stale_callback(self):
        self.start_wave()
        self._group_arrives(1, 2)
        self.win._wave_group_started_at = time.monotonic() - 301
        self.win._wave_tick()      # timeout → wave abort
        _pump(0.3)
        self.assertFalse(self.win._wave_executing)
        self.assertFalse(self.win._wp_waits)
        before = len([c for c in self.fake.calls if c[0] == "goto"])
        self.now += 400
        self.win._wp_wait_tick()
        _pump(0.9)
        after = len([c for c in self.fake.calls if c[0] == "goto"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
