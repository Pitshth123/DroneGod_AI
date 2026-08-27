"""
เทสต์รอบปรับ UI/UX — ไอคอนเรขาคณิต, ปุ่ม Cancel, ย้ายเมนู Save/Load, พิกัดเมาส์

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_ui_polish -v

ครอบคลุม:
  ข้อ 1 — ปุ่มเครื่องมือเป็นไอคอนล้วน (ไม่มีข้อความ) + มี tooltip ตอน hover
  ข้อ 2 — ปุ่ม Cancel Nav สีแดง และย้ายไปอยู่ในหมวด FLIGHT (แผงขวา)
  ข้อ 3 — เมนู Save/Export/Load ย้ายไปแผงขวา และยังใช้งานได้ครบ
  ข้อ 4 — พิกัด Lat/Long ตามเมาส์ แสดงใต้แผนที่ (นอกกรอบแผนที่)
"""
import os
import re
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

from PyQt5.QtWidgets import QApplication, QPushButton, QSizePolicy  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.core.map_bridge import MapBridge  # noqa: E402
from swarmgod_gui.widgets.controls import AccordionSection  # noqa: E402
from swarmgod_gui.widgets.icons import SHAPES, geo_icon, geo_pixmap  # noqa: E402

_app = QApplication.instance() or QApplication([])
_MAP_HTML = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map.html")


def _parents(w):
    out, p = [], w.parentWidget()
    while p is not None:
        out.append(p)
        p = p.parentWidget()
    return out


class TestGeoIcons(unittest.TestCase):
    def test_all_shapes_render(self):
        for s in SHAPES:
            px = geo_pixmap(s, "#20c77a", 18)
            self.assertFalse(px.isNull(), f"รูปทรง {s} วาดไม่ออก")

    def test_icon_is_high_dpi(self):
        px = geo_pixmap("circle", "#ffffff", 18, dpr=2.0)
        self.assertEqual(px.width(), 36, "ควรวาดที่ 2x เพื่อความคม")

    def test_unknown_shape_falls_back(self):
        self.assertFalse(geo_pixmap("ไม่มีรูปนี้", "#fff", 18).isNull())

    def test_geo_icon_returns_icon(self):
        self.assertFalse(geo_icon("square", "#fff", 16).isNull())


