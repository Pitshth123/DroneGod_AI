"""
เทสต์งานรอบนี้ (สเปกข้อ 5 — Self-Verification)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_map_collision_ui -v

ครอบคลุม:
  ข้อ 1 — คลิกแผนที่แล้วทุกลำที่เลือกบินไปพร้อมกัน (bug: เดิมบินลำเดียว)
  ข้อ 2 — ตรวจจับ/แจ้งเตือนการชน
  ข้อ 3 — ขยายฟอนต์ครอบคลุมทั้งระบบ
  ข้อ 4 — ปุ่ม Quick Action ในการ์ดล่างสั่งได้จริง (bug: ถูก disable ค้าง)
"""
import os
import re
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core import swarm_logic as SL  # noqa: E402
from swarmgod_gui.core import theme  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Base(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        for d in (1, 2, 3, 4, 5):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
        # เรียงหน้ากระดาน D1 ซ้าย → D5 ขวา, ลอย 20 m
        # ห่างกันลำละ ~22 m (0.0002°) — เกินระยะเตือน 6 m ชัดเจน ให้ baseline สะอาด
        self.win._last_telem = {
            d: _fake_telem(d, 14.0, 100.000 + 0.0002 * (d - 1), alt_rel=20.0)
            for d in (1, 2, 3, 4, 5)}
        for d in (1, 2, 3, 4, 5):
            self.win._last_alt[d] = 20.0
        self.win._refresh_takeoff_panel()
        # คลิกแผนที่สั่งบินต้องปลดล็อกก่อน (ปุ่มรูปโดรนบนแผนที่) — จำลองว่าผู้ใช้กดแล้ว
        # ปลดล็อกได้ครั้งละคำสั่ง เทสต์กลุ่มนี้คลิกครั้งเดียวต่อเทสต์จึงพอ
        self.win._set_goto_armed(True)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def gotos(self):
        return [c for c in self.fake.calls if c[0] == "goto"]


# ─────────────────────────────────────────────────────────────
#  ข้อ 1 — MAP CLICK: ทุกลำที่เลือกต้องบินไป
# ─────────────────────────────────────────────────────────────
class TestMapClickMulti(Base):
    def test_two_selected_both_fly(self):
        """บั๊กเดิม: เลือก D1+D2 แล้วคลิกแผนที่ → บินแค่ D1"""
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.0)
        ids = sorted(c[1] for c in self.gotos())
        self.assertEqual(ids, [1, 2], f"ต้องบินทั้ง 2 ลำ แต่ได้ {ids}")

    def test_select_all_every_drone_flies(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        ids = sorted(c[1] for c in self.gotos())
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_single_selected_goes_exactly_to_click(self):
        self.win._on_fleet_click(3, False)
        self.win._on_map_click(14.5, 100.5)
        _pump(0.6)
        g = self.gotos()
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0][2], 14.5, places=6)
        self.assertAlmostEqual(g[0][3], 100.5, places=6)

    def test_formation_preserved_not_stacked(self):
        """หลายลำต้องไม่บินไปทับจุดเดียวกัน — ต้องคงระยะห่างเดิม"""
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        pts = {c[1]: (c[2], c[3]) for c in self.gotos()}
        self.assertEqual(len(set(pts.values())), 5,
                         "ทุกลำบินไปจุดเดียวกัน = เสี่ยงชน (ต้องคงรูปขบวน)")

    def test_group_centroid_lands_on_click_point(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        pts = [(c[2], c[3]) for c in self.gotos()]
        clat = sum(p[0] for p in pts) / len(pts)
        clon = sum(p[1] for p in pts) / len(pts)
        self.assertAlmostEqual(clat, 14.5, places=5)
        self.assertAlmostEqual(clon, 100.5, places=5)

    def test_no_selection_sends_nothing(self):
        self.win._on_fleet_toggled(False)
        self.win._selected_id = 0
        self.win._on_map_click(14.5, 100.5)
        _pump(0.4)
        self.assertEqual(self.gotos(), [])


# ─────────────────────────────────────────────────────────────
#  ปุ่มปลดล็อกสั่งบินบนแผนที่ (Click-to-Fly arm)
#  กันเผลอคลิกแผนที่ตอนเลื่อน/ซูม/ดูพิกัด แล้วโดรนออกบินทันที
# ─────────────────────────────────────────────────────────────
class TestGotoArm(Base):
    def setUp(self):
        super().setUp()
        self.win._set_goto_armed(False)      # ล้างที่ Base ปลดไว้ — เริ่มจากล็อก
        self.win._on_fleet_toggled(True)

    def test_locked_by_default(self):
        win = GroundStation("127.0.0.1:59999")
        try:
            self.assertFalse(win._goto_armed,
                             "ค่าเริ่มต้นต้องล็อก — ไม่งั้นเผลอคลิกแผนที่แล้วบินทันที")
        finally:
            win.close(); win.deleteLater(); _app.processEvents()

    def test_click_while_locked_sends_nothing(self):
        self.win._on_map_click(14.5, 100.5)
        _pump(0.6)
        self.assertEqual(self.gotos(), [],
                         "ล็อกอยู่แต่คลิกแผนที่แล้วโดรนบิน = ด่านกันเผลอไม่ทำงาน")

    def test_click_after_arming_flies(self):
        self.win._set_goto_armed(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        self.assertTrue(self.gotos(), "ปลดล็อกแล้วยังสั่งบินไม่ได้")

    def test_stays_armed_after_command(self):
        """เป็นโหมดค้าง — สั่งแล้วยังเปิดอยู่ ผู้ใช้ไล่สั่งหลายจุดได้โดยไม่ต้องกดใหม่"""
        self.win._set_goto_armed(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        self.assertTrue(self.win._goto_armed)

    def test_repeated_clicks_keep_working(self):
        self.win._set_goto_armed(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.5)
        n = len(self.gotos())
        self.win._on_map_click(14.6, 100.6)
        _pump(1.5)
        self.assertGreater(len(self.gotos()), n,
                           "คลิกครั้งที่สองไม่ทำงานทั้งที่โหมดยังเปิดอยู่")

    def test_pressing_again_turns_mode_off(self):
        """กดปุ่มซ้ำ = ปิดโหมด แล้วคลิกต้องไม่สั่งบินอีก"""
        self.win._set_goto_armed(True)
        self.win._set_goto_armed(False)          # กดซ้ำ
        n = len(self.gotos())
        self.win._on_map_click(14.7, 100.7)
        _pump(0.6)
        self.assertEqual(len(self.gotos()), n)

    def test_waypoint_mode_relocks(self):
        """สลับโหมด Waypoint ต้องล็อกกลับ — ไม่งั้น state ฝั่ง JS/Python หลุดกัน"""
        self.win._set_goto_armed(True)
        self.win._wp_toggle(True)
        self.assertFalse(self.win._goto_armed)
        self.win._wp_toggle(False)
        self.assertFalse(self.win._goto_armed)

    def test_draw_tool_relocks(self):
        self.win._set_goto_armed(True)
        self.win._set_draw_tool("poly")
        self.assertFalse(self.win._goto_armed)

    def test_arm_syncs_button_on_map(self):
        js = []
        self.win._js = js.append
        self.win._set_goto_armed(True)
        self.assertTrue([c for c in js if "setGotoArmed(true)" in c])
        js.clear()
        self.win._set_goto_armed(False)
        self.assertTrue([c for c in js if "setGotoArmed(false)" in c])


class TestGroupGotoPure(unittest.TestCase):
    """logic ล้วนของ group goto"""

    def test_translation_keeps_relative_spacing(self):
        pos = {1: (14.0, 100.0000), 2: (14.0, 100.0001), 3: (14.0, 100.0002)}
        out = SL.group_goto_targets(pos, 15.0, 101.0)
        d12 = out[2][1] - out[1][1]
        d23 = out[3][1] - out[2][1]
        self.assertAlmostEqual(d12, 0.0001, places=9)
        self.assertAlmostEqual(d23, 0.0001, places=9)

    def test_single_drone_goes_to_exact_point(self):
        out = SL.group_goto_targets({7: (14.0, 100.0)}, 15.0, 101.0)
        self.assertEqual(out[7], (15.0, 101.0))

    def test_empty(self):
        self.assertEqual(SL.group_goto_targets({}, 1, 2), {})

    def test_keep_formation_false_stacks(self):
        pos = {1: (14.0, 100.0), 2: (14.0, 100.001)}
        out = SL.group_goto_targets(pos, 15.0, 101.0, keep_formation=False)
        self.assertEqual(out[1], out[2])


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 — COLLISION DETECTION
# ─────────────────────────────────────────────────────────────
class TestCollisionPure(unittest.TestCase):
    def test_far_apart_no_warning(self):
        pos = {1: (14.0, 100.0, 20.0), 2: (14.001, 100.001, 20.0)}
        self.assertEqual(SL.detect_collisions(pos), [])

    def test_overlapping_is_critical(self):
        pos = {1: (14.0, 100.0, 20.0), 2: (14.0, 100.0, 20.0)}
        pairs = SL.detect_collisions(pos)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].level, "critical")
        self.assertLess(pairs[0].dist, 0.5)

    def test_near_is_warn(self):
        # ~5 m ห่างกัน → เตือน (warn) แต่ยังไม่ critical
        pos = {1: (14.0, 100.0, 20.0), 2: (14.000045, 100.0, 20.0)}
        pairs = SL.detect_collisions(pos, critical_m=3.0, warn_m=6.0)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].level, "warn")

    def test_different_altitude_not_a_collision(self):
        """ซ้อนกันในแนวราบ แต่คนละชั้นความสูง = ไม่ชน"""
        pos = {1: (14.0, 100.0, 20.0), 2: (14.0, 100.0, 40.0)}
        self.assertEqual(SL.detect_collisions(pos), [])

    def test_reports_which_pair(self):
        pos = {3: (14.0, 100.0, 10.0), 7: (14.0, 100.0, 10.0)}
        p = SL.detect_collisions(pos)[0]
        self.assertEqual(p.key(), (3, 7))
        self.assertIn("D3", p.describe())
        self.assertIn("D7", p.describe())

    def test_multiple_pairs_sorted_by_distance(self):
        pos = {1: (14.0, 100.0, 20.0),
               2: (14.0, 100.00002, 20.0),   # ใกล้ 1 มาก
               3: (14.00004, 100.0, 20.0)}   # ห่างกว่า
        pairs = SL.detect_collisions(pos, critical_m=3.0, warn_m=10.0)
        self.assertGreaterEqual(len(pairs), 2)
        self.assertLessEqual(pairs[0].dist, pairs[1].dist)

    def test_haversine_sanity(self):
        # 0.001 องศา lat ≈ 111 m
        d = SL.haversine_m(14.0, 100.0, 14.001, 100.0)
        self.assertTrue(105 < d < 118, f"ระยะเพี้ยน: {d}")


