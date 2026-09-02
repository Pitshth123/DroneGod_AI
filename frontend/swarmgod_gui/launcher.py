"""
launcher.py — SwarmGod one-click launcher
กดเปิดครั้งเดียว → จัดการ certs + core + SITL + cockpit ให้เอง
พร้อม checklist โชว์สถานะแต่ละขั้นแบบสด จน "พร้อมใช้งาน"

รัน:  python -m swarmgod_gui.launcher     (หรือดับเบิลคลิก START_SWARMGOD.bat)
"""
import os
import shutil
import socket
import subprocess
import sys
import time

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QPlainTextEdit, QGraphicsOpacityEffect,
)

from .core.theme import (
    T, build_stylesheet, FONT_MONO, rgba, tinted_btn, filled_btn, ghost_btn,
)
from .core import settings_io

# ── paths ──
_HERE = os.path.dirname(__file__)                       # .../frontend/swarmgod_gui
ROOT = os.path.dirname(os.path.dirname(_HERE))          # .../SwarmGod
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
CERTS = os.path.join(ROOT, "certs")
CORE_EXE = os.path.join(BACKEND, "bin", "swarmgod-core.exe")

SITL_ADDR = ("127.0.0.1", 5760)
CORE_ADDR = ("127.0.0.1", 50051)
# ── ปรับได้ผ่าน env เมื่อย้ายเครื่อง (ดู docs/MIGRATION.md) ──
WSL_DISTRO = os.getenv("SWARMGOD_WSL_DISTRO", "Ubuntu")
GO_BIN = os.getenv("SWARMGOD_GO_BIN", r"C:\Program Files\Go\bin")
AP_DIR = os.getenv("SWARMGOD_AP_DIR", "~/ardupilot")          # ArduPilot ใน WSL
HOME_LOC = os.getenv("SWARMGOD_HOME_LOC", "14.9581695,102.0986187,0,0")  # lat,lon,alt,hdg

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _go_env():
    env = os.environ.copy()
    extra = GO_BIN + os.pathsep + os.path.join(os.path.expanduser("~"), "go", "bin")
    env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    return env


