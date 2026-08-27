"""
เทสต์ Field Tablet เฟส 2 — สิทธิ์ควบคุม + คำสั่ง (docs/FIELD_TABLET.md §3.2, §4)

    cd frontend
    python -m unittest tests.test_field_control -v

ชุดนี้ไม่ใช้ Qt (field_server.py ไม่ import Qt) จึงยิงเซิร์ฟเวอร์จริงแล้วคุย
ด้วย raw socket ได้ — จำเป็นเพื่อพิสูจน์ด่านจริง ไม่ใช่แค่ซ่อนปุ่มบนหน้าเว็บ
"""
import json
import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import field_server as fs  # noqa: E402

from tests.test_field_server import _raw  # noqa: E402

_ASSETS = os.path.join(_FRONTEND, "swarmgod_gui", "assets")


# ══════════════════════════════════════════════════════════════
#  ControlToken — ตรรกะล้วน
# ══════════════════════════════════════════════════════════════
class TestControlToken(unittest.TestCase):
    def setUp(self):
        self.c = fs.ControlToken(timeout=0.4)

    def test_desktop_holds_at_start(self):
        self.assertTrue(self.c.desktop_has_control())
        self.assertTrue(self.c.status()["desktop"])

    def test_first_tablet_claims(self):
        ok, _ = self.c.claim("s1", "iPad")
        self.assertTrue(ok)
        self.assertTrue(self.c.is_pilot("s1"))
        self.assertFalse(self.c.desktop_has_control())

    def test_only_one_holder_at_a_time(self):
        self.c.claim("s1")
        ok, _ = self.c.claim("s2")
        self.assertFalse(ok, "เครื่องที่สองไม่ควรแย่งสิทธิ์ได้เอง")
        self.assertTrue(self.c.is_pilot("s1"))
        self.assertFalse(self.c.is_pilot("s2"))

    def test_desktop_reclaims_without_approval(self):
        self.c.claim("s1")
        self.c.reclaim_desktop()
        self.assertTrue(self.c.desktop_has_control())
        self.assertFalse(self.c.is_pilot("s1"))

    def test_handoff_needs_approval_from_holder(self):
        self.c.claim("s1")
        ok, _ = self.c.request_handoff("s2", "มือถือ")
        self.assertTrue(ok)
        self.assertTrue(self.c.is_pilot("s1"), "ขอแล้วต้องยังไม่ได้จนกว่าจะอนุมัติ")
        self.assertTrue(self.c.approve_handoff())
        self.assertTrue(self.c.is_pilot("s2"))
        self.assertFalse(self.c.is_pilot("s1"))

    def test_denied_handoff_leaves_holder_alone(self):
        self.c.claim("s1")
        self.c.request_handoff("s2")
        self.assertTrue(self.c.deny_handoff())
        self.assertTrue(self.c.is_pilot("s1"))
        self.assertFalse(self.c.is_pilot("s2"))

    def test_heartbeat_loss_returns_control_to_desktop(self):
        """แท็บเล็ตเดินหลุดระยะ WiFi แล้วถือสิทธิ์ค้าง = สั่งอะไรไม่ได้จากที่ไหนเลย"""
        self.c.claim("s1")
        time.sleep(0.6)
        self.assertTrue(self.c.desktop_has_control())
        self.assertFalse(self.c.is_pilot("s1"))
        self.assertIn("heartbeat", self.c.status()["reason"])

    def test_heartbeat_keeps_control(self):
        self.c.claim("s1")
        for _ in range(4):
            time.sleep(0.15)
            self.assertTrue(self.c.heartbeat("s1"))
        self.assertTrue(self.c.is_pilot("s1"))

    def test_heartbeat_from_non_holder_does_nothing(self):
        self.c.claim("s1")
        self.assertFalse(self.c.heartbeat("s2"))
        self.assertTrue(self.c.is_pilot("s1"))

    def test_release_gives_it_back(self):
        self.c.claim("s1")
        self.assertTrue(self.c.release("s1"))
        self.assertTrue(self.c.desktop_has_control())

    def test_generation_moves_on_every_change(self):
        g0 = self.c.gen
        self.c.claim("s1")
        g1 = self.c.gen
        self.assertNotEqual(g0, g1)
        self.c.reclaim_desktop()
        self.assertNotEqual(g1, self.c.gen)

    def test_empty_session_is_never_pilot(self):
        self.assertFalse(self.c.is_pilot(""))
        self.assertFalse(self.c.is_pilot(None))

    def test_concurrent_claims_yield_exactly_one_winner(self):
        wins = []
        lock = threading.Lock()

        def race(n):
            ok, _ = self.c.claim("s%d" % n)
            if ok:
                with lock:
                    wins.append(n)

        ts = [threading.Thread(target=race, args=(i,)) for i in range(12)]
        [t.start() for t in ts]
        [t.join(3) for t in ts]
        self.assertEqual(len(wins), 1, "แย่งสิทธิ์พร้อมกันแล้วได้มากกว่าหนึ่ง: %s" % wins)


