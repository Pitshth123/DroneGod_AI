"""
เทสต์โหมด SEPARATE (เส้นทางแยกรายลำ) + กันชนก่อนบิน + ข้อจำกัดโหมด Swarm

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_waypoint_separate -v

ครอบคลุม:
  - GROUPED vs SEPARATE (สลับโหมด/ล้างจุดตอนสลับ)
  - SEPARATE: จุดเข้าเส้นทางของ "ลำที่โฟกัสอยู่", undo/clear แยกลำ, บินอิสระไม่รอกัน
  - Swarm: กำหนดได้แค่ลำแม่ + ห้ามใช้ SEPARATE
  - กันชน: บล็อก Execute ถ้าคู่ไหนระดับเดียวกันแล้วเส้นทางตัดกัน
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


# ─────────────────────────────────────────────────────────────
#  logic ล้วน — ตรวจการชนของเส้นทาง
# ─────────────────────────────────────────────────────────────
class TestRouteConflictLogic(unittest.TestCase):
    def test_crossing_same_altitude_is_conflict(self):
        routes = {1: [(14.0, 100.0), (14.0, 100.002)],
                  2: [(14.001, 100.001), (13.999, 100.001)]}
        c = WP.check_route_conflicts(routes, {1: 20.0, 2: 20.0})
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0].key(), (1, 2))
        self.assertAlmostEqual(c[0].dist, 0.0, places=3)

    def test_crossing_different_altitude_is_safe(self):
        """เส้นทางตัดกันบนแผนที่ แต่คนละชั้นความสูง = ไม่ชน"""
        routes = {1: [(14.0, 100.0), (14.0, 100.002)],
                  2: [(14.001, 100.001), (13.999, 100.001)]}
        self.assertEqual(WP.check_route_conflicts(routes, {1: 20.0, 2: 30.0}), [])

    def test_parallel_far_apart_is_safe(self):
        routes = {1: [(14.0, 100.0), (14.0, 100.002)],
                  2: [(14.01, 100.0), (14.01, 100.002)]}
        self.assertEqual(WP.check_route_conflicts(routes, {1: 20.0, 2: 20.0}), [])

    def test_close_parallel_same_alt_is_conflict(self):
        """ขนานกันแต่ห่างแค่ ~2 m ที่ระดับเดียวกัน = เสี่ยง"""
        routes = {1: [(14.0, 100.0), (14.0, 100.002)],
                  2: [(14.00002, 100.0), (14.00002, 100.002)]}
        c = WP.check_route_conflicts(routes, {1: 20.0, 2: 20.0}, min_dist_m=6.0)
        self.assertEqual(len(c), 1)

    def test_alt_sep_threshold_respected(self):
        routes = {1: [(14.0, 100.0), (14.0, 100.002)],
                  2: [(14.001, 100.001), (13.999, 100.001)]}
        # ต่าง 1.5 m < alt_sep 2.0 → ยังถือว่าชั้นเดียวกัน
        self.assertEqual(len(WP.check_route_conflicts(
            routes, {1: 20.0, 2: 21.5}, alt_sep_m=2.0)), 1)
        # ต่าง 2.5 m > alt_sep 2.0 → คนละชั้น
        self.assertEqual(WP.check_route_conflicts(
            routes, {1: 20.0, 2: 22.5}, alt_sep_m=2.0), [])

    def test_start_position_leg_is_checked(self):
        """ช่วงบินจากตำแหน่งปัจจุบันไปจุดแรกก็ต้องถูกตรวจด้วย"""
        routes = {1: [(14.002, 100.0)], 2: [(14.002, 100.001)]}
        starts = {1: (14.0, 100.0), 2: (14.0, 100.001)}
        # ไม่ส่ง start → เป็นแค่ 2 จุดห่างกัน ~108 m
        self.assertEqual(WP.check_route_conflicts(routes, {1: 20.0, 2: 20.0}), [])
        # ส่ง start ที่ทำให้ขาแรกตัดกัน
        routes2 = {1: [(14.002, 100.001)], 2: [(14.002, 100.0)]}
        starts2 = {1: (14.0, 100.0), 2: (14.0, 100.001)}
        c = WP.check_route_conflicts(routes2, {1: 20.0, 2: 20.0},
                                     start_positions=starts2)
        self.assertEqual(len(c), 1, "ขาแรกจากตำแหน่งปัจจุบันตัดกัน แต่ตรวจไม่เจอ")

    def test_no_route_no_conflict(self):
        self.assertEqual(WP.check_route_conflicts({1: [], 2: []}, {1: 20.0, 2: 20.0}), [])

    def test_single_drone_no_conflict(self):
        self.assertEqual(WP.check_route_conflicts(
            {1: [(14.0, 100.0), (14.1, 100.1)]}, {1: 20.0}), [])

    def test_conflicts_sorted_by_distance(self):
        routes = {
            1: [(14.0, 100.0), (14.0, 100.002)],
            2: [(14.0, 100.0), (14.0, 100.002)],          # ทับกันเลย
            3: [(14.00004, 100.0), (14.00004, 100.002)],  # ห่าง ~4 m
        }
        c = WP.check_route_conflicts(routes, {1: 20.0, 2: 20.0, 3: 20.0})
        self.assertGreaterEqual(len(c), 2)
        self.assertLessEqual(c[0].dist, c[1].dist)

    def test_describe_mentions_both_drones(self):
        routes = {3: [(14.0, 100.0), (14.0, 100.002)],
                  7: [(14.0, 100.0), (14.0, 100.002)]}
        c = WP.check_route_conflicts(routes, {3: 20.0, 7: 20.0})[0]
        self.assertIn("D3", c.describe())
        self.assertIn("D7", c.describe())

    def test_route_min_distance_point_to_line(self):
        d = WP.route_min_distance([(14.0, 100.0)], [(14.0, 100.0), (14.0, 100.002)])
        self.assertAlmostEqual(d, 0.0, places=3)


# ─────────────────────────────────────────────────────────────
#  UI
# ─────────────────────────────────────────────────────────────
class Base(unittest.TestCase):
    N = 3

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
        self.win._js = self.js_calls.append
        self.ids = list(range(1, self.N + 1))
        for i, d in enumerate(self.ids):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._last_alt[d] = 20.0 + i * 10   # คนละชั้นความสูง (กันชนไม่ให้บล็อก)
            self.win._last_telem[d] = _fake_telem(
                d, 14.0, 100.0 + 0.001 * i, alt_rel=20.0 + i * 10)
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


class TestModeSwitch(Base):
    def test_default_grouped(self):
        self.assertFalse(self.win._wp_separate)
        self.assertEqual(self.win.seg_wp_mode.current(), 0)

    def test_switch_to_separate(self):
        self.win._wp_set_separate(True)
        self.assertTrue(self.win._wp_separate)

    def test_switch_clears_existing_route(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.0, 100.0)
        self.js_calls.clear()
        self.win._wp_set_separate(True)
        self.assertTrue(self.js_matching("clearAllWaypoints()"))
        self.assertIsNone(self.win._waypoint_route)

    def test_segmented_click_drives_mode(self):
        """คลิกปุ่มจริงต้องสลับโหมด (setCurrent เป็น programmatic-only ไม่ยิง signal
        — จงใจ เพื่อไม่ให้เกิด loop ตอน _wp_set_separate เรียก setCurrent กลับ)"""
        self.win.seg_wp_mode._btns[1].click()
        self.assertTrue(self.win._wp_separate)

    def test_set_current_does_not_recurse(self):
        self.win._wp_set_separate(True)      # ข้างในเรียก setCurrent(1)
        self.assertTrue(self.win._wp_separate)
        self.assertEqual(self.win.seg_wp_mode.current(), 1)


class TestSeparatePlotting(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)     # เลือกทุกลำ
        self.win._wp_set_separate(True)
        self.win._wp_toggle(True)

    def test_point_goes_to_focused_drone(self):
        self.win._on_fleet_click(2, False)   # โฟกัส D2 (เลือกลำเดียว)
        self.win._on_waypoint_click(14.5, 100.5)
        self.assertIn(2, self.win._wp_routes)
        self.assertEqual(len(self.win._wp_routes[2]), 1)

    def test_switching_focus_switches_route(self):
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_fleet_click(3, False)
        self.win._on_waypoint_click(14.3, 100.3)
        self.assertEqual(len(self.win._wp_routes[1]), 1)
        self.assertEqual(len(self.win._wp_routes[3]), 1)
        self.assertNotIn(2, self.win._wp_routes)

    def test_each_route_uses_own_drone_key_in_js(self):
        self.win._on_fleet_click(2, False)
        self.js_calls.clear()
        self.win._on_waypoint_click(14.5, 100.5)
        call = self.js_matching("addWaypoint(")[-1]
        self.assertTrue(call.startswith("addWaypoint(2,"), call)

    def test_marker_color_is_that_drone(self):
        from swarmgod_gui.core.theme import drone_color
        self.win._on_fleet_click(3, False)
        self.js_calls.clear()
        self.win._on_waypoint_click(14.5, 100.5)
        self.assertIn(drone_color(3), self.js_matching("addWaypoint(")[-1])

    def test_undo_only_affects_focused_drone(self):
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_waypoint_click(14.2, 100.2)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.5, 100.5)
        self.win._wp_undo()                  # โฟกัส D2 → ลบของ D2
        self.assertEqual(len(self.win._wp_routes[1]), 2)
        self.assertEqual(len(self.win._wp_routes[2]), 0)

    def test_clear_removes_all_drones_routes(self):
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.2, 100.2)
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_clear()
        self.assertEqual(self.win._wp_routes, {})

    def test_points_label_lists_each_drone(self):
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.1, 100.1)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.2, 100.2)
        txt = self.win.lbl_wp_points.text()
        self.assertIn("D1", txt)
        self.assertIn("D2", txt)


class TestSeparateExecute(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)
        self.win._wp_set_separate(True)
        self.win._wp_toggle(True)
        # D1: 2 จุด, D2: 1 จุด — คนละพื้นที่ ไม่ตัดกัน (และคนละชั้นความสูงด้วย)
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.10, 100.10)
        self.win._on_waypoint_click(14.11, 100.11)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.50, 100.50)

    def test_execute_starts_all_routes(self):
        self.win._wp_execute()
        _pump(0.4)
        sent = sorted(c[1] for c in self.calls("goto"))
        self.assertEqual(sent, [1, 2], "ทุกลำที่มีเส้นทางต้องเริ่มบินพร้อมกัน")

    def test_each_drone_flies_own_first_point(self):
        self.win._wp_execute()
        _pump(0.4)
        pts = {c[1]: (round(c[2], 2), round(c[3], 2)) for c in self.calls("goto")}
        self.assertEqual(pts[1], (14.10, 100.10))
        self.assertEqual(pts[2], (14.50, 100.50))

    def test_drone_advances_independently(self):
        """D1 ถึงจุดแรก → ไปจุดสองของตัวเองได้เลย ไม่ต้องรอ D2"""
        self.win._wp_execute()
        _pump(0.4)
        self.win._on_target_reached(1)
        _pump(0.9)
        d1_pts = [(round(c[2], 2), round(c[3], 2))
                  for c in self.calls("goto") if c[1] == 1]
        self.assertIn((14.11, 100.11), d1_pts, "D1 ไม่เดินหน้าต่อทั้งที่ถึงจุดแล้ว")

    def test_other_drone_not_advanced_by_first(self):
        self.win._wp_execute()
        _pump(0.4)
        n2_before = len([c for c in self.calls("goto") if c[1] == 2])
        self.win._on_target_reached(1)
        _pump(0.9)
        n2_after = len([c for c in self.calls("goto") if c[1] == 2])
        self.assertEqual(n2_before, n2_after, "D1 ถึงจุด ไม่ควรทำให้ D2 เดินหน้า")

    def test_finishes_when_all_routes_done(self):
        self.win._wp_execute()
        _pump(0.4)
        self.win._on_target_reached(2)       # D2 มีจุดเดียว → จบ
        _pump(0.9)
        self.assertTrue(self.win._waypoint_executing, "D1 ยังไม่จบ ไม่ควรปิด execution")
        self.win._on_target_reached(1)
        _pump(0.9)
        self.win._on_target_reached(1)
        _pump(0.9)
        self.assertFalse(self.win._waypoint_executing)


class TestConflictBlocking(Base):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)
        self.win._wp_set_separate(True)
        self.win._wp_toggle(True)

    def _plot_crossing_same_alt(self):
        # บังคับให้ 2 ลำอยู่ระดับเดียวกัน
        for d in (1, 2):
            self.win._last_alt[d] = 25.0
            self.win._drone_alt[d] = 25.0
        self.win._on_fleet_click(1, False)
        self.win._on_waypoint_click(14.000, 100.000)
        self.win._on_waypoint_click(14.000, 100.002)
        self.win._on_fleet_click(2, False)
        self.win._on_waypoint_click(14.001, 100.001)
        self.win._on_waypoint_click(13.999, 100.001)

    def test_execute_blocked_on_crossing_same_alt(self):
        self._plot_crossing_same_alt()
        with mock.patch(_CONFIRM, return_value=True) as cm:
            self.win._wp_execute()
        _pump(0.3)
        self.assertFalse(self.win._waypoint_executing, "ต้องบล็อกไม่ให้บิน")
        self.assertEqual(self.calls("goto"), [], "ต้องไม่มีคำสั่งบินออกไปเลย")
        self.assertTrue(cm.called, "ต้องแจ้งผู้ใช้ว่าเสี่ยงชน")

    def test_block_reports_conflicting_pair(self):
        self._plot_crossing_same_alt()
        routes = self.win._wp_all_routes()
        conflicts = self.win._wp_check_conflicts(routes)
        self.assertTrue(conflicts)
        self.assertEqual(conflicts[0].key(), (1, 2))

    def test_execute_allowed_when_altitudes_differ(self):
        self._plot_crossing_same_alt()
        self.win._last_alt[2] = 45.0         # แยกชั้นความสูง
        self.win._drone_alt[2] = 45.0
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.4)
        self.assertTrue(self.win._waypoint_executing, "คนละชั้นแล้วต้องบินได้")
        self.assertTrue(self.calls("goto"))

    def test_status_shows_blocked(self):
        self._plot_crossing_same_alt()
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        self.assertIn("เสี่ยงชน", self.win.lbl_wp_status.text())


class TestGroupedNotFalselyBlocked(Base):
    """regression: GROUPED ใช้เส้นทางร่วมกัน 1 เส้น — ถ้าเอามาตรวจแบบคู่จะได้
    ระยะ 0 m ทุกคู่ (เส้นเดียวกัน) แล้วบล็อกผิด ทั้งที่ขบวนกระจาย offset ให้แล้ว"""

    def test_grouped_same_altitude_still_flies(self):
        for d in self.ids:                   # ทุกลำระดับเดียวกัน (ขบวนปกติ)
            self.win._last_alt[d] = 20.0
            self.win._drone_alt[d] = 20.0
        self.win._on_fleet_toggled(True)
        self.assertFalse(self.win._wp_separate)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.5, 100.5)
        self.win._on_waypoint_click(14.6, 100.6)
        with mock.patch(_CONFIRM, return_value=True) as cm:
            self.win._wp_execute()
        _pump(0.6)
        self.assertTrue(self.win._waypoint_executing,
                        "GROUPED ถูกบล็อกผิด — ขบวนบินระดับเดียวกันเป็นเรื่องปกติ")
        self.assertFalse(cm.called, "ไม่ควรเด้ง dialog เตือนชนในโหมด GROUPED")
        self.assertTrue(self.calls("goto"))

    def test_grouped_targets_are_spread_by_formation(self):
        """ยืนยันว่าที่ไม่บล็อกเพราะปลายทางจริงถูกกระจาย offset ไม่ทับกัน"""
        for d in self.ids:
            self.win._last_alt[d] = 20.0
        self.win._on_fleet_toggled(True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.5, 100.5)
        with mock.patch(_CONFIRM, return_value=True):
            self.win._wp_execute()
        _pump(0.6)
        pts = {c[1]: (round(c[2], 6), round(c[3], 6)) for c in self.calls("goto")}
        self.assertEqual(len(set(pts.values())), len(pts),
                         "ปลายทางของแต่ละลำทับกัน — รูปขบวนไม่ถูกกระจาย")


class TestSwarmRestriction(Base):
    class _SwarmState:
        def __init__(self, active=True, leader_id=1):
            self.active = active
            self.leader_id = leader_id
            self.edges = []
            self.formation = 1
            self.spacing = 12.0
            self.note = ""

    def test_separate_blocked_in_swarm_mode(self):
        self.win._swarm_active = True
        self.win._wp_set_separate(True)
        self.assertFalse(self.win._wp_separate, "โหมด Swarm ต้องใช้ SEPARATE ไม่ได้")

    def test_switch_reverts_to_grouped_visually(self):
        self.win._swarm_active = True
        self.win._wp_set_separate(True)
        self.assertEqual(self.win.seg_wp_mode.current(), 0)

    def test_already_separate_forced_back_when_swarm_starts(self):
        """regression: ถ้า SEPARATE เปิดค้างอยู่แล้ว swarm มาเปิดทีหลัง
        การขอ SEPARATE ซ้ำต้องบังคับกลับ GROUPED ไม่ใช่ปล่อยค้าง"""
        self.win._wp_set_separate(True)
        self.assertTrue(self.win._wp_separate)
        self.win._swarm_active = True
        self.win._wp_set_separate(True)      # ขอซ้ำระหว่าง swarm
        self.assertFalse(self.win._wp_separate,
                         "ต้องบังคับกลับ GROUPED ไม่ปล่อยให้ค้างเป็น SEPARATE")
        self.assertEqual(self.win.seg_wp_mode.current(), 0)

    def test_blocked_click_syncs_button_back(self):
        """คลิกปุ่ม SEPARATE ตอน swarm → ปุ่มต้องเด้งกลับไป GROUPED"""
        self.win._swarm_active = True
        self.win.seg_wp_mode._btns[1].click()
        self.assertFalse(self.win._wp_separate)
        self.assertEqual(self.win.seg_wp_mode.current(), 0)

    def test_entering_swarm_forces_grouped(self):
        self.win._wp_set_separate(True)
        self.assertTrue(self.win._wp_separate)
        self.win._on_swarm_update(self._SwarmState(active=True, leader_id=1))
        self.assertFalse(self.win._wp_separate,
                         "เข้าโหมด Swarm ต้องสลับกลับเป็น GROUPED อัตโนมัติ")

    def test_swarm_plotting_uses_head_only(self):
        from swarmgod_gui.core.theme import drone_color
        self.win._apply_head(2, push=False, reason="manual")
        self.win._swarm_active = True
        self.win._on_fleet_toggled(True)
        self.win._wp_toggle(True)
        self.js_calls.clear()
        self.win._on_waypoint_click(14.5, 100.5)
        call = self.js_matching("addWaypoint(")[-1]
        self.assertTrue(call.startswith("addWaypoint(0,"), "Swarm ต้องใช้ Leader Path เดียว")
        self.assertIn(drone_color(2), call, "ต้องใช้สีของลำแม่")

    def test_swarm_without_head_warns(self):
        self.win._swarm_active = True
        self.win._head_id = 0
        self.win._on_fleet_toggled(True)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.5, 100.5)
        self.assertIsNone(self.win._waypoint_route)

    def test_mode_hint_mentions_swarm_restriction(self):
        self.win._swarm_active = True
        self.win._wp_update_mode_hint()
        self.assertIn("ลำแม่", self.win.lbl_wp_mode_hint.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