class TestCollisionUI(Base):
    def test_badge_ok_when_separated(self):
        self.win._collision_watchdog()
        self.assertIn("OK", self.win.lbl_collide.text())

    def test_alert_when_drones_overlap(self):
        # ย้าย D2 ไปทับ D1
        self.win._last_telem[2] = _fake_telem(2, 14.0, 100.0, alt_rel=20.0)
        self.win._last_telem[1] = _fake_telem(1, 14.0, 100.0, alt_rel=20.0)
        self.win._collision_watchdog()
        self.assertIn("COLLISION", self.win.lbl_collide.text())

    def test_banner_shown_on_collision(self):
        self.win._last_telem[2] = _fake_telem(2, 14.0, 100.0, alt_rel=20.0)
        self.win._last_telem[1] = _fake_telem(1, 14.0, 100.0, alt_rel=20.0)
        self.win._collision_watchdog()
        self.assertTrue(self.win.banner.isVisibleTo(self.win.centralWidget())
                        or self.win.banner.text() != "")
        self.assertIn("COLLISION", self.win.banner.text())

    def test_alert_not_repeated_every_tick(self):
        self.win._last_telem[2] = _fake_telem(2, 14.0, 100.0, alt_rel=20.0)
        self.win._last_telem[1] = _fake_telem(1, 14.0, 100.0, alt_rel=20.0)
        self.win._collision_watchdog()
        before = len(self.win.mlog._entries)
        for _ in range(5):
            self.win._collision_watchdog()
        self.assertEqual(len(self.win.mlog._entries), before,
                         "ไม่ควรสแปม log ทุก tick ขณะสถานะเดิม")

    def test_recovers_to_ok_after_separating(self):
        self.win._last_telem[2] = _fake_telem(2, 14.0, 100.0, alt_rel=20.0)
        self.win._last_telem[1] = _fake_telem(1, 14.0, 100.0, alt_rel=20.0)
        self.win._collision_watchdog()
        self.win._last_telem[2] = _fake_telem(2, 14.01, 100.01, alt_rel=20.0)
        self.win._collision_watchdog()
        self.assertIn("OK", self.win.lbl_collide.text())


