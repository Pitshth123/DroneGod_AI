"""
เทสต์ Field Tablet เฟส 1 — ดูอย่างเดียว (docs/FIELD_TABLET.md)

    cd frontend
    python -m unittest tests.test_field_server -v

ชุดนี้ไม่ต้องใช้ Qt เลย (field_server.py ไม่ import Qt) จึงยิงเซิร์ฟเวอร์จริง
แล้วคุยด้วย raw socket ได้ — จำเป็น เพราะ urllib ย่อ '..' และตั้ง Host เองไม่ได้
ทำให้เทสต์ด่านความปลอดภัยกลวงถ้าใช้ urllib
"""
import json
import os
import socket
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import field_server as fs  # noqa: E402

_ASSETS = os.path.join(_FRONTEND, "swarmgod_gui", "assets")


def _raw(port, req_path, method="GET", host=None, cookie="", body=None,
         timeout=5.0, read_all=True):
    """ยิง HTTP ดิบ → (status_code:int, headers:dict, body:bytes)"""
    s = socket.create_connection(("127.0.0.1", port), timeout)
    s.settimeout(timeout)
    hdr = "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n" % (
        method, req_path, host or ("127.0.0.1:%d" % port))
    if cookie:
        hdr += "Cookie: %s\r\n" % cookie
    payload = b""
    if body is not None:
        payload = body if isinstance(body, bytes) else body.encode()
        hdr += "Content-Type: application/json\r\n"
        hdr += "Content-Length: %d\r\n" % len(payload)
    hdr += "\r\n"
    s.sendall(hdr.encode() + payload)
    buf = b""
    try:
        while read_all:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    finally:
        s.close()
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    code = int(lines[0].split(" ")[1]) if len(lines[0].split(" ")) > 1 else 0
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        headers.setdefault(k.strip().lower(), []).append(v.strip())
    return code, headers, rest


class Base(unittest.TestCase):
    RATE = 5.0

    def setUp(self):
        self.hub = fs.TelemetryHub()
        self.sessions = fs.SessionStore()
        self.srv = fs.FieldServer(_ASSETS, self.hub, self.sessions,
                                  tiles=None, host="127.0.0.1", port=0,
                                  rate_hz=self.RATE)
        self.srv.start()
        self.port = self.srv.port

    def tearDown(self):
        self.srv.stop()

    def pair(self):
        """จับคู่สำเร็จ → คืน cookie string พร้อมใช้"""
        pin = self.sessions.new_pin()
        code, hdrs, _ = _raw(self.port, "/api/pair", "POST",
                             body=json.dumps({"pin": pin}))
        self.assertEqual(code, 200)
        sc = hdrs["set-cookie"][0]
        return sc.split(";")[0]


