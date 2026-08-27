"""
เทสต์ Field Tablet เฟส 3 — WAYPOINT / SWARM / MOVEMENT (docs/FIELD_TABLET_V2.md)

    cd frontend
    python -m pytest tests/test_field_phase3.py -q

สองชั้น:
  1) ชั้น HTTP (ไม่ใช้ Qt) — พิสูจน์ว่าด่านสิทธิ์/allowlist ทำงานที่ **เซิร์ฟเวอร์**
     ไม่ใช่แค่ซ่อนปุ่มบนหน้าเว็บ
  2) ชั้นคอกพิต (Qt) — พิสูจน์ว่าคำสั่งวิ่งไปลงเมธอดเดิม และตัวกันของบังคับสด
     (deadman / เพดานความเร็ว / REMOTE) ทำงานจริง
"""
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

from swarmgod_gui.core import field_server as fs  # noqa: E402

from tests.test_field_control import Base  # noqa: E402

NEW_PILOT = ("waypoint_mode", "waypoint_add", "waypoint_undo", "waypoint_clear",
             "swarm_start", "swarm_stop", "swarm_return", "formation", "speed",
             "wave", "wave_groups", "select_group", "set_mode")


# ══════════════════════════════════════════════════════════════
#  ชั้น HTTP — ด่านจริงที่เซิร์ฟเวอร์
# ══════════════════════════════════════════════════════════════
class TestPhase3Gate(Base):
    def test_new_commands_are_in_the_allowlist(self):
        for act in NEW_PILOT:
            self.assertIn(act, fs.PILOT_COMMANDS, act)
        self.assertIn("move", fs.MOVE_COMMANDS)
        self.assertIn("move_stop", fs.SAFE_COMMANDS)
        self.assertIn("wave_cancel", fs.SAFE_COMMANDS)

    def test_viewer_cannot_send_new_pilot_commands(self):
        ck = self.pair()                      # ยังไม่ได้ขอสิทธิ์ = viewer
        for act in NEW_PILOT + ("move",):
            code, _ = self.cmd(act, ck)
            self.assertEqual(code, 403, "viewer สั่ง %s ได้ = ผิด" % act)
        self.assertEqual(self.sent, [], "มีคำสั่งของ viewer หลุดไปถึงคอกพิต")

    def test_pilot_can_send_new_commands(self):
        ck = self.pair()
        self.control("claim", ck)
        for act in ("waypoint_mode", "waypoint_add", "formation"):
            code, _ = self.cmd(act, ck)
            self.assertEqual(code, 200, act)
        self.assertEqual([a for _, a, _ in self.sent],
                         ["waypoint_mode", "waypoint_add", "formation"])

    def test_move_stop_works_without_the_token(self):
        """หยุดต้องกดได้เสมอ เหมือน hold/estop — กันคนกดหยุดคือทิศทางที่ผิด"""
        ck = self.pair()
        code, _ = self.cmd("move_stop", ck)
        self.assertEqual(code, 200)
        self.assertEqual([a for _, a, _ in self.sent], ["move_stop"])

    def test_viewer_can_cancel_wave(self):
        """ยกเลิก WAVE คือการหยุด — ห้ามเอาสิทธิ์ไปกั้น"""
        ck = self.pair()
        code, _ = self.cmd("wave_cancel", ck)
        self.assertEqual(code, 200)
        self.assertEqual([a for _, a, _ in self.sent], ["wave_cancel"])

    def test_move_has_its_own_burst_budget(self):
        """บังคับสดต้องยิงซ้ำถี่ ๆ ได้ — ถ้าใช้ถังเดียวกับคำสั่ง mission
        กดค้างไม่กี่วินาทีก็กินโควตาจนสั่ง LAND ไม่ออก"""
        ck = self.pair()
        self.control("claim", ck)
        for i in range(fs.MOVE_BURST):
            code, _ = self.cmd("move", ck, dir="FWD")
            self.assertEqual(code, 200, "move ครั้งที่ %d ถูกกั้น" % (i + 1))
        # คำสั่ง mission ยังมีโควตาของตัวเองเหลืออยู่ ไม่ถูก move กินไปด้วย
        self.assertEqual(self.cmd("land", ck)[0], 200)

    def test_live_stick_rpc_names_are_still_refused(self):
        ck = self.pair()
        self.control("claim", ck)
        for bad in ("rc_move", "rcmove", "set_yaw", "setyaw", "change_speed",
                    "waypoint_delete", "geofence", "cv_track"):
            self.assertEqual(self.cmd(bad, ck)[0], 403, bad)


