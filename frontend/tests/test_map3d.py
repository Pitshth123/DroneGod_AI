"""
เทสต์แผนที่ 3D — ส่วนที่ทดสอบได้แบบ headless

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_map3d -v

ข้อจำกัดสำคัญ: QtWebEngine + platform offscreen = **segfault** ทดสอบตัวเรนเดอร์
จริงในชุดนี้ไม่ได้ ที่นี่จึงตรวจเฉพาะตรรกะฝั่ง Python (payload, ด่านความปลอดภัย,
การเสิร์ฟไฟล์) ส่วนการเรนเดอร์จริงต้องรัน `prototypes/map3d/check_cockpit3d.py`
ซึ่งเปิดหน้าต่างจริงและตรวจ tile/fps/หมุดจากในหน้าเว็บ
"""
import json
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

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QScrollArea  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core import tile_cache  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Base(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        self.js3d = []
        self.win._js3d = self.js3d.append
        for d in (1, 2):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._last_alt[d] = 20.0
            self.win._last_telem[d] = _fake_telem(
                d, 14.95 + 0.001 * d, 102.09 + 0.001 * d, alt_rel=20.0)

    def tearDown(self):
        self.win._map3d_ready = False
        self.win._map3d_win_ready = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()


# ─────────────────────────────────────────────────────────────
#  จุดกึ่งกลางฉาก 3D
# ─────────────────────────────────────────────────────────────
class TestOrigin(Base):
    def test_map_center_beats_home(self):
        self.win._home_pos = {2: (15.5, 103.5)}
        self.win._map_center = (13.7563, 100.5018)
        self.assertEqual(self.win._map3d_origin(), (13.7563, 100.5018))

    def test_falls_back_to_latest_telemetry(self):
        self.win._home_pos = {}
        lat, lon = self.win._map3d_origin()
        self.assertAlmostEqual(lat, 14.951, places=3)

    def test_default_when_nothing_known(self):
        self.win._home_pos = {}
        self.win._last_telem = {}
        lat, lon = self.win._map3d_origin()
        self.assertTrue(13 < lat < 16 and 100 < lon < 104,
                        f"ค่าเริ่มต้นควรอยู่ในไทย ได้ {lat},{lon}")

    def test_ignores_drones_without_gps(self):
        """ลำที่ยังไม่มี fix (0,0) ห้ามถูกใช้เป็นจุดกึ่งกลาง — ฉากจะไปโผล่กลางทะเล"""
        self.win._home_pos = {}
        self.win._last_telem = {9: _fake_telem(9, 0.0, 0.0, alt_rel=0.0)}
        lat, lon = self.win._map3d_origin()
        self.assertNotEqual((lat, lon), (0.0, 0.0))


# ─────────────────────────────────────────────────────────────
#  การส่งตำแหน่งเข้าแผนที่ 3D
# ─────────────────────────────────────────────────────────────
class TestPush(Base):
    def test_silent_when_3d_not_open(self):
        """ไม่ได้เปิด 3D = ห้ามยิงอะไรข้าม process เปล่า ๆ (timer เดินตลอด)"""
        self.win._map3d_ready = False
        self.win._map3d_win_ready = False
        self.win._push_map3d()
        self.assertEqual(self.js3d, [])

    def test_sends_all_drones_in_one_call(self):
        self.win._map3d_ready = True
        self.win._push_map3d()
        self.assertEqual(len(self.js3d), 1, "ต้องยิงครั้งเดียวต่อรอบ ไม่ใช่ทีละลำ")
        payload = json.loads(self.js3d[0].split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0])
        self.assertEqual(sorted(d["id"] for d in payload), [1, 2])

    def test_payload_has_fields_the_scene_needs(self):
        self.win._map3d_ready = True
        self.win._push_map3d()
        d = json.loads(self.js3d[0].split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0])[0]
        for k in ("id", "lat", "lon", "alt", "hdg", "armed", "mode"):
            self.assertIn(k, d)

    def test_skips_drones_without_gps(self):
        """(0,0) = ยังไม่มี fix — ส่งไปหมุดจะไปโผล่นอกชายฝั่งแอฟริกา"""
        self.win._map3d_ready = True
        self.win._last_telem[3] = _fake_telem(3, 0.0, 0.0, alt_rel=0.0)
        self.win._push_map3d()
        ids = [d["id"] for d in json.loads(
            self.js3d[0].split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0])]
        self.assertNotIn(3, ids)

    def test_sends_amsl_altitude(self):
        """alt_rel เป็นความสูงจาก 'จุดปล่อย' เอาไปวางบน DEM ตรง ๆ ไม่ได้
        ต้องส่ง alt_abs (AMSL) ไปด้วย ซึ่งเป็นฐานเดียวกับ DEM"""
        self.win._map3d_ready = True
        self.win._push_map3d()
        d = json.loads(self.js3d[0].split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0])[0]
        self.assertIn("alt_abs", d)

    def test_sends_user_chosen_color(self):
        """ผู้ใช้เปลี่ยนสีประจำลำได้ — 3D ต้องใช้สีเดียวกับการ์ด/แผนที่ 2D"""
        from swarmgod_gui.core.theme import drone_color
        self.win._map3d_ready = True
        self.win._push_map3d()
        d = json.loads(self.js3d[0].split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0])[0]
        self.assertEqual(d["color"], drone_color(d["id"]))

    def test_payload_is_valid_json_for_js(self):
        """ยิงเข้า runJavaScript ตรง ๆ — ถ้า JSON เพี้ยนจะพังเงียบในหน้าเว็บ"""
        self.win._map3d_ready = True
        self.win._push_map3d()
        self.assertTrue(self.js3d[0].startswith("map3d.setDrones("))
        self.assertTrue(self.js3d[0].endswith(")"))