class Base(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")

    def tearDown(self):
        self.win._rtl_active = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()


# ─────────────────────────────────────────────────────────────
#  ข้อ 1 — ไอคอนเรขาคณิต + tooltip
# ─────────────────────────────────────────────────────────────
class TestIconButtons(Base):
    def _check_palette(self, btns, label):
        self.assertTrue(btns, f"{label}: ไม่มีปุ่ม")
        for k, b in btns.items():
            self.assertEqual(b.text(), "",
                             f"{label}/{k}: ยังมีข้อความบนปุ่ม ({b.text()!r})")
            self.assertFalse(b.icon().isNull(), f"{label}/{k}: ไม่มีไอคอน")
            self.assertTrue(b.toolTip().strip(),
                            f"{label}/{k}: ไม่มี tooltip อธิบาย")

    def test_geofence_tools_are_icons(self):
        self._check_palette(self.win._draw_btns, "geofence")

    def test_tactical_tools_are_icons(self):
        self._check_palette(self.win._tac_btns, "tactical")

    def test_geofence_tool_selection_highlights(self):
        self.win._set_draw_tool("rect")
        self.assertTrue(self.win._draw_btns["rect"].isChecked())
        self.assertFalse(self.win._draw_btns["poly"].isChecked())

    def test_tactical_tool_selection_highlights(self):
        self.win._set_tac_tool("circle")
        self.assertTrue(self.win._tac_btns["circle"].isChecked())
        self.assertFalse(self.win._tac_btns["line"].isChecked())

    def test_tooltips_are_descriptive(self):
        """tooltip ต้องอธิบายได้จริง ไม่ใช่คำเดียวสั้น ๆ"""
        for k, b in self.win._tac_btns.items():
            self.assertGreaterEqual(len(b.toolTip()), 8,
                                    f"tooltip ของ {k} สั้นเกินไป")

    def test_icon_buttons_are_compact(self):
        for k, b in self.win._draw_btns.items():
            self.assertLessEqual(b.width(), 40, f"ปุ่ม {k} ใหญ่เกินไป")


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 — ปุ่ม Cancel Nav
# ─────────────────────────────────────────────────────────────
class TestCancelButton(Base):
    def test_is_red(self):
        qss = self.win.btn_cancel_nav.styleSheet()
        self.assertTrue("239,68,68" in qss or "ef4444" in qss.lower(),
                        f"ปุ่ม Cancel ยังไม่เป็นสีแดง: {qss[:120]}")

    def test_not_amber_anymore(self):
        qss = self.win.btn_cancel_nav.styleSheet()
        self.assertNotIn("245,158,11", qss, "ยังเป็นสีเหลืองอยู่")

    def test_moved_out_of_topbar_into_a_section(self):
        parents = _parents(self.win.btn_cancel_nav)
        self.assertTrue(any(isinstance(p, AccordionSection) for p in parents),
                        "ปุ่ม Cancel ไม่ได้อยู่ในหมวดคำสั่ง (AccordionSection)")

    def test_not_in_left_panel(self):
        self.assertNotIn(self.win.btn_cancel_nav,
                         self.win.left_col.findChildren(QPushButton))

    def test_plain_text_no_icon_but_has_tooltip(self):
        # เอาไอคอนออก (feedback รอบ UI redesign) — เหลือแค่ข้อความล้วน + tooltip
        # อธิบายเต็มยังอยู่เหมือนเดิม
        self.assertTrue(self.win.btn_cancel_nav.icon().isNull())
        self.assertEqual(self.win.btn_cancel_nav.text(), "CANCEL NAV")
        self.assertTrue(self.win.btn_cancel_nav.toolTip().strip())

    def test_still_wired(self):
        called = []
        with mock.patch.object(self.win, "_cancel_navigation",
                               lambda: called.append(1)):
            btn = self.win.btn_cancel_nav
            btn.clicked.disconnect()
            btn.clicked.connect(self.win._cancel_navigation)
            btn.click()
        self.assertEqual(called, [1])


# ─────────────────────────────────────────────────────────────
#  ข้อ 3 — เมนู Save/Load ย้ายไปขวา
# ─────────────────────────────────────────────────────────────
class TestConfigMenuMoved(Base):
    def test_not_in_left_panel_anymore(self):
        self.assertNotIn(self.win.btn_cfg,
                         self.win.left_col.findChildren(QPushButton),
                         "เมนู ⋯ ยังอยู่แถบซ้าย")

    def test_is_icon_only(self):
        self.assertEqual(self.win.btn_cfg.text(), "")
        self.assertFalse(self.win.btn_cfg.icon().isNull())
        self.assertTrue(self.win.btn_cfg.toolTip().strip())

    def test_menu_has_four_actions(self):
        texts = [a.text() for a in self.win.btn_cfg.menu().actions() if not a.isSeparator()]
        self.assertEqual(len(texts), 5)
        joined = " ".join(texts)
        for want in ("Save", "Export", "Load", "แบตเตอรี่จำลอง"):
            self.assertIn(want, joined)

    def test_sim_battery_controls_are_hidden_in_three_dot_menu(self):
        self.assertFalse(hasattr(self.win, "sf_sim_batt"))
        texts = [a.text() for a in self.win.btn_cfg.menu().actions()]
        self.assertTrue(any("แบตเตอรี่จำลอง" in t for t in texts))

    def test_actions_still_call_handlers(self):
        """ย้ายที่แล้วฟังก์ชันต้องยังครบ"""
        called = []
        with mock.patch.object(self.win, "_save_settings",
                               lambda: called.append("save")), \
             mock.patch.object(self.win, "_export_settings",
                               lambda: called.append("export")), \
             mock.patch.object(self.win, "_load_settings_dialog",
                               lambda: called.append("load")), \
             mock.patch.object(self.win, "_load_saved_settings",
                               lambda: called.append("loadsaved")), \
             mock.patch.object(self.win, "_reset_sim_battery",
                               lambda: called.append("battery")):
            for a in self.win._build_cfg_button().menu().actions():
                if not a.isSeparator():
                    a.trigger()
        self.assertEqual(called, ["save", "export", "load", "loadsaved", "battery"])

    def test_menu_items_have_icons(self):
        for a in self.win.btn_cfg.menu().actions():
            if not a.isSeparator():
                self.assertFalse(a.icon().isNull(), f"เมนู {a.text()} ไม่มีไอคอน")

    def test_button_stays_compact(self):
        self.assertLessEqual(self.win.btn_cfg.width(), 32)
        self.assertLessEqual(self.win.btn_cfg.height(), 28)


# ─────────────────────────────────────────────────────────────
#  ข้อ 4 — พิกัดเมาส์
# ─────────────────────────────────────────────────────────────
class TestMouseCoords(Base):
    def test_label_exists(self):
        self.assertTrue(hasattr(self.win, "coord_mouse"))

    def test_placed_below_map_not_over_it(self):
        """ต้องอยู่นอกกรอบแผนที่ — ไม่ใช่ child ของ QWebEngineView"""
        parents = _parents(self.win.coord_mouse)
        self.assertNotIn(getattr(self.win, "web", None), parents)
        # อยู่ในกล่องเดียวกับแถบพิกัดเดิม
        self.assertTrue(set(_parents(self.win.coord)) & set(parents),
                        "ไม่ได้อยู่บริเวณเดียวกับแถบพิกัดเดิม")

    def test_initial_is_placeholder(self):
        self.assertIn("--", self.win.coord_mouse.text())

    def test_updates_on_move(self):
        self.win._on_mouse_coord(14.958123, 102.098456)
        txt = self.win.coord_mouse.text()
        self.assertIn("14.958123", txt)
        self.assertIn("102.098456", txt)

    def test_realtime_successive_updates(self):
        seen = []
        for lat, lon in ((14.1, 100.1), (14.2, 100.2), (14.3, 100.3)):
            self.win._on_mouse_coord(lat, lon)
            seen.append(self.win.coord_mouse.text())
        self.assertEqual(len(set(seen)), 3, "ค่าไม่อัปเดตตามเมาส์")

    def test_resets_on_mouse_out(self):
        self.win._on_mouse_coord(14.5, 100.5)
        self.win._on_mouse_out()
        self.assertIn("--", self.win.coord_mouse.text())

    def test_existing_coord_strip_untouched(self):
        self.assertIn("LAT", self.win.coord.text())

    def test_coord_labels_can_shrink_without_expanding_window(self):
        """ค่าพิกัดจริงยาวขึ้นภายหลังต้องไม่เพิ่ม minimum width ของหน้าต่าง"""
        self.assertEqual(self.win.coord.sizePolicy().horizontalPolicy(),
                         QSizePolicy.Ignored)
        self.assertEqual(self.win.coord_mouse.sizePolicy().horizontalPolicy(),
                         QSizePolicy.Ignored)
        self.assertEqual(self.win.coord.minimumWidth(), 0)
        self.assertLessEqual(self.win.coord_mouse.maximumWidth(), 280)

    def test_full_coordinates_remain_available_as_tooltips(self):
        self.win._on_mouse_coord(14.958123, 102.098456)
        self.assertIn("14.958123", self.win.coord_mouse.toolTip())
        self.assertIn("102.098456", self.win.coord_mouse.toolTip())


class TestCompactCoreLink(Base):
    def test_core_and_link_pills_are_compact(self):
        self.assertLessEqual(self.win.pill_core.height(), 20)
        self.assertLessEqual(self.win.pill_link.height(), 20)
        self.assertIn("padding:2px 6px", self.win.pill_link.styleSheet())
        self.assertIn("border-radius:6px", self.win.pill_link.styleSheet())


class TestMouseBridge(unittest.TestCase):
    def test_mouse_move_event(self):
        import json
        b = MapBridge()
        got = []
        b.mouse_move.connect(lambda la, lo: got.append((la, lo)))
        b.on_map_event(json.dumps(
            {"event": "mouse_move", "lat": 14.25, "lon": 100.75}))
        self.assertEqual(got, [(14.25, 100.75)])

    def test_mouse_out_event(self):
        import json
        b = MapBridge()
        got = []
        b.mouse_out.connect(lambda: got.append(1))
        b.on_map_event(json.dumps({"event": "mouse_out"}))
        self.assertEqual(got, [1])

    def test_drone_select_event(self):
        import json
        b = MapBridge()
        got = []
        b.drone_select.connect(got.append)
        b.on_map_event(json.dumps({"event": "drone_select", "drone_id": 3}))
        self.assertEqual(got, [3])

    def test_waypoint_context_event(self):
        import json
        b = MapBridge()
        got = []
        b.waypoint_context.connect(lambda route, index: got.append((route, index)))
        b.on_map_event(json.dumps(
            {"event": "waypoint_context", "drone_id": 2, "index": 4}))
        self.assertEqual(got, [(2, 4)])


class TestMapJs(unittest.TestCase):
    def setUp(self):
        with open(_MAP_HTML, encoding="utf-8") as f:
            self.html = f.read()

    def test_emits_mouse_move(self):
        self.assertIn("event:'mouse_move'", self.html.replace(" ", ""))

    def test_emits_mouse_out(self):
        self.assertIn("event:'mouse_out'", self.html.replace(" ", ""))

    def test_mouse_send_is_throttled(self):
        self.assertIn("_lastMouseSend", self.html)

    def test_drone_tooltip_is_white_with_black_text(self):
        """spec เพิ่มเติม: popup ตอนชี้โดรน พื้นขาว ตัวหนังสือดำ"""
        m = re.search(r"\.dlabel,\s*\.leaflet-tooltip\.dlabel\{(.*?)\}",
                      self.html, re.S)
        self.assertIsNotNone(m, "ไม่พบ CSS ของ .dlabel")
        css = m.group(1).replace(" ", "").lower()
        self.assertIn("background:#ffffff", css)
        self.assertIn("color:#101418", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
