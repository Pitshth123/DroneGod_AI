"""กดสวิตช์ WAVE ปิด — ต้องไม่วน changed ↔ setCurrent จนแอปเด้ง"""
import time
import unittest

from swarmgod_gui.widgets.controls import CapsuleSwitch

from tests.test_wave import WaveBase, _app


class WaveSwitchClickTest(WaveBase):
    def test_click_off_does_not_recurse(self):
        calls = []
        self.win.sw_wave.changed.connect(calls.append)
        self.win.sw_wave.setCurrent(1)       # เปิดแบบคลิกจริง
        _app.processEvents()
        self.assertTrue(self.win._wave_enabled)

        calls.clear()
        self.win.sw_wave._lab_l.click()      # กด OFF ระหว่างเม็ดยังสไลด์อยู่
        _app.processEvents()
        self.assertFalse(self.win._wave_enabled)
        self.assertEqual(self.win.sw_wave.current(), 0)
        self.assertEqual(calls, [0])

    def test_reject_open_in_separate_mode_settles_off(self):
        self.win._wp_set_separate(True)
        self.win.sw_wave._lab_r.click()      # ขอเปิดทั้งที่อยู่ SEPARATE
        _app.processEvents()
        self.assertFalse(self.win._wave_enabled)
        self.assertEqual(self.win.sw_wave.current(), 0)


class CapsuleSwitchReentryTest(unittest.TestCase):
    """เคสจริงที่ทำให้แอปเด้ง: กดสวิตช์กลับตอนเม็ดยังสไลด์ค้างกลางทาง
    แล้ว slot ของ changed เรียก setCurrent กลับมา → วนซ้ำจนสแตกล้น (โปรเซสตายเงียบ ๆ)"""

    def test_setcurrent_from_slot_mid_animation(self):
        sw = CapsuleSwitch("OFF", "ON", selected=0)
        self.addCleanup(sw.deleteLater)
        sw.show()
        _app.processEvents()

        emits = []

        def slot(i):
            emits.append(i)
            if i == 0:
                sw.setCurrent(0)          # sync กลับเหมือนที่ _wave_toggle ทำ

        sw.changed.connect(slot)
        sw.setCurrent(1)
        deadline = time.time() + 0.06     # ปล่อยให้อนิเมชันค้างกลางทาง
        while time.time() < deadline:
            _app.processEvents()
        self.assertNotEqual(sw._thumb.geometry(), sw._thumb_rect(1))

        emits.clear()
        sw._lab_l.click()
        self.assertEqual(emits, [0])
        self.assertEqual(sw.current(), 0)


if __name__ == "__main__":
    unittest.main()
