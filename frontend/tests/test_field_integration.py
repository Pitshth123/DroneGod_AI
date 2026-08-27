"""
เทสต์การต่อ Field Tablet เข้ากับคอกพิต (docs/FIELD_TABLET.md เฟส 1)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_field_integration -v

ชุดนี้เน้น 3 เรื่องที่ทำพลาดแล้วอันตราย/พังจริง:
  1. ค่าเริ่มต้นต้องไม่เปิดพอร์ตใด ๆ
  2. ข้อมูลที่ส่งออก LAN ต้องเป็น dict ล้วน (ห้าม protobuf/Qt ข้าม thread)
  3. เปิด/ปิดซ้ำ ๆ ต้องไม่ค้างและไม่ทิ้งพอร์ตเปิดไว้
"""
import json
import os
import socket
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.core import field_server as fs  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Base(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake

    def tearDown(self):
        try:
            self.win._field_stop()
        except Exception:
            pass
        # deleteLater() จำเป็น ไม่ใช่แค่ความสะอาด — ถ้าไม่ทำลายหน้าต่างจริง
        # ตัวที่ค้างจะสะสมจนสร้าง GroundStation ตัวถัดไปไม่ได้ (แขวนทั้ง process)
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def start_field(self):
        """ผูก 127.0.0.1 พอร์ตสุ่ม — เทสต์ไม่ควรเปิด listener ออก LAN จริง
        ทางที่ผู้ใช้กดจริง (0.0.0.0:8760) มีเทสต์แยกที่ TestLifecycle"""
        ok, msg = self.win._field_start(host="127.0.0.1", port=0)
        self.assertTrue(ok, msg)
        return ok, msg


class TestClosedByDefault(Base):
    def test_no_server_on_startup(self):
        """ไม่เปิดเอง — ต้องกดเปิดเท่านั้น (spec §3.1 ข้อ 1)"""
        self.assertIsNone(self.win.field)

    def test_pill_reads_off(self):
        self.assertIn("OFF", self.win.btn_field.text())

    def test_hub_exists_but_empty(self):
        self.assertEqual(self.win.field_hub.snapshot()["drones"], [])


class TestLifecycle(Base):
    def test_start_then_stop_frees_port(self):
        ok, msg = self.start_field()
        self.assertTrue(ok, msg)
        port = self.win.field.port
        self.assertIn("●", self.win.btn_field.text())
        # เข้าถึงได้จริงตอนเปิด
        socket.create_connection(("127.0.0.1", port), 2).close()

        self.win._field_stop()
        self.assertIsNone(self.win.field)
        self.assertIn("OFF", self.win.btn_field.text())
        _pump(0.3)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), 1.5).close()

    def test_start_issues_a_pin(self):
        self.start_field()
        pin, ttl, urls = self.win._field_pin_info()
        self.assertEqual(len(pin), 6)
        self.assertTrue(pin.isdigit())
        self.assertGreater(ttl, 0)
        self.assertTrue(urls)

    def test_regen_gives_a_different_pin(self):
        self.start_field()
        first = self.win._field_pin_info()[0]
        second = self.win._field_pin_info(regen=True)[0]
        self.assertNotEqual(first, second)

    def test_stop_kicks_every_session(self):
        self.start_field()
        pin = self.win._field_pin_info()[0]
        self.assertIsNotNone(self.win.field_sessions.pair(pin))
        self.assertEqual(self.win.field_sessions.count(), 1)
        self.win._field_stop()
        self.assertEqual(self.win.field_sessions.count(), 0)

    def test_repeated_toggle_is_stable(self):
        for _ in range(3):
            ok, msg = self.start_field()
            self.assertTrue(ok, msg)
            self.win._field_stop()
        self.assertIsNone(self.win.field)

    def test_production_defaults_bind_lan_on_fixed_port(self):
        """ทางที่ผู้ใช้กดจริง: ทุกอินเทอร์เฟซ + พอร์ตคงที่ (จะได้พิมพ์ URL เดิมได้)
        เทสต์อื่นผูก 127.0.0.1 เพื่อไม่เปิด listener ออก LAN ระหว่างรันเทสต์"""
        ok, msg = self.win._field_start()
        self.assertTrue(ok, msg)
        try:
            self.assertEqual(self.win.field.port, fs.DEFAULT_PORT)
            self.assertEqual(self.win.field.server_address[0], "0.0.0.0")
            self.assertTrue(self.win.field.urls())
        finally:
            self.win._field_stop()

    def test_double_start_is_noop(self):
        self.start_field()
        srv = self.win.field
        self.start_field()
        self.assertIs(self.win.field, srv)


