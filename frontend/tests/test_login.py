"""
ทดสอบหน้ารหัสผ่านก่อนเข้าโปรแกรม

รัน headless:
    cd frontend
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_login.py
"""
import os
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

from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

from swarmgod_gui.core import auth  # noqa: E402
from swarmgod_gui.widgets.login_dialog import LoginDialog, require_passcode  # noqa: E402

_app = QApplication.instance() or QApplication([])


class PasscodeTests(unittest.TestCase):
    def setUp(self):
        # กัน env/ไฟล์บนเครื่องจริงมารบกวนผลทดสอบ
        for k in ("SWARMGOD_PASSCODE", "SWARMGOD_AUTHED"):
            os.environ.pop(k, None)

    def test_correct_code_accepted(self):
        self.assertTrue(auth.verify("22140"))

    def test_wrong_codes_rejected(self):
        for bad in ("2214", "221400", "", "   ", "abcde", "00000"):
            self.assertFalse(auth.verify(bad), f"{bad!r} ไม่ควรผ่าน")

    def test_whitespace_around_code_tolerated(self):
        """ผู้ใช้พิมพ์เว้นวรรคติดมาไม่ควรถูกปฏิเสธ"""
        self.assertTrue(auth.verify("  22140  "))

    def test_source_does_not_contain_plaintext_code(self):
        """เปิดไฟล์ดูแล้วต้องไม่เห็นรหัสตรง ๆ (เก็บเป็น SHA-256)"""
        src = os.path.join(_FRONTEND, "swarmgod_gui", "core", "auth.py")
        with open(src, encoding="utf-8") as f:
            body = f.read()
        self.assertNotIn("22140", body)

    def test_env_overrides_the_code(self):
        os.environ["SWARMGOD_PASSCODE"] = "999"
        try:
            self.assertTrue(auth.verify("999"))
            self.assertFalse(auth.verify("22140"), "ตั้ง env แล้วรหัสเดิมต้องใช้ไม่ได้")
        finally:
            os.environ.pop("SWARMGOD_PASSCODE", None)

    def test_passcode_file_overrides_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "passcode")
            with open(path, "w", encoding="utf-8") as f:
                f.write("7777\n")
            with mock.patch.object(auth, "_passcode_file", return_value=path):
                self.assertTrue(auth.verify("7777"))
                self.assertFalse(auth.verify("22140"))


class LoginDialogTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SWARMGOD_AUTHED", None)
        self.dlg = LoginDialog()

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()
        _app.processEvents()

    def test_input_is_masked(self):
        from PyQt5.QtWidgets import QLineEdit
        self.assertEqual(self.dlg.ed.echoMode(), QLineEdit.Password,
                         "ช่องรหัสต้องไม่โชว์ตัวอักษร")

    def test_wrong_code_shows_error_and_does_not_let_you_in(self):
        self.dlg.ed.setText("00000")
        self.dlg._try_unlock()
        self.assertIn("ไม่ถูกต้อง", self.dlg.err.text())
        self.assertNotEqual(self.dlg.result(), QDialog.Accepted,
                            "รหัสผิดต้องไม่ผ่านเข้าโปรแกรม")

    def test_typing_again_clears_the_error(self):
        """แดงค้างทั้งที่กำลังแก้อยู่ = รบกวนสายตา"""
        self.dlg.ed.setText("00000")
        self.dlg._try_unlock()
        self.assertIn("ไม่ถูกต้อง", self.dlg.err.text())
        self.dlg._on_typing("2")
        self.assertFalse(self.dlg.err.text().strip())

    def test_correct_code_accepts_dialog(self):
        got = []
        self.dlg.unlocked.connect(lambda: got.append(True))
        self.dlg.ed.setText("22140")
        self.dlg._try_unlock()
        self.assertEqual(self.dlg.result(), QDialog.Accepted)
        self.assertTrue(got, "ต้องส่งสัญญาณ unlocked")

    def test_error_row_keeps_its_height(self):
        """ข้อความ error ต้องไม่ทำ layout ขยับตอนโผล่/หาย (บทเรียนจาก banner §11.13)"""
        before = self.dlg.err.height()
        self.dlg.ed.setText("00000")
        self.dlg._try_unlock()
        _app.processEvents()
        self.assertEqual(self.dlg.err.height(), before)


class AuthBypassTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SWARMGOD_AUTHED", None)

    def test_launcher_child_skips_the_prompt(self):
        """cockpit ที่ launcher เปิดต่อไม่ต้องถามรหัสซ้ำ"""
        os.environ["SWARMGOD_AUTHED"] = "1"
        self.assertTrue(auth.already_authed())
        self.assertTrue(require_passcode(), "มีธงแล้วต้องผ่านโดยไม่เปิดหน้าต่าง")

    def test_direct_launch_still_asks(self):
        os.environ.pop("SWARMGOD_AUTHED", None)
        self.assertFalse(auth.already_authed(),
                         "เปิด cockpit ตรง ๆ ต้องยังถามรหัส")

    def test_mark_authed_sets_flag_for_child_process(self):
        env = auth.mark_authed({})
        self.assertEqual(env["SWARMGOD_AUTHED"], "1")


if __name__ == "__main__":
    unittest.main()