# ══════════════════════════════════════════════════════════════
#  ชั้นคอกพิต — คำสั่งลงเมธอดเดิม + ตัวกันของบังคับสด
# ══════════════════════════════════════════════════════════════
from PyQt5.QtWidgets import QApplication            # noqa: E402

from swarmgod_gui.app import GroundStation          # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from tests.test_ui_selection import FakeClient, _fake_telem  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _MoveClient(FakeClient):
    def rc_move(self, ids, direction, speed, yaw_rate=30.0):
        self.calls.append(("rc_move", tuple(ids), int(direction), float(speed)))
        class _R:
            ok = True
            message = ""
        return _R()


class CockpitBase(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        self.fake = _MoveClient()
        self.win.client = self.fake
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        self.win.group_of.clear()
        for did in (1, 2):
            item = FleetItem(did, "Drone %d" % did, self.win._pixmap)
            self.win._wire_fleet_item(item)
            self.win.fleet_items[did] = item
            self.win.fleet_area.addWidget(item)
            self.win._last_seen[did] = time.monotonic()
            self.win._last_alt[did] = 20.0
            telem = _fake_telem(did, 14.0, 100.0 + did * 0.0003, alt_rel=20.0)
            telem.armed = True
            self.win._last_telem[did] = telem
        self.win.group_of.update({1: 1, 2: 2})
        self.win._refresh_group_ui()
        self.win._on_fleet_click(1, False)

    def tearDown(self):
        self.win._web_move_timer.stop()
        self.win._rc_timer.stop()
        self.win._rc_dir = None
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def web(self, action, **params):
        return self.win._dispatch_web(action, params)


class TestWebWaypoint(CockpitBase):
    def test_add_is_refused_while_the_mode_is_off(self):
        """ไม่แอบเปิดโหมดให้เอง — คนที่ยืนหน้าคอมต้องเห็นสถานะตรงกับแท็บเล็ต"""
        ok, msg = self.web("waypoint_add", lat=14.1, lon=100.1)
        self.assertFalse(ok)
        self.assertIn("โหมด", msg)
        self.assertFalse(self.win._wp_has_any_route())

    def test_mode_then_add_puts_a_point(self):
        self.assertTrue(self.web("waypoint_mode", on=True)[0])
        self.assertTrue(self.win._waypoint_mode)
        self.assertTrue(self.web("waypoint_add", lat=14.1, lon=100.1)[0])
        self.assertTrue(self.win._wp_has_any_route())

    def test_bad_coordinates_are_refused(self):
        self.web("waypoint_mode", on=True)
        for bad in ({"lat": 0, "lon": 0}, {"lat": 91, "lon": 100},
                    {"lat": "x", "lon": 100}, {}):
            ok, _ = self.web("waypoint_add", **bad)
            self.assertFalse(ok, bad)
        self.assertFalse(self.win._wp_has_any_route())

    def test_clear_needs_confirmation_from_the_tablet(self):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.1, lon=100.1)
        self.assertFalse(self.web("waypoint_clear")[0])
        self.assertTrue(self.win._wp_has_any_route(), "ล้างทิ้งทั้งที่ยังไม่ยืนยัน")
        self.assertTrue(self.web("waypoint_clear", confirmed=True)[0])
        self.assertFalse(self.win._wp_has_any_route())

    def test_routes_reach_the_tablet_snapshot(self):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.1, lon=100.1)
        self.web("waypoint_add", lat=14.2, lon=100.2)
        snap = self.win.field_hub.snapshot()
        self.assertTrue(snap.get("wp_mode"))
        self.assertEqual(len(snap["routes"]), 1)
        self.assertEqual(len(snap["routes"][0]["pts"]), 2)


class TestWebFlightMode(CockpitBase):
    def test_mode_command_reaches_the_same_core_client(self):
        ok, msg = self.web("set_mode", mode="LOITER")
        self.assertTrue(ok, msg)
        self.assertIn("รอผล", msg)
        self.assertTrue(any(c[0] == "set_mode" and c[1] == (1,)
                            for c in self.fake.calls))

    def test_unknown_mode_is_refused_without_calling_the_core(self):
        ok, msg = self.web("set_mode", mode="AUTO")
        self.assertFalse(ok)
        self.assertIn("ไม่รองรับ", msg)
        self.assertFalse(any(c[0] == "set_mode" for c in self.fake.calls))


