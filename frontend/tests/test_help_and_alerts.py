"""
ทดสอบ Help modal + banner แจ้งเตือน (รอบนี้: "ทำ doc ไว้ด้วยให้คลิกมาปล้วเป็น modal ...")

รัน headless:
    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_help_and_alerts -v

ครอบคลุม:
  - ปุ่ม "?" มีอยู่จริง + เปิด HelpDialog ได้โดยไม่ throw
  - เนื้อหา _help_html() มีหัวข้อ/relationship map/สองกล่อง alert ครบ
  - _show_banner(persistent=True) ไม่หายเอง / persistent=False หายเองตามเวลา
  - สลับเป็น REMOTE → banner ค้าง (persistent) พร้อมข้อความชื่อคำสั่งที่ถูกล็อก
  - สลับกลับ UI → banner หาย
  - _guard() ตอน REMOTE เปิด → บล็อกคำสั่ง + โชว์ banner ซ้ำทุกครั้ง
  - _on_head_req / _on_leader_combo ตอนเปลี่ยน Head ไม่ได้ → โชว์ banner สีแดงพร้อมเหตุผล
  - banner คงพื้นที่ไว้เสมอ (ไม่ setVisible(False)) กัน layout ขยับ/แผนที่วาบขาว
  - ฟอนต์เริ่มต้นแอป = 130% และ sync กับ theme._UI_SCALE ทันทีตอนสร้าง instance
  - แถบสีสถานะโหมดบน topbar (ป้ายโหมด → ก่อน CONTROL) เปลี่ยนสีตามโหมด แบบจางกว่า banner
  - SelectedDroneCard (การ์ดล่าง): ★ HEAD อยู่ที่เดิมในแถวหัวข้อ (ไม่ต้องแก้)
  - FleetItem (การ์ดเล็กในลิสต์): เอาป้าย "★ HEAD" (ข้อความ) ออก เหลือแค่ปุ่มดาวสี
"""
import os
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
from swarmgod_gui.widgets.help_dialog import HelpDialog, _help_html  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem, SelectedDroneCard  # noqa: E402
from swarmgod_gui.core.theme import T  # noqa: E402
from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])


class HelpDialogTests(unittest.TestCase):
    def test_help_html_has_expected_sections(self):
        html = _help_html()
        for kw in (
            "Waypoint", "GROUPED", "SEPARATE", "RTL", "Emergency",
            "REMOTE", "Head", "ไม่ใช่บั๊ก", "ไปที่ GPS", "CACHE AREA",
            "V3 ROADMAP", "V3-H02", "IP ซ้ำ", "_InactiveRpcError",
            "SWARMGOD_TOKEN", "docs/HELP.md",
        ):
            self.assertIn(kw, html, f"missing keyword in help html: {kw}")

    def test_help_dialog_constructs_and_opens_without_exception(self):
        win = GroundStation("127.0.0.1:59999")
        win.show()
        try:
            self.assertTrue(hasattr(win, "btn_help"))
            dlg = HelpDialog(win)
            dlg.show()
            _pump(0.1)
            self.assertTrue(dlg.isVisible())
            dlg.close()
        finally:
            win.close()
            win.deleteLater()
            _app.processEvents()

    def test_show_help_method_opens_dialog(self):
        win = GroundStation("127.0.0.1:59999")
        try:
            # _show_help() ใช้ .exec_() ซึ่งบล็อก event loop — ทดสอบแค่ construct+open ผ่าน HelpDialog ตรง ๆ
            self.assertTrue(callable(win._show_help))
        finally:
            win.close()
            win.deleteLater()
            _app.processEvents()