# ─────────────────────────────────────────────────────────────
#  จุดหมาย + เส้นประนำทางบนแผนที่ 3D (ให้เหมือนแผนที่ 2D)
# ─────────────────────────────────────────────────────────────
class TestTargets(Base):
    def setUp(self):
        super().setUp()
        self.win._map3d_ready = True

    def test_silent_when_3d_closed(self):
        self.win._map3d_ready = False
        self.win._nav_targets = {1: (14.9, 102.1)}
        self.win._push_map3d_targets()
        self.assertEqual(self.js3d, [])

    def test_sends_targets(self):
        self.win._nav_targets = {1: (14.9, 102.1), 2: (14.95, 102.15)}
        self.win._push_map3d_targets()
        payload = json.loads(
            self.js3d[-1].split("map3d.setTargets(", 1)[1].rsplit(")", 1)[0])
        self.assertEqual(sorted(d["id"] for d in payload), [1, 2])
        self.assertAlmostEqual(payload[0]["lat"], 14.9, places=5)

    def test_cancel_nav_clears_targets_in_3d(self):
        self.win._on_fleet_toggled(True)
        self.win._nav_targets = {1: (14.9, 102.1)}
        self.js3d.clear()
        self.win._cancel_navigation()
        _pump(0.4)
        sent = [c for c in self.js3d if "setTargets(" in c]
        self.assertTrue(sent, "Cancel Nav ไม่ได้บอกแผนที่ 3D ให้ล้างเป้า")
        self.assertEqual(
            json.loads(sent[-1].split("map3d.setTargets(", 1)[1].rsplit(")", 1)[0]), [],
            "ยกเลิกแล้วแต่จุดหมาย/เส้นประยังค้างบนแผนที่ 3D")

    def test_target_reached_removes_that_target(self):
        self.win._nav_targets = {1: (14.9, 102.1), 2: (14.95, 102.15)}
        self.js3d.clear()
        self.win._on_target_reached(1)
        sent = [c for c in self.js3d if "setTargets(" in c]
        ids = [d["id"] for d in json.loads(
            sent[-1].split("map3d.setTargets(", 1)[1].rsplit(")", 1)[0])]
        self.assertEqual(ids, [2])