class TestWebGoto(CockpitBase):
    def test_goto_uses_current_altitude_for_one_airborne_drone(self):
        ok, msg = self.web("goto", lat=14.123456, lon=100.654321)
        self.assertTrue(ok, msg)
        call = next(c for c in self.fake.calls if c[0] == "goto")
        self.assertEqual(call[1], 1)
        self.assertAlmostEqual(call[2], 14.123456)
        self.assertAlmostEqual(call[3], 100.654321)
        self.assertAlmostEqual(call[4], 20.0)

    def test_goto_refuses_multiple_drones_and_grounded_drone(self):
        self.web("select", ids=[1, 2])
        ok, msg = self.web("goto", lat=14.1, lon=100.1)
        self.assertFalse(ok)
        self.assertIn("ทีละลำ", msg)
        self.web("select", ids=[1])
        self.win._last_alt[1] = 0.0
        ok, msg = self.web("goto", lat=14.1, lon=100.1)
        self.assertFalse(ok)
        self.assertIn("บินขึ้น", msg)

    def test_goto_target_is_sent_to_tablet_and_cleared_on_arrival(self):
        ok, _ = self.web("goto", lat=14.123456, lon=100.654321)
        self.assertTrue(ok)
        target = self.win.field_hub.snapshot()["goto_targets"][0]
        self.assertEqual(target["id"], 1)
        self.assertAlmostEqual(target["lat"], 14.123456)
        self.win._field_update_goto_target(1, 14.123456, 100.654321)
        self.win._field_push_state()
        self.assertEqual(self.win.field_hub.snapshot()["goto_targets"], [])


class TestWebHoldAll(CockpitBase):
    def test_hold_all_stops_every_connected_drone_without_motor_cut(self):
        ok, msg = self.web("hold_all")
        self.assertTrue(ok, msg)
        self.assertIn("HOLD", msg)
        self.assertTrue(any(c[0] == "stop_all" and c[1] == (1, 2)
                            for c in self.fake.calls))
        self.assertFalse(any(c[0] in ("kill", "disarm") for c in self.fake.calls))


class TestWebExecuteAsksOnTheTablet(CockpitBase):
    """เดิม EXECUTE จากแท็บเล็ตไปเปิดกล่องถามบนคอกพิต คนถือแท็บเล็ตตอบไม่ได้
    และบนแท็บเล็ตไม่มีอะไรขึ้นเลย = กดแล้วเงียบ (docs/FIELD_TABLET_V2.md §6)"""

    def _ground(self, did):
        t = self.win._last_telem[did]
        t.armed = False
        t.position.alt_rel = 0.0
        self.win._last_alt[did] = 0.0

    def _route(self, action=""):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.10, lon=100.10)
        self.web("waypoint_add", lat=14.20, lon=100.20)
        if action:
            route = self.win._wp_all_routes()[1]
            route.points[-1].action = action

    def test_grounded_drone_is_reported_back_not_asked_on_the_cockpit(self):
        self._route()
        self._ground(1)
        opened = []
        self.win._confirm = lambda *a, **k: opened.append(a) or True
        ok, msg = self.web("waypoint_execute")
        self.assertFalse(ok)
        self.assertIn("TAKEOFF", msg)
        self.assertEqual(opened, [], "ไปเปิดกล่องถามบนคอกพิตอีกแล้ว")
        self.assertFalse(self.win._waypoint_executing)

    def test_servo_points_need_confirmation_from_the_tablet(self):
        self._route(action="servo_a")
        ok, msg = self.web("waypoint_execute")
        self.assertFalse(ok)
        self.assertIn("ปล่อยของ", msg)
        self.assertFalse(self.win._waypoint_executing)

    def test_confirmed_execute_runs_without_any_cockpit_dialog(self):
        self._route(action="servo_a")
        opened = []
        self.win._confirm = lambda *a, **k: opened.append(a) or True
        ok, _ = self.web("waypoint_execute", confirm_actions=True)
        self.assertTrue(ok)
        self.assertEqual(opened, [])
        self.assertTrue(self.win._waypoint_executing)

    def test_snapshot_carries_what_the_tablet_needs_to_ask(self):
        self._route(action="servo_b")
        # ของจริงสถานะนี้ถูกดันทุกแพ็กเก็ต telemetry — เทสไม่มี telemetry ไหล จึงสั่งเอง
        self.win._field_push_state()
        snap = self.win.field_hub.snapshot()
        self.assertEqual(snap["wp_actions"], 1)
        self.assertEqual(snap["wp_targets"], [1], "เส้นทางนี้วางให้ลำที่เลือกไว้ลำเดียว")
        self.assertEqual(snap["wp_grounded"], [], "ลำนี้บินอยู่ ไม่ควรถูกนับว่าติดพื้น")
        self.assertTrue(snap["preflight_ok"])

        self._ground(1)
        self.win._field_push_state()
        self.assertEqual(self.win.field_hub.snapshot()["wp_grounded"], [1])