def _port_open(hostport, timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(hostport) == 0
    finally:
        s.close()


def _wait_port(hostport, secs, tick=None):
    for i in range(secs):
        if _port_open(hostport):
            return True
        if tick:
            tick(i, secs)
        time.sleep(1)
    return _port_open(hostport)


def _tail(path, n=8):
    """บรรทัดท้ายของไฟล์ log (ใช้บอกสาเหตุตอน SITL ไม่ขึ้น)"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip() for ln in f.readlines() if ln.strip()]
        return "\n".join(lines[-n:])
    except OSError:
        return ""


# ── worker: รันขั้นตอนทั้งหมดใน thread แยก ──
class Launcher(QThread):
    step = pyqtSignal(int, str, str)   # index, status(pending/work/ok/fail), detail
    log = pyqtSignal(str)
    done = pyqtSignal(bool)            # all ready?

    STEPS = [
        "ตรวจเครื่องมือ (Go / Python / WSL)",
        "Certificates — mTLS + MAVLink signing key",
        "Build core (ถ้ายังไม่มี binary)",
        "เปิด Core (Go backend) — mTLS + signing",
        "เปิด SITL (ArduPilot จำลอง)",
        "เปิด Cockpit (หน้า UI)",
    ]

    def __init__(self, use_sitl=True, drone_count=1, parent=None):
        super().__init__(parent)
        self.use_sitl = use_sitl
        self.max_drones_limit = 20
        try:
            cfg = settings_io.try_load_default()
            if cfg and "sliders" in cfg and "max_drones" in cfg["sliders"]:
                self.max_drones_limit = int(cfg["sliders"]["max_drones"])
        except Exception:
            pass
        self.drone_count = max(1, min(self.max_drones_limit, drone_count))
        self.core_proc = None
        self.sitl_proc = None
        self.cockpit_procs = []
        self._go = None
        self.runtime_env = os.environ.copy()

    def _emit(self, i, status, detail=""):
        self.step.emit(i, status, detail)

    def run(self):
        try:
            self._clean_leftovers()
            self._step_tools(0)
            self._step_certs(1)
            self._step_build(2)
            self._step_core(3)
            if self.use_sitl:
                self._step_sitl(4)
            else:
                self._emit(4, "ok", "ข้าม (ใช้โดรนจริง)")
            self._step_cockpit(5)
            self.log.emit("✅ พร้อมใช้งาน — cockpit เปิดแล้ว")
            self.done.emit(True)
        except Exception as e:
            self.log.emit(f"✗ ล้มเหลว: {e}")
            self.done.emit(False)

    # ── steps ──
    def _step_tools(self, i):
        self._emit(i, "work")
        self._go = shutil.which("go") or (os.path.join(GO_BIN, "go.exe")
                                          if os.path.exists(os.path.join(GO_BIN, "go.exe")) else None)
        if not self._go:
            self._emit(i, "fail", "ไม่พบ Go — ติดตั้ง: winget install GoLang.Go")
            raise RuntimeError("Go not found")
        if not shutil.which("wsl") and self.use_sitl:
            self._emit(i, "fail", "ไม่พบ WSL (จำเป็นสำหรับ SITL)")
            raise RuntimeError("WSL not found")
        self.log.emit(f"Go: {self._go}")
        self._emit(i, "ok", "ครบ")

    def _step_certs(self, i):
        self._emit(i, "work")
        ca = os.path.join(CERTS, "ca.crt")
        mk = os.path.join(CERTS, "mavlink_key")
        required = [
            ca, mk,
            os.path.join(CERTS, "server.crt"),
            os.path.join(CERTS, "server.key"),
            os.path.join(CERTS, "client.crt"),
            os.path.join(CERTS, "client.key"),
        ]
        if all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in required):
            self._emit(i, "ok", "มีอยู่แล้ว")
            return
        if not self.use_sitl:
            self._emit(i, "fail", "โหมดโดรนจริงต้องติดตั้ง cert/key ที่จับคู่กับ FC เอง")
            raise RuntimeError("real-drone mode requires pre-provisioned mTLS certificates and MAVLink signing key")
        self.log.emit("สร้าง certificates (gencerts)...")
        r = subprocess.run([self._go, "run", "./cmd/gencerts", "-out", "../certs"],
                           cwd=BACKEND, env=_go_env(), capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(ca):
            self._emit(i, "fail", "gencerts ล้มเหลว")
            raise RuntimeError(r.stderr[:200])
        self._emit(i, "ok", "สร้างใหม่ (mTLS + signing)")

    def _step_build(self, i):
        self._emit(i, "work")
        self.log.emit("build core จาก source ปัจจุบัน...")
        r = subprocess.run([self._go, "build", "-o", "bin/swarmgod-core.exe", "./cmd/swarmgod-core"],
                           cwd=BACKEND, env=_go_env(), capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(CORE_EXE):
            self._emit(i, "fail", "build ล้มเหลว")
            raise RuntimeError(r.stderr[:200])
        self._emit(i, "ok", "build จาก source ล่าสุดแล้ว")

    def _core_runtime_env(self):
        """กำหนด profile แบบ fail-closed โดยเฉพาะเมื่อใช้โดรนจริง."""
        env = os.environ.copy()
        if self.use_sitl:
            env["SWARMGOD_PROFILE"] = "sitl"
            return env

        profile = env.get("SWARMGOD_PROFILE", "production").strip().lower()
        if profile not in ("setup", "hil", "production"):
            raise RuntimeError("โหมดโดรนจริงอนุญาต SWARMGOD_PROFILE=setup, hil หรือ production เท่านั้น")

        # First-start chicken-and-egg fix: the operator may need telemetry/GPS
        # from the actual FC before a real field Home is known. Start Core in the
        # fail-closed setup profile instead of refusing to open the UI. Core setup
        # permits connect/read-only telemetry but rejects every flight-mutating RPC.
        if not env.get("SWARMGOD_HOME_LOC", "").strip():
            env["SWARMGOD_PROFILE"] = "setup"
            env.pop("SWARMGOD_MISSION_AUTHORITY", None)
            env.pop("SWARMGOD_BENCH_CONFIRM", None)
            return env

        env["SWARMGOD_PROFILE"] = profile
        if profile == "production":
            env["SWARMGOD_MAVLINK_STRICT"] = "1"
            signing = env.get("SWARMGOD_MAVLINK_SIGNING", "").strip().lower()
            if signing in ("off", "0", "false"):
                raise RuntimeError("production ห้ามปิด MAVLink signing")
            if not env.get("SWARMGOD_TOKEN", "").strip():
                raise RuntimeError("production ต้องตั้ง SWARMGOD_TOKEN จาก swarmadmin session new")
        return env

    def _step_core(self, i):
        self._emit(i, "work")
        self.runtime_env = self._core_runtime_env()
        # เก็บ output ของ core ลงไฟล์ แทนที่จะทิ้งลง DEVNULL
        # (core เขียน log ของตัวเองที่ backend/logs/core-YYYYMMDD.log อยู่แล้ว
        #  ไฟล์นี้เก็บเพิ่มเผื่อ core พังก่อนตั้ง logger ได้ เช่น panic ตอนเริ่ม)
        log_dir = os.path.join(BACKEND, "logs")
        os.makedirs(log_dir, exist_ok=True)
        stdout_path = os.path.join(log_dir, "core-stdout.log")
        try:
            out = open(stdout_path, "a", encoding="utf-8", errors="replace")
        except OSError:
            out = subprocess.DEVNULL
        self.core_proc = subprocess.Popen(
            [CORE_EXE], cwd=BACKEND, creationflags=CREATE_NO_WINDOW,
            stdout=out, stderr=subprocess.STDOUT, env=self.runtime_env)
        if not _wait_port(CORE_ADDR, 12):
            self._emit(i, "fail", "core ไม่ตอบที่ :50051")
            raise RuntimeError("core did not start")
        profile = self.runtime_env.get("SWARMGOD_PROFILE", "sitl")
        self._emit(i, "ok", f"gRPC :50051 [{profile} · mTLS + signing]")

    def _check_sitl_built(self):
        """เช็คว่ามี arducopter ที่ build แล้วใน WSL — คืน (ok, เหตุผล)

        ถ้าไม่เช็คก่อน sim_vehicle.py จะตายเงียบ (เพราะ --no-rebuild)
        แล้ว launcher จะรอ port จนหมดเวลาโดยไม่รู้สาเหตุ
        """
        try:
            r = subprocess.run(
                ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc",
                 f"test -x {AP_DIR}/build/sitl/bin/arducopter && echo YES || echo NO"],
                capture_output=True, text=True, timeout=90,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            return False, f"เรียก WSL ไม่ได้ ({e})"
        out = (r.stdout or "").replace("\x00", "")
        if "YES" in out:
            return True, ""
        if "NO" in out:
            return False, (f"ยังไม่ได้ build ArduCopter SITL ที่ {AP_DIR} "
                           "— รัน BUILD_SITL.bat ก่อน (ครั้งเดียว ~15-30 นาที)")
        return False, (f"WSL distro '{WSL_DISTRO}' ใช้ไม่ได้ "
                       "— ตรวจด้วย: wsl -l -v (ตั้งชื่อที่ถูกผ่าน SWARMGOD_WSL_DISTRO)")

    def _step_sitl(self, i):
        n = self.drone_count
        self._emit(i, "work", f"boot {n} ลำ...")
        built, why = self._check_sitl_built()
        if not built:
            self._emit(i, "fail", why)
            raise RuntimeError(why)
        if n == 1:
            cmd = (f"cd {AP_DIR} && Tools/autotest/sim_vehicle.py -v ArduCopter -I0 "
                   f"--no-rebuild --no-mavproxy --custom-location={HOME_LOC}")
        else:
            cmd = (f"cd {AP_DIR} && Tools/autotest/sim_vehicle.py -v ArduCopter -I0 "
                   f"--count {n} --auto-sysid --no-rebuild --no-mavproxy "
                   f"--custom-location={HOME_LOC} --auto-offset-line 90,15")
        # เก็บ output ของ SITL ลงไฟล์ (เดิมทิ้งลง DEVNULL → พังแล้วไม่รู้สาเหตุ)
        log_dir = os.path.join(BACKEND, "logs")
        os.makedirs(log_dir, exist_ok=True)
        sitl_log = os.path.join(log_dir, "sitl.log")
        try:
            out = open(sitl_log, "w", encoding="utf-8", errors="replace")
        except OSError:
            out = subprocess.DEVNULL
        self.sitl_proc = subprocess.Popen(
            ["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc", cmd],
            creationflags=CREATE_NO_WINDOW,
            stdout=out, stderr=subprocess.STDOUT)

        # รอทุก port (5760, 5770, ...) พร้อม
        for idx in range(n):
            port = 5760 + idx * 10

            def tick(sec, total, p=port, k=idx):
                self._emit(i, "work", f"boot UAV_{k+1} (:{p})... {sec}s")
            if not _wait_port(("127.0.0.1", port), 45, tick):
                self._emit(i, "fail", f"SITL ไม่เปิดที่ :{port}")
                detail = _tail(sitl_log)
                if detail:
                    self.log.emit(f"— SITL log ({sitl_log}) —\n{detail}")
                else:
                    self.log.emit(f"ไม่มี output จาก SITL เลย (ดู {sitl_log})")
                raise RuntimeError(f"SITL port {port} not up")
        ports = ", ".join(str(5760 + k * 10) for k in range(n))
        self._emit(i, "ok", f"{n} ลำ พร้อม (:{ports})")

    def _step_cockpit(self, i):
        self._emit(i, "work")
        # ลำแรก: auto-connect โดรนทั้งหมด (ถ้าใช้ SITL)
        self.spawn_cockpit(autoconnect=self.use_sitl)
        time.sleep(1.5)
        self._emit(i, "ok", "หน้าต่าง UI เปิดแล้ว")

    def spawn_cockpit(self, autoconnect=False):
        """เปิด cockpit หนึ่งหน้าต่าง (autoconnect=เชื่อมโดรนอัตโนมัติ; ปุ่ม re-open ไม่ต้อง)"""
        env = self.runtime_env.copy()
        env["SWARMGOD_AUTHED"] = "1"   # ผ่านรหัสที่ launcher แล้ว ไม่ต้องถามซ้ำ
        if autoconnect and self.use_sitl:
            env["SWARMGOD_AUTOCONNECT_N"] = str(self.drone_count)
        else:
            env.pop("SWARMGOD_AUTOCONNECT_N", None)
            env.pop("SWARMGOD_AUTOCONNECT", None)
        p = subprocess.Popen(
            [sys.executable, "-m", "swarmgod_gui"], cwd=FRONTEND, env=env,
            creationflags=CREATE_NO_WINDOW)
        self.cockpit_procs.append(p)
        return p

    # ── cleanup ──
    def _clean_leftovers(self):
        try:
            subprocess.run(["taskkill", "/F", "/IM", "swarmgod-core.exe"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        if self.use_sitl:
            try:
                subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc",
                                "pkill -9 -x arducopter"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass

    def stop_all(self):
        for p in list(self.cockpit_procs) + [self.core_proc, self.sitl_proc]:
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        self.cockpit_procs = []
        try:
            subprocess.run(["taskkill", "/F", "/IM", "swarmgod-core.exe"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        if self.use_sitl:
            try:
                subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "-e", "bash", "-lc",
                                "pkill -9 -x arducopter"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass


# ── UI ──
# คำอธิบายสั้น ๆ ใต้แต่ละขั้น (ให้ผู้ใช้มือใหม่เข้าใจว่าแต่ละสเต็ปทำอะไร)
STEP_DESCS = [
    "เช็กว่ามี Go, Python และ WSL ครบสำหรับรันระบบ",
    "สร้างกุญแจเข้ารหัส mTLS และคีย์เซ็น MAVLink (ทำครั้งเดียว)",
    "คอมไพล์ Go backend เป็นไฟล์รัน (ครั้งแรกอาจนาน)",
    "สตาร์ท backend gRPC ที่พอร์ต :50051",
    "สตาร์ทโดรนจำลอง ArduPilot (ข้ามเมื่อใช้โดรนจริง)",
    "เปิดหน้าต่างควบคุม Cockpit",
]


class ModeCard(QFrame):
    """การ์ดเลือกโหมด กดทั้งใบได้ — ใช้คู่ซ้าย–ขวาในแถวเดียว"""
    clicked = pyqtSignal()

    def __init__(self, emoji, title, desc, accent, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(88)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        self._ic = QLabel(emoji)
        self._ic.setFixedWidth(34)
        self._ic.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._ic.setStyleSheet("font-size:26px; background:transparent;")
        lay.addWidget(self._ic)

        col = QVBoxLayout()
        col.setSpacing(3)
        self._t = QLabel(title)
        self._t.setWordWrap(True)
        self._t.setStyleSheet(
            f"color:{T('text')}; font-size:14px; font-weight:700; background:transparent;")
        self._d = QLabel(desc)
        self._d.setWordWrap(True)
        self._d.setStyleSheet(f"color:{T('dim')}; font-size:11px; background:transparent;")
        col.addWidget(self._t)
        col.addWidget(self._d)
        lay.addLayout(col, 1)

        self.set_selected(False)

    def set_selected(self, sel):
        self._selected = sel
        if sel:
            self.setStyleSheet(
                f"ModeCard {{ background:{rgba(self._accent, 0.14)};"
                f" border:2px solid {self._accent}; border-radius:14px; }}")
        else:
            self.setStyleSheet(
                f"ModeCard {{ background:{T('panel2')};"
                f" border:2px solid {T('line')}; border-radius:14px; }}")

    def mousePressEvent(self, e):
        if self.isEnabled() and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SwarmGod — Launcher")
        _icon_path = os.path.join(_HERE, "assets", "logo.ico")
        if os.path.exists(_icon_path):
            from PyQt5.QtGui import QIcon as _QIcon
            self.setWindowIcon(_QIcon(_icon_path))
        self.setFixedSize(960, 560)
        self.setStyleSheet(build_stylesheet())
        self.rows = []
        self.worker = None
        self.use_sitl = True
        self.drone_count = 1
        self.max_drones_limit = 20
        try:
            cfg = settings_io.try_load_default()
            if cfg and "sliders" in cfg and "max_drones" in cfg["sliders"]:
                self.max_drones_limit = int(cfg["sliders"]["max_drones"])
        except Exception:
            pass
        self._build()
        self._set_mode(True)
        _c = os.getenv("SWARMGOD_LAUNCHER_COUNT")
        if _c:
            try:
                self.drone_count = max(1, min(self.max_drones_limit, int(_c)))
            except ValueError:
                pass
        self._refresh_count()
        if os.getenv("SWARMGOD_LAUNCHER_AUTO"):
            QTimer.singleShot(300, self._start)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ── หัว + คำอธิบายสั้นในแถวเดียว ──
        head = QHBoxLayout()
        head.setSpacing(14)
        logo_path = os.path.join(_HERE, "assets", "logo.png")
        if os.path.exists(logo_path):
            from PyQt5.QtGui import QPixmap as _QPixmap
            lg = QLabel()
            lg.setPixmap(_QPixmap(logo_path).scaledToHeight(40, Qt.SmoothTransformation))
            lg.setStyleSheet("background:transparent;")
            head.addWidget(lg)
        title = QLabel("◈ SWARMGOD  LAUNCHER")
        title.setStyleSheet(
            f"color:{T('green')}; font-weight:700; letter-spacing:3px; font-size:16px;")
        head.addWidget(title)
        banner = QLabel(
            "สถานีควบคุมภาคพื้น (GCS) · เลือกโหมดแล้วกด START — "
            "ระบบเตรียม core / โดรน / Cockpit ให้อัตโนมัติ")
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; background:{rgba(T('accent'), 0.10)};"
            f" border:1px solid {rgba(T('accent'), 0.28)}; border-radius:10px; padding:7px 12px;")
        head.addWidget(banner, 1)
        root.addLayout(head)

        # ── การ์ดโหมดคู่ซ้าย–ขวา ──
        lbl_mode = QLabel("เลือกโหมด")
        lbl_mode.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:600; letter-spacing:1px;")
        root.addWidget(lbl_mode)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_sitl = ModeCard(
            "🖥️", "SITL — โดรนจำลอง",
            "จำลองด้วย ArduPilot บนเครื่องนี้ (ผ่าน WSL) — ไม่ต้องมีโดรนจริง "
            "ปลอดภัย เหมาะฝึกและทดสอบ",
            T("green"))
        self.card_sitl.clicked.connect(lambda: self._set_mode(True))
        cards.addWidget(self.card_sitl, 1)

        self.card_real = ModeCard(
            "📡", "โดรนจริง",
            "เชื่อมต่อผ่านวิทยุ/เทเลเมทรี (MAVLink) — ข้ามตัวจำลอง "
            "แล้วเพิ่มโดรนในหน้า Cockpit",
            T("accent"))
        self.card_real.clicked.connect(lambda: self._set_mode(False))
        cards.addWidget(self.card_real, 1)
        root.addLayout(cards)

        # ── แถวกลาง: ซ้าย stepper+ปุ่ม · ขวา checklist+log ──
        mid = QHBoxLayout()
        mid.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)

        self.stepper_box = QFrame()
        self.stepper_box.setObjectName("stepper")
        self.stepper_box.setStyleSheet(
            f"#stepper {{ background:{T('panel2')}; border:1px solid {T('line')};"
            f" border-radius:14px; }}")
        stl = QVBoxLayout(self.stepper_box)
        stl.setContentsMargins(14, 10, 14, 10)
        stl.setSpacing(6)

        strow = QHBoxLayout()
        strow.setSpacing(10)
        cap = QVBoxLayout()
        cap.setSpacing(1)
        lbl_c = QLabel("จำนวนโดรนจำลอง")
        lbl_c.setStyleSheet(
            f"color:{T('text')}; font-size:13px; font-weight:700; background:transparent;")
        hint = QLabel(f"แต่ละลำใช้ทรัพยากรเพิ่มขึ้น · 1–{self.max_drones_limit}")
        hint.setStyleSheet(f"color:{T('faint')}; font-size:10px; background:transparent;")
        cap.addWidget(lbl_c)
        cap.addWidget(hint)
        strow.addLayout(cap, 1)

        self.btn_minus = QPushButton("−")
        self.btn_minus.setFixedSize(40, 40)
        self.btn_minus.setStyleSheet(ghost_btn(radius=11, font=20, weight=700))
        self.btn_minus.clicked.connect(lambda: self._bump_count(-1))
        strow.addWidget(self.btn_minus)

        active = self.use_sitl
        self.lbl_num = QLabel("1")
        self.lbl_num.setFixedWidth(44)
        self.lbl_num.setAlignment(Qt.AlignCenter)
        self.lbl_num.setStyleSheet(
            f"color:{T('green') if active else T('dim')}; font-size:24px;"
            f" font-weight:800; background:transparent;")
        strow.addWidget(self.lbl_num)

        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedSize(40, 40)
        self.btn_plus.setStyleSheet(ghost_btn(radius=11, font=20, weight=700))
        self.btn_plus.clicked.connect(lambda: self._bump_count(+1))
        strow.addWidget(self.btn_plus)
        stl.addLayout(strow)
        left.addWidget(self.stepper_box)

        self._stepper_fx = QGraphicsOpacityEffect(self.stepper_box)
        self.stepper_box.setGraphicsEffect(self._stepper_fx)
        self._stepper_fx.setOpacity(1.0)

        self.status = QLabel("พร้อมเริ่ม — กด START")
        self.status.setStyleSheet(f"color:{T('amber')}; font-size:13px; font-weight:700;")
        self.status.setAlignment(Qt.AlignCenter)
        left.addWidget(self.status)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_start = QPushButton("▶  START")
        self.btn_start.setMinimumHeight(44)
        self.btn_start.setStyleSheet(filled_btn(T("green"), radius=12, font=15, weight=800))
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("■  STOP")
        self.btn_stop.setMinimumHeight(44)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(tinted_btn(T("red"), radius=12, font=13, weight=700))
        self.btn_stop.clicked.connect(self._stop)
        btns.addWidget(self.btn_start, 2)
        btns.addWidget(self.btn_stop, 1)
        left.addLayout(btns)

        self.btn_cockpit = QPushButton("⧉  เปิด COCKPIT อีกหน้าต่าง")
        self.btn_cockpit.setMinimumHeight(34)
        self.btn_cockpit.setEnabled(False)
        self.btn_cockpit.setStyleSheet(tinted_btn(T("cyan"), radius=11, font=12, weight=700))
        self.btn_cockpit.clicked.connect(self._open_cockpit)
        left.addWidget(self.btn_cockpit)
        left.addStretch(1)

        mid.addLayout(left, 2)

        # ขวา: checklist + log
        right = QVBoxLayout()
        right.setSpacing(8)

        box = QFrame()
        box.setObjectName("checklist")
        box.setStyleSheet(
            f"#checklist {{ background:{T('panel2')}; border:1px solid {T('line')};"
            f" border-radius:14px; }}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(6)
        for idx, name in enumerate(Launcher.STEPS):
            row = QHBoxLayout()
            row.setSpacing(8)
            icon = QLabel("○")
            icon.setFixedWidth(18)
            icon.setAlignment(Qt.AlignTop)
            icon.setStyleSheet(f"color:{T('dim')}; font-size:14px; background:transparent;")

            midcol = QVBoxLayout()
            midcol.setSpacing(0)
            txt = QLabel(name)
            txt.setStyleSheet(
                f"color:{T('text')}; font-size:11px; font-weight:600; background:transparent;")
            desc = QLabel(STEP_DESCS[idx] if idx < len(STEP_DESCS) else "")
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color:{T('faint')}; font-size:9px; background:transparent;")
            midcol.addWidget(txt)
            midcol.addWidget(desc)

            det = QLabel("")
            det.setStyleSheet(f"color:{T('dim')}; font-size:9px; background:transparent;")
            det.setAlignment(Qt.AlignRight | Qt.AlignTop)
            det.setMinimumWidth(90)

            row.addWidget(icon)
            row.addLayout(midcol, 1)
            row.addWidget(det)
            bl.addLayout(row)
            self.rows.append((icon, txt, det))
        right.addWidget(box, 1)

        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setFixedHeight(88)
        self.logbox.setPlaceholderText("log…")
        self.logbox.setStyleSheet(
            f"QPlainTextEdit {{ background:{T('bg')}; color:{T('green_dim')};"
            f" border:1px solid {T('line')}; border-radius:12px; padding:6px;"
            f" font-family:{FONT_MONO}; font-size:10px; }}")
        right.addWidget(self.logbox)

        mid.addLayout(right, 3)
        root.addLayout(mid, 1)

    # ── โหมด + จำนวนโดรน ──
    def _set_mode(self, use_sitl):
        self.use_sitl = use_sitl
        self.card_sitl.set_selected(use_sitl)
        self.card_real.set_selected(not use_sitl)
        self._set_stepper_enabled(use_sitl)

    def _set_stepper_enabled(self, on):
        self.stepper_box.setEnabled(on)
        if getattr(self, "_stepper_fx", None) is not None:
            self._stepper_fx.setOpacity(1.0 if on else 0.4)
        if on:
            self._refresh_count()

    def _bump_count(self, delta):
        self.drone_count = max(1, min(self.max_drones_limit, self.drone_count + delta))
        self._refresh_count()

    def _refresh_count(self):
        self.lbl_num.setText(str(self.drone_count))
        # เปิด/ปิดปุ่มที่ขอบเขต (เฉพาะตอน stepper ใช้งานได้)
        active = self.stepper_box.isEnabled()
        self.btn_minus.setEnabled(active and self.drone_count > 1)
        self.btn_plus.setEnabled(active and self.drone_count < self.max_drones_limit)

    def _set_controls_enabled(self, on):
        """เปิด/ปิดการ์ดเลือกโหมด + stepper (ล็อกระหว่างรัน)"""
        self.card_sitl.setEnabled(on)
        self.card_real.setEnabled(on)
        if on:
            self._set_stepper_enabled(self.use_sitl)
        else:
            self._set_stepper_enabled(False)

    def _start(self):
        self.btn_start.setEnabled(False)
        self._set_controls_enabled(False)
        self.status.setText("กำลังเริ่ม...")
        self.status.setStyleSheet(f"color:{T('amber')}; font-size:13px; font-weight:700;")
        for icon, _, det in self.rows:
            icon.setText("○"); icon.setStyleSheet(f"color:{T('dim')}; font-size:15px; background:transparent;")
            det.setText("")
        self.worker = Launcher(use_sitl=self.use_sitl,
                               drone_count=self.drone_count)
        self.worker.step.connect(self._on_step)
        self.worker.log.connect(self._on_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.btn_stop.setEnabled(True)

    def _on_step(self, i, status, detail):
        icon, _, det = self.rows[i]
        m = {"pending": ("○", T("dim")), "work": ("◐", T("amber")),
             "ok": ("●", T("green")), "fail": ("✗", T("red"))}
        ch, col = m.get(status, ("○", T("dim")))
        icon.setText(ch)
        icon.setStyleSheet(f"color:{col}; font-size:15px; font-weight:700; background:transparent;")
        if detail:
            det.setText(detail)
            det.setStyleSheet(
                f"color:{col if status != 'work' else T('dim')}; font-size:10px; background:transparent;")

    def _on_log(self, msg):
        self.logbox.appendPlainText(msg)

    def _on_done(self, ok):
        if ok:
            self.status.setText("✅  พร้อมใช้งาน — ไปที่หน้าต่าง Cockpit ได้เลย")
            self.status.setStyleSheet(f"color:{T('green')}; font-size:14px; font-weight:700;")
            self.btn_cockpit.setEnabled(True)
            shot = os.getenv("SWARMGOD_LAUNCHER_SHOT")
            if shot:
                QTimer.singleShot(700, lambda: QApplication.primaryScreen()
                                  .grabWindow(int(self.winId())).save(shot))
            if os.getenv("SWARMGOD_LAUNCHER_AUTO"):
                QTimer.singleShot(2500, lambda: (self.worker.stop_all(), QApplication.quit()))
        else:
            self.status.setText("✗  มีปัญหา — ดู log ด้านล่าง")
            self.status.setStyleSheet(f"color:{T('red')}; font-size:13px; font-weight:700;")
            self.btn_start.setEnabled(True)
            self._set_controls_enabled(True)

    def _open_cockpit(self):
        if self.worker:
            self.worker.spawn_cockpit(autoconnect=False)
            self._on_log("เปิด cockpit เพิ่มอีกหน้าต่าง")

    def _stop(self):
        if self.worker:
            self.worker.stop_all()
        self.status.setText("หยุดทั้งหมดแล้ว")
        self.status.setStyleSheet(f"color:{T('dim')}; font-size:13px;")
        self.btn_stop.setEnabled(False)
        self.btn_cockpit.setEnabled(False)
        self.btn_start.setEnabled(True)
        self._set_controls_enabled(True)

    def closeEvent(self, e):
        if self.worker:
            self.worker.stop_all()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _icon_path = os.path.join(_HERE, "assets", "logo.ico")
    if os.path.exists(_icon_path):
        from PyQt5.QtGui import QIcon
        app.setWindowIcon(QIcon(_icon_path))

    # ── ประตูรหัสผ่านก่อนเข้าโปรแกรม ──
    # ถามที่นี่ที่เดียว: cockpit ที่ launcher เปิดต่อจะได้ธง SWARMGOD_AUTHED=1
    # จึงไม่ถามซ้ำ (ดู spawn_cockpit / core.auth.already_authed)
    from .widgets.login_dialog import require_passcode
    if not require_passcode():
        sys.exit(0)

    w = LauncherWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