# ══════════════════════════════════════════════════════════════
class TestNoCommandSurface(Base):
    """ทางเข้าคำสั่งต้องมีจุดเดียวและมีด่านครบ — ไม่มีทางลัดอื่น

    เฟส 2 เปิด `/api/command` แล้ว แต่ต้องเป็น **ทางเดียว** ที่เข้าถึงคำสั่งได้
    และต้องผ่าน allowlist + สิทธิ์ + rate limit เสมอ (ดู test_field_control.py)
    """

    def test_module_never_imports_grpc_or_qt(self):
        """field_server ต้องไม่ยิงคำสั่งเอง — ส่งต่อให้ callback เท่านั้น
        และห้าม import Qt เพราะรันบน HTTP thread"""
        with open(fs.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("grpc", "PyQt5", "swarmgod_pb2", "grpc_client"):
            self.assertNotIn(bad, src,
                             "field_server.py ไม่ควรแตะ %s" % bad)

    def test_no_per_command_shortcut_paths_exist(self):
        """ห้ามมี endpoint ต่อคำสั่ง — ไม่งั้นเพิ่มของใหม่แล้วลืมใส่ด่านได้ง่าย"""
        ck = self.pair()
        for p in ("/api/arm", "/api/takeoff", "/api/land", "/api/rtl",
                  "/api/goto", "/api/servo", "/api/hold", "/api/estop",
                  "/api/rc", "/api/disarm", "/api/kill", "/api/swarm"):
            for m in ("POST", "GET"):
                code, _, _ = _raw(self.port, p, m, cookie=ck,
                                  body="{}" if m == "POST" else None)
                self.assertEqual(code, 404,
                                 "เจอทางสั่งการลัดที่ไม่ควรมี: %s %s" % (m, p))

    def test_command_endpoint_refuses_a_viewer(self):
        """เพิ่งจับคู่ = viewer ยังไม่ได้ถือสิทธิ์ ต้องสั่งบินไม่ได้"""
        ck = self.pair()
        code, _, _ = _raw(self.port, "/api/command", "POST", cookie=ck,
                          body='{"action":"arm"}')
        self.assertEqual(code, 403)

    def test_command_endpoint_needs_pairing(self):
        code, _, _ = _raw(self.port, "/api/command", "POST",
                          body='{"action":"hold"}')
        self.assertEqual(code, 401)

    def test_only_known_get_paths_answer_200(self):
        ck = self.pair()
        allowed_200 = {"/", "/tablet.html", "/api/hello", "/api/state"}
        for p in sorted(allowed_200):
            code, _, _ = _raw(self.port, p, cookie=ck)
            self.assertEqual(code, 200, "ควรเสิร์ฟได้: %s" % p)

    def test_post_surface_is_exactly_three_endpoints(self):
        """POST มีได้แค่ pair / control / command — ของใหม่ต้องเพิ่มเทสต์ด่านด้วย"""
        ck = self.pair()
        self.assertEqual(
            _raw(self.port, "/api/state", "POST", cookie=ck, body="{}")[0], 404)
        for p in ("/api/pair", "/api/control", "/api/command"):
            code, _, _ = _raw(self.port, p, "POST", cookie=ck, body="{}")
            self.assertNotEqual(code, 404, "%s ควรมีอยู่" % p)


# ══════════════════════════════════════════════════════════════
class TestPairing(Base):
    def test_state_requires_pairing(self):
        code, _, _ = _raw(self.port, "/api/state")
        self.assertEqual(code, 401)

    def test_events_requires_pairing(self):
        code, _, _ = _raw(self.port, "/api/events", timeout=2.0)
        self.assertEqual(code, 401)

    def test_hello_says_not_paired_without_cookie(self):
        code, _, body = _raw(self.port, "/api/hello")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["paired"])

    def test_wrong_pin_rejected(self):
        self.sessions.new_pin()
        code, _, _ = _raw(self.port, "/api/pair", "POST",
                          body=json.dumps({"pin": "000000"}))
        self.assertIn(code, (403,))

    def test_pair_then_state_works(self):
        ck = self.pair()
        self.hub.update(3, {"name": "Drone 3", "lat": 14.0, "lon": 100.0})
        code, _, body = _raw(self.port, "/api/state", cookie=ck)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["drones"][0]["id"], 3)

    def test_cookie_is_httponly_and_samesite(self):
        pin = self.sessions.new_pin()
        _, hdrs, _ = _raw(self.port, "/api/pair", "POST",
                          body=json.dumps({"pin": pin}))
        sc = hdrs["set-cookie"][0]
        self.assertIn("HttpOnly", sc)      # สคริปต์ในหน้าอ่าน token ไม่ได้
        self.assertIn("SameSite=Strict", sc)  # กัน CSRF จากเว็บอื่น

    def test_brute_force_kills_the_pin(self):
        """PIN 6 หลัก = 1M ชุด ถ้าเดาได้ไม่จำกัดครั้ง ยิงบน LAN แตกแน่"""
        pin = self.sessions.new_pin()
        for _ in range(fs.MAX_PIN_TRIES):
            self.assertIsNone(self.sessions.pair("999999" if pin != "999999"
                                                 else "111111"))
        # เดาครบโควตาแล้ว PIN ที่ถูกต้องก็ใช้ไม่ได้อีก
        self.assertIsNone(self.sessions.pair(pin))
        self.assertEqual(self.sessions.pin(), "")

    def test_expired_pin_rejected(self):
        st = fs.SessionStore(pin_ttl=0.05)
        pin = st.new_pin()
        time.sleep(0.12)
        self.assertIsNone(st.pair(pin))

    def test_kick_all_invalidates_live_session(self):
        ck = self.pair()
        self.assertEqual(_raw(self.port, "/api/state", cookie=ck)[0], 200)
        self.sessions.kick_all()
        self.assertEqual(_raw(self.port, "/api/state", cookie=ck)[0], 401)

    def test_forged_cookie_rejected(self):
        code, _, _ = _raw(self.port, "/api/state",
                          cookie="%s=not-a-real-token" % fs.COOKIE_NAME)
        self.assertEqual(code, 401)

    def test_expired_session_rejected(self):
        st = fs.SessionStore(session_ttl=0.05)
        pin = st.new_pin()
        tok = st.pair(pin)
        self.assertTrue(st.valid(tok))
        time.sleep(0.12)
        self.assertFalse(st.valid(tok))