class TestTelemetryBridge(Base):
    def feed(self, did=1, lat=14.5, lon=100.5, alt=12.0):
        t = _fake_telem(did, lat, lon, alt)
        self.win._on_telemetry(t)
        return t

    def test_nothing_published_while_lan_is_off(self):
        self.feed()
        self.assertEqual(self.win.field_hub.snapshot()["drones"], [])

    def test_telemetry_reaches_hub_when_on(self):
        self.start_field()
        self.feed(did=2, lat=14.25, lon=100.75, alt=30.0)
        d = self.win.field_hub.snapshot()["drones"][0]
        self.assertEqual(d["id"], 2)
        self.assertAlmostEqual(d["lat"], 14.25, places=4)
        self.assertAlmostEqual(d["lon"], 100.75, places=4)
        self.assertAlmostEqual(d["alt"], 30.0, places=3)

    def test_payload_is_plain_json(self):
        """ห้ามหลุด protobuf/Qt object ไปให้ HTTP thread — serialize ต้องผ่าน"""
        self.start_field()
        self.feed()
        blob = json.dumps(self.win.field_hub.snapshot())
        self.assertIn('"color"', blob)
        for v in self.win.field_hub.snapshot()["drones"][0].values():
            self.assertIsInstance(v, (int, float, str, bool))

    def test_color_matches_desktop(self):
        from swarmgod_gui.core.theme import drone_color, set_drone_color
        self.start_field()
        set_drone_color(4, "#123456")
        self.feed(did=4)
        self.assertEqual(self.win.field_hub.snapshot()["drones"][0]["color"],
                         drone_color(4))

    def test_existing_fleet_is_backfilled_on_start(self):
        """เปิด LAN ทีหลัง ต้องเห็นลำที่ต่ออยู่แล้วทันที ไม่ต้องรอรอบถัดไป"""
        self.feed(did=7)
        self.start_field()
        ids = [d["id"] for d in self.win.field_hub.snapshot()["drones"]]
        self.assertIn(7, ids)

    def test_removed_drone_disappears_from_hub(self):
        self.start_field()
        self.feed(did=5)
        self.assertTrue(self.win.field_hub.snapshot()["drones"])
        self.win._remove_fleet_item(5)
        self.assertEqual(self.win.field_hub.snapshot()["drones"], [],
                         "ลำที่ถอดออกแล้วยังค้างบนแท็บเล็ต = ลำผี")

    def test_stop_clears_the_hub(self):
        self.start_field()
        self.feed()
        self.win._field_stop()
        self.assertEqual(self.win.field_hub.snapshot()["drones"], [])


class TestNoCommandPathFromWeb(Base):
    """ด่านสุดท้าย: เฟส 1 ต้องไม่มีทางใดที่เว็บสั่งโดรนได้"""

    def test_web_traffic_sends_no_grpc_call(self):
        self.start_field()
        port = self.win.field.port
        pin = self.win._field_pin_info()[0]

        def hit(path, method="GET", cookie="", body=None):
            s = socket.create_connection(("127.0.0.1", port), 3)
            s.settimeout(3)
            req = "%s %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n" % (
                method, path, port)
            if cookie:
                req += "Cookie: %s\r\n" % cookie
            payload = b""
            if body is not None:
                payload = body.encode()
                req += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(payload)
            s.sendall((req + "\r\n").encode() + payload)
            buf = b""
            try:
                while True:
                    c = s.recv(4096)
                    if not c:
                        break
                    buf += c
            except socket.timeout:
                pass
            s.close()
            return buf

        raw = hit("/api/pair", "POST", body=json.dumps({"pin": pin}))
        ck = ""
        for ln in raw.split(b"\r\n"):
            if ln.lower().startswith(b"set-cookie:"):
                ck = ln.split(b":", 1)[1].decode().strip().split(";")[0]
        self.assertTrue(ck)

        before = len(self.fake.calls)
        for p in ("/api/arm", "/api/takeoff", "/api/land", "/api/rtl",
                  "/api/goto", "/api/servo", "/api/estop", "/api/command"):
            hit(p, "POST", cookie=ck, body="{}")
            hit(p, "GET", cookie=ck)
        hit("/api/state", cookie=ck)
        self.assertEqual(len(self.fake.calls), before,
                         "มีคำสั่งวิ่งไปหา core จากคำขอทางเว็บ — เฟส 1 ห้ามเด็ดขาด")

    def test_app_module_exposes_no_web_command_hook(self):
        for bad in ("_field_arm", "_field_takeoff", "_field_command",
                    "_field_execute", "_field_dispatch"):
            self.assertFalse(hasattr(self.win, bad),
                             "เจอ hook สั่งการจากเว็บที่ไม่ควรมีในเฟส 1: %s" % bad)