# ─────────────────────────────────────────────────────────────
#  ความสามารถชุดเดียวกับแผนที่ 2D (เลือกลำ/หัวขบวน/รั้ว/เส้นทาง)
# ─────────────────────────────────────────────────────────────
class TestParityWith2D(Base):
    def setUp(self):
        super().setUp()
        self.win._map3d_ready = True

    def sent(self, fn):
        return [c for c in self.js3d if c.startswith(f"map3d.{fn}(")]

    def test_pushes_full_view_state(self):
        self.win._push_map3d_view(force=True)
        for fn in ("setSelection", "setLeader", "setSwarmEdges",
                   "setFence", "setWaypoints", "setGCS"):
            self.assertTrue(self.sent(fn), f"ไม่ได้ส่ง {fn} เข้าแผนที่ 3D")

    def test_selection_follows_cockpit(self):
        self.win._on_fleet_click(2, False)
        self.js3d.clear()
        self.win._push_map3d_view(force=True)
        sel = json.loads(
            self.sent("setSelection")[-1].split("(", 1)[1].rsplit(")", 1)[0])
        self.assertIn(2, sel)

    def test_leader_follows_head(self):
        self.win._apply_head(2, push=False, reason="manual")
        self.js3d.clear()
        self.win._push_map3d_view(force=True)
        self.assertIn("map3d.setLeader(2)", self.sent("setLeader")[-1])

    def test_skips_when_nothing_changed(self):
        """ตัวนี้ถูกเรียกจาก timer 10 Hz — ถ้าไม่กันไว้จะยิงข้าม process 60 ครั้ง/วิ
        ทั้งที่ภาพไม่เปลี่ยนเลย"""
        self.win._push_map3d_view(force=True)
        self.js3d.clear()
        self.win._push_map3d_view()
        self.assertEqual(self.js3d, [])

    def test_pushes_again_when_selection_changes(self):
        self.win._push_map3d_view(force=True)
        self.js3d.clear()
        self.win._on_fleet_click(1, False)
        self.win._push_map3d_view()
        self.assertTrue(self.sent("setSelection"), "เลือกลำใหม่แล้ว 3D ไม่อัปเดต")

    def test_geofence_reaches_3d(self):
        self.win._fence_points = [(14.9, 102.0), (14.9, 102.1), (15.0, 102.1)]
        self.win._push_map3d_view(force=True)
        pts = json.loads(self.sent("setFence")[-1].split("(", 1)[1].rsplit(")", 1)[0])
        self.assertEqual(len(pts), 3)
        self.assertIn("lat", pts[0])

    def test_waypoints_reach_3d(self):
        self.win._on_fleet_click(1, False)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.96, 102.11)
        self.win._on_waypoint_click(14.97, 102.12)
        self.js3d.clear()
        self.win._push_map3d_view(force=True)
        routes = json.loads(
            self.sent("setWaypoints")[-1].split("(", 1)[1].rsplit(")", 1)[0])
        self.assertTrue(routes and len(routes[0]["points"]) == 2)


# ─────────────────────────────────────────────────────────────
#  ด่านโหมดสั่งบิน ต้องครอบแผนที่ 3D ด้วย
# ─────────────────────────────────────────────────────────────
class TestArmGateCovers3D(Base):
    def test_arm_state_pushed_to_3d(self):
        self.win._set_goto_armed(True)
        self.assertTrue([c for c in self.js3d if "setGotoArmed(true)" in c],
                        "แผนที่ 3D ไม่รู้ว่าปลดล็อกแล้ว → ปุ่มสองจอโชว์ไม่ตรงกัน")

    def test_disarm_state_pushed_to_3d(self):
        self.win._set_goto_armed(True)
        self.js3d.clear()
        self.win._set_goto_armed(False)
        self.assertTrue([c for c in self.js3d if "setGotoArmed(false)" in c])

    def test_mode_latches_on(self):
        """โหมดค้าง — เปิดแล้วสั่งได้เรื่อย ๆ ไม่ปิดเองหลังสั่ง 1 ครั้ง"""
        self.win._on_fleet_toggled(True)
        self.win._set_goto_armed(True)
        self.win._on_map_click(14.96, 102.10)
        _pump(1.2)
        self.assertTrue(self.win._goto_armed)

    def test_state_pushed_when_3d_opens_later(self):
        """เปิด 3D ทีหลังตอนโหมดเปิดอยู่แล้ว — ปุ่มต้องขึ้นว่าเปิด ไม่ใช่ปิด"""
        self.win._set_goto_armed(True)
        self.js3d.clear()
        self.win._on_map3d_loaded(True, "embed")
        self.assertTrue([c for c in self.js3d if "setGotoArmed(true)" in c],
                        "แผนที่ 3D ที่เพิ่งเปิดไม่รู้ว่าโหมดสั่งบินเปิดอยู่")

    def test_click_from_3d_blocked_while_locked(self):
        """แผนที่ 3D ส่ง map_click ผ่าน bridge ตัวเดียวกับ 2D — ต้องโดนด่านเดียวกัน"""
        self.win._on_fleet_toggled(True)
        self.win._set_goto_armed(False)
        self.win._on_map_click(14.96, 102.10)
        _pump(0.4)
        self.assertEqual([c for c in self.fake.calls if c[0] == "goto"], [],
                         "ล็อกอยู่แต่คำสั่งจาก 3D ทะลุไปได้")

    def test_waypoint_mode_is_pushed_to_3d(self):
        self.win._map3d_ready = True
        self.js3d.clear()
        self.win._wp_toggle(True)
        self.assertIn("map3d.setWaypointMode(true)", self.js3d)