# ══════════════════════════════════════════════════════════════
class TestRateLimiter(unittest.TestCase):
    def test_burst_then_blocked(self):
        rl = fs.RateLimiter(burst=3, per_sec=0.0)
        self.assertEqual([rl.allow("s") for _ in range(4)],
                         [True, True, True, False])

    def test_refills_over_time(self):
        rl = fs.RateLimiter(burst=1, per_sec=20.0)
        self.assertTrue(rl.allow("s"))
        self.assertFalse(rl.allow("s"))
        time.sleep(0.15)
        self.assertTrue(rl.allow("s"))

    def test_sessions_are_independent(self):
        rl = fs.RateLimiter(burst=1, per_sec=0.0)
        self.assertTrue(rl.allow("a"))
        self.assertTrue(rl.allow("b"), "session อื่นไม่ควรโดนโควตาของคนอื่น")


# ══════════════════════════════════════════════════════════════
#  ด่านจริงผ่าน HTTP
# ══════════════════════════════════════════════════════════════
class Base(unittest.TestCase):
    def setUp(self):
        self.hub = fs.TelemetryHub()
        self.sessions = fs.SessionStore()
        self.srv = fs.FieldServer(_ASSETS, self.hub, self.sessions,
                                  tiles=None, host="127.0.0.1", port=0)
        self.sent = []
        self.srv.on_command = self._record
        self.srv.start()
        self.port = self.srv.port

    def tearDown(self):
        self.srv.stop()

    def _record(self, session, action, params):
        self.sent.append((session[:6], action, params))
        return True, "ok"

    def pair(self):
        pin = self.sessions.new_pin()
        code, hdrs, _ = _raw(self.port, "/api/pair", "POST",
                             body=json.dumps({"pin": pin}))
        self.assertEqual(code, 200)
        return hdrs["set-cookie"][0].split(";")[0]

    def post(self, path, payload, cookie):
        code, _, body = _raw(self.port, path, "POST", cookie=cookie,
                             body=json.dumps(payload))
        try:
            return code, json.loads(body)
        except Exception:
            return code, {}

    def cmd(self, action, cookie, **params):
        return self.post("/api/command",
                         {"action": action, "params": params}, cookie)

    def control(self, action, cookie, **kw):
        kw["action"] = action
        return self.post("/api/control", kw, cookie)