# ══════════════════════════════════════════════════════════════
class TestHostHeader(Base):
    """กัน DNS rebinding — เว็บภายนอกชี้โดเมนมาที่ IP วง LAN ไม่ได้"""

    def test_domain_host_rejected(self):
        for bad in ("evil.example.com", "attacker.io:8760", "swarmgod.local"):
            code, _, _ = _raw(self.port, "/api/hello", host=bad)
            self.assertEqual(code, 421, "Host โดเมนต้องถูกปฏิเสธ: %s" % bad)

    def test_ip_host_accepted(self):
        code, _, _ = _raw(self.port, "/api/hello",
                          host="127.0.0.1:%d" % self.port)
        self.assertEqual(code, 200)

    def test_helper_accepts_ip_and_localhost_only(self):
        for good in ("127.0.0.1", "192.168.1.20:8760", "localhost:8760",
                     "[::1]:8760", "10.0.0.5"):
            self.assertTrue(fs._host_header_ok(good), good)
        for bad in ("", "example.com", "a.b.c.d", "127.0.0.1.evil.com"):
            self.assertFalse(fs._host_header_ok(bad), bad)


# ══════════════════════════════════════════════════════════════
class TestAssetServing(Base):
    def test_serves_tablet_page(self):
        code, _, body = _raw(self.port, "/")
        self.assertEqual(code, 200)
        self.assertIn(b"SWARMGOD FIELD", body)

    def test_serves_leaflet(self):
        self.assertEqual(_raw(self.port, "/leaflet/leaflet.js")[0], 200)

    def test_blocks_path_traversal(self):
        for evil in ("/leaflet/../../../../../../Windows/win.ini",
                     "/leaflet/../app.py",
                     "/tablet/../core/grpc_client.py",
                     "/leaflet/..%2f..%2fapp.py"):
            code, _, _ = _raw(self.port, evil)
            self.assertEqual(code, 404, "หลุดด่าน path traversal: %s" % evil)

    def test_source_files_not_reachable(self):
        for p in ("/app.py", "/core/grpc_client.py", "/map.html",
                  "/../swarmgod_gui/app.py"):
            self.assertEqual(_raw(self.port, p)[0], 404, p)

    def test_no_cors_header_on_api(self):
        """ถ้ามี Access-Control-Allow-Origin เว็บอื่นจะอ่าน telemetry ได้"""
        _, hdrs, _ = _raw(self.port, "/api/hello")
        self.assertNotIn("access-control-allow-origin", hdrs)