# ─────────────────────────────────────────────────────────────
#  ข้อ 3 — GLOBAL FONT SCALING
# ─────────────────────────────────────────────────────────────
class TestFontScaling(Base):
    def test_scale_stylesheet_pure(self):
        out = theme.scale_stylesheet("QLabel { font-size:10px; color:red; }", 2.0)
        self.assertIn("font-size:20px", out)

    def test_scale_covers_many_widgets(self):
        n = theme.apply_font_scale(self.win, 1.5)
        self.assertGreater(n, 20, f"สเกลได้แค่ {n} widget — ไม่ครอบคลุมทั้งระบบ")

    def test_scaling_is_idempotent(self):
        theme.apply_font_scale(self.win, 1.5)
        a = self.win.sel_card.lbl_name.styleSheet()
        theme.apply_font_scale(self.win, 1.5)
        b = self.win.sel_card.lbl_name.styleSheet()
        self.assertEqual(a, b, "กดซ้ำแล้วขนาดต้องไม่บวมสะสม")

    def test_bigger_scale_makes_bigger_font(self):
        import re
        theme.apply_font_scale(self.win, 1.0)
        s1 = self.win.sel_card.lbl_name.styleSheet()
        theme.apply_font_scale(self.win, 2.0)
        s2 = self.win.sel_card.lbl_name.styleSheet()
        f1 = int(re.search(r"font-size:(\d+)px", s1).group(1))
        f2 = int(re.search(r"font-size:(\d+)px", s2).group(1))
        self.assertEqual(f2, f1 * 2)

    def test_set_font_scale_updates_cards_and_log(self):
        import re
        self.win._set_font_scale(1.6)
        for w, label in ((self.win.sel_card.lbl_name, "การ์ดล่าง"),
                         (self.win.fleet_items[1].lbl_name, "การ์ดบน"),
                         (self.win.mlog.table, "กล่อง log")):
            m = re.search(r"font-size:(\d+)px", w.styleSheet())
            if m:
                self.assertGreaterEqual(int(m.group(1)), 13,
                                        f"{label} ฟอนต์ไม่ถูกขยาย")

    def test_theme_helpers_follow_global_scale(self):
        """ปรับ scale เป็น 2 เท่า ขนาดฟอนต์ต้องเป็น 2 เท่าจริง (ไม่ว่าฐานเป็นเท่าไร)"""
        theme.set_ui_scale(1.0)
        small = int(re.search(r"font-size:(\d+)px",
                              theme.tinted_btn("#ff0000", font=12)).group(1))
        theme.set_ui_scale(2.0)
        big = int(re.search(r"font-size:(\d+)px",
                            theme.tinted_btn("#ff0000", font=12)).group(1))
        theme.set_ui_scale(1.0)
        # เทียบเป็นพิกเซลตรง ๆ — ขนาดฟอนต์ปัดเป็นจำนวนเต็ม อัตราส่วนจึงคลาดได้ 1px
        self.assertLessEqual(abs(big - small * 2), 1,
                             f"scale 2 เท่าแล้วได้ {big}px จาก {small}px")

    def test_base_size_is_boosted_without_user_zoom(self):
        """ที่ 100% ตัวหนังสือต้องใหญ่กว่าเลขดิบใน stylesheet อยู่แล้ว
        ผู้ใช้จะได้ไม่ต้องไปกดขยายเองตั้งแต่แรก"""
        theme.set_ui_scale(1.0)
        self.assertGreater(theme.fs(12), 12)
        self.assertGreater(theme.BASE_BOOST, 1.0)