class TestCommandGate(Base):
    def test_unpaired_cannot_command(self):
        code, _ = self.cmd("hold", "")
        self.assertEqual(code, 401)
        self.assertEqual(self.sent, [])

    def test_live_stick_commands_are_refused_even_when_pilot(self):
        """RcMove/SetYaw ไม่อยู่ใน allowlist — WiFi กระตุกตอนบังคับสด = อันตรายจริง"""
        ck = self.pair()
        self.control("claim", ck)
        for bad in ("rc_move", "rcmove", "set_yaw", "setyaw", "change_speed",
                    "manual", "kill", "disarm"):
            code, _ = self.cmd(bad, ck)
            self.assertEqual(code, 403, "หลุด allowlist: %s" % bad)
        self.assertEqual(self.sent, [], "มีคำสั่งต้องห้ามหลุดไปถึงคอกพิต")

    def test_viewer_cannot_send_pilot_commands(self):
        ck = self.pair()          # ยังไม่ได้ขอสิทธิ์ = viewer
        for act in ("arm", "takeoff", "land", "rtl", "goto", "servo",
                    "waypoint_execute", "select"):
            code, _ = self.cmd(act, ck)
            self.assertEqual(code, 403, "viewer สั่ง %s ได้ = ผิด" % act)
        self.assertEqual(self.sent, [])

    def test_viewer_can_always_stop(self):
        """หยุดคือทิศทางที่ปลอดภัยเสมอ — ห้ามเอาสิทธิ์ไปกั้น"""
        ck = self.pair()
        for act in ("hold", "hold_all", "estop"):
            code, body = self.cmd(act, ck)
            self.assertEqual(code, 200, "viewer กด %s ไม่ได้ = อันตราย" % act)
            self.assertTrue(body["ok"])
        self.assertEqual([a for _, a, _ in self.sent], ["hold", "hold_all", "estop"])

    def test_pilot_can_send_pilot_commands(self):
        ck = self.pair()
        ok, body = self.control("claim", ck, label="iPad")
        self.assertEqual(ok, 200)
        self.assertTrue(body["pilot"])
        code, _ = self.cmd("arm", ck)
        self.assertEqual(code, 200)
        self.assertEqual(self.sent[-1][1], "arm")

    def test_second_device_is_viewer_and_blocked(self):
        a, b = self.pair(), self.pair()
        self.control("claim", a)
        code, _ = self.control("claim", b)
        self.assertEqual(code, 409)
        self.assertEqual(self.cmd("arm", b)[0], 403)
        self.assertEqual(self.cmd("arm", a)[0], 200)

    def test_params_reach_the_cockpit_unchanged(self):
        ck = self.pair()
        self.control("claim", ck)
        self.cmd("takeoff", ck, alt=12.5, confirmed=True)
        self.assertEqual(self.sent[-1][2], {"alt": 12.5, "confirmed": True})

    def test_rate_limit_blocks_a_flood(self):
        ck = self.pair()
        self.control("claim", ck)
        codes = [self.cmd("arm", ck)[0] for _ in range(fs.CMD_BURST + 4)]
        self.assertIn(429, codes, "ยิงรัวแล้วไม่โดนจำกัด")
        self.assertLessEqual(len(self.sent), fs.CMD_BURST + 1)

    def test_stop_is_never_rate_limited(self):
        """กันคนกดหยุดคือทิศทางที่ผิดของความปลอดภัย"""
        ck = self.pair()
        self.control("claim", ck)
        codes = [self.cmd("hold", ck)[0] for _ in range(fs.CMD_BURST + 6)]
        self.assertNotIn(429, codes)
        self.assertEqual(len(codes), codes.count(200))

    def test_command_rejected_when_cockpit_not_connected(self):
        """ค่าเริ่มต้นของ on_command ต้องปฏิเสธ — ลืมต่อแล้วต้องสั่งไม่ได้"""
        srv = fs.FieldServer(_ASSETS, fs.TelemetryHub(), fs.SessionStore(),
                             host="127.0.0.1", port=0)
        ok, msg = srv.on_command("s", "arm", {})
        srv.stop()
        self.assertFalse(ok)