class TestCockpitNotificationsReachTheTablet(CockpitBase):
    def test_toast_is_mirrored_into_the_snapshot(self):
        self.win._show_toast("ทดสอบข้อความ", "err")
        notice = self.win.field_hub.snapshot()["notice"]
        self.assertEqual(notice["text"], "ทดสอบข้อความ")
        self.assertEqual(notice["kind"], "err")

    def test_each_toast_gets_a_new_sequence_number(self):
        self.win._show_toast("หนึ่ง")
        first = self.win.field_hub.snapshot()["notice"]["n"]
        self.win._show_toast("สอง")
        self.assertGreater(self.win.field_hub.snapshot()["notice"]["n"], first)

    def test_winerror_32_is_logged_but_not_shown_as_a_notification(self):
        before = self.win.field_hub.snapshot().get("notice")
        self.win._show_toast("GOTO ล้มเหลว · [WinError 32] file is locked", "err")
        self.assertEqual(self.win.field_hub.snapshot().get("notice"), before)

    def test_banner_warning_is_mirrored_and_cleared(self):
        import swarmgod_gui.app as appmod
        self.win._show_banner("ทดสอบคำเตือน", appmod.T("amber"), persistent=True)
        banner = self.win.field_hub.snapshot()["banner"]
        self.assertEqual(banner["text"], "ทดสอบคำเตือน")
        self.assertEqual(banner["kind"], "warn")
        self.assertTrue(banner["persistent"])
        self.win._hide_banner()
        self.assertIsNone(self.win.field_hub.snapshot().get("banner"))

    def test_open_dialog_is_visible_to_the_tablet_and_cleared_after(self):
        seen = {}

        def fake_confirm(parent, title, text, **kw):
            seen["dialog"] = self.win.field_hub.snapshot().get("dialog")
            return True

        import swarmgod_gui.app as appmod
        real = appmod.confirm_dlg.confirm
        appmod.confirm_dlg.confirm = fake_confirm
        try:
            self.win._confirm(self.win, "ยืนยันอะไรสักอย่าง", "รายละเอียด")
        finally:
            appmod.confirm_dlg.confirm = real
        self.assertEqual(seen["dialog"]["title"], "ยืนยันอะไรสักอย่าง")
        self.assertIsNone(self.win.field_hub.snapshot().get("dialog"),
                          "ปิดกล่องแล้วต้องเคลียร์ ไม่งั้นแท็บเล็ตค้างป้ายถาวร")


class TestFieldTravelTrail(CockpitBase):
    def test_selected_drone_trail_reaches_the_tablet_snapshot(self):
        self.win._field_track_point(1, 14.1000000, 100.1000000)
        self.win._field_track_point(1, 14.1001000, 100.1001000)
        self.win._field_track_point(2, 14.2000000, 100.2000000)
        self.win._field_track_point(2, 14.2001000, 100.2001000)
        self.win._field_push_state()
        trails = self.win.field_hub.snapshot()["trails"]
        self.assertEqual([trail["id"] for trail in trails], [1])
        self.assertEqual(len(trails[0]["pts"]), 2)

    def test_tiny_gps_jitter_does_not_make_a_heavy_trail(self):
        self.win._field_track_point(1, 14.1000000, 100.1000000)
        self.win._field_track_point(1, 14.1000050, 100.1000050)
        self.assertEqual(len(self.win._field_trails[1]), 1)


