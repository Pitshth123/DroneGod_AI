"""
ทดสอบงานรอบ servo/datalink/UI (11 ข้อ)

รัน headless:
    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_servo_and_ui -v

ครอบคลุม:
  ข้อ 1  Datalink — ไม่มี rssi จริงต้องโชว์ N/A ไม่ใช่ 100% ปลอม
  ข้อ 3  ปุ่ม A=ch7 / B=ch8
  ข้อ 4  ป้ายสถานะ A(แดง)/B(เหลือง) บนการ์ด
  ข้อ 5  ลงจอดเสร็จ → GUIDED อัตโนมัติ (ไม่ยิงซ้ำ, REMOTE บล็อก)
  ข้อ 6  ป้ายโหมดปัจจุบันใน FLIGHT
  ข้อ 7  ป้ายโหมดกลาง topbar + อนิเมชัน (pill เดิมถูกถอด)
  ข้อ 8  DISARM ถามยืนยันก่อน (A/B ยิงทันที ไม่ถาม — ดู SWARM_CONTROL.md §11.19)
  ข้อ 9  โลโก้สูงสุด 5 รูป เรียงซ้าย→ขวา
  ข้อ 10 SCAN ติ๊กเลือก/เลือกทั้งหมด/ต่อพร้อมกัน + ID ไม่ชนกัน
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.core import logo_store  # noqa: E402
from swarmgod_gui.core.theme import T  # noqa: E402
from swarmgod_gui.core.ip_scan import ScanHit  # noqa: E402
from swarmgod_gui.widgets import confirm as confirm_mod  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem, servo_badge_qss  # noqa: E402
from swarmgod_gui.widgets.scan_dialog import ScanIpDialog, ConnectIpDialog  # noqa: E402
from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])

LANDING, READY = 10, 4
MODE_STABILIZE, MODE_GUIDED = 1, 5



def _user_servo_calls(fake, win):
    """คำสั่ง servo ที่ **ผู้ใช้สั่ง** เท่านั้น — ตัดคำสั่งอุ่นเครื่องอัตโนมัติออก

    การอุ่นเครื่อง (§11.37) ยิง servo_set ด้วยค่าในย่านปิด (SERVO_PWM_PRIME)
    ทันทีที่โดรนเข้าฝูง ซึ่งไม่ใช่การกระทำของผู้ใช้
    """
    prime = set(win.SERVO_PWM_PRIME.values())
    out = []
    for c in fake.calls:
        if not c[0].startswith("servo"):
            continue
        if c[0] == "servo_set" and len(c) > 3 and c[3] in prime:
            continue
        out.append(c)
    return out


class _WinBase(unittest.TestCase):
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
        t = _fake_telem(did, 14.0, 100.0 + did * 0.001)
        for k, v in kw.items():
            setattr(t, k, v)
        return t


# ── ข้อ 1: Datalink ──
class DatalinkTests(_WinBase):
    def test_no_radio_shows_na_not_fake_full_bar(self):
        """เดิม proto ไม่มี rssi เลย getattr ได้ 0 → สูตรเก่าให้ 100% ตลอด (ปลอม)"""
        t = self._telem(rssi=0, rssi_valid=False, link_quality=0)
        self.win._on_telemetry(t)
        self.win._select_drone(1)
        self.assertEqual(self.win.sel_card._vals["RSSI"].text(), "N/A")
        self.assertEqual(self.win.sel_card._vals["LINKQ"].text(), "--")

    def test_real_rssi_is_shown(self):
        t = self._telem(rssi=-72, rssi_valid=True, link_quality=68)
        self.win._on_telemetry(t)
        self.win._select_drone(1)
        self.assertIn("-72", self.win.sel_card._vals["RSSI"].text())
        self.assertIn("68", self.win.sel_card._vals["LINKQ"].text())

    def test_proto_actually_carries_the_fields(self):
        from swarmgod_gui.gen.swarmgod.v1 import telemetry_pb2
        names = [f.name for f in telemetry_pb2.Telemetry.DESCRIPTOR.fields]
        for f in ("rssi", "rssi_valid", "link_quality", "drop_rate"):
            self.assertIn(f, names, f"proto ต้องมี field {f}")


# ── ข้อ 3 + 8: ปุ่ม A/B + confirm ──
class ServoButtonTests(_WinBase):
    def test_channel_mapping(self):
        self.assertEqual(self.win.SERVO_CH["A"], 7)
        self.assertEqual(self.win.SERVO_CH["B"], 8)

    def test_pwm_matches_the_real_transmitter_per_channel(self):
        """cockpit ต้องส่ง PWM เท่ากับที่รีโมทส่งจริง **รายช่อง**

        ค่าที่ผู้ใช้วัดจากรีโมท: A(RC7) ปล่อย 1050 → กด 1950 ·
        B(RC8) ปล่อย 900 → กด 2100
        เดิมใช้ค่าเดียว 1950 กับทั้งสองช่อง → ช่อง B ไปไม่สุดระยะเท่ารีโมท
        """
        self.assertEqual(self.win.SERVO_PWM_ON["A"], 1950)
        self.assertEqual(self.win.SERVO_PWM_ON["B"], 2100)
        self.assertEqual(self.win.SERVO_PWM_OFF["A"], 1050)
        self.assertEqual(self.win.SERVO_PWM_OFF["B"], 900)

    def test_threshold_separates_released_from_pressed_on_every_channel(self):
        """เส้นแบ่ง "เปิด/ปิด" ต้องอยู่ระหว่างค่าปล่อยกับค่ากดของ **ทุกช่อง**

        ถ้าใครไปตั้งสวิตช์รีโมทใหม่แล้วค่าปล่อยขึ้นเกิน 1500 (หรือค่ากดต่ำกว่า)
        ระบบจะอ่านสถานะกลับด้าน — ปุ่มขึ้น 🔒 ทั้งที่ไม่มีใครกด หรือคิดว่าปิดทั้งที่เปิดอยู่
        """
        for label in ("A", "B"):
            off = self.win.SERVO_PWM_OFF[label]
            on = self.win.SERVO_PWM_ON[label]
            self.assertLess(off, self.win.SERVO_ON_MIN,
                            f"ค่าปล่อยของช่อง {label} ({off}) ต้องต่ำกว่าเส้นแบ่ง")
            self.assertGreaterEqual(on, self.win.SERVO_ON_MIN,
                                    f"ค่ากดของช่อง {label} ({on}) ต้องถึงเส้นแบ่ง")

    def test_pwm_stays_inside_the_range_core_accepts(self):
        """ค่าที่ส่งต้องไม่โดน core clamp ทิ้ง (fleet.ClampServoPWM = 900..2100)

        ถ้าเกินช่วงนี้ core จะตัดค่าเงียบ ๆ แล้วกลไกไปไม่สุดระยะเท่ารีโมท
        """
        for label in ("A", "B"):
            for pwm in (self.win.SERVO_PWM_ON[label], self.win.SERVO_PWM_OFF[label]):
                self.assertGreaterEqual(pwm, 900, f"ช่อง {label}: {pwm} ต่ำกว่าที่ core รับ")
                self.assertLessEqual(pwm, 2100, f"ช่อง {label}: {pwm} เกินที่ core รับ")

    def test_button_b_sends_2100_not_1950(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        self.win._cmd_servo("B")
        _pump(0.4)
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        self.assertEqual(calls[-1], ("servo_set", 1, 8, 2100),
                         "ช่อง B ต้องส่ง 2100 เท่ารีโมท ไม่ใช่ 1950")

    def test_buttons_exist_with_tooltip(self):
        self.assertEqual(self.win.btn_servo_a.text(), "A")
        self.assertEqual(self.win.btn_servo_b.text(), "B")
        self.assertIn("CH7", self.win.btn_servo_a.toolTip())
        self.assertIn("CH8", self.win.btn_servo_b.toolTip())

    def test_servo_asks_confirm_and_sends_on_yes(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.4)
        # toggle ใช้ servo_set พร้อมค่า PWM ชัดเจน (เปิด=2100 / ยกเลิก=900)
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        self.assertTrue(calls, "กดยืนยันแล้วต้องส่งคำสั่ง servo")
        self.assertEqual(calls[0][2], 7, "ปุ่ม A ต้องยิง CH7")
        self.assertEqual(calls[0][3], self.win.SERVO_PWM_ON["A"])

    def test_servo_fires_without_any_confirm_dialog(self):
        """A/B ยิงทันที ไม่ถามยืนยัน (ผู้ใช้เลือกไว้ — กด 2 จังหวะทำให้รู้สึกหน่วง)

        กล่องยืนยันที่เหลือในแอป (DISARM, E-STOP, Clear Waypoint) ไม่ถูกแตะ
        """
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm") as m:
            self.win._cmd_servo("B")
        _pump(0.4)
        m.assert_not_called()
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        self.assertTrue(calls, "กดแล้วต้องส่งคำสั่งทันที")
        self.assertEqual(calls[0][2], 8, "ปุ่ม B ต้องยิง CH8")

    def test_disarm_asks_confirm(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=False):
            self.win._cmd_disarm()
        _pump(0.3)
        self.assertFalse([c for c in self.fake.calls if c[0] == "disarm"],
                         "ยกเลิกแล้วต้องไม่ disarm")

    def test_disarm_proceeds_on_confirm(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_disarm()
        _pump(0.4)
        self.assertTrue([c for c in self.fake.calls if c[0] == "disarm"])

    # ── ไฟปุ่มต้องติดทันทีที่กด ไม่รอ telemetry รอบถัดไป ──
    # เดิมบล็อก optimistic update ถูกวางผิดที่ (อยู่หลัง return ของ _servo_debug)
    # จึงเป็น dead code — ปุ่มไม่เปลี่ยนหน้าจนกว่าค่าจริงจาก FC จะกลับมา = "กดแล้วไม่ติด"
    def test_button_lights_up_immediately_on_press(self):
        self.win._on_telemetry(self._telem(1, ovr_ch7=False, rc_valid=False,
                                           servo_valid=False))
        self.win._select_drone(1)
        self.assertEqual(self.win.btn_servo_a.text(), "A")
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        # ยังไม่ pump / ยังไม่มี telemetry รอบใหม่ — ปุ่มต้องติดไฟแล้ว
        self.assertEqual(self.win.btn_servo_a.text(), "A ●",
                         "ปุ่มต้องแสดงว่า UI ถือห้องทันทีที่กด")
        self.assertEqual(self.win._servo_owner(1, "A"), "UI")

    def test_real_state_wins_after_optimistic_window(self):
        """ค่าจริงต้องชนะเสมอเมื่อหมดเวลา — ไม่ใช่ค้างเป็นสิ่งที่ UI เดาไว้"""
        self.win._on_telemetry(self._telem(1, ovr_ch7=False, rc_valid=False,
                                           servo_valid=False))
        self.win._select_drone(1)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        self.assertEqual(self.win._servo_owner(1, "A"), "UI")
        # หมดหน้าต่าง optimistic + ค่าจริงบอกว่ารีโมทถือห้องอยู่
        self.win._servo_pending[(1, "A")] = ("UI", 0.0, 0.0)   # deadline อดีต = หมดเวลาแล้ว
        self.win._on_telemetry(self._telem(
            1, ovr_ch7=False, rc_valid=True, rc_ch7_raw=1950, rc_ch8_raw=1050,
            servo_valid=True, servo_ch7_pwm=1950, servo_ch8_pwm=1050))
        self.assertEqual(self.win._servo_owner(1, "A"), "RC")
        self.assertNotIn((1, "A"), self.win._servo_pending,
                         "pending ต้องถูกทิ้งเมื่อค่าจริงมาถึง")

    # ── อุ่นเครื่องกล่องยืนยันตอนเปิดโปรแกรม ──
    # กล่องยืนยันใบแรกของ process ใช้เวลา ~850 ms บน Windows (สร้าง HWND ของ dialog
    # ครั้งแรก) ใบต่อไป 3 ms — ถ้าไม่จ่ายล่วงหน้า ต้นทุนก้อนนี้ไปตกที่ปุ่มคำสั่งแรก
    def test_prewarm_runs_and_is_idempotent(self):
        confirm_mod._prewarmed = False
        self.win._prewarm_ui_paths()
        self.assertTrue(confirm_mod._prewarmed,
                        "_prewarm_ui_paths ต้องอุ่นกล่องยืนยันให้เรียบร้อย")
        self.win._prewarm_ui_paths()   # เรียกซ้ำต้องไม่พังและไม่ทำงานซ้ำ

    def test_prewarm_survives_client_without_channel(self):
        """FakeClient ไม่มี .channel — prewarm ต้องไม่ทำให้แอปพัง"""
        confirm_mod._prewarmed = False
        self.win._prewarm_ui_paths()
        _pump(0.2)

    # ── RC input ดิบสูงโดยไม่มีรีโมท ต้องไม่ทำให้ปุ่มถูกล็อก ──
    # ArduPilot สตรีม RC_CHANNELS เสมอแม้ไม่มีรีโมทต่ออยู่ (SITL จริง: RC8=1800)
    # เดิม UI อ่านค่าดิบนั้นเป็น "รีโมทถือห้อง" แล้วบล็อกปุ่ม B ทิ้งทั้งหมด
    def test_idle_rc_default_does_not_lock_the_button(self):
        self.win._on_telemetry(self._telem(
            1, rc_valid=True, rc_ch7_raw=1000, rc_ch8_raw=1800,
            servo_valid=True, servo_ch7_pwm=0, servo_ch8_pwm=0,
            ovr_ch7=False, ovr_ch8=False))
        self.win._select_drone(1)
        self.assertIsNone(self.win._servo_owner(1, "B"),
                          "ขาเซอร์โว = 0 แปลว่ากลไกยังไม่ทำงาน ห้องต้องว่าง")
        self.assertNotIn("🔒", self.win.btn_servo_b.text())
        self.win._cmd_servo("B")
        _pump(0.4)
        self.assertTrue([c for c in self.fake.calls if c[0] == "servo_set"],
                        "ห้องว่างแล้วต้องส่งคำสั่งออกจริง ไม่ใช่ถูกบล็อก")

    def test_no_servo_telemetry_yet_never_blocks_the_first_press(self):
        """เพิ่งเชื่อมโดรน — SERVO_OUTPUT_RAW ยังไม่มา แต่ RC_CHANNELS มาแล้วพร้อมค่า
        failsafe สูง ๆ · ต้องกดได้ทันที ไม่ใช่รอจน servo stream มาถึง

        นี่คืออาการ "เชื่อมโดรนครั้งแรกแล้วกด B ไม่ติด กว่าจะสั่งได้"
        """
        self.win._on_telemetry(self._telem(
            1, rc_valid=True, rc_ch7_raw=1800, rc_ch8_raw=1800,
            servo_valid=False, ovr_ch7=False, ovr_ch8=False))
        self.win._select_drone(1)
        for lb in ("A", "B"):
            self.assertIsNone(self.win._servo_owner(1, lb),
                              f"ยังไม่มีหลักฐานจากขาเซอร์โว ห้อง {lb} ต้องว่าง")
        self.assertNotIn("🔒", self.win.btn_servo_b.text())
        self.win._cmd_servo("B")
        _pump(0.4)
        self.assertTrue([c for c in self.fake.calls if c[0] == "servo_set"])

    def test_stale_servo_stream_does_not_relock_the_button(self):
        """servo_valid กะพริบเป็น False (สตรีมสะดุด) ต้องไม่ทำให้ปุ่มเด้งกลับไปล็อก"""
        self.win._on_telemetry(self._telem(
            1, rc_valid=True, rc_ch7_raw=1000, rc_ch8_raw=1800,
            servo_valid=True, servo_ch7_pwm=0, servo_ch8_pwm=0,
            ovr_ch7=False, ovr_ch8=False))
        self.win._select_drone(1)
        self.assertIsNone(self.win._servo_owner(1, "B"))
        # สตรีม servo หมดอายุที่ core แต่ RC ยังไม่หมด
        self.win._on_telemetry(self._telem(
            1, rc_valid=True, rc_ch7_raw=1000, rc_ch8_raw=1800,
            servo_valid=False, ovr_ch7=False, ovr_ch8=False))
        self.assertIsNone(self.win._servo_owner(1, "B"),
                          "servo หมดอายุชั่วคราวต้องไม่ทำให้ห้องกลับไปเป็นของรีโมท")

    # ── ปุ่มต้องบอกได้ว่า "สั่งแล้วกำลังรอเครื่องบิน" ──
    # FC ใช้เวลารับคำสั่งแรกหลังเชื่อมต่อ 1-25 วิ (แกว่ง คาดเดาไม่ได้)
    # เดิมปุ่มติดไฟทันทีแล้วดับหลัง 1 วิ → แยกไม่ออกว่าสั่งไปแล้วหรือยังไม่ได้สั่ง
    def test_button_shows_waiting_while_fc_has_not_confirmed(self):
        self.win._on_telemetry(self._telem(
            1, ovr_ch8=True, ovr_ch7=False,          # core override อยู่
            servo_valid=True, servo_ch8_pwm=900,     # แต่ขาเซอร์โวยังไม่ขยับ
            rc_valid=True, rc_ch8_raw=900))
        self.win._select_drone(1)
        self.assertTrue(self.win._servo_awaiting_fc(1, "B"))
        self.assertIn("⋯", self.win.btn_servo_b.text(),
                      "ต้องบอกผู้ใช้ว่ากำลังรอเครื่องบินตอบรับ")

    def test_button_shows_confirmed_once_servo_moves(self):
        self.win._on_telemetry(self._telem(
            1, ovr_ch8=True, ovr_ch7=False,
            servo_valid=True, servo_ch8_pwm=2100,    # ขาเซอร์โวขยับแล้ว
            rc_valid=True, rc_ch8_raw=2100))
        self.win._select_drone(1)
        self.assertFalse(self.win._servo_awaiting_fc(1, "B"))
        self.assertIn("●", self.win.btn_servo_b.text())
        self.assertNotIn("⋯", self.win.btn_servo_b.text())

    def test_no_waiting_state_without_servo_telemetry(self):
        """ไม่มีข้อมูลขาเซอร์โว = สรุปไม่ได้ ต้องไม่เดาว่ากำลังรอ"""
        self.win._on_telemetry(self._telem(
            1, ovr_ch8=True, servo_valid=False, rc_valid=True, rc_ch8_raw=900))
        self.win._select_drone(1)
        self.assertFalse(self.win._servo_awaiting_fc(1, "B"))

    def test_real_servo_output_still_locks_the_room(self):
        """ถ้าขาเซอร์โวทำงานจริงโดยที่ UI ไม่ได้ override = รีโมทถือห้อง ต้องบล็อกเหมือนเดิม"""
        self.win._on_telemetry(self._telem(
            1, rc_valid=True, rc_ch7_raw=1000, rc_ch8_raw=2100,
            servo_valid=True, servo_ch7_pwm=0, servo_ch8_pwm=2100,
            ovr_ch7=False, ovr_ch8=False))
        self.win._select_drone(1)
        self.assertEqual(self.win._servo_owner(1, "B"), "RC")
        self.assertIn("🔒", self.win.btn_servo_b.text())
        self.win._cmd_servo("B")
        _pump(0.4)
        self.assertFalse(_user_servo_calls(self.fake, self.win),
                         "รีโมทถือห้องอยู่ UI ต้องสั่งไม่ได้")

    # ── ลำที่กู้จาก SQLite ต้องไม่ดูดคำสั่งไปจากลำที่ออนไลน์จริง ──
    # เคสจริงจาก log ผู้ใช้: SQLite จำ Drone 1 ไว้จากรอบก่อน (offline) แต่ลำที่ต่ออยู่จริง
    # คือ Drone 3 — `_target_ids()` เดาเอา ID ต่ำสุด = 1 คำสั่งจึงวิ่งไปหาลำที่ไม่มีอยู่
    # แล้วเงียบหาย จนผู้ใช้คลิกการ์ด Drone 3 เองถึงจะสั่งได้
    def _add_offline_card(self, did):
        from swarmgod_gui.widgets.fleet_item import FleetItem
        item = FleetItem(did, f"Drone {did}")
        self.win._wire_fleet_item(item)
        self.win.fleet_items[did] = item
        self.win.fleet_area.addWidget(item)
        return item

    def test_online_drone_is_selected_even_if_card_already_existed(self):
        self._add_offline_card(1)               # กู้จาก SQLite ตอนเปิดโปรแกรม
        self._add_offline_card(3)               # ลำจริง — การ์ดมีอยู่แล้วเช่นกัน
        self.assertFalse(self.win._selected_id)
        self.win._on_telemetry(self._telem(3))  # Drone 3 ออนไลน์
        self.assertEqual(self.win._selected_id, 3,
                         "ลำที่ส่ง telemetry มาต้องถูกเลือกให้อัตโนมัติ "
                         "แม้การ์ดจะถูกสร้างไว้ก่อนแล้ว")

    def test_command_goes_to_the_online_drone_not_the_lowest_id(self):
        self._add_offline_card(1)               # offline ไม่มี telemetry เลย
        self.win._on_telemetry(self._telem(3))
        self.win._cmd_servo("B")
        _pump(0.4)
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        self.assertTrue(calls, "ต้องส่งคำสั่งออกจริง")
        self.assertEqual(calls[-1][1], 3,
                         "ต้องยิงไปที่ Drone 3 (ออนไลน์) ไม่ใช่ Drone 1 ที่ค้างจาก SQLite")

    # ── ช่อง IP รับ "host:port" ได้ ต้องไม่ต่อพอร์ตซ้ำ ──
    # เคสจริงจาก log: dial "192.168.9.146:5760:5760" → reader ตายใน 8 วิ เงียบ ๆ
    def test_host_with_embedded_port_is_not_duplicated(self):
        from swarmgod_gui.core.ip_scan import split_host_port
        self.assertEqual(split_host_port("192.168.9.146:5760"), ("192.168.9.146", 5760))
        self.assertEqual(split_host_port("192.168.9.146"), ("192.168.9.146", 5760))
        self.assertEqual(split_host_port("192.168.9.146:5770"), ("192.168.9.146", 5770))
        self.assertEqual(split_host_port("[::1]:5760"), ("::1", 5760))
        self.assertEqual(split_host_port("::1"), ("::1", 5760))

    def test_stale_sqlite_row_does_not_block_reconnect(self):
        """แถวเก่าใน SQLite ต้องไม่บล็อกการ connect ใหม่

        ฝูงเริ่มต้นว่างแล้ว (§11.25) จึงไม่มีการ์ดให้กด DEL ล้าง — ถ้ายังเอา SQLite
        มาตัดสิน "IP ซ้ำ" IP ที่เคยต่อจะ connect ไม่ได้อีกเลยตลอดกาล
        """
        self.win._endpoints.clear()
        self.assertEqual(self.win._find_duplicate_ip("192.168.9.146", 5760), 0)

    def test_card_connect_strips_embedded_port(self):
        self.fake.connect_drone = mock.Mock()
        self.win._endpoints.clear()          # กันค่าจากเครื่องจริงมารบกวนการตรวจ IP ซ้ำ
        self.win._card_connect(0, "192.168.9.146:5760", 5760)
        _pump(0.4)
        self.fake.connect_drone.assert_called()
        args, kwargs = self.fake.connect_drone.call_args
        sent = list(args) + list(kwargs.values())
        self.assertIn("192.168.9.146", sent, "host ต้องถูกแยกพอร์ตออกแล้ว")
        self.assertNotIn("192.168.9.146:5760", sent,
                         "host ติดพอร์ตมาด้วยจะทำให้ dial เป็น :5760:5760")
        self.assertIn(5760, sent, "พอร์ตต้องถูกส่งแยกตามปกติ")

    def test_fleet_starts_empty_no_offline_cards_restored(self):
        """เปิดโปรแกรมมาต้องไม่มีการ์ดโดรนค้างจาก SQLite (ผู้ใช้กด SCAN/CONNECT เอง)"""
        from swarmgod_gui.core.ip_store import SavedIp
        with mock.patch.object(type(self.win._ip_store), "list_all",
                               return_value=[SavedIp(7, "10.0.0.7", 5760)]) as m:
            self.win._load_saved_ips()
        self.assertNotIn(7, self.win.fleet_items,
                         "ต้องไม่สร้างการ์ดจาก IP ที่จำไว้")
        self.assertNotIn(7, self.win._endpoints,
                         "ต้องไม่เอา IP เก่ามาบล็อกการ connect ใหม่")
        m.assert_not_called()

    def test_target_falls_back_to_connected_drone_when_nothing_selected(self):
        self._add_offline_card(1)
        self.win._on_telemetry(self._telem(3))
        self.win._selected_id = 0                # จำลองว่ายังไม่มีใครถูกเลือก
        self.win._selected_ids = set()
        self.assertEqual(self.win._target_ids(), [3])

    def test_servo_blocked_in_remote(self):
        self.win._on_telemetry(self._telem(1))
        self.win._select_drone(1)
        self.win._on_control_changed(1)  # REMOTE
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.3)
        self.assertFalse([c for c in self.fake.calls if c[0].startswith("servo")])


# ── ข้อ 4: ป้าย A/B ──
class ServoBadgeTests(unittest.TestCase):
    def test_badge_colors(self):
        self.assertIn(T("red"), servo_badge_qss("A"))
        self.assertIn(T("yellow"), servo_badge_qss("B"))

    def test_badge_shows_letter_and_hides_when_none(self):
        it = FleetItem(1, "Drone 1")
        try:
            self.assertFalse(it.lbl_servo.isVisible())
            it.set_servo_state("A")
            self.assertEqual(it.lbl_servo.text(), "A")
            it.set_servo_state("B")
            self.assertEqual(it.lbl_servo.text(), "B")
            it.set_servo_state(None)
            self.assertFalse(it.lbl_servo.isVisible())
        finally:
            it.close()


# ── ข้อ 5: auto GUIDED ──
class AutoGuidedTests(_WinBase):
    def _land_sequence(self):
        self.win._on_telemetry(self._telem(1, status=LANDING))
        _pump(0.1)
        down = self._telem(1, status=READY, mode=MODE_STABILIZE, armed=False)
        down.position.alt_rel = 0.2
        self.win._on_telemetry(down)
        _pump(0.4)
        return down

    def test_sets_guided_after_touchdown(self):
        self._land_sequence()
        calls = [c for c in self.fake.calls if c[0] == "set_mode"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], MODE_GUIDED)

    def test_does_not_spam_on_repeat_packets(self):
        down = self._land_sequence()
        for _ in range(5):
            self.win._on_telemetry(down)
        _pump(0.3)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "set_mode"]), 1)

    def test_not_triggered_while_still_airborne(self):
        self.win._on_telemetry(self._telem(1, status=LANDING))
        flying = self._telem(1, status=LANDING, armed=True)
        flying.position.alt_rel = 25.0
        self.win._on_telemetry(flying)
        _pump(0.3)
        self.assertFalse([c for c in self.fake.calls if c[0] == "set_mode"])

    def test_remote_mode_blocks_auto_guided(self):
        self.win._on_control_changed(1)  # REMOTE
        self._land_sequence()
        self.assertFalse([c for c in self.fake.calls if c[0] == "set_mode"],
                         "REMOTE ถือคันบังคับ — UI ห้ามแทรกโหมด")


# ── ข้อ 6 + 7: ป้ายโหมด ──
class ModeIndicatorTests(_WinBase):
    def test_current_mode_label_exists_and_highlights_guided(self):
        self.win._style_cur_mode("GUIDED")
        self.assertIn("GUIDED", self.win.lbl_cur_mode.text())
        self.assertIn(T("green"), self.win.lbl_cur_mode.styleSheet())
        self.assertIn("border:2px solid", self.win.lbl_cur_mode.styleSheet())

    def test_old_topbar_pill_removed(self):
        self.assertFalse(hasattr(self.win, "pill_fmode"))

    def test_mode_drop_exists_and_is_centered(self):
        self.win.resize(1400, 860)
        _pump(0.1)
        self.assertTrue(hasattr(self.win, "mode_drop"))
        g = self.win.mode_drop.geometry()
        self.assertGreater(g.x(), 100, "ต้องอยู่กลางแถบ ไม่ใช่ชิดซ้าย")

    def test_each_mode_gets_own_text_and_strip_color(self):
        seen = set()
        for flag, expect in (("_rtl_active", "RTL"),
                             ("_waypoint_executing", "WAYPOINT"),
                             ("_swarm_active", "SWARM")):
            for f in ("_rtl_active", "_waypoint_executing", "_swarm_active"):
                setattr(self.win, f, False)
            setattr(self.win, flag, True)
            self.win._refresh_flight_mode()
            _pump(0.05)
            self.assertIn(expect, self.win.mode_drop.text())
            seen.add(self.win._mode_strip.styleSheet())
        self.assertEqual(len(seen), 3, "แต่ละโหมดต้องได้สีแถบต่างกัน")


# ── ข้อ 9: โลโก้ ──
class LogoStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = os.path.join(self.dir, "store")
        os.makedirs(self.store)
        self.srcs = []
        for i in range(6):
            p = os.path.join(self.dir, f"s{i}.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
            self.srcs.append(p)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_max_five(self):
        for i in range(5):
            logo_store.add_logo(self.srcs[i], self.store)
        self.assertEqual(len(logo_store.list_logos(self.store)), 5)
        self.assertFalse(logo_store.can_add(self.store))
        with self.assertRaises(ValueError):
            logo_store.add_logo(self.srcs[5], self.store)

    def test_order_is_insertion_order(self):
        for i in range(3):
            logo_store.add_logo(self.srcs[i], self.store)
        prefixes = [os.path.basename(p)[:2] for p in logo_store.list_logos(self.store)]
        self.assertEqual(prefixes, ["01", "02", "03"])

    def test_reject_non_image(self):
        bad = os.path.join(self.dir, "x.txt")
        with open(bad, "w") as f:
            f.write("nope")
        with self.assertRaises(ValueError):
            logo_store.add_logo(bad, self.store)

    def test_remove_frees_a_slot(self):
        for i in range(5):
            logo_store.add_logo(self.srcs[i], self.store)
        logo_store.remove_logo(logo_store.list_logos(self.store)[0])
        self.assertTrue(logo_store.can_add(self.store))


# ── ข้อ 10: SCAN ──
class ScanSelectTests(unittest.TestCase):
    def setUp(self):
        self.dlg = ScanIpDialog()
        for i in range(3):
            self.dlg._on_found(ScanHit(host=f"192.168.1.{10+i}", port=5760, ms=10.0 + i))

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()
        _app.processEvents()

    def test_rows_are_checkable(self):
        for i in range(3):
            self.assertTrue(self.dlg.list.item(i).flags() & Qt.ItemIsUserCheckable)

    def test_select_all_then_clear(self):
        self.dlg._toggle_select_all()
        self.assertEqual(len(self.dlg._checked_endpoints()), 3)
        self.assertEqual(self.dlg.btn_all.text(), "CLEAR ALL")
        self.dlg._toggle_select_all()
        self.assertEqual(len(self.dlg._checked_endpoints()), 0)

    def test_connect_emits_for_every_checked_row(self):
        self.dlg.list.item(0).setCheckState(Qt.Checked)
        self.dlg.list.item(2).setCheckState(Qt.Checked)
        got = []
        self.dlg.connect_requested.connect(lambda h, p: got.append((h, p)))
        self.dlg._connect_selected()
        self.assertEqual(got, [("192.168.1.10", 5760), ("192.168.1.12", 5760)])


class ConnectDialogTests(unittest.TestCase):
    def setUp(self):
        self.dlg = ConnectIpDialog("192.168.1.50:14550", "udp")

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()
        _app.processEvents()

    def test_manual_endpoint_emits_host_port_and_protocol(self):
        got = []
        self.dlg.connect_requested.connect(lambda h, p, proto: got.append((h, p, proto)))
        self.dlg._connect()
        self.assertEqual(got, [("192.168.1.50", 14550, "udp")])


class FleetConnectLayoutTests(_WinBase):
    def test_fleet_keeps_only_scan_and_connect_buttons(self):
        self.assertTrue(hasattr(self.win, "btn_scan"))
        self.assertTrue(hasattr(self.win, "btn_conn"))
        self.assertFalse(hasattr(self.win, "cmb_proto"))
        self.assertFalse(hasattr(self.win, "ed_sitl"))
        self.assertEqual(self.win.btn_group_all.text(), "ALL")
        self.assertEqual(self.win.group_chips[1].toolTip().splitlines()[0], "Group 1 · 0 ลำ")


class ServoRemoteStateTests(_WinBase):
    """สถานะ A/B ต้องมาจากค่าจริง ไม่ใช่แค่จำสิ่งที่ UI สั่ง
    — ผู้ใช้โยกสวิตช์ A/B บน "รีโมท" ได้ cockpit ต้องเห็นด้วย

    ค่าจริงจากรีโมทของผู้ใช้: A=RC7 (ปล่อย 1050 → กด 1950),
                              B=RC8 (ปล่อย  900 → กด 2100)
    """

    # ค่า RC จริงจากรีโมท
    A_OFF, A_ON = 1050, 1950
    B_OFF, B_ON = 900, 2100

    def _t(self, did=1, rc7=None, rc8=None, rc_valid=True):
        t = self._telem(did)
        t.rc_ch7_raw = self.A_OFF if rc7 is None else rc7
        t.rc_ch8_raw = self.B_OFF if rc8 is None else rc8
        t.rc_valid = rc_valid
        t.servo_valid = False
        return t

    def test_remote_press_shows_up_without_ui_command(self):
        self.win._on_telemetry(self._t())
        self.win._select_drone(1)
        self.assertEqual(self.win._servo_state.get(1), set())
        # ไม่มีคำสั่งจาก cockpit เลย — รีโมทโยกสวิตช์ A (RC7: 1050 → 1950)
        self.win._on_telemetry(self._t(rc7=self.A_ON))
        self.assertEqual(self.win._servo_state.get(1), {"A"})
        self.assertFalse(_user_servo_calls(self.fake, self.win))

    def test_rest_values_are_not_mistaken_for_on(self):
        """A ปล่อยอยู่ที่ 1050 ต้องไม่ถูกตีความว่า 'กดอยู่'"""
        self.win._on_telemetry(self._t(rc7=self.A_OFF, rc8=self.B_OFF))
        self.assertEqual(self.win._servo_state.get(1), set())

    def test_both_channels_can_be_on(self):
        self.win._on_telemetry(self._t(rc7=self.A_ON, rc8=self.B_ON))
        self.assertEqual(self.win._servo_state.get(1), {"A", "B"})

    def test_remote_release_clears_state(self):
        self.win._on_telemetry(self._t(rc7=self.A_ON, rc8=self.B_ON))
        self.win._on_telemetry(self._t(rc7=self.A_OFF, rc8=self.B_ON))
        self.assertEqual(self.win._servo_state.get(1), {"B"})

    def test_badge_reflects_real_state(self):
        self.win._on_telemetry(self._t(rc7=self.A_ON))
        self.win._select_drone(1)
        self.assertTrue(self.win.fleet_items[1].lbl_servo.isVisible())
        self.assertEqual(self.win.fleet_items[1].lbl_servo.text(), "A")

    def test_falls_back_to_servo_output_when_no_rc(self):
        """รีโมทไม่ได้ต่อ → ใช้ servo output ของ FC แทน"""
        t = self._telem(1)
        t.rc_valid = False
        t.servo_valid = True
        t.servo_ch7_pwm, t.servo_ch8_pwm = 2100, 900
        self.win._on_telemetry(t)
        self.assertEqual(self.win._servo_state.get(1), {"A"})

    def test_no_source_at_all_does_not_override(self):
        """ไม่มีทั้ง RC และ servo → อย่าล้างสถานะที่ UI สั่งไว้"""
        self.win._on_telemetry(self._t())
        self.win._servo_state[1] = {"A"}
        self.win._on_telemetry(self._t(rc_valid=False))
        self.assertEqual(self.win._servo_state.get(1), {"A"})


class ServoToggleTests(_WinBase):
    """กดปุ่มเดิมครั้งที่ 2 = ยกเลิกสถานะเดิม"""

    def _t(self, rc7=1050, rc8=900, ovr7=False, ovr8=False):
        """ovr7/ovr8 = core รายงานว่ากำลัง override ช่องนั้นอยู่ (UI ถือห้อง)

        ขาเซอร์โวสะท้อนค่า RC เหมือนเครื่องจริงที่ตั้ง SERVOn_FUNCTION = RCINn
        """
        t = self._telem(1)
        t.rc_ch7_raw, t.rc_ch8_raw, t.rc_valid = rc7, rc8, True
        t.servo_valid = True
        t.servo_ch7_pwm, t.servo_ch8_pwm = rc7, rc8
        t.ovr_ch7, t.ovr_ch8 = ovr7, ovr8
        return t

    def _press(self, label):
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo(label)
        _pump(0.35)
        return [c for c in self.fake.calls
                if c[0] in ("servo_set", "servo_release")]

    def test_first_press_opens(self):
        self.win._on_telemetry(self._t())
        self.win._on_fleet_click(1, False)
        calls = self._press("A")
        self.assertEqual(calls[-1], ("servo_set", 1, 7, self.win.SERVO_PWM_ON["A"]))

    def test_second_press_releases_channel_back_to_remote(self):
        """ปิด = ปล่อย override คืนช่องให้รีโมท ไม่ใช่ override ค้างที่ค่าปิด
        (ถ้า override ค้าง สวิตช์จริงบนรีโมทจะถูกเมินตลอดไป)"""
        self.win._on_telemetry(self._t())
        self.win._on_fleet_click(1, False)
        self._press("A")
        # core ยืนยันว่ากำลัง override ช่องนี้อยู่ = UI ถือห้อง
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        calls = self._press("A")
        self.assertEqual(calls[-1], ("servo_release", 1, 7))

    def test_toast_says_close_when_ui_owns(self):
        """ไม่มีกล่องยืนยันแล้ว — toast จึงเป็นหลักฐานเดียวที่ผู้ใช้เห็นทันที
        ว่าการกดครั้งนี้คือ "ปิด" ไม่ใช่ "เปิด" """
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        self.win._on_fleet_click(1, False)
        self.win._cmd_servo("A")
        _pump(0.3)
        self.assertIn("ปิด", self.win.toast_msg.text())

    def test_channels_toggle_independently(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))  # A เปิดโดย UI, B ว่าง
        self.win._on_fleet_click(1, False)
        calls = self._press("B")
        self.assertEqual(calls[-1], ("servo_set", 1, 8, self.win.SERVO_PWM_ON["B"]),
                         "B ห้องว่าง กดแล้วต้องเป็นการเปิด")

    def test_button_lights_up_when_ui_owns_channel(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        self.win._on_fleet_click(1, False)
        self.assertIn("●", self.win.btn_servo_a.text())
        self.assertNotIn("●", self.win.btn_servo_b.text())

    def test_buttons_are_large_and_on_one_row(self):
        self.assertGreaterEqual(self.win.btn_servo_a.minimumHeight(), 60)
        self.assertGreaterEqual(self.win.btn_servo_b.minimumHeight(), 60)
        self.assertEqual(self.win.btn_servo_a.y(), self.win.btn_servo_b.y())


class ServoOwnershipTests(_WinBase):
    """กติกา "1 ห้อง" — ช่องหนึ่งมีเจ้าของได้ทีละฝ่าย

    UI เปิด → UI ถือห้อง → รีโมทแตะไม่ได้ (override ทับ)
    รีโมทเปิด → รีโมทถือห้อง → UI ต้องถูกปฏิเสธจนกว่าจะปิดสวิตช์ที่รีโมท
    """

    def _t(self, rc7=1050, rc8=900, ovr7=False, ovr8=False, servo=True):
        """telemetry ของลำที่ตั้ง SERVO7/8_FUNCTION = RCIN7/8 ไว้แล้ว

        ขาเซอร์โวจึงสะท้อนค่า RC ตรง ๆ เหมือนเครื่องจริงที่ตั้งค่าครบ
        (เดิม fixture นี้ตั้ง servo_valid=False แล้วอาศัย RC ดิบอย่างเดียว ซึ่งไม่ตรง
        กับของจริง และเป็นจุดที่ทำให้บั๊ก "ปุ่มถูกล็อกทั้งที่ไม่มีรีโมท" หลุดรอดมาได้)

        servo=False = FC ยังไม่สตรีม SERVO_OUTPUT_RAW (เพิ่งเชื่อมต่อ) → ยืนยันไม่ได้
        """
        t = self._telem(1)
        t.rc_ch7_raw, t.rc_ch8_raw, t.rc_valid = rc7, rc8, True
        t.servo_valid = servo
        t.servo_ch7_pwm, t.servo_ch8_pwm = (rc7, rc8) if servo else (0, 0)
        t.ovr_ch7, t.ovr_ch8 = ovr7, ovr8
        return t

    def _select(self):
        self.win._on_fleet_click(1, False)
        _pump(0.1)

    def test_rc_alone_without_servo_proof_does_not_lock(self):
        """RC ดิบสูงแต่ยังไม่มีข้อมูลขาเซอร์โว = ยืนยันไม่ได้ → ห้ามล็อกปุ่ม

        ArduPilot ส่ง RC_CHANNELS พร้อมค่า failsafe มาแม้ไม่มีรีโมทเลย
        """
        self.win._on_telemetry(self._t(rc7=1950, ovr7=False, servo=False))
        self.assertIsNone(self.win._servo_owner(1, "A"))

    def test_free_room_has_no_owner(self):
        self.win._on_telemetry(self._t())
        self.assertIsNone(self.win._servo_owner(1, "A"))

    def test_override_means_ui_owns_it(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        self.assertEqual(self.win._servo_owner(1, "A"), "UI")

    def test_on_without_override_means_rc_owns_it(self):
        """ค่าเปิดแต่ core ไม่ได้ override = คนโยกสวิตช์เป็นเจ้าของ"""
        self.win._on_telemetry(self._t(rc7=1950, ovr7=False))
        self.assertEqual(self.win._servo_owner(1, "A"), "RC")

    def test_ui_press_rejected_while_rc_owns(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=False))
        self._select()
        before = len(self.fake.calls)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.3)
        self.assertEqual(len(self.fake.calls), before,
                         "รีโมทถือห้องอยู่ UI ต้องไม่ส่งคำสั่งเลย")
        self.assertIn("รีโมท", self.win.banner.text())

    def test_ui_can_take_free_room(self):
        self.win._on_telemetry(self._t())
        self._select()
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.35)
        calls = [c for c in self.fake.calls if c[0] == "servo_set"]
        self.assertTrue(calls, "ห้องว่าง UI ต้องสั่งได้")
        self.assertEqual(calls[-1], ("servo_set", 1, 7, self.win.SERVO_PWM_ON["A"]))

    def test_ui_press_while_ui_owns_releases_room(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        self._select()
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.35)
        calls = [c for c in self.fake.calls if c[0] == "servo_release"]
        self.assertTrue(calls, "UI ถือห้องอยู่ กดซ้ำต้องปล่อยห้อง")

    def test_channels_own_rooms_independently(self):
        """A ถูกรีโมทถือ ไม่ควรทำให้ B กดไม่ได้"""
        self.win._on_telemetry(self._t(rc7=1950, rc8=900))
        self._select()
        self.assertEqual(self.win._servo_owner(1, "A"), "RC")
        self.assertIsNone(self.win._servo_owner(1, "B"))
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("B")
        _pump(0.35)
        self.assertTrue([c for c in self.fake.calls
                         if c[0] == "servo_set" and c[2] == 8])

    def test_button_shows_lock_when_rc_owns(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=False))
        self._select()
        self.assertIn("🔒", self.win.btn_servo_a.text())
        self.assertNotIn("🔒", self.win.btn_servo_b.text())

    def test_button_shows_dot_when_ui_owns(self):
        self.win._on_telemetry(self._t(rc7=1950, ovr7=True))
        self._select()
        self.assertIn("●", self.win.btn_servo_a.text())

    def test_verify_warns_when_switch_still_held(self):
        """ปล่อย override แล้วยังเปิดอยู่ = สวิตช์รีโมทค้าง ต้องเตือน ไม่ใช่เงียบ"""
        self.win._on_telemetry(self._t(rc7=1950, ovr7=False))
        self.win._verify_servo_released([1], "A")
        self.assertIn("ยังไม่ปิด", self.win.banner.text())

    def test_verify_silent_when_actually_closed(self):
        self.win._on_telemetry(self._t(rc7=1050, ovr7=False))
        self.win._hide_banner()
        self.win._verify_servo_released([1], "A")
        self.assertEqual(self.win.banner.text(), "")


class SafeReturnsResultTests(_WinBase):
    """_safe() ต้องคืนค่าที่ fn คืนมา — ไม่งั้นผู้เรียกที่เช็คผลลัพธ์จะเห็นเป็น
    "ล้มเหลว" ทุกครั้ง ทั้งที่ core รับคำสั่งสำเร็จแล้ว

    บั๊กจริง: audit log บันทึก ACCEPTED ในวินาทีเดียวกับที่ UI ขึ้น
    "FAILED — ไม่ได้รับคำตอบจาก core"
    """

    def test_safe_returns_value_on_success(self):
        sentinel = object()
        self.assertIs(self.win._safe(lambda: sentinel), sentinel)

    def test_safe_returns_none_on_exception(self):
        def boom():
            raise RuntimeError("พัง")
        self.assertIsNone(self.win._safe(boom))

    def test_successful_servo_is_not_reported_as_failed(self):
        t = self._telem(1)
        t.rc_ch7_raw, t.rc_ch8_raw, t.rc_valid = 1050, 900, True
        t.servo_valid = False
        t.ovr_ch7 = t.ovr_ch8 = False
        self.win._on_telemetry(t)
        self.win._on_fleet_click(1, False)

        seen = []
        self.win.cmd_result.connect(seen.append)
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._cmd_servo("A")
        _pump(0.45)
        servo_msgs = [m for m in seen if m.startswith("SERVO A")]
        self.assertTrue(servo_msgs, "ควรมีข้อความผลลัพธ์")
        self.assertFalse([m for m in servo_msgs if "FAILED" in m],
                         f"FakeClient คืน ok=True แต่ยังขึ้น FAILED: {servo_msgs}")


class TelemetryPerfTests(_WinBase):
    """_on_telemetry ต้องเบาพอที่ main thread จะว่างรับคลิกปุ่ม

    ของจริง telemetry มา 10 Hz ต่อลำ × 5 ลำ = 50 แพ็กเก็ต/วิ
    เดิมใช้ ~14 ms/แพ็กเก็ต = กิน main thread 70% → กดปุ่ม A/B แล้วหน่วงนานมาก
    ต้นตอ: เขียน SQLite ทุกแพ็กเก็ต + setStyleSheet ~8 ครั้ง/แพ็กเก็ต
    """

    def _t(self, did=1, **kw):
        t = self._telem(did)
        t.rc_ch7_raw, t.rc_ch8_raw, t.rc_valid = 1050, 900, True
        t.servo_valid = False
        t.ovr_ch7 = t.ovr_ch8 = False
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    def test_telemetry_packet_is_fast(self):
        import time
        for d in (1, 2, 3):
            self.win._on_telemetry(self._t(d))
        self.win._on_fleet_click(1, False)
        _pump(0.2)
        n = 120
        t0 = time.perf_counter()
        for i in range(n):
            self.win._on_telemetry(self._t(1 + i % 3))
        ms = (time.perf_counter() - t0) / n * 1000
        self.assertLess(ms, 4.0,
                        f"_on_telemetry ใช้ {ms:.2f} ms/แพ็กเก็ต — ช้าเกินจน UI หน่วง")

    def test_endpoint_not_rewritten_when_unchanged(self):
        """เดิมเขียน SQLite ทุกแพ็กเก็ตทั้งที่ IP ไม่เปลี่ยน"""
        calls = []
        self.win._ip_store.upsert = lambda *a, **k: calls.append(a)
        self.win._on_telemetry(self._t(1))
        first = len(calls)
        for _ in range(10):
            self.win._on_telemetry(self._t(1))
        self.assertEqual(len(calls), first,
                         "endpoint เดิมไม่ควรเขียน SQLite ซ้ำ")

    def test_endpoint_written_when_actually_changed(self):
        calls = []
        self.win._ip_store.upsert = lambda *a, **k: calls.append(a)
        self.win._on_telemetry(self._t(1, host="10.0.0.1"))
        before = len(calls)
        self.win._on_telemetry(self._t(1, host="10.0.0.99"))
        self.assertGreater(len(calls), before, "IP เปลี่ยนต้องบันทึกจริง")

    def test_cached_style_still_updates_on_change(self):
        """set_qss ต้องไม่บังการอัปเดตเมื่อค่าเปลี่ยนจริง"""
        self.win._on_telemetry(self._t(1, battery_pct=100.0))
        self.win._on_fleet_click(1, False)
        _pump(0.15)
        self.assertIn("100%", self.win.fleet_items[1].lbl_bat.text())
        self.win._on_telemetry(self._t(1, battery_pct=20.0))
        _pump(0.15)
        self.assertIn("20%", self.win.fleet_items[1].lbl_bat.text())
        self.assertIn(T("red"), self.win.fleet_items[1].lbl_bat.styleSheet())


class DroneIdReservationTests(_WinBase):
    def test_rapid_connects_get_unique_ids(self):
        """เดิม _next_drone_id ดูแค่ fleet_items (ว่างจน telemetry มา) → ต่อหลายลำได้ ID ซ้ำ"""
        ids = [self.win._next_drone_id() for _ in range(4)]
        self.assertEqual(len(set(ids)), 4, f"ID ต้องไม่ซ้ำกัน ได้ {ids}")

    def test_reservation_released_when_telemetry_arrives(self):
        did = self.win._next_drone_id()
        self.assertIn(did, self.win._reserved_ids)
        self.win._on_telemetry(self._telem(did))
        self.assertNotIn(did, self.win._reserved_ids)


if __name__ == "__main__":
    unittest.main()