# ══════════════════════════════════════════════════════════════
class TestTelemetryHub(unittest.TestCase):
    def test_snapshot_has_age_and_sorted_ids(self):
        hub = fs.TelemetryHub()
        hub.update(5, {"name": "E"})
        hub.update(1, {"name": "A"})
        snap = hub.snapshot()
        self.assertEqual([d["id"] for d in snap["drones"]], [1, 5])
        self.assertIn("age", snap["drones"][0])

    def test_age_grows(self):
        hub = fs.TelemetryHub()
        hub.update(1, {})
        time.sleep(0.25)
        self.assertGreaterEqual(hub.snapshot()["drones"][0]["age"], 0.2)

    def test_seq_changes_only_on_write(self):
        hub = fs.TelemetryHub()
        a = hub.snapshot()["seq"]
        self.assertEqual(hub.snapshot()["seq"], a)
        hub.update(1, {})
        self.assertNotEqual(hub.snapshot()["seq"], a)

    def test_remove_and_clear(self):
        hub = fs.TelemetryHub()
        hub.update(1, {})
        hub.update(2, {})
        hub.remove(1)
        self.assertEqual(len(hub.snapshot()["drones"]), 1)
        hub.clear()
        self.assertEqual(hub.snapshot()["drones"], [])

    def test_internal_fields_never_leak(self):
        hub = fs.TelemetryHub()
        hub.update(1, {"name": "x"})
        self.assertNotIn("_t", hub.snapshot()["drones"][0])

    def test_snapshot_is_json_serializable(self):
        hub = fs.TelemetryHub()
        hub.update(1, {"name": "x", "lat": 1.0, "armed": True, "sats": 9})
        json.dumps(hub.snapshot())

    def test_caller_dict_is_copied_not_aliased(self):
        """ถ้าเก็บ reference ไว้ ผู้เรียกแก้ dict ทีหลังจะแก้ข้อมูลข้าม thread"""
        hub = fs.TelemetryHub()
        src = {"name": "before"}
        hub.update(1, src)
        src["name"] = "after"
        self.assertEqual(hub.snapshot()["drones"][0]["name"], "before")

    def test_concurrent_writes_do_not_corrupt(self):
        hub = fs.TelemetryHub()
        stop = threading.Event()

        def writer(base):
            while not stop.is_set():
                hub.update(base, {"n": base})

        ts = [threading.Thread(target=writer, args=(i,), daemon=True)
              for i in range(1, 5)]
        [t.start() for t in ts]
        for _ in range(200):
            json.dumps(hub.snapshot())      # อ่านพร้อมเขียนต้องไม่ระเบิด
        stop.set()
        [t.join(2) for t in ts]
        self.assertEqual(len(hub.snapshot()["drones"]), 4)


# ══════════════════════════════════════════════════════════════
class TestSseThrottle(Base):
    RATE = 5.0

    def test_stream_is_throttled_regardless_of_update_rate(self):
        """5 ลำ × 10Hz = 50 อัปเดต/วิ แต่ SSE ต้องไม่เกิน rate_hz (spec §5)"""
        ck = self.pair()
        stop = threading.Event()

        def spam():
            while not stop.is_set():
                for d in range(1, 6):
                    self.hub.update(d, {"name": "D%d" % d, "lat": 14.0})
                time.sleep(0.002)

        t = threading.Thread(target=spam, daemon=True)
        t.start()

        s = socket.create_connection(("127.0.0.1", self.port), 5)
        s.settimeout(5)
        s.sendall(("GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                   "Cookie: %s\r\nConnection: close\r\n\r\n"
                   % (self.port, ck)).encode())
        buf, dur = b"", 2.0
        end = time.time() + dur
        try:
            while time.time() < end:
                buf += s.recv(65536)
        except socket.timeout:
            pass
        finally:
            s.close()
            stop.set()
            t.join(2)

        frames = buf.count(b"data:")
        cap = int(self.RATE * dur) + 3          # เผื่อ jitter ตอนเริ่ม/จบ
        self.assertGreater(frames, 0, "ไม่ได้รับ telemetry เลย")
        self.assertLessEqual(frames, cap,
                             "SSE ส่ง %d เฟรมใน %.0fs เกินเพดาน %d"
                             % (frames, dur, cap))

    def test_stream_slot_released_after_disconnect(self):
        ck = self.pair()
        s = socket.create_connection(("127.0.0.1", self.port), 5)
        s.sendall(("GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                   "Cookie: %s\r\nConnection: close\r\n\r\n"
                   % (self.port, ck)).encode())
        time.sleep(0.4)
        self.assertEqual(self.srv.stream_count, 1)
        s.close()
        # ตัวส่งจะรู้ว่าสายขาดตอนเขียนรอบถัดไป
        for _ in range(60):
            self.hub.update(1, {"n": time.time()})
            if self.srv.stream_count == 0:
                break
            time.sleep(0.1)
        self.assertEqual(self.srv.stream_count, 0,
                         "ปล่อยสายไม่คืน slot — tablet เดินหลุดระยะบ่อย ๆ แล้ว thread หมด")