class TestWebSwarm(CockpitBase):
    def test_formation_and_spacing_land_on_the_cockpit_widgets(self):
        ok, _ = self.web("formation", formation=3, spacing=20)
        self.assertTrue(ok)
        self.assertEqual(self.win.form_picker.current(), 3)
        self.assertEqual(int(self.win.sf_spacing.value()), 20)

    def test_out_of_range_values_are_refused(self):
        self.assertFalse(self.web("formation", formation=9)[0])
        self.assertFalse(self.web("formation", spacing=999)[0])

    def test_form_up_needs_confirmation(self):
        self.assertFalse(self.web("swarm_start", formation=1)[0])
        self.assertTrue(self.web("swarm_start", formation=1, confirmed=True)[0])


class TestWebMove(CockpitBase):
    def test_direction_must_be_known(self):
        for bad in ("", "SIDEWAYS", "RC_DIR_FWD", 5):
            self.assertFalse(self.web("move", dir=bad)[0], bad)
        self.assertIsNone(self.win._rc_dir)

    def test_move_starts_the_rc_loop_and_arms_the_deadman(self):
        ok, _ = self.web("move", dir="FWD", speed=2.0)
        self.assertTrue(ok)
        self.assertIsNotNone(self.win._rc_dir)
        self.assertTrue(self.win._web_move_timer.isActive())

    def test_speed_is_capped_below_the_cockpit_limit(self):
        self.assertFalse(self.web("speed", speed=9.0)[0])
        self.assertTrue(self.web("speed", speed=2.5)[0])
        self.assertAlmostEqual(self.win.sf_speed.value(), 2.5, places=1)

    def test_move_borrows_no_speed_higher_than_the_web_cap(self):
        """คอกพิตตั้งไว้ 12 m/s แล้วแท็บเล็ตสั่ง move โดยไม่ระบุความเร็ว
        ต้องไม่ได้ยืมความเร็วนั้นไปใช้"""
        self.win.sf_speed.setValue(12.0)
        self.web("move", dir="FWD")
        self.assertLessEqual(self.win.sf_speed.value(),
                             self.win.WEB_MOVE_MAX_SPEED)

    def test_deadman_stops_when_the_tablet_goes_quiet(self):
        self.web("move", dir="FWD", speed=2.0)
        halted = []
        self.win._rc_halt = lambda notify=False: halted.append(True)
        self.win._web_move_deadman()
        self.assertTrue(halted, "ขาดคำสั่งแล้วไม่หยุดให้ = อันตรายจริงตามสเปก")

    def test_move_stop_clears_the_deadman(self):
        self.web("move", dir="FWD")
        self.assertTrue(self.web("move_stop")[0])
        self.assertFalse(self.win._web_move_timer.isActive())
        self.assertIsNone(self.win._rc_dir)


class TestRemoteModeBlocksEverything(CockpitBase):
    def test_remote_mode_refuses_the_new_commands(self):
        """REMOTE เปิดอยู่ = คอกพิตล็อกคำสั่งบินทั้งหมด เว็บต้องโดนล็อกด้วย"""
        self.win._ui_mode = False
        for act, params in (("waypoint_mode", {"on": True}),
                            ("waypoint_add", {"lat": 14.1, "lon": 100.1}),
                            ("move", {"dir": "FWD"}),
                            ("swarm_start", {"confirmed": True}),
                            ("swarm_return", {"confirmed": True}),
                            ("wave", {"on": True}),
                            ("takeoff", {"alt": 10, "confirmed": True}),
                            ("set_mode", {"mode": "GUIDED"})):
            ok, msg = self.web(act, **params)
            self.assertFalse(ok, act)
            self.assertIn("REMOTE", msg)
        self.assertIsNone(self.win._rc_dir)


class TestWebSelectMany(CockpitBase):
    def test_select_accepts_several_ids(self):
        ok, _ = self.web("select", ids=[1, 2])
        self.assertTrue(ok)
        self.assertEqual(self.win._selected_or_all(), [1, 2])
        self.assertEqual(self.win.field_hub.snapshot()["selected"], [1, 2])

    def test_select_group_picks_online_members(self):
        ok, _ = self.web("select_group", group=2)
        self.assertTrue(ok)
        self.assertEqual(self.win._selected_or_all(), [2])

    def test_select_group_zero_picks_the_whole_fleet(self):
        self.web("select", ids=[1])
        ok, _ = self.web("select_group", group=0)
        self.assertTrue(ok)
        self.assertEqual(self.win._selected_or_all(), [1, 2])

    def test_empty_group_is_refused(self):
        ok, msg = self.web("select_group", group=6)
        self.assertFalse(ok)
        self.assertIn("กลุ่ม", msg)
        self.assertEqual(self.win._selected_or_all(), [1], "ลำที่เลือกอยู่ต้องไม่หาย")


