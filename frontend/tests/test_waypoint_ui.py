"""
เทสต์ UI integration ของ Waypoint Route Planning (mock gRPC ไม่เชื่อมจริง)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_waypoint_ui -v

ครอบคลุม (spec checklist):
  - เปิด/ปิดโหมด Waypoint (สวิตช์)
  - คลิกแผนที่ในโหมด Waypoint = วางจุด ไม่บินทันที
  - ปิดโหมด Waypoint = Direct Flight เดิมทำงานปกติ
  - Undo / Clear
  - Execute: โดรนเดี่ยว vs Swarm (รักษารูปขบวน) + รอทุกลำถึงจุดก่อนไปจุดถัดไป
  - Cancel Nav ระหว่าง Execute
  - Waypoint Mode คนละระบบกับ Tactical/Geofence (กันชนกัน)
"""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core import waypoint_logic as WP  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])
_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"


class Base(unittest.TestCase):
    N = 5

    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        # ด่าน PRE-FLIGHT จะเด้ง popup ถามก่อน TAKEOFF — เทสชุดนี้ไม่ได้ทดสอบด่านนั้น
        # (ดู tests/test_preflight.py) จึงตั้งว่าเทสผ่านแล้วให้ผ่านไปเงียบ ๆ
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        self.js_calls = []
        self.win._js = self.js_calls.append   # ดักคำสั่งที่ยิงเข้าแผนที่
        self.ids = list(range(1, self.N + 1))
        for i, d in enumerate(self.ids):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._last_alt[d] = 20.0
            # กระจายกันในแนวราบเล็กน้อยให้มีระยะห่างสัมพัทธ์ให้ตรวจสอบได้
            self.win._last_telem[d] = _fake_telem(
                d, 14.0, 100.0 + 0.0003 * i, alt_rel=20.0)
        self.win._refresh_takeoff_panel()

    def tearDown(self):
        self.win._rtl_active = False
        self.win._waypoint_executing = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def js_matching(self, needle):
        return [c for c in self.js_calls if needle in c]

    def calls(self, kind):
        return [c for c in self.fake.calls if c[0] == kind]


# ─────────────────────────────────────────────────────────────
#  เปิด/ปิดโหมด Waypoint
# ─────────────────────────────────────────────────────────────
class TestWaypointToggle(Base):
    def test_default_off(self):
        self.assertFalse(self.win._waypoint_mode)

    def test_toggle_on(self):
        self.win._wp_toggle(True)
        self.assertTrue(self.win._waypoint_mode)
        self.assertTrue(self.js_matching("setWaypointMode(true)"))

    def test_toggle_off(self):
        self.win._wp_toggle(True)
        self.js_calls.clear()
        self.win._wp_toggle(False)
        self.assertFalse(self.win._waypoint_mode)
        self.assertTrue(self.js_matching("setWaypointMode(false)"))

    def test_redundant_toggle_is_noop(self):
        self.win._wp_toggle(True)
        n = len(self.js_calls)
        self.win._wp_toggle(True)          # เปิดซ้ำ — ไม่ควรยิง JS ซ้ำ
        self.assertEqual(len(self.js_calls), n)

    def test_switch_reflects_state(self):
        self.win._wp_toggle(True)
        self.assertEqual(self.win.sw_waypoint.current(), 1)
        self.win._wp_toggle(False)
        self.assertEqual(self.win.sw_waypoint.current(), 0)

    def test_capsule_switch_drives_toggle(self):
        self.win.sw_waypoint.setCurrent(1)
        self.assertTrue(self.win._waypoint_mode)

    def test_preflight_does_not_treat_waypoint_mode_as_a_plan_row(self):
        self.win._wp_toggle(True)
        labels_when_on = {label for label, _ in self.win._cmd_summary.rows()}
        self.assertNotIn("WAYPOINT MODE", labels_when_on)

        self.win._wp_toggle(False)
        labels_when_off = {label for label, _ in self.win._cmd_summary.rows()}
        self.assertFalse(any(label.startswith("WAYPOINT") or label == "WAVE"
                             for label in labels_when_off))


