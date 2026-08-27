"""Phase 1 (observability) — UI Watchdog / HealthMonitor แบบ observe-only.

ล็อกพฤติกรรมของ core/health_monitor.py:
- ตรวจ UI event-loop stall (HEALTHY/DEGRADED/STALLED) จาก heartbeat latency
- วัด telemetry age ราย drone, last RPC, render rate, core-connected
- **observe-only**: monitor ต้องไม่มี flight authority (ไม่มี client, ไม่ส่ง command)

หมายเหตุ: ใช้ FakeClock inject เวลา ไม่รอเวลาจริง (ตามหลัก plan: ห้าม test รอเวลาจริง)
"""
import unittest

from swarmgod_gui.core.health_monitor import HealthMonitor


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestUiStallDetection(unittest.TestCase):
    def setUp(self):
        self.clk = FakeClock()
        self.stalls = []
        self.m = HealthMonitor(
            clock=self.clk,
            heartbeat_ms=1000,
            degraded_ms=250,
            stalled_ms=1000,
            on_stall=lambda ms: self.stalls.append(ms),
        )

    def test_initial_status_healthy(self):
        self.assertEqual(self.m.status(), HealthMonitor.HEALTHY)
        snap = self.m.snapshot()
        self.assertEqual(snap["status"], "HEALTHY")
        self.assertEqual(snap["stall_count"], 0)

    def test_normal_ticks_stay_healthy(self):
        for _ in range(5):
            self.clk.advance(1.0)  # ตรงตาม heartbeat 1000ms
            self.assertEqual(self.m.record_ui_tick(), HealthMonitor.HEALTHY)
        self.assertEqual(self.m.snapshot()["stall_count"], 0)
        self.assertEqual(self.stalls, [])

    def test_small_jitter_still_healthy(self):
        self.clk.advance(1.0)
        self.m.record_ui_tick()
        self.clk.advance(1.1)  # lateness 100ms < degraded 250ms
        self.assertEqual(self.m.record_ui_tick(), HealthMonitor.HEALTHY)

    def test_degraded_when_late(self):
        self.clk.advance(1.0)
        self.m.record_ui_tick()
        self.clk.advance(1.4)  # lateness 400ms → DEGRADED (< stalled 1000)
        self.assertEqual(self.m.record_ui_tick(), HealthMonitor.DEGRADED)
        self.assertEqual(self.m.snapshot()["stall_count"], 0)
        self.assertEqual(self.stalls, [])

    def test_stalled_when_blocked(self):
        self.clk.advance(1.0)
        self.m.record_ui_tick()
        self.clk.advance(3.0)  # gap 3000ms, lateness 2000ms ≥ stalled 1000ms
        self.assertEqual(self.m.record_ui_tick(), HealthMonitor.STALLED)
        snap = self.m.snapshot()
        self.assertEqual(snap["stall_count"], 1)
        self.assertAlmostEqual(snap["last_stall_ms"], 2000.0, delta=1.0)
        self.assertAlmostEqual(snap["total_stall_ms"], 2000.0, delta=1.0)
        # callback ถูกเรียกพร้อม stall duration
        self.assertEqual(len(self.stalls), 1)
        self.assertAlmostEqual(self.stalls[0], 2000.0, delta=1.0)

    def test_recovers_to_healthy_after_stall(self):
        self.clk.advance(1.0)
        self.m.record_ui_tick()
        self.clk.advance(3.0)
        self.m.record_ui_tick()  # STALLED
        self.clk.advance(1.0)
        self.assertEqual(self.m.record_ui_tick(), HealthMonitor.HEALTHY)
        # stall count สะสมไว้ ไม่รีเซ็ต
        self.assertEqual(self.m.snapshot()["stall_count"], 1)


class TestTelemetryAge(unittest.TestCase):
    def setUp(self):
        self.clk = FakeClock()
        self.m = HealthMonitor(clock=self.clk)

    def test_age_grows_with_clock(self):
        self.m.note_telemetry(3)
        self.clk.advance(2.0)
        self.assertAlmostEqual(self.m.telemetry_age_ms(3), 2000.0, delta=1.0)

    def test_note_resets_age(self):
        self.m.note_telemetry(3)
        self.clk.advance(2.0)
        self.m.note_telemetry(3)
        self.assertAlmostEqual(self.m.telemetry_age_ms(3), 0.0, delta=1.0)

    def test_unknown_drone_age_none(self):
        self.assertIsNone(self.m.telemetry_age_ms(99))

    def test_ages_dict(self):
        self.m.note_telemetry(1)
        self.m.note_telemetry(2)
        self.clk.advance(1.0)
        ages = self.m.telemetry_ages_ms()
        self.assertEqual(set(ages.keys()), {1, 2})
        self.assertAlmostEqual(ages[1], 1000.0, delta=1.0)


class TestMiscMetrics(unittest.TestCase):
    def setUp(self):
        self.clk = FakeClock()
        self.m = HealthMonitor(clock=self.clk)

    def test_core_connected(self):
        self.assertIn("core_connected", self.m.snapshot())
        self.m.note_core_connected(True)
        self.assertTrue(self.m.snapshot()["core_connected"])
        self.m.note_core_connected(False)
        self.assertFalse(self.m.snapshot()["core_connected"])

    def test_last_rpc(self):
        self.m.note_rpc("takeoff", 42.0)
        last = self.m.snapshot()["last_rpc"]
        self.assertEqual(last[0], "takeoff")
        self.assertAlmostEqual(last[1], 42.0, delta=0.001)

    def test_render_rate(self):
        for _ in range(5):
            self.m.record_render()
        self.clk.advance(1.0)
        self.assertAlmostEqual(self.m.render_rate(), 5.0, delta=0.01)
        self.assertEqual(self.m.snapshot()["render_total"], 5)


class TestObserveOnlyNoFlightAuthority(unittest.TestCase):
    """monitor ต้องไม่ถือ flight authority — ไม่มี client, ไม่ส่ง command"""

    def test_no_client_attribute(self):
        m = HealthMonitor()
        self.assertFalse(hasattr(m, "client"))

    def test_module_does_not_import_core_client(self):
        import swarmgod_gui.core.health_monitor as hm
        src = hm.__file__
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        # observe-only: ห้าม import/สร้างตัวส่งคำสั่ง (ตรวจการใช้จริง ไม่ใช่การเอ่ยถึงใน docstring)
        self.assertNotIn("import grpc_client", text)
        self.assertNotIn("from .grpc_client", text)
        self.assertNotIn("CoreClient(", text)   # ไม่มีการสร้าง client
        self.assertNotIn(".takeoff(", text)
        self.assertNotIn(".rtl(", text)

    def test_no_public_command_methods(self):
        m = HealthMonitor()
        for name in ("takeoff", "rtl", "arm", "goto", "hold", "land", "stop_all"):
            self.assertFalse(hasattr(m, name), f"monitor ไม่ควรมี method {name}")


if __name__ == "__main__":
    unittest.main()