# ══════════════════════════════════════════════════════════════
class TestLifecycle(unittest.TestCase):
    def test_stop_frees_the_port_completely(self):
        """ปิด LAN = ไม่มี listener จริง ไม่ใช่แค่ปฏิเสธคำขอ (spec §3.1)"""
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        srv = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        srv.start()
        port = srv.port
        self.assertEqual(_raw(port, "/api/hello")[0], 200)
        srv.stop()
        time.sleep(0.3)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), 1.5).close()

    def test_urls_are_shown_for_typing_on_tablet(self):
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        srv = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        try:
            urls = srv.urls()
            self.assertTrue(urls and all(u.startswith("http://") for u in urls))
            self.assertTrue(all(str(srv.port) in u for u in urls))
        finally:
            srv.stop()

    def test_stop_without_start_does_not_hang(self):
        """socketserver.shutdown() บล็อกถาวรถ้า serve_forever() ไม่เคยรัน
        ปุ่มปิด LAN เรียกจาก Qt main thread → บล็อกตรงนั้นคือคอกพิตค้างทั้งตัว"""
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        srv = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        done = threading.Event()
        threading.Thread(target=lambda: (srv.stop(), done.set()),
                         daemon=True).start()
        self.assertTrue(done.wait(5), "stop() ค้างเมื่อยังไม่เคย start()")

    def test_second_server_cannot_shadow_the_first_on_windows(self):
        """เปิดคอกพิตซ้อนกันแล้วตัวที่สองต้องล้มเหลวเสียงดัง ไม่ใช่แย่งพอร์ตเงียบ ๆ

        บน Windows SO_REUSEADDR แย่งพอร์ตที่มีคนใช้อยู่ได้ ตัวหลังจะดูเหมือน
        เปิดสำเร็จแต่คำขอยังไปหาตัวเก่า → แท็บเล็ตคุยกับคอกพิตคนละตัวที่คุม
        โดรนคนละชุด โดยไม่มีอะไรฟ้องเลย
        """
        if os.name != "nt":
            self.skipTest("พฤติกรรมนี้เป็นของ Windows")
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        a = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        a.start()
        try:
            with self.assertRaises(OSError):
                fs.FieldServer(_ASSETS, fs.TelemetryHub(), fs.SessionStore(),
                               host="127.0.0.1", port=a.port)
        finally:
            a.stop()

    def test_port_is_reusable_after_a_clean_stop(self):
        """ปิดแล้วเปิดใหม่ที่พอร์ตเดิมต้องได้ ไม่งั้นกดปิด/เปิด LAN ซ้ำไม่ได้"""
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        a = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        a.start()
        port = a.port
        a.stop()
        time.sleep(0.3)
        b = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=port)
        b.start()
        try:
            self.assertEqual(_raw(port, "/api/hello")[0], 200)
        finally:
            b.stop()

    def test_stop_twice_is_safe(self):
        hub, ses = fs.TelemetryHub(), fs.SessionStore()
        srv = fs.FieldServer(_ASSETS, hub, ses, host="127.0.0.1", port=0)
        srv.start()
        srv.stop()
        done = threading.Event()
        threading.Thread(target=lambda: (srv.stop(), done.set()),
                         daemon=True).start()
        self.assertTrue(done.wait(5), "stop() ซ้ำแล้วค้าง")


if __name__ == "__main__":
    unittest.main()