class FontScaleDefaultTests(unittest.TestCase):
    def test_default_font_scale_is_130_percent(self):
        # ต้อง monkeypatch autoload ทิ้ง เพราะเครื่อง dev อาจมี ~/.swarmgod/cockpit_settings.json
        # ที่เคยเซฟค่าอื่นไว้จากรอบก่อน ๆ ซึ่งจะบังค่า default โดยตั้งใจ (พฤติกรรมที่ถูกต้อง)
        from swarmgod_gui.core import settings_io
        from swarmgod_gui.core import theme
        orig = settings_io.try_load_default
        settings_io.try_load_default = lambda: None
        try:
            win = GroundStation("127.0.0.1:59999")
            try:
                # 100% = "ขนาดที่ออกแบบไว้" ซึ่งใหญ่พออ่านได้อยู่แล้ว
                # (ตัวขยายฐานอยู่ที่ theme.BASE_BOOST ไม่ใช่ค่าที่ผู้ใช้ปรับ)
                # ถ้าเอา 1.3 มาเป็นค่าเริ่มต้นของ _font_scale เหมือนเดิม
                # พอผู้ใช้กด "ขนาดมาตรฐาน (100%)" ตัวหนังสือจะหดเล็กกว่าที่ออกแบบ
                self.assertAlmostEqual(win._font_scale, 1.0)
                self.assertAlmostEqual(theme.ui_scale(), 1.0)
                self.assertGreater(theme.BASE_BOOST, 1.0,
                                   "ขนาดฐานต้องใหญ่กว่าเลขดิบใน stylesheet")
            finally:
                win.close()
                win.deleteLater()
                _app.processEvents()
        finally:
            settings_io.try_load_default = orig


class BannerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        self.fake = FakeClient()
        self.win.client = self.fake
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_banner_widget_always_visible_reserves_layout_space(self):
        # banner ต้อง visible เสมอ (กันแถบทั้งแอปขยับ/แผนที่วาบขาวตอนโผล่/หาย) —
        # "ซ่อน" หมายถึงข้อความว่าง+โปร่งใส ไม่ใช่ setVisible(False)
        self.assertTrue(self.win.banner.isVisible())
        self.win._hide_banner()
        self.assertEqual(self.win.banner.text(), "")

    def test_startup_shows_preflight_warning_when_red(self):
        self.assertIn(self.win._PF_WARN, self.win.banner.text())
        self.assertFalse(self.win._banner_timer.isActive())

    def test_persistent_banner_does_not_autohide(self):
        self.win._show_banner("test persistent", "#ff0000", persistent=True)
        self.assertTrue(self.win.banner.isVisible())
        self.assertNotEqual(self.win.banner.text(), "")
        self.assertFalse(self.win._banner_timer.isActive())

    def test_non_persistent_banner_starts_autohide_timer(self):
        self.win._show_banner("test transient", "#ff0000", persistent=False)
        self.assertNotEqual(self.win.banner.text(), "")
        self.assertTrue(self.win._banner_timer.isActive())

    def test_switch_to_remote_shows_persistent_banner(self):
        self.win._on_control_changed(1)  # REMOTE
        self.assertNotEqual(self.win.banner.text(), "")
        self.assertFalse(self.win._banner_timer.isActive())
        self.assertIn("REMOTE", self.win.banner.text())

    def test_switch_back_to_ui_hides_remote_banner(self):
        self.win._on_control_changed(1)  # REMOTE ก่อน
        self.assertIn("REMOTE", self.win.banner.text())
        self.win._on_control_changed(0)  # กลับ UI → ถ้ายังไม่เทส แถบ PREFLIGHT กลับมา
        self.assertIn(self.win._PF_WARN, self.win.banner.text())
        self.assertTrue(self.win.banner.isVisible())
        self.assertFalse(self.win._banner_timer.isActive())

    def test_guard_blocks_and_reshows_banner_when_remote_active(self):
        self.win._on_control_changed(1)  # REMOTE
        self.win._hide_banner()  # จำลองว่า user มองพลาด/banner หายไปแล้ว
        ok = self.win._guard()
        self.assertFalse(ok)
        self.assertNotEqual(self.win.banner.text(), "")
        self.assertFalse(self.win._banner_timer.isActive())

    def test_guard_allows_when_ui_active(self):
        self.assertTrue(self.win._guard())