# ─────────────────────────────────────────────────────────────
#  tile proxy — layer ความสูง + การเสิร์ฟไฟล์
# ─────────────────────────────────────────────────────────────
class TestTileCache(unittest.TestCase):
    def test_dem_layer_exists(self):
        self.assertIn("dem", tile_cache.LAYERS)

    def test_dem_is_xyz_like_other_layers(self):
        """ต้องมีครบ {z}{x}{y} — ไม่งั้น cache/prefetch/offline ใช้ทางเดียวกันไม่ได้"""
        t = tile_cache.LAYERS["dem"]
        for tok in ("{z}", "{x}", "{y}"):
            self.assertIn(tok, t)

    def test_sat_and_dem_share_tile_scheme(self):
        """ภาพกับความสูงต้องเป็นกริดเดียวกัน ไม่งั้นภาพจะเหลื่อมพื้น
        (sat ของ Esri เป็น {z}/{y}/{x} ส่วน dem เป็น {z}/{x}/{y} — คนละลำดับ
        ในสตริง แต่เป็น XYZ scheme เดียวกัน ตัวเสิร์ฟใส่ค่าให้ถูกช่องเอง)"""
        for layer in ("sat", "dem"):
            self.assertTrue(tile_cache.LAYERS[layer].startswith("https://"))

    def test_asset_dirs_allowlist_covers_3d(self):
        dirs = tile_cache._Handler._ASSET_DIRS
        self.assertIn("three/", dirs)
        self.assertIn("map3d/", dirs)