# ─────────────────────────────────────────────────────────────
#  คลิกแผนที่ในโหมด Waypoint = วางจุด ไม่บินทันที
# ─────────────────────────────────────────────────────────────
class TestWaypointClick(Base):
    def test_click_does_not_fly_immediately(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_map_click(14.5, 100.5)   # เผื่อ JS ยังส่ง map_click มาผิด
        _pump(0.3)
        self.assertEqual(self.calls("goto"), [], "ไม่ควรมีการบินทันทีระหว่างโหมด Waypoint")

    def test_on_map_click_guard_returns_early(self):
        """_on_map_click ต้องมี guard ให้คืนทันทีเมื่อ waypoint mode เปิด"""
        self.win._wp_toggle(True)
        self.win._on_map_click(14.5, 100.5)
        self.assertEqual(self.calls("goto"), [])

    def test_waypoint_click_creates_marker(self):
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.958, 102.099)
        self.assertTrue(self.js_matching("addWaypoint("))
        self.assertEqual(len(self.win._waypoint_route), 1)

    def test_waypoint_click_no_selection_warns(self):
        self.win._selected_ids = set()
        self.win._selected_id = 0
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.958, 102.099)
        self.assertIsNone(self.win._waypoint_route)

    def test_multiple_clicks_build_route_in_order(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_waypoint_click(14.2, 100.2)
        route = self.win._waypoint_route
        self.assertEqual(len(route), 3)
        self.assertEqual([wp.index for wp in route.points], [0, 1, 2])

    def test_marker_color_matches_drone_single(self):
        from swarmgod_gui.core.theme import drone_color
        self.win._on_fleet_click(3, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        call = self.js_matching("addWaypoint(")[0]
        self.assertIn(drone_color(3), call)

    def test_points_label_updates(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.958123, 102.099456)
        self.assertIn("14.95812", self.win.lbl_wp_points.text())

    def test_direct_flight_still_works_when_off(self):
        """ปิดโหมด Waypoint = Direct Flight เดิมต้องทำงานปกติ (ห้ามพัง)"""
        self.win._on_fleet_click(2, False)
        self.assertFalse(self.win._waypoint_mode)
        self.win._set_goto_armed(True)     # ปลดล็อกปุ่มบนแผนที่ก่อน (ด่านกันเผลอคลิก)
        self.win._on_map_click(14.5, 100.5)
        _pump(0.6)
        self.assertTrue(self.calls("goto"), "Direct Flight ต้องยังบินทันทีเหมือนเดิม")


# ─────────────────────────────────────────────────────────────
#  Undo / Clear
# ─────────────────────────────────────────────────────────────
class TestWaypointUndoClear(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)

    def test_undo_removes_last_point(self):
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._wp_undo()
        self.assertEqual(len(self.win._waypoint_route), 1)
        self.assertTrue(self.js_matching("removeLastWaypoint("))

    def test_undo_on_empty_route_is_safe(self):
        self.win._wp_undo()          # ยังไม่มีจุดเลย — ต้องไม่ error
        self.assertIsNone(self.win._waypoint_route)

    def test_undo_to_empty_then_readd(self):
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._wp_undo()
        self.assertTrue(self.win._waypoint_route.is_empty())
        self.win._on_waypoint_click(14.5, 100.5)
        self.assertEqual(len(self.win._waypoint_route), 1)

    def test_clear_removes_all_points(self):
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._on_waypoint_click(14.1, 100.1)
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_clear()
        self.assertTrue(self.js_matching("clearAllWaypoints()"))
        self.assertIsNone(self.win._waypoint_route)

    def test_clear_asks_confirmation(self):
        self.win._on_waypoint_click(14.0, 100.0)
        with mock.patch(_CONFIRM, return_value=False):
            self.win._wp_clear()
        self.assertIsNotNone(self.win._waypoint_route)
        self.assertEqual(len(self.win._waypoint_route), 1)

    def test_clear_on_empty_route_is_safe(self):
        self.win._wp_clear()         # ไม่มีจุด — ต้องไม่ error/ไม่ถามยืนยัน
        self.assertIsNone(self.win._waypoint_route)

    def test_points_label_resets_after_clear(self):
        self.win._on_waypoint_click(14.0, 100.0)
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_clear()
        self.assertIn("ยังไม่มีจุด", self.win.lbl_wp_points.text())


# ─────────────────────────────────────────────────────────────
#  Execute — โดรนเดี่ยว
# ─────────────────────────────────────────────────────────────
class TestExecuteSingle(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.20, 100.20)
        self.win._on_waypoint_click(14.30, 100.30)

    def test_execute_without_waypoints_warns(self):
        w2 = self.win
        w2._waypoint_route = None
        w2._wp_execute()
        self.assertFalse(w2._waypoint_executing)

    def test_execute_starts_and_flies_first_point(self):
        self.win._wp_execute()
        _pump(0.3)
        self.assertTrue(self.win._waypoint_executing)
        gotos = self.calls("goto")
        self.assertEqual(len(gotos), 1)
        self.assertEqual(gotos[0][1], 2)
        self.assertAlmostEqual(gotos[0][2], 14.10)
        self.assertAlmostEqual(gotos[0][3], 100.10)

    def test_execute_disables_waypoint_mode(self):
        self.win._wp_execute()
        self.assertFalse(self.win._waypoint_mode,
                         "ต้องปิดโหมดวางจุดกันเผลอเพิ่มจุดระหว่างบิน")

    def test_execute_highlights_current_point(self):
        self.win._wp_execute()
        self.assertTrue(self.js_matching("highlightWaypoint("))

    def test_advances_through_all_points_on_arrival(self):
        self.win._wp_execute()
        _pump(0.3)
        for _ in range(3):
            self.win._on_target_reached(2)     # จำลองว่าถึงจุดปัจจุบันแล้ว
            _pump(0.9)
        gotos = [(round(c[2], 2), round(c[3], 2)) for c in self.calls("goto")]
        self.assertEqual(gotos, [(14.10, 100.10), (14.20, 100.20), (14.30, 100.30)])

    def test_finishes_after_last_point(self):
        self.win._wp_execute()
        _pump(0.3)
        for _ in range(3):
            self.win._on_target_reached(2)
            _pump(0.9)
        self.assertFalse(self.win._waypoint_executing)

    def test_status_label_shows_progress(self):
        self.win._wp_execute()
        _pump(0.3)
        self.assertIn("1/3", self.win.lbl_wp_status.text())

    def test_finish_resets_route_state(self):
        """regression: หลัง Execute จบ ต้องรีเซ็ต route — ไม่งั้นจุดใหม่ที่วาง
        จะไปต่อกับเส้นทางเก่าที่จบไปแล้ว (drone_ids/key ไม่ตรงกับที่เลือกใหม่)"""
        self.win._wp_execute()
        _pump(0.3)
        for _ in range(3):
            self.win._on_target_reached(2)
            _pump(0.9)
        self.assertIsNone(self.win._waypoint_route,
                          "route ค้างอยู่หลัง Execute จบ — จุดใหม่จะไปต่อเส้นทางเก่า")
        self.assertTrue(self.js_matching("clearAllWaypoints()"))

    def test_new_route_after_finish_reflects_new_selection(self):
        """regression: plot ใหม่หลัง finish ต้องได้ route ของ selection ปัจจุบัน
        (ไม่ใช่ drone_ids ของเส้นทางเก่าที่จบไปแล้ว)"""
        self.win._wp_execute()
        _pump(0.3)
        for _ in range(3):
            self.win._on_target_reached(2)
            _pump(0.9)
        # เลือกใหม่เป็นทั้งฝูง แล้ววางจุดใหม่
        self.win._on_fleet_toggled(True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(15.0, 101.0)
        self.assertTrue(self.win._waypoint_route.is_swarm,
                        "route ใหม่ต้องเป็น swarm ตาม selection ปัจจุบัน ไม่ใช่ลำเดียวจากรอบก่อน")

    def test_execute_without_selection_warns(self):
        self.win._selected_ids = set()
        self.win._selected_id = 0
        route = WP.WaypointRoute([])          # ไม่มีโดรนในเส้นทาง
        route.add(14.0, 100.0)
        self.win._waypoint_route = route
        self.win._wp_execute()
        self.assertFalse(self.win._waypoint_executing)


# ─────────────────────────────────────────────────────────────
#  Execute — Swarm (รักษารูปขบวน)
# ─────────────────────────────────────────────────────────────
class TestExecuteSwarm(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)      # เลือกทุกลำ = Swarm
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.50, 100.50)
        self.win._on_waypoint_click(14.60, 100.60)

    def test_route_is_swarm(self):
        self.assertTrue(self.win._waypoint_route.is_swarm)

    def test_execute_sends_all_drones(self):
        self.win._wp_execute()
        _pump(1.0)
        sent = sorted(c[1] for c in self.calls("goto"))
        self.assertEqual(sent, self.ids)

    def test_execute_keeps_formation_spacing(self):
        """ทุกลำต้องไปคนละจุด (ไม่ทับกัน) — คงระยะห่างเดิม"""
        self.win._wp_execute()
        _pump(1.0)
        pts = {c[1]: (round(c[2], 6), round(c[3], 6)) for c in self.calls("goto")}
        self.assertEqual(len(set(pts.values())), len(self.ids),
                         "ทุกลำบินไปจุดเดียวกัน = รูปขบวนไม่ถูกรักษาไว้")

    def test_execute_centroid_lands_on_waypoint(self):
        self.win._wp_execute()
        _pump(1.0)
        pts = [(c[2], c[3]) for c in self.calls("goto")]
        clat = sum(p[0] for p in pts) / len(pts)
        clon = sum(p[1] for p in pts) / len(pts)
        self.assertAlmostEqual(clat, 14.50, places=5)
        self.assertAlmostEqual(clon, 100.50, places=5)

    def test_waits_for_all_drones_before_advancing(self):
        self.win._wp_execute()
        _pump(1.0)
        first_wp_count = len(self.calls("goto"))
        # ถึงแค่บางลำ — ยังไม่ครบ ห้ามไปจุดถัดไป
        for d in self.ids[:-1]:
            self.win._on_target_reached(d)
        _pump(0.9)
        self.assertEqual(len(self.calls("goto")), first_wp_count,
                         "ไปจุดถัดไปก่อนที่ทุกลำจะถึง — ต้องรอให้ครบ")
        # ลำสุดท้ายถึงด้วย → ครบแล้ว ไปจุดถัดไปได้
        self.win._on_target_reached(self.ids[-1])
        _pump(0.9)
        self.assertGreater(len(self.calls("goto")), first_wp_count)

    def test_all_arrive_advances_to_second_point(self):
        self.win._wp_execute()
        _pump(1.0)
        for d in self.ids:
            self.win._on_target_reached(d)
        _pump(0.9)
        pts = [(round(c[2], 2), round(c[3], 2)) for c in self.calls("goto")]
        self.assertIn((14.60, 100.60), pts)

    def test_leader_path_uses_head_color_when_selected(self):
        from swarmgod_gui.core.theme import drone_color
        self.win._waypoint_route = None
        self.win._apply_head(3, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.js_calls.clear()             # ล้าง call จาก setUp() ก่อนตรวจจุดใหม่นี้
        self.win._on_waypoint_click(14.7, 100.7)
        call = self.js_matching("addWaypoint(")[-1]
        self.assertIn(drone_color(3), call)


# ─────────────────────────────────────────────────────────────
#  Cancel Nav ระหว่าง Execute
# ─────────────────────────────────────────────────────────────
class TestExecuteSwarmActive(Base):
    """Waypoint ตอน "โหมด Swarm เปิดจริง" (_swarm_active) — ต่างจาก TestExecuteSwarm
    ที่แค่เลือกหลายลำ (FLEET) โดย formation loop ที่ core ยังไม่ทำงาน

    เมื่อ loop ทำงาน core จะส่ง GotoYaw ให้ตัวลูกทุก 400ms อยู่แล้ว
    cockpit จึงต้องสั่ง "ลำแม่ลำเดียว" แล้วปล่อยให้ loop ลากลูกตาม
    ถ้าสั่งรายลำไปด้วยจะมีตัวคุม 2 ตัวแย่งกัน ขบวนสะบัด/เสียรูป
    """

    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)
        self.win._apply_head(2, push=False, reason="manual")
        self.win._swarm_active = True
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.50, 100.50)
        self.win._on_waypoint_click(14.60, 100.60)

    def test_execute_commands_head_only(self):
        self.win._wp_execute()
        _pump(1.0)
        sent = sorted({c[1] for c in self.calls("goto")})
        self.assertEqual(sent, [2],
                         "โหมด Swarm ต้องสั่งเฉพาะลำแม่ — ลูกเกาะขบวนจาก core เอง")

    def test_takeoff_gate_checks_every_swarm_member(self):
        captured = {}

        def capture(ids, context, on_ready, on_cancel=None, auto=False):
            captured["ids"] = list(ids)
            return True

        self.win._wp_require_takeoff = capture
        self.win._wp_execute()
        self.assertEqual(captured.get("ids"), self.ids,
                         "ก่อน Leader Path ต้องตรวจ TAKEOFF ของสมาชิกฝูงทุกลำ")

    def test_execute_targets_the_waypoint_itself(self):
        """ลำแม่ต้องไปที่จุดที่วางไว้ตรง ๆ (ไม่ใช่ offset ของขบวน)"""
        self.win._wp_execute()
        _pump(1.0)
        lat, lon = [(c[2], c[3]) for c in self.calls("goto")][0]
        self.assertAlmostEqual(lat, 14.50, places=5)
        self.assertAlmostEqual(lon, 100.50, places=5)

    def test_advance_waits_only_for_head(self):
        self.win._wp_execute()
        _pump(1.0)
        n = len(self.calls("goto"))
        self.win._on_target_reached(2)        # ลำแม่ถึงจุดแรก
        _pump(0.9)
        pts = [(round(c[2], 2), round(c[3], 2)) for c in self.calls("goto")]
        self.assertGreater(len(self.calls("goto")), n)
        self.assertIn((14.60, 100.60), pts)

    def test_follower_arrival_does_not_advance(self):
        """ตัวลูกถึงจุดไม่นับ — ขบวนเดินตามลำแม่เท่านั้น"""
        self.win._wp_execute()
        _pump(1.0)
        n = len(self.calls("goto"))
        for d in self.ids:
            if d != 2:
                self.win._on_target_reached(d)
        _pump(0.9)
        self.assertEqual(len(self.calls("goto")), n,
                         "ตัวลูกถึงจุดแล้วขบวนเดินหน้าเอง — ต้องรอลำแม่เท่านั้น")

    def test_execute_blocked_without_head(self):
        self.win._head_id = 0
        self.win._leader_id = 0
        self.win._wp_execute()
        _pump(0.5)
        self.assertEqual(self.calls("goto"), [])
        self.assertFalse(self.win._waypoint_executing)

    def test_cancel_nav_stops_formation_loop(self):
        """Cancel Nav ต้องหยุด follow loop ด้วย ไม่งั้น loop ลากลูกกลับเข้าขบวน
        ทับคำสั่ง Hold ที่เพิ่งสั่งไป — ตัวลูกไม่ได้ลอยรอรับคำสั่งจริง"""
        self.win._wp_execute()
        _pump(0.5)
        self.win._cancel_navigation()
        _pump(0.5)
        kinds = [c[0] for c in self.fake.calls]
        self.assertIn("swarm_stop", kinds)
        self.assertIn("hold", kinds)
        self.assertLess(kinds.index("swarm_stop"), kinds.index("hold"),
                        "ต้องหยุดขบวนก่อนสั่ง hold ไม่งั้น loop ทับคำสั่งทันที")

    def test_hold_still_sent_when_swarm_stop_fails(self):
        """swarm_stop ล้มเหลวห้ามทำให้ hold ถูกข้าม — การหยุดลอยสำคัญกว่า"""
        def boom():
            raise RuntimeError("core unreachable")
        self.fake.swarm_stop = boom
        self.win._wp_execute()
        _pump(0.5)
        self.win._cancel_navigation()
        _pump(0.5)
        self.assertIn("hold", [c[0] for c in self.fake.calls],
                      "swarm_stop พังแล้ว hold หายไปด้วย = โดรนไม่ถูกสั่งให้ลอยค้าง")


# ─────────────────────────────────────────────────────────────
#  Cancel Nav ระหว่าง Execute
# ─────────────────────────────────────────────────────────────
class TestCancelDuringExecute(Base):
    def test_cancel_stops_execution(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._wp_execute()
        _pump(0.3)
        self.assertTrue(self.win._waypoint_executing)
        self.win._cancel_navigation()
        _pump(0.3)
        self.assertFalse(self.win._waypoint_executing)

    def test_cancel_clears_waypoints_on_map(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._wp_execute()
        self.js_calls.clear()
        self.win._cancel_navigation()
        _pump(0.3)
        self.assertTrue(self.js_matching("clearAllWaypoints()"))

    def test_cancel_resets_progress_index(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._wp_execute()
        _pump(0.3)
        self.win._on_target_reached(1)
        _pump(0.9)
        self.win._cancel_navigation()
        self.assertEqual(self.win._wp_current_index, 0)

    def test_emergency_stop_also_aborts_waypoint_execute(self):
        """regression: E-STOP เดิมตัด RTL แต่ไม่ตัด Waypoint EXECUTE —
        drones หยุดจริง (stop_all) แต่ _waypoint_executing ค้าง True ทำให้
        กด Execute ใหม่แล้วขึ้นแค่ toast 'กำลังบินอยู่แล้ว' ไม่ยอมเริ่ม"""
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._wp_execute()
        _pump(0.3)
        self.assertTrue(self.win._waypoint_executing)
        self.win._do_estop([1], "Drone 1")
        _pump(0.3)
        self.assertFalse(self.win._waypoint_executing,
                         "E-STOP ต้องตัด Waypoint EXECUTE ที่ค้างอยู่ด้วย")

    def test_after_estop_can_start_new_route(self):
        """ยืนยันผลจริง: หลัง E-STOP ต้องวางเส้นทางใหม่ + Execute ได้ทันที"""
        self.win._on_fleet_click(2, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.win._wp_execute()
        _pump(0.3)
        self.win._do_estop([2], "Drone 2")
        _pump(0.3)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.5, 100.5)
        self.win._wp_execute()
        _pump(0.3)
        self.assertTrue(self.win._waypoint_executing,
                        "หลัง E-STOP ควรเริ่มเส้นทางใหม่ได้ปกติ")


# ─────────────────────────────────────────────────────────────
#  แยกระบบจาก Tactical / Geofence (กันชนกัน)
# ─────────────────────────────────────────────────────────────
class TestWaypointVsOtherTools(Base):
    def test_enabling_waypoint_disables_geofence_tool(self):
        self.win._set_draw_tool("poly")
        self.win._wp_toggle(True)
        # เปิด waypoint ไม่บังคับปิด geofence ฝั่ง python (คนละทิศ) —
        # แต่การเลือกเครื่องมือ geofence/tactical ต้องปิด waypoint (ทดสอบด้านล่าง)
        self.assertTrue(self.win._waypoint_mode)

    def test_selecting_geofence_tool_disables_waypoint(self):
        self.win._wp_toggle(True)
        self.win._set_draw_tool("rect")
        self.assertFalse(self.win._waypoint_mode,
                         "เลือกเครื่องมือ geofence ต้องปิดโหมด waypoint")

    def test_selecting_tactical_tool_disables_waypoint(self):
        self.win._wp_toggle(True)
        self.win._set_tac_tool("line")
        self.assertFalse(self.win._waypoint_mode,
                         "เลือกเครื่องมือ tactical ต้องปิดโหมด waypoint")

    def test_turning_off_geofence_tool_does_not_toggle_waypoint(self):
        self.win._set_draw_tool("none")
        self.assertFalse(self.win._waypoint_mode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