class TestWebCommandDispatch(Base):
    """คำสั่งจากแท็บเล็ต → เมธอดเดิมของ desktop → _selected_or_all() → core

    เฟส 2: ถึงตรงนี้ field_server ตรวจสิทธิ์/allowlist/rate limit มาแล้ว
    ที่นี่ตรวจว่าคำสั่ง "ลงจริง" ที่ไหน และตรวจด่านฝั่งคอกพิตที่เหลือ
    """

    def setUp(self):
        super().setUp()
        self.win._on_telemetry(_fake_telem(1, 14.5, 100.5, 20.0))
        self.win._selected_ids = {1}
        self.win._selected_id = 1
        self.fake.calls.clear()

    def send(self, action, **params):
        return self.win._on_web_command("sess1234", action, params)

    def kinds(self):
        return [c[0] for c in self.fake.calls]

    # ── หยุด: ทุกคนกดได้ ──
    def test_hold_reaches_the_core(self):
        ok, _ = self.send("hold")
        _pump(0.4)
        self.assertTrue(ok)
        self.assertIn("hold", self.kinds())

    def test_estop_reaches_the_core(self):
        ok, _ = self.send("estop")
        _pump(0.5)
        self.assertTrue(ok)
        self.assertTrue(self.fake.calls, "E-STOP จากแท็บเล็ตไม่ถึง core")

    # ── takeoff ──
    def test_takeoff_without_confirmation_is_refused(self):
        ok, msg = self.send("takeoff", alt=10)
        _pump(0.3)
        self.assertFalse(ok)
        self.assertEqual(self.fake.calls, [])

    def test_takeoff_with_absurd_altitude_is_refused(self):
        for alt in (0, -5, 500, 1e9, float("nan")):
            ok, _ = self.send("takeoff", alt=alt, confirmed=True)
            self.assertFalse(ok, "ยอมรับความสูง %r" % alt)
        _pump(0.3)
        self.assertEqual(self.fake.calls, [])

    def test_takeoff_missing_altitude_is_refused(self):
        ok, _ = self.send("takeoff", confirmed=True)
        self.assertFalse(ok)

    def test_valid_takeoff_goes_through_selected_or_all(self):
        self.win._preflight.mark_ready() if hasattr(
            self.win._preflight, "mark_ready") else None
        ok, _ = self.send("takeoff", alt=15, confirmed=True)
        _pump(0.4)
        if ok:      # ผ่านด่าน preflight
            self.assertIn(("takeoff", (1,), 15.0), self.fake.calls)
        else:       # ถูกด่าน preflight กั้น — ต้องไม่มีคำสั่งหลุดไป
            self.assertEqual(self.fake.calls, [])

    # ── goto ──
    def test_goto_with_bad_coordinates_is_refused(self):
        for lat, lon in ((0, 0), (91, 100), (14, 999), ("x", 1), (None, None)):
            ok, _ = self.send("goto", lat=lat, lon=lon)
            self.assertFalse(ok, "ยอมรับพิกัด %r,%r" % (lat, lon))
        _pump(0.3)
        self.assertEqual(self.fake.calls, [])

    def test_goto_refused_for_multiple_drones(self):
        self.win._on_telemetry(_fake_telem(2, 14.6, 100.6, 20.0))
        self.win._selected_ids = {1, 2}
        self.fake.calls.clear()
        ok, msg = self.send("goto", lat=14.55, lon=100.55)
        _pump(0.3)
        self.assertFalse(ok, "GOTO หลายลำไปจุดเดียวกัน = เสี่ยงชน")
        self.assertEqual(self.fake.calls, [])

    def test_goto_refused_when_drone_is_on_the_ground(self):
        self.win._last_alt[1] = 0.0
        ok, _ = self.send("goto", lat=14.55, lon=100.55)
        _pump(0.3)
        self.assertFalse(ok)
        self.assertEqual(self.fake.calls, [])

    def test_goto_when_airborne_uses_current_altitude(self):
        self.win._last_alt[1] = 32.0
        ok, _ = self.send("goto", lat=14.55, lon=100.55)
        _pump(0.5)
        self.assertTrue(ok)
        goto = [c for c in self.fake.calls if c[0] == "goto"]
        self.assertTrue(goto, "GOTO ไม่ถึง core")
        self.assertEqual(goto[0][4], 32.0, "ควรรักษาระดับความสูงเดิม")

    # ── servo ──
    def test_servo_without_confirmation_is_refused(self):
        ok, _ = self.send("servo", channel="A")
        _pump(0.3)
        self.assertFalse(ok)
        self.assertEqual(self.fake.calls, [])

    def test_servo_with_unknown_channel_is_refused(self):
        for ch in ("Z", "", "1", None):
            ok, _ = self.send("servo", channel=ch, confirmed=True)
            self.assertFalse(ok, "ยอมรับช่อง %r" % ch)
        _pump(0.3)
        self.assertEqual(self.fake.calls, [])

    # ── select ──
    def test_select_changes_the_target(self):
        self.win._on_telemetry(_fake_telem(2, 14.6, 100.6, 20.0))
        ok, _ = self.send("select", ids=[2])
        self.assertTrue(ok)
        self.assertEqual(self.win._selected_or_all(), [2])

    def test_select_rejects_offline_drone(self):
        ok, _ = self.send("select", ids=[99])
        self.assertFalse(ok)
        self.assertEqual(self.win._selected_or_all(), [1])

    def test_select_rejects_garbage(self):
        ok, _ = self.send("select", ids=["../etc/passwd"])
        self.assertFalse(ok)

    # ── ด่านทั่วไป ──
    def test_unknown_action_is_refused(self):
        for a in ("rc_move", "set_yaw", "kill", "disarm", "", "eval"):
            ok, _ = self.send(a)
            self.assertFalse(ok, "ยอมรับคำสั่ง %r" % a)
        _pump(0.3)
        self.assertEqual(self.fake.calls, [])

    def test_exception_inside_a_command_never_kills_the_qt_loop(self):
        """ถ้าคำสั่งพัง ต้องตอบว่าไม่สำเร็จ ไม่ใช่โยน exception ขึ้น event loop"""
        def boom():
            raise RuntimeError("จงพัง")
        self.win._cmd_hold = boom
        ok, msg = self.send("hold")
        self.assertFalse(ok)
        self.assertIn("ผิดพลาด", msg)

    def test_bridge_is_connected_so_http_thread_never_touches_qt(self):
        got = []
        self.win.web_bridge.command.connect(
            lambda s, a, p: got.append((s, a, p)))
        ok, msg = self.win.web_bridge.dispatch("s", "hold", {})
        _pump(0.3)
        self.assertTrue(ok)
        self.assertTrue(got, "signal ของ WebBridge ไม่ได้ถูกต่อ")