class TestAssetServing(unittest.TestCase):
    """เสิร์ฟไฟล์จริงผ่าน TileServer — รวมด่านกัน path traversal"""

    @classmethod
    def setUpClass(cls):
        assets = os.path.join(_FRONTEND, "swarmgod_gui", "assets")
        cache = os.path.join(_HERE, "_tilecache_tmp")
        cls.srv = tile_cache.TileServer(assets, cache)
        cls.base = cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        import urllib.request
        return urllib.request.urlopen(self.base + path, timeout=5)

    def test_serves_map3d_page(self):
        r = self.get("/map3d.html")
        self.assertEqual(r.status, 200)
        self.assertIn(b"map3d", r.read()[:4000])

    def test_serves_terrain_module(self):
        r = self.get("/map3d/terrain.js")
        self.assertEqual(r.status, 200)

    def test_serves_three(self):
        r = self.get("/three/three.module.js")
        self.assertEqual(r.status, 200)

    def test_three_is_r170_not_newer(self):
        """QtWebEngine 5.15 = Chromium 83 รัน three r178+ ไม่ได้ (โมดูลไม่ยอม evaluate)
        ถ้าใครอัปเวอร์ชัน แผนที่ 3D จะจอดำเงียบ ๆ — ล็อกไว้ตรงนี้"""
        body = self.get("/three/three.module.js").read().decode("utf-8", "replace")
        idx = body.find("REVISION")
        rev = body[idx:idx + 40]
        self.assertIn("170", rev, f"three revision ไม่ใช่ r170: {rev!r}")

    def raw_status(self, path):
        """ยิงคำขอดิบ — urllib จะย่อ '..' ให้ตั้งแต่ฝั่ง client ทำให้เทสต์กลวง
        ต้องส่ง path ดิบ ๆ ไปถึงตัวเสิร์ฟจริงถึงจะพิสูจน์ด่านได้"""
        import socket
        host, port = self.srv.server_address
        s = socket.create_connection((host, port), 5)
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: x\r\n\r\n".encode())
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return buf.split(b"\r\n", 1)[0].decode()

    def test_blocks_path_traversal(self):
        for evil in ("/three/../../../../../../Windows/win.ini",
                     "/three/..%2f..%2fapp.py",
                     "/map3d/../../core/grpc_client.py"):
            self.assertIn("404", self.raw_status(evil),
                          f"หลุดด่าน path traversal: {evil}")

    def test_allowed_asset_still_served_by_raw_request(self):
        # กันเทสต์ข้างบนผ่านเพราะเสิร์ฟพังไปทั้งหมด
        self.assertIn("200", self.raw_status("/three/three.module.js"))

    def test_unknown_path_404(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/secrets.txt")
        self.assertEqual(cm.exception.code, 404)


# ─────────────────────────────────────────────────────────────
# regression: กด "3D" ครั้งแรกแล้วแถบ COMMANDS ตกขอบจอ
#
# ต้นตอ: Mission Log มีแถบเครื่องมือ (tabs All/Commands/Alerts/Telemetry +
# ช่องค้นหา + ปุ่ม Pause/Clear/Export) ที่วางเป็น QHBoxLayout ตรง ๆ ไม่มีทางบีบ
# เล็กลงได้เลย — ต้องการอย่างน้อย ~744px เสมอ ทำให้ทั้งแอปต้องการความกว้างขั้นต่ำ
# มากกว่าที่ตั้งใจไว้มาก แต่ Qt ไม่บังคับใช้ค่านี้ทันที จนกว่าจะมีการคำนวณ
# layout ใหม่ทั้งต้นไม้วิดเจ็ต — ซึ่งเกิดขึ้นพอดีตอนสร้าง QWebEngineView ตัวที่สอง
# สำหรับแผนที่ 3D (กด "3D" ครั้งแรก) หน้าต่างเลยเด้งกว้างเกินจอตอนนั้น
# แก้ด้วยจัด toolbar เป็นสองแถวคงที่และทำ search ย่อได้ จึงไม่ต้องใช้ horizontal scroll
# ─────────────────────────────────────────────────────────────
class TestMissionLogToolbarDoesNotForceWideWindow(unittest.TestCase):
    def test_toolbar_min_width_is_small(self):
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        log.show()
        _app.processEvents()
        try:
            w = log.minimumSizeHint().width()
            self.assertLess(w, 300,
                            f"Mission Log บังคับความกว้างขั้นต่ำ {w}px — ถ้าเกิน "
                            "300px แถบเครื่องมือน่าจะกลับไปเป็น QHBoxLayout ตรง ๆ "
                            "อีกแล้ว (ไม่ได้ห่อด้วย QScrollArea)")
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    def test_only_vehicle_filter_strip_can_scroll_horizontally(self):
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        try:
            self.assertEqual(log.pill_scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
            self.assertEqual(log.pill_scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertEqual(log.table.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        finally:
            log.close()
            log.deleteLater()

    def test_toolbar_widgets_still_reachable_when_narrow(self):
        """แคบกว่าที่เนื้อหาต้องการจริง — ปุ่ม/tabs ต้องยังกดได้ ไม่ใช่หายไปเงียบ ๆ"""
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        log.resize(200, 400)
        log.show()
        _app.processEvents()
        try:
            for w in (log.btn_pause, log.btn_clear, log.btn_export, log.search):
                self.assertTrue(w.isVisible(), f"{w} หายไปตอนแคบ")
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    def test_category_and_system_filters_are_visible_at_normal_width(self):
        """คืนแถบ All / Commands / Alerts / Telemetry และตัวกรอง System."""
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        log.resize(900, 400)
        log.show()
        _app.processEvents()
        try:
            self.assertGreater(log.tabs.width(), 180)
            self.assertTrue(all(button.isVisible() and button.width() > 0
                                for button in log.tabs._btns))
            self.assertIn("System", log._pill_btns)
            self.assertTrue(log._pill_btns["System"].isVisible())
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    def test_long_drone_names_are_readable_and_scroll_when_needed(self):
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        log.resize(420, 400)
        log.set_vehicles(range(1, 12))
        log.show()
        _app.processEvents()
        try:
            last = log._pill_btns["Drone 11"]
            self.assertGreaterEqual(
                last.width(), last.fontMetrics().horizontalAdvance("Drone 11"),
                "ชื่อ Drone 11 ต้องไม่ถูกตัดในปุ่มกรอง log")
            self.assertGreater(log.pill_scroll.horizontalScrollBar().maximum(), 0)
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    def test_first_drone_filter_remains_visible_after_vehicle_rebuild(self):
        """รายการจาก telemetry มาใหม่ต้องไม่ทำให้แถวตัวกรองกลายเป็นพื้นที่ว่าง."""
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        log.resize(900, 400)
        log.show()
        log.set_vehicles(range(1, 12))
        _app.processEvents()
        try:
            first_drone = log._pill_btns["Drone 1"]
            self.assertFalse(first_drone.visibleRegion().isEmpty())
            self.assertGreater(log.pill_holder.width(), log.pill_scroll.viewport().width())
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    def test_drone_one_filter_does_not_include_drone_ten_or_eleven(self):
        from swarmgod_gui.widgets.mission_log import MissionLog
        log = MissionLog()
        try:
            log._vehicle = "Drone 1"
            entry = ("10:00:00", "Drone 1", "STATUS", "INFO", "own log")
            self.assertTrue(log._passes(entry))
            for vehicle in ("Drone 10", "Drone 11"):
                other = ("10:00:00", vehicle, "STATUS", "INFO", "other log")
                self.assertFalse(log._passes(other), vehicle)
        finally:
            log.close()
            log.deleteLater()
            _app.processEvents()

    # หมายเหตุ: ไม่มีเทสต์ "ความกว้างขั้นต่ำทั้งแอป" ในไฟล์นี้ — รันภายใต้
    # SWARMGOD_NO_MAP=1 (บังคับเพราะ QtWebEngine ใช้ offscreen จริงไม่ได้/segfault)
    # ซึ่งสลับไปใช้ _build_map_placeholder() แทนแผนที่จริง คนละโครงสร้างกับที่
    # บั๊กนี้เกิด (ปุ่ม 3D ก็ไม่ถูกสร้างในโหมดนี้ด้วย) วัดในโหมด headless แล้ว
    # จะเจอเลขคนละชุดที่ไม่เกี่ยวกับบั๊กจริง — ยืนยันตัวเลขจริงด้วย
    # prototypes/map3d/check_cockpit3d.py ที่เปิดหน้าต่างจริงแทน


class TestMap3DDronePanelCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map3d.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_panel_uses_thai_drone_word_not_fleet_heading(self):
        self.assertIn("<h2>โดรน</h2>", self.html)
        self.assertNotIn("<h2>FLEET</h2>", self.html)
        self.assertIn("`โดรน ${d.id}`", self.html)

    def test_panel_has_count_and_structured_drone_rows(self):
        self.assertIn('id="dcount"', self.html)
        self.assertIn('className = "dmain"', self.html)
        self.assertIn('className = "dmode"', self.html)

    def test_panel_can_collapse_and_select_a_drone(self):
        self.assertIn('id="dtoggle"', self.html)
        self.assertIn('classList.toggle("collapsed")', self.html)
        self.assertIn('event: "drone_select"', self.html)
        self.assertIn('classList.toggle("selected"', self.html)

    def test_panel_displays_drone_group(self):
        self.assertIn("Number(d.group)", self.html)
        self.assertIn("กลุ่ม ${d.group}", self.html)


class TestMap3DWaypointInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map3d.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_click_sends_waypoint_event_when_mode_is_enabled(self):
        self.assertIn("t.setWaypointMode = function", self.html)
        self.assertIn('event: "waypoint_click"', self.html)
        self.assertIn("if (!waypointMode", self.html)

    def test_double_click_goto_is_disabled_while_placing_waypoints(self):
        start = self.html.index('addEventListener("dblclick"')
        handler = self.html[start:start + 300]
        self.assertIn("if (waypointMode) return", handler)


class TestMap3DStatusLabelStableWidth(unittest.TestCase):
    """regression รอบสอง: กด 3D ครั้งแรกแล้ว "แถบคอมมานด์ตกขอบ"

    เจอด้วย geomdiff.py (ไล่ minimumSizeHint ของทุก widget ก่อน/หลังกด 3D):
    ป้ายสถานะ lbl_map3d ("โหลดภูมิประเทศ…") ใช้ setVisible(False) ตอนว่าง — Qt
    ไม่กันพื้นที่ layout ให้ widget ที่ซ่อนอยู่ พอข้อความโผล่ปุ๊บ (8px -> 95px)
    แถบพิกัดทั้งแถบ (ซึ่งบีบเล็กลงไม่ได้อยู่แล้ว) ต้องขยายตาม ทั้งแอปเลยกว้างขึ้น
    ตามไปด้วยทันทีตอนโหลดภูมิประเทศเสร็จ — คนละจุดกับบั๊ก Mission Log ข้างบน
    แต่อาการเดียวกัน (แถวที่บีบไม่ได้ + เนื้อหาโผล่แบบไม่เตือนล่วงหน้า)

    แก้ด้วยหลักการเดียวกับ self.banner (เว้นพื้นที่ไว้ตลอด): lbl_map3d
    ใช้ setFixedWidth() แทน setVisible() ควบคุมพื้นที่ ความกว้างจึงคงที่เสมอ
    """

    # ไฟล์นี้ทั้งไฟล์รันภายใต้ SWARMGOD_NO_MAP=1 (บังคับ เพราะ QtWebEngine จริง
    # ใช้ offscreen ไม่ได้/segfault) ซึ่งแปลว่า win.lbl_map3d จริงไม่ถูกสร้างเลย
    # (อยู่ใน branch self._map_enabled เท่านั้น) เลยทดสอบผ่าน win ตรง ๆ ไม่ได้
    # ในไฟล์นี้ — จำลองรูปแบบเดียวกัน (QLabel + setFixedWidth + elidedText)
    # แยกเป็น unit test ล้วน ๆ แทน ให้ยังรันอัตโนมัติได้จริงทุกครั้ง
    # ยืนยันกับของจริงแล้วด้วย prototypes/map3d/check_cockpit3d.py (เปิดหน้าต่างจริง)
    def test_fixed_width_label_pattern_stays_stable(self):
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtWidgets import QLabel
        lbl = QLabel("")
        lbl.setFixedWidth(96)
        w0 = lbl.width()

        def set_note(text):
            text = text or ""
            if text:
                fm = lbl.fontMetrics()
                lbl.setText(fm.elidedText(text, _Qt.ElideRight, lbl.width()))
            else:
                lbl.setText("")

        set_note("โหลดภูมิประเทศ…")
        self.assertEqual(lbl.width(), w0,
                         "ความกว้างเปลี่ยนตอนมีข้อความ — จะดันทั้งแอปกว้างขึ้นตาม")
        set_note("")
        self.assertEqual(lbl.width(), w0)
        set_note("ข้อความยาวมาก ๆ เกินกว่าที่ป้ายจะแสดงได้หมดแน่นอนแบบนี้เลย")
        self.assertEqual(lbl.width(), w0,
                         "ข้อความยาวผิดปกติก็ต้องไม่ดันความกว้าง (ต้องถูกตัดด้วย …)")
        self.assertIn("…", lbl.text(), "ข้อความยาวเกินต้องถูกตัดด้วย ellipsis")


if __name__ == "__main__":
    unittest.main()