class HeadBlockAlertTests(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        for d in (1, 2):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
        self.win._last_telem = {
            1: _fake_telem(1, 14.0, 100.0),
            2: _fake_telem(2, 14.0, 100.001),
        }
        self.win._apply_head(1, push=False, reason="init")
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_on_head_req_shows_red_banner_when_rtl_active(self):
        self.win._rtl_active = True
        self.win._on_head_req(2)
        self.assertTrue(self.win.banner.isVisible())
        self.assertIn("Head", self.win.banner.text())
        self.assertIn("RTL", self.win.banner.text())
        # Head ไม่เปลี่ยนจริง
        self.assertEqual(self.win._head_id, 1)

    def test_on_leader_combo_shows_red_banner_when_swarm_moving(self):
        self.win._swarm_active = True
        self.win._last_telem[2].ground_speed = 3.0
        self.win._on_leader_combo("Drone 2")
        self.assertTrue(self.win.banner.isVisible())
        self.assertIn("Head", self.win.banner.text())
        self.assertEqual(self.win._head_id, 1)


class ModeStripColorTests(unittest.TestCase):
    """แถบสีใต้ป้ายโหมด (topbar, ป้ายโหมด → ก่อน CONTROL) ต้องเปลี่ยนสีตามโหมดบิน
    แต่ต้องจางกว่า banner แจ้งเตือนเสมอ (alpha ต่ำกว่า) กันสีซ้ำ/แย่งความสำคัญ"""

    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def _strip_color(self):
        # ดึงค่า rgba(...) ตัวสุดท้ายจาก stylesheet ของ _mode_strip
        style = self.win._mode_strip.styleSheet()
        return style.split("border-bottom:")[-1].strip().rstrip(";")

    def test_mode_strip_exists_between_pill_and_control(self):
        self.assertTrue(hasattr(self.win, "_mode_strip"))

    def test_mode_strip_default_is_flight_accent(self):
        self.assertIn("59,130,246", self._strip_color())  # accent = #3b82f6

    def test_mode_strip_changes_color_per_mode(self):
        seen = set()
        for mode in ("flight", "swarm", "rtl", "waypoint"):
            self.win._flight_mode = None  # บังคับให้ _set_flight_mode_badge ทำงานใหม่
            self.win._set_flight_mode_badge(mode)
            seen.add(self._strip_color())
        self.assertEqual(len(seen), 4, "แต่ละโหมดควรได้สีแถบไม่ซ้ำกัน")

    def test_mode_strip_alpha_is_lighter_than_banner(self):
        # banner ใช้ solid border (alpha เต็ม 1.0 ผ่านค่าสีตรง ๆ) ส่วน mode strip ต้องจางกว่า (0.35)
        self.win._flight_mode = None
        self.win._set_flight_mode_badge("swarm")
        self.assertIn("0.35", self._strip_color())
        self.win._show_banner("test", T("amber"), persistent=True)
        self.assertNotIn("0.35", self.win.banner.styleSheet())


class SelectedCardHeadBadgeTests(unittest.TestCase):
    """SelectedDroneCard (การ์ดล่าง/รายละเอียด) — ป้าย ★ HEAD อยู่ที่เดิมในแถวหัวข้อ
    ไม่ต้องแก้ (ผู้ใช้ยืนยันว่าจุดนี้ไม่ใช่ปัญหา ปัญหาจริงอยู่ที่การ์ดเล็ก FleetItem)"""

    def setUp(self):
        self.card = SelectedDroneCard()
        self.card.show()

    def tearDown(self):
        self.card.close()
        self.card.deleteLater()
        _app.processEvents()

    def test_head_badge_is_normal_layout_child_not_floating(self):
        # ต้อง "ไม่ใช่" floating overlay อีกต่อไป — กลับไปเป็น widget ปกติใน top layout
        self.assertTrue(hasattr(self.card, "lbl_head"))
        self.assertFalse(self.card.lbl_head.isVisible())  # ซ่อนจนกว่าจะเป็นหัว

    def test_set_head_shows_head_label_and_star_button_text(self):
        self.card.set_head(True)
        self.assertTrue(self.card.lbl_head.isVisible())
        self.assertEqual(self.card.lbl_head.text(), "★ HEAD")
        self.assertEqual(self.card.btn_sethead.text(), "★ HEAD")

    def test_selected_drone_model_is_balanced_and_high_quality(self):
        self.assertGreaterEqual(self.card.img.width(), 68)
        self.assertLessEqual(self.card.img.width(), 76)
        self.assertGreaterEqual(self.card.img.height(), 56)
        self.assertLessEqual(self.card.img.height(), 64)
        self.assertFalse(self.card.img.pixmap().isNull())

    def test_head_and_ready_badges_have_balanced_height(self):
        self.assertEqual(self.card.lbl_head.height(), self.card.badge.height())
        self.assertEqual(self.card.badge.height(), 18)


class FleetItemHeadStarOnlyTests(unittest.TestCase):
    """FleetItem (การ์ดเล็กในลิสต์ FLEET) — เอาป้าย "★ HEAD" (ข้อความ) ออก เหลือแค่
    ปุ่มดาว (self.btn_head) ที่เปลี่ยนเป็นสีเหลืองตอนเป็นหัว กันไม่ให้ป้ายไปทับชื่อโดรน
    ในแถวที่แคบอยู่แล้ว"""

    def setUp(self):
        self.item = FleetItem(1, "Drone 1")

    def tearDown(self):
        self.item.close()
        self.item.deleteLater()
        _app.processEvents()

    def test_no_head_text_label_exists(self):
        self.assertFalse(hasattr(self.item, "lbl_head"))

    def test_star_button_shows_head_state_via_color_only(self):
        self.item.set_head(False)
        self.assertEqual(self.item.btn_head.text(), "☆")
        self.item.set_head(True)
        self.assertEqual(self.item.btn_head.text(), "★")
        self.assertIn(T("yellow"), self.item.btn_head.styleSheet())


class ConnectivityWatchdogTests(unittest.TestCase):
    """ตัวนับ ONLINE ต้องนับ "ที่ต่ออยู่จริง" ไม่ใช่ "ที่แอดไว้" · telemetry ขาด →
    ทั้งแถว FLEET และการ์ดล่างต้องขึ้น OFFLINE ตรงกัน (ไม่ค้าง READY)"""

    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.win.client = FakeClient()
        self.win.show()
        for d in (1, 2, 3):
            t = _fake_telem(d, 14.0, 100.0 + d * 0.001)
            t.status = 3
            self.win._on_telemetry(t)
        _pump(0.15)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def _stall(self, ids):
        for d in ids:
            self.win._last_seen[d] = time.monotonic() - 99

    def test_counts_only_actually_connected(self):
        self.assertEqual(len(self.win._connected_ids()), 3)
        self.assertIn("3/3", self.win.lbl_count.text())

    def test_count_drops_when_telemetry_stalls(self):
        self._stall((1, 3))
        self.win._conn_watchdog()
        self.assertIn("1/3", self.win.lbl_count.text())

    def test_fleet_row_flips_to_offline(self):
        self._stall((1,))
        self.win._conn_watchdog()
        self.assertEqual(self.win.fleet_items[1].badge.text(), "OFFLINE")

    def test_selected_card_matches_fleet_row(self):
        # การ์ดล่างเคยค้าง READY ทั้งที่แถวบนขึ้น OFFLINE แล้ว — ต้องตรงกัน
        self.win._select_drone(1)
        self._stall((1, 2, 3))
        self.win._conn_watchdog()
        self.assertEqual(self.win.sel_card._status, "OFFLINE")
        self.assertEqual(self.win.fleet_items[1]._status, "OFFLINE")

    def test_recovers_when_telemetry_returns(self):
        self._stall((1, 2, 3))
        self.win._conn_watchdog()
        self.assertIn("0/3", self.win.lbl_count.text())
        t = _fake_telem(1, 14.0, 100.001)
        t.status = 3
        self.win._on_telemetry(t)
        self.assertIn("1/3", self.win.lbl_count.text())
        self.assertNotEqual(self.win.fleet_items[1]._status, "OFFLINE")

    def test_last_seen_updates_every_packet_not_only_on_log(self):
        # เดิม _last_seen ถูกอัปเดตเฉพาะตอน log throttle ผ่าน (ทุก 1.5 วิ) — ค่าที่ใช้
        # ตัดสิน online/offline จึงเพี้ยนตามจังหวะ log
        self.win._last_seen[2] = 0.0
        self.win._last_telem_log[2] = time.monotonic()  # กัน log throttle ให้ไม่ยิง
        t = _fake_telem(2, 14.0, 100.002)
        t.status = 3
        self.win._on_telemetry(t)
        self.assertGreater(self.win._last_seen[2], 0.0)


if __name__ == "__main__":
    unittest.main()