class TestControlWiring(Base):
    def test_server_gets_the_bridge_not_a_direct_call(self):
        self.start_field()
        self.assertEqual(self.win.field.on_command,
                         self.win.web_bridge.dispatch)
        self.assertEqual(self.win.field.on_control_change,
                         self.win.web_bridge.control_moved)

    def test_desktop_holds_control_when_lan_opens(self):
        self.start_field()
        self.assertTrue(self.win.field.control.desktop_has_control())

    def test_desktop_can_always_reclaim(self):
        self.start_field()
        self.win.field.control.claim("tablet-1", "iPad")
        self.assertFalse(self.win.field.control.desktop_has_control())
        self.assertTrue(self.win._field_reclaim())
        self.assertTrue(self.win.field.control.desktop_has_control())

    def test_banner_warns_while_control_is_elsewhere(self):
        self.start_field()
        self.win.field.control.claim("tablet-1", "iPad")
        self.win._field_control_changed()
        self.assertIn("สิทธิ์ควบคุม", self.win.banner.text())
        self.win._field_reclaim()
        self.assertEqual(self.win.banner.text().strip(), "")


class TestWindowWidth(Base):
    def test_field_button_does_not_blow_up_min_width(self):
        """ปุ่มบน topbar เคยดันหน้าต่างให้ล้นจอมาแล้ว — ล็อกเพดานไว้"""
        _pump(0.1)
        self.assertLess(self.win.minimumSizeHint().width(), 1600)

    def test_button_keeps_fixed_width_in_both_states(self):
        w_off = self.win.btn_field.width()
        self.start_field()
        self.win._field_paint()
        self.assertEqual(self.win.btn_field.width(), w_off,
                         "ปุ่มเปลี่ยนขนาดตามสถานะ = แถบขยับ = เสี่ยงล้นจอ")


if __name__ == "__main__":
    unittest.main()