class TestWebWave(CockpitBase):
    def test_web_can_toggle_auto_next_group_and_snapshot_matches(self):
        self.web("wave", on=True)
        ok, msg = self.web("wave_auto_next", on=True)
        self.assertTrue(ok, msg)
        self.assertTrue(self.win.chk_wave_auto_next.isChecked())
        self.win._field_push_state()
        self.assertTrue(self.win.field_hub.snapshot()["wave_auto_next"])

    def test_payload_result_reaches_tablet_snapshot(self):
        self.web("wave", on=True)
        self.win._wave_record_payload_release(1, "A", 0)
        status = self.win.field_hub.snapshot()["wave_payload_status"]
        self.assertIn("✓ ปล่อย A แล้ว", status["1"])

    def test_wave_needs_two_groups(self):
        self.win.group_of.update({1: 1, 2: 1})
        self.win._refresh_group_ui()
        ok, msg = self.web("wave", on=True)
        self.assertFalse(ok)
        self.assertIn("2 กลุ่ม", msg)
        self.assertFalse(self.win._wave_enabled)

    def test_wave_is_refused_in_separate_mode(self):
        self.win._wp_separate = True
        ok, msg = self.web("wave", on=True)
        self.assertFalse(ok)
        self.assertIn("GROUPED", msg)
        self.assertFalse(self.win._wave_enabled)

    def test_wave_then_execute_does_not_open_a_cockpit_dialog(self):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.10, lon=100.10)
        self.web("waypoint_add", lat=14.20, lon=100.20)
        ok, msg = self.web("wave", on=True)
        self.assertTrue(ok, msg)
        self.assertTrue(self.win._wave_enabled)
        self.win._field_push_state()
        snap = self.win.field_hub.snapshot()
        self.assertTrue(snap["wave"])
        self.assertEqual(snap["wave_groups"], [1, 2])
        self.assertEqual(sorted(snap["wave_avail"]), [1, 2])

        opened = []
        self.win._confirm = lambda *a, **k: opened.append(a) or True
        ok, msg = self.web("waypoint_execute")
        self.assertTrue(ok, msg)
        self.assertEqual(opened, [], "WAVE จากแท็บเล็ตไปเปิดกล่องถามบนคอกพิต")
        self.assertTrue(self.win._wave_executing)
        self.assertTrue(self.win._wave_auto)

    def test_wave_servo_points_still_need_tablet_confirmation(self):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.10, lon=100.10)
        self.web("waypoint_add", lat=14.20, lon=100.20)
        route = self.win._wp_all_routes()[1]
        route.points[-1].action = "servo_a"
        self.web("wave", on=True)
        ok, msg = self.web("waypoint_execute")
        self.assertFalse(ok)
        self.assertIn("ปล่อยของ", msg)
        self.assertFalse(self.win._wave_executing)
        opened = []
        self.win._confirm = lambda *a, **k: opened.append(a) or True
        ok, _ = self.web("waypoint_execute", confirm_actions=True)
        self.assertTrue(ok)
        self.assertEqual(opened, [])
        self.assertTrue(self.win._wave_executing)

    def test_wave_cancel_stops_without_a_dialog(self):
        self.web("waypoint_mode", on=True)
        self.web("waypoint_add", lat=14.10, lon=100.10)
        self.web("waypoint_add", lat=14.20, lon=100.20)
        self.web("wave", on=True)
        self.web("waypoint_execute")
        self.assertTrue(self.win._wave_executing)
        ok, _ = self.web("wave_cancel")
        self.assertTrue(ok)
        self.assertFalse(self.win._wave_executing)

    def test_wave_groups_must_stay_at_least_two(self):
        self.web("wave", on=True)
        ok, msg = self.web("wave_groups", groups=[1])
        self.assertFalse(ok)
        self.assertIn("2 กลุ่ม", msg)
        self.assertEqual(self.win._wave_selected_groups(), [1, 2])


if __name__ == "__main__":
    unittest.main()