class TestControlOverHttp(Base):
    def test_desktop_holds_before_anyone_claims(self):
        ck = self.pair()
        _, _, raw = _raw(self.port, "/api/hello", cookie=ck)
        hello = json.loads(raw)
        self.assertTrue(hello["control"]["desktop"])
        self.assertFalse(hello["pilot"])
        self.assertNotIn("rc_move", hello["commands"])
        # "หยุด" ต้องอยู่ในชุดที่ทุกคนกดได้เสมอ (move_stop เพิ่มมาในเฟส 3
        # ด้วยเหตุผลเดียวกับ hold/hold_all — การกันไม่ให้คนกดหยุดคือทิศทางที่ผิด)
        for safe in ("estop", "hold", "hold_all", "move_stop"):
            self.assertIn(safe, hello["safe_commands"])

    def test_handoff_flow_over_http(self):
        a, b = self.pair(), self.pair()
        self.control("claim", a, label="A")
        self.assertEqual(self.control("request", b, label="B")[0], 200)
        # เครื่องที่ไม่ได้ถือสิทธิ์อนุมัติแทนไม่ได้
        self.assertEqual(self.control("approve", b)[0], 403)
        self.assertEqual(self.control("approve", a)[0], 200)
        self.assertEqual(self.cmd("arm", b)[0], 200)
        self.assertEqual(self.cmd("arm", a)[0], 403)

    def test_release_returns_control_to_desktop(self):
        ck = self.pair()
        self.control("claim", ck)
        self.control("release", ck)
        self.assertTrue(self.srv.control.desktop_has_control())
        self.assertEqual(self.cmd("arm", ck)[0], 403)

    def test_unknown_control_action_rejected(self):
        ck = self.pair()
        self.assertEqual(self.control("takeover_everything", ck)[0], 400)

    def test_control_change_notifies_the_desktop(self):
        seen = []
        self.srv.on_control_change = lambda: seen.append(1)
        ck = self.pair()
        self.control("claim", ck)
        self.assertTrue(seen, "desktop ไม่ได้รับแจ้งว่าสิทธิ์ย้ายมือ")

    def test_heartbeat_does_not_spam_the_desktop(self):
        """heartbeat เข้ามาทุก 2 วิ ถ้านับเป็น 'สิทธิ์เปลี่ยน' คอกพิตจะวาด
        แบนเนอร์ใหม่ตลอดเวลา = กะพริบ และกลบข้อความจริง"""
        ck = self.pair()
        self.control("claim", ck)
        seen = []
        self.srv.on_control_change = lambda: seen.append(1)
        for _ in range(6):
            self.control("beat", ck)
        self.assertEqual(seen, [], "heartbeat ไม่ควรนับเป็นการเปลี่ยนสิทธิ์")

    def test_dropped_connection_is_not_logged_as_an_error(self):
        """เลื่อนแผนที่ = บราวเซอร์ยกเลิก tile ที่ค้าง ปล่อย traceback ขึ้นคือ
        ถล่ม console ของคอกพิตด้วยเรื่องปกติจนกลบข้อความสำคัญ"""
        import io
        import socket as sk
        buf = io.StringIO()
        old, sys.stderr = sys.stderr, buf
        try:
            s = sk.create_connection(("127.0.0.1", self.port), 3)
            s.sendall(b"GET /api/hello HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Connection: close\r\n\r\n")
            s.close()                     # ตัดสายก่อนอ่านคำตอบ
            time.sleep(0.5)
        finally:
            sys.stderr = old
        self.assertNotIn("Traceback", buf.getvalue())

    def test_state_tells_each_device_its_own_role(self):
        a, b = self.pair(), self.pair()
        self.control("claim", a)
        _, _, ra = _raw(self.port, "/api/state", cookie=a)
        _, _, rb = _raw(self.port, "/api/state", cookie=b)
        self.assertTrue(json.loads(ra)["pilot"])
        self.assertFalse(json.loads(rb)["pilot"],
                         "viewer ต้องไม่เห็นว่าตัวเองคุมอยู่ — เข้าใจผิดแล้วอันตราย")


if __name__ == "__main__":
    unittest.main()