# ─────────────────────────────────────────────────────────────
#  ข้อ 4 — QUICK ACTIONS ต้องกดได้จริง
# ─────────────────────────────────────────────────────────────
class TestQuickActionsEnabled(Base):
    def test_buttons_enabled_after_select_with_telemetry(self):
        self.win._select_drone(2)
        c = self.win.sel_card
        for name in ("btn_arm", "btn_disarm", "btn_rtl", "btn_land", "btn_hold"):
            self.assertTrue(getattr(c, name).isEnabled(),
                            f"ปุ่ม {name} ถูก disable อยู่ — กดไม่ได้")

    def test_regression_disabled_then_reenabled(self):
        """บั๊กเดิม: เลือกลำที่ยังไม่มี telemetry ก่อน → ปุ่มถูกปิดค้างตลอด"""
        self.win._last_telem.pop(4, None)
        self.win._select_drone(4)                 # ไม่มี telemetry → ปิดปุ่ม
        self.assertFalse(self.win.sel_card.btn_arm.isEnabled())
        self.win._select_drone(2)                 # มี telemetry → ต้องเปิดคืน
        self.assertTrue(self.win.sel_card.btn_arm.isEnabled(),
                        "ปุ่มยังถูก disable ค้าง หลังเลือกลำที่มี telemetry")

    def test_telemetry_arrival_reenables(self):
        self.win._last_telem.pop(3, None)
        self.win._select_drone(3)
        self.assertFalse(self.win.sel_card.btn_arm.isEnabled())
        self.win._on_telemetry(_fake_telem(3, 14.0, 100.0, alt_rel=5.0))
        _pump(0.2)
        self.assertTrue(self.win.sel_card.btn_arm.isEnabled())

    def test_click_arm_sends_to_that_drone_only(self):
        self.win._select_drone(2)
        self.win.sel_card.btn_arm.click()
        _pump(0.4)
        self.assertIn(("arm", (2,)), self.fake.calls)

    def test_click_rtl_land_hold(self):
        self.win._select_drone(4)
        self.win.sel_card.btn_rtl.click()
        self.win.sel_card.btn_land.click()
        self.win.sel_card.btn_hold.click()
        _pump(0.6)
        self.assertIn(("rtl", (4,)), self.fake.calls)
        self.assertIn(("land", (4,)), self.fake.calls)
        self.assertIn(("hold", (4,)), self.fake.calls)

    def test_quick_action_follows_selected_card(self):
        self.win._select_drone(1)
        self.win.sel_card.btn_rtl.click()
        self.win._select_drone(5)
        self.win.sel_card.btn_rtl.click()
        _pump(0.5)
        self.assertIn(("rtl", (1,)), self.fake.calls)
        self.assertIn(("rtl", (5,)), self.fake.calls)

    def test_remote_mode_disables_quick_actions(self):
        """สลับเป็น REMOTE ต้องล็อกปุ่ม (ความปลอดภัยเดิมต้องไม่หาย)"""
        self.win._select_drone(2)
        self.win._on_control_changed(1)           # REMOTE
        self.assertFalse(self.win.sel_card.btn_arm.isEnabled())
        self.win._on_control_changed(0)           # กลับ UI
        self.win._select_drone(2)
        self.assertTrue(self.win.sel_card.btn_arm.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
