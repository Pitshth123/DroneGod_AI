"""
ทดสอบการอุ่นเครื่องช่อง A/B อัตโนมัติหลังเชื่อมต่อ

FC ใช้เวลารับ RC override ครั้งแรกหลังเชื่อมต่อ 1-25 วินาที (ดู SWARM_CONTROL.md
§11.20-11.35) — โปรแกรมจึงส่งคำสั่งอุ่นเครื่องให้เองในเบื้องหลัง แล้วขึ้นสถานะ
บนปุ่มว่าพร้อมใช้งานแล้ว ผู้ใช้ไม่ต้องกดทิ้งเองอีก

รัน headless:
    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest tests/test_servo_prime.py
"""
import os
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
from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])


class ServoPrimeTests(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def _telem(self, did=1, **kw):
        t = _fake_telem(did, 14.0, 100.0)
        t.rc_valid = True
        t.servo_valid = True
        t.ovr_ch7 = t.ovr_ch8 = False
        t.rc_ch7_raw, t.rc_ch8_raw = 1050, 900
        t.servo_ch7_pwm, t.servo_ch8_pwm = 0, 900
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    # ── ความปลอดภัย: ห้ามยิงค่า "เปิด" ตอนอุ่นเครื่อง ──
    def test_prime_value_is_in_the_off_band(self):
        """อุ่นเครื่องด้วยค่าเปิด = กลไกปล่อยของทันทีที่เปิดโปรแกรม"""
        for label in ("A", "B"):
            prime = self.win.SERVO_PWM_PRIME[label]
            self.assertLess(prime, self.win.SERVO_ON_MIN,
                            f"ค่าอุ่นเครื่องช่อง {label} ต้องอยู่ในย่านปิด")
            self.assertNotEqual(prime, self.win.SERVO_PWM_ON[label])
            self.assertGreaterEqual(prime, 900, "ต้องไม่ต่ำกว่าที่ core รับ")
            self.assertLessEqual(prime, 2100)

    def test_prime_value_differs_enough_to_be_detected(self):
        """ต้องต่างจากค่าปล่อยเกิน deadband ไม่งั้นตรวจไม่ได้ว่า FC ตอบรับ"""
        for label in ("A", "B"):
            delta = abs(self.win.SERVO_PWM_PRIME[label] - self.win.SERVO_PWM_OFF[label])
            self.assertGreater(delta, 20, f"ช่อง {label} ต่างน้อยเกินไป ({delta} µs)")

    def test_prime_starts_automatically_on_telemetry(self):
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        sent = {c[2]: c[3] for c in calls}
        self.assertEqual(sent.get(7), self.win.SERVO_PWM_PRIME["A"])
        self.assertEqual(sent.get(8), self.win.SERVO_PWM_PRIME["B"])
        self.assertFalse(self.win._servo_ready(1, "B"), "ยังไม่ตอบกลับ = ยังไม่พร้อม")

    def test_ready_once_fc_echoes_the_prime_value(self):
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        p = self.win.SERVO_PWM_PRIME
        self.win._on_telemetry(self._telem(
            1, rc_ch7_raw=p["A"], rc_ch8_raw=p["B"]))
        _pump(0.3)
        self.assertTrue(self.win._servo_ready(1, "A"))
        self.assertTrue(self.win._servo_ready(1, "B"))

    def test_override_released_after_priming(self):
        """อุ่นเสร็จต้องคืนช่องให้รีโมท ไม่ค้าง override ไว้"""
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        p = self.win.SERVO_PWM_PRIME
        self.win._on_telemetry(self._telem(1, rc_ch7_raw=p["A"], rc_ch8_raw=p["B"]))
        _pump(0.4)
        rel = [c for c in self.fake.calls if c[0] == "servo_release"]
        self.assertTrue(rel, "ต้องปล่อย override คืนหลังอุ่นเสร็จ")

    def test_prime_skipped_when_remote_holds_the_channel(self):
        """รีโมทถือห้องอยู่ = ห้ามแตะ (กติกา 1 ห้อง)"""
        self.win._on_telemetry(self._telem(
            1, rc_ch8_raw=2100, servo_ch8_pwm=2100))   # รีโมทเปิด B ค้างไว้
        _pump(0.3)
        sent = {c[2] for c in self.fake.calls if c[0] == "servo_set"}
        self.assertNotIn(8, sent, "ช่องที่รีโมทถืออยู่ต้องไม่ถูกอุ่นเครื่อง")

    def test_prime_runs_once_not_every_telemetry_frame(self):
        for _ in range(5):
            self.win._on_telemetry(self._telem(1))
        _pump(0.4)
        ch8 = [c for c in self.fake.calls if c[0] == "servo_set" and c[2] == 8]
        self.assertEqual(len(ch8), 1, "ต้องอุ่นครั้งเดียว ไม่ใช่ทุกแพ็กเก็ต")

    def test_button_shows_preparing_state(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        _pump(0.2)
        self.assertIn("⌛", self.win.btn_servo_b.text(),
                      "ต้องบอกผู้ใช้ว่ากำลังเตรียมช่องอยู่")

    def test_button_back_to_normal_once_ready(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        _pump(0.2)
        p = self.win.SERVO_PWM_PRIME
        self.win._on_telemetry(self._telem(1, rc_ch7_raw=p["A"], rc_ch8_raw=p["B"]))
        _pump(0.3)
        # กลับมาเป็นค่าปล่อยตามจริงหลังปล่อย override
        self.win._on_telemetry(self._telem(1))
        _pump(0.2)
        self.assertNotIn("⌛", self.win.btn_servo_b.text())

    def test_timeout_unlocks_the_button_anyway(self):
        """FC ไม่ตอบ ต้องไม่ค้าง ⌛ ตลอดกาล — ปุ่มต้องใช้งานได้ตามปกติ"""
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        for key in list(self.win._servo_priming):
            self.win._servo_priming[key] -= (self.win.SERVO_PRIME_TIMEOUT + 1)
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        self.assertTrue(self.win._servo_ready(1, "B"))
        self.assertFalse(self.win._servo_priming)

    def test_state_cleared_when_drone_removed(self):
        """ต่อกลับมาใหม่ FC เริ่มนับใหม่ ต้องอุ่นเครื่องอีกรอบ"""
        self.win._on_telemetry(self._telem(1))
        _pump(0.3)
        p = self.win.SERVO_PWM_PRIME
        self.win._on_telemetry(self._telem(1, rc_ch7_raw=p["A"], rc_ch8_raw=p["B"]))
        _pump(0.3)
        self.assertTrue(self.win._servo_ready(1, "B"))
        self.win._remove_fleet_item(1)
        self.assertFalse(self.win._servo_ready(1, "B"))


if __name__ == "__main__":
    unittest.main()
