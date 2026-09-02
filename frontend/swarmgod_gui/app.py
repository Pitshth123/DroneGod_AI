"""
app.py — SwarmGod cockpit main window (SPA-style dark redesign)
เลย์เอาต์ 4 ส่วน: ซ้าย = FLEET + ลำที่เลือก, กลาง = แผนที่, ล่าง = MISSION LOG,
ขวา = COMMANDS (accordion) + สวิตช์ UI/REMOTE ที่ล็อกคำสั่งเมื่ออยู่โหมด REMOTE
"""
import json
import html
import math
import os
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import (
    Qt, QTimer, QUrl, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect, QEvent,
)
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QGridLayout, QComboBox, QButtonGroup,
    QSizePolicy, QMenu, QFileDialog, QInputDialog, QStackedWidget, QApplication,
    QCheckBox,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel

from .core import theme
from .core.theme import (
    T, build_stylesheet, FONT_FAMILY, FONT_MONO, rgba, hairline,
    tinted_btn, filled_btn, ghost_btn, pad_btn, section_label_qss, card_qss,
    drone_color, badge_style, DRONE_COLORS, set_drone_color,
)
from .core.grpc_client import CoreClient, TelemetryThread, EventThread
from .core.command_gateway import CommandGateway
from .core.field_server import (
    FieldServer, SessionStore, TelemetryHub, DEFAULT_PORT as DEFAULT_FIELD_PORT,
)
from .core.web_bridge import WebBridge
from .core.map_bridge import MapBridge, parse_latlon
from .core.tile_cache import TileServer
from .core.pinger import PingService
from .core.ip_store import IpStore
from .core.group_store import GroupStore
from .core.health_monitor import HealthMonitor
from .core.telemetry_store import TelemetryRenderGate, TelemetryStore
from .core import settings_io
from .core import rpc
from .widgets.controls import SliderField, Segmented, CapsuleSwitch, AccordionSection
from .widgets.fleet_item import FleetItem, SelectedDroneCard, _drone_pixmap
from .widgets.formation_picker import FormationPicker
from .widgets.mission_log import MissionLog
from .widgets.cv_track_panel import CvTrackPanel
from .widgets.scan_dialog import ScanIpDialog, ConnectIpDialog
from .core.ip_scan import split_host_port
from .widgets.icons import geo_icon
from .widgets.help_dialog import HelpDialog
from .widgets.logo_dialog import LogoSettingsDialog
from .widgets.field_dialog import FieldTabletDialog
from .core import logo_store
from .widgets.takeoff_panel import TakeoffPanel
from .widgets.command_summary import CommandSummaryBox
from .widgets import confirm as confirm_dlg
from .widgets.preflight_dialog import (
    PreflightDialog, ChecklistDialog, ask_before_takeoff,
)
from .core import swarm_logic
from .core import waypoint_logic
from .core import preflight
from .core import flight_progress
from .core import mission_shadow
from .controllers.summary_presenter import SummaryPresenter
from .controllers.fleet_presenter import FleetPresenter
from .controllers.map_presenter import MapPresenter

FORMATION_NAMES = {0: "WEDGE ลิ่ม", 1: "LINE หน้ากระดาน", 2: "COLUMN แถวตอน",
                   3: "DIAMOND เพชร", 4: "ECHELON ทแยง"}
FORMATIONS = [("Wedge", "Wedge"), ("Line", "Line"), ("Column", "Column"),
              ("Diamond", "Diamond"), ("Echelon", "Echelon")]

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# probe เล็ก ๆ: เปิด QWebEngineView นอกจอ 1 ตัวแล้วปิด — ถ้า process จบด้วย code 0 = ใช้ได้
# SetErrorMode(0x0003)=SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX กัน Windows เด้ง
# crash dialog ("X แดง") เวลา QtWebEngine crash แบบ native บนเครื่องที่ TSF/CTF เสีย
_WEBENGINE_PROBE = (
    "import sys,os;"
    "os.name=='nt' and __import__('ctypes').windll.kernel32.SetErrorMode(0x0003);"
    "from PyQt5.QtWidgets import QApplication;"
    "from PyQt5.QtCore import Qt,QTimer,QUrl;"
    "QApplication.setAttribute(Qt.AA_ShareOpenGLContexts);"
    "a=QApplication([]);"
    "from PyQt5.QtWebEngineWidgets import QWebEngineView as W;"
    "v=W();v.move(-2000,-2000);v.resize(40,40);"
    "v.load(QUrl('about:blank'));v.show();"
    "QTimer.singleShot(800,a.quit);"
    "sys.exit(a.exec_())"
)

_webengine_cache = None


def webengine_available():
    """ตรวจว่า QtWebEngine (Chromium) ใช้ได้บนเครื่องนี้ไหม โดยรัน probe ใน subprocess"""
    global _webengine_cache
    if _webengine_cache is not None:
        return _webengine_cache
    if os.getenv("SWARMGOD_NO_MAP") == "1":
        _webengine_cache = False
        return False
    try:
        r = subprocess.run([sys.executable, "-c", _WEBENGINE_PROBE],
                           timeout=20, creationflags=_CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _webengine_cache = (r.returncode == 0)
    except Exception:
        _webengine_cache = False
    return _webengine_cache


class GroundStation(QMainWindow):
    _PF_WARN = "⚠ ยังไม่ได้ทดสอบก่อนบิน — กด SYSTEM TEST ที่เมนู PRE-FLIGHT TEST"
    cmd_result = pyqtSignal(str)
    swarm_update = pyqtSignal(object)
    event_recv = pyqtSignal(object)
    ui_call = pyqtSignal(object)

    def __init__(self, core_addr: str = "127.0.0.1:50051"):
        super().__init__()
        self.setWindowTitle("SwarmGod Cockpit")
        _icon = os.path.join(_ASSETS, "logo.ico")
        if os.path.exists(_icon):
            from PyQt5.QtGui import QIcon as _QIcon
            self.setWindowIcon(_QIcon(_icon))
        self.resize(1360, 820)
        # Lifecycle guard for queued QTimer/web callbacks.  closeEvent flips this
        # before tearing down QtWebEngine so a pending callback never touches a
        # destroyed-but-not-None QWebEngineView (native access-violation class).
        self._closing = False
        # 1.0 = ขนาดที่ออกแบบไว้ (ดู theme.BASE_BOOST) ผู้ใช้ปรับจากตรงนี้ได้
        self._font_scale = 1.0
        # sync ค่า global ของ theme.py ให้ตรงกับ instance นี้ทันที ก่อนสร้าง widget ไหน ๆ —
        # กันไม่ให้ค่าที่ค้างจาก instance อื่นในโปรเซสเดียวกัน (เช่นตอนรัน test หลายเคส) รั่วมาใช้
        theme.set_ui_scale(self._font_scale)
        self.setStyleSheet(build_stylesheet())

        self.client = CoreClient(core_addr)
        # GroundStation owns the transport client it creates. Tests may replace
        # self.client with a fake later, but the original channel still needs a
        # deterministic shutdown to avoid leaking native gRPC poll threads.
        self._owned_core_client = self.client
        # V3-S07: single observable command boundary. Provider pattern so tests
        # that replace self.client with a fake are still routed correctly; the
        # gateway never captures a CoreClient reference. Pass-through only —
        # no retry/dedup/reorder/policy; authority/safety stay in Go.
        self._gateway = CommandGateway(lambda: self.client)
        self.telem_thread = None
        self.fleet_items = {}        # drone_id -> FleetItem
        self._removed_ids = set()    # ลบแล้ว — อย่าให้ telemetry ใส่กลับ
        self._reserved_ids = set()   # จอง ID ไว้ตอนกด CONNECT (ก่อน telemetry มาถึง)
        self._endpoints = {}         # drone_id -> (host, port)
        self._connect_protocol = "tcp"       # ค่าเริ่มต้นของ popup CONNECT
        self._connect_endpoint = "127.0.0.1:5760"
        self._ip_store = IpStore()
        self._group_store = GroupStore(self._ip_store.path)
        self.group_of = self._group_store.get_all()  # drone_id -> group 1..6
        # ล้างกลุ่ม orphan ที่ไม่มี endpoint อยู่ในฝูงถาวรแล้ว
        try:
            known_ids = {int(saved.drone_id) for saved in self._ip_store.list_all()}
            for stale_id in [did for did in self.group_of if did not in known_ids]:
                self.group_of.pop(stale_id, None)
                self._group_store.clear_drone(stale_id)
        except Exception:
            pass
        self._last_seen = {}         # drone_id -> เวลาที่ได้ telemetry ล่าสุด (ใช้ตัดสิน online)
        self._last_telem_log = {}    # drone_id -> เวลาที่ log telemetry ล่าสุด (throttle เฉย ๆ)
        self._last_alt = {}
        self._last_telem = {}        # drone_id -> Telemetry (legacy compatibility / flight-business reads)
        # V1 Phase 3: frontend-only immutable telemetry read model.  Safety/Mission/
        # command decisions keep using Core/raw compatibility state until their own
        # migration phase; this store is presentation + observability only.
        self._telemetry_store = TelemetryStore()
        self._telemetry_render_gate = TelemetryRenderGate(min_interval_s=0.10)
        self._telemetry_shadow_mismatch_count = 0
        # เส้นทางที่ผ่านมาให้ Field Tablet: เก็บเป็นพิกัดล้วนและจำกัดจำนวนจุด
        # เพื่อให้เปิดเว็บกลางภารกิจแล้วเห็นเส้นทางก่อนหน้าได้ โดยไม่ส่งข้อมูลหนักเกิน LAN
        self._field_trails = {}       # drone_id -> [[lat, lon], ...]
        self._field_goto_targets = {} # drone_id -> {lat, lon}; เป้าหมาย GOTO ที่กำลังวิ่งหา
        self._selected_id = 0            # ลำ "หลัก" ที่โชว์ใน bottom card
        self._selected_ids = set()       # เลือกหลายลำได้ (Ctrl+Click / FLEET)
        self._drone_alt = {}             # drone_id -> ความสูง takeoff รายลำ
        self._drone_spacing = {}         # drone_id -> ระยะห่างรายลำ
        self._drone_names = {}           # drone_id -> ชื่อที่ผู้ใช้แก้ใน cockpit
        self._fleet_presenter = FleetPresenter()
        self._map_presenter = MapPresenter()
        self._target_mode = "selected"  # "selected" | "fleet"
        self._ui_mode = True             # True=UI control, False=REMOTE
        self._map_ready = False
        self.web = None
        self._sidebar_open = True
        self._sidebar_w = 376
        self._map_enabled = webengine_available()
        self._pixmap = _drone_pixmap()
        self._sections = []

        cache_dir = os.path.join(os.path.expanduser("~"), ".swarmgod", "tiles")
        if self._map_enabled:
            self.tiles = TileServer(_ASSETS, cache_dir)
            self.map_url = self.tiles.start()
        else:
            self.tiles = None
            self.map_url = ""

        # ── Field Tablet (docs/FIELD_TABLET.md เฟส 1 — ดูอย่างเดียว) ──
        # ค่าเริ่มต้นคือ **ไม่มี listener อยู่เลย** ไม่ใช่แค่ bind 127.0.0.1
        # ต้องกดเปิดที่ปุ่ม LAN บน topbar เท่านั้น ตัวเซิร์ฟเวอร์ถึงจะถูกสร้าง
        self.field_hub = TelemetryHub()
        self.field_sessions = SessionStore()
        self.field = None
        # เฟส 2: คำสั่งจากแท็บเล็ตเข้าทางนี้ทางเดียว — signal ข้าม thread ให้ Qt
        # จัดคิวเข้า main thread เอง (HTTP handler แตะ Qt object ตรง ๆ ไม่ได้)
        self.web_bridge = WebBridge()
        self.web_bridge.command.connect(self._on_web_command)
        self.web_bridge.control_changed.connect(self._field_control_changed)

        self._fence_mode = False
        self._leader_id = 0
        self._head_id = 0                 # หัวขบวน (Head/Leader) ระดับ UI — spec 1
        self._head_pinned = 0             # หัวที่ผู้ใช้เลือกเอง (กัน swarm poll ทับ)
        self._head_push_ts = 0.0
        self._cmd_summary = swarm_logic.CommandSummary()  # สรุปก่อนบิน — spec 6
        self._summ_batch = False          # True = รวบหลาย set/remove แล้วค่อย render ครั้งเดียว
        self._summary_presenter = SummaryPresenter(
            self._cmd_summary,
            request_render=self._render_summary,
            log_command=lambda message, severity: self._log(
                message, category="COMMAND", severity=severity),
            summary_box=lambda: getattr(self, "summary_box", None),
            record_render=lambda: (
                self._health.record_render() if hasattr(self, "_health") else None),
        )
        # PRE-FLIGHT SUMMARY V2: one frozen operation at a time.  Async callbacks
        # carry this run id and are ignored after Cancel / the next operation.
        self._flight_run = None
        self._flight_run_id = 0
        self._flight_takeoff_sent = set()
        self._flight_takeoff_targets = []
        # ── ทดสอบก่อนบินจริง (2 ปุ่ม: self-test + เช็คลิสต์ Codex) ──
        # เก็บในหน่วยความจำเท่านั้น: เปิดโปรแกรมใหม่ = ต้องเทสใหม่ (fail-closed)
        self._preflight = preflight.PreflightState()
        self._swarm_active = False
        self._flight_mode = None          # 'flight' | 'swarm' | 'rtl' (ป้ายบนแถบบน)
        self._nav_targets = {}            # drone_id -> (lat, lon) เป้าหมายที่สั่งไว้
        # ── แผนที่ 3D (สร้างตอนกดใช้ครั้งแรก ไม่กินทรัพยากรถ้าไม่ได้เปิด) ──
        self.web3d = None                 # บานที่ฝังแทนแผนที่เดิม
        self.web3d_win = None             # บานในหน้าต่างแยก (จอที่สอง)
        self.map3d_win = None
        self._map3d_ready = False
        self._map3d_win_ready = False
        # ปลดล็อก "คลิกแผนที่ = สั่งบิน" — ค่าเริ่มต้นล็อกไว้เสมอ (fail-safe)
        # ปลดล็อกได้ครั้งละ 1 คำสั่ง สั่งไปแล้วกลับมาล็อกเอง (ดู _set_goto_armed)
        self._goto_armed = False
        self._tac_icons = {}              # ชื่อ -> path ของสัญลักษณ์ที่อัปโหลด
        self._tac_tool = "none"
        self._home_pos = {}               # drone_id -> (lat, lon) จุดปล่อย (ใช้ตอน RTL)
        self._map_center = None           # จุดที่แผนที่กำลังโฟกัส (ไม่ใช่ค่า SITL โคราช)
        self._map_followed_gps = False    # กระโดดตาม GPS โดรนลำแรกแล้วหรือยัง
        self._rtl_active = False          # กัน RTL ซ้อนกัน
        self._rtl_pending = set()         # ลำที่ยังทำ RTL ไม่จบ
        self._rtl_phase = {}              # drone_id -> 'CLIMB'|'RETURN'|'LAND'

        # ── Waypoint Route Planning ──
        self._waypoint_mode = False
        self._wp_separate = False         # False = GROUPED (ไปพร้อมกัน) / True = SEPARATE (แยกลำ)
        self._waypoint_route = None       # GROUPED: เส้นทางเดียวร่วมกัน
        self._wp_routes = {}              # SEPARATE: drone_id -> WaypointRoute
        self._waypoint_executing = False
        self._wp_current_index = 0        # GROUPED: จุดปัจจุบันของเส้นทางร่วม
        self._wp_sep_index = {}           # SEPARATE: drone_id -> จุดปัจจุบันของลำนั้น
        self._wp_target_ids = []
        self._wp_arrived = set()          # drone_id ที่ถึงจุดปัจจุบันแล้ว (GROUPED)
        self._wp_key = 0                  # key ที่ใช้เรียก JS (0 = เส้นทางร่วม)
        # F6 restartable cockpit: these are display/cache mirrors of Core state,
        # never waypoint authority. Core state refresh owns their values while
        # _mission_core_authority is true.
        self._mission_core_authority = False
        self._mission_core_state = None
        self._mission_recovery_required = False
        self._mission_state_query_busy = False
        self._mission_state_last_query = 0.0
        self._mission_shadow_run_id = 0
        self._mission_shadow_operation_id = ""
        # ── WAIT execution state (spec §6) — cancellable ไม่ใช้ singleShot ยาว ──
        # timer เป็นแค่ตัวเรียกตรวจ state ไม่ใช่ business state เอง
        self._wp_waits = {}               # scope_key -> entry (0=GROUPED, drone_id=SEPARATE)
        self._wp_wait_generation = 0      # bump = invalidate WAIT ที่ค้างทั้งหมด
        self._wp_clock = time.monotonic   # inject ได้ตอนทดสอบ (ไม่ต้องรอเวลาจริง)
        self._wp_takeoff_pending = False  # รอ telemetry ยืนยันว่าพ้นพื้นก่อนยิง GOTO
        self._wp_takeoff_generation = 0   # ยกเลิก callback TAKEOFF/รอบตรวจที่ค้างอยู่
        self._wp_airborne_alt_m = 1.5     # ถือว่าพร้อมบิน route เมื่อ armed + สูง >= ค่านี้
        self._wp_takeoff_timeout_s = 90.0
        # เกณฑ์กันชนตอน Execute (โหมด SEPARATE)
        self._wp_alt_sep = 2.0            # ต่างระดับ ≤ นี้ = ถือว่าชั้นเดียวกัน (m)
        self._wp_min_dist = 6.0           # เส้นทางใกล้กว่านี้ = เสี่ยงชน (m)

        # ── WAVE: ใช้เฉพาะ Waypoint GROUPED เท่านั้น ──
        self._wave_enabled = False
        self._wave_executing = False
        self._wave_groups = []
        self._wave_group_index = -1
        self._wave_phase = "idle"        # idle|takeoff|route|return|waiting_land
        self._wave_generation = 0         # invalidates QTimer callbacks หลังยกเลิก
        self._wave_group_started_at = 0.0
        self._wave_timeout_s = 300.0
        self._wave_route_template = None
        self._wave_auto = False           # True = ยืนยันมาจากแท็บเล็ตแล้ว ห้ามเปิดกล่องบนคอกพิต
        self._wave_auto_next = False      # snapshot: กลุ่มถัดไป TAKEOFF อัตโนมัติ
        self._wave_payload_done = {}      # group -> [(label, waypoint index)] สำหรับ ACTIVE
        self._wave_payload_failed = {}    # group -> [(label, waypoint index)]

        self._collision_seen = {}         # (a,b) -> (เวลาเตือนล่าสุด, ระดับ)
        self._collision_crit = 3.0        # ใกล้กว่านี้ = เสี่ยงชน/ซ้อนทับ (m)
        self._collision_warn = 6.0        # ใกล้กว่านี้ = เตือน (m)
        self._rc_dir = None
        self._tip_at = {}                 # drone_id -> เวลาที่ push tooltip เข้าแผนที่ล่าสุด
        self._tip_txt = {}                # drone_id -> เนื้อหา tooltip ล่าสุด (กันส่งซ้ำ)
        self._servo_btn_mode = {}         # 'A'|'B' -> 'rc'|'ui'|'free' (กันทาสีปุ่มซ้ำ)
        self._servo_primed = set()        # (drone_id,'A'|'B') ที่อุ่นเครื่องเสร็จแล้ว
        self._servo_priming = {}          # (drone_id,'A'|'B') -> เวลาที่เริ่มอุ่นเครื่อง
        self._servo_state = {}            # drone_id -> 'A'|'B' (ป้ายสถานะ servo ล่าสุด)
        # (drone_id, 'A'|'B') -> (เจ้าของที่คาดไว้, deadline) — ไฟปุ่มติดทันทีที่กด
        # ไม่ต้องรอ telemetry รอบถัดไปมายืนยัน (ดู _servo_owner)
        self._servo_pending = {}
        self._landing_seen = set()        # ลำที่เห็นสถานะ LANDING แล้ว (รอแตะพื้น)
        self._land_guided_done = set()    # ลำที่ตั้ง GUIDED หลังลงจอดไปแล้ว (กันยิงซ้ำ)
        self._rc_order = []               # คิวลำดับเคลื่อนที่กันชน (spec 7)
        self._rc_order_dir = None
        self._build_ui(core_addr)
        self._install_font_shortcuts()
        self._load_saved_ips()
        self._autoload_settings()
        self._refresh_preflight_ui()
        if not self._map_enabled:
            self._log("MAP DISABLED — QtWebEngine ใช้ไม่ได้บนเครื่องนี้; ส่วนอื่นทำงานปกติ",
                      severity="WARNING", category="ALERT")

        self.cmd_result.connect(self._on_cmd_result)
        self.swarm_update.connect(self._on_swarm_update)
        self.event_recv.connect(self._on_event)
        self.ui_call.connect(lambda fn: fn())

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)

        self._swarm_timer = QTimer(self)
        self._swarm_timer.timeout.connect(self._poll_swarm)
        self._swarm_timer.start(1200)

        self._rc_timer = QTimer(self)
        self._rc_timer.setInterval(150)
        self._rc_timer.timeout.connect(self._rc_tick)

        # deadman ของการบังคับสดจากแท็บเล็ต (docs/FIELD_TABLET_V2.md §0.1)
        # แท็บเล็ตต้องส่ง move ซ้ำระหว่างกดค้าง ขาดไปเกิน TTL = หยุดเอง
        # เพราะ WiFi หลุดกลางคันแล้ว _rc_timer จะยิง rc_move ต่อไปเรื่อย ๆ
        # โดยไม่มีใครสั่ง — นั่นคืออันตรายจริงที่สเปกเดิมกลัว
        self._web_move_timer = QTimer(self)
        self._web_move_timer.setSingleShot(True)
        self._web_move_timer.setInterval(self.WEB_MOVE_TTL_MS)
        self._web_move_timer.timeout.connect(self._web_move_deadman)

        self._wave_timer = QTimer(self)
        self._wave_timer.setInterval(1000)
        self._wave_timer.timeout.connect(self._wave_tick)

        # WAIT poll — ตรวจ deadline/safety ทุก 500ms (ตัวเรียกตรวจ ไม่ใช่ authority)
        self._wp_wait_timer = QTimer(self)
        self._wp_wait_timer.setInterval(500)
        self._wp_wait_timer.timeout.connect(self._wp_wait_tick)

        self._ping = PingService(self)
        self._ping.result.connect(self._on_ping_result)
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._auto_ping_selected)
        self._ping_timer.start(5000)

        # Auto-Reassign Head — เช็คทุก 2 วิว่าหัวยัง online ไหม (spec 1)
        self._head_timer = QTimer(self)
        self._head_timer.timeout.connect(self._head_watchdog)
        self._head_timer.start(2000)

        # Collision detection — ตรวจระยะห่างทุกคู่แบบ real-time
        self._collision_timer = QTimer(self)
        self._collision_timer.timeout.connect(self._collision_watchdog)
        self._collision_timer.start(700)

        # Connectivity — ลำไหน telemetry ขาด → ขึ้น OFFLINE + อัปเดตตัวนับ ONLINE ให้ตรงจริง
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._conn_watchdog)
        self._conn_timer.start(2000)

        # แผนที่ 3D — ส่งตำแหน่งทั้งฝูงรวดเดียวที่ 10 Hz
        # (ไม่ยิงจาก _on_telemetry ทุกแพ็กเก็ต เพราะ 5 ลำ = 50 ครั้ง/วิ ข้าม process)
        # timer เดินตลอดแต่ _push_map3d คืนทันทีถ้ายังไม่ได้เปิด 3D
        self._map3d_timer = QTimer(self)
        self._map3d_timer.timeout.connect(self._push_map3d)
        self._map3d_timer.timeout.connect(self._push_map3d_view)
        self._map3d_timer.start(100)

        # UI Watchdog / observability (observe-only) — Phase 1 architecture migration
        # heartbeat เกาะ _tick_clock (1s) อยู่แล้ว จึงไม่เพิ่ม timer ใหม่ = lightweight
        # ถ้า event loop ค้าง _tick_clock จะมาช้า → HealthMonitor จับ stall ได้
        # LOG อย่างเดียว: ห้าม restart/RTL/HOLD/failsafe (monitor ไม่มี client)
        self._health = HealthMonitor(on_stall=self._on_ui_stall)
        self._health_log_every = 30       # log snapshot ทุก ~30 heartbeat (≈30s)
        self._health_ticks = 0

        self._start_stream()

        # F6: on an authority-enabled launch, query the existing Core run once.
        # Live refresh is telemetry-throttled below instead of using another Qt
        # timer, avoiding a timer that can outlive a closing/restarting cockpit.
        if mission_shadow.authority_requested():
            QTimer.singleShot(100, lambda: mission_shadow.recover(self))

        # อุ่นเครื่อง native dialog + gRPC channel ให้เสร็จก่อนผู้ใช้กดปุ่มแรก
        # (ดู _prewarm_ui_paths — แก้อาการ "กด A/B ครั้งแรกแล้วค้างเกือบ 1 วิ")
        QTimer.singleShot(250, self._prewarm_ui_paths)

        auto_n = os.getenv("SWARMGOD_AUTOCONNECT_N")
        auto = os.getenv("SWARMGOD_AUTOCONNECT")
        if auto_n:
            try:
                n = max(1, int(auto_n))
            except ValueError:
                n = 1
            QTimer.singleShot(700, lambda: self._autoconnect_n(n))
        elif auto:
            self._connect_endpoint = auto
            QTimer.singleShot(
                600,
                lambda: self._connect_from_dialog(
                    *split_host_port(self._connect_endpoint, 5760), self._connect_protocol))

        if os.getenv("SWARMGOD_DEMO") == "takeoff":
            QTimer.singleShot(13000, self._cmd_takeoff)
            QTimer.singleShot(27000, lambda: self._on_map_click(14.9585695, 102.0986187))
        if os.getenv("SWARMGOD_DEMO") == "swarm":
            QTimer.singleShot(500, self._demo_connect_all)
            QTimer.singleShot(10000, self._demo_takeoff_parallel)
            QTimer.singleShot(34000, self._swarm_start)

        shot = os.getenv("SWARMGOD_SHOT")
        if shot:
            delay = int(os.getenv("SWARMGOD_SHOT_DELAY", "14000"))
            QTimer.singleShot(delay, lambda: self._save_shot(shot))

    def _prewarm_ui_paths(self):
        """จ่ายต้นทุน "ครั้งแรก" ให้เสร็จตอนเปิดโปรแกรม แทนที่จะไปตกที่ปุ่มคำสั่งแรก

        วัดบนเครื่องจริง (platform=windows) — ทุกอย่างนี้เป็นต้นทุนครั้งเดียวต่อ process:

          กล่องยืนยันใบแรก (QMessageBox + CSS + HWND)  ~825 ms → ใบต่อไป 3-4 ms
          RPC unary ครั้งแรกบน channel (TCP+mTLS)        ~6 ms → ครั้งต่อไป 0.3 ms

        อาการที่ผู้ใช้เจอคือ "กด A/B ครั้งแรกหลังเปิดโปรแกรมแล้วค้างเกือบวินาที
        แต่ครั้งต่อ ๆ ไปกดติดทันที" — ตัวการคือกล่องยืนยัน ไม่ใช่ SQLite/gRPC/เซอร์โว
        """
        try:
            confirm_dlg.prewarm(self)
        except Exception as e:
            self._log(f"prewarm dialog ล้มเหลว (ไม่กระทบการใช้งาน): {e}",
                      severity="WARNING")

        # Do not prewarm the gRPC channel with channel_ready_future().  A timed-out
        # ready future keeps a native connectivity-poll thread alive until it is
        # explicitly cancelled; repeated cockpit lifecycles can then race channel
        # shutdown (and previously caused Windows access violations in the full
        # frontend suite).  The measured first-unary cost is only a few ms, while
        # the dialog prewarm above saves nearly a second, so deterministic channel
        # lifecycle is the better trade-off here.

    def _save_shot(self, path):
        from PyQt5.QtWidgets import QApplication
        pix = QApplication.primaryScreen().grabWindow(int(self.winId()))
        pix.save(path)
        self._log(f"screenshot saved: {path}")
        QTimer.singleShot(300, QApplication.quit)

    # ══════════════════════════════════════════════════════════
    #  UI ASSEMBLY
    # ══════════════════════════════════════════════════════════
    def _build_ui(self, core_addr):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar(core_addr))

        self.banner = QLabel("")
        self.banner.setFixedHeight(26)
        # เว้นพื้นที่ไว้ตลอด (visible เสมอ แค่ไม่มีข้อความ/พื้นหลังโปร่งใสตอนไม่มีแจ้งเตือน)
        # กัน layout ทั้งแถบขยับตอน banner โผล่/หาย ซึ่งทำให้แผนที่ (QWebEngineView) วาบขาวตอน resize
        self.banner.setVisible(True)
        root.addWidget(self.banner)
        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(lambda: self._hide_banner(restore_preflight=True))
        self._hide_banner()
        self._pf_warn_on = False

        # main body (left | center+log | right)
        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)

        body.addWidget(self._build_left())

        center = QVBoxLayout()
        center.setSpacing(10)
        center.addWidget(self._build_map(), 1)
        # แถวล่าง: MISSION LOG (ซ้าย กว้าง) + PRE-FLIGHT SUMMARY (ขวา แคบ) — spec 6
        lower = QHBoxLayout()
        lower.setSpacing(10)
        self.mlog = MissionLog()
        self.mlog.setMinimumHeight(260)
        self.mlog.setMaximumHeight(360)
        lower.addWidget(self.mlog, 3)
        self.summary_box = CommandSummaryBox()
        self.summary_box.setMinimumWidth(240)
        self.summary_box.setMaximumWidth(320)
        self.summary_box.cleared.connect(self._clear_summary)
        lower.addWidget(self.summary_box, 1)
        center.addLayout(lower, 0)
        cwrap = QWidget()
        cwrap.setLayout(center)
        body.addWidget(cwrap, 1)
        self._render_summary()

        body.addWidget(self._build_commands())

        root.addLayout(body, 1)
        root.addWidget(self._build_statusbar())
        self._build_float_toast(central)

    # ── top bar ──
    def _build_topbar(self, core_addr):
        bar = QFrame()
        bar.setFixedHeight(54)
        bar.setStyleSheet(
            f"background:{T('panel')}; border-bottom:1px solid {rgba('#ffffff', 0.05)};")
        self._topbar = bar
        self._build_mode_drop(bar)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 14, 0)
        h.setSpacing(9)

        self.btn_burger = QPushButton("☰")
        self.btn_burger.setFixedSize(38, 34)
        self.btn_burger.setCursor(Qt.PointingHandCursor)
        self.btn_burger.setStyleSheet(ghost_btn(radius=10, font=16))
        self.btn_burger.clicked.connect(self._toggle_sidebar)
        h.addWidget(self.btn_burger)

        # แถบโลโก้ — รูปเรียงซ้าย→ขวา เพิ่มได้สูงสุด 5 รูป (ตั้งค่าที่เมนู ⚙ → รูปไอคอน)
        self.logo_row = QHBoxLayout()
        self.logo_row.setSpacing(6)
        self.logo_row.setContentsMargins(0, 0, 0, 0)
        h.addLayout(self.logo_row)
        self._refresh_logos()

        brand = QLabel("◆ SwarmGod")
        brand.setStyleSheet(
            f"color:{T('text')}; font-weight:800; font-size:16px; letter-spacing:0.5px;")
        h.addWidget(brand)
        sub = QLabel("Cockpit")
        sub.setStyleSheet(f"color:{T('faint')}; font-size:12px;")
        h.addWidget(sub)

        h.addSpacing(6)
        self.pill_core = self._pill("● CORE", T("green"), compact=True)
        h.addWidget(self.pill_core)
        self.pill_link = self._pill("LINK --", T("dim"), compact=True)
        h.addWidget(self.pill_link)
        # PREFLIGHT อยู่ข้าง ● ONLINE ในแผง FLEET — ไม่ใส่ตรงนี้
        # กันแถบซ้ายของ topbar ยื่นยาวจนป้ายโหมดกลางไม่สมดุล
        # ปุ่ม LAN อยู่หัวแผง COMMANDS · โหมดการบินเป็นป้ายกลาง (ดู _build_mode_drop)

        # หมายเหตุ: ปุ่ม CANCEL NAV ย้ายไปอยู่ในหมวด FLIGHT (แผงขวา) แล้ว
        # จัดกลุ่มกับคำสั่งการบินอื่น ๆ และเปลี่ยนเป็นสีแดง

        # แถบสถานะโหมด — จากป้ายโหมด (✈ FLIGHT) ไปจนถึงก่อนคำว่า CONTROL เปลี่ยนสีตาม
        # โหมดการบินปัจจุบัน (ดู _set_flight_mode_badge) โทนจาง/หม่นกว่า banner แจ้งเตือน
        # ตั้งใจ เพื่อไม่ให้สีซ้ำ/แย่งความสำคัญกับ banner ที่เป็นเรื่องด่วนกว่า
        self._mode_strip = QFrame()
        self._mode_strip.setStyleSheet(
            f"background:transparent; border:none;"
            f" border-bottom:3px solid {rgba(T('accent'), 0.35)};")
        h.addWidget(self._mode_strip, 1)

        # UI / REMOTE — แคปซูลสวิตช์
        cs_lab = QLabel("CONTROL")
        cs_lab.setStyleSheet(f"color:{T('faint')}; font-size:10px; letter-spacing:1.2px; font-weight:700;")
        h.addWidget(cs_lab)
        self.sw_control = CapsuleSwitch(
            "UI", "REMOTE", selected=0,
            left_accent=T("accent"), right_accent=T("amber"), height=28)
        self.sw_control.changed.connect(self._on_control_changed)
        h.addWidget(self.sw_control)

        self.btn_help = QPushButton("?")
        self.btn_help.setFixedSize(34, 34)
        self.btn_help.setCursor(Qt.PointingHandCursor)
        self.btn_help.setToolTip("คู่มือการใช้งาน — วิธีใช้แต่ละฟีเจอร์ + ความสัมพันธ์ระหว่างปุ่มต่าง ๆ")
        # tinted (ไม่ใช่ ghost แบบปุ่มอื่น ๆ ในแถบนี้) ให้เด่นชัดว่าเป็นปุ่มช่วยเหลือ กดได้
        self.btn_help.setStyleSheet(tinted_btn(T("accent"), radius=17, font=15, weight=800))
        self.btn_help.clicked.connect(self._show_help)
        h.addWidget(self.btn_help)

        self.btn_gear = QPushButton("⚙")
        self.btn_gear.setFixedSize(38, 34)
        self.btn_gear.setCursor(Qt.PointingHandCursor)
        self.btn_gear.setStyleSheet(ghost_btn(radius=10, font=15))
        self._build_gear_menu()
        h.addWidget(self.btn_gear)

        self.lbl_clock = QLabel("--:--:--")
        self.lbl_clock.setStyleSheet(
            f"color:{T('text')}; font-size:13px; font-family:{FONT_MONO}; letter-spacing:1px;")
        h.addWidget(self.lbl_clock)
        return bar

    def _show_help(self):
        """เปิด modal คู่มือการใช้งาน + แผนที่ความสัมพันธ์ระหว่างฟังก์ชัน"""
        HelpDialog(self).exec_()

    def _build_gear_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{T('panel2')}; color:{T('text')}; border:1px solid {rgba('#ffffff', 0.1)};"
            f" border-radius:8px; padding:6px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:6px; }}"
            f"QMenu::item:selected {{ background:{rgba(T('accent'), 0.25)}; }}")
        menu.addAction("Save Settings", self._save_settings)
        menu.addAction("Export Settings…", self._export_settings)
        menu.addAction("Load Settings…", self._load_settings_dialog)
        menu.addAction("Load Saved Settings", self._load_saved_settings)
        menu.addSeparator()
        # ── ขนาดฟอนต์ทั้งแอป (spec: ปุ่มขยายฟอนต์ให้ใหญ่ขึ้นทั้งหมด) ──
        font_menu = menu.addMenu("ขนาดฟอนต์ (Font Size)")
        font_menu.addAction("ขยายฟอนต์  A+   (Ctrl +)", lambda: self._bump_font(+0.1))
        font_menu.addAction("ลดฟอนต์  A−   (Ctrl −)", lambda: self._bump_font(-0.1))
        font_menu.addAction("ตัวหนังสือใหญ่ขึ้น  (115%)", lambda: self._set_font_scale(1.15))
        font_menu.addAction("ตัวหนังสือใหญ่มาก  (130%)", lambda: self._set_font_scale(1.3))
        font_menu.addAction("ขนาดมาตรฐาน  (100%)", lambda: self._set_font_scale(1.0))
        menu.addSeparator()
        menu.addAction("รูปไอคอน Top bar…", self._open_logo_settings)
        menu.addAction("? Help / คู่มือการใช้งาน", self._show_help)
        menu.addAction("Toggle Fleet Panel", self._toggle_sidebar)
        menu.addAction("Clear Log", lambda: self.mlog.clear())
        menu.addSeparator()
        menu.addAction("About SwarmGod", lambda: self._log(
            "SwarmGod Cockpit · hybrid GCS (Go core + Python UI)"))
        self.btn_gear.setMenu(menu)

    def _pill(self, text, color, compact=False):
        lb = QLabel(text)
        pad_y, pad_x, font, radius = (2, 6, 9, 6) if compact else (4, 10, 11, 8)
        lb.setStyleSheet(
            f"color:{color}; background:{rgba(color, 0.14)}; border-radius:{radius}px;"
            f" padding:{pad_y}px {pad_x}px; font-size:{font}px; font-weight:700;")
        if compact:
            lb.setFixedHeight(20)
            lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        return lb

    def _build_float_toast(self, host: QWidget):
        """การ์ดแจ้งเตือนลอยมุมล่างขวา ทับหน้าจอ"""
        self.toast = QFrame(host)
        self.toast.setObjectName("FloatToast")
        self.toast.setVisible(False)
        self.toast.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self.toast)
        lay.setContentsMargins(14, 10, 16, 10)
        lay.setSpacing(10)
        self.toast_icon = QLabel("i")
        self.toast_icon.setFixedSize(22, 22)
        self.toast_icon.setAlignment(Qt.AlignCenter)
        self.toast_msg = QLabel("")
        self.toast_msg.setWordWrap(True)
        self.toast_msg.setMaximumWidth(320)
        self.toast_msg.setStyleSheet(
            f"color:{T('text')}; font-size:13px; font-weight:600; background:transparent;")
        lay.addWidget(self.toast_icon, 0, Qt.AlignTop)
        lay.addWidget(self.toast_msg, 1)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        self.toast.raise_()

    def _position_toast(self):
        if not hasattr(self, "toast") or self.toast is None:
            return
        host = self.toast.parentWidget()
        if host is None:
            return
        self.toast.adjustSize()
        margin = 18
        x = host.width() - self.toast.width() - margin
        y = host.height() - self.toast.height() - margin - 28  # เหนือ statusbar นิด
        self.toast.move(max(margin, x), max(margin, y))
        self.toast.raise_()

    # ── left column (fleet + selected) ──
    def _build_left(self):
        self.left_col = QFrame()
        self.left_col.setObjectName("LeftCol")
        self.left_col.setStyleSheet(card_qss("#LeftCol", radius=10))
        self.left_col.setMinimumWidth(0)
        self.left_col.setMaximumWidth(self._sidebar_w)
        self.left_col.setFixedWidth(self._sidebar_w)

        v = QVBoxLayout(self.left_col)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        # header
        hd = QHBoxLayout()
        title = QLabel("FLEET")
        title.setStyleSheet(section_label_qss() + f" color:{T('dim')};")
        hd.addWidget(title)
        hd.addStretch()
        # PREFLIGHT คู่กับ ONLINE ในหัวแผง FLEET — ไม่ขึ้น topbar เพื่อไม่ดันแถบโหมดกลาง
        self.btn_preflight = QPushButton("⚠ PREFLIGHT")
        self.btn_preflight.setCursor(Qt.PointingHandCursor)
        self.btn_preflight.setFixedHeight(20)
        self.btn_preflight.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btn_preflight.setToolTip("ทดสอบก่อนบิน — กดเพื่อไปที่หมวด PRE-FLIGHT")
        self.btn_preflight.clicked.connect(self._focus_preflight_section)
        hd.addWidget(self.btn_preflight)
        self.lbl_count = QLabel("● 0 ONLINE")
        self._set_online_count(0)
        hd.addWidget(self.lbl_count)
        v.addLayout(hd)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_scan = QPushButton("SCAN")
        self.btn_scan.setStyleSheet(tinted_btn(T("accent"), radius=6, font=11))
        self.btn_scan.setFixedHeight(32)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.setToolTip("Scan the local network for drones")
        self.btn_scan.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_scan.clicked.connect(self._open_ip_scan)
        self.btn_conn = QPushButton("CONNECT")
        self.btn_conn.setStyleSheet(tinted_btn(T("green"), radius=6, font=11))
        self.btn_conn.setFixedHeight(32)
        self.btn_conn.setCursor(Qt.PointingHandCursor)
        self.btn_conn.setToolTip("Open direct connection")
        self.btn_conn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_conn.clicked.connect(self._on_connect_clicked)
        btn_row.addWidget(self.btn_scan, 1)
        btn_row.addWidget(self.btn_conn, 1)
        v.addLayout(btn_row)

        # กลุ่ม = selection preset เท่านั้น; ไม่มีการส่งคำสั่งบินจากแถวนี้
        v.addWidget(self._build_group_bar())

        # หมายเหตุ: เมนู Save/Export/Load ย้ายไปอยู่หัวแผงขวา (COMMANDS)
        # เพื่อเคลียร์พื้นที่ฝั่งซ้ายให้การ์ดโดรน — ดู _build_cfg_button()

        # fleet list — โชว์ครบถึง 5 ลำ เกินค่อยเลื่อน
        self._fleet_item_h = 64
        self._fleet_gap = 6
        self._fleet_visible_max = 5

        self.fleet_holder = QWidget()
        self.fleet_holder.setStyleSheet("background:transparent;")
        self.fleet_area = QVBoxLayout(self.fleet_holder)
        self.fleet_area.setContentsMargins(0, 0, 0, 0)
        self.fleet_area.setSpacing(self._fleet_gap)
        self.fleet_area.setAlignment(Qt.AlignTop)

        self.fleet_scroll = QScrollArea()
        self.fleet_scroll.setWidget(self.fleet_holder)
        self.fleet_scroll.setWidgetResizable(False)
        self.fleet_scroll.setFrameShape(QFrame.NoFrame)
        self.fleet_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.fleet_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.fleet_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.fleet_scroll.setStyleSheet(
            "QScrollArea { border:none; background:transparent; }"
            f"QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}"
            f"QScrollBar::handle:vertical {{ background:{rgba('#ffffff', 0.18)};"
            f" border-radius:3px; min-height:24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}")
        self._update_fleet_scroll_height()
        v.addWidget(self.fleet_scroll, 0)

        # selected drone card — กินพื้นที่ที่เหลือทั้งหมด + เลื่อนได้ถ้าจอเตี้ย
        sel_hd = QLabel("SELECTED DRONE")
        sel_hd.setStyleSheet(section_label_qss() + f" color:{T('dim')};")
        v.addWidget(sel_hd)
        self.sel_card = SelectedDroneCard(self._pixmap)
        self.sel_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self.sel_scroll = QScrollArea()
        self.sel_scroll.setWidget(self.sel_card)
        self.sel_scroll.setWidgetResizable(True)
        self.sel_scroll.setFrameShape(QFrame.NoFrame)
        self.sel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sel_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.sel_scroll.setMinimumHeight(220)
        self.sel_card.setMaximumWidth(max(200, self._sidebar_w - 28))
        self.sel_scroll.setStyleSheet(
            "QScrollArea { border:none; background:transparent; }"
            f"QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}"
            f"QScrollBar::handle:vertical {{ background:{rgba('#ffffff', 0.18)};"
            f" border-radius:3px; min-height:24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}")

        self.sel_card.rtl_req.connect(self._card_rtl)
        self.sel_card.land_req.connect(self._card_land)
        self.sel_card.hold_req.connect(self._card_hold)
        self.sel_card.color_changed.connect(self._on_drone_color)
        self.sel_card.ping_req.connect(self._ping_drone)
        self.sel_card.connect_req.connect(self._card_connect)
        self.sel_card.disconnect_req.connect(self._card_disconnect)
        self.sel_card.delete_req.connect(self._card_delete)
        self.sel_card.apply_ip_req.connect(self._card_apply_ip)
        self.sel_card.head_req.connect(self._on_head_req)          # spec 1
        self.sel_card.estop_req.connect(self._on_estop_drone)      # spec 4
        # Quick actions รายลำ + พารามิเตอร์รายลำ (spec 3)
        self.sel_card.arm_req.connect(self._card_arm)
        self.sel_card.disarm_req.connect(self._card_disarm)
        self.sel_card.alt_changed.connect(self._on_card_alt_changed)
        self.sel_card.spacing_changed.connect(self._on_card_spacing_changed)
        self.sel_card.rename_req.connect(self._on_drone_renamed)
        v.addWidget(self.sel_scroll, 1)
        return self.left_col

    def _build_group_bar(self):
        """แถว preset กลุ่ม 1..6 — เลือกได้อย่างเดียว ไม่ส่งคำสั่งบิน"""
        scroll = QScrollArea()
        scroll.setObjectName("GroupBar")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(38)
        scroll.setMinimumWidth(0)
        scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        scroll.setStyleSheet(
            "#GroupBar { background:transparent; border:none; }"
            f"QScrollBar:horizontal {{ height:4px; background:transparent; }}"
            f"QScrollBar::handle:horizontal {{ background:{rgba('#ffffff', 0.18)};"
            f" border-radius:2px; min-width:20px; }}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }")

        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 2, 0, 4)
        row.setSpacing(4)
        label = QLabel("GROUP")
        label.setFixedWidth(38)
        label.setStyleSheet(f"color:{T('dim')}; font-size:10px; font-weight:700;")
        row.addWidget(label)

        self.btn_group_all = QPushButton("ALL")
        self.btn_group_all.setFixedSize(54, 26)
        self.btn_group_all.setCursor(Qt.PointingHandCursor)
        self.btn_group_all.setStyleSheet(tinted_btn(T("accent"), radius=8, font=10))
        self.btn_group_all.clicked.connect(lambda: self._on_fleet_toggled(True))
        row.addWidget(self.btn_group_all)

        self.group_chips = {}
        for group in range(1, 7):
            chip = QPushButton(str(group))
            # tinted_btn มี padding ซ้าย/ขวารวม 20px ซึ่งเคยบีบข้อความในปุ่ม 30px
            # จนเลขกลุ่ม/จำนวนสมาชิกถูกตัดหาย ใช้ QSS สำหรับชิปโดยเฉพาะแทน
            chip.setFixedSize(40, 26)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(
                f"Click: select Group {group} · Ctrl+Click: add selection · "
                f"Right-click: assign selected drones to Group {group}")
            color = drone_color(group)
            chip.setStyleSheet(
                f"QPushButton {{ background:{rgba(color, 0.13)};"
                f" border:1px solid {rgba(color, 0.48)}; border-radius:8px;"
                f" color:{color}; font-size:12px; font-weight:800; padding:0; }}"
                f"QPushButton:hover {{ background:{rgba(color, 0.25)};"
                f" border:1px solid {rgba(color, 0.8)}; }}"
                f"QPushButton:pressed {{ background:{rgba(color, 0.36)}; }}"
                f"QPushButton:disabled {{ background:{rgba('#ffffff', 0.03)};"
                f" color:{T('faint')}; border:1px solid {rgba('#ffffff', 0.08)}; }}")
            chip.clicked.connect(
                lambda _=False, g=group: self._select_group(
                    g, bool(QApplication.keyboardModifiers() & Qt.ControlModifier)))
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, g=group, b=chip: self._group_chip_menu(g, b, pos))
            self.group_chips[group] = chip
            row.addWidget(chip)
        row.addStretch(1)
        holder.setMinimumWidth(362)
        scroll.setWidget(holder)
        self._refresh_group_ui()
        return scroll

    # ── center map ──
    def _build_map(self):
        wrap = QFrame()
        wrap.setObjectName("MapWrap")
        wrap.setStyleSheet(card_qss("#MapWrap", radius=10))
        # QWebEngine/ข้อความ telemetry ต้องไม่เพิ่ม minimumSizeHint ของหน้าต่างภายหลัง
        # ไม่เช่นนั้นเมื่อ 3D พร้อมหรือพิกัดจริงยาวขึ้น Qt จะดันแผงขวาตกขอบจอ
        wrap.setMinimumWidth(0)
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        if not self._map_enabled:
            lay.addWidget(self._build_map_placeholder(), 1)
        else:
            self.web = QWebEngineView()
            self.bridge = MapBridge()
            self.bridge.map_click.connect(self._on_map_click)
            self.bridge.target_reached.connect(self._on_target_reached)
            self.bridge.mouse_move.connect(self._on_mouse_coord)
            self.bridge.mouse_out.connect(self._on_mouse_out)
            self.bridge.waypoint_click.connect(self._on_waypoint_click)
            self.bridge.waypoint_context.connect(self._wp_action_menu)
            self.bridge.goto_arm.connect(self._on_goto_arm)
            self.bridge.drone_select.connect(self._on_map_drone_selected)
            self.channel = QWebChannel()
            self.channel.registerObject("bridge", self.bridge)
            self.web.page().setWebChannel(self.channel)
            self.web.loadFinished.connect(self._on_map_loaded)
            self.web.load(QUrl(self.map_url + "/map.html"))
            self.web.setStyleSheet("border-radius:12px;")
            self.web.setMinimumSize(0, 0)
            self.web.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

            # แผนที่ 2D กับ 3D ซ้อนกันใน stack เดียว — สลับแล้วแผนที่เดิมไม่ถูกทำลาย
            # (geofence/เส้นทาง/จุดที่วาดไว้ยังอยู่ครบเมื่อสลับกลับมา)
            self.map_stack = QStackedWidget()
            self.map_stack.setMinimumSize(0, 0)
            self.map_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self.map_stack.addWidget(self.web)          # index 0 = 2D
            lay.addWidget(self.map_stack, 1)

        # ── แถบพิกัดใต้แผนที่ (อยู่นอกกรอบแผนที่ ไม่ทับตัวแผนที่) ──
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.coord = QLabel("LAT --  ·  LNG --  ·  ALT --  ·  GND SPD --")
        self.coord.setToolTip("พิกัดและสถานะของโดรนลำที่เลือก")
        self.coord.setStyleSheet(
            f"color:{T('dim')}; background:{rgba('#000000', 0.35)}; border-radius:9px;"
            f" padding:6px 12px; font-size:11px; font-family:{FONT_MONO};")
        self.coord.setMinimumWidth(0)
        self.coord.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        strip.addWidget(self.coord, 1)

        # พิกัดตามเมาส์แบบ real-time (อัปเดตจาก map.html ผ่าน MapBridge)
        self.coord_mouse = QLabel("CURSOR  LAT --.------  LNG --.------")
        self.coord_mouse.setToolTip("พิกัดตำแหน่งเมาส์บนแผนที่ (อัปเดตสด)")
        self._coord_mouse_qss = (
            f"color:{{col}}; background:{rgba(T('cyan'), 0.10)};"
            f" border:1px solid {rgba(T('cyan'), 0.28)}; border-radius:9px;"
            f" padding:6px 12px; font-size:11px; font-family:{FONT_MONO};")
        self.coord_mouse.setStyleSheet(self._coord_mouse_qss.format(col=T("faint")))
        self.coord_mouse.setMinimumWidth(0)
        self.coord_mouse.setMaximumWidth(280)
        self.coord_mouse.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        strip.addWidget(self.coord_mouse, 1)

        # ปุ่มแผนที่ 3D — อยู่ในแถบเดียวกับพิกัด ทรงเดียวกัน ไม่แย่งพื้นที่แผนที่
        if self._map_enabled:
            for w in self._build_map3d_buttons():
                strip.addWidget(w, 0)

        lay.addLayout(strip)
        return wrap

    def _map3d_chip_qss(self, color, on=False):
        """ชิปเล็กทรงเดียวกับแถบพิกัด — ต่างแค่สีตอนเปิดใช้งาน"""
        bg = rgba(color, 0.22 if on else 0.10)
        bd = rgba(color, 0.55 if on else 0.26)
        fg = color if on else T("faint")
        return (f"QPushButton {{ color:{fg}; background:{bg};"
                f" border:1px solid {bd}; border-radius:9px;"
                f" padding:6px 12px; font-size:11px; font-weight:700;"
                f" font-family:{FONT_MONO}; }}"
                f"QPushButton:hover {{ border-color:{rgba(color, 0.7)}; color:{color}; }}")

    def _on_mouse_coord(self, lat, lon):
        """เมาส์ขยับบนแผนที่ → อัปเดตพิกัดที่แถบใต้แผนที่ (real-time)"""
        text = f"CURSOR  LAT {lat:.6f}  LNG {lon:.6f}"
        self.coord_mouse.setText(text)
        self.coord_mouse.setToolTip(text)
        self.coord_mouse.setStyleSheet(self._coord_mouse_qss.format(col=T("cyan")))

    def _on_mouse_out(self):
        self.coord_mouse.setText("CURSOR  LAT --.------  LNG --.------")
        self.coord_mouse.setToolTip("พิกัดตำแหน่งเมาส์บนแผนที่ (อัปเดตสด)")
        self.coord_mouse.setStyleSheet(self._coord_mouse_qss.format(col=T("faint")))

    def _build_map_placeholder(self):
        box = QFrame()
        box.setStyleSheet(f"background:{T('panel2')}; border-radius:12px;")
        v = QVBoxLayout(box)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(8)
        title = QLabel("🗺  MAP")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{T('faint')}; font-size:22px; font-weight:700; letter-spacing:3px;")
        v.addWidget(title)
        why = QLabel("QtWebEngine ใช้ไม่ได้บนเครื่องนี้ · ส่วนอื่นทำงานปกติ")
        why.setAlignment(Qt.AlignCenter)
        why.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(why)
        return box

    def _on_map_loaded(self, ok):
        self._map_ready = ok
        self._log("map loaded" if ok else "map load failed")
        if ok and self._map_center:
            lat, lon = self._map_center
            self._jump_map_to(lat, lon, set_gcs=True)

    def _js(self, code):
        if getattr(self, "_closing", False):
            return
        if self._map_ready and self.web is not None:
            self.web.page().runJavaScript(code)

    # ══════════════════════════════════════════════════════════
    #  แผนที่ 3D — 2 ทาง: สลับแทนแผนที่เดิม / เปิดหน้าต่างแยกไว้โชว์อีกจอ
    #
    #  ภูมิประเทศสตรีมเป็น XYZ tile รอบตำแหน่งปัจจุบันเหมือนแผนที่ 2D
    #  (ดู assets/map3d/terrain.js) ผ่าน tile_cache ตัวเดิม จึงใช้ offline ได้ด้วย
    # ══════════════════════════════════════════════════════════
    def _build_map3d_buttons(self):
        """ชิป 3D สองอัน วางต่อท้ายแถบพิกัดใต้แผนที่ (ทรง/ขนาดเดียวกัน)"""
        self.btn_map3d = QPushButton("3D")
        self.btn_map3d.setCheckable(True)
        self.btn_map3d.setCursor(Qt.PointingHandCursor)
        self.btn_map3d.setToolTip(
            "สลับแผนที่เป็นภูมิประเทศ 3 มิติ (โหลดรอบตำแหน่งปัจจุบัน ค่อย ๆ เติม)\n"
            "ดับเบิลคลิกพื้นในโหมด 3D = สั่งบินไปจุดนั้น")
        self.btn_map3d.setStyleSheet(self._map3d_chip_qss(T("cyan")))
        self.btn_map3d.toggled.connect(self._toggle_map3d)

        self.btn_map3d_win = QPushButton("3D ⧉")
        self.btn_map3d_win.setCursor(Qt.PointingHandCursor)
        self.btn_map3d_win.setToolTip(
            "เปิดแผนที่ 3D เป็นหน้าต่างแยก — ลากไปโชว์อีกจอได้ "
            "โดยแผนที่เดิมยังใช้งานปกติ")
        self.btn_map3d_win.setStyleSheet(self._map3d_chip_qss(T("cyan")))
        self.btn_map3d_win.clicked.connect(self._open_map3d_window)

        # ป้ายสถานะสั้น ๆ ("โหลดภูมิประเทศ…" ตอนเปิด 3D ครั้งแรก ฯลฯ)
        #
        # กว้างคงที่ + visible เสมอ (ข้อความว่างตอนไม่มีอะไรบอก) — เจตนาเดียวกับ
        # self.banner ด้านบนสุดของหน้าต่าง (เว้นพื้นที่ไว้ตลอดกัน layout ขยับ):
        # แถบพิกัดที่ป้ายนี้อยู่เป็น QHBoxLayout ธรรมดา ไม่มีตัวไหนบีบเล็กลงได้
        # ถ้าปล่อยให้ label หด/ขยายตามการ show/hide (Qt ไม่กันพื้นที่ให้ widget
        # ที่ setVisible(False)) พอข้อความโผล่ปุ๊บทั้งแถบ (และทั้งแอป) จะต้องขยาย
        # ตามทันที — เจอจริงตอนกด 3D ครั้งแรกแล้วโหลดภูมิประเทศเสร็จ ป้ายจาก 8px
        # เป็น 95px ดันทั้งหน้าต่างกว้างเกินจอ (ต้นตอเดียวกับที่ Mission Log เจอ)
        self.lbl_map3d = QLabel("")
        self.lbl_map3d.setFixedWidth(96)
        self.lbl_map3d.setStyleSheet(
            f"color:{T('faint')}; font-size:11px; font-family:{FONT_MONO};")
        return [self.lbl_map3d, self.btn_map3d, self.btn_map3d_win]

    def _set_map3d_note(self, text):
        # ป้ายนี้มีเฉพาะตอนแผนที่ใช้งานได้ — เครื่องที่ไม่มี QtWebEngine ไม่มีปุ่ม/ป้าย
        lbl = getattr(self, "lbl_map3d", None)
        if lbl is None:
            return
        text = text or ""
        if text:
            fm = lbl.fontMetrics()
            lbl.setText(fm.elidedText(text, Qt.ElideRight, lbl.width()))
            lbl.setToolTip(text)
        else:
            lbl.setText("")
            lbl.setToolTip("")

    def _map3d_url(self):
        """URL ของหน้า 3D — ตั้งจุดกึ่งกลางที่ home/โดรนที่เห็นล่าสุด"""
        lat, lon = self._map3d_origin()
        return QUrl(f"{self.map_url}/map3d.html?lat={lat:.7f}&lon={lon:.7f}"
                    f"&zoom=13&radius=3")

    def _presentation_telemetry_snapshot(self):
        """Latest read-model snapshot with legacy fallback for unmigrated/test paths."""
        snap = self._telemetry_store.snapshot()
        if not snap:
            return self._last_telem
        # During the migration a legacy/test path may seed a telemetry object
        # without passing through _on_telemetry. Preserve it as fallback, while
        # normal ingested IDs prefer the immutable store view.
        for did, telemetry in self._last_telem.items():
            snap.setdefault(int(did), telemetry)
        return snap

    def _map3d_origin(self):
        """จุดกึ่งกลางฉาก 3D: จุดที่กระโดดไปแล้ว > home > ตำแหน่งโดรนล่าสุด > ค่าเริ่มต้น"""
        if self._map_center:
            return self._map_center
        for d in sorted(self._home_pos):
            return self._home_pos[d]
        telemetry = self._presentation_telemetry_snapshot()
        for d in sorted(telemetry):
            p = telemetry[d].position
            if p.lat or p.lon:
                return (p.lat, p.lon)
        return (14.9581695, 102.0986187)

    def _jump_map_to(self, lat, lon, zoom=17, set_gcs=False):
        """เลื่อนแผนที่ 2D/3D ไปพิกัด — ไม่สั่งโดรนบิน"""
        lat, lon = float(lat), float(lon)
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            return False
        self._map_center = (lat, lon)
        z = int(zoom)
        self._js(f"jumpTo({lat:.7f},{lon:.7f},{z})")
        if set_gcs:
            self._js(f"setGCS({lat:.7f},{lon:.7f})")
            self._js3d(f"map3d.setGCS({lat:.7f},{lon:.7f})")
        self._js3d(f"map3d.setOrigin({lon:.7f},{lat:.7f})")
        return True

    def _make_map3d_view(self):
        """สร้าง QWebEngineView ของแผนที่ 3D — ใช้ bridge ตัวเดียวกับแผนที่ 2D
        คำสั่งจากแผนที่ 3D จึงวิ่งผ่านด่านเดิมทุกอย่าง (รวมด่านปลดล็อกสั่งบิน)"""
        view = QWebEngineView()
        view.setMinimumSize(0, 0)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        ch = QWebChannel(view)
        ch.registerObject("bridge", self.bridge)
        view.page().setWebChannel(ch)
        view.setStyleSheet("border-radius:12px;")
        view._channel = ch          # กันโดน GC เก็บไปก่อนหน้าเว็บใช้เสร็จ
        view.load(self._map3d_url())
        return view

    def _toggle_map3d(self, on):
        """ปุ่ม 3D — สลับแผนที่ในหน้าต่างเดิม (แผนที่ 2D ไม่ถูกทำลาย)"""
        if not self._map_enabled or not hasattr(self, "map_stack"):
            return
        if on and self.web3d is None:
            self._set_map3d_note("โหลดภูมิประเทศ…")
            self.web3d = self._make_map3d_view()
            self.web3d.loadFinished.connect(
                lambda ok: self._on_map3d_loaded(ok, "embed"))
            self.map_stack.addWidget(self.web3d)        # index 1 = 3D
        self.map_stack.setCurrentIndex(1 if on else 0)
        self.btn_map3d.setStyleSheet(self._map3d_chip_qss(T("cyan"), on=on))
        self._log(f"แผนที่ → {'3D' if on else '2D'}", category="STATUS")
        if not on:
            self._set_map3d_note("")

    def _open_map3d_window(self):
        """ปุ่ม 3D จอแยก — เปิดหน้าต่างใหม่ ลากไปจอที่สองได้ แผนที่เดิมยังใช้ได้ปกติ"""
        if not self._map_enabled:
            self._show_toast("เครื่องนี้เปิดแผนที่ไม่ได้ (ไม่มี QtWebEngine)", "err")
            return
        if self.map3d_win is not None:
            self.map3d_win.raise_()
            self.map3d_win.activateWindow()
            return
        win = QWidget()
        win.setWindowTitle("SwarmGod — แผนที่ 3D")
        win.setStyleSheet(f"background:{T('bg')};")
        win.resize(1280, 800)
        v = QVBoxLayout(win)
        v.setContentsMargins(0, 0, 0, 0)
        view = self._make_map3d_view()
        view.loadFinished.connect(lambda ok: self._on_map3d_loaded(ok, "window"))
        v.addWidget(view)
        win._view = view
        # ปิดหน้าต่างแล้วต้องเคลียร์อ้างอิง ไม่งั้นกดเปิดใหม่จะไปปลุกหน้าต่างที่ตายแล้ว
        win.destroyed.connect(self._on_map3d_window_closed)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.show()
        self.map3d_win = win
        self.web3d_win = view
        self._log("เปิดแผนที่ 3D หน้าต่างแยก", category="STATUS")

    def _on_map3d_window_closed(self, *_):
        self.map3d_win = None
        self.web3d_win = None
        self._map3d_win_ready = False

    def _on_map3d_loaded(self, ok, which):
        if which == "embed":
            self._map3d_ready = bool(ok)
        else:
            self._map3d_win_ready = bool(ok)
        if ok:
            self._set_map3d_note("")
            # ส่งสถานะชุดแรกทันที ไม่ต้องรอ tick ถัดไป
            self._push_map3d()
            self._push_map3d_targets()
            self._push_map3d_view(force=True)
            self._js3d(f"setGotoArmed("
                       f"{'true' if getattr(self, '_goto_armed', False) else 'false'})")
            self._js3d(f"map3d.setWaypointMode("
                       f"{'true' if getattr(self, '_waypoint_mode', False) else 'false'})")
        else:
            self._set_map3d_note("3D โหลดไม่สำเร็จ")
            self._log("โหลดแผนที่ 3D ไม่สำเร็จ", category="STATUS", severity="WARNING")

    def _js3d(self, code):
        """ยิงคำสั่งเข้าแผนที่ 3D ทุกบานที่เปิดอยู่ (ฝัง + หน้าต่างแยก)"""
        if getattr(self, "_closing", False):
            return
        if self._map3d_ready and self.web3d is not None:
            self.web3d.page().runJavaScript(code)
        if self._map3d_win_ready and self.web3d_win is not None:
            self.web3d_win.page().runJavaScript(code)

    def _push_map3d(self):
        """ส่งตำแหน่งโดรนทั้งฝูงเข้าแผนที่ 3D ครั้งเดียวจบ (ไม่ใช่ทีละลำทุกแพ็กเก็ต)

        เหตุผลเดียวกับที่ tooltip ถูก throttle ไว้: runJavaScript ต้อง marshal
        ข้าม process ทุกครั้ง ยิงถี่ ๆ ต่อลำจะกิน GUI thread เปล่า ๆ
        """
        if not (self._map3d_ready or self._map3d_win_ready):
            return
        out = self._map_presenter.drone_markers(
            self._presentation_telemetry_snapshot(),
            self._drone_names,
            self.group_of,
            color_for=drone_color,
            mode_name_for=rpc.mode_name,
        )
        self._js3d(f"map3d.setDrones({json.dumps(out)})")

    def _push_map3d_view(self, force=False):
        """ส่งสถานะ "ที่ไม่ใช่ตำแหน่งโดรน" เข้าแผนที่ 3D ให้ครบเหมือนแผนที่ 2D

        เลือกลำไหน / ใครเป็นหัวขบวน / เส้นขบวน / geofence / เส้นทาง waypoint
        เรียกเมื่อสถานะเหล่านี้เปลี่ยนเท่านั้น — ไม่ต้องยิงทุกเฟรม
        """
        if not (self._map3d_ready or self._map3d_win_ready):
            return
        sel = sorted(int(d) for d in (self._selected_or_all() or []))
        head = int(self._head_id or self._leader_id or 0)
        edges = []
        if getattr(self, "_swarm_active", False) and head:
            edges = [{"leader": head, "follower": int(d)}
                     for d in sorted(self.fleet_items) if int(d) != head]
        fence = [{"lat": la, "lon": lo} for (la, lo) in (self._fence_pts_3d() or [])]
        routes = self._wp_routes_3d()
        gcs = self._map3d_origin()

        # ยิงเฉพาะตอนค่าเปลี่ยนจริง — ตัวนี้ถูกเรียกจาก timer เดียวกับตำแหน่งโดรน
        # ถ้าส่งทุกรอบจะกลายเป็น 50 runJavaScript ต่อวินาทีโดยที่ภาพไม่ต่างเลย
        sig = json.dumps([sel, head, edges, fence, routes, gcs], sort_keys=True)
        if sig == getattr(self, "_map3d_view_sig", None) and not force:
            return
        self._map3d_view_sig = sig

        self._js3d(f"map3d.setSelection({json.dumps(sel)})")
        self._js3d(f"map3d.setLeader({head})")
        self._js3d(f"map3d.setSwarmEdges({json.dumps(edges)})")
        self._js3d(f"map3d.setFence({json.dumps(fence)})")
        self._js3d(f"map3d.setWaypoints({json.dumps(routes)})")
        self._js3d(f"map3d.setGCS({gcs[0]:.7f},{gcs[1]:.7f})")

    def _fence_pts_3d(self):
        """จุด geofence ที่ตั้งไว้ (ว่าง = ไม่มีรั้ว)"""
        return list(getattr(self, "_fence_points", []) or [])

    def _wp_routes_3d(self):
        """เส้นทาง Waypoint ในรูปที่แผนที่ 3D ใช้ได้"""
        out = []
        for did, r in sorted(self._wp_all_routes().items()):
            if r is None or r.is_empty():
                continue
            done = (self._wp_sep_index.get(did, 0) if self._wp_separate
                    else self._wp_current_index)
            out.append({"id": int(did), "color": drone_color(int(did)),
                        "done": int(done),
                        "points": [{"lat": p.lat, "lon": p.lon, "action": p.action}
                                   for p in r.points]})
        return out

    def _push_map3d_targets(self):
        """ส่งจุดหมายที่สั่งไว้เข้าแผนที่ 3D — ให้มีจุดกะพริบ + เส้นประเหมือน 2D

        เรียกตอนที่ _nav_targets เปลี่ยนเท่านั้น (สั่ง goto / ถึงเป้า / cancel)
        ไม่ต้องยิงทุกเฟรม — เส้นประหดตามโดรนเองในฝั่ง JS อยู่แล้ว
        """
        if not (self._map3d_ready or self._map3d_win_ready):
            return
        out = [{"id": int(d), "lat": ll[0], "lon": ll[1]}
               for d, ll in sorted(self._nav_targets.items())]
        self._js3d(f"map3d.setTargets({json.dumps(out)})")

    # ── right command panel (accordion) ──
    def _build_commands(self):
        col = QWidget()
        col.setStyleSheet("background:transparent;")
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        hd = QHBoxLayout()
        hd.setContentsMargins(2, 0, 2, 6)
        title = QLabel("COMMANDS")
        title.setStyleSheet(section_label_qss() + f" color:{T('dim')};")
        hd.addWidget(title)
        hd.addStretch()
        self.lbl_mode = QLabel("UI")
        self.lbl_mode.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};"
            f" letter-spacing:0.8px;")
        hd.addWidget(self.lbl_mode)
        # Field Tablet — ย้ายมาจาก top bar (ที่นั่นเหลือเฉพาะป้ายสถานะ)
        # ปุ่มนี้ **เว้นที่ไว้ตลอด** ไม่ show/hide ตามสถานะ
        # (การซ่อน/โผล่ของ widget ในแถวที่บีบไม่ได้ เคยดันทั้งหน้าต่างล้นจอมาแล้ว)
        self.btn_field = QPushButton("LAN OFF")
        self.btn_field.setFixedSize(78, 22)
        self.btn_field.setCursor(Qt.PointingHandCursor)
        self.btn_field.setToolTip(
            "Field Tablet — ให้แท็บเล็ตในวง LAN เปิดดูสถานะได้ (ดูอย่างเดียว สั่งงานไม่ได้)")
        self.btn_field.clicked.connect(self._field_dialog)
        hd.addSpacing(6)
        hd.addWidget(self.btn_field)
        self._field_paint()
        # เมนูไฟล์ตั้งค่า (Save/Export/Load) — ย้ายมาจากแถบซ้าย
        hd.addSpacing(6)
        hd.addWidget(self._build_cfg_button())
        v.addLayout(hd)

        # PRE-FLIGHT อยู่บนสุด — ต้องผ่านก่อนถึงจะไปหมวด FLIGHT ด้านล่าง
        self.sec_preflight = self._sec_preflight()
        v.addWidget(self.sec_preflight)
        for sec in (self._sec_flight(), self._sec_waypoint(), self._sec_movement(),
                    self._sec_formation(), self._sec_safety(), self._sec_geofence(),
                    self._sec_tactical()):
            self._sections.append(sec)
            v.addWidget(sec)
        # CV Track ไม่ถูกล็อกโดย REMOTE (เป็นเซนเซอร์ ไม่ใช่คำสั่งบิน) + กล้องยังไม่ต่อ
        v.addWidget(self._sec_cv_track())
        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(col)
        self._cmd_scroll = scroll        # ใช้เลื่อนไปหาหมวด PRE-FLIGHT ตอนกดป้ายบน topbar
        # กว้างขึ้นให้ปุ่มคู่ ARM/DISARM และคำอธิบายไทยไม่ต้องตัดบรรทัดถี่ ๆ
        scroll.setFixedWidth(372)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:transparent; }}"
            f"QScrollArea > QWidget > QWidget {{ background:transparent; }}")
        wrap = QFrame()
        wrap.setObjectName("CmdWrap")
        wrap.setStyleSheet(card_qss("#CmdWrap", radius=10))
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 10, 10, 10)
        wl.addWidget(scroll)
        wrap.setFixedWidth(394)
        return wrap

    def _build_cfg_button(self):
        """ปุ่มไอคอน 3 จุด — เมนู Save / Export / Load (อยู่หัวแผงขวา)"""
        self.btn_cfg = self._icon_btn(
            "dots", "ไฟล์ตั้งค่า — Save / Export / Load",
            color=T("faint"), size=26, icon_px=15)
        self.btn_cfg.setStyleSheet(
            self._icon_btn_qss(T("faint"), on=False)
            + "QPushButton::menu-indicator { width:0; height:0; }")
        cfg_menu = QMenu(self)
        cfg_menu.setStyleSheet(
            f"QMenu {{ background:{T('panel2')}; color:{T('text')};"
            f" border:1px solid {rgba('#ffffff', 0.1)}; border-radius:8px; padding:6px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:6px; }}"
            f"QMenu::item:selected {{ background:{rgba(T('accent'), 0.25)}; }}")
        a_save = cfg_menu.addAction("Save Settings", self._save_settings)
        a_exp = cfg_menu.addAction("Export…", self._export_settings)
        a_load = cfg_menu.addAction("Load…", self._load_settings_dialog)
        a_prev = cfg_menu.addAction("Load Saved", self._load_saved_settings)
        for act, shape in ((a_save, "save"), (a_exp, "download"),
                           (a_load, "folder"), (a_prev, "check")):
            act.setIcon(geo_icon(shape, T("dim"), 15))
        cfg_menu.addSeparator()
        a_batt = cfg_menu.addAction("รีเซ็ตแบตเตอรี่จำลอง (SITL)…", self._reset_sim_battery)
        a_batt.setIcon(geo_icon("circle", T("green"), 15))
        self.btn_cfg.setMenu(cfg_menu)
        return self.btn_cfg

    def _icon_btn(self, shape, tip, fn=None, color=None, size=30,
                  checkable=False, icon_px=17):
        """ปุ่มไอคอนเรขาคณิต ไม่มีข้อความ — คำอธิบายโผล่ตอน hover เท่านั้น

        ใช้แทนปุ่มข้อความในแถบเครื่องมือ เพื่อลดความรกของหน้าจอ
        """
        color = color or T("dim")
        b = QPushButton()
        b.setIcon(geo_icon(shape, color, icon_px))
        b.setFixedSize(size, size)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)                 # ← ข้อความอธิบายอยู่ที่นี่ที่เดียว
        b.setProperty("geo_shape", shape)
        b.setProperty("geo_color", color)
        b.setStyleSheet(self._icon_btn_qss(color, on=False))
        if fn is not None:
            b.clicked.connect(fn)
        return b

    @staticmethod
    def _icon_btn_qss(color, on=False):
        if on:
            return (f"QPushButton {{ background:{rgba(color, 0.24)};"
                    f" border:1px solid {rgba(color, 0.65)}; border-radius:7px; }}"
                    f"QPushButton:hover {{ background:{rgba(color, 0.34)}; }}")
        return (f"QPushButton {{ background:transparent;"
                f" border:1px solid {hairline()}; border-radius:7px; }}"
                f"QPushButton:hover {{ background:{rgba(color, 0.16)};"
                f" border:1px solid {rgba(color, 0.45)}; }}"
                f"QPushButton:pressed {{ background:{rgba(color, 0.26)}; }}")

    def _set_icon_btn_state(self, btn, on):
        btn.setChecked(on)
        btn.setStyleSheet(self._icon_btn_qss(btn.property("geo_color"), on=on))

    def _abtn(self, text, color, fn, kind="ghost", height=32):
        b = QPushButton(text)
        b.setMinimumHeight(height)
        if kind == "filled":
            b.setStyleSheet(filled_btn(color, radius=6, font=12))
        elif kind == "tinted":
            b.setStyleSheet(tinted_btn(color, radius=6, font=12))
        else:
            b.setStyleSheet(ghost_btn(radius=6, font=12))
        b.clicked.connect(fn)
        return b

    def _flight_btn(self, icon, label, fn, primary=False):
        """ปุ่ม FLIGHT: โลโก้ไว้ "ด้านหน้า" ข้อความในบรรทัดเดียวกัน โทนเดียว ไม่หลากสี"""
        b = QPushButton(f"{icon}  {label}")
        b.setMinimumHeight(38)
        b.setCursor(Qt.PointingHandCursor)
        if primary:
            b.setStyleSheet(
                f"QPushButton {{ background:{rgba(T('accent'), 0.16)};"
                f" border:1px solid {rgba(T('accent'), 0.35)}; border-radius:8px;"
                f" color:{T('text')}; font-size:11px; font-weight:600;"
                f" letter-spacing:0.4px; padding:6px 4px; }}"
                f"QPushButton:hover {{ background:{rgba(T('accent'), 0.26)}; }}"
                f"QPushButton:pressed {{ background:{rgba(T('accent'), 0.36)}; }}"
                f"QPushButton:disabled {{ color:{T('faint')};"
                f" background:{rgba('#ffffff', 0.03)}; border:1px solid {hairline()}; }}")
        else:
            b.setStyleSheet(
                f"QPushButton {{ background:{rgba('#ffffff', 0.03)};"
                f" border:1px solid {hairline()}; border-radius:8px;"
                f" color:{T('text')}; font-size:11px; font-weight:600;"
                f" letter-spacing:0.4px; padding:6px 4px; }}"
                f"QPushButton:hover {{ background:{rgba('#ffffff', 0.07)};"
                f" border:1px solid {rgba('#ffffff', 0.16)}; }}"
                f"QPushButton:pressed {{ background:{rgba('#ffffff', 0.11)}; }}"
                f"QPushButton:disabled {{ color:{T('faint')};"
                f" background:{rgba('#ffffff', 0.02)}; }}")
        b.clicked.connect(fn)
        return b

    def _arrow_btn(self, arrow, fn_press=None, fn_click=None, size=44, primary=False):
        """ปุ่มลูกศรกลม"""
        b = QPushButton(arrow)
        b.setFixedSize(size, size)
        b.setCursor(Qt.PointingHandCursor)
        if primary:
            b.setStyleSheet(
                f"QPushButton {{ background:{rgba('#ffffff', 0.06)}; border:1px solid {rgba('#ffffff', 0.14)};"
                f" border-radius:{size // 2}px; color:{T('text')}; font-size:18px; font-weight:700; }}"
                f"QPushButton:hover {{ background:{rgba('#ffffff', 0.11)}; }}"
                f"QPushButton:pressed {{ background:{rgba(T('accent'), 0.28)}; color:#fff; }}"
                f"QPushButton:disabled {{ color:{T('faint')}; }}")
        else:
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; border:1px solid {hairline()};"
                f" border-radius:{size // 2}px; color:{T('dim')}; font-size:16px; font-weight:600; }}"
                f"QPushButton:hover {{ background:{rgba('#ffffff', 0.06)}; color:{T('text')}; }}"
                f"QPushButton:pressed {{ background:{rgba(T('accent'), 0.22)}; color:#fff; }}"
                f"QPushButton:disabled {{ color:{T('faint')}; }}")
        if fn_press is not None:
            b.pressed.connect(fn_press)
            b.released.connect(self._rc_release)
        if fn_click is not None:
            b.clicked.connect(fn_click)
        return b

    def _arrow_cell(self, arrow, caption, fn_press=None, fn_click=None, size=44, primary=False):
        """ลูกศร + ตัวหนังสือเล็กบอกชื่อปุ่ม"""
        cell = QWidget()
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        v.setAlignment(Qt.AlignCenter)
        btn = self._arrow_btn(arrow, fn_press=fn_press, fn_click=fn_click,
                              size=size, primary=primary)
        btn.setToolTip(caption)
        lab = QLabel(caption)
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet(
            f"color:{T('faint')}; font-size:9px; font-weight:600; letter-spacing:0.4px;")
        v.addWidget(btn, 0, Qt.AlignCenter)
        v.addWidget(lab)
        return cell

    def _refresh_logos(self):
        """วาดแถบโลโก้ใหม่ — ของผู้ใช้ก่อน ถ้ายังไม่เคยใส่ ใช้ logo.png ที่มากับโปรเจค"""
        if not hasattr(self, "logo_row"):
            return
        while self.logo_row.count():
            it = self.logo_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        paths = logo_store.list_logos()
        if not paths:
            default = os.path.join(_ASSETS, "logo.png")
            if os.path.exists(default):
                paths = [default]
        from PyQt5.QtGui import QPixmap as _QPixmap
        for p in paths[:logo_store.MAX_LOGOS]:
            pm = _QPixmap(p)
            if pm.isNull():
                continue
            lg = QLabel()
            lg.setPixmap(pm.scaledToHeight(36, Qt.SmoothTransformation))
            lg.setStyleSheet("background:transparent;")
            lg.setToolTip(os.path.basename(p))
            self.logo_row.addWidget(lg)

    def _open_logo_settings(self):
        dlg = LogoSettingsDialog(self)
        dlg.changed.connect(self._refresh_logos)
        dlg.exec_()
        self._refresh_logos()

    def _build_mode_drop(self, bar):
        """ป้ายโหมดกลาง topbar ที่ "เลื่อนลงมา" แล้วค้างไว้ (spec ข้อ 7)

        เป็น child ลอยของ topbar (ไม่อยู่ใน layout) เพื่อให้ขยับตำแหน่งด้วย animation ได้
        โดยไม่ดันปุ่มอื่นในแถบ — ใช้ QPropertyAnimation กับ geometry
        """
        self.mode_drop = QLabel("✈ FLIGHT", bar)
        self.mode_drop.setAlignment(Qt.AlignCenter)
        self.mode_drop.setFixedSize(150, 26)
        self._style_mode_drop("flight")
        self._mode_anim = QPropertyAnimation(self.mode_drop, b"geometry", self)
        self._mode_anim.setDuration(320)
        self._mode_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.mode_drop.show()
        # จัดกึ่งกลางตาม "ความกว้างของ topbar" ไม่ใช่ของหน้าต่าง — ตอน window resize
        # topbar ยัง layout ไม่เสร็จ ค่า width() ยังเป็นของเก่า ป้ายเลยไปกองซ้ายสุด
        bar.installEventFilter(self)

    def _mode_drop_rect(self, shown=True):
        """ตำแหน่งเป้าหมาย: ซ่อน = อยู่เหนือขอบบน, โชว์ = ค้างกลางแถบ"""
        bar = getattr(self, "_topbar", None)
        w, h = self.mode_drop.width(), self.mode_drop.height()
        bw = bar.width() if bar is not None else self.width()
        x = max(0, (bw - w) // 2)
        return QRect(x, 14 if shown else -h, w, h)

    def _style_mode_drop(self, mode):
        text, color = {
            "swarm": ("◆ SWARM", T("amber")),
            "rtl": ("↩ RTL", T("orange")),
            "waypoint": ("◇ WAYPOINT", T("green")),
        }.get(mode, ("✈ FLIGHT", T("accent")))
        self.mode_drop.setText(text)
        self.mode_drop.setStyleSheet(
            f"color:{color}; background:{rgba(color, 0.20)};"
            f" border:1px solid {rgba(color, 0.55)}; border-radius:13px;"
            f" font-size:12px; font-weight:800; letter-spacing:1.2px;")
        self.mode_drop.setToolTip({
            "swarm": "โหมด SWARM — ฝูงเกาะขบวนตามตัวแม่",
            "rtl": "กำลังกลับฐานแบบแยกชั้นความสูง",
            "waypoint": "กำลังบินตามเส้นทาง Waypoint ที่วางไว้",
        }.get(mode, "โหมด FLIGHT — สั่งรายลำ/กลุ่มที่เลือก"))

    def _play_mode_drop(self, mode):
        """เล่นอนิเมชันเลื่อนลง: ดีดขึ้นไปซ่อนก่อน แล้วค่อยเลื่อนลงมาค้างพร้อมสีใหม่"""
        if not hasattr(self, "mode_drop"):
            return
        self._style_mode_drop(mode)
        self._mode_anim.stop()
        self.mode_drop.setGeometry(self._mode_drop_rect(shown=False))
        self._mode_anim.setStartValue(self._mode_drop_rect(shown=False))
        self._mode_anim.setEndValue(self._mode_drop_rect(shown=True))
        self._mode_anim.start()

    def eventFilter(self, obj, e):
        # topbar เปลี่ยนขนาดจริงเมื่อไหร่ ค่อยจัดป้ายให้กึ่งกลางใหม่
        if (obj is getattr(self, "_topbar", None)
                and e.type() == QEvent.Resize
                and hasattr(self, "mode_drop")
                and self._mode_anim.state() != QPropertyAnimation.Running):
            self.mode_drop.setGeometry(self._mode_drop_rect(shown=True))
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        """1..6 เลือกกลุ่ม; Ctrl+1..6 กำหนดชุดที่เลือกเข้ากลุ่ม"""
        key = int(e.key())
        if Qt.Key_1 <= key <= Qt.Key_6 and not (e.modifiers() & (Qt.AltModifier | Qt.MetaModifier)):
            group = key - Qt.Key_0
            if e.modifiers() & Qt.ControlModifier:
                self._assign_selected_to_group(group)
            else:
                self._select_group(group, additive=False)
            e.accept()
            return
        super().keyPressEvent(e)

    def _servo_btn(self, label, color):
        """ปุ่ม SERVO A/B — สีตรงกับป้ายสถานะบนการ์ด (A=แดง, B=เหลือง)

        ใหญ่พอให้กดง่ายตอนสวมถุงมือ/สถานการณ์จริง แต่ยังอยู่แถวเดียวกันทั้งคู่
        """
        b = QPushButton(label)
        b.setMinimumHeight(60)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.setCursor(Qt.PointingHandCursor)
        ch = self.SERVO_CH[label]
        b.setToolTip(f"SERVO {label} (CH{ch}) — กดแล้วสั่งทันที ไม่ถามยืนยัน · "
                     f"กดซ้ำ = ยกเลิกและคืนช่องให้รีโมท")
        b.setStyleSheet(
            f"QPushButton {{ background:{rgba(color, 0.14)};"
            f" border:1px solid {rgba(color, 0.45)}; border-radius:10px;"
            f" color:{color}; font-size:26px; font-weight:800; letter-spacing:2px; }}"
            f"QPushButton:hover {{ background:{rgba(color, 0.26)};"
            f" border:1px solid {rgba(color, 0.7)}; }}"
            f"QPushButton:pressed {{ background:{rgba(color, 0.36)}; }}"
            f"QPushButton:disabled {{ color:{T('faint')};"
            f" background:{rgba('#ffffff', 0.03)}; border:1px solid {hairline()}; }}")
        b.clicked.connect(lambda _=False, l=label: self._cmd_servo(l))
        return b

    def _target_seg(self):
        seg = Segmented(["Selected", "Fleet"], selected=0, accent=T("accent"),
                        height=28, style="capsule")
        seg.changed.connect(lambda i: setattr(self, "_target_mode",
                                               "fleet" if i == 1 else "selected"))
        return seg

    def _sec_flight(self):
        sec = AccordionSection("FLIGHT", accent=T("green"), expanded=True)

        # คำสั่งที่ใช้ระหว่างบินจริงอยู่ชั้นแรกทั้งหมด — ไม่ต้องกางเมนูเพิ่ม
        tl = QLabel("QUICK FLIGHT")
        tl.setStyleSheet(section_label_qss())
        sec.add_widget(tl)
        g = QGridLayout()
        g.setSpacing(6)
        g.addWidget(self._flight_btn("▶", "ARM", self._cmd_arm, primary=True), 0, 0)
        g.addWidget(self._flight_btn("■", "DISARM", self._cmd_disarm), 0, 1)
        g.addWidget(self._flight_btn("⬇", "LAND", self._cmd_land), 1, 0)
        g.addWidget(self._flight_btn("↩", "RTL", self._cmd_rtl), 1, 1)
        g.addWidget(self._flight_btn("❚❚", "HOLD", self._cmd_hold), 2, 0, 1, 2)
        sec.add_layout(g)

        # TAKEOFF เป็น workflow หลัก จึงคงไว้ชั้นแรก แต่ตัด label ที่ซ้ำกันออก
        self.sf_takeoff = SliderField("TAKEOFF ALTITUDE", 1, 120, 20, 1, "m", 0, T("accent"))
        sec.add_widget(self.sf_takeoff)
        self.takeoff_panel = TakeoffPanel(default_alt=20.0)
        self.takeoff_panel.takeoff_requested.connect(self._on_panel_takeoff)
        self.takeoff_panel.fleet_toggled.connect(self._on_fleet_toggled)
        self.takeoff_panel.changed.connect(self._update_takeoff_summary)
        self.sf_takeoff.valueChanged.connect(self.takeoff_panel.set_default_alt)
        sec.add_widget(self.takeoff_panel)

        # ของที่ใช้เป็นครั้งคราวย้ายเข้า ADVANCED FLIGHT เพื่อลดความแน่นของหน้า
        # signal/handler เดิมทั้งหมดคงไว้ — เปลี่ยนเฉพาะ presentation hierarchy
        advanced = AccordionSection("ADVANCED FLIGHT", accent=T("dim"), expanded=False)

        sl = QLabel("PAYLOAD SERVO")
        sl.setStyleSheet(section_label_qss())
        advanced.add_widget(sl)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.btn_servo_a = self._servo_btn("A", T("red"))
        self.btn_servo_b = self._servo_btn("B", T("yellow"))
        srow.addWidget(self.btn_servo_a, 1)
        srow.addWidget(self.btn_servo_b, 1)
        advanced.add_layout(srow)

        self.btn_cancel_nav = QPushButton("CANCEL NAV")
        self.btn_cancel_nav.setMinimumHeight(36)
        self.btn_cancel_nav.setCursor(Qt.PointingHandCursor)
        self.btn_cancel_nav.setToolTip(
            "ยกเลิกเป้าหมาย: ลบจุดเป้า+เส้นประ+เส้นทาง Waypoint บนแผนที่\n"
            "แล้วให้โดรนหยุดลอยค้างที่เดิม (คงโหมด GUIDED · ความสูงเท่าเดิม ไม่ลดระดับ)")
        self.btn_cancel_nav.setStyleSheet(tinted_btn(T("red"), radius=8, font=11))
        self.btn_cancel_nav.clicked.connect(self._cancel_navigation)
        advanced.add_widget(self.btn_cancel_nav)

        ml = QLabel("FLIGHT MODE")
        ml.setStyleSheet(section_label_qss())
        advanced.add_widget(ml)
        gm = QGridLayout()
        gm.setSpacing(6)
        modes = [("Guided", "FLIGHT_MODE_GUIDED"), ("Loiter", "FLIGHT_MODE_LOITER"),
                 ("Stabilize", "FLIGHT_MODE_STABILIZE"), ("PosHold", "FLIGHT_MODE_POSHOLD")]
        for i, (label, enum) in enumerate(modes):
            gm.addWidget(self._abtn(label, T("dim"), lambda _, e=enum: self._cmd_mode(e), "ghost"),
                         i // 2, i % 2)
        advanced.add_layout(gm)
        sec.add_widget(advanced)
        self.sec_flight_advanced = advanced
        return sec

    # ══════════════════════════════════════════════════════════
    #  WAYPOINT ROUTE PLANNING
    # ══════════════════════════════════════════════════════════
    def _sec_waypoint(self):
        sec = AccordionSection("WAYPOINT ROUTE", accent=T("green"), expanded=False)

        srow = QHBoxLayout()
        srow.setSpacing(8)
        slab = QLabel("WAYPOINT MODE")
        slab.setStyleSheet(section_label_qss())
        srow.addWidget(slab)
        srow.addStretch(1)
        self.sw_waypoint = CapsuleSwitch(
            "OFF", "ON", selected=0,
            left_accent=T("dim"), right_accent=T("green"), height=26)
        self.sw_waypoint.changed.connect(lambda i: self._wp_toggle(i == 1))
        srow.addWidget(self.sw_waypoint)
        sec.add_layout(srow)

        hint = QLabel("เปิดแล้วคลิกแผนที่เพื่อวางจุด — ไม่บินทันที (ปิด = Direct Flight ปกติ)")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
        sec.add_widget(hint)

        # ── โหมดเส้นทาง: รวม (ไปพร้อมกัน) vs แยกลำ (คำนวณ/บินแยก) ──
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        mlab = QLabel("ROUTE MODE")
        mlab.setStyleSheet(section_label_qss())
        mrow.addWidget(mlab)
        mrow.addStretch(1)
        self.seg_wp_mode = Segmented(["GROUPED", "SEPARATE"], selected=0,
                                     accent=T("green"), height=26, style="capsule")
        self.seg_wp_mode.changed.connect(lambda i: self._wp_set_separate(i == 1))
        mrow.addWidget(self.seg_wp_mode)
        sec.add_layout(mrow)

        # WAVE มีผลกับปุ่ม EXECUTE ROUTE เท่านั้น และเปิดได้เฉพาะ GROUPED
        wrow = QHBoxLayout()
        wrow.setSpacing(8)
        wlab = QLabel("WAVE")
        wlab.setStyleSheet(section_label_qss())
        wrow.addWidget(wlab)
        wrow.addStretch(1)
        self.sw_wave = CapsuleSwitch(
            "OFF", "ON", selected=0,
            left_accent=T("dim"), right_accent=T("amber"), height=26)
        self.sw_wave.changed.connect(lambda i: self._wave_toggle(i == 1))
        wrow.addWidget(self.sw_wave)
        sec.add_layout(wrow)

        self.wave_body = QWidget()
        wave_lay = QVBoxLayout(self.wave_body)
        wave_lay.setContentsMargins(0, 2, 0, 2)
        wave_lay.setSpacing(5)
        group_row = QHBoxLayout()
        group_row.setSpacing(5)
        group_label = QLabel("WAVE GROUPS")
        group_label.setFixedWidth(92)
        group_label.setStyleSheet(f"color:{T('dim')}; font-size:9px;")
        group_row.addWidget(group_label)
        self.wave_group_checks = {}
        for group in range(1, 7):
            check = QCheckBox(str(group))
            check.setFixedWidth(32)
            check.setStyleSheet(f"color:{drone_color(group)}; font-size:10px; font-weight:700;")
            check.toggled.connect(self._wave_update_sequence)
            self.wave_group_checks[group] = check
            group_row.addWidget(check)
        group_row.addStretch(1)
        wave_lay.addLayout(group_row)
        self.lbl_wave_sequence = QLabel("ลำดับ: —")
        self.lbl_wave_sequence.setFixedWidth(300)
        self.lbl_wave_sequence.setStyleSheet(
            f"color:{T('amber')}; font-size:9px; font-family:{FONT_MONO};")
        wave_lay.addWidget(self.lbl_wave_sequence)
        self.lbl_wave_progress = QLabel("WAVE: พร้อม")
        self.lbl_wave_progress.setFixedWidth(300)
        self.lbl_wave_progress.setStyleSheet(
            f"color:{T('dim')}; font-size:9px; font-family:{FONT_MONO};")
        wave_lay.addWidget(self.lbl_wave_progress)
        self.chk_wave_auto_next = QCheckBox("AUTO NEXT GROUP · ไม่ถาม TAKEOFF ซ้ำ")
        self.chk_wave_auto_next.setToolTip(
            "เมื่อเริ่ม WAVE แล้ว กลุ่มแรกยังยืนยันตามปกติ แต่กลุ่มถัดไปจะ TAKEOFF "
            "และทำเส้นทางต่ออัตโนมัติ โดยไม่เปิดกล่องยืนยันซ้ำ")
        self.chk_wave_auto_next.setStyleSheet(
            f"QCheckBox {{ color:{T('green')}; font-size:9px; font-weight:700; }}"
            f"QCheckBox::indicator {{ width:15px; height:15px; }}")
        self.chk_wave_auto_next.toggled.connect(
            lambda _checked: self._summ_sync_waypoint())
        wave_lay.addWidget(self.chk_wave_auto_next)
        self.btn_wave_cancel = self._abtn(
            "■ ยกเลิกเวฟ", T("red"), self._wave_cancel, "tinted", 30)
        self.btn_wave_cancel.setVisible(False)
        wave_lay.addWidget(self.btn_wave_cancel)
        self.wave_body.setVisible(False)
        sec.add_widget(self.wave_body)

        self.lbl_wp_mode_hint = QLabel("")
        self.lbl_wp_mode_hint.setWordWrap(True)
        self.lbl_wp_mode_hint.setStyleSheet(
            f"color:{T('dim')}; font-size:9px; line-height:135%;"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:5px 8px;")
        sec.add_widget(self.lbl_wp_mode_hint)

        pl = QLabel("ROUTE POINTS")
        pl.setStyleSheet(section_label_qss())
        sec.add_widget(pl)
        self.lbl_wp_points = QLabel("ยังไม่มีจุด")
        self.lbl_wp_points.setWordWrap(True)
        self.lbl_wp_points.setStyleSheet(
            f"color:{T('dim')}; font-size:10px; font-family:{FONT_MONO};"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:6px 8px;")
        self.lbl_wp_points.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lbl_wp_points.customContextMenuRequested.connect(self._wp_points_menu)
        sec.add_widget(self.lbl_wp_points)

        brow = QHBoxLayout(); brow.setSpacing(6)
        brow.addWidget(self._abtn("↩ UNDO", T("dim"), self._wp_undo, "ghost"))
        brow.addWidget(self._abtn("✖ CLEAR ALL", T("red"), self._wp_clear, "tinted"))
        sec.add_layout(brow)

        self.btn_wp_execute = self._abtn(
            "▶ EXECUTE ROUTE", T("green"), self._wp_execute, "filled", 40)
        sec.add_widget(self.btn_wp_execute)

        self.lbl_wp_status = QLabel("พร้อม · 0 จุด")
        self.lbl_wp_status.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};")
        sec.add_widget(self.lbl_wp_status)
        self._wp_update_mode_hint()
        self._refresh_wave_group_choices()
        self._wp_refresh_mode_availability()
        return sec

    def _wp_update_mode_hint(self):
        """อธิบายโหมดปัจจุบัน + บอกว่ากำลังวางแผนให้ลำไหนอยู่"""
        if not hasattr(self, "lbl_wp_mode_hint"):
            return
        if self._swarm_active:
            self.lbl_wp_mode_hint.setText(
                "SWARM ACTIVE — ใช้ GROUPED Leader Path เท่านั้น · "
                "SEPARATE และ WAVE ถูกล็อก\n"
                "วางเส้นทาง/คลิกขวา/ตั้ง WAIT/ใช้ A/B ได้เฉพาะ 'ลำแม่' (Head) · "
                "ทั้งขบวนบินตามพร้อมกัน")
            return
        if self._wp_separate:
            did = self._wp_plot_target()
            who = f"Drone {did}" if did else "— ยังไม่ได้เลือกลำ —"
            self.lbl_wp_mode_hint.setText(
                f"SEPARATE — แต่ละลำมีเส้นทางของตัวเอง คำนวณ/บินแยกกัน\n"
                f"กำลังวางแผนให้: {who}  (คลิกการ์ดฝั่งซ้ายเพื่อสลับลำ)")
        else:
            self.lbl_wp_mode_hint.setText(
                "GROUPED — เส้นทางเดียวร่วมกัน ทุกลำที่เลือกบินไปพร้อมกัน "
                "โดยคงรูปขบวน/ระยะห่างไว้")

    def _wp_refresh_mode_availability(self):
        """ล็อก/ปลดล็อก SEPARATE + WAVE ตามสถานะ Swarm (spec §10)

        เรียกตอนสร้าง UI, ตอน _on_swarm_update active state เปลี่ยน, และตอนออก Swarm
        UI disable เป็นแค่ชั้นแรก — logic guard ใน _wp_set_separate/_wave_toggle กันซ้ำ
        ออกจาก Swarm แล้ว controls กลับมา enable แต่ "ไม่ restore" state เดิมอัตโนมัติ
        """
        active = bool(getattr(self, "_swarm_active", False))
        if active:
            # 1. force GROUPED
            if self._wp_separate:
                self._wp_set_separate(False)
            # 2. WAVE ที่เปิดอยู่ → ปิดอย่างปลอดภัย
            if getattr(self, "_wave_executing", False):
                # Core รายงาน Swarm ขณะ WAVE ยังวิ่ง — invalidate โดยไม่ยิงคำสั่งทับ Core
                self._wp_swarm_defensive_wave_stop()
            elif getattr(self, "_wave_enabled", False):
                self._wave_toggle(False)
        # 3+4. disable/enable controls (GROUPED ยัง enabled เสมอ)
        if hasattr(self, "seg_wp_mode"):
            self.seg_wp_mode.setEnabled(not active)
        if hasattr(self, "sw_wave"):
            self.sw_wave.setEnabled(not active)
        self._wp_update_mode_hint()

    def _wp_swarm_defensive_wave_stop(self):
        """Core รายงาน Swarm Active ขณะ WAVE ยัง execute (spec §10 defensive)

        invalidate WAVE/Waypoint progression ฝั่ง cockpit + ห้าม callback เก่าเดินต่อ
        โดย **ไม่ส่ง command** (stop/hold/swarm_stop) ที่อาจชนกับ Core Swarm state
        """
        self._wave_generation += 1
        self._wp_takeoff_generation += 1
        self._wp_takeoff_pending = False
        self._wp_wait_invalidate()
        self._wave_timer.stop()
        self._wave_executing = False
        self._waypoint_executing = False
        self._wave_enabled = False
        self._wave_phase = "idle"
        self._wave_group_index = -1
        self._wave_auto = False
        self._wave_auto_next = False
        self._wp_arrived = set()
        self._wp_sep_index = {}
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_run_cancel("Swarm active — WAVE invalidated")
        if hasattr(self, "chk_wave_auto_next"):
            self.chk_wave_auto_next.setEnabled(True)
        if hasattr(self, "wave_body"):
            self.wave_body.setVisible(False)
        if hasattr(self, "sw_wave"):
            self.sw_wave.setCurrent(0)
        if hasattr(self, "btn_wave_cancel"):
            self.btn_wave_cancel.setVisible(False)
        self._log("SWARM ACTIVE ระหว่าง WAVE — ยกเลิก WAVE ฝั่ง cockpit "
                  "(ไม่ส่งคำสั่งทับ Core)", category="ALERT", severity="ERROR")
        self._show_banner("SWARM ACTIVE — WAVE ถูกยกเลิก (Core เป็นเจ้าของขบวน)", T("red"))
        self._summ_sync_waypoint()
        self._refresh_flight_mode()

    def _wave_available_groups(self):
        return [group for group in range(1, 7)
                if any(self.group_of.get(int(did)) == group for did in self.fleet_items)]

    def _wave_selected_groups(self):
        if not hasattr(self, "wave_group_checks"):
            return []
        return [g for g, check in self.wave_group_checks.items()
                if check.isEnabled() and check.isChecked()]

    def _wave_member_ids(self, groups=None):
        """ลำออนไลน์ที่เป็นสมาชิกกลุ่ม WAVE ที่เลือก (หรือที่ส่งมา)"""
        want = set(groups if groups is not None else self._wave_selected_groups())
        online = set(self._connected_ids())
        return [d for d in sorted(self.fleet_items)
                if d in online and self.group_of.get(int(d)) in want]

    def _wave_progress_text(self):
        if not getattr(self, "_wave_executing", False):
            return ""
        groups = getattr(self, "_wave_groups", None) or []
        idx = int(getattr(self, "_wave_group_index", -1) or -1)
        phase = str(getattr(self, "_wave_phase", "") or "")
        n = len(groups)
        if n and 0 <= idx < n:
            return "กลุ่ม %d จาก %d · %s" % (idx + 1, n, phase or "—")
        return phase or "กำลัง WAVE"

    def _wave_payload_summary(self, group):
        """ข้อความผล Payload ที่คงอยู่ใน ACTIVE ทั้งสำเร็จและไม่สำเร็จ."""
        releases = self._wave_payload_done.get(int(group), [])
        failures = self._wave_payload_failed.get(int(group), [])
        if not releases and not failures:
            return ""
        counts = {"A": 0, "B": 0}
        for label, _wp_index in releases:
            if label in counts:
                counts[label] += 1
        parts = []
        for label in ("A", "B"):
            count = counts[label]
            if count:
                suffix = " ×%d" % count if count > 1 else ""
                parts.append("✓ ปล่อย %s แล้ว%s" % (label, suffix))
        for label, wp_index in failures:
            parts.append("! ปล่อย %s ไม่สำเร็จ (WP %d)" % (label, int(wp_index) + 1))
        return " · ".join(parts)

    def _wave_group_detail(self, group, phase):
        released = self._wave_payload_summary(group)
        return str(phase) + ((" · " + released) if released else "")

    def _wave_record_payload_release(self, group, label, wp_index):
        """บันทึกผลจริงหลังครบ HOLD → SERVO → release และวาดในแท็บ ACTIVE."""
        group = int(group)
        self._wave_payload_done.setdefault(group, []).append((str(label), int(wp_index)))
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, "ROUTE · WP %d" % (int(wp_index) + 1)))
        self._log("WAVE กลุ่ม %d: ✓ ปล่อย %s แล้วที่ WP %d" %
                  (group, label, int(wp_index) + 1),
                  category="COMMAND", severity="SUCCESS")
        self._field_push_state()

    def _wave_record_payload_failure(self, group, label, wp_index):
        group = int(group)
        self._wave_payload_failed.setdefault(group, []).append((str(label), int(wp_index)))
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, "ROUTE · WP %d" % (int(wp_index) + 1)))
        self._log("WAVE กลุ่ม %d: ปล่อย %s ไม่สำเร็จที่ WP %d" %
                  (group, label, int(wp_index) + 1),
                  category="COMMAND", severity="ERROR")
        self._field_push_state()

    @staticmethod
    def _set_fixed_label_text(label, text):
        label.setToolTip(text)
        label.setText(label.fontMetrics().elidedText(text, Qt.ElideRight, label.width()))

    def _wave_update_sequence(self):
        if not hasattr(self, "lbl_wave_sequence"):
            return
        groups = self._wave_selected_groups()
        text = "ลำดับ: " + (" → ".join(map(str, groups)) if groups else "—")
        self._set_fixed_label_text(self.lbl_wave_sequence, text)
        if hasattr(self, "_cmd_summary"):
            self._summ_sync_waypoint()

    def _wave_toggle(self, on):
        on = bool(on)
        # ปิดซ้ำทั้งที่ปิดอยู่แล้ว = สวิตช์ sync กลับมาเอง ไม่ใช่คำสั่งของคน
        # ทำงานต่อจะได้ log/banner ผี และเสี่ยงวน setCurrent ↔ changed จนแอปเด้ง
        if not on and not self._wave_enabled and not self._wave_executing:
            if hasattr(self, "sw_wave"):
                self.sw_wave.setCurrent(0)
            return
        if on:
            # Logic guard (spec §10): ระหว่าง Swarm Active ห้ามเปิด WAVE
            # แม้ถูกเรียกจาก code ตรง ๆ — ไม่พึ่งแค่ปุ่ม disabled
            if getattr(self, "_swarm_active", False):
                self._show_toast(
                    "โหมด Swarm เปิดอยู่ — WAVE ถูกล็อก · ออกจาก Swarm ก่อน", "err")
                self._log("เปิด WAVE ถูกปฏิเสธ — อยู่ในโหมด Swarm",
                          category="COMMAND", severity="WARNING")
                if hasattr(self, "sw_wave"):
                    self.sw_wave.setCurrent(0)
                return
            if self._wp_separate:
                self._show_toast("WAVE ใช้ได้เฉพาะ ROUTE MODE: GROUPED", "err")
                self._log("เปิด WAVE ถูกปฏิเสธ — อยู่ในโหมด SEPARATE",
                          category="COMMAND", severity="WARNING")
                if hasattr(self, "sw_wave"):
                    self.sw_wave.setCurrent(0)
                return
            groups = self._wave_available_groups()
            if len(groups) < 2:
                self._show_toast("WAVE ต้องมีอย่างน้อย 2 กลุ่มที่มีโดรน", "err")
                self._log("เปิด WAVE ถูกปฏิเสธ — มีกลุ่มไม่ถึง 2 กลุ่ม",
                          category="COMMAND", severity="WARNING")
                if hasattr(self, "sw_wave"):
                    self.sw_wave.setCurrent(0)
                return
            self._wave_enabled = True
            for group, check in self.wave_group_checks.items():
                check.setChecked(group in groups)
            self.wave_body.setVisible(True)
            self._show_banner(
                "WAVE ACTIVE — EXECUTE ROUTE จะส่งแต่ละกลุ่มบินตามลำดับและรอให้กลุ่มก่อนลงจอด",
                T("amber"), persistent=True)
            self._log(f"เปิด WAVE · กลุ่ม {' → '.join(map(str, groups))}", category="COMMAND")
        else:
            if self._wave_executing:
                self._wave_cancel()
            self._wave_enabled = False
            if hasattr(self, "wave_body"):
                self.wave_body.setVisible(False)
            if self._ui_mode:
                self._banner_timer.stop()
                self._hide_banner()
            if hasattr(self, "sw_wave"):
                self.sw_wave.setCurrent(0)
            self._log("ปิด WAVE", category="COMMAND")
        self._summ_sync_waypoint()

    def _wave_cancel(self):
        if self._wave_executing or self._waypoint_executing:
            self._cancel_navigation()
        else:
            self._wave_abort("ยกเลิกแล้ว", clear_route=False)

    def _wave_abort(self, reason="ยกเลิกแล้ว", clear_route=True):
        was_running = self._wave_executing
        self._wave_generation += 1
        self._wp_takeoff_generation += 1
        self._wp_takeoff_pending = False
        self._wp_wait_invalidate()   # WAIT ค้างในกลุ่มปัจจุบันต้องถูกยกเลิกด้วย
        self._wave_timer.stop()
        self._wave_executing = False
        self._waypoint_executing = False
        self._wave_phase = "idle"
        self._wave_group_index = -1
        self._wave_auto = False
        self._wave_auto_next = False
        if hasattr(self, "chk_wave_auto_next"):
            self.chk_wave_auto_next.setEnabled(True)
        self._wp_arrived = set()
        self._wp_sep_index = {}
        self._abort_rtl()
        if clear_route:
            self._js("clearAllWaypoints()")
            self._waypoint_route = None
            self._wp_routes = {}
            self._wave_route_template = None
            if hasattr(self, "lbl_wp_points"):
                self.lbl_wp_points.setText("ยังไม่มีจุด")
        if hasattr(self, "btn_wave_cancel"):
            self.btn_wave_cancel.setVisible(False)
        if hasattr(self, "lbl_wave_progress"):
            self._set_fixed_label_text(self.lbl_wave_progress, f"WAVE: {reason}")
        self._refresh_wave_group_choices()
        if was_running:
            if self._flight_run and self._flight_run.kind == "wave":
                self._flight_run_cancel(reason)
            self._log(f"WAVE หยุด — {reason}", category="COMMAND", severity="WARNING")
            self._show_toast(f"WAVE หยุด · {reason}", "err")
        self._summ_sync_waypoint()
        self._refresh_flight_mode()

    def _wave_tick(self):
        if not self._wave_executing:
            self._wave_timer.stop()
            return
        if time.monotonic() - self._wave_group_started_at > self._wave_timeout_s:
            ids = list(self._wp_target_ids)
            self._wave_abort("หมดเวลา 5 นาที", clear_route=True)
            if ids:
                # StopAll ยกเลิก return goroutine ใน Core ก่อน HOLD; การเรียก
                # Hold อย่างเดียวปล่อย fallback RTL เก่ายิงคำสั่งทับภายหลังได้.
                threading.Thread(target=lambda: self._safe(lambda: self._dispatch_core(
                    "STOP ALL [wave-timeout]", lambda: self.client.stop_all(ids),
                    source="wave-timeout", targets=ids)), daemon=True).start()
            self._show_banner("WAVE TIMEOUT — หยุดทั้งชุดแล้ว ไม่เริ่มกลุ่มถัดไป", T("red"))
            return
        if self._wave_phase != "waiting_land":
            self._wp_render_status()
            return
        telemetry = [self._last_telem.get(did) for did in self._wp_target_ids]
        landed = bool(telemetry) and all(t is not None and not bool(getattr(t, "armed", True))
                                         for t in telemetry)
        if landed and not self._rtl_active:
            generation = self._wave_generation
            QTimer.singleShot(
                1000, lambda g=generation: self._wave_start_next_group()
                if self._wave_executing and self._wave_generation == g else None)
            self._wave_phase = "landed"
        self._wp_render_status()

    def _sec_movement(self):
        sec = AccordionSection("MOVEMENT", accent=T("cyan"), expanded=False)
        self.sf_speed = SliderField("SPEED", 0.5, 15, 3, 0.5, "m/s", 1, T("accent"))
        sec.add_widget(self.sf_speed)

        cp = rpc.command_pb2

        wrap = QWidget()
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignCenter)

        # D-pad พร้อมป้ายชื่อ
        pad = QGridLayout()
        pad.setSpacing(4)
        pad.setContentsMargins(0, 0, 0, 0)
        pad.addWidget(self._arrow_cell("▲", "FWD",
            fn_press=lambda: self._rc_press(cp.RC_DIR_FWD), primary=True), 0, 1)
        pad.addWidget(self._arrow_cell("◀", "LEFT",
            fn_press=lambda: self._rc_press(cp.RC_DIR_LEFT), primary=True), 1, 0)
        pad.addWidget(self._arrow_cell("●", "STOP",
            fn_click=self._rc_stop, primary=True), 1, 1)
        pad.addWidget(self._arrow_cell("▶", "RIGHT",
            fn_press=lambda: self._rc_press(cp.RC_DIR_RIGHT), primary=True), 1, 2)
        pad.addWidget(self._arrow_cell("▼", "BACK",
            fn_press=lambda: self._rc_press(cp.RC_DIR_BWD), primary=True), 2, 1)

        yaw = QHBoxLayout()
        yaw.setSpacing(8)
        yaw.addStretch()
        yaw.addWidget(self._arrow_cell("↺", "YAW L",
            fn_press=lambda: self._rc_press(cp.RC_DIR_YAW_L), size=40))
        yaw.addWidget(self._arrow_cell("↻", "YAW R",
            fn_press=lambda: self._rc_press(cp.RC_DIR_YAW_R), size=40))
        yaw.addStretch()

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addLayout(pad)
        left.addLayout(yaw)
        outer.addLayout(left)

        vert = QVBoxLayout()
        vert.setSpacing(4)
        vert.setAlignment(Qt.AlignCenter)
        vlab = QLabel("ALT")
        vlab.setAlignment(Qt.AlignCenter)
        vlab.setStyleSheet(section_label_qss())
        vert.addWidget(vlab)
        vert.addWidget(self._arrow_cell("⬆", "UP",
            fn_press=lambda: self._rc_press(cp.RC_DIR_UP), size=42, primary=True))
        vert.addWidget(self._arrow_cell("⬇", "DOWN",
            fn_press=lambda: self._rc_press(cp.RC_DIR_DOWN), size=42, primary=True))
        outer.addLayout(vert)

        sec.add_widget(wrap)

        self.sf_setalt = SliderField("SET ALTITUDE", 1, 120, 30, 1, "m", 0, T("accent"))
        sec.add_widget(self.sf_setalt)
        sec.add_widget(self._abtn("GO TO ALTITUDE", T("dim"), self._cmd_goalt, "ghost"))

        ll = QLabel("LEADER MODE")
        ll.setStyleSheet(section_label_qss())
        sec.add_widget(ll)

        # โหมดปัจจุบันของลำที่เลือก — เดิมมีแต่ปุ่มสั่ง ไม่มีอะไรบอกว่า "ตอนนี้อยู่โหมดอะไร"
        # ต้องไปไล่ดูที่การ์ดล่างเอง จึงเพิ่มป้ายตัวใหญ่ชัด ๆ ตรงนี้
        self.lbl_cur_mode = QLabel("MODE —")
        self.lbl_cur_mode.setAlignment(Qt.AlignCenter)
        self.lbl_cur_mode.setMinimumHeight(34)
        self._style_cur_mode(None)
        sec.add_widget(self.lbl_cur_mode)

        lrow = QHBoxLayout(); lrow.setSpacing(6)
        lrow.addWidget(self._abtn("UI GUIDED", T("dim"), lambda: self._leader_mode("GUIDED"), "ghost"))
        lrow.addWidget(self._abtn("RC LOITER", T("dim"), lambda: self._leader_mode("LOITER"), "ghost"))
        sec.add_layout(lrow)
        return sec

    def _style_cur_mode(self, mode_name):
        """ป้ายโหมดปัจจุบัน — GUIDED เน้นเขียวชัด (โหมดที่ UI สั่งงานได้เต็มที่)"""
        if not mode_name:
            self.lbl_cur_mode.setText("MODE  —")
            self.lbl_cur_mode.setStyleSheet(
                f"color:{T('faint')}; background:{rgba('#ffffff', 0.03)};"
                f" border:2px solid {rgba('#ffffff', 0.26)}; border-radius:7px;"
                f" font-size:13px; font-weight:700; letter-spacing:1px;")
            return
        color = T("green") if mode_name == "GUIDED" else (
            T("cyan") if mode_name in ("LOITER", "POSHOLD") else T("amber"))
        self.lbl_cur_mode.setText(f"MODE  {mode_name}")
        self.lbl_cur_mode.setStyleSheet(
            f"color:{color}; background:{rgba(color, 0.14)};"
            f" border:2px solid {rgba(color, 0.78)}; border-radius:7px;"
            f" font-size:15px; font-weight:800; letter-spacing:1.4px;")

    def _sec_formation(self):
        # พระเอกโหมด Swarm — เปิดค้าง + รูปขบวนชัด
        sec = AccordionSection("FORMATION / SWARM", accent=T("amber"), expanded=True)
        # หมายเหตุ: ไม่มี SCOPE (Selected/Fleet) ในโหมด Swarm แล้ว —
        # คำสั่ง swarm ทำงานกับ "ทั้งฝูง" เสมอ (core ใช้โดรน online ทุกลำ) จึงไม่จำเป็น
        pl = QLabel("PATTERN")
        pl.setStyleSheet(section_label_qss())
        sec.add_widget(pl)
        self.form_picker = FormationPicker()
        sec.add_widget(self.form_picker)
        # เข้ากับโค้ดเก่าที่อ่าน _form_group.checkedId()
        self._form_group = self.form_picker._group

        self.sf_spacing = SliderField("FORMATION SPACING", 1, 50, 12, 1, "m", 0, T("amber"))
        sec.add_widget(self.sf_spacing)
        self.sf_offset = SliderField("ALTITUDE OFFSET", 0, 30, 5, 1, "m", 0, T("amber"))
        sec.add_widget(self.sf_offset)
        self.sf_formspeed = SliderField("FORMATION SPEED", 0.5, 15, 4, 0.5, "m/s", 1, T("amber"))
        sec.add_widget(self.sf_formspeed)

        lrow = QHBoxLayout(); lrow.setSpacing(6)
        ll = QLabel("LEADER / HEAD")
        ll.setStyleSheet(section_label_qss())
        lrow.addWidget(ll)
        self.cmb_leader = QComboBox()
        self.cmb_leader.addItem("Auto")
        self.cmb_leader.setMinimumHeight(30)
        self.cmb_leader.setCursor(Qt.PointingHandCursor)
        # แบคกราวชัด + ขอบเหลืองให้เห็นเด่น (spec: leader dropdown มองเห็นง่าย)
        self.cmb_leader.setStyleSheet(
            f"QComboBox {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {rgba(T('yellow'), 0.55)}; border-radius:7px;"
            f" padding:4px 10px; font-size:12px; font-weight:700; }}"
            f"QComboBox:hover {{ border:1px solid {rgba(T('yellow'), 0.8)}; }}"
            f"QComboBox::drop-down {{ border:none; width:22px; }}"
            f"QComboBox QAbstractItemView {{ background:{T('panel2')};"
            f" border:1px solid {rgba(T('yellow'), 0.45)}; border-radius:6px;"
            f" selection-background-color:{rgba(T('yellow'), 0.35)};"
            f" color:{T('text')}; padding:4px; }}")
        self.cmb_leader.currentTextChanged.connect(self._on_leader_combo)
        lrow.addWidget(self.cmb_leader, 1)
        sec.add_layout(lrow)

        sec.add_widget(self._abtn("FORM UP", T("green"), self._swarm_start, "filled", 36))
        # spec 3 — กด Take off ในหน้า Swarm = แนบ Form up (ดึงค่า swarm) อัตโนมัติ
        sec.add_widget(self._abtn("TAKE OFF", T("amber"), self._swarm_takeoff, "filled", 38))
        shint = QLabel(
            "ปุ่มนี้จะ Form up (ดึงค่า Spacing/Formation ของ Swarm ทั้งหมด) ให้อัตโนมัติก่อน "
            "แล้วค่อยสั่งโดรนขึ้นบินตามโหมด All/Sequential ที่ตั้งไว้ด้านบน "
            "— ไม่ต้องกด Form up เองก่อน")
        shint.setWordWrap(True)
        shint.setStyleSheet(
            f"color:{T('dim')}; font-size:10px; line-height:140%;"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:5px 8px;")
        sec.add_widget(shint)
        grow = QHBoxLayout(); grow.setSpacing(6)
        grow.addWidget(self._abtn("STOP", T("red"), self._swarm_stop, "tinted"))
        grow.addWidget(self._abtn("RETURN + LAND", T("cyan"), self._swarm_return, "tinted"))
        sec.add_layout(grow)
        self.lbl_swarm = QLabel("swarm: off")
        self.lbl_swarm.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        sec.add_widget(self.lbl_swarm)
        return sec

    # ══════════════════════════════════════════════════════════
    #  PRE-FLIGHT — 2 ปุ่มที่ต้องผ่านก่อนบินจริง
    #
    #  ปุ่ม 1 «ทดสอบระบบก่อนบิน» = ตรวจอัตโนมัติ (สถานะเชื่อมต่อ/ความพร้อมรายลำ/
    #         ระยะห่าง/ค่าตั้ง) แล้วยิงคำสั่งจริงพิสูจน์ว่าสั่งโหมดแล้ว FC ตอบรับ
    #  ปุ่ม 2 «เช็คลิสต์ก่อนบินจริง» = เอา docs/REAL_FLIGHT_CHECKLIST.md ของ Codex
    #         มาติ๊กทีละข้อ (ข้อที่ต้องใช้ตาคนดู เครื่องตรวจแทนไม่ได้)
    #  ถ้ายังไม่ผ่านทั้ง 2 → ป้ายแดงบน topbar + ถามก่อนทุกครั้งที่สั่ง TAKEOFF
    # ══════════════════════════════════════════════════════════
    def _sec_preflight(self):
        sec = AccordionSection("PRE-FLIGHT TEST", accent=T("amber"), expanded=True)

        self.lbl_preflight = QLabel("")
        self.lbl_preflight.setWordWrap(True)
        sec.add_widget(self.lbl_preflight)

        # ปุ่มคู่ในแถวเดียว — เตี้ยและกะทัดรัดเหมือนปุ่มคำสั่งอื่นในแผงนี้
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_pf_test = self._abtn("SYSTEM TEST", T("green"),
                                      self._open_preflight_test, "tinted", 30)
        self.btn_pf_test.setToolTip(
            "ไล่ตรวจเป็นรายการ: core/mTLS/token · telemetry สด · GPS/แบต/อยู่บนพื้น ·\n"
            "ระยะห่างกันชน · ค่า TAKEOFF/RTL/Geofence/หัวขบวน ·\n"
            "แล้วยิงคำสั่งจริง (HOLD + สั่งโหมด GUIDED) ดูว่า FC ตอบรับกลับมาจริงไหม")
        row.addWidget(self.btn_pf_test, 1)
        self.btn_pf_list = self._abtn("CHECKLIST", T("cyan"),
                                      self._open_preflight_checklist, "tinted", 30)
        self.btn_pf_list.setToolTip(
            "เช็คลิสต์จาก docs/REAL_FLIGHT_CHECKLIST.md — ติ๊กตามที่ตรวจจริงในสนาม")
        row.addWidget(self.btn_pf_list, 1)
        sec.add_layout(row)

        # สถานะสองบรรทัด อ่านคู่กับปุ่มด้านบน (ซ้าย = TEST, ขวา = CHECKLIST)
        self.lbl_pf_test = QLabel("")
        self.lbl_pf_test.setWordWrap(True)
        self.lbl_pf_test.setStyleSheet(
            f"color:{T('faint')}; font-size:9px; font-family:{FONT_MONO};")
        sec.add_widget(self.lbl_pf_test)
        self.lbl_pf_list = QLabel("")
        self.lbl_pf_list.setWordWrap(True)
        self.lbl_pf_list.setStyleSheet(
            f"color:{T('faint')}; font-size:9px; font-family:{FONT_MONO};")
        sec.add_widget(self.lbl_pf_list)
        return sec

    def _focus_preflight_section(self):
        """กดป้าย PREFLIGHT ข้าง ONLINE → กางหมวด PRE-FLIGHT แล้วเลื่อนไปให้เห็น"""
        if hasattr(self, "sec_preflight"):
            self.sec_preflight.setExpanded(True)
            if hasattr(self, "_cmd_scroll"):
                self._cmd_scroll.ensureWidgetVisible(self.sec_preflight)
        if self._preflight.ready():
            self._show_toast("ทดสอบก่อนบินผ่านแล้ว", "ok")
        else:
            self._show_toast("ยังเทสก่อนบินไม่ครบ · ดูหมวด PRE-FLIGHT", "info")

    # ── ข้อมูลที่ป้อนให้ชุดทดสอบ ──
    def _preflight_target_ids(self):
        """ลำที่จะเอาไปตรวจ = ลำที่เลือกไว้ ไม่งั้นลำที่ยังต่ออยู่"""
        ids = self._selected_or_all() or self._connected_ids()
        return [int(d) for d in ids]

    def _preflight_snapshot(self):
        """สรุปสถานะ cockpit ให้ core/preflight.py ตรวจ (ไม่ยิง RPC ในนี้)"""
        now = time.monotonic()
        ids = self._preflight_target_ids()
        drones = []
        for did in ids:
            seen = self._last_seen.get(did, 0.0)
            age = (now - seen) if seen else 999.0
            t = self._last_telem.get(did)
            row = {"id": did, "name": self._drone_name(did), "age_s": age,
                   "home_set": did in self._home_pos}
            if t is None:
                row.update({"link_quality": 0, "gps_fix": 0, "sats": 0,
                            "batt_pct": 0.0, "armed": False, "alt": 0.0,
                            "lat": 0.0, "lon": 0.0, "mode": ""})
            else:
                p = t.position
                row.update({
                    "link_quality": int(getattr(t, "link_quality", 0)),
                    "gps_fix": int(getattr(t, "gps_fix", 0)),
                    "sats": int(getattr(t, "sat_count", 0)),
                    "batt_pct": float(getattr(t, "battery_pct", 0.0)),
                    "armed": bool(getattr(t, "armed", False)),
                    "alt": float(getattr(p, "alt_rel", 0.0)),
                    "lat": float(getattr(p, "lat", 0.0)),
                    "lon": float(getattr(p, "lon", 0.0)),
                    "mode": rpc.mode_name(getattr(t, "mode", 0)),
                })
            drones.append(row)

        target = set(ids)
        pos = {d: v for d, v in self._collision_positions().items() if d in target}
        pairs = [(p.a, p.b, p.dist, p.level) for p in swarm_logic.detect_collisions(
            pos, critical_m=self._collision_crit, warn_m=self._collision_warn)]

        routes = self._wp_all_routes()
        conflicts = []
        if routes and self._wp_separate:
            try:
                conflicts = [c.describe() for c in self._wp_check_conflicts(routes)]
            except Exception:
                conflicts = []

        return {
            "profile": os.getenv("SWARMGOD_PROFILE", ""),
            "token": bool(os.getenv("SWARMGOD_TOKEN", "").strip()),
            "home_loc": bool(os.getenv("SWARMGOD_HOME_LOC", "").strip()),
            "mtls": bool(getattr(self.client, "secure", False)),
            "ui_mode": bool(self._ui_mode),
            "drones": drones,
            "close_pairs": pairs,
            "min_sep": float(self._collision_warn),
            "takeoff_alt": float(self.sf_takeoff.value()),
            "rtl_base": float(getattr(self, "RTL_BASE_ALT", 15.0)),
            "rtl_gap": float(getattr(self, "RTL_LAYER_GAP", 5.0)),
            "geofence_points": len(getattr(self, "_fence_points", []) or []),
            "head_id": int(self._head_id or 0),
            "waypoint_planned": bool(routes),
            "waypoint_conflicts": conflicts,
        }

    def _preflight_telem(self, did):
        """สถานะล่าสุดของลำนั้น — ชุดทดสอบใช้รอยืนยันว่า FC ตอบรับคำสั่งจริง"""
        t = self._last_telem.get(int(did))
        if t is None:
            return {}
        return {"mode_name": rpc.mode_name(getattr(t, "mode", 0)),
                "armed": bool(getattr(t, "armed", False)),
                "alt": float(getattr(t.position, "alt_rel", 0.0))}

    # ── ปุ่มที่ 1: ทดสอบระบบ ──
    def _open_preflight_test(self):
        ids = self._preflight_target_ids()
        if not ids:
            self._show_toast("ยังไม่มีโดรนให้ทดสอบ · เชื่อมต่อหรือเลือกลำก่อน", "err")
        dlg = PreflightDialog(
            self, client=self.client, snapshot_fn=self._preflight_snapshot,
            telem_fn=self._preflight_telem, target_ids=ids,
            log_fn=lambda m: self._log(m, category="COMMAND"),
            dispatch_fn=lambda label, invoke, targets: self._dispatch_core(
                label, invoke, source="preflight", targets=targets,
                enforce_dedup=False))
        dlg.exec_()
        if dlg.summary.total:
            self._preflight.mark_selftest(dlg.passed, dlg.summary.text())
            self._log(
                f"PRE-FLIGHT TEST: {'ผ่าน' if dlg.passed else 'ไม่ผ่าน'} · "
                f"{dlg.summary.text()}",
                category="COMMAND",
                severity="SUCCESS" if dlg.passed else "ERROR")
            self._show_toast(
                "ทดสอบก่อนบินผ่าน" if dlg.passed else "ทดสอบก่อนบินไม่ผ่าน",
                "ok" if dlg.passed else "err")
            if not dlg.passed:
                self._show_banner(
                    "PRE-FLIGHT TEST ไม่ผ่าน — แก้ข้อที่ตกก่อนบิน", T("red"))
        self._refresh_preflight_ui()
        return self._preflight.selftest_ready()

    # ── ปุ่มที่ 2: เช็คลิสต์ของ Codex ──
    def _open_preflight_checklist(self):
        try:
            dlg = ChecklistDialog(self, log_fn=lambda m: self._log(m, category="COMMAND"))
        except Exception as exc:
            self._log(f"CHECKLIST ERROR: {exc}", category="COMMAND", severity="ERROR")
            self._show_toast(f"เปิด Checklist ไม่สำเร็จ: {exc}", "err")
            return False
        if dlg.total_n == 0:
            self._show_toast("อ่าน docs/REAL_FLIGHT_CHECKLIST.md ไม่ได้", "err")
        dlg.exec_()
        self._preflight.mark_checklist(dlg.completed, f"{dlg.done_n}/{dlg.total_n}")
        self._log(f"PRE-FLIGHT CHECKLIST: ติ๊กแล้ว {dlg.done_n}/{dlg.total_n} ข้อ"
                  + ("" if dlg.completed else " (ยังไม่ครบ)"),
                  category="COMMAND",
                  severity="SUCCESS" if dlg.completed else "WARNING")
        self._show_toast(
            "เช็คลิสต์ครบทุกข้อ" if dlg.completed
            else f"เช็คลิสต์ {dlg.done_n}/{dlg.total_n} ยังไม่ครบ",
            "ok" if dlg.completed else "info")
        self._refresh_preflight_ui()
        return self._preflight.checklist_ready()

    # ── ป้ายสถานะ (topbar + ในหมวด) ──
    def _refresh_preflight_ui(self):
        if not hasattr(self, "btn_preflight"):
            return
        text, level = self._preflight.badge()
        color = {"ok": T("green"), "warn": T("amber"), "bad": T("red")}[level]
        if getattr(self, "_pf_badge_cache", None) != (text, level):
            self._pf_badge_cache = (text, level)
            self.btn_preflight.setText(text)
            # ทรงเดียวกับป้าย CORE/LINK (ดู _pill compact) — ต่างแค่กดได้
            self.btn_preflight.setStyleSheet(
                f"QPushButton {{ color:{color}; background:{rgba(color, 0.14)};"
                f" border:none; border-radius:6px;"
                f" padding:2px 6px; font-size:9px; font-weight:700; }}"
                f"QPushButton:hover {{ background:{rgba(color, 0.26)}; }}")
            self.btn_preflight.setToolTip(
                "ทดสอบก่อนบินครบแล้ว — กดเพื่อดู/ทดสอบซ้ำ" if level == "ok"
                else "ยังเทสก่อนบินไม่ครบ:\n"
                     + "\n".join("• " + m for m in self._preflight.missing())
                     + "\n\nกดเพื่อไปที่หมวด PRE-FLIGHT")
        self._sync_preflight_warning(level)

        if not hasattr(self, "lbl_preflight"):
            return
        missing = self._preflight.missing()
        if missing:
            msg = "⚠ " + " · ".join(missing)
            c = T("red") if len(missing) == 2 else T("amber")
        else:
            msg = "✓ พร้อมบิน — TAKEOFF จะไม่ถูกถามซ้ำ"
            c = T("green")
        # ตัวนี้ถูกเรียกทุกวินาทีจากนาฬิกา — ทาสีใหม่เฉพาะตอนข้อความเปลี่ยนจริง
        if getattr(self, "_pf_label_cache", None) != msg:
            self._pf_label_cache = msg
            self.lbl_preflight.setText(msg)
            self.lbl_preflight.setStyleSheet(
                f"color:{c}; font-size:10px; font-weight:600; background:{rgba(c, 0.10)};"
                f" border:1px solid {rgba(c, 0.28)}; border-radius:6px; padding:5px 8px;")

        # บรรทัดสถานะสั้น ๆ — รายละเอียดเต็มอยู่ใน tooltip กับใน popup
        st = self._preflight.selftest
        self.lbl_pf_test.setText(
            "TEST      — ยังไม่เคยรัน" if st.ts <= 0 else
            "TEST      — %s · %s" % ("PASS" if st.ok else "FAIL",
                                     preflight.age_text(st.ts)))
        self.lbl_pf_test.setToolTip(st.detail or "ยังไม่เคยรัน")
        cl = self._preflight.checklist
        self.lbl_pf_list.setText(
            "CHECKLIST — ยังไม่เคยเปิด" if cl.ts <= 0 else
            "CHECKLIST — %s%s · %s" % (cl.detail, "" if cl.ok else " (ไม่ครบ)",
                                       preflight.age_text(cl.ts)))

    # ── ด่านก่อนสั่งขึ้นบิน ──
    def _preflight_gate(self, action="TAKEOFF"):
        """คืน True = สั่งต่อได้

        ยังไม่ได้เทส → ถามก่อน: «ทดสอบก่อน» / «ไม่เทส บินเลย» / «ยกเลิก»
        เลือกทดสอบ = เปิดชุดทดสอบให้ทันที (และเช็คลิสต์ถ้ายังไม่ได้กรอก)
        """
        if self._preflight.ready():
            return True
        missing = self._preflight.missing()
        self._log("PRE-FLIGHT: สั่ง " + action + " ทั้งที่ยังไม่ได้เทส — ถามผู้ใช้ก่อน "
                  + " / ".join(missing), category="ALERT", severity="WARNING")
        # กล่องนี้ก็ modal เหมือนกัน — แท็บเล็ตต้องเห็นว่าค้างรอคนตอบอยู่ที่คอม
        self.field_hub.set_extra("dialog", {
            "title": "ยังไม่ได้ทดสอบก่อนบิน",
            "text": "กำลังจะสั่ง %s แต่ยังไม่ได้ทดสอบก่อนบิน: %s"
                    % (action, " / ".join(missing))})
        try:
            choice = ask_before_takeoff(self, missing, action)
        finally:
            self.field_hub.set_extra("dialog", None)

        if choice == "cancel":
            self._log(f"ยกเลิก {action} — ผู้ใช้เลือกไม่บินก่อนเทส", category="COMMAND")
            self._show_toast(f"ยกเลิก {action}", "info")
            return False

        if choice == "test":
            if not self._preflight.selftest_ready():
                self._open_preflight_test()
            if not self._preflight.checklist_ready():
                self._open_preflight_checklist()
            if self._preflight.ready():
                self._show_toast(f"เทสผ่านแล้ว · สั่ง {action} ต่อ", "ok")
                return True
            still = self._preflight.missing()
            if not self._confirm(
                    self, f"เทสยังไม่ผ่าน — จะ {action} ต่อไหม?",
                    "ยังเหลือ:\n" + "\n".join("• " + m for m in still)
                    + f"\n\nสั่ง {action} ต่อทั้งที่ยังไม่ผ่าน?",
                    ok_text=f"{action} ต่อ", danger=True):
                self._show_toast(f"ยกเลิก {action}", "info")
                return False

        # ถึงตรงนี้ = ผู้ใช้ยืนยันบินทั้งที่ยังไม่ผ่าน — ต้องเห็นชัดว่าข้ามด่านไปแล้ว
        self._preflight.mark_bypass()
        self._log(f"⚠ {action} โดยข้ามการทดสอบก่อนบิน (ครั้งที่ "
                  f"{self._preflight.bypass_count}) — " + " / ".join(missing),
                  category="COMMAND", severity="ERROR")
        self._show_banner(
            f"{action} โดยยังไม่ได้ทดสอบก่อนบิน — ผู้ควบคุมรับความเสี่ยงเอง", T("red"))
        self._show_toast("ข้ามการเทสก่อนบิน", "err")
        self._refresh_preflight_ui()
        return True

    def _preflight_soft_warn(self, action):
        """เตือนเฉย ๆ ไม่บล็อก (ใช้กับ ARM ที่ยังไม่ใช่การขึ้นบิน)"""
        if self._preflight.ready():
            return
        self._log(f"⚠ {action} ทั้งที่ยังไม่ได้ทดสอบก่อนบิน — "
                  + " / ".join(self._preflight.missing()),
                  category="ALERT", severity="WARNING")
        self._show_banner("ยังไม่ได้ทดสอบก่อนบิน — กด SYSTEM TEST ที่เมนู PRE-FLIGHT TEST", T("amber"))

    def _sec_testing(self):
        """หมวดเครื่องมือทดลอง — ใช้กับ SITL เท่านั้น

        แยกหมวดและใส่คำเตือนไว้ชัด ๆ เพราะคำสั่งในนี้แก้พารามิเตอร์ของ FC
        ถ้าเผลอไปกดตอนต่อโดรนจริง ผลจะไม่ใช่แค่ "ไม่มีอะไรเกิดขึ้น"
        (core บล็อกให้อีกชั้นถ้า armed อยู่ แต่ไม่ควรพึ่งด่านเดียว)
        """
        sec = AccordionSection("โหมดทดลอง (SITL)", accent=T("amber"), expanded=False)

        warn = QLabel("⚠ ใช้กับ SITL เท่านั้น — คำสั่งในหมวดนี้แก้พารามิเตอร์ที่ FC")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            f"color:{T('amber')}; font-size:11px;"
            f" background:{rgba(T('amber'), 0.10)};"
            f" border:1px solid {rgba(T('amber'), 0.28)};"
            f" border-radius:6px; padding:6px 9px;")
        sec.add_widget(warn)

        self.sf_sim_batt = SliderField("แรงดันแบตจำลอง", 9.0, 16.8, 12.6, 0.1,
                                       "V", 1, T("green"))
        sec.add_widget(self.sf_sim_batt)

        sec.add_widget(self._abtn("🔋 รีเซ็ตแบตเตอรี่ (SITL)", T("green"),
                                  self._reset_sim_battery, "tinted", 34))

        hint = QLabel("SITL จำลองแบตไหลลงเรื่อย ๆ จนติด safety gate แล้ว arm ไม่ได้ "
                      "ปุ่มนี้ตั้ง SIM_BATT_VOLTAGE กลับเป็นค่าเต็มให้ทดลองต่อได้ "
                      "(ต้อง disarm ก่อน)")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        sec.add_widget(hint)
        return sec

    def _reset_sim_battery(self):
        """ตั้ง SIM_BATT_VOLTAGE กลับเป็นค่าเต็ม — ใช้ทดลองกับ SITL"""
        ids = self._selected_or_all()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกโดรน", "err")
            return
        volts, accepted = QInputDialog.getDouble(
            self, "รีเซ็ตแบตเตอรี่จำลอง", "แรงดันหลังรีเซ็ต (SITL เท่านั้น):",
            float(getattr(self, "_sim_batt_voltage", 12.6)), 9.0, 16.8, 1)
        if not accepted:
            return
        self._sim_batt_voltage = float(volts)
        if not self._confirm(
                self, "รีเซ็ตแบตเตอรี่จำลอง",
                f"ตั้ง SIM_BATT_VOLTAGE = {volts:.1f} V ให้ {len(ids)} ลำ\n\n"
                "ใช้ได้กับ SITL เท่านั้น — ถ้ากำลังต่อโดรนจริงอยู่ "
                "คำสั่งนี้จะไปแก้พารามิเตอร์ที่ FC จริง",
                ok_text="รีเซ็ตแบต"):
            return
        self._log(f"รีเซ็ตแบตจำลอง → {volts:.1f} V ({len(ids)} ลำ)", category="COMMAND")

        def worker():
            ok = 0
            for d in ids:
                try:
                    r = self._dispatch_core(
                        "PARAM SET SIM_BATT_VOLTAGE", lambda d=d: self.client.param_set(
                            int(d), "SIM_BATT_VOLTAGE", volts),
                        source="sitl-admin", targets=[int(d)], enforce_dedup=False)
                    if getattr(r, "ok", False):
                        ok += 1
                    else:
                        self.cmd_result.emit(
                            f"รีเซ็ตแบต D{d}: {getattr(r, 'message', 'ล้มเหลว')}")
                except Exception as e:
                    self.cmd_result.emit(f"รีเซ็ตแบต D{d}: ERROR {e}")
            self.cmd_result.emit(f"รีเซ็ตแบตจำลอง: สำเร็จ {ok}/{len(ids)} ลำ")
        threading.Thread(target=worker, daemon=True).start()

    def _sec_safety(self):
        sec = AccordionSection("SAFETY & SYSTEM", accent=T("red"), expanded=False)
        self.sf_maxdrones = SliderField("MAX DRONES LIMIT", 1, 255, 20, 1, "drones", 0, T("dim"))
        sec.add_widget(self.sf_maxdrones)

        # ── RTL — ชั้นความสูงกันชนตอนกลับฐาน ──
        rl = QLabel("RTL — ชั้นความสูงกันชน")
        rl.setStyleSheet(section_label_qss())
        sec.add_widget(rl)
        self.sf_rtl_alt = SliderField("RETURN BASE ALTITUDE (เหนือ home)",
                                      15, 120, 15, 1, "m", 0, T("cyan"))
        sec.add_widget(self.sf_rtl_alt)
        self.sf_rtl_gap = SliderField("RETURN MIN GAP (core clamp ตาม MinSeparation)",
                                      5, 20, 5, 1, "m", 0, T("cyan"))
        sec.add_widget(self.sf_rtl_gap)
        self.lbl_rtl_preview = QLabel("")
        self.lbl_rtl_preview.setWordWrap(True)
        self.lbl_rtl_preview.setStyleSheet(
            f"color:{T('dim')}; font-size:10px; font-family:{FONT_MONO};"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:5px 8px;")
        sec.add_widget(self.lbl_rtl_preview)
        self.sf_rtl_alt.valueChanged.connect(lambda _: self._update_rtl_preview())
        self.sf_rtl_gap.valueChanged.connect(lambda _: self._update_rtl_preview())

        # ปุ่มบันทึก + นำค่าไปใช้ทันที
        btn_save = self._abtn("💾 SAVE SYSTEM SETTINGS", T("green"),
                              self._apply_and_save_system, "tinted", 34)
        sec.add_widget(btn_save)

        # spec 4 — กดแล้วมีเมนูเลือกว่าจะหยุดลำไหน / ALL (ALL ต้องยืนยันซ้ำ)
        self.btn_estop_all = self._abtn("■ EMERGENCY STOP ▾", T("red"),
                                        self._emergency_menu, "filled", 40)
        sec.add_widget(self.btn_estop_all)
        # เอา HOLD ALL / RTL ALL / DISARM ALL ออก — ซ้ำซ้อนกับหมวด FLIGHT
        # สั่งได้จาก FLIGHT (ตามลำที่เลือก) หรือ Quick Actions บนการ์ดโดรน
        hint = QLabel("คำสั่ง HOLD / RTL / DISARM สั่งได้ที่หมวด FLIGHT "
                      "(ตามลำที่เลือก) หรือปุ่มบนการ์ดโดรนฝั่งซ้าย")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
        sec.add_widget(hint)
        return sec

    def _rtl_preview_text(self, base_alt=None, gap=None):
        """ตัวอย่างชั้น RETURN แบบ absolute ที่ core จะตรวจทั้งชุดก่อนเริ่ม."""
        base_alt = float(
            base_alt if base_alt is not None else getattr(self, "RTL_BASE_ALT", 15.0))
        gap = float(gap if gap is not None else getattr(self, "RTL_LAYER_GAP", 5.0))
        cur = {d: float(self._last_alt.get(d, 0.0))
               for d in sorted(self.fleet_items)} or {1: 10.0, 2: 12.0}
        layers = swarm_logic.plan_rtl_layers(cur, base_alt=base_alt, layer_gap=gap)
        if not layers:
            return f"base {base_alt:.0f}m · gap {gap:.0f}m"
        parts = [f"D{l.drone_id} {cur.get(l.drone_id, 0):.0f}→{l.alt:.0f}m"
                 for l in layers[:6]]
        return f"base {base_alt:.0f}m · gap {gap:.0f}m · " + " · ".join(parts)

    def _update_rtl_preview(self):
        if hasattr(self, "lbl_rtl_preview"):
            self.lbl_rtl_preview.setText(self._rtl_preview_text(
                self.sf_rtl_alt.value(), self.sf_rtl_gap.value()))

    def _apply_and_save_system(self):
        """อ่านค่าจาก slider แล้วอัปเดตค่าใน runtime + บันทึกลงไฟล์"""
        if hasattr(self, 'sf_rtl_alt'):
            self.RTL_BASE_ALT = float(self.sf_rtl_alt.value())
        if hasattr(self, 'sf_rtl_gap'):
            self.RTL_LAYER_GAP = float(self.sf_rtl_gap.value())
        self._update_rtl_preview()
        self._log(f"RTL config → ชั้นล่างสุด {self.RTL_BASE_ALT:.0f} m, "
                  f"ห่างชั้นละ {self.RTL_LAYER_GAP:.0f} m", category="COMMAND",
                  severity="SUCCESS")
        self._summ_set("rtlcfg", "RTL CONFIG",
                       f"base {self.RTL_BASE_ALT:.0f}m · gap {self.RTL_LAYER_GAP:.0f}m")
        self._show_toast(
            f"บันทึกแล้ว · RTL {self.RTL_BASE_ALT:.0f}m ห่างชั้นละ {self.RTL_LAYER_GAP:.0f}m",
            "ok")
        self._save_settings()

    def _sec_cv_track(self):
        sec = AccordionSection("CV TRACK", accent=T("cyan"), expanded=False)
        self.cv_panel = CvTrackPanel()
        self.cv_panel.logged.connect(
            lambda msg, sev: self._log(msg, category="TELEMETRY",
                                       severity={"info": "INFO", "warn": "WARNING",
                                                 "success": "SUCCESS", "error": "ERROR"}.get(sev, "INFO")))
        self.cv_panel.track_update.connect(self._on_cv_track)
        sec.add_widget(self.cv_panel)
        return sec

    def _on_cv_track(self, state):
        # เก็บ offset ล่าสุดไว้ใช้ต่อเมื่อมี gimbal/follow ในเฟสถัดไป
        self._cv_last = state

    def _sec_geofence(self):
        sec = AccordionSection("GEOFENCE / MAP", accent=T("dim"), expanded=False)
        tl = QLabel("DRAW TOOL")
        tl.setStyleSheet(section_label_qss())
        sec.add_widget(tl)

        # ปุ่มเป็นไอคอนเรขาคณิตล้วน — ชื่อเครื่องมือโผล่เป็น tooltip ตอน hover
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._draw_tool = "none"
        self._draw_btns = {}
        for key, shape, tip in (
            ("none", "target", "GOTO — คลิกแผนที่เพื่อสั่งบินไปจุดนั้น (ปิดโหมดวาด)"),
            ("poly", "polygon", "POLY — วาดรั้วหลายเหลี่ยม (คลิกหลายจุด)"),
            ("rect", "square", "RECT — วาดรั้วสี่เหลี่ยม (คลิก 2 จุด)"),
            ("circle", "circle", "CIRCLE — วาดรั้ววงกลม (คลิก 2 จุด)"),
        ):
            b = self._icon_btn(shape, tip,
                               lambda _=False, k=key: self._set_draw_tool(k),
                               color=T("dim"), checkable=True)
            self._draw_btns[key] = b
            tools.addWidget(b)
        tools.addStretch(1)
        sec.add_layout(tools)
        self._refresh_draw_btns()

        hint = QLabel("POLY คลิกหลายจุด · RECT/CIRCLE คลิก 2 จุด · แล้วกด SET")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        sec.add_widget(hint)

        grow = QHBoxLayout(); grow.setSpacing(6)
        grow.addWidget(self._abtn("SET FENCE", T("green"), self._fence_set, "tinted"))
        grow.addWidget(self._abtn("CLEAR", T("red"), self._fence_clear, "tinted"))
        sec.add_layout(grow)
        gps_row = QHBoxLayout(); gps_row.setSpacing(6)
        gps_row.addWidget(self._abtn("ไปที่ GPS", T("cyan"), self._goto_drone_gps, "tinted"))
        gps_row.addWidget(self._abtn("ใส่พิกัด", T("dim"), self._goto_typed_gps, "ghost"))
        sec.add_layout(gps_row)
        sec.add_widget(self._abtn("CACHE AREA", T("dim"), self._cache_area, "ghost"))
        cache_hint = QLabel(
            "ต่อโดรนแล้วแผนที่กระโดดตาม GPS เอง · CACHE AREA โหลดเฉพาะพื้นที่บนจอ "
            "(ไม่กี่ MB) ไว้ใช้ตอนไม่มีเน็ต · 3D ไม่เปิดเอง ต้องกดปุ่ม 3D")
        cache_hint.setWordWrap(True)
        cache_hint.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        sec.add_widget(cache_hint)
        # เก็บ alias ให้โค้ดเก่าที่อ้าง btn_fence ยังไม่พัง
        self.btn_fence = self._draw_btns.get("poly")
        return sec

    # ══════════════════════════════════════════════════════════
    #  TACTICAL PLANNING — วาดแผนที่ยุทธวิธี (แยกจาก Geofence)
    # ══════════════════════════════════════════════════════════
    def _sec_tactical(self):
        sec = AccordionSection("TACTICAL PLANNING", accent=T("cyan"), expanded=False)
        note = QLabel("วางแผนอย่างเดียว — ไม่ใช่เขตห้ามบิน (Geofence แยกอีกหมวด)")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
        sec.add_widget(note)

        tl = QLabel("DRAW TOOL")
        tl.setStyleSheet(section_label_qss())
        sec.add_widget(tl)

        # ไอคอนเรขาคณิตล้วน — ชื่อเครื่องมือโผล่เป็น tooltip ตอน hover
        self._tac_btns = {}
        trow = QHBoxLayout()
        trow.setSpacing(6)
        tools = [
            ("none", "cross", "ปิดเครื่องมือวาด"),
            ("line", "polyline", "เส้น — คลิกหลายจุด แล้วดับเบิลคลิก/กดเสร็จ"),
            ("polygon", "polygon", "รูปหลายเหลี่ยม — คลิก ≥3 จุด แล้วดับเบิลคลิก/กดเสร็จ"),
            ("circle", "circle", "วงกลม — คลิกจุดศูนย์กลาง แล้วคลิกกำหนดรัศมี"),
            ("text", "text", "ข้อความ — พิมพ์ข้อความแล้วคลิกวางบนแผนที่"),
            ("icon", "image", "สัญลักษณ์ — วางรูปที่อัปโหลดไว้บนแผนที่"),
        ]
        for key, shape, tip in tools:
            b = self._icon_btn(shape, tip,
                               lambda _=False, k=key: self._set_tac_tool(k),
                               color=T("cyan"), checkable=True, size=32)
            self._tac_btns[key] = b
            trow.addWidget(b)
        trow.addStretch(1)
        sec.add_layout(trow)

        hint = QLabel("เส้น/รูปหลายเหลี่ยม: คลิกหลายจุด แล้ว ดับเบิลคลิก หรือกด เสร็จ\n"
                      "วงกลม: คลิกจุดศูนย์กลาง แล้วคลิกกำหนดรัศมี")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
        sec.add_widget(hint)

        row = QHBoxLayout(); row.setSpacing(5)
        row.addWidget(self._abtn("เสร็จ", T("green"), self._tac_finish, "tinted"))
        row.addWidget(self._abtn("ย้อนกลับ", T("dim"), self._tac_undo, "ghost"))
        row.addWidget(self._abtn("ล้าง", T("red"), self._tac_clear, "tinted"))
        sec.add_layout(row)

        il = QLabel("สัญลักษณ์ยุทธวิธี (Custom Icons)")
        il.setStyleSheet(section_label_qss())
        sec.add_widget(il)
        self.cmb_tac_icon = QComboBox()
        self.cmb_tac_icon.addItem("— ยังไม่มีสัญลักษณ์ —")
        self.cmb_tac_icon.setMinimumHeight(28)
        self.cmb_tac_icon.currentTextChanged.connect(self._on_tac_icon_picked)
        sec.add_widget(self.cmb_tac_icon)
        sec.add_widget(self._abtn("⬆ อัปโหลดรูปสัญลักษณ์…", T("cyan"),
                                  self._tac_upload_icon, "tinted"))

        pl = QLabel("แผนยุทธวิธี (GeoJSON)")
        pl.setStyleSheet(section_label_qss())
        sec.add_widget(pl)
        prow = QHBoxLayout(); prow.setSpacing(5)
        prow.addWidget(self._abtn("💾 SAVE", T("green"), self._tac_save, "tinted"))
        prow.addWidget(self._abtn("📂 LOAD", T("amber"), self._tac_load, "tinted"))
        sec.add_layout(prow)
        self.lbl_tac_status = QLabel("ยังไม่ได้วาดอะไร")
        self.lbl_tac_status.setWordWrap(True)
        self.lbl_tac_status.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        sec.add_widget(self.lbl_tac_status)
        self._refresh_tac_btns()
        return sec

    def _refresh_tac_btns(self):
        for k, b in self._tac_btns.items():
            self._set_icon_btn_state(b, k == self._tac_tool)

    def _set_tac_tool(self, tool):
        if tool == "text":
            txt, ok = QInputDialog.getText(self, "ใส่ข้อความบนแผนที่",
                                           "ข้อความที่จะวาง:")
            if not ok or not txt.strip():
                self._refresh_tac_btns()
                return
            self._js(f"setTacticalText({txt.strip()!r})")
        if tool == "icon" and not self._tac_icons:
            self._show_toast("ยังไม่มีสัญลักษณ์ · อัปโหลดรูปก่อน", "err")
            self._refresh_tac_btns()
            return
        self._tac_tool = tool
        # หยิบเครื่องมือยุทธวิธี = คลิกแผนที่ไม่ใช่คำสั่งบินแล้ว → ล็อกให้ตรงกับ JS
        if tool != "none" and getattr(self, "_goto_armed", False):
            self._set_goto_armed(False, reason=" (เลือกเครื่องมือยุทธวิธี)")
        self._js(f"setTacticalTool({tool!r})")
        self._refresh_tac_btns()
        # เลือกเครื่องมือวาดยุทธวิธี → ปิดโหมด Waypoint (คนละระบบ ใช้พร้อมกันไม่ได้)
        if tool != "none" and self._waypoint_mode:
            self._wp_toggle(False)
        names = {"none": "ปิดเครื่องมือวาด", "line": "วาดเส้น",
                 "polygon": "วาดรูปหลายเหลี่ยม", "circle": "วาดวงกลม",
                 "text": "วางข้อความ", "icon": "วางสัญลักษณ์"}
        self._log(f"tactical tool → {names.get(tool, tool)}", category="STATUS")

    def _tac_finish(self):
        self._js("finishTactical()")
        self._tac_refresh_count()

    def _tac_undo(self):
        self._js("undoTactical()")
        self._tac_refresh_count()

    def _tac_clear(self):
        if not self._confirm(self, "ล้างแผนยุทธวิธี",
                                   "ลบสิ่งที่วาดไว้ทั้งหมดบนแผนที่ใช่หรือไม่?",
                                   ok_text="ล้างทั้งหมด", danger=True):
            return
        self._js("clearTactical()")
        self._set_tac_tool("none")
        self.lbl_tac_status.setText("ล้างแผนแล้ว")
        self._log("ล้างแผนยุทธวิธี", category="COMMAND")

    def _tac_refresh_count(self):
        if not self._map_enabled or self.web is None:
            return
        self.web.page().runJavaScript(
            "tacticalCount()",
            lambda n: self.lbl_tac_status.setText(f"วาดไว้ {int(n or 0)} ชิ้น"))

    def _tac_upload_icon(self):
        """อัปโหลดรูปสัญลักษณ์ → ฝังเป็น data URL (อยู่ในหน่วยความจำชั่วคราว)"""
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกรูปสัญลักษณ์", os.path.expanduser("~"),
            "รูปภาพ (*.png *.jpg *.jpeg *.svg *.gif);;ทุกไฟล์ (*)")
        if not path:
            return
        try:
            data_url, name = self._icon_data_url(path)
        except Exception as e:
            self._show_toast(f"อัปโหลดไม่สำเร็จ: {e}", "err")
            return
        self._tac_icons[name] = path
        self._js(f"addTacticalIcon({name!r}, {data_url!r})")
        # เติมใน dropdown
        if self.cmb_tac_icon.itemText(0).startswith("—"):
            self.cmb_tac_icon.clear()
        if self.cmb_tac_icon.findText(name) < 0:
            self.cmb_tac_icon.addItem(name)
        self.cmb_tac_icon.setCurrentText(name)
        self._show_toast(f"เพิ่มสัญลักษณ์ '{name}' แล้ว · คลิกบนแผนที่เพื่อวาง", "ok")
        self._log(f"อัปโหลดสัญลักษณ์: {name}", category="STATUS")
        self._set_tac_tool("icon")

    @staticmethod
    def _icon_data_url(path):
        """อ่านไฟล์รูป → (data URL, ชื่อ) ; จำกัดขนาดกันไฟล์ใหญ่เกิน"""
        import base64
        import mimetypes
        size = os.path.getsize(path)
        if size > 2 * 1024 * 1024:
            raise ValueError("ไฟล์ใหญ่เกิน 2 MB")
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        name = os.path.splitext(os.path.basename(path))[0][:40]
        return f"data:{mime};base64,{b64}", name

    def _on_tac_icon_picked(self, name):
        name = (name or "").strip()
        if not name or name.startswith("—"):
            return
        self._js(f"useTacticalIcon({name!r})")
        self._tac_tool = "icon"
        self._refresh_tac_btns()

    def _tac_save(self):
        if not self._map_enabled or self.web is None:
            self._show_toast("SAVE PLAN: ต้องใช้แผนที่", "err")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "บันทึกแผนยุทธวิธี",
            os.path.join(os.path.expanduser("~"), "tactical_plan.geojson"),
            "GeoJSON (*.geojson);;JSON (*.json);;ทุกไฟล์ (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".geojson"
        self.web.page().runJavaScript(
            "getTacticalGeoJSON()", lambda js: self._tac_write(path, js))

    def _tac_write(self, path, js):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(js or "{}")
            import json as _json
            n = len((_json.loads(js or "{}") or {}).get("features", []))
            self._show_toast(f"บันทึกแผนแล้ว ({n} ชิ้น)", "ok")
            self._log(f"SAVE tactical plan → {path} ({n} ชิ้น)", category="STATUS")
            self.lbl_tac_status.setText(f"บันทึกแล้ว {n} ชิ้น")
        except Exception as e:
            self._show_toast(f"บันทึกไม่สำเร็จ: {e}", "err")
            self._log(f"SAVE tactical plan failed: {e}",
                      severity="ERROR", category="STATUS")

    def _tac_load(self):
        if not self._map_enabled or self.web is None:
            self._show_toast("LOAD PLAN: ต้องใช้แผนที่", "err")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "โหลดแผนยุทธวิธี", os.path.expanduser("~"),
            "GeoJSON (*.geojson *.json);;ทุกไฟล์ (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                js = f.read()
        except Exception as e:
            self._show_toast(f"โหลดไม่สำเร็จ: {e}", "err")
            return
        self.web.page().runJavaScript(
            f"loadTacticalGeoJSON({js!r})",
            lambda n: self._tac_loaded(path, n))

    def _tac_loaded(self, path, n):
        n = int(n or 0)
        self.lbl_tac_status.setText(f"โหลดแล้ว {n} ชิ้น")
        self._show_toast(f"โหลดแผนยุทธวิธี {n} ชิ้น", "ok")
        self._log(f"LOAD tactical plan ← {path} ({n} ชิ้น)", category="STATUS")
        self._refresh_tac_icon_combo()

    def _refresh_tac_icon_combo(self):
        if not self._map_enabled or self.web is None:
            return

        def apply(js):
            import json as _json
            try:
                names = _json.loads(js or "[]")
            except Exception:
                return
            cur = self.cmb_tac_icon.currentText()
            self.cmb_tac_icon.blockSignals(True)
            self.cmb_tac_icon.clear()
            if names:
                self.cmb_tac_icon.addItems(names)
            else:
                self.cmb_tac_icon.addItem("— ยังไม่มีสัญลักษณ์ —")
            i = self.cmb_tac_icon.findText(cur)
            if i >= 0:
                self.cmb_tac_icon.setCurrentIndex(i)
            self.cmb_tac_icon.blockSignals(False)

        self.web.page().runJavaScript("listTacticalIcons()", apply)

    def _refresh_draw_btns(self):
        for k, b in self._draw_btns.items():
            self._set_icon_btn_state(b, k == self._draw_tool)

    def _set_draw_tool(self, tool: str):
        self._draw_tool = tool
        self._fence_mode = tool != "none"
        self._refresh_draw_btns()
        # หยิบเครื่องมือวาด = คลิกแผนที่ไม่ใช่คำสั่งบินแล้ว → ล็อกให้ตรงกับ JS
        if tool != "none" and getattr(self, "_goto_armed", False):
            self._set_goto_armed(False, reason=" (เลือกเครื่องมือ Geofence)")
        self._js(f"setDrawTool('{tool}')")
        # เลือกเครื่องมือ geofence → ปิดโหมด Waypoint (คนละระบบ ใช้พร้อมกันไม่ได้)
        if tool != "none" and self._waypoint_mode:
            self._wp_toggle(False)
        names = {"none": "GOTO", "poly": "POLY", "rect": "RECT", "circle": "CIRCLE"}
        self._log(f"map tool → {names.get(tool, tool)}")

    def _build_statusbar(self):
        bar = QFrame()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f"background:{T('panel')}; border-top:1px solid {rgba('#ffffff', 0.05)};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 14, 0)
        self.lbl_status = QLabel("SwarmGod · connecting…")
        self.lbl_status.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        h.addWidget(self.lbl_status)
        h.addStretch()
        # สถานะระยะห่าง/การชน (อัปเดตจาก _collision_watchdog)
        self.lbl_collide = QLabel("SEPARATION OK")
        self.lbl_collide.setStyleSheet(
            f"color:{T('green')}; font-size:10px; font-weight:700;")
        h.addWidget(self.lbl_collide)
        h.addSpacing(12)
        self.lbl_fcount = QLabel("fleet 0")
        self.lbl_fcount.setStyleSheet(f"color:{T('green')}; font-size:10px; font-weight:700;")
        h.addWidget(self.lbl_fcount)
        return bar

    # ══════════════════════════════════════════════════════════
    #  SIDEBAR / CONTROL SOURCE
    # ══════════════════════════════════════════════════════════
    def _update_fleet_scroll_height(self):
        """สูงพอดีกับจำนวนโดรน — ≤5 โชว์ครบไม่เลื่อน, >5 ล็อกสูง 5 แถวแล้วเลื่อน"""
        if not hasattr(self, "fleet_scroll"):
            return
        n = len(getattr(self, "fleet_items", {}) or {})
        gap = self._fleet_gap
        ih = self._fleet_item_h
        vmax = self._fleet_visible_max

        content_h = (n * ih + max(0, n - 1) * gap) if n else 0
        inner_w = max(120, self._sidebar_w - 28)
        self.fleet_holder.setFixedSize(inner_w, max(content_h, 1))

        if n == 0:
            self.fleet_scroll.setFixedHeight(0)
            self.fleet_scroll.setVisible(False)
            return

        self.fleet_scroll.setVisible(True)
        visible = min(n, vmax)
        view_h = visible * ih + max(0, visible - 1) * gap
        self.fleet_scroll.setFixedHeight(view_h)

        if n > vmax:
            self.fleet_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.fleet_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.fleet_scroll.verticalScrollBar().setValue(0)

    def _set_online_count(self, n: int):
        n = max(0, int(n))
        total = len(getattr(self, "fleet_items", {}) or {})
        # Telemetry arrives per drone; repainting this label + stylesheet on every
        # packet is wasted Qt re-polish work when the online count did not change.
        state = (n, total)
        if getattr(self, "_online_count_state", None) == state:
            return
        self._online_count_state = state
        self.lbl_count.setText(f"● {n}/{total} ONLINE" if total else "● 0 ONLINE")
        if n > 0:
            self.lbl_count.setStyleSheet(
                f"color:{T('green')}; font-size:10px; font-weight:700;"
                f" font-family:{FONT_MONO}; letter-spacing:0.6px;")
        else:
            self.lbl_count.setStyleSheet(
                f"color:{T('faint')}; font-size:10px; font-weight:600;"
                f" font-family:{FONT_MONO}; letter-spacing:0.6px;")

    def _refresh_online_count(self):
        """นับเฉพาะลำที่ "ออนไลน์จริง" (มี telemetry ใน 4 วิล่าสุด)

        เดิมส่ง len(self.fleet_items) เข้าไปตรง ๆ = นับลำที่ "แอดไว้" ไม่ใช่ลำที่ต่ออยู่จริง
        ตัวเลขเลยไม่ลดลงเลยแม้โดรนหลุดหมดแล้ว
        """
        self._set_online_count(len(self._connected_ids()))

    def _conn_watchdog(self):
        """เช็คทุก 2 วิว่าลำไหน telemetry ขาด → ตีเป็น OFFLINE ทั้งแถว FLEET + การ์ดล่าง

        ไม่มีใครทำหน้าที่นี้มาก่อน: badge จะค้างสถานะสุดท้ายที่เคยได้รับ (เช่น READY)
        ตลอดไปแม้โดรนหลุดไปแล้ว และตัวนับ ONLINE ก็ไม่ขยับ
        """
        live = set(self._connected_ids())
        self._health.note_core_connected(bool(live))   # observability
        for did, item in self.fleet_items.items():
            stale = did not in live
            if stale and getattr(item, "_status", "") != "OFFLINE":
                item.mark_offline()
            if did == self._selected_id and hasattr(self, "sel_card"):
                if stale and getattr(self.sel_card, "_status", "") != "OFFLINE":
                    self.sel_card.mark_offline()
                    self.sel_card.set_quick_enabled(False)
        self._refresh_online_count()

    def _toggle_sidebar(self):
        self._sidebar_open = not self._sidebar_open
        self.left_col.setMinimumWidth(0)
        self.left_col.setMaximumWidth(16777215)
        start = self.left_col.width()
        end = self._sidebar_w if self._sidebar_open else 0
        self._anim = QPropertyAnimation(self.left_col, b"maximumWidth")
        self._anim.setDuration(220)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

        def _done():
            if self._sidebar_open:
                self.left_col.setFixedWidth(self._sidebar_w)
        self._anim.finished.connect(_done)
        self._anim.start()

    def _set_font_scale(self, scale):
        """ขยาย/ลดขนาดฟอนต์ "ทั้งระบบ" (การ์ด, เมนู, แผงควบคุม, log, statusbar)

        ทำ 3 ชั้นเพื่อให้ครอบคลุมจริง (เดิมขยายแค่บางส่วน):
          1) QApplication font — widget ที่ไม่ได้กำหนด font-size เอง
          2) global stylesheet — QSS ระดับแอป
          3) ไล่สเกล stylesheet ของทุก widget ที่ฝัง font-size ไว้ในตัวเอง
             (การ์ดโดรน/ปุ่ม/ป้าย ที่ setStyleSheet เองจะทับ QSS ระดับแอป)
        """
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFont
        self._font_scale = max(0.8, min(2.5, round(float(scale), 2)))
        theme.set_ui_scale(self._font_scale)

        app = QApplication.instance()
        if app is not None:
            f = QFont(theme.FONT_UI_NAME,
                      max(8, int(round(theme.BASE_PT * self._font_scale))))
            f.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
            app.setFont(f)

        self.setProperty("_qss_base", None)      # main window ใช้ QSS ที่ generate ใหม่
        self.setStyleSheet(build_stylesheet(self._font_scale))
        n = theme.apply_font_scale(self, self._font_scale)

        self._log(f"ขนาดฟอนต์ → {int(self._font_scale * 100)}% ({n} องค์ประกอบ)",
                  category="STATUS")
        self._show_toast(f"ฟอนต์ {int(self._font_scale * 100)}%", "info")

    def _bump_font(self, delta):
        self._set_font_scale(self._font_scale + delta)

    def _install_font_shortcuts(self):
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self._bump_font(+0.1))
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self._bump_font(+0.1))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self._bump_font(-0.1))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self._set_font_scale(1.0))

    def _on_control_changed(self, idx):
        self._ui_mode = (idx == 0)
        for sec in self._sections:
            sec.set_locked(not self._ui_mode)
        self.sel_card.set_quick_enabled(self._ui_mode)
        if self._ui_mode:
            self.lbl_mode.setText("UI")
            self.lbl_mode.setStyleSheet(
                f"color:{T('accent')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};"
                f" letter-spacing:0.8px;")
            # ปิด banner ค้าง (REMOTE ACTIVE) ที่เปิดไว้ตอนอยู่โหมด REMOTE
            self._banner_timer.stop()
            if getattr(self, "_wave_enabled", False):
                self._show_banner(
                    "WAVE ACTIVE — EXECUTE ROUTE จะส่งแต่ละกลุ่มบินตามลำดับและรอให้กลุ่มก่อนลงจอด",
                    T("amber"), persistent=True)
            else:
                self._hide_banner()
                self._sync_preflight_warning()
            self._log("control source → UI (commands enabled)", category="STATUS")
        else:
            if getattr(self, "_wave_executing", False):
                self._abort_waypoint_execution()
            self.lbl_mode.setText("REMOTE")
            self.lbl_mode.setStyleSheet(
                f"color:{T('amber')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};"
                f" letter-spacing:0.8px;")
            # persistent=True — banner ค้างไว้ตลอดเวลาที่ยังอยู่โหมด REMOTE ไม่ให้งงว่า
            # ทำไมกดปุ่มอื่นไม่ติด (เดิมหายไปเองใน 6 วิ ทั้งที่ยังล็อกอยู่)
            self._show_banner(
                "REMOTE CONTROL ACTIVE — คำสั่งบินจาก UI ถูกล็อกทั้งหมด "
                "(Arm/Takeoff/RC/Waypoint/RTL) · สลับกลับ UI เพื่อสั่งงานได้",
                T("amber"), persistent=True)
            self._log("control source → REMOTE (UI commands disabled)",
                      severity="WARNING", category="ALERT")

    def _sync_preflight_warning(self, level=None):
        """ป้ายแดง = แถบเตือนค้างใต้ topbar จนกว่าจะเทส · REMOTE/WAVE ชนะถ้าเปิดอยู่"""
        if not hasattr(self, "banner"):
            return
        if level is None and hasattr(self, "_preflight"):
            level = self._preflight.badge()[1]
        want = (
            level == "bad"
            and getattr(self, "_ui_mode", True)
            and not getattr(self, "_wave_enabled", False)
        )
        if want:
            if not getattr(self, "_pf_warn_on", False):
                self._show_banner(self._PF_WARN, T("red"), persistent=True)
            return
        if getattr(self, "_pf_warn_on", False):
            self._pf_warn_on = False
            self._hide_banner()

    def _show_banner(self, text, color, persistent=False):
        """persistent=True → ไม่หายเอง (ใช้ตอน REMOTE เปิดค้าง) จนกว่าจะสั่งซ่อนเอง

        banner คงพื้นที่ไว้ตลอด (ดู _build_ui) — ฟังก์ชันนี้แค่เปลี่ยนข้อความ/สี
        ไม่ยุ่งกับ setVisible เพื่อไม่ให้ layout ขยับ/แผนที่วาบขาว
        """
        self._pf_warn_on = (text == self._PF_WARN)
        self.banner.setText(f"  {text}")
        self.banner.setStyleSheet(
            f"background:{rgba(color, 0.16)}; color:{color};"
            f" border-bottom:2px solid {color}; font-weight:700; font-size:12px; padding:3px 12px;")
        # กระจกของ banner บนคอกพิต: หน้า Field ต้องเห็น alarm/warning เดียวกัน
        # ไม่ใช่เฉพาะ toast ชั่วคราว มิฉะนั้นคนกลางสนามอาจพลาดเหตุสำคัญ
        kind = ("alarm" if color == T("red") else
                "warn" if color in (T("amber"), T("yellow")) else "info")
        self.field_hub.set_extra("banner", {"text": str(text),
                                             "kind": kind,
                                             "persistent": bool(persistent)})
        if persistent:
            self._banner_timer.stop()
        else:
            self._banner_timer.start(6000)

    def _hide_banner(self, restore_preflight=False):
        """เคลียร์ข้อความ banner แต่ 'เว้นพื้นที่ไว้เท่าเดิม' — กัน layout ขยับ/แผนที่วาบขาว"""
        self.banner.setText("")
        self.banner.setStyleSheet("background:transparent; border:none;")
        self.field_hub.set_extra("banner", None)
        self._pf_warn_on = False
        if restore_preflight:
            self._sync_preflight_warning()

    def _confirm(self, parent, title, text, **kw):
        """กล่องยืนยันบนคอกพิต — ระหว่างที่ค้างรอคนตอบ ให้แท็บเล็ตรู้ด้วย

        กล่องพวกนี้เป็น modal บล็อกคอกพิตทั้งตัว คนที่ถือแท็บเล็ตอยู่กลางสนาม
        จะเห็นแค่ "กดแล้วเงียบ" ถ้าไม่บอก — ต้องเห็นว่าคำสั่งไปค้างรอคนตอบอยู่
        ที่คอมควบคุม ไม่ใช่หายไปเฉย ๆ (docs/FIELD_TABLET_V2.md §6)
        """
        self.field_hub.set_extra("dialog", {"title": str(title),
                                            "text": str(text)[:400]})
        try:
            return confirm_dlg.confirm(parent, title, text, **kw)
        finally:
            self.field_hub.set_extra("dialog", None)

    def _field_notify(self, text, kind="ok"):
        """ส่งข้อความแจ้งเตือนของคอกพิตไปขึ้นบนแท็บเล็ตด้วย

        แท็บเล็ตเทียบเลขลำดับเพื่อขึ้นเฉพาะข้อความใหม่ (snapshot ไหลมา 5 ครั้ง/วิ)
        """
        self._field_notice_seq = getattr(self, "_field_notice_seq", 0) + 1
        self.field_hub.set_extra("notice", {"n": self._field_notice_seq,
                                            "text": str(text),
                                            "kind": str(kind)})

    def _show_toast(self, text, kind="ok"):
        """แจ้งผลลอยมุมล่างขวา — และส่งต่อให้แท็บเล็ตเห็นข้อความเดียวกัน"""
        # Windows ล็อกไฟล์ชั่วคราว (WinError 32) ไม่ใช่สถานะการบิน; เก็บใน log
        # ไว้ตรวจได้ แต่ไม่รบกวนคนควบคุมด้วย toast ทั้งคอกพิตและแท็บเล็ต
        if "winerror 32" in str(text).lower():
            return
        self._field_notify(text, kind)
        if not hasattr(self, "toast"):
            return
        if kind == "ok":
            color = T("green")
            icon, icol = "OK", T("green")
        elif kind == "err":
            color = T("red")
            icon, icol = "!", T("red")
        else:
            color = T("accent")
            icon, icol = "i", T("accent")

        self.toast_icon.setText(icon)
        self.toast_icon.setStyleSheet(
            f"color:#0a0c0e; background:{icol}; border-radius:11px;"
            f" font-size:10px; font-weight:800;")
        self.toast_msg.setText(text)
        self.toast.setStyleSheet(
            f"#FloatToast {{ background:{T('panel2')}; border:1px solid {rgba(color, 0.45)};"
            f" border-radius:12px; }}")
        self.toast.setVisible(True)
        self._position_toast()
        self._toast_timer.start(3800 if kind != "info" else 2000)

    def _hide_toast(self):
        if hasattr(self, "toast"):
            self.toast.setVisible(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "toast", None) and self.toast.isVisible():
            self._position_toast()

    def _on_cmd_result(self, msg: str):
        self._log(msg, category="COMMAND")
        low = (msg or "").lower()
        # ดึงชื่อคำสั่งหน้าแรกก่อน :
        title = msg.split(":", 1)[0].strip() if msg else "COMMAND"
        # V3-S08: a suppressed duplicate (double-click) is NEITHER success NOR
        # failure — the identical command is already in progress. Show an info
        # toast so the operator is not told it succeeded or failed.
        if "in_progress" in low:
            self._show_toast(f"{title} · คำสั่งเดียวกันกำลังดำเนินการอยู่", "info")
            return
        failed = (
            "error" in low
            or "ok=false" in low
            or "ปฏิเสธ" in msg
            or "failed" in low
            or "blocked" in low
        )
        if failed:
            detail = msg.split(":", 1)[-1].strip() if ":" in msg else msg
            self._show_toast(f"{title} ล้มเหลว · {detail[:48]}", "err")
        elif msg.startswith("SERVO "):
            # ปุ่ม A/B โชว์ toast ตอนกดไปแล้ว ("SERVO B เปิด · 1 ลำ") ซึ่งบอกชัดกว่า
            # "SERVO B D1 สำเร็จ" — ปล่อยให้ toast เดิมค้างไว้ ไม่ทับภายในไม่กี่ ms
            # (ถ้าล้มเหลวยังเข้า if ข้างบนและทับด้วย toast แดงตามปกติ)
            pass
        else:
            self._show_toast(f"{title} สำเร็จ", "ok")

    # ══════════════════════════════════════════════════════════
    #  COMMAND DISPATCH
    # ══════════════════════════════════════════════════════════
    def _target_ids(self):
        """เป้าหมายคำสั่ง = ลำที่เลือกไว้จากการ์ดฝั่งซ้าย (หรือทุกลำเมื่อเปิด FLEET)"""
        ids = self._selected_or_all()
        if ids:
            return ids
        if self._selected_id:
            return [self._selected_id]
        # ── ไม่มีลำไหนถูกเลือกเลย ──
        # เดิม fallback เป็น "ID ต่ำสุดในฝูง" ซึ่งอันตราย: การ์ดที่กู้จาก SQLite ตอนเปิด
        # โปรแกรมยังไม่ได้เชื่อมต่อ แต่มี ID ต่ำกว่าลำจริงได้ คำสั่ง (รวมถึงปล่อยของ)
        # จะถูกยิงไปที่ลำที่ไม่มีอยู่จริงแล้วหายเงียบ — หรือแย่กว่านั้นคือไปผิดลำ
        # ตอนนี้เลือกจากลำที่ **มี telemetry เข้ามาจริง** ก่อนเสมอ
        online = self._connected_ids()
        if online:
            return online[:1]
        ids = sorted(self.fleet_items.keys())
        return ids[:1] if ids else [1]

    def _guard(self):
        if not self._ui_mode:
            self._log("blocked: REMOTE control active — สลับเป็น UI ก่อน",
                      severity="WARNING", category="ALERT")
            self._show_toast("REMOTE เปิดอยู่ · สลับเป็น UI ก่อน", "err")
            # ย้ำ banner ทุกครั้งที่คำสั่งถูกบล็อก (เผื่อพลาดดูตอนสลับสวิตช์ครั้งแรก)
            self._show_banner(
                "REMOTE CONTROL ACTIVE — คำสั่งบินจาก UI ถูกล็อกทั้งหมด "
                "(Arm/Takeoff/RC/Waypoint/RTL) · สลับกลับ UI เพื่อสั่งงานได้",
                T("amber"), persistent=True)
            return False
        return True

    def _manual_nav_blocked_by_core(self, action):
        """Fail closed while a Core-owned mission still owns navigation.

        Ad-hoc GOTO/RC movement must never race an authoritative Core mission.
        Operator HOLD is handled separately as an explicit takeover through
        Cancel Navigation, which cancels the mission before issuing stop/hold.
        """
        if not bool(getattr(self, "_mission_core_authority", False)):
            return False
        label = str(action or "MANUAL NAV")
        self._log(f"{label} ถูกบล็อก — Core mission ยังถือ flight authority; "
                  "กด CANCEL NAV/HOLD ก่อนสั่ง manual",
                  severity="WARNING", category="ALERT")
        self._show_toast(f"{label} ถูกบล็อก · ยกเลิก Mission ก่อน", "err")
        return True

    def _dispatch_core(self, label, fn, *, source, targets=(), dedup_key=None,
                       enforce_dedup=True, operation_id=None):
        """Single observable boundary for every flight-mutating CoreClient call.

        Internal mission/automation callers set ``enforce_dedup=False`` when an
        idempotent-family label is used: the gateway still supplies correlation
        and timing, but never changes orchestration semantics. Emergency/takeover
        families are never suppressed by the ledger regardless.
        """
        return self._gateway.dispatch(
            label, fn, source=source, targets=targets,
            dedup_key=dedup_key, enforce_dedup=enforce_dedup,
            operation_id=operation_id)

    def _run_cmd(self, label, fn, dedup_key=None):
        if not self._guard():
            return
        ids = self._target_ids()
        vehicle = f"Drone {ids[0]}" if len(ids) == 1 else "Fleet"
        self._log(f"{label} → {','.join(map(str, ids))}", vehicle=vehicle, category="COMMAND")
        self._health.note_rpc(label)   # observability: last operator command
        self._show_toast(f"{label} · ส่งคำสั่ง…", "info")

        def worker():
            try:
                # V3-S07/S08: observable gateway boundary. dedup_key carries the
                # actual semantic arguments so double-click dedup keys on real
                # command identity, not the rounded display label.
                r = self._dispatch_core(
                    label, lambda: fn(ids), source="cockpit", targets=ids,
                    dedup_key=dedup_key)
                if getattr(r, "in_progress", False):
                    self.cmd_result.emit(
                        f"{label}: in_progress {getattr(r, 'message', '') or ''}".strip())
                else:
                    ok = bool(getattr(r, "ok", True))
                    msg = getattr(r, "message", "") or ""
                    if ok:
                        self.cmd_result.emit(f"{label}: ok={ok} {msg}".strip())
                    else:
                        self.cmd_result.emit(f"{label}: ok=False {msg}".strip())
            except Exception as e:
                self.cmd_result.emit(f"{label}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _cmd_arm(self):
        if self._manual_nav_blocked_by_core("ARM"):
            return
        # ARM ยังไม่ใช่การขึ้นบิน → เตือนอย่างเดียว ไม่บล็อก (ด่านจริงอยู่ที่ TAKEOFF)
        self._preflight_soft_warn("ARM")
        self._run_cmd("ARM", lambda ids: self.client.arm(ids))
    def _cmd_disarm(self):
        """DISARM — ถามยืนยัน 1 ครั้งก่อน (กดพลาดกลางอากาศ = ตกทันที)"""
        ids = self._target_ids()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกลำ", "err")
            return
        names = ", ".join(self._drone_name(i) for i in ids)
        if not self._confirm(
                self, "ยืนยัน DISARM",
                f"ดับมอเตอร์ {len(ids)} ลำ ({names}) ใช่หรือไม่?\n"
                f"ถ้าโดรนยังลอยอยู่ จะร่วงทันที",
                ok_text="DISARM", danger=True):
            return
        if bool(getattr(self, "_mission_core_authority", False)):
            self._abort_waypoint_execution()
        self._run_cmd("DISARM", lambda i: self.client.disarm(i, confirmed=True))

    # ── Servo A/B (spec: servo.md · A=ch7, B=ch8) ──
    SERVO_CH = {"A": 7, "B": 8}
    # ค่าที่วัดได้จากรีโมทจริง (ผู้ใช้ยืนยัน):
    #   ปุ่ม A = RC7 : ปล่อย 1050 → กด 1950
    #   ปุ่ม B = RC8 : ปล่อย  900 → กด 2100
    # ทั้งคู่ข้ามกลาง 1500 เวลาเปลี่ยนสถานะ จึงใช้ 1500 เป็นเส้นแบ่ง
    # BUGFIX: เดิมใช้ค่าเดียว (SERVO_ON = 1950) กับ **ทั้งสองช่อง** ทั้งที่ค่าที่วัดได้
    # ข้างบนบอกชัดว่าช่อง B ต้องเป็น 2100 — cockpit จึงสั่ง B ไปแค่ 1950
    # ต่างจากตอนกดที่รีโมท 150 µs · ถ้าปลายทาง (servo/กลไกปล่อย) ถูก calibrate
    # ตามระยะของรีโมท ค่านี้จะไป**ไม่สุดระยะ** กลไกขยับช้า/ฝืด/ค้างครึ่งทาง
    # = อาการ "กดจาก UI แล้วหน่วง แต่กดที่รีโมทติดปุบปับ"
    #
    # ตอนนี้ส่งค่าเท่ากับรีโมทเป๊ะรายช่อง — กลไกทำงานเหมือนกันไม่ว่าสั่งจากทางไหน
    SERVO_PWM_ON = {"A": 1950, "B": 2100}

    # ค่าตอน "ปล่อย" ของรีโมท — เป็นค่าอ้างอิงสำหรับตรวจสอบเท่านั้น **ไม่ได้ส่งออกไป**
    # การปิดจาก UI ใช้วิธี "ปล่อย override คืนช่องให้รีโมท" (servo_release)
    # ค่าที่ช่องนั้นตกลงไปจึงเป็นค่าจริงจากสวิตช์ ไม่ใช่ค่าที่เราสั่ง
    SERVO_PWM_OFF = {"A": 1050, "B": 900}

    SERVO_ON = 1950      # ค่าเริ่มต้น (ใช้เมื่อไม่รู้ว่าเป็นช่องไหน)
    SERVO_ON_MIN = 1500  # PWM เกินนี้ = ถือว่าช่องนั้น "เปิดอยู่"

    # ── ค่าที่ใช้ "อุ่นเครื่อง" ช่องหลังเชื่อมต่อ (ดู _prime_servo) ──
    #
    # ⚠️ ห้ามใช้ค่า "เปิด" เด็ดขาด — กลไกจะปล่อยของจริงตอนเปิดโปรแกรม
    # ต้องอยู่ในย่าน "ปิด" (ต่ำกว่า SERVO_ON_MIN) แต่ต่างจากค่าปล่อยมากพอให้
    # ตรวจจับได้ว่า FC ตอบกลับแล้ว (deadband ของ rcdbg คือ 20 µs)
    #
    #   A: ปล่อย 1050 → อุ่นที่ 1200 (+150) · เปิดจริงที่ 1950
    #   B: ปล่อย  900 → อุ่นที่ 1200 (+300) · เปิดจริงที่ 2100
    #
    # เซอร์โวจะขยับเล็กน้อยในย่านปิดแล้วกลับที่เดิม — **ทดสอบบนพื้นโดยไม่ใส่ของ
    # ก่อนใช้งานจริงทุกครั้งที่เปลี่ยนกลไก**
    SERVO_PWM_PRIME = {"A": 1200, "B": 1200}
    SERVO_PRIME_TIMEOUT = 20.0   # ไม่ตอบภายในนี้ = เลิกรอ ปลดล็อกปุ่มให้ใช้ตามปกติ

    def _servo_on_pwm(self, label):
        """PWM ตอน "เปิด" ของช่องนั้น — ตรงกับที่รีโมทส่งจริง"""
        return self.SERVO_PWM_ON.get(label, self.SERVO_ON)

    def _servo_active(self, drone_id, label):
        """ช่องนี้ "เปิดอยู่จริง" ไหม — ดูขาออกของ FC เป็นหลัก

        ลำดับความน่าเชื่อถือ:
          1. servo output = PWM ที่ FC ส่งออกขาเซอร์โวจริง → กลไกขยับจริงหรือยัง
          2. RC input     = สวิตช์บนรีโมท (ใช้เมื่อ FC ไม่รายงาน servo output)
          3. สิ่งที่ UI สั่งล่าสุด (ไม่มีข้อมูลจาก FC เลย)

        BUGFIX — เดิมเอา RC input ขึ้นก่อน ซึ่งผิด:
        ArduPilot สตรีม `RC_CHANNELS` มาเสมอ **แม้ไม่มีรีโมทต่ออยู่เลย** โดยใส่ค่า
        default/failsafe ให้ (วัดกับ SITL จริง: RC7=1000 แต่ RC8=**1800**)
        ค่าดิบเกิน 1500 จึงไม่ได้แปลว่า "มีคนโยกสวิตช์" — UI เลยสรุปว่ารีโมทถือห้อง
        ช่อง B อยู่ตลอดเวลา ปุ่มขึ้น `B 🔒` แล้ว **บล็อกการกดทิ้งทั้งหมดโดยไม่ส่ง
        คำสั่งออกเลย** ผู้ใช้เห็นเป็น "กดปุ่ม B แล้วไม่ทำงาน/หน่วง" ส่วนปุ่ม A ปกติ
        เพราะ RC7 บังเอิญต่ำกว่าเกณฑ์

        ขาเซอร์โวไม่โกหกแบบนั้น — ช่องที่ยังไม่ผูกฟังก์ชันไว้รายงาน 0 และเมื่อผูก
        `SERVOn_FUNCTION = RCINn` แล้ว ค่าขาออกก็สะท้อนสวิตช์รีโมทให้อยู่ดี
        กติกา "1 ห้อง" จึงยังทำงานครบเหมือนเดิม แต่ตัดสินจากสิ่งที่ขยับจริง
        """
        t = self._last_telem.get(int(drone_id))
        if t is not None:
            if getattr(t, "servo_valid", False):
                pwm = getattr(t, "servo_ch7_pwm" if label == "A" else "servo_ch8_pwm", 0)
                return int(pwm) >= self.SERVO_ON_MIN
            if getattr(t, "rc_valid", False):
                raw = getattr(t, "rc_ch7_raw" if label == "A" else "rc_ch8_raw", 0)
                return int(raw) >= self.SERVO_ON_MIN
        return label in self._servo_state.get(int(drone_id), set())

    def _servo_owner(self, drone_id, label):
        """ใครถือ "ห้อง" ของช่องนี้อยู่ — 'UI' | 'RC' | None (ว่าง)

        กติกา (ตามที่ผู้ใช้กำหนด): ช่องหนึ่งมีเจ้าของได้ทีละฝ่าย ฝ่ายที่ไม่ได้ถือ
        จะสั่งไม่ได้จนกว่าเจ้าของจะปิดงานของตัวเองก่อน

          core override อยู่        → 'UI'  (cockpit ถือห้อง)
          ไม่ override แต่ค่าเปิด    → 'RC'  (คนโยกสวิตช์ถือห้อง)
          ไม่ override และค่าปิด     → None  (ห้องว่าง ใครกดก่อนได้ก่อน)
        """
        t = self._last_telem.get(int(drone_id))
        if t is None:
            real = "UI" if label in self._servo_state.get(int(drone_id), set()) else None
        elif getattr(t, "ovr_ch7" if label == "A" else "ovr_ch8", False):
            real = "UI"
        elif getattr(t, "servo_valid", False):
            # ยืนยันได้จริงว่ากลไกทำงานอยู่โดยที่ UI ไม่ได้สั่ง = รีโมทถือห้อง
            pwm = getattr(t, "servo_ch7_pwm" if label == "A" else "servo_ch8_pwm", 0)
            real = "RC" if int(pwm) >= self.SERVO_ON_MIN else None
        else:
            # ── ไม่มีข้อมูลขาเซอร์โว = ยังสรุปไม่ได้ว่าใครถือห้อง → ห้ามบล็อก ──
            # เจตนา: "ล็อกห้อง" ต้องมีหลักฐานว่ากลไกทำงานจริงเท่านั้น
            #
            # ห้ามใช้ RC input ดิบมาล็อกเด็ดขาด — ArduPilot สตรีม RC_CHANNELS มาเสมอ
            # แม้ไม่มีรีโมทต่ออยู่ โดยเติมค่า default/failsafe ให้ (วัดจริง: RC8=1800)
            # จึงแยกไม่ออกระหว่าง "มีคนโยกสวิตช์ค้าง" กับ "ไม่มีตัวรับสัญญาณเลย"
            #
            # ที่สำคัญกว่านั้น: rc_valid/servo_valid ที่ core มีอายุ 5 วิ แล้ว **กะพริบ**
            # ตามจังหวะที่สตรีมมาถึง — ช่วงที่ servo หมดอายุแต่ RC ยังไม่หมด ปุ่มจะ
            # เด้งกลับไปล็อกเองทั้งที่ไม่มีอะไรเปลี่ยน นี่คือต้นตอของอาการ
            # "เชื่อมโดรนครั้งแรกแล้วกด B ไม่ติด กว่าจะสั่งได้ พอรอบต่อไปกดปุบปับ"
            # (พอ UI ยึดช่องได้ครั้งหนึ่ง ovr_ch* = True ก็ตอบ "UI" ทันทีตลอดไป)
            #
            # ป้ายบนการ์ดยังใช้ RC เป็น fallback ได้ (ดู _servo_active) — แค่ไม่เอามา
            # ห้ามผู้ใช้กดปุ่ม เพราะบล็อกจากหลักฐานที่ยืนยันไม่ได้แย่กว่าปล่อยให้สั่ง
            real = None
        return self._servo_apply_pending(int(drone_id), label, real)

    # ══════════════════════════════════════════════════════════
    #  อุ่นเครื่องช่อง A/B อัตโนมัติหลังเชื่อมต่อ
    # ══════════════════════════════════════════════════════════
    def _servo_ready(self, drone_id, label):
        """ช่องนี้ผ่านการอุ่นเครื่องแล้วหรือยัง (กดแล้วติดทันที)"""
        return (int(drone_id), label) in self._servo_primed

    def _prime_servo(self, drone_id):
        """ส่งคำสั่งอุ่นเครื่องเงียบ ๆ ทันทีที่โดรนเข้าฝูง แล้วรอ FC ตอบกลับ

        ทำไม: FC ใช้เวลารับ RC override **ครั้งแรก** หลังเชื่อมต่อ 1-25 วินาที
        (ดู SWARM_CONTROL.md §11.20-11.35 — ไล่หาต้นเหตุแล้วยังไม่เจอ gate ที่แท้จริง)
        แต่พอผ่านครั้งแรกไปแล้ว ทุกครั้งถัดไปติดทันทีตลอด

        เดิมต้องบอกผู้ใช้ว่า "กดทิ้ง 1 ครั้งตอนอยู่บนพื้นก่อน" ซึ่งไม่สะดวกและลืมง่าย
        ตอนนี้โปรแกรมทำให้เองในเบื้องหลัง แล้วขึ้นสถานะบนปุ่มว่าพร้อมแล้ว

        **ความปลอดภัย** — ส่งค่าในย่าน "ปิด" (SERVO_PWM_PRIME) ไม่ใช่ค่าเปิด
        กลไกจึงไม่ปล่อยของ · ปล่อย override คืนทันทีที่ FC ตอบรับ ·
        ข้ามช่องที่รีโมทถืออยู่ (กติกา 1 ห้อง) · ข้ามถ้าอยู่โหมด REMOTE
        """
        did = int(drone_id)
        for label, ch in self.SERVO_CH.items():
            key = (did, label)
            if key in self._servo_primed or key in self._servo_priming:
                continue
            if self._servo_owner(did, label) is not None:
                continue        # รีโมทถืออยู่ หรือ UI สั่งค้างไว้ — ไม่แตะ
            self._servo_priming[key] = time.monotonic()
            self._log(f"เตรียมช่อง SERVO {label} (CH{ch}) อัตโนมัติ…",
                      vehicle=f"Drone {did}", category="STATUS")
            pwm = self.SERVO_PWM_PRIME.get(label, 1200)
            threading.Thread(
                target=lambda d=did, c=ch: self._safe(lambda: self._dispatch_core(
                    f"SERVO PRIME CH{int(c)}", lambda: self.client.servo_set(d, c, pwm),
                    source="servo-prime", targets=[int(d)], enforce_dedup=False)),
                daemon=True).start()
        self._refresh_servo_buttons()

    def _check_servo_primed(self, t):
        """เรียกทุกเฟรม telemetry — FC ตอบรับค่าอุ่นเครื่องแล้วหรือยัง"""
        if not self._servo_priming:
            return
        did = int(t.drone_id)
        now = time.monotonic()
        for label, ch in self.SERVO_CH.items():
            key = (did, label)
            started = self._servo_priming.get(key)
            if started is None:
                continue

            echoed = False
            if getattr(t, "rc_valid", False):
                raw = int(getattr(t, "rc_ch7_raw" if label == "A" else "rc_ch8_raw", 0))
                echoed = abs(raw - self.SERVO_PWM_PRIME.get(label, 1200)) <= 20

            if echoed:
                took = now - started
                self._servo_priming.pop(key, None)
                self._servo_primed.add(key)
                self._log(f"SERVO {label} พร้อมใช้งาน (เตรียมเสร็จใน {took:.1f} วินาที)",
                          vehicle=f"Drone {did}", category="STATUS", severity="SUCCESS")
                threading.Thread(
                    target=lambda d=did, c=ch: self._safe(lambda: self._dispatch_core(
                        f"SERVO RELEASE CH{int(c)} [prime]",
                        lambda: self.client.servo_release(d, c),
                        source="servo-prime", targets=[int(d)], enforce_dedup=False)),
                    daemon=True).start()
                self._refresh_servo_buttons()
            elif now - started > self.SERVO_PRIME_TIMEOUT:
                # ไม่ตอบภายในเวลา — เลิกรอ ปล่อย override คืน แล้วปลดล็อกปุ่มให้ใช้
                # ตามปกติ (คำสั่งจริงยังส่งได้ core ส่งซ้ำจน FC รับอยู่ดี)
                self._servo_priming.pop(key, None)
                self._servo_primed.add(key)
                self._log(f"SERVO {label}: เตรียมไม่สำเร็จใน "
                          f"{self.SERVO_PRIME_TIMEOUT:.0f} วินาที — ใช้งานได้ตามปกติ "
                          f"แต่คำสั่งแรกอาจหน่วง",
                          vehicle=f"Drone {did}", category="STATUS", severity="WARNING")
                threading.Thread(
                    target=lambda d=did, c=ch: self._safe(lambda: self._dispatch_core(
                        f"SERVO RELEASE CH{int(c)} [prime]",
                        lambda: self.client.servo_release(d, c),
                        source="servo-prime", targets=[int(d)], enforce_dedup=False)),
                    daemon=True).start()
                self._refresh_servo_buttons()

    def _servo_awaiting_fc(self, drone_id, label):
        """core สั่งไปแล้วแต่ **เครื่องบินยังไม่ตอบรับ** — ใช้แสดงสถานะ "รอ" บนปุ่ม

        ทำไมต้องมี: วัดจากโดรนจริง FC ใช้เวลารับคำสั่ง **แรก** หลังเชื่อมต่อ
        ตั้งแต่ 1 ถึง 25 วินาที (แกว่งมาก คาดเดาไม่ได้) คำสั่งไม่ได้หาย —
        core ส่งซ้ำทุก 500 ms จนกว่า FC จะรับ แต่ผู้ใช้ไม่มีทางรู้ว่าเกิดอะไรขึ้น
        เลยกดซ้ำ ๆ หรือรอเก้อ (คำถามผู้ใช้: "ต้องรออีกกี่วิถึงจะกด B ได้")

        แทนที่จะให้เดา ให้ปุ่มบอกตรง ๆ:
          core override อยู่ + ขาเซอร์โวขยับแล้ว  → ยืนยันแล้ว (●)
          core override อยู่ + ขาเซอร์โวยังไม่ขยับ → กำลังรอเครื่องบิน (⋯)
        """
        t = self._last_telem.get(int(drone_id))
        if t is None:
            return False
        if not getattr(t, "ovr_ch7" if label == "A" else "ovr_ch8", False):
            return False        # core ไม่ได้ override อยู่ = ไม่มีอะไรให้รอ
        if not getattr(t, "servo_valid", False):
            return False        # ไม่มีข้อมูลขาเซอร์โว = สรุปไม่ได้ อย่าเดา
        pwm = getattr(t, "servo_ch7_pwm" if label == "A" else "servo_ch8_pwm", 0)
        return int(pwm) < self.SERVO_ON_MIN

    # หน้าต่างที่ยอมให้ปุ่มแสดงผลแบบ optimistic ก่อน telemetry ยืนยัน (วินาที)
    # สั้นกว่า 1.2 วิของ _verify_servo_released เพื่อไม่ให้ปุ่มยังโชว์ "ปิดแล้ว"
    # ในขณะที่ banner เตือนว่าสวิตช์รีโมทค้าง
    SERVO_PENDING_SECS = 1.0

    def _servo_apply_pending(self, drone_id, label, real):
        """คลุมค่าจริงด้วย "สิ่งที่เพิ่งกด" ชั่วคราว — กันปุ่มดูเหมือนไม่ตอบสนอง

        เดิมสถานะปุ่มอ่านจาก telemetry ล้วน ๆ ปุ่มจึงยังไม่เปลี่ยนหน้าจนกว่า core
        จะรายงาน RC/override รอบถัดไปกลับมา (เห็นเป็น "กดแล้วไม่ติด")
        ถ้าค่าจริงตรงกับที่คาด หรือหมดเวลาแล้ว → ทิ้ง pending ให้ค่าจริงชนะเสมอ
        """
        key = (int(drone_id), label)
        pend = self._servo_pending.get(key)
        if pend is None:
            return real
        expected, deadline, t0 = pend
        if real == expected:
            # เครื่องบินยืนยันแล้ว — log เวลาจริงไว้ให้เห็นว่าหน่วงตรงไหน (ครั้งเดียวต่อการกด)
            self._servo_pending.pop(key, None)
            self._log(f"SERVO {label}: เครื่องบินยืนยันใน "
                      f"{(time.monotonic() - t0) * 1000:.0f} ms",
                      vehicle=f"Drone {drone_id}", category="TELEMETRY")
            return real
        if time.monotonic() >= deadline:
            self._servo_pending.pop(key, None)
            return real
        return expected

    def _cmd_servo(self, label):
        """กดปุ่ม A/B — toggle: กดซ้ำอีกครั้งคือ "ยกเลิก" ช่องนั้น

        **ยิงทันที ไม่ถามยืนยัน** (ผู้ใช้เลือกไว้ — ในงานจริงจังหวะสำคัญกว่า)
        เดิมมีกล่องยืนยันทุกครั้ง ทำให้ต้องกด 2 จังหวะ = รู้สึกหน่วงแม้โค้ดจะเร็วแล้ว

        สิ่งที่ยังกันพลาดให้อยู่ (ไม่มีอันไหนกินเวลา):
          · เป็น toggle — กดซ้ำ = ยกเลิก/คืนช่องให้รีโมททันที
          · กติกา "1 ห้อง" — รีโมทถือห้องอยู่ UI สั่งไม่ได้เลย
          · โหมด REMOTE บล็อกทั้งหมด (`_guard`)
          · toast + mission log บอกทุกครั้งว่าเพิ่งสั่งอะไรไป
        """
        if not self._guard():
            return
        ch = self.SERVO_CH.get(label)
        if ch is None:
            return
        ids = self._target_ids()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกลำ", "err")
            return

        # ── กติกา "1 ห้อง": ถ้ารีโมทถือห้องอยู่ UI ห้ามแตะ ──
        rc_held = [d for d in ids if self._servo_owner(d, label) == "RC"]
        if rc_held:
            who = ", ".join(self._drone_name(d) for d in rc_held)
            msg = (f"SERVO {label} ถูกสั่งจากรีโมทอยู่ ({who})\n"
                   f"ต้องปิดสวิตช์ {label} ที่รีโมทก่อน UI ถึงจะสั่งได้")
            self._show_toast(f"รีโมทถือ SERVO {label} อยู่ · ปิดที่รีโมทก่อน", "err")
            self._show_banner(
                f"SERVO {label} — รีโมทกำลังใช้อยู่ ({who}) · UI สั่งไม่ได้จนกว่าจะปิดสวิตช์ที่รีโมท",
                T("amber"))
            self._log(f"SERVO {label} ถูกปฏิเสธ — รีโมทถือห้องอยู่ ({who})",
                      category="COMMAND", severity="WARNING")
            return

        # UI ถือห้องอยู่ทุกลำ → กดครั้งนี้คือ "ปิดงานของ UI"
        turning_off = all(self._servo_owner(d, label) == "UI" for d in ids)
        self._run_servo(ids, label, ch, off=turning_off)

    def _run_servo(self, ids, label, ch, off=False):
        """ยิงคำสั่ง servo ทีละลำใน thread (Servo RPC เป็น per-drone ไม่ใช่ target list)

        เปิด  = override ช่องนั้นด้วย PWM เปิด (core ส่งซ้ำให้เองกัน override หมดอายุ)
        ยกเลิก = "ปล่อยช่องคืนรีโมท" ไม่ใช่ override ค้างไว้ที่ค่าปิด — ไม่งั้นสวิตช์จริง
                จะถูกเมินตลอดไป ซึ่งขัดกับที่ตั้งใจให้สั่งได้ทั้งสองทาง
        """
        # ── 1) ยิงคำสั่งออกก่อนเป็นอย่างแรก ──
        # ทุกอย่างที่เหลือ (ไฟปุ่ม, log, toast) เป็นงานฝั่งจอ รอได้อีกไม่กี่ ms
        # แต่ไม่ควรมาคั่นก่อนคำสั่งลงสาย — เรียงแบบนี้คำสั่งถึง core เร็วที่สุด
        t_click = time.monotonic()
        on_pwm = self._servo_on_pwm(label)

        def work():
            for did in ids:
                if off:
                    r = self._safe(lambda d=did: self._dispatch_core(
                        f"SERVO RELEASE CH{int(ch)}", lambda: self.client.servo_release(d, ch),
                        source="cockpit-servo", targets=[int(d)], enforce_dedup=False))
                else:
                    r = self._safe(lambda d=did: self._dispatch_core(
                        f"SERVO SET CH{int(ch)}", lambda: self.client.servo_set(d, ch, on_pwm),
                        source="cockpit-servo", targets=[int(d)], enforce_dedup=False))
                ok = bool(r is not None and getattr(r, "ok", False))
                if ok:
                    # เวลาจริงจากกดถึง core รับคำสั่ง — ไว้ดูว่าถ้าหน่วง หน่วงที่ชั้นไหน
                    self.cmd_result.emit(
                        f"SERVO {label} D{did}: ok "
                        f"({(time.monotonic() - t_click) * 1000:.0f} ms ถึง core)")
                else:
                    # เดิมขึ้นแค่ "FAILED" ไม่บอกเหตุผล ทำให้ไล่ปัญหาไม่ได้เลย
                    why = (getattr(r, "message", "") or "ไม่มีรายละเอียด") if r is not None \
                        else "ไม่ได้รับคำตอบจาก core (ดู log ของ core)"
                    self.cmd_result.emit(f"SERVO {label} D{did}: FAILED — {why}")
        threading.Thread(target=work, daemon=True).start()

        # ── 2) ไฟปุ่ม/ป้ายเปลี่ยนทันที (optimistic) ──
        # หน้าตาปุ่มเดิมรอค่าจริงจาก telemetry รอบถัดไป จึงดูเหมือน "กดแล้วไม่ติด"
        # pending จะถูกทิ้งเองเมื่อค่าจริงมาถึงหรือหมดเวลา
        expected = None if off else "UI"
        deadline = t_click + self.SERVO_PENDING_SECS
        for did in ids:
            self._servo_pending[(int(did), label)] = (expected, deadline, t_click)
            cur = set(self._servo_state.get(int(did), set()))
            cur.discard(label) if off else cur.add(label)
            self._servo_state[int(did)] = cur
            self._refresh_servo_badge(did)

        # ── 3) บอกผู้ใช้ว่าเพิ่งสั่งอะไรไป ──
        # ไม่มีกล่องยืนยันแล้ว toast/log จึงเป็นหลักฐานเดียวที่ผู้ใช้เห็นทันที
        verb = "ยกเลิก (คืนช่องให้รีโมท)" if off else f"สั่ง (PWM {on_pwm})"
        targets = ", ".join(f"D{did}" for did in ids)
        if off:
            self._summ_event(
                "ยกเลิก", f"SERVO {label} ปิด/คืนรีโมท → {targets}", cancelled=True)
        else:
            self._summ_event(
                "คำสั่งล่าสุด", f"SERVO {label} (CH{ch}={on_pwm}) → {targets}")
        self._show_toast(
            f"SERVO {label} {'ปิด' if off else 'เปิด'} · {len(ids)} ลำ"
            + ("" if off else f" (CH{ch}={on_pwm})"),
            "info" if off else "ok")
        self._log(f"SERVO {label} CH{ch} — {verb} · {len(ids)} ลำ "
                  f"[{self._servo_debug(ids[0])}]", category="COMMAND")

        if off:
            # ขณะ override ค้างอยู่เรามองไม่เห็นตำแหน่งสวิตช์จริง (ArduPilot รายงานค่าที่
            # override แทน) จึงต้อง "ปล่อยก่อนแล้วค่อยอ่าน" — ถ้าปล่อยแล้วยังเปิดอยู่
            # แปลว่าสวิตช์บนรีโมทค้างที่ตำแหน่งเปิด ต้องเตือนผู้ใช้
            QTimer.singleShot(1200, lambda: self._verify_servo_released(ids, label))

    def _verify_servo_released(self, ids, label):
        """หลังปล่อย override แล้วเช็คว่ากลไกปิดจริงไหม

        ถ้ายังเปิดอยู่ = สวิตช์บนรีโมทค้างที่ตำแหน่งเปิด → ห้องตกเป็นของรีโมททันที
        ต้องบอกผู้ใช้ตรง ๆ ไม่ใช่ปล่อยให้เข้าใจผิดว่าปิดเรียบร้อยแล้ว
        """
        still = [d for d in ids if self._servo_active(d, label)]
        if not still:
            self._log(f"SERVO {label} ปิดแล้ว · คืนช่องให้รีโมท (ห้องว่าง)",
                      category="COMMAND", severity="SUCCESS")
            return
        who = ", ".join(self._drone_name(d) for d in still)
        self._show_toast(f"SERVO {label} ยังเปิดอยู่ · สวิตช์รีโมทค้าง", "err")
        self._show_banner(
            f"SERVO {label} ยังไม่ปิด ({who}) — สวิตช์บนรีโมทค้างที่ตำแหน่งเปิด "
            f"กรุณาปิดสวิตช์ที่รีโมท",
            T("red"), persistent=True)
        self._log(f"SERVO {label} ปล่อย override แล้วแต่ยังเปิดอยู่ ({who}) — "
                  f"สวิตช์รีโมทค้าง ห้องตกเป็นของรีโมท",
                  category="COMMAND", severity="WARNING")

    def _servo_debug(self, drone_id):
        """สรุปค่าดิบที่ใช้ตัดสินสถานะ A/B — ไว้ไล่ปัญหาเวลา toggle ไม่ตรงที่คาด"""
        t = self._last_telem.get(int(drone_id))
        if t is None:
            return "ไม่มี telemetry"
        parts = []
        if getattr(t, "rc_valid", False):
            parts.append(f"RC7={int(getattr(t, 'rc_ch7_raw', 0))}"
                         f" RC8={int(getattr(t, 'rc_ch8_raw', 0))}")
        else:
            parts.append("RC=ไม่มีข้อมูล")
        if getattr(t, "servo_valid", False):
            parts.append(f"SV7={int(getattr(t, 'servo_ch7_pwm', 0))}"
                         f" SV8={int(getattr(t, 'servo_ch8_pwm', 0))}")
        else:
            parts.append("SERVO=ไม่มีข้อมูล")
        return " · ".join(parts)

    def _sync_servo_from_telemetry(self, t):
        """sync ป้าย A/B จากค่าจริง — เห็นได้แม้สวิตช์ถูกโยกที่รีโมท

        ใช้กติกาเดียวกับ `_servo_active()` (ขาเซอร์โวก่อน แล้วค่อย RC) เพื่อไม่ให้
        ป้ายบนการ์ดกับปุ่มตัดสินคนละแบบ — ดูเหตุผลที่ `_servo_active`
        """
        if not (getattr(t, "rc_valid", False) or getattr(t, "servo_valid", False)):
            return
        did = int(t.drone_id)
        real = {lb for lb in ("A", "B") if self._servo_active(did, lb)}
        if self._servo_state.get(did) != real:
            self._servo_state[did] = real
            self._refresh_servo_badge(did)
        elif did == self._selected_id:
            # state เท่าเดิม (เช่น telemetry มายืนยันสิ่งที่เพิ่งสั่งไป) แต่หน้าปุ่มอาจยังเป็น
            # ของเดิมอยู่ — ต้อง sync ปุ่มด้วย ไม่งั้นไฟบอกสถานะไม่ติดจนกว่าจะมีการเปลี่ยนแปลง
            self._refresh_servo_buttons()

    def _refresh_servo_badge(self, drone_id):
        """อัปเดตป้าย A/B บนการ์ดลำนั้น (ทั้งแถว FLEET และการ์ดล่างถ้ากำลังเลือกอยู่)"""
        labels = self._servo_state.get(int(drone_id)) or set()
        item = self.fleet_items.get(int(drone_id))
        if item is not None:
            item.set_servo_state(labels)
        if hasattr(self, "sel_card") and int(drone_id) == self._selected_id:
            self.sel_card.set_servo_state(labels)
        self._refresh_servo_buttons()

    def _refresh_servo_buttons(self):
        """ปุ่ม A/B มี 4 สถานะ

          ว่าง          → ปุ่มปกติ (กดเพื่อเปิด)
          รอเครื่องบิน   → `A ⋯` สั่งไปแล้ว core ส่งซ้ำอยู่ แต่ FC ยังไม่ตอบรับ
          UI ถือ         → `A ●` ติดไฟทึบ ยืนยันจากขาเซอร์โวแล้ว (กดซ้ำ = ปิด)
          รีโมทถือ      → `A 🔒` กดไม่ได้ ต้องปิดที่รีโมทก่อน

        สถานะ "รอเครื่องบิน" สำคัญที่สุดสำหรับผู้ใช้ — FC ใช้เวลารับคำสั่งแรก
        หลังเชื่อมต่อ 1-25 วินาที (แกว่ง คาดเดาไม่ได้) เดิมปุ่มติดไฟทันทีแบบ
        optimistic แล้วดับลงหลัง 1 วิ ทำให้แยกไม่ออกว่า "สั่งไปแล้วกำลังรอ" กับ
        "ไม่ได้สั่ง" — ผู้ใช้จึงกดซ้ำ ๆ หรือรอเก้อโดยไม่รู้ว่าต้องรอนานแค่ไหน
        """
        if not hasattr(self, "btn_servo_a"):
            return
        ids = self._target_ids()
        for label, btn, color in (("A", self.btn_servo_a, T("red")),
                                  ("B", self.btn_servo_b, T("yellow"))):
            owners = {self._servo_owner(d, label) for d in ids} if ids else {None}
            rc_held = "RC" in owners
            ui_held = bool(ids) and owners == {"UI"}
            waiting = ui_held and any(self._servo_awaiting_fc(d, label) for d in ids)
            # PERF: เมธอดนี้ถูกเรียกจาก _sync_servo_from_telemetry ทุกแพ็กเก็ต (10 Hz)
            # เดิม setStyleSheet ใหม่ทุกครั้งทั้งสองปุ่ม = Qt re-parse CSS + repolish
            # 20 ครั้ง/วินาที บน "ปุ่มที่ผู้ใช้กำลังจะกด" พอดี ทั้งที่หน้าตาเหมือนเดิม
            # — ทาสีเฉพาะตอนสถานะเปลี่ยนจริงเท่านั้น
            # กำลังอุ่นเครื่องอยู่ = ยังไม่พร้อม แสดงให้เห็นชัด (แต่ยังกดได้)
            priming = bool(ids) and any(
                (int(d), label) in self._servo_priming for d in ids)
            mode = ("rc" if rc_held else
                    "wait" if waiting else
                    "ui" if ui_held else
                    "prime" if priming else "free")
            if getattr(self, "_servo_btn_mode", {}).get(label) == mode:
                continue
            if not hasattr(self, "_servo_btn_mode"):
                self._servo_btn_mode = {}
            self._servo_btn_mode[label] = mode
            if rc_held:
                btn.setText(f"{label} 🔒")
                btn.setStyleSheet(
                    f"QPushButton {{ background:{rgba(T('amber'), 0.10)};"
                    f" border:2px dashed {rgba(T('amber'), 0.6)}; border-radius:10px;"
                    f" color:{T('amber')}; font-size:22px; font-weight:800;"
                    f" letter-spacing:2px; }}")
                btn.setToolTip(f"รีโมทกำลังใช้ SERVO {label} อยู่ — "
                               f"ปิดสวิตช์ที่รีโมทก่อน UI ถึงจะสั่งได้")
            elif priming:
                # โปรแกรมกำลังอุ่นเครื่องช่องนี้ให้เอง — ยังกดได้ แค่บอกว่ายังไม่พร้อมสุด
                btn.setText(f"{label} ⌛")
                btn.setStyleSheet(
                    f"QPushButton {{ background:{rgba('#ffffff', 0.04)};"
                    f" border:1px dashed {rgba(color, 0.35)}; border-radius:10px;"
                    f" color:{T('faint')}; font-size:24px; font-weight:800;"
                    f" letter-spacing:2px; }}")
                btn.setToolTip(f"กำลังเตรียมช่อง SERVO {label} ให้พร้อมใช้งาน…\n"
                               f"กดได้เลยถ้าต้องการ แต่คำสั่งแรกอาจหน่วงเล็กน้อย\n"
                               f"รอจนตัวอักษรเป็นสีปกติ = พร้อมกดแล้วติดทันที")
            elif waiting:
                # สั่งไปแล้ว core ส่งซ้ำทุก 500 ms อยู่ แต่ FC ยังไม่ขยับขาเซอร์โว
                # ไม่ต้องกดซ้ำ — กดซ้ำ = ยกเลิกคำสั่งที่กำลังรออยู่
                btn.setText(f"{label} ⋯")
                btn.setStyleSheet(
                    f"QPushButton {{ background:{rgba(color, 0.22)};"
                    f" border:2px dashed {color}; border-radius:10px;"
                    f" color:{color}; font-size:26px; font-weight:800;"
                    f" letter-spacing:2px; }}"
                    f"QPushButton:hover {{ background:{rgba(color, 0.32)}; }}")
                btn.setToolTip(f"ส่งคำสั่ง SERVO {label} แล้ว — กำลังรอเครื่องบินตอบรับ\n"
                               f"ระบบส่งซ้ำให้เองทุก 0.5 วิ ไม่ต้องกดซ้ำ\n"
                               f"(กดซ้ำ = ยกเลิกคำสั่งที่รออยู่)")
            elif ui_held:
                btn.setText(f"{label} ●")
                btn.setStyleSheet(
                    f"QPushButton {{ background:{color}; border:2px solid {color};"
                    f" border-radius:10px; color:#101418; font-size:26px;"
                    f" font-weight:800; letter-spacing:2px; }}"
                    f"QPushButton:hover {{ background:{rgba(color, 0.85)}; }}")
                btn.setToolTip(f"UI ถือ SERVO {label} อยู่ (เครื่องบินยืนยันแล้ว) — "
                               f"กดเพื่อปิดและคืนช่องให้รีโมท")
            else:
                ch = self.SERVO_CH[label]
                btn.setText(label)
                btn.setStyleSheet(
                    f"QPushButton {{ background:{rgba(color, 0.14)};"
                    f" border:1px solid {rgba(color, 0.45)}; border-radius:10px;"
                    f" color:{color}; font-size:26px; font-weight:800;"
                    f" letter-spacing:2px; }}"
                    f"QPushButton:hover {{ background:{rgba(color, 0.26)};"
                    f" border:1px solid {rgba(color, 0.7)}; }}"
                    f"QPushButton:pressed {{ background:{rgba(color, 0.36)}; }}"
                    f"QPushButton:disabled {{ color:{T('faint')};"
                    f" background:{rgba('#ffffff', 0.03)};"
                    f" border:1px solid {hairline()}; }}")
                btn.setToolTip(f"SERVO {label} (CH{ch}) — กดแล้วสั่งทันที ไม่ถามยืนยัน · "
                               f"กดซ้ำ = ยกเลิกและคืนช่องให้รีโมท")
    def _cmd_land(self):
        if bool(getattr(self, "_mission_core_authority", False)):
            self._abort_waypoint_execution()
        self._run_cmd("LAND", lambda ids: self.client.land(ids))

    def _cmd_rtl(self):
        """RTL — ลำเดียวสั่งตรง, หลายลำใช้ลำดับแยกชั้นความสูงกันชน (spec 2)"""
        if bool(getattr(self, "_mission_core_authority", False)):
            self._abort_waypoint_execution()
        ids = self._target_ids()
        if len(ids) <= 1:
            self._run_cmd("RTL", lambda i: self.client.rtl(i))
            return
        self._staggered_rtl(ids)
    def _cmd_hold(self):
        # HOLD while Core owns a mission is an explicit operator takeover, not a
        # second navigation owner. Reuse Cancel Navigation so the Core run is
        # cancelled and stale waypoint/WAIT callbacks are invalidated first.
        if bool(getattr(self, "_mission_core_authority", False)):
            self._cancel_navigation()
            return
        self._run_cmd("HOLD", lambda ids: self.client.hold(ids))

    def _cmd_takeoff(self):
        if self._manual_nav_blocked_by_core("TAKEOFF"):
            return
        if not self._preflight_gate("TAKEOFF"):
            return
        alt = self.sf_takeoff.value()
        ids = self._target_ids()
        if not ids or not self._confirm(
                self, "ยืนยัน TAKEOFF",
                f"สั่งโดรน {len(ids)} ลำขึ้นบินที่ความสูง {alt:.0f} เมตร",
                ok_text="ยืนยันขึ้นบิน", accent=T("green"), danger=True):
            return
        self._run_cmd(f"TAKEOFF {alt:.0f}m",
                      lambda target: self.client.takeoff(target, alt, confirmed=True),
                      dedup_key=f"TAKEOFF|alt={alt!r}|confirmed=True")

    def _cmd_mode(self, enum_name):
        if self._manual_nav_blocked_by_core("MODE"):
            return
        mode = getattr(rpc.common_pb2, enum_name)
        self._run_cmd(f"MODE {enum_name.replace('FLIGHT_MODE_', '')}",
                      lambda ids: self.client.set_mode(ids, mode))

    def _cmd_goalt(self):
        if bool(getattr(self, "_mission_core_authority", False)):
            self._abort_waypoint_execution()
        alt = self.sf_setalt.value()
        self._run_cmd(f"GO ALT {alt:.0f}m", lambda ids: self.client.change_alt(ids, alt))

    def _swarm_start(self):
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("SWARM START"):
            return
        # เส้นทาง UI ปกติ: reject การเริ่ม Swarm ถ้า WAVE กำลัง execute (spec §10)
        if getattr(self, "_wave_executing", False):
            self._show_toast("WAVE กำลังทำงาน — ยกเลิก WAVE ก่อนเริ่ม Swarm", "err")
            self._log("SWARM START ถูกปฏิเสธ — WAVE กำลัง execute",
                      category="COMMAND", severity="WARNING")
            return
        sep = self.sf_spacing.value()
        formation = self.form_picker.current() if hasattr(self, "form_picker") else self._form_group.checkedId()
        fname = FORMATION_NAMES.get(formation, str(formation))
        gateway_targets = list(self._selected_or_all() or self._connected_ids())
        self._log(f"FORM UP ({fname}, sep {sep:.0f}m)", category="COMMAND")
        self._show_toast("FORM UP · ส่งคำสั่ง…", "info")

        def worker():
            try:
                cfg = self._dispatch_core(
                    "SWARM CONFIG", lambda: self.client.swarm_config(sep, formation=formation),
                    source="swarm", targets=gateway_targets)
                if not cfg.ok:
                    self.cmd_result.emit(f"FORMATION: ok=False {cfg.message}")
                    return
                r = self._dispatch_core(
                    "SWARM START", lambda: self.client.swarm_start(),
                    source="swarm", targets=gateway_targets)
                self.cmd_result.emit(f"SWARM: ok={r.ok} {r.message}")
            except Exception as e:
                self.cmd_result.emit(f"SWARM: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _swarm_stop(self):
        if not self._guard():
            return
        self._summ_event("ยกเลิก", "หยุดขบวน SWARM", cancelled=True)
        self._show_toast("SWARM STOP · ส่งคำสั่ง…", "info")
        gateway_targets = list(self._selected_or_all() or self._connected_ids())
        threading.Thread(target=lambda: self._safe(
            lambda: self.cmd_result.emit(
                f"SWARM STOP: ok={self._dispatch_core('SWARM STOP', lambda: self.client.swarm_stop(), source='swarm', targets=gateway_targets).ok}")),
            daemon=True).start()

    def _swarm_return(self):
        if not self._guard():
            return
        ids = self._selected_or_all() or self._connected_ids()
        self._staggered_rtl(ids)

    # ══════════════════════════════════════════════════════════
    #  DRONE SELECTION — คลิกการ์ดฝั่งซ้าย / Ctrl+Click / ปุ่ม FLEET
    # ══════════════════════════════════════════════════════════
    @staticmethod
    def _superscript_count(value):
        return str(int(value)).translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))

    def _refresh_group_ui(self):
        """อัปเดตจำนวนบนชิปและป้ายวงกลมบนการ์ด โดยไม่เปลี่ยน selection"""
        self._fleet_presenter.render_groups(
            self.fleet_items, self.group_of,
            getattr(self, "group_chips", None),
        )
        if not hasattr(self, "group_chips"):
            return
        # WAVE presentation/runtime remains owned by GroundStation in this phase.
        self._refresh_wave_group_choices()

    def _refresh_wave_group_choices(self):
        if not hasattr(self, "wave_group_checks"):
            return
        for group, check in self.wave_group_checks.items():
            count = sum(1 for did in self.fleet_items
                        if self.group_of.get(int(did)) == group)
            check.setEnabled(count > 0 and not self._wave_executing)
            check.setText(f"{group}{self._superscript_count(count)}" if count else str(group))
            if not count:
                check.setChecked(False)
        self._wave_update_sequence()

    def _persist_group(self, drone_id, group_no):
        try:
            self._group_store.set_group(int(drone_id), int(group_no))
        except Exception as exc:
            self._log(f"Group store save failed: {exc}", severity="WARNING", category="STATUS")

    def _set_drone_group(self, drone_id, group_no):
        """ย้ายโดรนหนึ่งลำไปกลุ่มใหม่; ลำหนึ่งอยู่ได้กลุ่มเดียวโดยโครง dict/PK"""
        did = int(drone_id or 0)
        group = int(group_no or 0)
        if did not in self.fleet_items or group not in range(0, 7):
            return
        if group:
            self.group_of[did] = group
        else:
            self.group_of.pop(did, None)
        self._persist_group(did, group)
        self._refresh_group_ui()
        self._push_map3d()
        label = f"Group {group}" if group else "No group"
        self._log(f"Drone {did} → {label}", vehicle=f"Drone {did}", category="STATUS")
        self._show_toast(f"Drone {did} · {label}", "ok")

    def _assign_selected_to_group(self, group_no):
        ids = self._selected_or_all()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกโดรนสำหรับกำหนดกลุ่ม", "err")
            return
        for did in ids:
            self.group_of[int(did)] = int(group_no)
            self._persist_group(did, group_no)
        self._refresh_group_ui()
        self._push_map3d()
        self._log(f"กำหนด {len(ids)} ลำ → กลุ่ม {group_no}", category="COMMAND")
        self._show_toast(f"จัด {len(ids)} ลำเข้ากลุ่ม {group_no} แล้ว", "ok")

    def _group_chip_menu(self, group_no, button, pos):
        ids = self._selected_or_all()
        menu = QMenu(button)
        action = menu.addAction(
            f"Assign {len(ids)} selected drone(s) to Group {int(group_no)}")
        action.setEnabled(bool(ids))
        action.triggered.connect(lambda: self._assign_selected_to_group(group_no))
        menu.exec_(button.mapToGlobal(pos))

    def _select_group(self, group_no: int, additive: bool = False):
        """เลือกเฉพาะลำ online ในกลุ่ม; เทียบเท่า Ctrl+คลิกการ์ดทีละลำ"""
        group = int(group_no or 0)
        assigned = sorted(d for d in self.fleet_items
                          if self.group_of.get(int(d)) == group)
        online_set = set(self._connected_ids())
        online = [d for d in assigned if d in online_set]
        skipped = len(assigned) - len(online)
        if not online:
            self._show_toast(f"กลุ่ม {group}: ไม่มีลำที่เชื่อมต่ออยู่", "err")
            self._log(f"เลือกกลุ่ม {group} ไม่สำเร็จ — ทุกลำ offline",
                      category="COMMAND", severity="WARNING")
            return
        if additive:
            self._selected_ids.update(online)
        else:
            self._selected_ids = set(online)
        self._selected_id = online[0]
        self._select_drone(self._selected_id)
        self._sync_fleet_button()
        self._refresh_selection_ui()
        if skipped:
            self._show_toast(f"กลุ่ม {group}: ข้าม {skipped} ลำที่หลุดการเชื่อมต่อ", "err")
        else:
            self._show_toast(f"เลือกกลุ่ม {group} · {len(online)} ลำ", "ok")
        if self._head_id not in online:
            self._apply_head(online[0], push=True, reason="group")
        self._summ_event(
            "เลือกกลุ่ม", f"กลุ่ม {group} · {', '.join(f'D{did}' for did in online)}")
        self._push_map3d_view(force=True)

    def _wire_fleet_item(self, item):
        """ต่อสัญญาณของการ์ดโดรน 1 ใบ (ที่เดียว — กันลืมต่อไม่ครบ)"""
        item.clicked.connect(self._on_fleet_click)
        item.head_req.connect(self._on_head_req)
        item.group_req.connect(self._set_drone_group)
        item.set_group(self.group_of.get(int(item.drone_id), 0))

    def _on_fleet_click(self, drone_id, ctrl):
        """คลิกการ์ด: ปกติ = เลือกลำเดียว, Ctrl+Click = เพิ่ม/เอาออกจากชุดที่เลือก"""
        did = int(drone_id or 0)
        if not did:
            return
        if ctrl:
            if did in self._selected_ids:
                self._selected_ids.discard(did)
            else:
                self._selected_ids.add(did)
            # ยังต้องมีลำหลักไว้โชว์ในการ์ดล่าง
            if did not in self._selected_ids and self._selected_id == did:
                self._selected_id = next(iter(sorted(self._selected_ids)), 0)
            elif did in self._selected_ids:
                self._selected_id = did
        else:
            self._selected_ids = {did}
            self._selected_id = did
        # ผู้ใช้เลือกเอง → FLEET ไม่ใช่ "ทุกลำ" อีกต่อไป (เว้นแต่บังเอิญครบ)
        self._sync_fleet_button()
        if self._selected_id:
            self._select_drone(self._selected_id)
        else:
            self._clear_selected_card()
        self._refresh_selection_ui()

    def _on_map_drone_selected(self, drone_id):
        """คลิกรายการโดรนใน Map 3D = เลือกลำเดียวเหมือนคลิกการ์ดหลัก"""
        did = int(drone_id or 0)
        if did in self.fleet_items:
            self._on_fleet_click(did, False)

    def _sync_fleet_button(self):
        if not hasattr(self, "takeoff_panel"):
            return
        all_ids = set(self.fleet_items.keys())
        on = bool(all_ids) and self._selected_ids == all_ids
        if on != self.takeoff_panel.fleet_on():
            self.takeoff_panel.set_fleet_on(on)

    def _refresh_fleet_count(self):
        """Compatibility wrapper for the fleet-count label only."""
        self._fleet_presenter.render_fleet_count(
            getattr(self, "lbl_fcount", None), len(self.fleet_items))

    def _on_fleet_toggled(self, on):
        """ปุ่ม FLEET: เปิด = เลือกทุกลำ, ปิด = ล้างการเลือก (กลับไปคลิกเลือกเอง)"""
        if on:
            self._selected_ids = set(self.fleet_items.keys())
            if self._selected_ids and self._selected_id not in self._selected_ids:
                self._selected_id = next(iter(sorted(self._selected_ids)))
                self._select_drone(self._selected_id)
            n = len(self._selected_ids)
            self._log(f"FLEET เปิด — เลือกทุกลำ ({n} ลำ)", category="COMMAND")
            self._show_toast(f"FLEET · เลือกทุกลำ {n} ลำ", "ok")
        else:
            self._selected_ids.clear()
            self._log("FLEET ปิด — ล้างการเลือก (คลิกเลือกเองที่การ์ดซ้าย)",
                      category="COMMAND")
            self._show_toast("FLEET ปิด · เลือกเองได้", "info")
        self._refresh_selection_ui()

    def _refresh_selection_ui(self):
        """อัปเดตไฮไลต์การ์ด + ป้ายจำนวนที่เลือก + สรุปคำสั่ง"""
        summary = self._fleet_presenter.render_selection(
            self.fleet_items,
            self._selected_ids,
            getattr(self, "takeoff_panel", None),
        )
        if summary:
            self._summ_set("sel", "SELECTED", summary)
        else:
            self._summ_remove("sel")
        # โหมด SEPARATE: เปลี่ยนลำที่โฟกัส = เปลี่ยนลำที่กำลังวางแผนให้
        if getattr(self, "_wp_separate", False) and hasattr(self, "lbl_wp_points"):
            self._wp_render_points_label()

    def _selected_or_all(self):
        """ลำเป้าหมายของคำสั่ง: ที่เลือกไว้ ถ้าไม่ได้เลือกเลยคืนว่าง (ให้ผู้เรียกเตือน)"""
        return sorted(i for i in self._selected_ids if i in self.fleet_items)

    def _on_card_alt_changed(self, drone_id, alt):
        self._drone_alt[int(drone_id)] = float(alt)
        self._summ_set(f"alt{int(drone_id)}", f"ALT D{int(drone_id)}", f"{alt:.0f} m")

    def _on_card_spacing_changed(self, drone_id, spacing):
        self._drone_spacing[int(drone_id)] = float(spacing)
        self._summ_set(f"sp{int(drone_id)}", f"SPACING D{int(drone_id)}", f"{spacing:.0f} m")

    def _on_drone_renamed(self, drone_id, name):
        """เปลี่ยนชื่อเฉพาะการแสดงผลใน cockpit และบันทึกไว้กับ endpoint เดิม"""
        did = int(drone_id or 0)
        shown = self._fleet_presenter.normalize_display_name(name)
        if not did or not shown:
            return
        self._drone_names[did] = shown
        item = self.fleet_items.get(did)
        if item is not None:
            item.set_display_name(shown)
        if did == self._selected_id:
            self.sel_card.set_display_name(shown)
        host, port = self._endpoints.get(did, ("", 0))
        if host:
            self._remember_endpoint(did, host, port, name=shown)
        t = self._last_telem.get(did)
        if t is not None and (t.position.lat or t.position.lon):
            color = drone_color(did)
            self._js(f"updateDrone({did},{t.position.lat:.7f},{t.position.lon:.7f},"
                     f"{t.heading:.1f},'{color}',{shown!r})")
        self._push_map3d()
        self._log(f"เปลี่ยนชื่อ → {shown}", vehicle=f"Drone {did}", category="STATUS")
        self._show_toast(f"บันทึกชื่อ {shown} แล้ว", "ok")

    def _alt_for(self, did):
        return float(self._drone_alt.get(int(did), self.sf_takeoff.value()))

    # ══════════════════════════════════════════════════════════
    #  HEAD / LEADER (spec 1)
    # ══════════════════════════════════════════════════════════
    def _connected_ids(self):
        """โดรนที่ยังเชื่อมต่อ = มี telemetry เข้ามาภายใน 4 วินาทีล่าสุด"""
        now = time.monotonic()
        out = []
        for did in self.fleet_items:
            if did in self._removed_ids:
                continue
            if now - self._last_seen.get(did, 0) < 4.0:
                out.append(did)
        return sorted(out)

    def _priority_ids(self):
        """ลำดับความสำคัญของหัว = เรียงตาม id (น้อย→มาก)"""
        return sorted(self.fleet_items.keys())

    # สถานะที่ "ห้ามเปลี่ยน Head" (spec 1.3, 1.4)
    _HEAD_LOCK_STATUS = {"TAKEOFF", "LANDING", "RTL"}

    def _head_change_block_reason(self):
        """คืนเหตุผลถ้าตอนนี้เปลี่ยน Head ไม่ได้ / None = เปลี่ยนได้

        1.1 ก่อน takeoff เปลี่ยนได้   1.2 ลอยอยู่กลางอากาศเปลี่ยนได้
        1.3 กำลัง takeoff / landing ห้าม   1.4 ขบวนกำลังเคลื่อนที่ห้าม
        """
        if getattr(self, "_rtl_active", False):
            return "กำลังทำ RTL อยู่"
        for did in self._connected_ids():
            t = self._last_telem.get(did)
            if t is None:
                continue
            st = rpc.status_name(t.status)
            if st in self._HEAD_LOCK_STATUS:
                return f"Drone {did} กำลัง {st} อยู่"
        # 1.4 — ขบวนกำลังเคลื่อนที่ (swarm active + มีลำที่ยังขยับอยู่)
        if getattr(self, "_swarm_active", False):
            moving = [d for d in self._connected_ids()
                      if (self._last_telem.get(d) is not None
                          and self._last_telem[d].ground_speed > 0.6)]
            if moving:
                return ("ขบวนกำลังเคลื่อนที่ (Drone "
                        + ", ".join(str(d) for d in moving) + ")")
        return None

    def _on_head_req(self, drone_id):
        """ผู้ใช้กดดาว/ปุ่ม SET HEAD บนการ์ด → ถามยืนยันก่อน (spec 1)"""
        did = int(drone_id or 0)
        if not did:
            return
        if did == self._head_id:
            self._show_toast(f"Drone {did} เป็น Head อยู่แล้ว", "info")
            return
        block = self._head_change_block_reason()
        if block:
            self._show_toast(f"เปลี่ยน Head ไม่ได้ · {block}", "err")
            self._show_banner(f"ตั้ง Head ไม่ได้ — {block} (ล็อกความปลอดภัยตั้งใจ)", T("red"))
            self._log(f"เปลี่ยน Head ถูกปฏิเสธ — {block}",
                      category="COMMAND", severity="WARNING")
            return
        name = self._drone_name(did)
        if not self._confirm(
                self, "ตั้งเป็น Head",
                f"ต้องการตั้ง {name} เป็น Head (หัวขบวน) ใช่หรือไม่?",
                ok_text="ตั้งเป็น Head", accent=T("yellow")):
            return
        self._apply_head(did, push=True, reason="manual")

    def _apply_head(self, drone_id, push=True, reason="manual"):
        """เปลี่ยนหัวขบวนจริง — push=True จะแจ้ง core ด้วย (SetLeader)"""
        did = int(drone_id or 0)
        self._head_id = did
        self._leader_id = did          # ให้ RC/movement อ้าง head เดียวกัน
        # ผู้ใช้เลือกเอง = "ปักหมุด" ไม่ให้ swarm poll ดึงกลับไปลำเดิม
        if reason in ("manual", "auto", "group"):
            self._head_pinned = did
        self._update_head_ui()
        if did:
            self._summ_set("head", "HEAD", f"{self._drone_name(did)}")
            tag = {"manual": "ตั้ง Head", "auto": "Auto-Reassign Head",
                   "group": "ย้าย Head ตามกลุ่ม", "init": "เลือก Head อัตโนมัติ",
                   "swarm": "Head (จาก swarm)"}.get(reason, "Head")
            sev = "WARNING" if reason == "auto" else "SUCCESS"
            self._log(f"{tag} → Drone {did}", vehicle=f"Drone {did}",
                      category="COMMAND", severity=sev)
            if reason == "auto":
                self._show_toast(f"หัวหลุด · เลื่อน Drone {did} ขึ้นเป็น Head อัตโนมัติ", "info")
            elif reason == "manual":
                self._show_toast(f"ตั้ง Drone {did} เป็น Head แล้ว", "ok")
        else:
            self._summ_remove("head")
        if push and did:
            self._js(f"setLeaderId({did})")
            threading.Thread(target=lambda: self._safe(lambda: self._dispatch_core(
                "SET LEADER", lambda: self.client.set_leader(did),
                source="head-selection", targets=[did], enforce_dedup=False)),
                daemon=True).start()

    def _update_head_ui(self):
        for did, item in self.fleet_items.items():
            item.set_head(did == self._head_id)
        if hasattr(self, "sel_card"):
            self.sel_card.set_head(self.sel_card.drone_id == self._head_id
                                   and self._head_id != 0)
        if hasattr(self, "cmb_leader"):
            want = "Auto" if not self._head_id else f"Drone {self._head_id}"
            idx = self.cmb_leader.findText(want)
            if idx >= 0:
                self.cmb_leader.blockSignals(True)
                self.cmb_leader.setCurrentIndex(idx)
                self.cmb_leader.blockSignals(False)

    def _head_watchdog(self):
        """spec 1 — หัวหลุด (disconnect) → เลื่อนลำถัดไปที่ยัง online ขึ้นเป็นหัวอัตโนมัติ"""
        connected = self._connected_ids()
        if getattr(self, "_swarm_active", False):
            return  # ระหว่าง swarm ให้ core เป็นเจ้าของ leader (sync ผ่าน _on_swarm_update)
        if self._head_id == 0:
            # ยังไม่มีหัว แต่มีลำ online → เลือกหัวเริ่มต้นให้อัตโนมัติ
            if connected:
                self._apply_head(swarm_logic.choose_head(
                    connected, priority=self._priority_ids()),
                    push=False, reason="init")
            return
        new_head, changed = swarm_logic.next_head_after_loss(
            self._head_id, connected, priority=self._priority_ids())
        if changed:
            self._apply_head(new_head, push=(new_head != 0), reason="auto")

    def _on_leader_combo(self, text):
        """เลือกหัวจาก dropdown LEADER ในหน้า Swarm — มีเงื่อนไข/ยืนยันเหมือนปุ่มบนการ์ด"""
        text = (text or "").strip()
        if text == "Auto" or not text:
            self._head_pinned = 0
            self._head_id = 0
            self._update_head_ui()
            self._summ_remove("head")
            threading.Thread(target=lambda: self._safe(lambda: self._dispatch_core(
                "SET LEADER AUTO", lambda: self.client.set_leader(0),
                source="head-selection", targets=(), enforce_dedup=False)),
                daemon=True).start()  # ปลดหมุดที่ core
            self._head_watchdog()   # ให้เลือกหัวอัตโนมัติทันที
            return
        try:
            did = int(text.replace("Drone", "").strip())
        except ValueError:
            return
        if not did or did == self._head_id:
            return
        block = self._head_change_block_reason()
        if block:
            self._show_toast(f"เปลี่ยน Head ไม่ได้ · {block}", "err")
            self._show_banner(f"ตั้ง Head ไม่ได้ — {block} (ล็อกความปลอดภัยตั้งใจ)", T("red"))
            self._log(f"เปลี่ยน Head (dropdown) ถูกปฏิเสธ — {block}",
                      category="COMMAND", severity="WARNING")
            self._update_head_ui()      # ดีดกลับไปค่าเดิม
            return
        if not self._confirm(
                self, "ตั้งเป็น Head",
                f"ต้องการตั้ง {self._drone_name(did)} เป็น Head (หัวขบวน) ใช่หรือไม่?",
                ok_text="ตั้งเป็น Head", accent=T("yellow")):
            self._update_head_ui()      # ยกเลิก → ดีดกลับ
            return
        self._apply_head(did, push=True, reason="manual")

    def _drone_name(self, did):
        """Compatibility wrapper for read-only drone display-name formatting."""
        item = self.fleet_items.get(did)
        t = self._last_telem.get(did)
        return self._fleet_presenter.display_name(
            did,
            getattr(item, "name", "") if item is not None else "",
            getattr(t, "name", "") if t is not None else "",
        )

    # ══════════════════════════════════════════════════════════
    #  TAKE OFF PANEL (spec 2 + spec 5)
    # ══════════════════════════════════════════════════════════
    def _refresh_takeoff_panel(self):
        # การ์ดถูกเพิ่ม/ลบ → ล้าง id ที่หายไปออกจากชุดที่เลือก แล้ว sync UI
        self._selected_ids &= set(self.fleet_items.keys())
        self._sync_fleet_button()
        self._refresh_selection_ui()

    def _update_takeoff_summary(self):
        if not hasattr(self, "takeoff_panel"):
            return
        mode = self.takeoff_panel.mode()
        self._summ_set("to_mode", "TAKEOFF MODE",
                       "All (พร้อมกัน)" if mode == "all" else "Sequential (ไล่ลำดับ)")

    def _on_panel_takeoff(self, mode):
        """กดปุ่ม TAKE OFF ในแผง (spec 2) — สั่งเฉพาะลำที่เลือกไว้ ตามโหมด"""
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("TAKEOFF"):
            return
        ids = self._selected_or_all()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกลำที่จะขึ้นบิน · คลิกการ์ดซ้าย หรือกด FLEET", "err")
            return
        if not self._preflight_gate("TAKEOFF"):
            return
        alts = {d: self._alt_for(d) for d in ids}
        steps = swarm_logic.plan_takeoff(
            mode, ids, head_id=self._head_id,
            default_alt=self.sf_takeoff.value(), alts=alts,
            priority=self._priority_ids())
        self._execute_takeoff_steps(steps, mode, swarm_prefix=False)

    def _execute_takeoff_steps(self, steps, mode, swarm_prefix=False, generation=None,
                               flow_run_id=None, flow_step_id=None):
        if not steps:
            return
        label_mode = "ALL" if mode == "all" else "SEQ"
        plan_text = " · ".join(f"D{st.drone_id}→{st.alt:.0f}m" for st in steps)
        # Waypoint flow ยืนยันไปแล้วใน _wp_require_takeoff; การกดจาก panel/swarm
        # ต้องยืนยันตรงนี้หนึ่งครั้งก่อนส่ง confirmed=true ไป core.
        if generation is None and not self._confirm(
                self, "ยืนยัน TAKEOFF",
                f"แผนขึ้นบิน {len(steps)} ลำ\n{plan_text}",
                ok_text="ยืนยันขึ้นบิน", accent=T("green"), danger=True):
            self._show_toast("ยกเลิก TAKEOFF", "info")
            return
        self._summ_set("takeoff_plan", "TAKEOFF PLAN", f"{label_mode} · {plan_text}")
        if flow_run_id is None and generation is None:
            flow_run_id = self._flight_run_start(
                "swarm_takeoff" if swarm_prefix else "takeoff", mode=mode)
        step_id = flow_step_id or ("takeoff" if swarm_prefix else "send_takeoff")
        if flow_run_id is not None:
            self._flight_step_active(flow_run_id, step_id,
                                     f"Sending 0/{len(steps)} · {label_mode}")
            self._flight_takeoff_sent = set()
            self._flight_takeoff_targets = [int(st.drone_id) for st in steps]
        self._log(f"TAKE OFF [{label_mode}] {len(steps)} ลำ"
                  + (" (swarm/form-up)" if swarm_prefix else ""),
                  category="COMMAND")
        if mode == "all":
            # ทุกลำพร้อมกัน
            for st in steps:
                if generation is None or generation == self._wp_takeoff_generation:
                    self._takeoff_one(st.drone_id, st.alt, flow_run_id, step_id)
        else:
            # ไล่ทีละลำ: หัวก่อน แล้วลูกตามลำดับ (เว้นช่วง ~2.5s/ลำ)
            for st in steps:
                QTimer.singleShot(int(st.order * 2500),
                                  lambda d=st.drone_id, a=st.alt, g=generation,
                                  r=flow_run_id, sid=step_id:
                                  self._takeoff_one(d, a, r, sid)
                                  if g is None or g == self._wp_takeoff_generation else None)
        self._show_toast(f"TAKE OFF {label_mode} · {len(steps)} ลำ", "info")
        return flow_run_id

    def _takeoff_one(self, did, alt, flow_run_id=None, flow_step_id="send_takeoff"):
        is_head = (int(did) == int(self._head_id or 0))

        def worker():
            try:
                r = self._dispatch_core(
                    f"TAKEOFF {float(alt):.17g}m [orchestrator]",
                    lambda: self.client.takeoff([int(did)], float(alt), confirmed=True),
                    source="takeoff-orchestrator", targets=[int(did)],
                    dedup_key=f"TAKEOFF|alt={float(alt)!r}|confirmed=True",
                    enforce_dedup=False)
                ok = bool(getattr(r, "ok", True))
                msg = getattr(r, "message", "") or ""
                tag = "HEAD " if is_head else ""
                self.cmd_result.emit(
                    f"TAKEOFF {tag}D{did} {alt:.0f}m: ok={ok} {msg}".strip())
                if flow_run_id is not None:
                    self.ui_call.emit(lambda d=int(did), good=ok, text=msg,
                                      rid=flow_run_id, sid=flow_step_id:
                                      self._flight_takeoff_response(rid, sid, d, good, text))
                # ลำแม่ล้มเหลว = ขบวนไปต่อไม่ได้ → เตือนแรงกว่าปกติ ไม่ให้พลาดตา
                if not ok and is_head:
                    self.cmd_result.emit(
                        f"⚠ ลำแม่ D{did} ขึ้นบินไม่สำเร็จ: {msg}")
            except Exception as e:
                self.cmd_result.emit(f"TAKEOFF D{did}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _flight_takeoff_response(self, run_id, step_id, did, ok, message):
        if not self._flight_is_current(run_id):
            return
        if not ok:
            self._flight_step_failed(run_id, step_id, message or f"D{did} rejected TAKEOFF")
            return
        self._flight_takeoff_sent.add(int(did))
        total = len(self._flight_takeoff_targets)
        self._flight_step_active(run_id, step_id,
                                 f"Command accepted {len(self._flight_takeoff_sent)}/{total}")
        if len(self._flight_takeoff_sent) < total:
            return
        self._flight_step_done(run_id, step_id, f"Command accepted {total}/{total}")
        self._flight_step_active(run_id, "wait_airborne", f"Airborne 0/{total}")
        if self._flight_run.kind == "takeoff":
            QTimer.singleShot(500, lambda r=run_id: self._flight_watch_takeoff(r))

    def _flight_watch_takeoff(self, run_id):
        if not self._flight_is_current(run_id):
            return
        ids = list(self._flight_takeoff_targets)
        airborne = [did for did in ids if self._wp_is_airborne(did)]
        self._flight_step_active(run_id, "wait_airborne", "Airborne %d/%d" % (len(airborne), len(ids)))
        if len(airborne) < len(ids):
            QTimer.singleShot(700, lambda r=run_id: self._flight_watch_takeoff(r))
            return
        self._flight_step_done(run_id, "wait_airborne", "Airborne %d/%d" % (len(ids), len(ids)))
        self._flight_step_active(run_id, "target_alt", "Checking target altitude")
        at_alt = [did for did in ids if float(self._last_alt.get(did, 0.0)) >= self._alt_for(did) * 0.9]
        if len(at_alt) < len(ids):
            self._flight_step_active(run_id, "target_alt", "Target altitude %d/%d" % (len(at_alt), len(ids)))
            QTimer.singleShot(700, lambda r=run_id: self._flight_watch_takeoff(r))
            return
        self._flight_step_done(run_id, "target_alt", "Target altitude %d/%d" % (len(ids), len(ids)))
        self._flight_step_active(run_id, "flight_ready")
        self._flight_step_done(run_id, "flight_ready", "Ready")

    # ══════════════════════════════════════════════════════════
    #  SWARM TAKE OFF = FORM UP + FLY (spec 3)
    # ══════════════════════════════════════════════════════════
    def _swarm_takeoff(self):
        """spec 2 — กด Take off ในหน้า Swarm = ขึ้นบิน + แนบ Form up ให้อัตโนมัติ

        BUGFIX: เดิมสั่ง form-up (swarm_start) ก่อน takeoff 800ms ทำให้ swarm loop
        ยิง GotoYaw ใส่โดรนที่ยัง disarm อยู่บนพื้น แล้วตีกับคำสั่ง takeoff ที่ตามมา
        → ไม่มีลำไหนขึ้นบิน. ลำดับที่ถูกคือ ขึ้นบินก่อน → รอลอยตัว → ค่อยจัดขบวน
        """
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("SWARM TAKEOFF"):
            return
        ids = self._selected_or_all() or sorted(self.fleet_items.keys())
        if not ids:
            self._show_toast("ยังไม่มีโดรนสำหรับ Swarm take off", "err")
            return
        if not self._preflight_gate("SWARM TAKEOFF"):
            return
        sep = self.sf_spacing.value()
        formation = self.form_picker.current() if hasattr(self, "form_picker") else 1
        alts = {d: self._alt_for(d) for d in ids}
        mode = self.takeoff_panel.mode() if hasattr(self, "takeoff_panel") else "sequential"
        ops = swarm_logic.build_swarm_takeoff_ops(
            ids, head_id=self._head_id, default_alt=self.sf_takeoff.value(),
            spacing=sep, formation=formation, mode=mode, alts=alts,
            priority=self._priority_ids())
        fname = FORMATION_NAMES.get(formation, str(formation))
        self._summ_set("swarm", "SWARM TAKEOFF",
                       f"{len(ids)} ลำ → Form-up {fname.split()[0]} sep {sep:.0f}m")
        self._log(f"SWARM TAKE OFF {len(ids)} ลำ — จะ FORM UP ({fname}, sep {sep:.0f}m) "
                  f"อัตโนมัติหลังลอยตัว", category="COMMAND")
        self._show_toast("SWARM · Take off แล้วจะ Form up ให้อัตโนมัติ", "info")

        # 1) ขึ้นบินก่อน (ตามโหมด All/Sequential)
        steps = [swarm_logic.TakeoffStep(o.payload["drone_id"], o.payload["alt"],
                                         o.payload["order"])
                 for o in ops if o.kind == "takeoff"]
        run_id = self._execute_takeoff_steps(steps, mode, swarm_prefix=True)
        if run_id is None:
            return

        # 2) รอให้ลอยตัวถึงระดับ แล้วค่อย form up (ไม่งั้นสั่งขบวนใส่โดรนที่ยังอยู่บนพื้น)
        target_alt = min(alts.values()) if alts else self.sf_takeoff.value()
        self._await_airborne_then_formup(ids, target_alt, sep, formation, run_id)

    def _await_airborne_then_formup(self, ids, target_alt, sep, formation, run_id=None):
        """รอให้ลอยตัวก่อนแล้วจึงสั่ง FORM UP

        BUGFIX: เดิมเริ่ม form-up เมื่อลอยครบ n-1 ลำ — ถ้า "ลำแม่" เป็นลำที่ช้าที่สุด
        (arm นานกว่าเพื่อน) ขบวนจะเริ่มทั้งที่แม่ยังอยู่บนพื้น → core ใช้ตำแหน่ง/ความสูง
        ของแม่ที่ alt≈0 เป็นจุดอ้างอิง สั่งลูกบินลงมาระดับพื้น และคำสั่ง takeoff ของแม่
        ก็ถูกขบวนตีทับ ผลคือ "ทุกลำขึ้น ยกเว้นลำแม่"
        ตอนนี้: ต้องรอ "ลำแม่ลอยตัวแล้ว" เป็นเงื่อนไขบังคับก่อนเสมอ
        """
        deadline = time.monotonic() + 90.0
        need = max(1.0, float(target_alt) * 0.6)
        head = int(self._head_id or 0)

        def check():
            if run_id is not None and not self._flight_is_current(run_id):
                return
            airborne = [d for d in ids
                        if float(self._last_alt.get(d, 0.0)) >= need]
            head_up = (head not in ids) or (head in airborne)
            timed_out = time.monotonic() > deadline

            if (head_up and len(airborne) >= max(2, len(ids) - 1)) or timed_out:
                if timed_out and not head_up:
                    # แม่ไม่ยอมขึ้นจริง ๆ — เตือนให้เห็นชัด อย่าปล่อยเงียบ
                    self._log(
                        f"⚠ FORM UP: ลำแม่ (Drone {head}) ยังไม่ลอยตัวหลังรอ 90 วิ "
                        f"— ตรวจ log TAKEOFF ของลำนี้ (pre-arm/GPS)",
                        vehicle=f"Drone {head}", category="ALERT", severity="ERROR")
                    self._show_banner(
                        f"ลำแม่ Drone {head} ไม่ขึ้นบิน — ยกเลิก FORM UP", T("red"))
                    self._show_toast(f"ลำแม่ D{head} ไม่ขึ้นบิน · ไม่ Form up", "err")
                    if run_id is not None:
                        self._flight_step_failed(run_id, "wait_airborne", "Head D%d not airborne" % head)
                    return          # ไม่ form up ถ้าไม่มีแม่ — กันขบวนพัง
                self._log(
                    f"FORM UP อัตโนมัติ — ลอยตัวแล้ว {len(airborne)}/{len(ids)} ลำ"
                    + (" (หมดเวลารอ)" if timed_out else ""),
                    category="COMMAND",
                    severity="WARNING" if timed_out else "INFO")
                if run_id is not None:
                    self._flight_step_done(run_id, "wait_airborne",
                                           "Airborne %d/%d · Head D%d ready" % (len(airborne), len(ids), head))
                    self._flight_step_active(run_id, "form_up")
                self._do_formup(sep, formation, run_id)
                return
            QTimer.singleShot(1000, check)

        QTimer.singleShot(2000, check)

    def _do_formup(self, sep, formation, run_id=None):
        targets = list(self._selected_or_all() or self._connected_ids())

        def worker():
            try:
                cfg = self._dispatch_core(
                    "SWARM CONFIG [form-up]",
                    lambda: self.client.swarm_config(sep, formation=formation),
                    source="swarm-orchestrator", targets=targets,
                    enforce_dedup=False)
                if not getattr(cfg, "ok", True):
                    self.cmd_result.emit(f"FORMATION: ok=False {cfg.message}")
                    if run_id is not None:
                        self.ui_call.emit(lambda r=run_id, m=cfg.message: self._flight_step_failed(r, "form_up", m))
                    return
                r = self._dispatch_core(
                    "SWARM START [form-up]", lambda: self.client.swarm_start(),
                    source="swarm-orchestrator", targets=targets,
                    enforce_dedup=False)
                self.cmd_result.emit(f"SWARM FORM UP: ok={r.ok} {r.message}")
                if run_id is not None:
                    if getattr(r, "ok", True):
                        self.ui_call.emit(lambda rid=run_id: self._flight_swarm_active(rid))
                    else:
                        self.ui_call.emit(lambda rid=run_id, m=r.message: self._flight_step_failed(rid, "form_up", m))
            except Exception as e:
                self.cmd_result.emit(f"SWARM FORM UP: ERROR {e}")
                if run_id is not None:
                    self.ui_call.emit(lambda rid=run_id, m=str(e): self._flight_step_failed(rid, "form_up", m))
        threading.Thread(target=worker, daemon=True).start()

    def _flight_swarm_active(self, run_id):
        self._flight_step_done(run_id, "form_up", "Formation accepted")
        self._flight_step_active(run_id, "swarm_active")
        self._flight_step_done(run_id, "swarm_active", "Swarm active")

    # ══════════════════════════════════════════════════════════
    #  RTL แบบแยกชั้นความสูง — กันชนตอนกลับฐาน (spec 2)
    # ══════════════════════════════════════════════════════════
    RTL_LAYER_GAP = 5.0        # core clamp อีกครั้งตาม MinSeparation
    RTL_BASE_ALT = 15.0        # ความสูงชั้นล่างสุดเหนือ home (m)

    def _staggered_rtl(self, ids):
        """ส่ง multi-drone RETURN ให้ core เป็นผู้จัดชั้น/จุดลง/ลำดับ LAND.

        ห้าม orchestrate รายลำจาก UI: timer อิสระเคยทำให้หลายลำลงพร้อมกันที่ home
        เดียวกัน และ E-STOP ยกเลิกคำสั่งที่รันค้างฝั่ง UI/core ไม่ครบ.
        """
        if not self._guard():
            return
        if bool(getattr(self, "_mission_core_authority", False)):
            self._abort_waypoint_execution()
        if self._rtl_active:
            self._show_toast("RTL กำลังทำงานอยู่", "info")
            return
        ids = sorted({int(d) for d in ids})
        if not ids:
            return
        self._rtl_active = True
        self._rtl_pending = set(ids)
        for did in ids:
            self._set_rtl_phase(did, "RETURN")
        desc = " · ".join(f"D{did}" for did in ids)
        self._log(
            f"RETURN + LAND {len(ids)} ลำ → core จัดชั้นและจุดลงแยกรายลำ: {desc}",
            category="COMMAND")
        self._summ_set("rtl", "RETURN + LAND", desc)
        self._show_toast(f"RETURN + LAND · {len(ids)} ลำ", "info")
        self._refresh_flight_mode()

        def worker():
            try:
                result = self._dispatch_core(
                    "SWARM RETURN", lambda: self.client.swarm_return(
                        ids, base_alt=self.RTL_BASE_ALT, gap=self.RTL_LAYER_GAP),
                    source="rtl-orchestrator", targets=ids,
                    enforce_dedup=False)
                self.cmd_result.emit(
                    f"RETURN + LAND {len(ids)} ลำ: ok={getattr(result, 'ok', False)} "
                    f"{getattr(result, 'message', '')}")
                if not bool(getattr(result, "ok", False)):
                    message = getattr(result, "message", "core rejected")
                    self.ui_call.emit(lambda m=message: self._rtl_core_rejected(m))
            except Exception as exc:
                message = str(exc)
                self.cmd_result.emit(f"RETURN + LAND: ERROR {message}")
                self.ui_call.emit(lambda m=message: self._rtl_core_uncertain(m))

        threading.Thread(target=worker, daemon=True).start()
        self._rtl_monitor_core(ids)

    def _rtl_core_rejected(self, message):
        self._log(f"RETURN + LAND ถูกปฏิเสธ: {message}",
                  category="ALERT", severity="WARNING")
        self._show_toast("Core ปฏิเสธ RETURN + LAND", "err")
        self._abort_rtl()

    def _rtl_core_uncertain(self, message):
        """RPC ขาดหลังส่งอาจหมายถึง core รับแล้ว ห้ามสรุปเองว่า operation หยุด."""
        self._log(f"RETURN + LAND ไม่ทราบผลตอบรับ: {message} — กด E-STOP หากต้องการยกเลิก",
                  category="ALERT", severity="ERROR")
        self._show_toast("RETURN ไม่ทราบผล · ใช้ E-STOP เพื่อยกเลิกอย่างแน่นอน", "err")

    def _rtl_monitor_core(self, ids):
        """ปิดสถานะ UI เมื่อ telemetry ยืนยันว่าทุกลำอยู่พื้นและ disarmed."""
        def check():
            if not self._rtl_active:
                return
            landed = []
            now_ms = int(time.time() * 1000)
            for did in ids:
                t = self._last_telem.get(int(did))
                if t is None:
                    continue
                ts_ms = int(getattr(t, "timestamp_ms", 0) or 0)
                if ts_ms and now_ms - ts_ms > 3000:
                    continue
                alt = float(getattr(
                    getattr(t, "position", None), "alt_rel",
                    self._last_alt.get(did, 0.0)) or 0.0)
                armed = bool(getattr(t, "armed", alt > 1.0))
                if not armed and alt < 1.0:
                    landed.append(did)
            for did in landed:
                if did in self._rtl_pending:
                    self._rtl_finish(did)
            if self._rtl_active:
                QTimer.singleShot(1000, check)

        QTimer.singleShot(1000, check)

    def _rtl_finish(self, did):
        """ลำนี้จบ pipeline — ถ้าครบทุกลำแล้วปิดโหมด RTL"""
        self._rtl_pending.discard(int(did))
        self._set_rtl_phase(did, None)
        if not self._rtl_pending:
            self._rtl_active = False
            self._log("RTL เสร็จสมบูรณ์ — ทุกลำลงจอดแล้ว",
                      category="COMMAND", severity="SUCCESS")
            self._show_toast("RTL เสร็จ · ทุกลำลงจอดแล้ว", "ok")
            self._summ_remove("rtl")
            self._refresh_flight_mode()

    def _set_rtl_phase(self, did, phase):
        """โชว์สถานะ RTL บนการ์ดโดรน (ซ้าย) — phase=None คือเลิกโชว์"""
        did = int(did)
        if phase:
            self._rtl_phase[did] = phase
        else:
            self._rtl_phase.pop(did, None)
        item = self.fleet_items.get(did)
        if item is not None:
            item.set_rtl_phase(phase)
        if hasattr(self, "sel_card") and self.sel_card.drone_id == did:
            self.sel_card.set_rtl_phase(phase)

    def _clear_all_rtl_phase(self):
        for did in list(getattr(self, "_rtl_phase", {})):
            self._set_rtl_phase(did, None)

    def _abort_rtl(self):
        if self._rtl_active:
            self._rtl_active = False
            self._rtl_pending = set()
            self._clear_all_rtl_phase()
            self._log("RTL ถูกยกเลิก", category="COMMAND", severity="WARNING")
            self._refresh_flight_mode()

    def _abort_waypoint_execution(self):
        """ตัดลำดับ Waypoint EXECUTE ที่ค้างอยู่ (ใช้ร่วมกันจาก Cancel Nav / E-STOP)

        เดิม E-STOP ไม่เรียกตัวนี้ — ทำให้ drones หยุดจริงบนอากาศ (stop_all)
        แต่ _waypoint_executing ยังค้างเป็น True: _wp_execute() รอบถัดไปจะขึ้น
        toast 'กำลังบินตามเส้นทางอยู่แล้ว' เฉยๆ ไม่ยอมเริ่มใหม่ จนกว่าจะกด
        Cancel Nav แยกต่างหากก่อน — งงว่าทำไมกด Execute ซ้ำแล้วไม่ขยับ
        """
        if (not self._waypoint_executing and not self._wave_executing
                and not self._wp_takeoff_pending):
            return
        mission_shadow.cancel(self)
        self._mission_core_authority = False
        self._wp_takeoff_generation += 1
        self._wp_takeoff_pending = False
        self._wp_wait_invalidate()   # WAIT ค้างอยู่ต้องถูกยกเลิก — no stale callback
        if self._wave_executing:
            self._wave_abort("ยกเลิกแล้ว", clear_route=True)
            return
        if self._flight_run and self._flight_run.kind == "waypoint":
            self._flight_run_cancel("Navigation cancelled")
        self._waypoint_executing = False
        self._wp_current_index = 0
        self._wp_arrived = set()
        self._wp_sep_index = {}
        self._js("clearAllWaypoints()")
        self._waypoint_route = None
        self._wp_routes = {}
        self._summ_event("ยกเลิก", "Waypoint EXECUTE ถูกยกเลิก", cancelled=True)
        self._summ_sync_waypoint()
        self._wp_render_status("ยกเลิกแล้ว")
        self._refresh_flight_mode()

    # ══════════════════════════════════════════════════════════
    #  EMERGENCY STOP (spec 4)
    # ══════════════════════════════════════════════════════════
    def _emergency_menu(self):
        """เมนูเลือกว่าจะหยุดลำไหน / ALL (ALL ต้องยืนยันซ้ำ)"""
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{T('panel2')}; color:{T('text')};"
            f" border:1px solid {rgba(T('red'), 0.4)}; border-radius:8px; padding:6px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:6px; }}"
            f"QMenu::item:selected {{ background:{rgba(T('red'), 0.28)}; }}")
        act_all = menu.addAction("■  STOP ALL (ทุกลำ)")
        act_all.triggered.connect(self._estop_all)
        menu.addSeparator()
        ids = self._connected_ids() or sorted(self.fleet_items.keys())
        if not ids:
            a = menu.addAction("— ไม่มีโดรน —")
            a.setEnabled(False)
        for did in ids:
            a = menu.addAction(f"หยุด {self._drone_name(did)}")
            a.triggered.connect(lambda _=False, d=did: self._on_estop_drone(d))
        menu.exec_(self.btn_estop_all.mapToGlobal(
            self.btn_estop_all.rect().bottomLeft()))

    def _on_estop_drone(self, drone_id):
        """หยุดฉุกเฉินลำเดียว — double confirm (spec 4)"""
        did = int(drone_id or 0)
        if not did:
            return
        name = self._drone_name(did)
        if not confirm_dlg.confirm_twice(
                self, "Emergency Stop",
                f"คุณต้องการหยุดการทำงานของ {name} ใช่หรือไม่?",
                f"ยืนยันหยุด {name} — โดรนจะหยุดเคลื่อนที่ทันที",
                accent=T("red")):
            return
        self._do_estop([did], name)

    def _estop_all(self):
        """STOP ALL — double confirm (spec 4)"""
        ids = self._connected_ids() or sorted(self.fleet_items.keys())
        if not ids:
            self._show_toast("ไม่มีโดรนให้หยุด", "err")
            return
        if not confirm_dlg.confirm_twice(
                self, "Emergency Stop — ALL",
                f"คุณต้องการหยุดโดรน 'ทุกลำ' ({len(ids)} ลำ) ใช่หรือไม่?",
                f"ยืนยันหยุดทั้งฝูง {len(ids)} ลำ — ทุกลำจะหยุดเคลื่อนที่ทันที",
                accent=T("red")):
            return
        self._do_estop(ids, f"ALL ({len(ids)} ลำ)")

    def _do_estop(self, ids, label):
        self._abort_rtl()                    # หยุดฉุกเฉินต้องตัดลำดับ RTL ที่ค้างอยู่ด้วย
        self._abort_waypoint_execution()      # และตัดลำดับ Waypoint EXECUTE ที่ค้างอยู่ด้วย
        self._log(f"EMERGENCY STOP → {label}", category="COMMAND", severity="WARNING")
        self._show_toast(f"EMERGENCY STOP · {label}", "err")
        self._show_banner(f"EMERGENCY STOP — {label}", T("red"))

        def worker():
            try:
                estop_ids = [int(i) for i in ids]
                r = self._dispatch_core(
                    "E-STOP", lambda: self.client.stop_all(estop_ids),
                    source="emergency", targets=estop_ids,
                    enforce_dedup=False)
                self.cmd_result.emit(f"EMERGENCY STOP {label}: ok={getattr(r, 'ok', True)}")
            except Exception as e:
                self.cmd_result.emit(f"EMERGENCY STOP {label}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    # ══════════════════════════════════════════════════════════
    #  PRE-FLIGHT COMMAND SUMMARY (spec 6)
    # ══════════════════════════════════════════════════════════
    def _summ_set(self, key, label, value):
        """Compatibility wrapper; presentation mapping lives in SummaryPresenter."""
        return self._summary_presenter.set(
            key, label, value, render=not self._summ_batch)

    def _summ_remove(self, key):
        """Compatibility wrapper retained while call sites remain in GroundStation."""
        return self._summary_presenter.remove(key, render=not self._summ_batch)

    def _summ_event(self, label, value, *, cancelled=False):
        """เหตุการณ์ กด/ตั้งค่า/ยกเลิก — เข้า Mission Log (หมวด COMMAND) ไม่ใช่ Pre-flight Summary

        Pre-flight Summary เก็บเฉพาะ "แผนที่จะมีผลตอนบิน" (ลำที่เลือก, Head, Take off,
        Waypoint/A-B, WAVE, Swarm, RTL config) — เป็นภาพนิ่งของสิ่งที่ *กำลังจะสั่ง*
        ส่วนประวัติการกด/การยกเลิก/ผลลัพธ์ ไปไล่ดูย้อนหลังที่ Mission Log แทน
        """
        return self._summary_presenter.event(label, value, cancelled=cancelled)

    def _summ_sync_waypoint(self):
        """สะท้อนแผน Waypoint/WAVE/A-B ปัจจุบันลง Pre-flight Summary"""
        # รวบทุก set/remove ในรอบนี้แล้วค่อยวาดครั้งเดียว — กันวาดซ้ำ 4-5 รอบต่อการกดปุ่มเดียว
        # (เดิมทุก _summ_set ลบ+สร้าง widget ใหม่ทั้งกล่อง เสี่ยงกระตุกและ churn กับแผนที่ WebEngine)
        self._summ_batch = True
        try:
            self._summ_sync_waypoint_body()
        finally:
            self._summ_batch = False
        self._render_summary()

    def _summ_sync_waypoint_body(self):
        # ซ่อนแผนที่ค้างไว้เมื่อผู้ใช้ปิด Waypoint Mode แล้ว เว้นแต่กำลังบินจริง
        planning_active = (self._waypoint_mode or self._waypoint_executing
                           or self._wave_executing)
        if not planning_active:
            for key in ("wp_mode", "wp_route", "wp_actions", "wp_wait", "wave"):
                self._summ_remove(key)
            return

        mode = "SEPARATE (แยกลำ)" if self._wp_separate else "GROUPED (ร่วม)"
        state = ("กำลัง EXECUTE" if (self._waypoint_executing or self._wave_executing)
                 else "เปิดวางจุด")
        self._summ_set("wp_mode", "WAYPOINT MODE", f"{state} · {mode}")

        routes = self._wp_all_routes()
        unique = self._wp_unique_routes(routes) if routes else []
        total = sum(len(route) for route in unique)
        if total:
            ids = sorted(routes)
            targets = ", ".join(f"D{did}" for did in ids)
            self._summ_set(
                "wp_route", "WAYPOINT ROUTE",
                f"{total} จุด · {mode.split()[0]} · {targets}")
            actions = []
            for route in unique:
                for wp in route.points:
                    if wp.action:
                        ab = "A" if wp.action == "servo_a" else "B"
                        actions.append(f"#{wp.index + 1}={ab}")
            if actions:
                self._summ_set("wp_actions", "WAYPOINT A/B", " · ".join(actions))
            else:
                self._summ_remove("wp_actions")
            # WAIT — วางถัดจาก PAYLOAD A/B ก่อน WAVE (spec §11)
            waits = []
            for route in unique:
                for pos, minutes in route.wait_summary():
                    waits.append(f"#{pos}={minutes}m")
            if waits:
                self._summ_set("wp_wait", "WAIT", " · ".join(waits))
            else:
                self._summ_remove("wp_wait")
        else:
            self._summ_remove("wp_route")
            self._summ_remove("wp_actions")
            self._summ_remove("wp_wait")

        if self._wave_enabled:
            groups = self._wave_selected_groups()
            sequence = " → ".join(map(str, groups)) if groups else "ยังไม่เลือกกลุ่ม"
            auto_next = (hasattr(self, "chk_wave_auto_next")
                         and self.chk_wave_auto_next.isChecked())
            suffix = " · AUTO NEXT GROUP ✓" if auto_next else ""
            self._summ_set("wave", "WAVE", f"เปิด · กลุ่ม {sequence}{suffix}")
        else:
            self._summ_remove("wave")

    def _render_summary(self):
        """Compatibility wrapper; presenter remains read-only flight presentation."""
        self._summary_presenter.render(self._flight_run)

    def _flight_snapshot(self):
        """A plain copy so plan edits never alter a mission already in progress."""
        return self._summary_presenter.snapshot()

    def _flight_run_start(self, kind, *, mode="all", auto_takeoff=True,
                          has_actions=False, groups=()):
        snapshot = self._flight_snapshot()
        if kind == "takeoff":
            run = flight_progress.build_takeoff_flow(snapshot, mode)
        elif kind == "swarm_takeoff":
            run = flight_progress.build_swarm_takeoff_flow(snapshot, mode)
        elif kind == "waypoint":
            run = flight_progress.build_waypoint_flow(
                snapshot, auto_takeoff=auto_takeoff, has_actions=has_actions)
        elif kind == "wave":
            run = flight_progress.build_wave_flow(snapshot, groups)
        else:
            raise ValueError("unknown flight run kind: %s" % kind)
        self._flight_run = run
        self._flight_run_id = run.run_id
        run.active(run.steps[0].id)
        run.complete(run.steps[0].id)
        # All mission builders use the gate as their second trusted transition.
        if len(run.steps) > 1 and run.steps[1].id in ("preflight_gate", "confirm_mission"):
            run.active(run.steps[1].id, "Passed")
            run.complete(run.steps[1].id)
        self._render_summary()
        return run.run_id

    def _flight_is_current(self, run_id):
        return bool(self._flight_run and self._flight_run.run_id == run_id)

    def _flight_step_active(self, run_id, step_id, detail=None):
        if self._flight_is_current(run_id):
            self._flight_run.active(step_id, detail)
            self._render_summary()

    def _flight_step_done(self, run_id, step_id, detail=None):
        if self._flight_is_current(run_id):
            self._flight_run.complete(step_id, detail)
            self._render_summary()

    def _flight_step_failed(self, run_id, step_id, reason):
        if self._flight_is_current(run_id):
            self._flight_run.fail(step_id, reason)
            self._render_summary()

    def _flight_run_cancel(self, reason="Cancelled"):
        if self._flight_run is not None:
            self._flight_run.cancel(reason)
            self._render_summary()

    def _clear_summary(self):
        self._cmd_summary.clear()
        # Clear is the explicit boundary at which the retained completed run may
        # be removed.  Active operations are cancelled first so old callbacks
        # cannot repaint a newly cleared/new summary.
        if self._flight_run is not None:
            self._flight_run_cancel("Cleared")
        self._flight_run = None
        self._flight_run_id = 0
        self._render_summary()
        self._show_toast("ล้างสรุปคำสั่งแล้ว", "info")

    # per-drone (จากการ์ด)
    def _card_cmd(self, label, drone_id, fn):
        if not self._guard():
            return
        self._log(f"{label}", vehicle=f"Drone {drone_id}", category="COMMAND")
        self._show_toast(f"{label} · ส่งคำสั่ง…", "info")

        def worker():
            try:
                # V3-S07: pass-through gateway boundary (card/quick-action source).
                r = self._gateway.dispatch(
                    label, fn, source="card", targets=[int(drone_id)])
                if getattr(r, "in_progress", False):
                    self.cmd_result.emit(
                        f"{label}: in_progress {getattr(r, 'message', '') or ''}".strip())
                else:
                    self.cmd_result.emit(
                        f"{label}: ok={getattr(r, 'ok', True)} {getattr(r, 'message', '')}".strip())
            except Exception as e:
                self.cmd_result.emit(f"{label}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _card_arm(self, drone_id):
        """Quick action ARM รายลำ (spec 3)"""
        if self._manual_nav_blocked_by_core("ARM"):
            return
        self._card_cmd("ARM", drone_id, lambda: self.client.arm([drone_id]))

    def _card_disarm(self, drone_id):
        """Quick action DISARM รายลำ — ยืนยันก่อน กันสั่งพลาดกลางอากาศ (spec 3)"""
        did = int(drone_id or 0)
        if not did:
            return
        if not self._confirm(
                self, "DISARM",
                f"ต้องการ DISARM {self._drone_name(did)} ใช่หรือไม่?\n"
                f"ถ้าโดรนกำลังลอยอยู่ จะตกทันที",
                ok_text="DISARM", danger=True):
            return
        self._card_core_takeover_if_owned(did)
        self._card_cmd("DISARM", did,
                       lambda: self.client.disarm([did], confirmed=True))

    def _card_core_takeover_if_owned(self, drone_id):
        did = int(drone_id or 0)
        if not bool(getattr(self, "_mission_core_authority", False)):
            return
        participants = {int(d) for d in getattr(self, "_wp_target_ids", [])}
        if participants and did not in participants:
            return
        self._abort_waypoint_execution()

    def _card_hold(self, drone_id):
        self._card_core_takeover_if_owned(drone_id)
        self._card_cmd("HOLD", drone_id, lambda: self.client.hold([drone_id]))

    def _card_rtl(self, drone_id):
        self._card_core_takeover_if_owned(drone_id)
        self._card_cmd("RTL", drone_id, lambda: self.client.rtl([drone_id]))

    def _card_land(self, drone_id):
        self._card_core_takeover_if_owned(drone_id)
        self._card_cmd("LAND", drone_id, lambda: self.client.land([drone_id]))

    @staticmethod
    def _norm_host(host: str) -> str:
        h = (host or "").strip().lower()
        if h in ("localhost", "::1"):
            return "127.0.0.1"
        return h

    def _dup_ip_key(self, host: str, port: int = 0) -> str:
        """LAN ใช้ host อย่างเดียว · localhost/SITL ใช้ host:port"""
        h = self._norm_host(host)
        if not h:
            return ""
        if h == "127.0.0.1":
            return f"{h}:{int(port or 0)}"
        return h

    def _remember_endpoint(self, drone_id: int, host: str, port: int = 0,
                           protocol: str = "", name: str = ""):
        did = int(drone_id or 0)
        host = (host or "").strip()
        if not did or not host:
            return
        port = int(port or 0) or 5760
        explicit_name = bool((name or "").strip())
        if not name:
            name = self._drone_names.get(did, "")
        if not name:
            # รักษาชื่อที่ผู้ใช้เคยตั้งไว้ เมื่อ reconnect endpoint เดิม
            try:
                saved = self._ip_store.get(did)
                if (saved and self._norm_host(saved.host) == self._norm_host(host)
                        and int(saved.port or 0) == port and saved.name):
                    name = saved.name
            except Exception:
                pass
        # PERF: เดิมเขียน SQLite ทุกครั้งที่ถูกเรียก ซึ่ง _on_telemetry เรียกทุกแพ็กเก็ต
        # (10 Hz ต่อลำ × 5 ลำ = 50 ครั้ง/วิ) — เปิด connection + เขียนดิสก์รัวขนาดนั้น
        # ทำให้ main thread ตัน คลิกปุ่มแล้วหน่วง · ค่าจริงแทบไม่เคยเปลี่ยน
        # จึงข้ามทั้งหมดถ้า endpoint เดิมกับที่จำไว้แล้ว
        unchanged = self._endpoints.get(did) == (host, port)
        self._endpoints[did] = (host, port)
        item = self.fleet_items.get(did)
        if item is not None:
            item._host = host
            item._port = port
        if unchanged and not protocol and not explicit_name:
            return
        proto = (protocol or "").strip().lower()
        if not proto:
            proto = getattr(self, "_connect_protocol", "tcp")
        if not proto:
            proto = "tcp"
        if not name:
            t = self._last_telem.get(did)
            if t is not None and getattr(t, "name", ""):
                name = t.name
            else:
                name = f"Drone {did}"
        self._drone_names[did] = name
        try:
            self._ip_store.upsert(did, host, port, protocol=proto, name=name)
        except Exception as e:
            self._log(f"IP store save failed: {e}", severity="WARNING", category="STATUS")

    def _forget_endpoint(self, drone_id: int, persist: bool = False):
        """persist=True ลบออกจาก SQLite ด้วย (ใช้ตอน DEL)"""
        did = int(drone_id or 0)
        self._endpoints.pop(did, None)
        if not did or not persist:
            return
        try:
            self._ip_store.delete(did)
        except Exception as e:
            self._log(f"IP store delete failed: {e}", severity="WARNING", category="STATUS")

    def _load_saved_ips(self):
        """เปิดโปรแกรมมาแล้วฝูงว่างเสมอ — ไม่กู้โดรนที่เคยต่อจาก SQLite

        ผู้ใช้เลือกให้ทำแบบนี้: เปิดมาแล้วกด SCAN หรือ CONNECT เอง

        เดิมเมธอดนี้สร้างการ์ดโดรนให้ทุกลำที่เคย add (แสดงเป็น OFFLINE) ซึ่งเป็น
        ต้นเหตุของบั๊ก §11.24 — การ์ด offline ที่มี ID ต่ำกว่าลำจริงดูดคำสั่งไปกิน
        จน "กดครั้งแรกไม่ติด" · แก้ที่ต้นเหตุไปแล้วก็จริง แต่การไม่มีการ์ดค้างเลย
        ตัดความสับสนเรื่อง "ตอนนี้กำลังสั่งลำไหนอยู่" ออกทั้งหมด และทำให้
        `_find_duplicate_ip()` ไม่เอา IP เก่าที่ไม่ได้ต่อแล้วมาบล็อกการ connect ใหม่

        SQLite (`~/.swarmgod/fleet_ips.db`) ยังถูกเขียนอยู่ตามปกติ — ปุ่ม DEL และ
        การกัน IP ซ้ำภายในเซสชันยังทำงานเหมือนเดิม แค่ไม่ถูกอ่านมาสร้างการ์ดตอนเปิด

        อยากได้พฤติกรรมเดิมคืน: กู้โค้ดเวอร์ชันก่อนของเมธอดนี้จาก git history
        """
        return

    def _collect_settings(self) -> dict:
        """รวบรวมการตั้งค่าปัจจุบันเป็น dict"""
        ips = []
        try:
            for s in self._ip_store.list_all():
                ips.append({
                    "drone_id": s.drone_id,
                    "host": s.host,
                    "port": int(s.port or 5760),
                    "protocol": s.protocol or "tcp",
                    "name": s.name or f"Drone {s.drone_id}",
                })
        except Exception:
            for did, (host, port) in self._endpoints.items():
                ips.append({
                    "drone_id": int(did),
                    "host": host,
                    "port": int(port or 5760),
                    "protocol": "tcp",
                    "name": f"Drone {did}",
                })

        def _sf(name, default=0.0):
            w = getattr(self, name, None)
            try:
                return float(w.value()) if w is not None else float(default)
            except Exception:
                return float(default)

        formation = 1
        if hasattr(self, "form_picker"):
            try:
                formation = int(self.form_picker.current())
            except Exception:
                formation = 1

        return {
            "version": settings_io.SETTINGS_VERSION,
            "protocol": getattr(self, "_connect_protocol", "tcp"),
            "endpoint": getattr(self, "_connect_endpoint", ""),
            "ui_mode": bool(getattr(self, "_ui_mode", True)),
            "target_mode": getattr(self, "_target_mode", "selected"),
            "formation": formation,
            "font_scale": getattr(self, '_font_scale', 1.2),
            "sliders": {
                "takeoff_alt": _sf("sf_takeoff", 20),
                "speed": _sf("sf_speed", 3),
                "set_alt": _sf("sf_setalt", 30),
                "spacing": _sf("sf_spacing", 12),
                "offset": _sf("sf_offset", 5),
                "form_speed": _sf("sf_formspeed", 4),
                "max_drones": _sf("sf_maxdrones", 20),
				"rtl_alt": _sf("sf_rtl_alt", 15),
                "rtl_gap": _sf("sf_rtl_gap", 5),
            },
            "drone_colors": {str(k): v for k, v in DRONE_COLORS.items()},
            "saved_ips": ips,
        }

    def _apply_settings(self, data: dict, *, replace_ips: bool = True) -> None:
        if not isinstance(data, dict):
            raise ValueError("invalid settings")

        proto = str(data.get("protocol") or "").strip().upper()
        if proto in ("TCP", "UDP"):
            self._connect_protocol = proto.lower()

        endpoint = str(data.get("endpoint") or "").strip()
        if endpoint:
            self._connect_endpoint = endpoint

        if "ui_mode" in data and hasattr(self, "sw_control"):
            ui = bool(data.get("ui_mode"))
            idx = 0 if ui else 1
            try:
                self.sw_control.setCurrent(idx)
            except Exception:
                self._on_control_changed(idx)

        tm = data.get("target_mode")
        if tm in ("selected", "fleet"):
            self._target_mode = tm

        fid = data.get("formation")
        if fid is not None and hasattr(self, "form_picker"):
            try:
                self.form_picker.set_current(int(fid))
            except Exception:
                pass

        # โหลด font_scale จาก settings
        fs = data.get("font_scale")
        if fs is not None:
            try:
                self._set_font_scale(float(fs))
            except Exception:
                pass

        sliders = data.get("sliders") or {}
        mapping = {
            "takeoff_alt": "sf_takeoff",
            "speed": "sf_speed",
            "set_alt": "sf_setalt",
            "spacing": "sf_spacing",
            "offset": "sf_offset",
            "form_speed": "sf_formspeed",
            "max_drones": "sf_maxdrones",
            "rtl_alt": "sf_rtl_alt",
            "rtl_gap": "sf_rtl_gap",
        }
        for key, attr in mapping.items():
            if key not in sliders:
                continue
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                w.setValue(float(sliders[key]))
            except Exception:
                pass

        # นำค่า RTL ที่โหลดมาไปใช้กับ runtime จริง (ไม่ใช่แค่เลื่อน slider)
        if hasattr(self, "sf_rtl_alt"):
            self.RTL_BASE_ALT = float(self.sf_rtl_alt.value())
        if hasattr(self, "sf_rtl_gap"):
            self.RTL_LAYER_GAP = float(self.sf_rtl_gap.value())
        self._update_rtl_preview()

        colors = data.get("drone_colors") or {}
        for k, col in colors.items():
            try:
                did = int(k)
                set_drone_color(did, str(col))
                self._on_drone_color(did, str(col))
            except Exception:
                pass

        ips = data.get("saved_ips")
        if isinstance(ips, list) and replace_ips:
            # ล้างรายการเก่าใน DB แล้วใส่ชุดใหม่
            try:
                for old in self._ip_store.list_all():
                    if old.drone_id not in self.fleet_items:
                        self._ip_store.delete(old.drone_id)
            except Exception:
                pass
            for row in ips:
                try:
                    did = int(row.get("drone_id") or 0)
                    host = str(row.get("host") or "").strip()
                    port = int(row.get("port") or 5760)
                    if not did or not host:
                        continue
                    name = str(row.get("name") or f"Drone {did}")
                    protocol = str(row.get("protocol") or "tcp")
                    self._removed_ids.discard(did)
                    self._remember_endpoint(did, host, port, protocol=protocol, name=name)
                    if did not in self.fleet_items:
                        item = FleetItem(did, name, self._pixmap)
                        item._host = host
                        item._port = port
                        self._wire_fleet_item(item)
                        self.fleet_items[did] = item
                        self.fleet_area.addWidget(item)
                    else:
                        self.fleet_items[did]._host = host
                        self.fleet_items[did]._port = port
                except Exception:
                    continue
            self._refresh_online_count()
            self._refresh_fleet_count()
            self._update_fleet_scroll_height()
            self._refresh_leader_combo()
            self._refresh_takeoff_panel()
            self._update_head_ui()
            self._refresh_group_ui()
            if hasattr(self, "mlog"):
                self.mlog.set_vehicles(self.fleet_items.keys())

    def _save_settings(self):
        try:
            path = settings_io.save_json(
                settings_io.default_settings_path(), self._collect_settings())
            self._show_toast("บันทึกการตั้งค่าแล้ว", "ok")
            self._log(f"SAVE settings → {path}", category="STATUS")
        except Exception as e:
            self._show_toast(f"SAVE ล้มเหลว: {e}", "err")
            self._log(f"SAVE settings failed: {e}", severity="ERROR", category="STATUS")

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings",
            os.path.join(os.path.expanduser("~"), "swarmgod_settings.json"),
            "JSON (*.json);;All Files (*)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            out = settings_io.save_json(path, self._collect_settings())
            self._show_toast("EXPORT สำเร็จ", "ok")
            self._log(f"EXPORT settings → {out}", category="STATUS")
        except Exception as e:
            self._show_toast(f"EXPORT ล้มเหลว: {e}", "err")
            self._log(f"EXPORT settings failed: {e}", severity="ERROR", category="STATUS")

    def _load_settings_dialog(self):
        start = settings_io.default_settings_path()
        start_dir = os.path.dirname(start) if os.path.isfile(start) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Settings", start_dir,
            "JSON (*.json);;All Files (*)")
        if not path:
            return
        self._load_settings_from(path)

    def _load_saved_settings(self):
        path = settings_io.default_settings_path()
        if not os.path.isfile(path):
            self._show_toast("ยังไม่มีไฟล์ที่ SAVE ไว้", "err")
            return
        self._load_settings_from(path)

    def _load_settings_from(self, path: str):
        try:
            data = settings_io.load_json(path)
            self._apply_settings(data, replace_ips=True)
            # sync default save slot ด้วย
            try:
                settings_io.save_json(settings_io.default_settings_path(), data)
            except Exception:
                pass
            self._show_toast("โหลดการตั้งค่าแล้ว", "ok")
            self._log(f"LOAD settings ← {path}", category="STATUS")
        except Exception as e:
            self._show_toast(f"LOAD ล้มเหลว: {e}", "err")
            self._log(f"LOAD settings failed: {e}", severity="ERROR", category="STATUS")

    def _autoload_settings(self):
        """โหลดค่าที่ SAVE ไว้ตอนเปิดแอป (ไม่ทับ IP จาก SQLite ถ้าไฟล์ไม่มี saved_ips)"""
        try:
            data = settings_io.try_load_default()
        except Exception as e:
            self._log(f"autoload settings failed: {e}", severity="WARNING", category="STATUS")
            return
        if not data:
            return
        try:
            # ตอนเปิดโปรแกรม **ไม่กู้รายชื่อโดรน** ไม่ว่าจากทางไหน — ฝูงต้องเริ่มว่างเสมอ
            # (คู่กับ `_load_saved_ips()` ที่ปิดไปแล้ว · ดู §11.25)
            # เดิมบล็อก saved_ips ใน `_apply_settings` เป็นเส้นทางกู้การ์ด offline ที่สอง
            # ซึ่งหลุดรอดมา ทำให้ยังมีการ์ดค้างแม้ปิด SQLite restore ไปแล้ว
            # ปุ่ม LOAD ที่ผู้ใช้กดเองยังกู้ IP ได้ตามปกติ (เจตนาชัดเจนกว่า)
            self._apply_settings(data, replace_ips=False)
            self._log("autoload cockpit settings", category="STATUS")
        except Exception as e:
            self._log(f"apply autoload settings failed: {e}",
                      severity="WARNING", category="STATUS")

    def _find_duplicate_ip(self, host: str, port: int = 0, exclude_id: int = 0) -> int:
        """คืน drone_id ที่ใช้ IP ซ้ำ หรือ 0 ถ้าไม่ซ้ำ"""
        key = self._dup_ip_key(host, port)
        if not key:
            return 0
        exclude_id = int(exclude_id or 0)

        for did, (eh, ep) in list(self._endpoints.items()):
            if exclude_id and int(did) == exclude_id:
                continue
            if int(did) in self._removed_ids:
                continue
            if self._dup_ip_key(eh, ep) == key:
                return int(did)

        for did, t in list(self._last_telem.items()):
            if exclude_id and int(did) == exclude_id:
                continue
            if int(did) in self._removed_ids:
                continue
            th = (getattr(t, "host", "") or "").strip()
            tp = int(getattr(t, "port", 0) or 0)
            if th and self._dup_ip_key(th, tp) == key:
                return int(did)

        # ช่อง IP บน fleets list (ถ้ามี cache ใน item)
        for did, item in list(self.fleet_items.items()):
            if exclude_id and int(did) == exclude_id:
                continue
            eh = (getattr(item, "_host", "") or "").strip()
            ep = int(getattr(item, "_port", 0) or 0)
            if eh and self._dup_ip_key(eh, ep) == key:
                return int(did)

        # หมายเหตุ: เดิมมีด่านสุดท้ายที่ query SQLite ด้วย ("กรณี memory ยังไม่ครบ")
        # ตอนนี้เอาออกแล้ว เพราะฝูงเริ่มต้นว่างเสมอ (§11.25) แถวใน SQLite จึงไม่ได้แปลว่า
        # "โดรนตัวนั้นกำลังต่ออยู่ในเซสชันนี้" อีกต่อไป — ปล่อยไว้จะกลายเป็นว่า IP ที่เคย
        # ต่อเมื่อไหร่ก็ตาม **บล็อกการ connect ใหม่ถาวร** และไม่มีการ์ดให้กด DEL ล้างด้วย
        #
        # กติกาที่ถูกต้องคือ "ห้ามต่อโดรน 2 ลำเข้า IP เดียวกัน **ตอนนี้**"
        # ซึ่งดูจาก endpoint/telemetry/การ์ดในเซสชันปัจจุบันครบแล้ว
        return 0

    def _reject_duplicate_ip(self, host: str, port: int = 0, exclude_id: int = 0) -> bool:
        """True = ซ้ำ ห้าม connect (ขึ้น toast)"""
        other = self._find_duplicate_ip(host, port, exclude_id=exclude_id)
        if not other:
            return False
        tip = f"{host}:{port}" if self._norm_host(host) == "127.0.0.1" else host
        self._show_toast(f"IP ซ้ำ · {tip} ใช้โดย Drone {other}", "err")
        self._log(f"CONNECT ปฏิเสธ: IP ซ้ำ ({tip}) กับ Drone {other}",
                  severity="WARNING", category="COMMAND")
        return True

    def _card_connect(self, drone_id: int, host: str, port: int):
        # กันไว้อีกชั้น: host ที่ส่งมาอาจติดพอร์ตมาด้วย ("10.0.0.1:5760")
        # ถ้าปล่อยผ่านจะไป dial เป็น "10.0.0.1:5760:5760" แล้ว reader ตายเงียบ ๆ
        host, port = split_host_port(host, int(port or 0))
        port = int(port or 0)
        if not host:
            # ลองดึงจาก endpoint ล่าสุดที่กรอกใน popup CONNECT
            target = getattr(self, "_connect_endpoint", "")
            host, port = split_host_port(target, port)
        if not port:
            port = 5760
        if not host:
            self._show_toast("CONN · ใส่ IP ก่อน", "err")
            self._log("CONNECT ล้มเหลว: ไม่มี IP", severity="WARNING", category="COMMAND")
            return

        did = int(drone_id or 0) or self._next_drone_id()
        if self._reject_duplicate_ip(host, port, exclude_id=did if drone_id else 0):
            return
        self._removed_ids.discard(did)
        self._remember_endpoint(did, host, port)
        name = f"Drone {did}"
        t = self._last_telem.get(did)
        if t is not None and getattr(t, "name", ""):
            name = t.name
        proto = getattr(self, "_connect_protocol", "tcp")
        # อัปเดตช่องบนการ์ด
        if hasattr(self, "sel_card") and self.sel_card.drone_id in (0, did):
            self.sel_card.drone_id = did
            if not self.sel_card.ed_host.hasFocus():
                self.sel_card.ed_host.setText(host)
            if not self.sel_card.ed_port.hasFocus():
                self.sel_card.ed_port.setText(str(port))
        self._connect_endpoint = f"{host}:{port}"

        self._log(f"CONNECT {name} → {host}:{port} [{proto}]", vehicle=f"Drone {did}",
                  category="COMMAND")
        self._show_toast(f"CONNECT {host}:{port}", "info")

        def worker():
            try:
                r = self.client.connect_drone(did, name, host, port, protocol=proto)
                self.cmd_result.emit(f"CONNECT: ok={r.ok} {r.message}")
            except Exception as e:
                self.cmd_result.emit(f"CONNECT: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _card_disconnect(self, drone_id: int):
        did = int(drone_id or 0) or self._selected_id
        if not did:
            self._show_toast("DISC · เลือกโดรนก่อน", "err")
            return
        self._log(f"DISCONNECT Drone {did} · กำลังตัดการเชื่อมต่อ",
                  vehicle=f"Drone {did}", category="COMMAND")
        self._show_toast(f"กำลังตัด Drone {did}…", "info")
        # ตัดทันทีฝั่ง UI: อัปเดตสถานะเป็น "ตัดแล้ว" + ให้ระบบถือว่า offline
        self._mark_disconnected(did)

        def worker():
            try:
                r = self.client.disconnect_drone(did)
                ok = getattr(r, "ok", True)
                self.cmd_result.emit(
                    f"DISCONNECT Drone {did}: ok={ok} "
                    f"{getattr(r, 'message', '') or 'ตัดการเชื่อมต่อแล้ว'}".strip())
            except Exception as e:
                self.cmd_result.emit(f"DISCONNECT Drone {did}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _mark_disconnected(self, did: int):
        """อัปเดต UI ให้เห็นชัดว่า 'ตัดการเชื่อมต่อแล้ว' + ปลดออกจากชุด online"""
        did = int(did or 0)
        # ปลดออกจากสถานะ online → auto-reassign head + connected-detection เห็นว่าหลุด
        self._last_seen.pop(did, None)
        self._last_telem.pop(did, None)
        self._telemetry_store.forget(did)
        self._telemetry_render_gate.forget(did)
        self._health.forget_drone(did)
        item = self.fleet_items.get(did)
        if item is not None:
            item.badge.setText("OFFLINE")
            item.badge.setStyleSheet(badge_style("OFFLINE"))
            item.dot.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
            item.lbl_meta.setText("● ตัดการเชื่อมต่อแล้ว")
            item.lbl_bat.setText("--%")
        if hasattr(self, "sel_card") and self.sel_card.drone_id == did:
            self.sel_card.badge.setText("OFFLINE")
            self.sel_card.badge.setStyleSheet(badge_style("OFFLINE"))
            self.sel_card.set_quick_enabled(False)
            if hasattr(self.sel_card, "lbl_link"):
                self.sel_card.lbl_link.setText("LINK ตัดแล้ว")
        self._js(f"clearDrone({did})")
        # หัวถูกตัด → เลื่อนหัวใหม่ทันที (spec 1)
        if getattr(self, "_head_id", 0) == did:
            self._head_id = 0
            new_head, _ = swarm_logic.next_head_after_loss(
                did, self._connected_ids(), priority=self._priority_ids())
            self._apply_head(new_head, push=(new_head != 0), reason="auto")
        self._refresh_takeoff_panel()
        self._update_head_ui()

    def _card_apply_ip(self, drone_id: int, host: str, port: int):
        """แก้ IP แล้ว reconnect"""
        host = (host or "").strip()
        port = int(port or 0) or 5760
        if not host:
            self._show_toast("APPLY · ใส่ IP ก่อน", "err")
            return
        did = int(drone_id or 0) or self._selected_id or self._next_drone_id()
        if self._reject_duplicate_ip(host, port, exclude_id=did):
            return
        self._removed_ids.discard(did)
        self._remember_endpoint(did, host, port)
        name = f"Drone {did}"
        t = self._last_telem.get(did)
        if t is not None and getattr(t, "name", ""):
            name = t.name
        proto = getattr(self, "_connect_protocol", "tcp")
        self._log(f"APPLY IP {host}:{port}", vehicle=f"Drone {did}", category="COMMAND")
        self._show_toast(f"IP → {host}:{port}", "info")
        self._connect_endpoint = f"{host}:{port}"

        def worker():
            try:
                try:
                    self.client.disconnect_drone(did)
                except Exception:
                    pass
                r = self.client.connect_drone(did, name, host, port, protocol=proto)
                self.cmd_result.emit(f"APPLY IP: ok={r.ok} {r.message}")
            except Exception as e:
                self.cmd_result.emit(f"APPLY IP: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _card_delete(self, drone_id: int):
        """ตัดลิงก์ + เอาออกจากรายการ fleets/แผนที่ทันที"""
        did = int(drone_id or 0) or int(self._selected_id or 0)
        if not did and hasattr(self, "sel_card"):
            did = int(self.sel_card.drone_id or 0)
        if not did:
            # ยังมีรายการใน fleets — ลบตัวที่เลือกไม่ได้ก็ลบตัวแรก
            if self.fleet_items:
                did = next(iter(sorted(self.fleet_items.keys())))
            else:
                self._show_toast("DEL · ไม่มีโดรนในรายการ", "err")
                return

        self._removed_ids.add(did)
        self._log(f"DELETE Drone {did} · remove from fleet",
                  vehicle=f"Drone {did}", category="COMMAND")
        self._show_toast(f"ลบ Drone {did} ออกแล้ว", "info")
        # ลบ UI ทันทีใน main thread (อย่าใช้ QTimer จาก worker)
        self._remove_fleet_item(did)

        def worker():
            try:
                r = self.client.disconnect_drone(did)
                self.cmd_result.emit(
                    f"DELETE: ok={getattr(r, 'ok', True)} {getattr(r, 'message', '')}".strip())
            except Exception as e:
                self.cmd_result.emit(f"DELETE: disconnect ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _remove_fleet_item(self, drone_id: int):
        item = self.fleet_items.pop(drone_id, None)
        if item is not None:
            self.fleet_area.removeWidget(item)
            item.setParent(None)
            item.deleteLater()
        self._last_telem.pop(drone_id, None)
        self._last_seen.pop(drone_id, None)
        self._telemetry_store.forget(drone_id)
        self._telemetry_render_gate.forget(drone_id)
        self._health.forget_drone(drone_id)
        self._reserved_ids.discard(drone_id)
        self._servo_state.pop(drone_id, None)
        # ลบออกจากฝูงแล้วต้องล้างสถานะอุ่นเครื่องด้วย — ต่อกลับมาใหม่ FC เริ่มนับใหม่
        for lb in self.SERVO_CH:
            self._servo_primed.discard((int(drone_id), lb))
            self._servo_priming.pop((int(drone_id), lb), None)
        self._last_alt.pop(drone_id, None)
        self._drone_names.pop(drone_id, None)
        self.group_of.pop(int(drone_id), None)
        self.field_hub.remove(drone_id)   # ไม่งั้นค้างบน tablet เป็นลำผี
        try:
            self._group_store.clear_drone(int(drone_id))
        except Exception as exc:
            self._log(f"Group store delete failed: {exc}", severity="WARNING", category="STATUS")
        self._forget_endpoint(drone_id, persist=True)
        if getattr(self, "_leader_id", 0) == drone_id:
            self._leader_id = 0
        # หัวถูกลบ → เลื่อนหัวใหม่ทันที (spec 1)
        if getattr(self, "_head_id", 0) == drone_id:
            self._head_id = 0
            new_head, _ = swarm_logic.next_head_after_loss(
                drone_id, self._connected_ids(), priority=self._priority_ids())
            self._apply_head(new_head, push=(new_head != 0), reason="auto")
        self._js(f"clearDrone({drone_id})")
        self._refresh_online_count()
        self._refresh_fleet_count()
        self._update_fleet_scroll_height()
        self._refresh_leader_combo()
        self._refresh_takeoff_panel()
        self._update_head_ui()
        self._refresh_group_ui()
        if hasattr(self, "mlog"):
            self.mlog.set_vehicles(self.fleet_items.keys())
        if self._selected_id == drone_id:
            self._selected_id = 0
            nxt = next(iter(sorted(self.fleet_items.keys())), 0)
            if nxt:
                self._select_drone(nxt)
            else:
                self._clear_selected_card()
        self._log("removed from fleet", vehicle=f"Drone {drone_id}", category="STATUS")

    def _clear_selected_card(self):
        if not hasattr(self, "sel_card"):
            return
        self.sel_card.drone_id = 0
        self.sel_card.set_display_name("No vehicle")
        self.sel_card.badge.setText("OFFLINE")
        self.sel_card.badge.setStyleSheet(badge_style("OFFLINE"))
        self.sel_card.ed_host.clear()
        self.sel_card.ed_port.clear()
        if hasattr(self.sel_card, "lbl_ping"):
            self.sel_card.lbl_ping.setText("PING --")
        if hasattr(self.sel_card, "lbl_link"):
            self.sel_card.lbl_link.setText("LINK --")
        self.sel_card.set_quick_enabled(False)
        if hasattr(self, "coord"):
            self.coord.setText("LAT --  ·  LNG --  ·  ALT --  ·  GND SPD --")
            self.coord.setToolTip("พิกัดและสถานะของโดรนลำที่เลือก")
    def _rc_target(self):
        if getattr(self, "_leader_id", 0):
            return [self._leader_id]
        ids = self._target_ids()
        return ids[:1] if ids else [1]

    def _safe(self, fn):
        """เรียก fn() แบบกลืน exception — **คืนค่าที่ fn คืนมาด้วย**

        BUGFIX: เดิมไม่มี `return` เลยคืน None เสมอ ผู้เรียกที่เช็คผลลัพธ์ (เช่น
        คำสั่ง servo) จึงเห็นเป็น "ล้มเหลว" ทุกครั้ง ทั้งที่ core รับคำสั่งสำเร็จแล้ว
        (ยืนยันจาก audit log: ACCEPTED ในวินาทีเดียวกับที่ UI ขึ้น FAILED)
        """
        try:
            return fn()
        except Exception as e:
            self.cmd_result.emit(f"RC: ERROR {e}")
            return None

    def _leader_mode(self, mode_name):
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("LEADER MODE"):
            return
        ids = self._rc_target()
        mode = getattr(rpc.common_pb2, "FLIGHT_MODE_" + mode_name)
        label = "UI/GUIDED" if mode_name == "GUIDED" else "RC/LOITER"
        self._log(f"LEADER Drone {ids[0]} → {label}", category="COMMAND")
        self._show_toast(f"{label} · ส่งคำสั่ง…", "info")

        def worker():
            try:
                r = self._dispatch_core(
                    f"MODE {mode_name} [leader]", lambda: self.client.set_mode(ids, mode),
                    source="leader-control", targets=ids,
                    enforce_dedup=False)
                self.cmd_result.emit(f"{label}: ok={getattr(r, 'ok', True)}")
            except Exception as e:
                self.cmd_result.emit(f"{label}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    # ── collision-avoidance movement ordering (spec 7) ──
    def _dir_name(self, direction):
        """แปลง RC_DIR_* (proto int) → ชื่อทิศระนาบให้ swarm_logic; ไม่ใช่แนวราบ → None"""
        cp = rpc.command_pb2
        return {cp.RC_DIR_FWD: "FWD", cp.RC_DIR_BWD: "BWD",
                cp.RC_DIR_LEFT: "LEFT", cp.RC_DIR_RIGHT: "RIGHT"}.get(direction)

    def _fleet_positions(self):
        """{drone_id: (x=east/lon, y=north/lat)} จาก telemetry ล่าสุด"""
        pos = {}
        for did, t in self._last_telem.items():
            if did in self._removed_ids or did not in self.fleet_items:
                continue
            p = t.position
            if p.lat == 0.0 and p.lon == 0.0:
                continue
            pos[did] = (p.lon, p.lat)
        return pos

    # ══════════════════════════════════════════════════════════
    #  COLLISION DETECTION — เตือนเมื่อโดรนใกล้กันเกินไป (real-time)
    # ══════════════════════════════════════════════════════════
    def _collision_positions(self):
        """{drone_id: (lat, lon, alt_rel)} เฉพาะลำที่มี GPS แล้ว"""
        pos = {}
        for did, t in self._last_telem.items():
            if did in self._removed_ids or did not in self.fleet_items:
                continue
            p = t.position
            if p.lat == 0.0 and p.lon == 0.0:
                continue
            pos[did] = (p.lat, p.lon, getattr(p, "alt_rel", 0.0))
        return pos

    def _collision_watchdog(self):
        """ตรวจทุกคู่โดรนแบบ real-time — ใกล้เกินไป/ซ้อนทับ = เตือนบน UI ทันที"""
        self._refresh_flight_mode()      # อัปเดตป้ายโหมดการบินไปด้วย (spec 5)
        pairs = swarm_logic.detect_collisions(
            self._collision_positions(),
            critical_m=self._collision_crit,
            warn_m=self._collision_warn)
        now = time.monotonic()
        current = {p.key() for p in pairs}

        for p in pairs:
            k = p.key()
            prev = self._collision_seen.get(k)
            # เตือนซ้ำได้ทุก 8 วิ ถ้ายังเสี่ยงอยู่ / เตือนทันทีเมื่อยกระดับเป็น critical
            if prev and now - prev[0] < 8.0 and prev[1] == p.level:
                continue
            self._collision_seen[k] = (now, p.level)
            if p.level == "critical":
                self._log(f"⚠ COLLISION {p.describe()}", vehicle=f"Drone {p.a}",
                          category="ALERT", severity="ERROR")
                self._show_banner(f"⚠ COLLISION — {p.describe()}", T("red"))
                self._show_toast(f"ชนกัน! {p.describe()}", "err")
            else:
                self._log(f"PROXIMITY {p.describe()}", vehicle=f"Drone {p.a}",
                          category="ALERT", severity="WARNING")
                self._show_banner(f"PROXIMITY — {p.describe()}", T("amber"))

        # คู่ที่ห่างออกไปแล้ว → เคลียร์ ให้เตือนใหม่ได้ถ้ากลับมาใกล้อีก
        for k in list(self._collision_seen):
            if k not in current:
                del self._collision_seen[k]

        self._update_collision_badge(pairs)

    def _set_flight_mode_badge(self, mode):
        """spec 5 + ข้อ 7 — โหมดการบินโชว์เป็นป้ายกลาง topbar ที่เลื่อนลงมาแล้วค้างไว้

        (ป้าย pill เดิมที่ซ้ายบนถูกถอดออกแล้ว — เหลือแถบสี _mode_strip ไว้เหมือนเดิม)
        """
        if not hasattr(self, "mode_drop"):
            return
        if self._flight_mode == mode:
            return
        self._flight_mode = mode
        color = {
            "swarm": T("amber"), "rtl": T("orange"), "waypoint": T("green"),
        }.get(mode, T("accent"))
        self._play_mode_drop(mode)
        # แถบสถานะโหมดบน topbar — สีเดียวกับป้าย แต่จางกว่า (alpha 0.35 vs ป้ายที่ทึบ)
        # กันไม่ให้แย่งความสำคัญกับ banner แจ้งเตือน
        if hasattr(self, "_mode_strip"):
            self._mode_strip.setStyleSheet(
                f"background:transparent; border:none;"
                f" border-bottom:3px solid {rgba(color, 0.35)};")
        self._log(f"โหมดการบิน → {self.mode_drop.text().split()[-1]}", category="STATUS")

    def _refresh_flight_mode(self):
        if getattr(self, "_rtl_active", False):
            self._set_flight_mode_badge("rtl")
        elif getattr(self, "_waypoint_executing", False):
            self._set_flight_mode_badge("waypoint")
        elif getattr(self, "_swarm_active", False):
            self._set_flight_mode_badge("swarm")
        else:
            self._set_flight_mode_badge("flight")

    def _update_collision_badge(self, pairs):
        """ป้ายสถานะการชนบน statusbar — เห็นสถานะรวมได้ตลอดเวลา"""
        if not hasattr(self, "lbl_collide"):
            return
        crit = [p for p in pairs if p.level == "critical"]
        if crit:
            self.lbl_collide.setText(f"⚠ COLLISION {len(crit)}")
            self.lbl_collide.setStyleSheet(
                f"color:#ffffff; background:{T('red')}; border-radius:6px;"
                f" padding:1px 8px; font-size:10px; font-weight:800;")
            self.lbl_collide.setToolTip("\n".join(p.describe() for p in crit))
        elif pairs:
            self.lbl_collide.setText(f"⚠ ใกล้กัน {len(pairs)}")
            self.lbl_collide.setStyleSheet(
                f"color:#101418; background:{T('amber')}; border-radius:6px;"
                f" padding:1px 8px; font-size:10px; font-weight:800;")
            self.lbl_collide.setToolTip("\n".join(p.describe() for p in pairs))
        else:
            self.lbl_collide.setText("SEPARATION OK")
            self.lbl_collide.setStyleSheet(
                f"color:{T('green')}; font-size:10px; font-weight:700;")
            self.lbl_collide.setToolTip("ทุกลำรักษาระยะห่างปลอดภัย")

    def _multi_move(self):
        """สั่งเคลื่อนที่พร้อมกันหลายลำอยู่ไหม → ต้องจัดคิวกันชน (spec 6)"""
        return len(self._selected_or_all()) > 1

    def _ordered_fleet(self, direction):
        """คิวลำดับการเคลื่อนที่กันชน (spec 6) — ลำที่อยู่ไกลสุดในทิศเดินทางไปก่อน
        คิดเฉพาะลำที่เลือกไว้; ลำที่ไม่มีพิกัด (ยังไม่มี GPS) ต่อท้ายตาม id"""
        dname = self._dir_name(direction)
        want = self._selected_or_all() or sorted(self.fleet_items.keys())
        if dname is None:
            return want
        pos = {d: p for d, p in self._fleet_positions().items() if d in want}
        if not pos:
            return want
        order = swarm_logic.movement_order(pos, dname)
        tail = [i for i in want if i not in order]
        return order + tail

    def _rc_press(self, direction):
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("MOVE"):
            return
        self._rc_dir = direction
        # เลือกหลายลำ + ทิศแนวราบ → คำนวณคิวกันชน แล้ว log ให้เห็นครั้งเดียวต่อการกด
        if self._multi_move():
            dname = self._dir_name(direction)
            if dname and self._rc_order_dir != direction:
                order = self._ordered_fleet(direction)
                self._rc_order = order
                arrow = "→".join(str(i) for i in order)
                self._log(f"MOVE ORDER [{dname}] {arrow} (กันชน)", category="COMMAND")
                self._summ_set("move", "MOVE ORDER", f"{dname}: {arrow}")
                self._rc_order_dir = direction
        else:
            self._rc_order_dir = None
        if not self._rc_timer.isActive():
            self._rc_timer.start()
        self._rc_tick()

    def _rc_release(self):
        self._rc_dir = None
        self._rc_order_dir = None
        self._rc_halt(notify=False)

    def _rc_stop(self):
        """ปุ่ม ● STOP — แจ้ง toast"""
        self._rc_halt(notify=True)

    def _rc_halt(self, notify=False):
        self._rc_dir = None
        ids = self._rc_target()
        if notify:
            self._show_toast("STOP · ส่งคำสั่ง…", "info")

        def worker():
            try:
                r = self._dispatch_core(
                    "STOP ALL [rc]", lambda: self.client.stop_all(ids),
                    source="rc", targets=ids,
                    enforce_dedup=False)
                if notify:
                    self.cmd_result.emit(f"STOP: ok={getattr(r, 'ok', True)}")
            except Exception as e:
                self.cmd_result.emit(f"STOP: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _rc_tick(self):
        if self._rc_dir is None:
            return
        if bool(getattr(self, "_mission_core_authority", False)):
            # A press may have started before Core authority was acquired. Stop
            # the local repeat timer/state without emitting a competing stop/move.
            self._rc_dir = None
            self._rc_order_dir = None
            if self._rc_timer.isActive():
                self._rc_timer.stop()
            return
        spd = self.sf_speed.value()
        d = self._rc_dir
        if self._multi_move() and self._dir_name(d):
            # ยิงทีละลำตามคิวกันชน (ไกลสุดในทิศเดินทางก่อน) — spec 7
            order = self._rc_order or self._ordered_fleet(d)
            for i, did in enumerate(order):
                delay = i * 120  # หน่วงเล็กน้อยให้ตัวหน้าเคลียร์ทางก่อน
                QTimer.singleShot(delay, lambda x=did: threading.Thread(
                    target=lambda: self._safe(lambda: self._dispatch_core(
                        "RC MOVE [repeat]", lambda: self.client.rc_move([x], d, spd),
                        source="rc-repeat", targets=[x], enforce_dedup=False)),
                    daemon=True).start())
        else:
            ids = self._rc_target()
            threading.Thread(target=lambda: self._safe(lambda: self._dispatch_core(
                "RC MOVE [repeat]", lambda: self.client.rc_move(ids, d, spd),
                source="rc-repeat", targets=ids, enforce_dedup=False)),
                             daemon=True).start()

    # ══════════════════════════════════════════════════════════
    #  SWARM / EVENTS
    # ══════════════════════════════════════════════════════════
    def _poll_swarm(self):
        def worker():
            try:
                st = self.client.swarm_state()
                self.swarm_update.emit(st)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _on_swarm_update(self, st):
        import json
        was_swarm = getattr(self, "_swarm_active", False)
        self._swarm_active = bool(st.active)
        # เข้า/ออกโหมด Swarm → รีเฟรชการล็อก SEPARATE/WAVE ทั้ง UI + logic (spec §10)
        # (force GROUPED, ปิด WAVE, disable/enable controls, อัปเดต hint)
        if self._swarm_active != was_swarm:
            if self._swarm_active and not was_swarm and self._wp_separate:
                self._log("เข้าโหมด Swarm — สลับ Waypoint เป็น GROUPED (Leader Path)",
                          category="STATUS")
            self._wp_refresh_mode_availability()
        leader = st.leader_id if st.active else 0
        self._leader_id = leader or self._head_id
        # BUGFIX: เดิม poll ทุก 1.2s แล้ว "ทับ" head ที่ผู้ใช้เพิ่งเลือกกลับเป็นของ core
        # → กดเปลี่ยนเป็นลำ 2 แล้วเด้งกลับลำ 1 ตลอด
        # ตอนนี้: ถ้าผู้ใช้ปักหมุดไว้และลำนั้นยัง online → ยืนยันค่ากลับไปที่ core แทน
        if st.active and leader and leader != self._head_id:
            pinned = getattr(self, "_head_pinned", 0)
            if pinned and pinned in self._connected_ids():
                if time.monotonic() - getattr(self, "_head_push_ts", 0) > 3.0:
                    self._head_push_ts = time.monotonic()
                    self._log(f"core leader={leader} ไม่ตรงกับ Head ที่เลือก ({pinned}) "
                              f"— ส่ง SetLeader ซ้ำ", category="STATUS")
                    threading.Thread(target=lambda p=pinned: self._safe(lambda: self._dispatch_core(
                        "SET LEADER [resync]", lambda: self.client.set_leader(p),
                        source="swarm-resync", targets=[int(p)], enforce_dedup=False)),
                        daemon=True).start()
            else:
                # ไม่ได้ปักหมุด (หรือลำที่ปักหลุดไปแล้ว) → ตาม core (เช่น failover)
                self._apply_head(leader, push=False, reason="swarm")
        self._js(f"setLeaderId({int(leader)})")
        if st.active:
            fname = FORMATION_NAMES.get(st.formation, "?").split()[0]
            self.lbl_swarm.setText(
                f"swarm: {fname} · LEADER {leader} · {len(st.edges)} ลูก · {st.spacing:.0f}m")
            self.lbl_swarm.setStyleSheet(f"color:{T('amber')}; font-size:10px; font-weight:700;")
            if hasattr(self, "form_picker"):
                # sync เฉพาะตอนขบวนฝั่งเซิร์ฟเวอร์เปลี่ยนจริง — ไม่ทับการคลิกเลือก pattern
                fid = int(st.formation)
                if getattr(self, "_swarm_form_synced", None) != fid:
                    self._swarm_form_synced = fid
                    self.form_picker.set_current(fid)
            edges = [{"leader": e.leader_id, "follower": e.follower_id} for e in st.edges]
            self._js(f"setSwarmEdges({json.dumps(edges)!r})")
            if st.note and getattr(self, "_last_swarm_note", "") != st.note:
                self._last_swarm_note = st.note
                self._log(f"[swarm] {st.note}", category="STATUS")
        else:
            self._swarm_form_synced = None
            self.lbl_swarm.setText("swarm: off")
            self.lbl_swarm.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
            self._js("clearSwarmEdges()")

    def _on_event(self, ev):
        did = ev.drone_id
        tag = f"Drone {did}" if did else "System"
        WARN = rpc.telemetry_pb2.EVENT_LEVEL_WARN
        ALARM = rpc.telemetry_pb2.EVENT_LEVEL_ALARM
        sev = "ERROR" if ev.level >= ALARM else "WARNING" if ev.level >= WARN else "INFO"
        self._log(ev.message, vehicle=tag, category="ALERT" if ev.level >= WARN else "STATUS",
                  severity=sev)
        if ev.level >= WARN:
            color = T("red") if ev.level >= ALARM else T("amber")
            self._show_banner(f"[{ev.category.upper()}] {tag} · {ev.message}", color)
        # ── Battery/Link failsafe จาก Core มี priority สูงกว่า WAIT/route (spec §8) ──
        # Core เริ่ม failsafe RTL เองแล้ว — cockpit แค่ยกเลิก route progression ฝั่งตัวเอง
        if ev.level >= ALARM and str(ev.category).lower() in ("battery", "link"):
            self._wp_failsafe_interrupt(int(ev.drone_id or 0),
                                        str(ev.category).lower(), ev.message)

    def _wp_participant(self, drone_id):
        """โดรนนี้อยู่ในภารกิจ Waypoint/WAVE ที่กำลังทำงานอยู่หรือไม่"""
        did = int(drone_id or 0)
        if not did:
            return False
        if did in [int(d) for d in self._wp_target_ids]:
            return True
        if self._wp_separate:
            route = self._wp_routes.get(did)
            if route is not None and not route.is_empty():
                return True
        if (self._wave_executing and 0 <= self._wave_group_index < len(self._wave_groups)
                and self.group_of.get(did) == self._wave_groups[self._wave_group_index]):
            return True
        return False

    def _wp_failsafe_interrupt(self, drone_id, category, message=""):
        """Core failsafe (battery/link) แทรก WAIT/route — fail-closed (spec §8)

        - invalidate WAIT + pending takeoff/wave callback (no stale callback)
        - หยุด route progression: ไม่ advance waypoint ต่อ
        - **ไม่ยิง command ใหม่** (HOLD/GOTO/RTL) ที่อาจทับ failsafe RTL ของ Core
        - mission ไม่ auto-resume — ผู้ใช้ต้องเริ่มใหม่เอง
        - timeline → FAILED (แสดงสถานะ failsafe)
        """
        if not (self._waypoint_executing or self._wave_executing
                or self._wp_takeoff_pending):
            return
        if not self._wp_participant(drone_id):
            return
        mission_shadow.cancel(self)  # stop shadow progression; Core still owns failsafe action
        # invalidate async callback ที่ค้างทั้งหมด (WAIT / takeoff-gate / wave next-group)
        self._wp_wait_invalidate()
        self._wp_takeoff_generation += 1
        self._wave_generation += 1
        self._wp_takeoff_pending = False
        self._wave_executing = False
        self._waypoint_executing = False
        if hasattr(self, "_wave_timer"):
            self._wave_timer.stop()
        self._wp_arrived = set()
        self._wp_sep_index = {}
        reason = f"{category} failsafe D{int(drone_id)} → RTL"
        # timeline: mark ขั้นปัจจุบัน FAILED แล้ว skip downstream (ไม่เดินต่อ)
        if self._flight_run and self._flight_run.kind in ("waypoint", "wave"):
            step_id = self._flight_run.active_step_id
            if step_id:
                self._flight_run.fail(step_id, reason)
            else:
                self._flight_run.cancel(reason)
            self._render_summary()
        self._log(f"FAILSAFE — {reason} · ยกเลิก route progression ฝั่ง cockpit "
                  "(Core เป็นเจ้าของ failsafe · ไม่ส่งคำสั่งทับ)",
                  category="ALERT", severity="ERROR")
        self._summ_event("FAILSAFE", reason, cancelled=True)
        self._summ_sync_waypoint()
        self._refresh_flight_mode()

    # ══════════════════════════════════════════════════════════
    #  GEOFENCE / CACHE / MAP CLICK
    # ══════════════════════════════════════════════════════════
    def _fence_toggle(self):
        nxt = "none" if getattr(self, "_draw_tool", "none") == "poly" else "poly"
        self._set_draw_tool(nxt)

    def _fence_set(self):
        if not self._map_enabled or self.web is None:
            self._log("GEOFENCE: ต้องใช้แผนที่ (ปิดอยู่บนเครื่องนี้)", severity="WARNING")
            return
        self.web.page().runJavaScript("getFencePoints()", self._fence_apply)

    def _fence_apply(self, pts_json):
        import json
        try:
            pts = json.loads(pts_json or "[]")
        except Exception:
            pts = []
        if len(pts) < 3:
            self._log("GEOFENCE: ต้อง ≥ 3 จุด (วาด POLY/RECT/CIRCLE ให้ครบ)", severity="WARNING")
            self._show_toast("Fence ต้อง ≥ 3 จุด", "err")
            return
        points = [(p["lat"], p["lon"]) for p in pts]
        if not self._confirm(
                self, "ยืนยัน Geofence",
                f"กำลังบังคับใช้เขตบินใหม่ {len(points)} จุดกับทุกคำสั่งบิน\n\n"
                "ตรวจตำแหน่งบนแผนที่ให้ถูกต้องก่อนดำเนินการ",
                ok_text="บังคับใช้ Geofence", accent=T("amber"), danger=True):
            self._show_toast("ยกเลิกการตั้ง Geofence", "info")
            return
        self._set_draw_tool("none")
        threading.Thread(target=lambda: self._fence_send(points, pts_json), daemon=True).start()

    def _fence_clear(self):
        if not self._confirm(
                self, "ยืนยันปิด Geofence",
                "การปิด Geofence จะยกเลิกขอบเขต polygon ที่บังคับใช้อยู่\n"
                "เพดานความสูงและรัศมีจาก GCS ยังทำงานตามเดิม",
                ok_text="ปิด Geofence", danger=True):
            self._show_toast("ยกเลิกการปิด Geofence", "info")
            return
        self._set_draw_tool("none")
        threading.Thread(target=lambda: self._fence_send([], "[]"), daemon=True).start()

    def _fence_send(self, points, pts_json):
        """อัปเดตภาพรั้วหลัง core ยืนยันเท่านั้น กัน UI แสดง fence ที่ไม่ได้ enforce."""
        try:
            result = self._dispatch_core(
                "SET GEOFENCE", lambda: self.client.set_geofence(points),
                source="geofence", targets=(),
                enforce_dedup=False)
            self.cmd_result.emit(f"GEOFENCE: {result.message}")
            if not bool(getattr(result, "ok", False)):
                self.ui_call.emit(lambda: self._show_toast(
                    "Core ปฏิเสธ Geofence · รั้วเดิมยังทำงานอยู่", "err"))
                return

            saved_points = list(points)

            def commit():
                self._fence_points = saved_points
                self._js(f"drawFence({pts_json!r})")
                self._push_map3d_view()
                if saved_points:
                    self._summ_set("fence", "GEOFENCE", "SET · %d points" % len(saved_points))
                else:
                    self._summ_remove("fence")
                self._show_toast(
                    "ปิด Geofence แล้ว" if not saved_points else "บังคับใช้ Geofence แล้ว",
                    "ok")

            self.ui_call.emit(commit)
        except Exception as exc:
            self.cmd_result.emit(f"GEOFENCE: ERROR {exc}")
            self.ui_call.emit(lambda: self._show_toast(
                "ตั้ง Geofence ไม่สำเร็จ · รั้วเดิมยังทำงานอยู่", "err"))

    def _cache_area(self):
        if not self._map_enabled or self.web is None:
            self._log("CACHE: ต้องใช้แผนที่ (ปิดอยู่บนเครื่องนี้)", severity="WARNING")
            return
        self.web.page().runJavaScript("getBoundsJSON()", self._prefetch_bounds)

    def _prefetch_bounds(self, bounds_json):
        import json
        try:
            b = json.loads(bounds_json)
        except Exception:
            self._log("CACHE: no map bounds", severity="WARNING")
            return
        z = int(b["zoom"])
        zmax = min(z + 2, 19)
        self._log(f"CACHE AREA zoom {z}-{zmax} (กำลังดาวน์โหลด...)", category="COMMAND")

        def worker():
            n = self.tiles.prefetch(
                "sat", b["north"], b["south"], b["east"], b["west"], z, zmax)
            # แผนที่ 3D ใช้ DEM ที่ z13 (tile กว้าง ~5 km) — พื้นที่จอซูมใกล้ตกในไม่กี่แผ่น
            n_dem = self.tiles.prefetch(
                "dem", b["north"], b["south"], b["east"], b["west"], 13, 13)
            self.cmd_result.emit(
                f"CACHE AREA: {n} sat + {n_dem} dem tiles saved → offline ready")
        threading.Thread(target=worker, daemon=True).start()

    def _goto_drone_gps(self):
        """เลื่อนแผนที่ไป GPS ของลำที่เลือก — ไม่สั่งบิน"""
        did = self._selected_id
        t = self._last_telem.get(did) if did else None
        if t is None:
            for tid, tt in sorted(self._last_telem.items()):
                if tt.position.lat or tt.position.lon:
                    did, t = tid, tt
                    break
        if t is None or (not t.position.lat and not t.position.lon):
            self._show_toast("ยังไม่มี GPS · ต่อโดรนหรือใช้ปุ่มใส่พิกัด", "err")
            return
        if self._jump_map_to(t.position.lat, t.position.lon, set_gcs=True):
            who = self._drone_names.get(int(did)) or f"Drone {did}"
            self._show_toast(f"แผนที่ → GPS {who}", "ok")
            self._log(f"แผนที่กระโดดไป GPS {who} "
                      f"{t.position.lat:.6f},{t.position.lon:.6f}",
                      category="STATUS")

    def _goto_typed_gps(self):
        """เลื่อนแผนที่ไปพิกัดที่พิมพ์ — ใช้ตอนยังไม่ต่อโดรน / จะ CACHE พื้นที่อื่น"""
        txt, ok = QInputDialog.getText(
            self, "ไปที่พิกัด",
            "ละติจูด, ลองจิจูด\nเช่น 13.7563, 100.5018 (กรุงเทพ)")
        if not ok:
            return
        parsed = parse_latlon(txt)
        if not parsed:
            self._show_toast("พิกัดไม่ถูกต้อง · ใช้รูปแบบ lat, lon", "err")
            return
        lat, lon = parsed
        self._jump_map_to(lat, lon, set_gcs=False)
        self._show_toast(f"แผนที่ → {lat:.5f}, {lon:.5f}", "ok")
        self._log(f"แผนที่กระโดดไปพิกัด {lat:.6f},{lon:.6f}", category="STATUS")

    def _set_goto_armed(self, on, reason=""):
        """ปลดล็อก/ล็อก "คลิกแผนที่ = สั่งบิน" + sync ปุ่มบนแผนที่ให้ตรงกัน

        on=True มาจากผู้ใช้กดปุ่มรูปโดรนบนแผนที่เท่านั้น
        on=False เกิดได้ทั้งจากกดซ้ำ, สั่งบินไปแล้ว (one-shot), หรือสลับไปโหมดอื่น
        """
        on = bool(on)
        was = getattr(self, "_goto_armed", False)
        self._goto_armed = on
        js = f"setGotoArmed({'true' if on else 'false'})"
        self._js(js)
        self._js3d(js)          # แผนที่ 3D ถือด่านเดียวกัน ต้องโชว์ตรงกัน
        if on == was:
            return
        if on:
            self._show_toast("โหมดสั่งบิน: เปิด · คลิกแผนที่เพื่อสั่ง (กดปุ่มซ้ำเพื่อปิด)", "info")
            self._log("เปิดโหมดสั่งบินจากแผนที่", category="COMMAND")
        else:
            self._log(f"ปิดโหมดสั่งบินจากแผนที่{reason}", category="COMMAND")

    def _on_goto_arm(self, on):
        """ผู้ใช้กดปุ่มบนแผนที่ — JS แจ้ง state ที่ตัวเองสลับไปแล้วกลับมา"""
        self._set_goto_armed(on)

    def _on_map_click(self, lat, lon):
        """คลิกแผนที่ = สั่ง "ทุกลำที่เลือก" บินไปพร้อมกัน (คงรูปขบวน/ระยะห่างเดิม)

        BUGFIX: เดิมใช้ self._target_ids()[0] → บินแค่ลำเดียว (id ต่ำสุด)
        """
        if self._waypoint_mode:
            return  # โหมด Waypoint เปิดอยู่ — JS ส่ง waypoint_click มาแทนแล้ว ไม่ใช่บินทันที
        # ── ด่านปลดล็อก: คลิกแผนที่เฉย ๆ ห้ามสั่งโดรนบิน ──
        # ต้องกดปุ่มรูปโดรนบนแผนที่ก่อน (ดู _set_goto_armed) กันเคสเผลอคลิกแผนที่
        # ตอนเลื่อน/ซูม/ดูพิกัด แล้วโดรนออกบินทันทีโดยไม่ตั้งใจ
        # ตัดสินที่ Python เสมอ ไม่เชื่อ state ฝั่ง JS (UI คือกระจก ไม่ใช่สมอง)
        if not getattr(self, "_goto_armed", False):
            self._show_toast("ล็อกอยู่ · กดปุ่มรูปโดรนบนแผนที่ก่อนถึงสั่งบินได้", "err")
            self._log(f"คลิกแผนที่ {lat:.6f},{lon:.6f} ถูกบล็อก — ยังไม่ได้ปลดล็อกสั่งบิน",
                      category="COMMAND", severity="WARNING")
            return
        if not self._guard():
            return
        if self._manual_nav_blocked_by_core("GOTO(click)"):
            return
        # ใช้ชุดที่เลือกจริงเท่านั้น (ไม่ fallback ไปลำแรก) — คลิกแผนที่คือคำสั่งให้บิน
        # ถ้าไม่ได้เลือกไว้แล้วเผลอคลิก ไม่ควรมีโดรนลำไหนออกบินเอง
        ids = self._selected_or_all()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกโดรน · คลิกการ์ดซ้าย หรือกด FLEET", "err")
            self._log("GOTO(click) ยกเลิก — ยังไม่ได้เลือกโดรน",
                      category="COMMAND", severity="WARNING")
            return

        # ลำที่มี GPS แล้ว → ย้ายยกขบวน; ลำที่ยังไม่มีพิกัด → ส่งไปจุดเป้าตรง ๆ
        pos = {d: p for d, p in self._fleet_positions().items() if d in ids}
        latlon = {d: (p[1], p[0]) for d, p in pos.items()}   # (lon,lat) → (lat,lon)
        targets = swarm_logic.group_goto_targets(latlon, lat, lon,
                                                 keep_formation=len(ids) > 1)
        for d in ids:
            targets.setdefault(d, (lat, lon))

        # ส่งตามคิวกันชน: ลำที่อยู่ไกลสุดในทิศเดินทางไปก่อน
        order = self._goto_order(ids, latlon, lat, lon)
        self._log(f"GOTO(click) {len(order)} ลำ → {lat:.6f},{lon:.6f} "
                  f"[{'→'.join(str(i) for i in order)}]", category="COMMAND")
        self._summ_set("goto", "GOTO", f"{len(order)} ลำ → {lat:.5f},{lon:.5f}")
        self._show_toast(f"GOTO · {len(order)} ลำ", "info")

        for i, did in enumerate(order):
            tlat, tlon = targets[did]
            alt = self._last_alt.get(did) or self._alt_for(did)
            QTimer.singleShot(i * 150, lambda d=did, la=tlat, lo=tlon, a=alt:
                              self._goto_one(d, la, lo, a))
            # จุดเป้ากะพริบ + เส้นประ สีเดียวกับโดรน (หดสั้นลงเองตามระยะจริง)
            self._js(f"setTarget({did},{tlat:.7f},{tlon:.7f},'{drone_color(did)}')")
        self._nav_targets = {int(d): targets[d] for d in order}
        # โหมดสั่งบินเป็นโหมดค้าง — สั่งต่อได้เรื่อย ๆ จนกว่าจะกดปิดเอง
        # (ไม่ปิดให้อัตโนมัติ ผู้ใช้ที่กำลังไล่สั่งหลายจุดจะได้ไม่ต้องกดเปิดใหม่ทุกครั้ง)
        self._push_map3d_targets()

    def _on_target_reached(self, drone_id):
        """แผนที่แจ้งว่าโดรนถึงเป้าแล้ว (ล้างเป้า+เส้นประให้เองแล้ว)"""
        did = int(drone_id or 0)
        self._nav_targets.pop(did, None)
        self._push_map3d_targets()      # เอาจุดหมาย+เส้นประออกจากแผนที่ 3D ด้วย
        if did:
            self._log(f"ถึงเป้าหมายแล้ว", vehicle=f"Drone {did}",
                      category="STATUS", severity="SUCCESS")
        if not self._nav_targets:
            self._summ_remove("goto")

        # เมื่อ Go Core ถือ authority, browser target_reached เป็น presentation
        # signal เท่านั้น ห้ามขยับ Python index/WAIT/action หรือยิง GOTO ต่อ.
        if bool(getattr(self, "_mission_core_authority", False)):
            return

        # กำลัง Execute เส้นทาง Waypoint อยู่
        if self._waypoint_executing and did:
            if self._wp_separate:
                # SEPARATE — ลำนี้เดินเส้นทางของตัวเองต่อได้เลย ไม่ต้องรอลำอื่น
                if did not in self._wp_target_ids:
                    return
                idx = self._wp_sep_index.get(did, 0)
                route = self._wp_routes.get(did)
                if route is None or idx >= len(route):
                    return
                wp = route.points[idx]
                self._js(f"markWaypointDone({did}, {wp.index})")
                self._wp_sep_index[did] = idx + 1
                go_next = lambda d=did: self._wp_advance_one(d)
                # ARRIVE → (WAIT) → action A/B → advance (แต่ละลำอิสระ)
                self._wp_on_arrived(did, wp, [did], go_next)
            else:
                # GROUPED — ต้องรอครบทุกลำก่อนไปจุดถัดไป (กันตัดขบวน)
                arrived, complete, accepted = waypoint_logic.grouped_arrival_update(
                    self._wp_arrived, self._wp_target_ids, did)
                if not accepted:
                    return
                self._wp_arrived = arrived
                if complete:
                    route = self._waypoint_route
                    if route is None or self._wp_current_index >= len(route):
                        return
                    wp = route.points[self._wp_current_index]
                    self._js(f"markWaypointDone({self._wp_key}, {wp.index})")
                    self._wp_current_index += 1
                    self._wp_arrived.clear()
                    # ทุกลำถึงครบ → ARRIVE → (WAIT ร่วมกัน) → action A/B → advance
                    self._wp_on_arrived(0, wp, list(self._wp_target_ids),
                                        self._wp_advance)

    def _cancel_navigation(self):
        """ยกเลิกคำสั่งเคลื่อนที่: ล้างเป้า/เส้นประบนแผนที่ + ให้โดรนหยุดลอยอยู่กับที่"""
        self._abort_waypoint_execution()
        ids = self._selected_or_all() or sorted(self.fleet_items.keys())
        target_text = ", ".join(f"D{did}" for did in ids) if ids else "ไม่มีโดรน"
        self._summ_event(
            "ยกเลิก", f"CANCEL NAV · ล้างเป้าหมาย/Waypoint · HOLD {target_text}",
            cancelled=True)
        self._js("clearAllTargets()")
        self._nav_targets = {}
        self._push_map3d_targets()      # ล้างจุดหมาย+เส้นประบนแผนที่ 3D ด้วย
        self._summ_remove("goto")
        if not ids:
            self._show_toast("ล้างเป้าหมายบนแผนที่แล้ว", "info")
            return
        if not self._guard():
            return
        self._abort_rtl()          # ยกเลิกลำดับ RTL ที่ค้างอยู่ด้วย
        self._rc_dir = None
        self._rc_order_dir = None
        # อยู่ในโหมด Swarm → ต้องหยุด formation loop ที่ core ด้วย
        # ไม่งั้น loop (tick ทุก 400ms) จะส่ง GotoYaw ลากตัวลูกกลับเข้ารูปขบวน
        # ทับคำสั่ง Hold ที่เพิ่งสั่งไป — ตัวลูกไม่ได้ "ลอยรอรับคำสั่ง" จริง
        was_swarm = bool(getattr(self, "_swarm_active", False))
        self._log(f"CANCEL NAV → หยุดลอยอยู่กับที่ {len(ids)} ลำ "
                  f"({','.join(str(i) for i in ids)})"
                  + (" · หยุดขบวน Swarm ด้วย" if was_swarm else ""),
                  category="COMMAND")
        self._show_toast(f"ยกเลิกเป้าหมาย · HOLD {len(ids)} ลำ", "info")

        def worker():
            # หยุด formation loop ก่อน แต่แยก try ของตัวเอง:
            # ถ้าขั้นนี้พลาด ห้ามให้ hold ที่อยู่ข้างล่างถูกข้ามไปด้วยเด็ดขาด
            # (การหยุดลอยสำคัญกว่าการหยุดขบวน — พลาดแล้วโดรนไหลต่อ)
            if was_swarm:
                try:
                    self._dispatch_core(
                        "SWARM STOP [cancel-nav]", lambda: self.client.swarm_stop(),
                        source="cancel-nav", targets=ids, enforce_dedup=False)
                except Exception as e:
                    self.cmd_result.emit(f"CANCEL NAV: swarm stop ล้มเหลว ({e}) — สั่ง HOLD ต่อ")
            try:
                nav_ids = [int(i) for i in ids]
                self._dispatch_core(
                    "STOP ALL [cancel-nav]", lambda: self.client.stop_all(nav_ids),
                    source="cancel-nav", targets=nav_ids, enforce_dedup=False)
                r = self._dispatch_core(
                    "HOLD [cancel-nav]", lambda: self.client.hold(nav_ids),
                    source="cancel-nav", targets=nav_ids, enforce_dedup=False)
                self.cmd_result.emit(f"CANCEL NAV: ok={getattr(r, 'ok', True)}")
            except Exception as e:
                self.cmd_result.emit(f"CANCEL NAV: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _goto_one(self, did, lat, lon, alt):
        # Defense against a stale QTimer/manual click queued before Core took
        # authority. Legacy waypoint execution only reaches here while the Core
        # flag is false, so failing closed here cannot suppress a valid legacy run.
        if bool(getattr(self, "_mission_core_authority", False)):
            self._log(f"GOTO D{int(did)} ถูกทิ้ง — Core mission ถือ flight authority",
                      severity="WARNING", category="ALERT")
            return
        def worker():
            try:
                target_id = int(did)
                r = self._dispatch_core(
                    "GOTO [legacy]", lambda: self.client.goto(
                        target_id, float(lat), float(lon), float(alt)),
                    source="legacy-goto", targets=[target_id],
                    enforce_dedup=False)
                self.cmd_result.emit(
                    f"GOTO D{did}: ok={getattr(r, 'ok', True)} "
                    f"{getattr(r, 'message', '')}".strip())
            except Exception as e:
                self.cmd_result.emit(f"GOTO D{did}: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _goto_order(self, ids, latlon, tlat, tlon):
        """เรียงลำดับส่ง GOTO ด้วย production helper ที่ทดสอบแบบ headless ได้"""
        return waypoint_logic.grouped_goto_order(ids, latlon, tlat, tlon)

    # ══════════════════════════════════════════════════════════
    #  WAYPOINT ROUTE PLANNING
    #  วางจุดหลายจุดล่วงหน้าแล้วสั่งบินทีละจุดจนจบเส้นทาง — โดรนเดี่ยวหรือ
    #  ทั้งขบวน (คงรูปขบวน/ระยะห่างไว้ตลอดเส้นทาง)
    # ══════════════════════════════════════════════════════════
    def _wp_toggle(self, on):
        """เปิด/ปิด Waypoint Mode — เปิดแล้วคลิกแผนที่ = วางจุด ไม่ใช่บินทันที"""
        on = bool(on)
        if self._waypoint_mode == on:
            return
        self._waypoint_mode = on
        # สลับโหมดเมื่อไร = ล็อกการสั่งบินจากแผนที่เสมอ
        # JS ล็อกฝั่งตัวเองอยู่แล้ว (_gotoArmSync) ถ้า Python ไม่ล็อกตาม สองฝั่งจะหลุดกัน
        # → ปุ่มโชว์ "ล็อก" แต่ Python ยังปลดล็อกอยู่ = คลิกแล้วบินทั้งที่ไม่ได้ตั้งใจ
        if getattr(self, "_goto_armed", False):
            self._set_goto_armed(False, reason=" (สลับโหมด Waypoint)")
        self._js(f"setWaypointMode({'true' if on else 'false'})")
        self._js3d(f"map3d.setWaypointMode({'true' if on else 'false'})")
        if hasattr(self, "sw_waypoint"):
            self.sw_waypoint.setCurrent(1 if on else 0)
        if on:
            # คนละระบบกับ geofence/tactical — ปิดของอื่นถ้าเปิดอยู่ (กันชนกัน)
            if getattr(self, "_draw_tool", "none") != "none":
                self._set_draw_tool("none")
            if getattr(self, "_tac_tool", "none") != "none":
                self._set_tac_tool("none")
            self._show_toast("เปิดโหมด Waypoint — คลิกแผนที่เพื่อวางจุด", "info")
            self._log("เปิดโหมด Waypoint Route Planning", category="COMMAND")
        else:
            self._show_toast("ปิดโหมด Waypoint", "info")
            self._log("ปิดโหมด Waypoint Route Planning", category="COMMAND")
        self._summ_sync_waypoint()

    # ── โหมดเส้นทาง: GROUPED (ร่วม) / SEPARATE (แยกลำ) ──
    def _wp_set_separate(self, on):
        """สลับโหมดเส้นทาง — SEPARATE ใช้ไม่ได้ตอนอยู่ในโหมด Swarm"""
        on = bool(on)
        if on and getattr(self, "_wave_enabled", False):
            self._wave_toggle(False)
            self._show_toast("สลับเป็น SEPARATE · ปิด WAVE อัตโนมัติ", "info")
        if on and self._swarm_active:
            self._show_toast(
                "โหมด Swarm เปิดอยู่ — กำหนดได้แค่ลำแม่ · ออกจาก Swarm ก่อน", "err")
            self._log("Waypoint: ขอ SEPARATE ถูกปฏิเสธ — อยู่ในโหมด Swarm",
                      category="COMMAND", severity="WARNING")
            # บังคับกลับ GROUPED เสมอ — ระหว่าง Swarm ห้ามค้างสถานะ SEPARATE ไว้
            on = False
        if self._wp_separate == on:
            # สถานะตรงอยู่แล้ว แต่ปุ่มบนจออาจถูกกดค้างไว้ผิด (เช่นโดนบล็อก) → sync กลับ
            if hasattr(self, "seg_wp_mode") and self.seg_wp_mode.current() != (1 if on else 0):
                self.seg_wp_mode.setCurrent(1 if on else 0)
            self._wp_update_mode_hint()
            return
        # เปลี่ยนโหมดแล้วเส้นทางเดิมคนละรูปแบบกัน — ล้างทิ้งกันสับสน
        if self._wp_has_any_route():
            self._js("clearAllWaypoints()")
            self._waypoint_route = None
            self._wp_routes = {}
            self._show_toast("เปลี่ยนโหมดเส้นทาง — ล้างจุดเดิมทิ้ง", "info")
        self._wp_separate = on
        if hasattr(self, "seg_wp_mode"):
            self.seg_wp_mode.setCurrent(1 if on else 0)
        self._log(f"Waypoint route mode → {'SEPARATE (แยกลำ)' if on else 'GROUPED (ร่วม)'}",
                  category="COMMAND")
        self._wp_render_points_label()

    def _wp_plot_target(self):
        """SEPARATE: ลำที่กำลังวางแผนให้ = ลำที่โฟกัสอยู่ (คลิกการ์ดฝั่งซ้าย)"""
        sel = self._selected_or_all()
        if not sel:
            return 0
        did = int(self._selected_id or 0)
        return did if did in sel else sel[0]

    def _wp_has_any_route(self):
        if self._wp_separate:
            return any(not r.is_empty() for r in self._wp_routes.values())
        return self._waypoint_route is not None and not self._waypoint_route.is_empty()

    def _wp_all_routes(self):
        """{drone_id: WaypointRoute} ของทุกลำที่มีเส้นทาง (ใช้ตอน execute/ตรวจชน)"""
        if self._wp_separate:
            return {d: r for d, r in self._wp_routes.items() if not r.is_empty()}
        if self._waypoint_route is None or self._waypoint_route.is_empty():
            return {}
        return {d: self._waypoint_route for d in self._waypoint_route.drone_ids}

    def _wp_route_for_key(self, route_key):
        if self._wp_separate:
            return self._wp_routes.get(int(route_key))
        return self._waypoint_route

    def _wp_set_action(self, route_key, index, action):
        if self._waypoint_executing or self._wave_executing:
            self._show_toast("แก้การปล่อยของไม่ได้ระหว่างกำลัง EXECUTE", "err")
            return
        route = self._wp_route_for_key(route_key)
        if route is None:
            return
        try:
            wp = route.set_action(int(index), action)
        except (ValueError, IndexError):
            return
        self._js(f"setWaypointAction({int(route_key)}, {int(index)}, {wp.action!r})")
        label = {"servo_a": "ปล่อย A (CH7)", "servo_b": "ปล่อย B (CH8)"}.get(
            wp.action, "ไม่ทำอะไร")
        self._log(f"Waypoint #{wp.index + 1} → {label}", category="COMMAND")
        self._wp_render_points_label()
        self._push_map3d_view(force=True)

    def _wp_set_wait(self, route_key, index, seconds):
        """ตั้ง/ล้าง WAIT ของ waypoint — คืน True เมื่อสำเร็จ

        WAIT เป็น metadata แยกจาก action A/B — ห้ามแก้ระหว่าง EXECUTE
        """
        if self._waypoint_executing or self._wave_executing:
            self._show_toast("แก้ WAIT ไม่ได้ระหว่างกำลัง EXECUTE", "err")
            return False
        route = self._wp_route_for_key(route_key)
        if route is None:
            return False
        try:
            wp = route.set_wait(int(index), int(seconds))
        except waypoint_logic.WaitLimitError:
            self._show_toast(
                f"ตั้ง WAIT ได้สูงสุด {waypoint_logic.MAX_WAIT_POINTS} จุดต่อ Route"
                " · ลบ WAIT จุดเดิมก่อน", "err")
            self._log("Waypoint: ตั้ง WAIT เกินจำนวนสูงสุดต่อ Route ถูกปฏิเสธ",
                      category="COMMAND", severity="WARNING")
            return False
        except (ValueError, IndexError):
            return False
        self._js(f"setWaypointWait({int(route_key)}, {int(index)}, {wp.wait_seconds})")
        if wp.wait_seconds:
            self._log(f"Waypoint #{wp.index + 1} → WAIT {wp.wait_minutes} นาที",
                      category="COMMAND")
        else:
            self._log(f"Waypoint #{wp.index + 1} → ยกเลิก WAIT", category="COMMAND")
        self._wp_render_points_label()
        self._push_map3d_view(force=True)
        return True

    def _wp_clear_wait(self, route_key, index):
        return self._wp_set_wait(route_key, index, 0)

    def _wp_prompt_wait(self, route_key, index):
        """เปิด popup กรอกเวลา WAIT (นาที) แล้วตั้งค่าให้ waypoint"""
        if self._waypoint_executing or self._wave_executing:
            self._show_toast("แก้ WAIT ไม่ได้ระหว่างกำลัง EXECUTE", "err")
            return
        route = self._wp_route_for_key(route_key)
        if route is None:
            return
        wp = next((p for p in route.points if p.index == int(index)), None)
        if wp is None:
            return
        max_min = waypoint_logic.MAX_WAIT_SECONDS // 60
        # กันเพิ่มจุดที่เกินโควตาตั้งแต่ก่อนเปิด popup (แก้จุดเดิมยังทำได้เสมอ)
        if wp.wait_seconds == 0 and route.wait_count() >= waypoint_logic.MAX_WAIT_POINTS:
            self._show_toast(
                f"ตั้ง WAIT ได้สูงสุด {waypoint_logic.MAX_WAIT_POINTS} จุดต่อ Route"
                " · ลบ WAIT จุดเดิมก่อน", "err")
            return
        default = wp.wait_minutes if wp.wait_seconds else 1
        minutes, ok = QInputDialog.getInt(
            self, f"WAIT ที่ Waypoint #{int(index) + 1}",
            f"รอกี่นาที? (สูงสุด {max_min} นาที)", default, 1, max_min, 1)
        if not ok:
            return
        self._wp_set_wait(route_key, index, int(minutes) * 60)

    def _wp_action_submenu(self, menu, route_key, wp):
        # ── ACTION (A/B เดิม) — คงพฤติกรรมเดิมทุกอย่าง ──
        none = menu.addAction("ไม่ทำอะไร")
        a = menu.addAction("ปล่อย A (CH7)")
        b = menu.addAction("ปล่อย B (CH8)")
        none.setCheckable(True); a.setCheckable(True); b.setCheckable(True)
        none.setChecked(not wp.action)
        a.setChecked(wp.action == "servo_a")
        b.setChecked(wp.action == "servo_b")
        none.triggered.connect(
            lambda _=False, k=route_key, i=wp.index: self._wp_set_action(k, i, ""))
        a.triggered.connect(
            lambda _=False, k=route_key, i=wp.index: self._wp_set_action(k, i, "servo_a"))
        b.triggered.connect(
            lambda _=False, k=route_key, i=wp.index: self._wp_set_action(k, i, "servo_b"))
        # ── WAIT (metadata แยก) ──
        menu.addSeparator()
        head = menu.addAction("— WAIT / รอ —")
        head.setEnabled(False)
        setw = menu.addAction("ตั้งเวลารอ...")
        setw.triggered.connect(
            lambda _=False, k=route_key, i=wp.index: self._wp_prompt_wait(k, i))
        if wp.wait_seconds:
            cur = menu.addAction(f"WAIT ปัจจุบัน: {wp.wait_minutes} นาที")
            cur.setEnabled(False)
            clr = menu.addAction("ยกเลิก WAIT")
            clr.triggered.connect(
                lambda _=False, k=route_key, i=wp.index: self._wp_clear_wait(k, i))

    def _wp_action_menu(self, route_key, index):
        route = self._wp_route_for_key(route_key)
        if route is None:
            return
        wp = next((p for p in route.points if p.index == int(index)), None)
        if wp is None:
            return
        menu = QMenu(self)
        menu.setTitle(f"Waypoint {wp.index + 1}")
        self._wp_action_submenu(menu, int(route_key), wp)
        menu.exec_(QCursor.pos())

    def _wp_points_menu(self, pos):
        menu = QMenu(self.lbl_wp_points)
        routes = self._wp_all_routes()
        seen = set()
        for did, route in sorted(routes.items()):
            marker = id(route)
            if marker in seen:
                continue
            seen.add(marker)
            route_key = int(did) if self._wp_separate else int(self._wp_key)
            for wp in route.points:
                sub = menu.addMenu(f"จุดที่ {wp.index + 1}")
                self._wp_action_submenu(sub, route_key, wp)
        if not menu.actions():
            empty = menu.addAction("ยังไม่มีจุด Waypoint")
            empty.setEnabled(False)
        menu.exec_(self.lbl_wp_points.mapToGlobal(pos))

    def _on_waypoint_click(self, lat, lon):
        """คลิกแผนที่ในโหมด Waypoint — วางจุดมาร์คเกอร์ ไม่บินทันที"""
        ids = self._selected_or_all()
        if not ids:
            self._show_toast("ยังไม่ได้เลือกโดรน · คลิกการ์ดซ้าย หรือกด FLEET", "err")
            self._log("Waypoint: ยกเลิกวางจุด — ยังไม่ได้เลือกโดรน",
                      category="COMMAND", severity="WARNING")
            return

        # ── โหมด Swarm: กำหนดได้เฉพาะลำแม่ (Leader Path) ──
        if self._swarm_active:
            head = int(self._head_id or 0)
            if not head:
                self._show_toast("โหมด Swarm — ยังไม่มีลำแม่ (Head)", "err")
                return
            if self._wp_separate:          # กันหลุด: swarm ต้องเป็น GROUPED เสมอ
                self._wp_set_separate(False)
            if self._waypoint_route is None:
                self._waypoint_route = waypoint_logic.WaypointRoute(ids)
                self._wp_key = 0
            wp = self._waypoint_route.add(lat, lon)
            self._js(f"addWaypoint(0, {lat:.7f}, {lon:.7f}, "
                     f"{drone_color(head)!r}, {wp.index})")
            self._log(f"Waypoint #{wp.index + 1} → {lat:.6f},{lon:.6f} "
                      f"[Swarm · Leader Path ของ Drone {head}]", category="COMMAND")
            self._wp_render_points_label()
            return

        # ── SEPARATE: เข้าเส้นทางของ "ลำที่โฟกัสอยู่" ลำเดียว ──
        if self._wp_separate:
            did = self._wp_plot_target()
            if not did:
                self._show_toast("ยังไม่ได้เลือกลำที่จะวางแผนให้", "err")
                return
            route = self._wp_routes.get(did)
            if route is None:
                route = waypoint_logic.WaypointRoute([did])
                self._wp_routes[did] = route
            wp = route.add(lat, lon)
            self._js(f"addWaypoint({did}, {lat:.7f}, {lon:.7f}, "
                     f"{drone_color(did)!r}, {wp.index})")
            self._log(f"Waypoint #{wp.index + 1} → {lat:.6f},{lon:.6f} "
                      f"[SEPARATE · Drone {did}]", category="COMMAND")
            self._wp_render_points_label()
            return

        # ── GROUPED: เส้นทางเดียวร่วมกัน ──
        if self._waypoint_route is None:
            self._waypoint_route = waypoint_logic.WaypointRoute(ids)
            self._wp_key = ids[0] if len(ids) == 1 else 0
        route = self._waypoint_route
        wp = route.add(lat, lon)
        if len(route.drone_ids) == 1:
            color = drone_color(route.drone_ids[0])
        elif self._head_id and self._head_id in route.drone_ids:
            color = drone_color(self._head_id)
        else:
            color = T("cyan")
        self._js(f"addWaypoint({self._wp_key}, {lat:.7f}, {lon:.7f}, "
                 f"{color!r}, {wp.index})")
        kind = "โดรนเดี่ยว" if len(route.drone_ids) == 1 else \
               f"GROUPED {len(route.drone_ids)} ลำ (ไปพร้อมกัน)"
        self._log(f"Waypoint #{wp.index + 1} → {lat:.6f},{lon:.6f} [{kind}]",
                  category="COMMAND")
        self._wp_render_points_label()

    def _apply_mission_core_state(self, state, rebuild=False):
        """Apply GetMissionState as presentation cache; never emit flight commands."""
        if state is None:
            return
        run_id = int(getattr(state, "run_id", 0) or 0)
        active = bool(getattr(state, "active", False))
        authority = bool(getattr(state, "authority_active", False))
        recovery = bool(getattr(state, "recovery_required", False))
        self._mission_recovery_required = recovery
        same_run = (run_id > 0 and run_id == int(getattr(self, "_mission_shadow_run_id", 0) or 0))

        if (not active or not authority) and not recovery:
            # A terminal snapshot for the bound run is authoritative: stop all
            # Python mission progression caches, but do not issue HOLD/GOTO.
            if same_run or bool(getattr(self, "_mission_core_authority", False)):
                self._mission_core_state = state
                self._mission_core_authority = False
                self._waypoint_executing = False
                self._wp_waits = {}
                if hasattr(self, "_wp_wait_timer"):
                    self._wp_wait_timer.stop()
                reason = str(getattr(state, "terminal_reason", "") or "").strip()
                self._wp_render_status(reason or "Core mission ended")
                self._refresh_flight_mode()
            return

        plan = getattr(state, "plan", None)
        participants = [int(x) for x in getattr(state, "participants", [])]
        plan_id = str(getattr(state, "plan_id", "") or "")
        identity_changed = (
            run_id != int(getattr(self, "_mission_shadow_run_id", 0) or 0)
            or plan_id != str(getattr(self, "_mission_shadow_operation_id", "") or ""))

        self._mission_core_authority = not recovery
        self._mission_core_state = state
        self._mission_shadow_run_id = run_id
        self._mission_shadow_operation_id = str(
            getattr(state, "operation_id", "") or plan_id)
        self._mission_shadow_cancel_pending = False
        self._waypoint_executing = not recovery
        mode = int(getattr(state, "mode", rpc.mission_pb2.MISSION_MODE_GROUPED))
        self._wp_separate = (mode == int(rpc.mission_pb2.MISSION_MODE_SEPARATE))
        active_participants = [
            int(x) for x in getattr(state, "active_participants", [])]
        excluded_participants = [
            int(x) for x in getattr(state, "excluded_participants", [])]
        if (mode == int(rpc.mission_pb2.MISSION_MODE_SWARM_LEADER)
                and active_participants):
            self._wp_target_ids = active_participants
        else:
            self._wp_target_ids = participants
        self._wp_current_index = max(0, int(getattr(state, "current_index", 0) or 0))
        self._wp_sep_index = {
            int(drone_id): max(0, int(index))
            for drone_id, index in dict(getattr(state, "sep_index", {})).items()
        }
        self._wp_arrived = {int(x) for x in getattr(state, "arrived", [])}
        original_leader_id = (
            int(getattr(plan, "leader_id", 0) or 0) if plan is not None else 0)
        leader_id = int(
            getattr(state, "current_leader_id", 0) or original_leader_id)
        if mode == int(rpc.mission_pb2.MISSION_MODE_SWARM_LEADER):
            self._head_id = leader_id
            self._leader_id = leader_id
            managed = active_participants or participants
            self._mission_core_ownership = {
                "leader": leader_id,
                "mission_owned": [leader_id] if leader_id else [],
                "swarm_owned": [d for d in managed if d != leader_id],
                "excluded": list(excluded_participants),
            }
        elif self._wp_separate:
            self._mission_core_ownership = {
                "mission_owned": list(participants), "swarm_owned": []}
        else:
            self._mission_core_ownership = {
                "mission_owned": list(participants), "swarm_owned": []}
        # Legacy WAIT callbacks are never allowed to drive a Core-owned run.
        self._wp_waits = {}
        if hasattr(self, "_wp_wait_timer"):
            self._wp_wait_timer.stop()

        routes = list(getattr(plan, "routes", [])) if plan is not None else []

        def rebuild_route(proto_route, route_ids):
            rebuilt = waypoint_logic.WaypointRoute(route_ids)
            rebuilt.points = []
            for i, point in enumerate(getattr(proto_route, "points", [])):
                action = ""
                if int(getattr(point, "action", 0)) == int(
                        rpc.mission_pb2.MISSION_WP_ACTION_SERVO_A):
                    action = "servo_a"
                elif int(getattr(point, "action", 0)) == int(
                        rpc.mission_pb2.MISSION_WP_ACTION_SERVO_B):
                    action = "servo_b"
                rebuilt.points.append(waypoint_logic.Waypoint(
                    int(getattr(point, "seq", i)), float(point.lat), float(point.lon),
                    action, int(getattr(point, "wait_seconds", 0) or 0)))
            rebuilt._next_index = max(
                [wp.index for wp in rebuilt.points], default=-1) + 1
            return rebuilt

        needs_rebuild = rebuild or identity_changed or (
            self._waypoint_route is None and not self._wp_routes)
        if routes and needs_rebuild:
            self._js("clearAllWaypoints()")
            if self._wp_separate:
                self._waypoint_route = None
                self._wp_routes = {}
                for proto_route in routes:
                    drone_id = int(getattr(proto_route, "drone_id", 0) or 0)
                    if drone_id <= 0:
                        continue
                    route = rebuild_route(proto_route, [drone_id])
                    self._wp_routes[drone_id] = route
                    self._wp_sep_index.setdefault(drone_id, 0)
                    for wp in route.points:
                        self._js(
                            f"addWaypoint({drone_id}, {wp.lat:.7f}, {wp.lon:.7f}, "
                            f"{drone_color(drone_id)!r}, {wp.index})")
                self._wp_key = 0
            else:
                route = rebuild_route(routes[0], participants)
                self._waypoint_route = route
                self._wp_routes = {}
                self._wp_key = participants[0] if len(participants) == 1 else 0
                color = (drone_color(participants[0])
                         if len(participants) == 1 else T("cyan"))
                for wp in route.points:
                    self._js(
                        f"addWaypoint({self._wp_key}, {wp.lat:.7f}, {wp.lon:.7f}, "
                        f"{color!r}, {wp.index})")
            self._wp_render_points_label()
            if mode == int(rpc.mission_pb2.MISSION_MODE_SWARM_LEADER):
                managed = active_participants or participants
                followers = [d for d in managed if d != leader_id]
                detail = (f"Leader D{leader_id}=Mission · "
                          f"Followers {followers}=Swarm · "
                          f"Excluded {excluded_participants}=Operator")
            elif self._wp_separate:
                detail = "SEPARATE per-drone routes"
            else:
                detail = f"WP {self._wp_current_index + 1}"
            if recovery:
                self._log(
                    f"CORE RECOVERY REQUIRED · previous run {run_id} · {detail}",
                    category="STATUS", severity="WARNING")
            else:
                self._log(
                    f"Cockpit rebound to Core mission run {run_id} · {detail}",
                    category="STATUS", severity="SUCCESS")
        elif not recovery:
            self._wp_render_status()
        if recovery:
            reason = str(getattr(state, "recovery_reason", "") or
                         getattr(state, "terminal_reason", "") or
                         "Core restarted with an unfinished mission")
            self._wp_render_status(f"CORE RECOVERY REQUIRED · {reason}")
        # Presentation recovery is deliberately command-free: it never calls
        # _goto_one, HOLD, servo, swarm_start, or any navigation RPC.
        self._refresh_flight_mode()

    def _wp_render_points_label(self):
        if self._wp_separate:
            routes = {d: r for d, r in self._wp_routes.items() if not r.is_empty()}
            if not routes:
                self.lbl_wp_points.setText("ยังไม่มีจุด")
            else:
                cur = self._wp_plot_target()
                blocks = []
                for d in sorted(routes):
                    mark = "▶ " if d == cur else "  "
                    actions = sum(1 for wp in routes[d].points if wp.action)
                    waits = routes[d].wait_count()
                    suffix = f" · ปล่อยของ {actions}" if actions else ""
                    if waits:
                        suffix += f" · WAIT {waits}"
                    blocks.append(f"{mark}D{d} · {len(routes[d])} จุด{suffix}")
                self.lbl_wp_points.setText("\n".join(blocks))
        else:
            route = self._waypoint_route
            if route is None or route.is_empty():
                self.lbl_wp_points.setText("ยังไม่มีจุด")
            else:
                pts = []
                for wp in route.points:
                    act = ('[A]' if wp.action == 'servo_a'
                           else '[B]' if wp.action == 'servo_b' else '')
                    wait = f"[WAIT {wp.wait_minutes}m]" if wp.wait_seconds else ''
                    tags = " ".join(t for t in (act, wait) if t)
                    pts.append(f"{wp.index + 1}. {wp.lat:.5f}, {wp.lon:.5f}  {tags}".rstrip())
                self.lbl_wp_points.setText("\n".join(pts))
        self._wp_update_mode_hint()
        self._wp_render_status()
        self._summ_sync_waypoint()

    def _wp_render_status(self, msg=None):
        if msg is not None:
            self.lbl_wp_status.setText(msg)
            return
        if bool(getattr(self, "_mission_core_authority", False)):
            state = getattr(self, "_mission_core_state", None)
            total = len(self._waypoint_route) if self._waypoint_route else 0
            idx = max(0, int(getattr(state, "current_index", self._wp_current_index) or 0))
            waits = list(getattr(state, "waits", [])) if state is not None else []
            if waits:
                rem = max(float(getattr(w, "remaining_s", 0.0) or 0.0) for w in waits)
                mm, ss = divmod(int(rem + 0.999), 60)
                self.lbl_wp_status.setText(
                    f"CORE WAITING · จุดที่ {idx + 1}/{total} · {mm:02d}:{ss:02d} remaining")
            else:
                self.lbl_wp_status.setText(
                    f"CORE EXECUTE · จุดที่ {min(idx + 1, total)}/{total}" if total
                    else "CORE EXECUTE · กำลังโหลดแผนภารกิจ")
            return
        if self._wave_executing and self._wave_groups:
            group = self._wave_groups[self._wave_group_index]
            total_groups = len(self._wave_groups)
            group_no = self._wave_group_index + 1
            total_points = len(self._waypoint_route) if self._waypoint_route else 0
            if self._wave_phase == "takeoff":
                waiting = self._wp_grounded_ids(
                    did for did in self._connected_ids()
                    if self.group_of.get(int(did)) == group)
                detail = ("รอ TAKEOFF " + ",".join(f"D{did}" for did in waiting)
                          if waiting else "TAKEOFF ครบ · เตรียมเส้นทาง")
            elif self._wave_phase == "route":
                detail = f"กำลังบิน จุดที่ {min(self._wp_current_index + 1, total_points)}/{total_points}"
            elif self._wave_phase == "waiting_land":
                detail = "กลับฐาน/ลงจอด · รอ disarm ครบ"
            elif self._wave_phase == "landed":
                detail = "ลงจอดแล้ว · เตรียมกลุ่มถัดไป"
            else:
                detail = "เตรียมเริ่ม"
            text = f"WAVE: กลุ่ม {group} ({group_no}/{total_groups}) · {detail}"
            self.lbl_wp_status.setText(text)
            if hasattr(self, "lbl_wave_progress"):
                self._set_fixed_label_text(self.lbl_wave_progress, text)
            return
        routes = self._wp_all_routes()
        if self._waypoint_executing:
            if self._wp_separate:
                done = sum(1 for d in self._wp_target_ids
                           if self._wp_sep_index.get(d, 0) >= len(routes.get(d, [])))
                self.lbl_wp_status.setText(
                    f"กำลังบิน (แยกลำ) · จบแล้ว {done}/{len(self._wp_target_ids)} ลำ")
            else:
                n = len(self._waypoint_route) if self._waypoint_route else 0
                self.lbl_wp_status.setText(
                    f"กำลังบินไปจุดที่ {self._wp_current_index + 1}/{n}")
            return
        if self._wp_separate:
            total = sum(len(r) for r in routes.values())
            self.lbl_wp_status.setText(f"พร้อม · {len(routes)} ลำ · {total} จุดรวม")
        else:
            n = len(self._waypoint_route) if self._waypoint_route else 0
            self.lbl_wp_status.setText(f"พร้อม · {n} จุด")

    def _wp_undo(self):
        """Undo — ลบจุดสุดท้าย (SEPARATE = ของลำที่โฟกัสอยู่)"""
        if self._waypoint_executing or self._wave_executing:
            self._show_toast("Undo ไม่ได้ระหว่างกำลัง EXECUTE", "err")
            return
        if self._wp_separate:
            did = self._wp_plot_target()
            route = self._wp_routes.get(did)
            if route is None or route.is_empty():
                self._show_toast("ลำนี้ยังไม่มีจุดให้ลบ", "info")
                return
            route.remove_last()
            self._js(f"removeLastWaypoint({did})")
            self._log(f"Waypoint: ลบจุดสุดท้ายของ Drone {did} (Undo)", category="COMMAND")
        else:
            route = self._waypoint_route
            if route is None or route.is_empty():
                self._show_toast("ไม่มีจุดให้ลบ", "info")
                return
            route.remove_last()
            self._js(f"removeLastWaypoint({self._wp_key})")
            self._log("Waypoint: ลบจุดสุดท้าย (Undo)", category="COMMAND")
        self._wp_render_points_label()

    def _wp_clear(self):
        """ล้างจุด Waypoint ทั้งหมดทิ้ง เพื่อเริ่มวาดใหม่"""
        if self._waypoint_executing or self._wave_executing:
            self._show_toast("ล้างเส้นทางไม่ได้ระหว่างกำลัง EXECUTE", "err")
            return
        if not self._wp_has_any_route():
            self._show_toast("ยังไม่มีจุดให้ล้าง", "info")
            return
        if self._wp_separate:
            total = sum(len(r) for r in self._wp_routes.values())
        else:
            total = len(self._waypoint_route)
        if not self._confirm(
                self, "ล้างเส้นทาง Waypoint",
                f"ลบจุด Waypoint ที่วางไว้ทั้งหมด ({total} จุด) ใช่หรือไม่?",
                ok_text="ล้างทั้งหมด", danger=True):
            return
        self._js("clearAllWaypoints()")
        self._waypoint_route = None
        self._wp_routes = {}
        self._log("Waypoint: ล้างเส้นทางทั้งหมด", category="COMMAND")
        self._wp_render_points_label()

    # ── ตรวจกันชนก่อนบิน ──
    def _wp_check_conflicts(self, routes):
        """คืนรายการคู่ที่เสี่ยงชน (ระดับความสูงเดียวกัน + เส้นทางใกล้/ตัดกัน)"""
        paths = {d: r.as_pairs() for d, r in routes.items()}
        alts = {d: float(self._last_alt.get(d) or self._alt_for(d)) for d in paths}
        pos = self._fleet_positions()
        starts = {d: (p[1], p[0]) for d, p in pos.items() if d in paths}
        return waypoint_logic.check_route_conflicts(
            paths, alts, start_positions=starts,
            alt_sep_m=self._wp_alt_sep, min_dist_m=self._wp_min_dist)

    def _wp_block_on_conflicts(self, conflicts):
        """แจ้งเตือนคู่ที่เสี่ยงชน แล้วบล็อกไม่ให้บิน"""
        lines = "\n".join("• " + c.describe() for c in conflicts[:6])
        more = f"\n… และอีก {len(conflicts) - 6} คู่" if len(conflicts) > 6 else ""
        for c in conflicts:
            self._log(f"⚠ Waypoint เสี่ยงชน — {c.describe()}",
                      vehicle=f"Drone {c.a}", category="ALERT", severity="ERROR")
        self._show_banner(
            f"บินไม่ได้ — เส้นทางเสี่ยงชน {len(conflicts)} คู่", T("red"))
        self._show_toast(f"บล็อกไว้ · เส้นทางเสี่ยงชน {len(conflicts)} คู่", "err")
        self._confirm(
            self, "เส้นทางเสี่ยงชนกัน — ยังบินไม่ได้",
            "โดรนเหล่านี้จะบินที่ระดับความสูงเดียวกันและเส้นทางใกล้/ตัดกัน:\n\n"
            f"{lines}{more}\n\n"
            "แก้ก่อนบิน: ตั้งความสูง (ALT) ของแต่ละลำให้ต่างกันในการ์ดโดรนฝั่งซ้าย "
            "หรือแก้เส้นทางไม่ให้ตัดกัน",
            ok_text="เข้าใจแล้ว", cancel_text="ปิด", danger=True)
        self._wp_render_status(f"บล็อก · เสี่ยงชน {len(conflicts)} คู่")

    @staticmethod
    def _wp_unique_routes(routes):
        unique = []
        seen = set()
        for route in routes.values():
            marker = id(route)
            if marker not in seen:
                seen.add(marker)
                unique.append(route)
        return unique

    def _wp_action_points(self, routes):
        points = []
        for route in self._wp_unique_routes(routes):
            points.extend(wp for wp in route.points if wp.action)
        return points

    def _wp_confirm_actions(self, routes, wave_groups=0, auto=False):
        actions = self._wp_action_points(routes)
        if not actions:
            return True
        if auto:
            # ยืนยันไปแล้วบนแท็บเล็ต (เห็นจำนวนจุดปล่อยของก่อนกด) — ไม่ถามซ้ำที่คอม
            self._log("Waypoint: ยืนยันการปล่อยของ %d จุด [tablet]" % len(actions),
                      category="COMMAND")
            return True
        lines = []
        for wp in actions:
            label = "A (CH7)" if wp.action == "servo_a" else "B (CH8)"
            lines.append(f"จุดที่ {wp.index + 1}  →  {label}")
        repeat = ""
        if wave_groups:
            repeat = (f"\n\nWAVE จะทำซ้ำทุกกลุ่ม ({wave_groups} กลุ่ม)"
                      f"\nรวมปล่อยทั้งหมด {len(actions) * wave_groups} ครั้ง")
        text = (f"เส้นทางนี้จะปล่อยของ {len(actions)} ครั้งต่อรอบ\n\n"
                + "\n".join(lines) + repeat
                + "\n\nตรวจพื้นที่ใต้เส้นทางให้ปลอดคนก่อนยืนยัน")
        return self._confirm(
            self, "ยืนยันการปล่อยของตาม Waypoint", text,
            ok_text="ยืนยันปล่อยของ", danger=True)

    def _wp_run_action(self, ids, wp, callback):
        """HOLD → servo → รอ 2 วิ → release → เดินเส้นทางต่อ"""
        ids = [int(d) for d in ids]
        label = "A" if wp.action == "servo_a" else "B"
        channel = self.SERVO_CH[label]
        pwm = self._servo_on_pwm(label)
        generation = self._wave_generation
        self._log(f"Waypoint #{wp.index + 1}: HOLD แล้วปล่อย {label} (CH{channel})",
                  category="COMMAND", severity="WARNING")
        run_id = self._flight_run_id
        if self._flight_is_current(run_id) and self._flight_run.kind == "waypoint":
            self._flight_step_active(run_id, "waypoint_action",
                                     "WP %d · HOLD → SERVO %s → wait 2s" % (wp.index + 1, label))
        elif (self._flight_is_current(run_id) and self._flight_run.kind == "wave"
              and 0 <= self._wave_group_index < len(self._wave_groups)):
            group = self._wave_groups[self._wave_group_index]
            self._flight_step_active(
                run_id, "g%d_block" % group,
                self._wave_group_detail(group, "WP %d · กำลังปล่อย %s" % (wp.index + 1, label)))

        def finish(released=True):
            for did in ids:
                threading.Thread(
                    target=lambda d=did: self._safe(
                        lambda: self._dispatch_core(
                            f"SERVO RELEASE CH{int(channel)} [waypoint]",
                            lambda: self.client.servo_release(d, channel),
                            source="waypoint-action", targets=[int(d)],
                            enforce_dedup=False)), daemon=True).start()
            if (self._waypoint_executing
                    and (not self._wave_executing or generation == self._wave_generation)):
                if (released and self._flight_is_current(run_id)
                        and self._flight_run.kind == "waypoint"):
                    self._flight_step_done(run_id, "waypoint_action", "Servo %s released" % label)
                elif (not released and self._flight_is_current(run_id)
                      and self._flight_run.kind == "waypoint"):
                    self._flight_run.fail(
                        "waypoint_action", "Servo %s failed" % label,
                        skip_downstream=False)
                    self._render_summary()
                elif (released and self._flight_is_current(run_id)
                      and self._flight_run.kind == "wave"
                      and 0 <= self._wave_group_index < len(self._wave_groups)):
                    group = self._wave_groups[self._wave_group_index]
                    self._wave_record_payload_release(group, label, wp.index)
                elif (not released and self._flight_is_current(run_id)
                      and self._flight_run.kind == "wave"
                      and 0 <= self._wave_group_index < len(self._wave_groups)):
                    group = self._wave_groups[self._wave_group_index]
                    self._wave_record_payload_failure(group, label, wp.index)
                callback()

        def worker():
            try:
                if (not self._waypoint_executing
                        or (self._wave_executing and generation != self._wave_generation)):
                    return
                hold_result = self._dispatch_core(
                    "HOLD [waypoint-action]", lambda: self.client.hold(ids),
                    source="waypoint-action", targets=ids,
                    enforce_dedup=False)
                if not bool(getattr(hold_result, "ok", False)):
                    raise RuntimeError(
                        "HOLD rejected: %s" % getattr(hold_result, "message", "unknown error"))
                if (not self._waypoint_executing
                        or (self._wave_executing and generation != self._wave_generation)):
                    return
                for did in ids:
                    servo_result = self._dispatch_core(
                        f"SERVO SET CH{int(channel)} [waypoint]",
                        lambda d=did: self.client.servo_set(d, channel, pwm),
                        source="waypoint-action", targets=[int(did)],
                        enforce_dedup=False)
                    if not bool(getattr(servo_result, "ok", False)):
                        raise RuntimeError(
                            "SERVO D%d rejected: %s" % (
                                did, getattr(servo_result, "message", "unknown error")))
                self.ui_call.emit(lambda: QTimer.singleShot(2000, finish))
            except Exception as exc:
                self.cmd_result.emit(f"WAYPOINT SERVO {label}: ERROR {exc}")
                self.ui_call.emit(lambda: finish(False))

        threading.Thread(target=worker, daemon=True).start()

    def _wp_is_airborne(self, drone_id):
        """True เมื่อ telemetry ยืนยันว่า armed และสูงพ้นพื้นแล้ว"""
        did = int(drone_id)
        # ค่าค้างจากลำที่หลุดการเชื่อมต่อห้ามใช้ยืนยันความพร้อมบิน
        if did not in self._connected_ids():
            return False
        t = self._last_telem.get(did)
        if t is None:
            return False
        position = getattr(t, "position", None)
        alt = float(getattr(position, "alt_rel", self._last_alt.get(did, 0.0)) or 0.0)
        # telemetry ปกติมี armed; fallback ตามความสูงรองรับข้อมูลรุ่นเก่า
        armed = bool(getattr(t, "armed", alt >= self._wp_airborne_alt_m))
        return armed and alt >= self._wp_airborne_alt_m

    def _wp_grounded_ids(self, ids):
        return [int(did) for did in ids if not self._wp_is_airborne(did)]

    def _wp_require_takeoff(self, ids, context, on_ready, on_cancel=None, auto=False,
                            flow_run_id=None):
        """ถาม TAKEOFF เฉพาะลำที่ยังไม่บิน แล้วรอยืนยันจาก telemetry ก่อนเริ่ม route"""
        # Keep the public call shape stable: tests and integrations may replace
        # this safety adapter with the original five-argument callable.
        if flow_run_id is None:
            candidate = getattr(self, "_wp_flow_run_id", None)
            if self._flight_is_current(candidate) and self._flight_run.kind == "waypoint":
                flow_run_id = candidate
        ids = [int(did) for did in ids]
        grounded = self._wp_grounded_ids(ids)
        if not grounded:
            if flow_run_id is not None and self._flight_is_current(flow_run_id):
                self._flight_run.skip("auto_takeoff", "All targets already airborne")
                self._flight_run.skip("wait_airborne")
                self._render_summary()
            self._summ_set(
                "takeoff_check", "TAKEOFF CHECK",
                f"พร้อมบินครบ {len(ids)} ลำ · ไม่ต้อง TAKEOFF เพิ่ม")
            on_ready()
            return True

        # ต้องขึ้นบินจริงในขั้นนี้ → ผ่านด่านทดสอบก่อนบินก่อน (เหมือนกด TAKEOFF เอง)
        # ทางแท็บเล็ตตรวจด่านนี้ไปแล้วก่อนส่งคำสั่ง จึงข้ามการ "ถาม" ตรงนี้
        if not auto and not self._preflight_gate("AUTO TAKEOFF"):
            self._show_toast("ยกเลิก EXECUTE · ยังไม่ได้ TAKEOFF", "info")
            if on_cancel:
                on_cancel()
            return False

        names = ", ".join(f"{self._drone_name(did)} (D{did})" for did in grounded)
        alts = {did: self._alt_for(did) for did in grounded}
        alt_text = ", ".join(f"D{did} {alts[did]:.0f}m" for did in grounded)
        self._summ_set(
            "takeoff_check", "TAKEOFF CHECK",
            f"{context} · ยังไม่บิน {len(grounded)} ลำ: {alt_text}")
        if not auto and not self._confirm(
                self, "ต้อง TAKEOFF ก่อน EXECUTE ROUTE",
                f"{context}\n\nโดรนที่ยังไม่ขึ้นบิน {len(grounded)} ลำ:\n{names}\n\n"
                f"ความสูงที่จะ TAKEOFF: {alt_text}\n"
                f"เมื่อยืนยัน ระบบจะ TAKEOFF และรอจนพ้นพื้นก่อนเริ่มเส้นทางอัตโนมัติ",
                ok_text="TAKEOFF แล้วเริ่ม ROUTE", accent=T("green")):
            self._summ_event(
                "ยกเลิก", f"{context} · ไม่ยืนยัน TAKEOFF จึงยังไม่ EXECUTE",
                cancelled=True)
            self._show_toast("ยกเลิก EXECUTE · ยังไม่ได้ TAKEOFF", "info")
            if on_cancel:
                on_cancel()
            return False

        self._wp_takeoff_generation += 1
        generation = self._wp_takeoff_generation
        self._wp_takeoff_pending = True
        mode = self.takeoff_panel.mode() if hasattr(self, "takeoff_panel") else "all"
        steps = swarm_logic.plan_takeoff(
            mode, grounded, head_id=self._head_id,
            default_alt=self.sf_takeoff.value(), alts=alts,
            priority=self._priority_ids())
        self._summ_event(
            "คำสั่งล่าสุด", f"AUTO TAKEOFF {len(grounded)} ลำก่อน {context}")
        self._summ_set(
            "takeoff_check", "TAKEOFF CHECK",
            f"กำลัง TAKEOFF/รอยืนยันพ้นพื้น · {alt_text}")
        if flow_run_id is not None:
            self._flight_step_active(flow_run_id, "auto_takeoff",
                                     "Sending TAKEOFF for %d target(s)" % len(grounded))
            self._execute_takeoff_steps(steps, mode, generation=generation,
                                        flow_run_id=flow_run_id,
                                        flow_step_id="auto_takeoff")
        else:
            self._execute_takeoff_steps(steps, mode, generation=generation)
        deadline = time.monotonic() + self._wp_takeoff_timeout_s

        def check():
            if generation != self._wp_takeoff_generation:
                return
            waiting = self._wp_grounded_ids(ids)
            if not waiting:
                self._wp_takeoff_pending = False
                if flow_run_id is not None:
                    self._flight_step_done(flow_run_id, "auto_takeoff", "Command accepted")
                    self._flight_step_active(flow_run_id, "wait_airborne",
                                             "Airborne %d/%d" % (len(ids), len(ids)))
                    self._flight_step_done(flow_run_id, "wait_airborne",
                                           "Airborne %d/%d" % (len(ids), len(ids)))
                self._summ_set(
                    "takeoff_check", "TAKEOFF CHECK",
                    f"พ้นพื้นครบ {len(ids)} ลำ ✓ · เริ่ม {context}")
                self._show_toast("TAKEOFF ครบแล้ว · เริ่มเส้นทาง", "ok")
                on_ready()
                return
            if time.monotonic() >= deadline:
                self._wp_takeoff_pending = False
                names_waiting = ", ".join(f"D{did}" for did in waiting)
                self._summ_event(
                    "ยกเลิก", f"{context} · TAKEOFF timeout: {names_waiting}",
                    cancelled=True)
                self._summ_set(
                    "takeoff_check", "TAKEOFF CHECK",
                    f"หมดเวลารอ 90 วิ · ยังไม่พ้นพื้น: {names_waiting}")
                self._show_banner(
                    f"ไม่เริ่ม ROUTE — {names_waiting} ยังไม่พ้นพื้นหลังรอ 90 วินาที",
                    T("red"))
                self._show_toast("TAKEOFF ไม่ครบ · ยกเลิก ROUTE", "err")
                if flow_run_id is not None:
                    self._flight_step_failed(flow_run_id, "wait_airborne",
                                             "Takeoff timeout: %s" % names_waiting)
                if on_cancel:
                    on_cancel()
                return
            if hasattr(self, "lbl_wp_status"):
                self._wp_render_status(
                    f"รอ TAKEOFF · ยังไม่พ้นพื้น {', '.join(f'D{d}' for d in waiting)}")
            QTimer.singleShot(700, check)

        QTimer.singleShot(700, check)
        return True

    def _wave_execute(self, auto=False):
        self._wave_auto = bool(auto)
        # WAVE is intentionally legacy Python-owned until its Go authority
        # semantics are migrated. Under a configured Core-authority token, every
        # entrypoint (cockpit or Field Tablet) must first prove the Core mission
        # slot is idle so the two executors can never overlap.
        if not mission_shadow.legacy_ownership_allowed(self):
            self._wave_auto = False
            self._log("WAVE EXECUTE ถูกบล็อก — Core mission slot ไม่ยืนยันว่าว่าง",
                      category="COMMAND", severity="ERROR")
            self._show_toast("ไม่เริ่ม WAVE · Core mission ยัง active/ไม่ทราบสถานะ", "err")
            self._wp_render_status("WAVE blocked · Core mission slot not idle")
            return False
        route = self._waypoint_route
        groups = self._wave_selected_groups()
        if self._wp_separate:
            self._show_toast("WAVE ใช้ไม่ได้กับ SEPARATE", "err")
            self._wave_auto = False
            return False
        if route is None or route.is_empty():
            self._show_toast("ยังไม่มีเส้นทางร่วมสำหรับ WAVE", "err")
            self._wave_auto = False
            return False
        if len(groups) < 2:
            self._show_toast("เลือกอย่างน้อย 2 กลุ่มสำหรับ WAVE", "err")
            self._wave_auto = False
            return False
        online = set(self._connected_ids())
        usable = [g for g in groups if any(
            did in online and self.group_of.get(int(did)) == g for did in self.fleet_items)]
        if len(usable) < 2:
            self._show_toast("WAVE ต้องมีอย่างน้อย 2 กลุ่มที่ออนไลน์", "err")
            self._wave_auto = False
            return False
        if not self._wp_confirm_actions({0: route}, wave_groups=len(usable), auto=auto):
            self._show_toast("ยกเลิก EXECUTE · ยังไม่ปล่อยของ", "info")
            self._summ_event(
                "ยกเลิก", "WAVE EXECUTE · ไม่ยืนยันการทำงาน A/B",
                cancelled=True)
            self._wave_auto = False
            return False

        self._wave_generation += 1
        self._wave_executing = True
        # จับค่าตอนเริ่มภารกิจ ไม่อ่าน checkbox สดระหว่างบิน เพื่อไม่ให้พฤติกรรม
        # เปลี่ยนกลาง WAVE. กลุ่มแรกยังถามตามปกติ; ข้ามเฉพาะกลุ่มถัดไป.
        self._wave_auto_next = bool(
            hasattr(self, "chk_wave_auto_next") and self.chk_wave_auto_next.isChecked())
        if hasattr(self, "chk_wave_auto_next"):
            self.chk_wave_auto_next.setEnabled(False)
        self._wave_payload_done = {}
        self._wave_payload_failed = {}
        self._wave_groups = sorted(usable)
        self._wave_group_index = -1
        self._wave_route_template = route
        self._wave_phase = "idle"
        self._wp_flow_run_id = None
        run_id = self._flight_run_start("wave", groups=self._wave_groups)
        # Validation above is real; the first group block becomes active only as
        # _wave_start_next_group selects it, never from a timer guess.
        if self._waypoint_mode:
            self._wp_toggle(False)
        self.btn_wave_cancel.setVisible(True)
        self._refresh_wave_group_choices()
        self._wave_timer.start()
        self._log(f"WAVE EXECUTE · กลุ่ม {' → '.join(map(str, self._wave_groups))}",
                  category="COMMAND", severity="WARNING")
        self._summ_sync_waypoint()
        self._wave_start_next_group()
        return True

    def _wave_start_next_group(self):
        if not self._wave_executing:
            return
        # Re-check before every *subsequent* group.  Another authorized client
        # could have started a Core-owned mission while this WAVE was waiting for
        # the previous group to land.  Local abort invalidates callbacks/return
        # bookkeeping without sending a competing flight command.
        if (self._wave_group_index >= 0
                and not mission_shadow.legacy_ownership_allowed(self)):
            self._wave_abort("Core mission slot changed — WAVE stopped", clear_route=True)
            self._show_banner(
                "WAVE STOPPED — Core mission active/unknown; next group was not started",
                T("red"))
            return
        if (self._wave_group_index >= 0 and self._flight_run
                and self._flight_run.kind == "wave"):
            prior = self._wave_groups[self._wave_group_index]
            self._flight_step_done(
                self._flight_run_id, "g%d_block" % prior,
                self._wave_group_detail(prior, "COMPLETE · disarmed"))
        self._wave_group_index += 1
        if self._wave_group_index >= len(self._wave_groups):
            self._wave_complete()
            return
        group = self._wave_groups[self._wave_group_index]
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, "PREPARE"))
        ids = sorted(did for did in self._connected_ids()
                     if self.group_of.get(int(did)) == group)
        if not ids:
            self._wave_abort(f"กลุ่ม {group} ไม่มีลำออนไลน์", clear_route=True)
            return
        self._wave_phase = "takeoff"
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, "TAKEOFF"))
        self._wave_group_started_at = time.monotonic()
        self._wp_require_takeoff(
            ids, f"WAVE กลุ่ม {group}",
            lambda g=group, members=list(ids): self._wave_begin_group_route(g, members)
            if self._wave_executing else None,
            lambda g=group: self._wave_abort(
                f"ยกเลิก TAKEOFF กลุ่ม {g}", clear_route=False),
            auto=bool(getattr(self, "_wave_auto", False)
                      or (getattr(self, "_wave_auto_next", False)
                          and self._wave_group_index > 0)))
        self._wp_render_status()

    def _wave_begin_group_route(self, group, ids):
        """เริ่ม GOTO ของกลุ่มหลังยืนยันว่าทุกลำพ้นพื้นแล้วเท่านั้น"""
        if not self._wave_executing:
            return
        self._select_group(group, additive=False)
        self._waypoint_route = self._wave_route_template
        self._waypoint_route.drone_ids = list(ids)
        self._wp_target_ids = list(self._selected_or_all())
        self._wp_current_index = 0
        self._wp_arrived = set()
        self._wp_sep_index = {}
        self._waypoint_executing = True
        self._wave_phase = "route"
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(
                    group, "ROUTE · WP 1/%d" % len(self._waypoint_route)))
        self._wave_group_started_at = time.monotonic()
        self._js(f"resetWaypointProgress({self._wp_key})")
        self._log(f"WAVE: เริ่มกลุ่ม {group} ({len(ids)} ลำ)", category="COMMAND")
        self._show_toast(f"WAVE · กลุ่ม {group} เริ่มบิน", "info")
        self._summ_set(
            "wave_progress", "WAVE PROGRESS",
            f"กลุ่ม {group} · TAKEOFF ครบ · เริ่ม ROUTE {len(self._waypoint_route)} จุด")
        self._refresh_flight_mode()
        self._wp_advance()
        self._wp_render_status()

    def _wave_route_finished(self):
        if not self._wave_executing:
            return
        group = self._wave_groups[self._wave_group_index]
        ids = list(self._wp_target_ids)
        self._waypoint_executing = False
        self._wave_phase = "waiting_land"
        # timeout ของช่วง RETURN ต้องเริ่มนับใหม่หลัง route จบ ไม่ใช่นับต่อ
        # จากตอนเริ่ม route (เส้นทางยาวเคยทำให้ return ถูก HOLD กลางคัน).
        self._wave_group_started_at = time.monotonic()
        if self._flight_run and self._flight_run.kind == "wave":
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, "RETURN + LAND"))
        self._log(f"WAVE: กลุ่ม {group} บินครบ · เริ่มกลับฐานและลงจอด",
                  category="COMMAND", severity="SUCCESS")
        self._staggered_rtl(ids)
        self._wp_render_status()

    def _wave_complete(self):
        self._wave_timer.stop()
        self._wave_executing = False
        self._waypoint_executing = False
        self._wave_phase = "idle"
        self._wave_auto = False
        self._wave_auto_next = False
        if hasattr(self, "chk_wave_auto_next"):
            self.chk_wave_auto_next.setEnabled(True)
        if self._flight_run and self._flight_run.kind == "wave":
            if self._wave_groups:
                self._flight_step_done(self._flight_run_id, "g%d_block" % self._wave_groups[-1], "COMPLETE")
            self._flight_step_active(self._flight_run_id, "wave_complete")
            self._flight_step_done(self._flight_run_id, "wave_complete", "All groups disarmed")
        self._js("clearAllWaypoints()")
        self._waypoint_route = None
        self._wp_routes = {}
        self._wave_route_template = None
        self.btn_wave_cancel.setVisible(False)
        self.lbl_wp_points.setText("ยังไม่มีจุด")
        self._set_fixed_label_text(self.lbl_wave_progress, "WAVE: เสร็จครบทุกกลุ่ม ✓")
        self._log("WAVE เสร็จสมบูรณ์ · ทุกกลุ่มลงจอดแล้ว ✓",
                  category="COMMAND", severity="SUCCESS")
        self._summ_set("wave_progress", "WAVE PROGRESS", "เสร็จครบทุกกลุ่ม ✓")
        self._summ_sync_waypoint()
        self._show_toast("WAVE เสร็จครบทุกกลุ่ม ✓", "ok")
        self._refresh_wave_group_choices()
        self._refresh_flight_mode()

    def _wp_execute(self, auto=False):
        """เริ่มบินตามเส้นทาง Waypoint ที่วางไว้ — ตรวจกันชนก่อนเสมอ

        `auto=True` = คำสั่งมาจากแท็บเล็ตซึ่งยืนยันครบมาแล้ว จึงห้ามเปิดกล่องถาม
        บนคอกพิต (คนถือแท็บเล็ตอยู่กลางสนาม ตอบกล่องบนคอมไม่ได้ คำสั่งจะค้างเงียบ)
        """
        if not self._wp_has_any_route():
            self._show_toast("ยังไม่ได้วางจุด Waypoint", "err")
            return
        if self._waypoint_executing or self._wave_executing or self._wp_takeoff_pending:
            self._show_toast("กำลังบินตามเส้นทางอยู่แล้ว", "info")
            return

        routes = {d: r for d, r in self._wp_all_routes().items()
                  if d in self.fleet_items}
        if not routes:
            self._show_toast("ไม่มีโดรนในเส้นทางนี้แล้ว (ถูกลบออกจากฝูงไปแล้ว)", "err")
            return
        if not self._guard():
            return

        if self._wave_enabled:
            self._wave_execute(auto=auto)
            return

        # ── กันชน: ตรวจเฉพาะโหมด SEPARATE ──
        # GROUPED ใช้เส้นทางเดียวร่วมกัน โดรนบินเป็นขบวนโดย group_goto_targets
        # กระจาย offset ให้อยู่แล้ว (ระยะห่างคงเดิม ไม่ทับกัน) + มีคิวกันชนตอนสั่ง
        # ถ้าเอาเส้นทางร่วมมาตรวจแบบคู่ ทุกคู่จะได้ระยะ 0 m (เส้นเดียวกัน) = บล็อกผิด
        if self._wp_separate:
            conflicts = self._wp_check_conflicts(routes)
            if conflicts:
                self._wp_block_on_conflicts(conflicts)
                return

        ids = sorted(routes.keys())
        # รายชื่อที่ต้องยืนยัน TAKEOFF ต้องคงเป็นสมาชิกเส้นทางทั้งหมด แม้โหมด Swarm
        # จะลดเป้าคำสั่ง Goto เหลือเฉพาะ Head ในขั้นถัดไป.
        takeoff_ids = list(ids)

        # ── โหมด Swarm: สั่งเฉพาะ "ลำแม่" (Head) — ตัวลูกเกาะขบวนเอง ──
        # core มี formation loop (tick ทุก 400ms) ที่ส่ง GotoYaw ให้ตัวลูกตามแม่อยู่แล้ว
        # ถ้า cockpit สั่ง Goto รายลำเข้าไปด้วย จะมีตัวคุม 2 ตัวแย่งกัน:
        # คำสั่งจาก cockpit ถูก loop ทับภายใน 400ms → ขบวนสะบัด เสียรูป ไปไม่ถึงจุด
        # สั่งแม่ลำเดียวแล้วปล่อยให้ loop ลากลูกตาม = คงรูปขบวนตลอดเส้นทาง
        swarm_head = 0
        if self._swarm_active:
            swarm_head = int(getattr(self, "_head_id", 0)
                             or getattr(self, "_leader_id", 0) or 0)
            if not swarm_head:
                self._show_toast("โหมด Swarm — ยังไม่มีลำแม่ (Head)", "err")
                self._log("Waypoint EXECUTE ยกเลิก — โหมด Swarm แต่ยังไม่มีลำแม่",
                          category="COMMAND", severity="WARNING")
                return
            if swarm_head not in self.fleet_items:
                self._show_toast(f"ลำแม่ (Drone {swarm_head}) ไม่ได้เชื่อมต่อ", "err")
                self._log(f"Waypoint EXECUTE ยกเลิก — ลำแม่ Drone {swarm_head} หลุด",
                          category="COMMAND", severity="WARNING")
                return
            ids = [swarm_head]

        if not self._wp_confirm_actions(routes, auto=auto):
            self._show_toast("ยกเลิก EXECUTE · ยังไม่ปล่อยของ", "info")
            self._summ_event(
                "ยกเลิก", "Waypoint EXECUTE · ไม่ยืนยันการทำงาน A/B",
                cancelled=True)
            return

        # Under a configured Core-authority token, prove the Core mission slot
        # is idle *before* auto-TAKEOFF or any other mission-side flight prep.
        # _wp_begin_execute checks again before legacy GOTO to close the later
        # handoff window; eligible Core plans still perform authoritative Start
        # only after airborne readiness is confirmed.
        if not mission_shadow.authority_slot_idle(self):
            self._log("Waypoint EXECUTE ถูกบล็อกก่อน TAKEOFF — Core mission slot ไม่ยืนยันว่าว่าง",
                      category="COMMAND", severity="ERROR")
            self._show_toast("ไม่เริ่ม Waypoint · Core mission ยัง active/ไม่ทราบสถานะ", "err")
            self._wp_render_status("Blocked before TAKEOFF · Core mission slot not idle")
            return

        context = ("Waypoint SEPARATE" if self._wp_separate
                   else "Waypoint GROUPED")
        has_actions = bool(self._wp_action_points(routes))
        run_id = self._flight_run_start(
            "waypoint", auto_takeoff=bool(self._wp_grounded_ids(takeoff_ids)),
            has_actions=has_actions)
        self._wp_flow_run_id = run_id
        self._wp_require_takeoff(
            takeoff_ids, context,
            lambda planned=dict(routes), targets=list(ids), head=swarm_head, rid=run_id:
            self._wp_begin_execute(planned, targets, head, rid), auto=auto)
        return

    def _wp_begin_execute(self, routes, ids, swarm_head=0, flow_run_id=None):
        """เริ่มเส้นทางจริงหลังด่าน TAKEOFF ยืนยันว่าทุกลำพ้นพื้นแล้ว"""
        if self._waypoint_executing or self._wave_executing:
            return

        self._waypoint_executing = True
        self._wp_target_ids = ids
        self._wp_arrived = set()
        self._wp_current_index = 0
        self._wp_sep_index = {d: 0 for d in ids}
        if self._waypoint_mode:            # ปิดโหมดวางจุด กันเผลอเพิ่มจุดระหว่างบิน
            self._wp_toggle(False)
        self._refresh_flight_mode()
        if flow_run_id is not None:
            self._flight_step_active(flow_run_id, "fly_waypoint", "WP 1/%d" % (
                len(self._waypoint_route) if self._waypoint_route else 0))

        core_expected = mission_shadow.authority_eligible(
            self, routes, ids, swarm_head)
        core_authority = mission_shadow.start(
            self, routes, ids, swarm_head, flow_run_id)
        legacy_blocked = bool(getattr(self, "_mission_legacy_ownership_blocked", False))
        if legacy_blocked:
            # The plan itself belongs to the legacy executor, but Core could not
            # prove the authority slot is idle (or reports an active Core run).
            # Never overlap two mission owners.
            self._mission_core_authority = False
            self._waypoint_executing = False
            if self._flight_run and self._flight_run.kind == "waypoint":
                self._flight_run_cancel("Core mission slot is not confirmed idle")
            self._log("Waypoint EXECUTE ถูกบล็อก — ยังยืนยันไม่ได้ว่า Core ไม่มี mission ค้าง",
                      category="COMMAND", severity="ERROR")
            self._show_toast("ไม่เริ่ม Waypoint · Core mission slot ยังไม่ว่าง", "err")
            self._wp_render_status("Core mission slot ยังไม่ว่าง")
            self._refresh_flight_mode()
            return
        if core_expected and not core_authority:
            # Fail closed only for a plan that belongs to the enabled Core scope:
            # the Start reply may be ambiguous and Core may already own it.
            # Plans outside that scope never call authoritative StartMission and
            # deliberately retain the proven legacy Python executor.
            self._mission_core_authority = False
            self._waypoint_executing = False
            if self._flight_run and self._flight_run.kind == "waypoint":
                self._flight_run_cancel("Core waypoint authority not confirmed")
            self._log("Waypoint EXECUTE ถูกบล็อก — Core authority ไม่ยืนยัน (no fallback GOTO)",
                      category="COMMAND", severity="ERROR")
            self._show_toast("ไม่เริ่ม Waypoint · Core authority ไม่ยืนยัน", "err")
            self._wp_render_status("Core authority ไม่ยืนยัน")
            self._refresh_flight_mode()
            return

        if self._wp_separate:
            total = sum(len(r) for r in routes.values())
            self._log(f"Waypoint EXECUTE [SEPARATE] {len(ids)} ลำ · {total} จุดรวม "
                      f"— แต่ละลำบินอิสระ", category="COMMAND")
            self._show_toast(f"เริ่มบินแยกลำ · {len(ids)} เส้นทาง", "info")
            for d in ids:
                self._wp_advance_one(d)
        else:
            n = len(self._waypoint_route)
            if swarm_head:
                kind = f"Swarm · Leader Path ของ Drone {swarm_head} (ลูกเกาะขบวนตาม)"
            elif len(ids) == 1:
                kind = "โดรนเดี่ยว"
            else:
                kind = f"GROUPED {len(ids)} ลำ"
            self._log(f"Waypoint EXECUTE — {n} จุด [{kind}]", category="COMMAND")
            if swarm_head:
                self._show_toast(
                    f"เริ่มบินตามเส้นทาง · {n} จุด · นำโดย Drone {swarm_head}", "info")
            else:
                self._show_toast(f"เริ่มบินตามเส้นทาง · {n} จุด", "info")
            if not core_authority:
                self._wp_advance()
            else:
                self._log("Waypoint authority = Go Core · Python GOTO disabled",
                          category="COMMAND")
        self._wp_render_status()

    # ── SEPARATE: แต่ละลำเดินเส้นทางของตัวเองอิสระ ──
    def _wp_advance_one(self, did):
        if not self._waypoint_executing:
            return
        route = self._wp_routes.get(did)
        idx = self._wp_sep_index.get(did, 0)
        if route is None or idx >= len(route):
            self._wp_finish_one(did)
            return
        wp = route.points[idx]
        if self._flight_run and self._flight_run.kind == "waypoint":
            self._flight_step_active(self._flight_run_id, "fly_waypoint",
                                     "D%d · WP %d/%d" % (did, idx + 1, len(route)))
        self._js(f"highlightWaypoint({did}, {wp.index})")
        alt = self._last_alt.get(did) or self._alt_for(did)
        self._goto_one(did, wp.lat, wp.lon, alt)
        self._js(f"setTarget({did},{wp.lat:.7f},{wp.lon:.7f},{drone_color(did)!r})")
        self._wp_render_status()

    def _wp_finish_one(self, did):
        """ลำนี้บินครบเส้นทางของตัวเองแล้ว — ลำอื่นบินต่อได้"""
        self._js(f"clearWaypoints({did})")
        self._log(f"Waypoint: Drone {did} บินครบเส้นทางแล้ว ✓",
                  vehicle=f"Drone {did}", category="COMMAND", severity="SUCCESS")
        done = all(self._wp_sep_index.get(d, 0) >= len(self._wp_routes.get(d, []))
                   for d in self._wp_target_ids)
        if done:
            self._wp_finish()
        else:
            self._wp_render_status()

    # ── GROUPED: ทุกลำไปจุดเดียวกันพร้อมกัน แล้วรอครบก่อนไปจุดถัดไป ──
    def _wp_advance(self):
        """สั่งบินไปยัง waypoint จุดถัดไป (เรียกซ้ำจนครบทุกจุด)"""
        if not self._waypoint_executing:
            return
        if bool(getattr(self, "_mission_core_authority", False)):
            return  # Core telemetry/mission engine owns progression + GOTO
        route = self._waypoint_route
        if route is None or self._wp_current_index >= len(route):
            self._wp_finish()
            return

        wp = route.points[self._wp_current_index]
        if self._flight_run and self._flight_run.kind == "waypoint":
            self._flight_step_active(self._flight_run_id, "fly_waypoint",
                                     "WP %d/%d · %s" % (self._wp_current_index + 1, len(route),
                                                         ",".join("D%d" % d for d in self._wp_target_ids)))
        elif (self._flight_run and self._flight_run.kind == "wave"
              and 0 <= self._wave_group_index < len(self._wave_groups)):
            group = self._wave_groups[self._wave_group_index]
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(
                    group, "ROUTE · WP %d/%d" % (self._wp_current_index + 1, len(route))))
        self._js(f"highlightWaypoint({self._wp_key}, {wp.index})")
        self._wp_arrived = set()
        self._wp_render_status()
        ids = self._wp_target_ids

        if len(ids) == 1:
            did = ids[0]
            alt = self._last_alt.get(did) or self._alt_for(did)
            self._goto_one(did, wp.lat, wp.lon, alt)
            self._js(f"setTarget({did},{wp.lat:.7f},{wp.lon:.7f},"
                     f"{drone_color(did)!r})")
        else:
            # Swarm/Grouped — คงรูปขบวน/ระยะห่างไว้ทุกจุดตลอดเส้นทาง
            pos = {d: p for d, p in self._fleet_positions().items() if d in ids}
            latlon = {d: (p[1], p[0]) for d, p in pos.items()}   # (lon,lat)→(lat,lon)
            wp_targets, order = waypoint_logic.grouped_dispatch_plan(
                ids, latlon, wp.lat, wp.lon)
            generation = self._wave_generation
            for i, did in enumerate(order):
                tlat, tlon = wp_targets[did]
                alt = self._last_alt.get(did) or self._alt_for(did)
                QTimer.singleShot(
                    i * 150,
                    lambda d=did, la=tlat, lo=tlon, a=alt, g=generation:
                    self._goto_one(d, la, lo, a)
                    if waypoint_logic.grouped_dispatch_generation_valid(
                        self._waypoint_executing, self._wave_executing,
                        g, self._wave_generation)
                    else None)
                self._js(f"setTarget({did},{tlat:.7f},{tlon:.7f},"
                         f"{drone_color(did)!r})")

    def _wp_finish(self):
        """จบเส้นทาง Waypoint — ทุกจุดผ่านหมดแล้ว

        รีเซ็ตทั้ง state และล้างจุด/เส้นบนแผนที่ (ระบบคุมได้ทีละ 1 เส้นทาง) —
        กันบั๊ก: ถ้าไม่รีเซ็ต route object ค้างไว้ การพล็อตครั้งถัดไปจะไปต่อจุด
        บนเส้นทางเดิมที่จบไปแล้ว (drone_ids/key เก่าไม่ตรงกับที่เลือกใหม่)
        """
        # เส้นทางจบ (หรือกลุ่ม WAVE จบ) → WAIT ที่ค้างถือว่าหมดอายุ
        self._wp_wait_invalidate()
        if self._wave_executing and self._wave_phase == "route":
            self._wave_route_finished()
            return
        if self._flight_run and self._flight_run.kind == "waypoint":
            self._flight_step_done(self._flight_run_id, "fly_waypoint", "All waypoints reached")
            if any(step.id == "waypoint_action" for step in self._flight_run.steps):
                action = self._flight_run.step("waypoint_action")
                if action.status == flight_progress.StepStatus.PENDING:
                    self._flight_run.skip("waypoint_action", "No action at final waypoint")
            self._flight_step_active(self._flight_run_id, "route_complete")
            self._flight_step_done(self._flight_run_id, "route_complete", "Route complete")
        self._waypoint_executing = False
        self._wp_current_index = 0
        self._wp_arrived = set()
        self._wp_sep_index = {}
        self._js("clearAllWaypoints()")
        self._waypoint_route = None
        self._wp_routes = {}
        self.lbl_wp_points.setText("ยังไม่มีจุด")
        self._log("Waypoint: เส้นทางเสร็จสมบูรณ์ ✓", category="COMMAND", severity="SUCCESS")
        self._summ_sync_waypoint()
        self._show_toast("เส้นทาง Waypoint เสร็จสมบูรณ์ ✓", "ok")
        self._wp_render_status("เสร็จสมบูรณ์ ✓")
        self._refresh_flight_mode()

    # ══════════════════════════════════════════════════════════
    #  WAIT ราย Waypoint — HOLD/รอ ที่ waypoint แบบ cancellable (spec §6)
    #  ลำดับที่ waypoint หนึ่งจุด: ARRIVE → HOLD/WAIT → action A/B → advance
    #  Timer เป็นแค่ตัวเรียกตรวจ state — ห้ามผูก business decision กับ timer/sleep
    # ══════════════════════════════════════════════════════════
    def _wp_on_arrived(self, scope_key, wp, ids, advance_cb):
        """ถึง waypoint แล้ว — ถ้ามี WAIT ให้ HOLD/รอก่อน จบแล้วค่อยทำ action/ไปต่อ

        คงพฤติกรรม A/B เดิมทุกอย่าง: ไม่มี WAIT = เดินเส้นทางเดิมเป๊ะ
        """
        def after_wait():
            if wp.action:
                self._wp_run_action(ids, wp, advance_cb)
            else:
                QTimer.singleShot(500, advance_cb)
        if wp.wait_seconds > 0:
            self._wp_wait_begin(scope_key, wp, ids, after_wait)
        else:
            after_wait()

    def _wp_wait_begin(self, scope_key, wp, ids, on_done):
        """เริ่ม HOLD/WAIT ที่ waypoint — เก็บ run/generation guard ครบ (spec §6)"""
        seconds = int(wp.wait_seconds)
        ids = [int(d) for d in ids]
        entry = {
            "gen": self._wp_wait_generation,
            "run_id": self._flight_run_id,
            "wave_gen": self._wave_generation,
            "index": int(wp.index),
            "deadline": self._wp_clock() + seconds,
            "total": seconds,
            "scope_ids": ids,
            "on_done": on_done,
        }
        self._wp_waits[scope_key] = entry
        # HOLD ตำแหน่งไว้ระหว่างรอ (ยิงครั้งเดียวตอนเริ่ม — ไม่ยิงซ้ำทับ failsafe)
        self._wp_wait_hold(ids)
        self._log(f"Waypoint #{wp.index + 1}: HOLD/WAIT {wp.wait_minutes} นาที "
                  f"({','.join('D%d' % d for d in ids)})",
                  category="COMMAND", severity="WARNING")
        if not self._wp_wait_timer.isActive():
            self._wp_wait_timer.start()
        self._wp_wait_render()

    def _wp_wait_hold(self, ids):
        ids = [int(d) for d in ids]
        if not ids:
            return

        def worker():
            self._safe(lambda: self._dispatch_core(
                "HOLD [waypoint-wait]", lambda: self.client.hold(ids),
                source="waypoint-wait", targets=ids, enforce_dedup=False))
        threading.Thread(target=worker, daemon=True).start()

    def _wp_wait_valid(self, entry):
        """WAIT ยังใช้ได้อยู่ไหม — generation/run guard ป้องกัน callback เก่า"""
        if entry.get("gen") != self._wp_wait_generation:
            return False
        if not self._waypoint_executing:
            return False
        if self._wave_executing and entry.get("wave_gen") != self._wave_generation:
            return False
        if entry.get("run_id") != self._flight_run_id:
            return False
        return True

    def _wp_wait_invalidate(self):
        """ยกเลิก WAIT ที่ค้างอยู่ทั้งหมด — callback เก่าห้ามกลับมาสั่ง waypoint ถัดไป

        เรียกจาก Cancel Nav / E-STOP / WAVE cancel-timeout / abort / mission ใหม่ /
        battery-link failsafe. bump generation = entry เก่าทุกตัวถือเป็น invalid ทันที
        """
        self._wp_wait_generation += 1
        self._wp_waits = {}
        if hasattr(self, "_wp_wait_timer"):
            self._wp_wait_timer.stop()

    def _wp_wait_remaining(self):
        """วินาทีที่เหลือของ WAIT ที่นานสุด (สำหรับ UI) — None ถ้าไม่มี WAIT ค้าง"""
        if not self._wp_waits:
            return None
        now = self._wp_clock()
        rem = [max(0.0, e["deadline"] - now) for e in self._wp_waits.values()]
        return max(rem) if rem else None

    def _wp_wait_tick(self):
        """ตัวเรียกตรวจ WAIT ทุก 500ms — timer ไม่ใช่ authority ของ state"""
        if bool(getattr(self, "_mission_core_authority", False)):
            # F5/F6: Core owns WAIT deadline and progression.  A stale Python
            # timer may refresh presentation only; it must never run on_done.
            self._wp_waits = {}
            self._wp_wait_timer.stop()
            self._wp_render_status()
            return
        if not self._wp_waits:
            self._wp_wait_timer.stop()
            return
        now = self._wp_clock()
        for key in list(self._wp_waits.keys()):
            entry = self._wp_waits.get(key)
            if entry is None:
                continue
            if not self._wp_wait_valid(entry):
                # run/generation เปลี่ยน = ถูกยกเลิก/failsafe → ทิ้งเงียบ ไม่ยิง on_done
                self._wp_waits.pop(key, None)
                continue
            if now >= entry["deadline"]:
                self._wp_waits.pop(key, None)
                self._log(f"Waypoint #{entry['index'] + 1}: WAIT ครบเวลา — ทำงานต่อ",
                          category="COMMAND")
                entry["on_done"]()
        if not self._wp_waits:
            self._wp_wait_timer.stop()
        else:
            self._wp_wait_render()

    def _wp_wait_render(self):
        """แสดง countdown ที่ status/summary — เป็นแค่การแสดงผล ไม่ใช่ safety authority"""
        rem = self._wp_wait_remaining()
        if rem is None:
            return
        mm, ss = divmod(int(rem + 0.999), 60)
        entry = max(self._wp_waits.values(), key=lambda e: e["deadline"])
        text = f"WAITING · WP {entry['index'] + 1} · {mm:02d}:{ss:02d} remaining"
        if hasattr(self, "lbl_wp_status"):
            self.lbl_wp_status.setText(text)
        if self._flight_run and self._flight_run.kind == "waypoint":
            self._flight_step_active(self._flight_run_id, "fly_waypoint", text)
        elif (self._flight_run and self._flight_run.kind == "wave"
              and 0 <= self._wave_group_index < len(self._wave_groups)):
            group = self._wave_groups[self._wave_group_index]
            self._flight_step_active(
                self._flight_run_id, "g%d_block" % group,
                self._wave_group_detail(group, text))

    # ══════════════════════════════════════════════════════════
    #  FIELD TABLET — แท็บเล็ตในวง LAN เปิดดูได้ (docs/FIELD_TABLET.md เฟส 1)
    #  เฟสนี้ **ดูอย่างเดียว** ไม่มีทางสั่งการโดรนจากเว็บเลย
    # ══════════════════════════════════════════════════════════
    def _field_push(self, t, display_name):
        """แปลง telemetry เป็น dict ล้วนแล้วส่งเข้า hub

        ต้องเป็น dict — ห้ามส่ง protobuf/Qt object ข้ามไปให้ HTTP thread อ่าน
        สีดึงจาก drone_color() ตัวเดียวกับที่ desktop ใช้ แท็บเล็ตจะได้เห็นสี
        ตรงกับที่ผู้ใช้ตั้งไว้เอง
        """
        did = int(t.drone_id)
        p = getattr(t, "position", None)
        lat = float(getattr(p, "lat", 0.0) or 0.0)
        lon = float(getattr(p, "lon", 0.0) or 0.0)
        self._field_track_point(did, lat, lon)
        self._field_update_goto_target(did, lat, lon)
        self.field_hub.update(did, {
            "name":  display_name,
            "lat":   lat,
            "lon":   lon,
            "alt":   float(getattr(p, "alt_rel", 0.0) or 0.0),
            "hdg":   float(getattr(t, "heading", 0.0) or 0.0),
            "spd":   float(getattr(t, "ground_speed", 0.0) or 0.0),
            "batt":  float(getattr(t, "battery_pct", 0.0) or 0.0),
            "volt":  float(getattr(t, "voltage", 0.0) or 0.0),
            "sats":  int(getattr(t, "sat_count", 0) or 0),
            "fix":   int(getattr(t, "gps_fix", 0) or 0),
            "armed": bool(getattr(t, "armed", False)),
            "mode":  rpc.mode_name(getattr(t, "mode", 0)),
            "link":  rpc.status_name(getattr(t, "status", 0)),
            # datalink: ส่งค่าจริงเท่านั้น ไม่มีวิทยุรายงาน = บอกว่าไม่มี
            # (เคยมีบั๊กโชว์ "ลิงก์เต็ม" ปลอม ๆ บนจอที่ใช้ตัดสินใจบิน)
            "rssi":  int(getattr(t, "rssi", 0) or 0),
            "rssi_ok": bool(getattr(t, "rssi_valid", False)),
            "linkq": int(getattr(t, "link_quality", 0) or 0),
            "color": drone_color(did),
            "head":  int(did) == int(getattr(self, "_head_id", 0) or 0),
            # กลุ่มของลำนั้น — แถบโดรนบนแท็บเล็ตโชว์ "กลุ่ม N · MODE"
            # ทรงเดียวกับการ์ด #fleet ใน map3d.html
            "group": int(self.group_of.get(did, 0) or 0),
        })
        # ลำที่เลือกบนคอกพิต — แท็บเล็ตต้องเห็นตรงกัน ไม่งั้นกดสั่งแล้วไปลงผิดลำ
        # set_extra ขยับ seq เฉพาะตอนค่าเปลี่ยนจริง เรียกถี่ได้ไม่เปลือง
        self.field_hub.set_extra("selected", self._selected_or_all())
        self._field_push_state()

    def _field_track_point(self, drone_id, lat, lon):
        """เก็บรอยการเดินทางแบบเบาบาง; ขยับน้อยกว่า 2 ม. ไม่ต้องเพิ่มจุดใหม่."""
        if not (lat or lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return
        did = int(drone_id)
        points = self._field_trails.setdefault(did, [])
        if points:
            prev_lat, prev_lon = points[-1]
            # ระยะใกล้เคียงพอสำหรับคัดจุด telemetry ที่เด้งอยู่กับที่
            dy = (lat - prev_lat) * 111_320.0
            dx = (lon - prev_lon) * 111_320.0 * math.cos(math.radians(lat))
            if dx * dx + dy * dy < 4.0:
                return
        points.append([round(lat, 7), round(lon, 7)])
        if len(points) > 180:
            del points[:-180]

    def _field_selected_trails(self):
        """ส่งเฉพาะรอยของลำที่กำลังเลือก เพื่อให้แผนที่ยังลื่นเมื่อมีหลายลำ."""
        return [{"id": int(did), "color": drone_color(did),
                 "pts": self._field_trails.get(int(did), [])}
                for did in self._selected_or_all()
                if len(self._field_trails.get(int(did), [])) >= 2]

    def _field_update_goto_target(self, drone_id, lat, lon):
        """ลบเป้า GOTO เมื่อ telemetry รายงานว่าโดรนถึงระยะ 3 เมตรแล้ว."""
        target = self._field_goto_targets.get(int(drone_id))
        if target is None or not (lat or lon):
            return
        dy = (lat - target["lat"]) * 111_320.0
        dx = (lon - target["lon"]) * 111_320.0 * math.cos(math.radians(lat))
        if dx * dx + dy * dy <= 9.0:
            self._field_goto_targets.pop(int(drone_id), None)

    def _field_selected_goto_targets(self):
        return [{"id": int(did), "lat": target["lat"], "lon": target["lon"],
                 "color": drone_color(did)}
                for did, target in sorted(self._field_goto_targets.items())
                if int(did) in self._selected_or_all()]

    def _field_push_state(self):
        """สถานะระดับฝูงที่หน้าแท็บเล็ตใช้วาด (docs/FIELD_TABLET_V2.md §3.2)

        เรียกถี่ได้ — `set_extra` ขยับ seq เฉพาะตอนค่าเปลี่ยนจริง ส่วนเส้นทาง
        Waypoint สร้างใหม่เฉพาะตอนลายเซ็นเปลี่ยน (จุดไม่ขยับเองหลังวางแล้ว)
        """
        hub = self.field_hub
        hub.set_extra("ui_mode", bool(getattr(self, "_ui_mode", True)))
        hub.set_extra("wp_mode", bool(getattr(self, "_waypoint_mode", False)))
        hub.set_extra("wp_separate", bool(getattr(self, "_wp_separate", False)))
        hub.set_extra("wp_busy", bool(getattr(self, "_waypoint_executing", False)
                                      or getattr(self, "_wave_executing", False)))
        hub.set_extra("wave", bool(getattr(self, "_wave_enabled", False)))
        hub.set_extra("wave_busy", bool(getattr(self, "_wave_executing", False)))
        hub.set_extra("wave_avail", self._wave_available_groups())
        hub.set_extra("wave_groups", (self._wave_selected_groups()
                                      if getattr(self, "_wave_enabled", False) else []))
        hub.set_extra("wave_progress", self._wave_progress_text())
        auto_next = (bool(getattr(self, "_wave_auto_next", False))
                     if getattr(self, "_wave_executing", False)
                     else bool(hasattr(self, "chk_wave_auto_next")
                               and self.chk_wave_auto_next.isChecked()))
        hub.set_extra("wave_auto_next", auto_next)
        groups = set(self._wave_payload_done) | set(self._wave_payload_failed)
        hub.set_extra("wave_payload_status", {
            str(group): self._wave_payload_summary(group) for group in sorted(groups)
        })
        hub.set_extra("head", int(getattr(self, "_head_id", 0) or 0))
        if hasattr(self, "sf_speed"):
            hub.set_extra("speed", round(float(self.sf_speed.value()), 1))
        fid = 0
        if hasattr(self, "form_picker"):
            fid = int(self.form_picker.current())
        hub.set_extra("swarm", {
            "active": bool(getattr(self, "_swarm_active", False)),
            "formation": fid,
            "name": FORMATION_NAMES.get(fid, str(fid)),
            "spacing": (round(float(self.sf_spacing.value()), 0)
                        if hasattr(self, "sf_spacing") else 0.0),
        })
        hub.set_extra("routes", self._field_routes())
        hub.set_extra("trails", self._field_selected_trails())
        hub.set_extra("goto_targets", self._field_selected_goto_targets())
        # ข้อมูลที่แท็บเล็ตใช้ "ถามแทนคอกพิต" ก่อนกด EXECUTE — ลำไหนยังไม่บิน
        # และเส้นทางมีจุดปล่อยของกี่จุด (ดู `_web_wp_execute`)
        # WAVE: เป้าคือสมาชิกกลุ่มที่ร่วมเวฟ ไม่ใช่แค่ลำที่อยู่ในเส้นทางร่วม
        routes = self._wp_all_routes()
        if getattr(self, "_wave_enabled", False):
            ids = self._wave_member_ids()
        else:
            ids = sorted(d for d in routes if d in self.fleet_items)
        hub.set_extra("wp_targets", ids)
        hub.set_extra("wp_grounded", self._wp_grounded_ids(ids) if ids else [])
        hub.set_extra("wp_actions", len(self._wp_action_points(routes)) if routes else 0)
        hub.set_extra("preflight_ok", bool(self._preflight.ready())
                      if hasattr(self, "_preflight") else True)

    def _field_routes(self):
        """เส้นทาง Waypoint ในรูปแบบ dict ล้วน ให้แท็บเล็ตวาดเส้นเองได้

        แคชด้วยลายเซ็น (โหมด + จำนวนจุดต่อเส้น + จำนวนจุดที่มี action) เพราะ
        เมธอดนี้ถูกเรียกทุกแพ็กเก็ต telemetry (5 ลำ × 10Hz) แต่จุดจะเปลี่ยน
        เฉพาะตอนคนวาง/ลบจุดเท่านั้น — สร้างลิสต์ใหม่ทุกครั้งคือเผาซีพียูเปล่า
        """
        routes = self._wp_all_routes()
        sig = (bool(getattr(self, "_wp_separate", False)),
               tuple(sorted((d, len(r), sum(1 for wp in r.points if wp.action))
                            for d, r in routes.items())))
        if sig == getattr(self, "_field_routes_sig", None):
            return self._field_routes_cache
        out, seen = [], set()
        for did in sorted(routes):
            route = routes[did]
            # GROUPED = ทุกลำชี้ WaypointRoute ก้อนเดียวกัน — ส่งซ้ำไม่ได้
            if id(route) in seen:
                continue
            seen.add(id(route))
            out.append({
                "key": int(did),
                "color": drone_color(did),
                "pts": [[round(wp.lat, 7), round(wp.lon, 7)] for wp in route.points],
                "act": [i for i, wp in enumerate(route.points) if wp.action],
            })
        self._field_routes_sig = sig
        self._field_routes_cache = out
        return out

    # ── คำสั่งจากแท็บเล็ต (เฟส 2) ──
    def _on_web_command(self, session, action, params):
        """รับคำสั่งจากแท็บเล็ต — ถึงตรงนี้แล้วอยู่บน Qt main thread เสมอ
        (มาทาง WebBridge.command signal ซึ่ง Qt แปลงเป็น queued connection ให้)

        field_server ตรวจไปแล้วว่า: จับคู่แล้ว, action อยู่ใน allowlist,
        ถือสิทธิ์อยู่ (ถ้าไม่ใช่ HOLD/E-STOP) และผ่าน rate limit
        ตรงนี้จึงเหลือหน้าที่เดียว — พาไปลงเมธอดเดิมที่ปุ่มบน desktop เรียก
        เพื่อให้ได้ safety envelope + audit เส้นเดียวกัน ห้ามยิง client ตรง
        ยกเว้นผ่าน `_run_cmd` ซึ่งวิ่งผ่าน `_target_ids()` → `_selected_or_all()`
        """
        tag = (session or "")[:8]
        try:
            ok, msg = self._dispatch_web(action, params or {})
        except Exception as e:                     # ห้ามให้ข้อผิดพลาดฆ่า Qt loop
            ok, msg = False, "ผิดพลาด: %s" % e
        self._log("tablet[%s] %s → %s%s" % (tag, action, "ok" if ok else "ปฏิเสธ",
                                            (" · " + msg) if msg else ""),
                  category="COMMAND", severity="INFO" if ok else "WARNING")
        return ok, msg

    def _dispatch_web(self, action, p):
        # ── กดหยุด: ทุกเครื่องทำได้เสมอ ไม่ต้องถือสิทธิ์ ──
        if action == "estop":
            ids = self._connected_ids()
            if not ids:
                return False, "ไม่มีลำที่เชื่อมต่ออยู่"
            self._do_estop(ids, "ALL (%d ลำ) [tablet]" % len(ids))
            return True, "E-STOP ทุกลำ"
        if action == "hold":
            self._cmd_hold()
            return True, "HOLD"
        if action == "hold_all":
            return self._web_hold_all()

        # ── ที่เหลือเป็นคำสั่งของผู้ถือสิทธิ์ ──
        if action == "select":
            return self._web_select(p)
        if action == "select_group":
            return self._web_select_group(p)
        if action == "arm":
            self._cmd_arm()
            return True, "ARM"
        if action == "land":
            self._cmd_land()
            return True, "LAND"
        if action == "rtl":
            self._cmd_rtl()
            return True, "RTL"
        if action == "takeoff":
            return self._web_takeoff(p)
        if action == "set_mode":
            return self._web_set_mode(p)
        if action == "goto":
            return self._web_goto(p)
        if action == "servo":
            return self._web_servo(p)
        if action == "waypoint_execute":
            return self._web_wp_execute(p)

        # ── เฟส 3: Waypoint / Swarm / Movement (docs/FIELD_TABLET_V2.md) ──
        if action == "waypoint_mode":
            return self._web_wp_mode(p)
        if action == "waypoint_add":
            return self._web_wp_add(p)
        if action == "waypoint_undo":
            if self._wp_busy():
                return False, "กำลัง EXECUTE อยู่ — แก้เส้นทางไม่ได้"
            if not self._wp_has_any_route():
                return False, "ยังไม่มีจุดให้ลบ"
            self._wp_undo()
            return True, "ลบจุดล่าสุดแล้ว"
        if action == "waypoint_clear":
            return self._web_wp_clear(p)
        if action == "swarm_start":
            return self._web_swarm_start(p)
        if action == "swarm_stop":
            self._swarm_stop()
            return True, "SWARM STOP"
        if action == "swarm_return":
            if not p.get("confirmed"):
                return False, "ต้องยืนยันบนแท็บเล็ตก่อน"
            if not self._guard():
                return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
            self._swarm_return()
            return True, "RETURN ทั้งฝูง"
        if action == "formation":
            return self._web_formation(p)
        if action == "speed":
            return self._web_speed(p)
        if action == "move":
            return self._web_move(p)
        if action == "move_stop":
            return self._web_move_stop()
        if action == "wave":
            return self._web_wave(p)
        if action == "wave_groups":
            return self._web_wave_groups(p)
        if action == "wave_auto_next":
            return self._web_wave_auto_next(p)
        if action == "wave_cancel":
            return self._web_wave_cancel()
        return False, "ไม่รู้จักคำสั่งนี้"

    def _web_hold_all(self):
        """หยุดแผน/ขบวน/บังคับสด แล้วให้ทุกลำค้างตำแหน่ง โดยไม่ตัดมอเตอร์.

        ปุ่มนี้แทน E-STOP บน Field Web: ใช้ StopAll ของ core ซึ่งส่ง zero velocity
        และเข้า GUIDED hold ที่ความสูงปัจจุบัน ไม่ใช่ Kill หรือ Disarm.
        """
        ids = self._connected_ids()
        if not ids:
            return False, "ไม่มีโดรนที่เชื่อมต่ออยู่"
        self._web_move_timer.stop()
        self._rc_dir = None
        self._rc_order_dir = None
        self._abort_rtl()
        self._abort_waypoint_execution()
        self._summ_event("หยุดทุกคำสั่ง", "HOLD ALL · %d ลำ" % len(ids), cancelled=True)
        self._log("HOLD ALL [tablet] → %s" % ",".join(map(str, ids)),
                  category="COMMAND", severity="WARNING")
        self._show_toast("HOLD ALL · กำลังให้ %d ลำลอยนิ่ง" % len(ids), "info")

        def worker():
            try:
                web_ids = [int(d) for d in ids]
                r = self._dispatch_core(
                    "STOP ALL [tablet]", lambda: self.client.stop_all(web_ids),
                    source="tablet", targets=web_ids, enforce_dedup=False)
                self.cmd_result.emit("HOLD ALL: ok=%s %s" % (
                    getattr(r, "ok", True), getattr(r, "message", "")))
            except Exception as e:
                self.cmd_result.emit("HOLD ALL: ERROR %s" % e)
        threading.Thread(target=worker, daemon=True).start()
        return True, "กำลังหยุดทุกคำสั่งและ HOLD %d ลำ" % len(ids)

    # ── ตัวช่วยของคำสั่งเฟส 3 ──
    def _wp_busy(self):
        return bool(getattr(self, "_waypoint_executing", False)
                    or getattr(self, "_wave_executing", False))

    def _web_wp_mode(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        on = bool(p.get("on"))
        if self._wp_busy():
            return False, "กำลัง EXECUTE อยู่ — สลับโหมดไม่ได้"
        self._wp_toggle(on)
        self._field_push_state()
        return True, ("เปิดโหมดวางจุด" if on else "ปิดโหมดวางจุด")

    def _web_wp_add(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        # ไม่แอบเปิดโหมดให้เอง — คอกพิตกับแท็บเล็ตต้องเห็นสถานะตรงกันเสมอ
        # (แอบเปิด = คนที่ยืนอยู่หน้าคอมไม่รู้ว่าจอตัวเองเปลี่ยนโหมดไปแล้ว)
        if not getattr(self, "_waypoint_mode", False):
            return False, "เปิดโหมดวางจุดก่อน"
        if self._wp_busy():
            return False, "กำลัง EXECUTE อยู่ — วางจุดเพิ่มไม่ได้"
        try:
            lat, lon = float(p.get("lat")), float(p.get("lon"))
        except (TypeError, ValueError):
            return False, "พิกัดไม่ถูกต้อง"
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) \
                or (lat == 0 and lon == 0):
            return False, "พิกัดไม่ถูกต้อง"
        before = sum(len(r) for r in self._wp_all_routes().values())
        self._on_waypoint_click(lat, lon)
        after = sum(len(r) for r in self._wp_all_routes().values())
        self._field_push_state()
        if after <= before:
            # _on_waypoint_click ปฏิเสธเอง (ยังไม่เลือกลำ / swarm ไม่มีหัวขบวน)
            # ข้อความจริงขึ้น toast ที่คอกพิตแล้ว — ตรงนี้บอกกลางๆ ไม่เดาเหตุผล
            return False, "วางจุดไม่สำเร็จ — ดูข้อความที่คอมควบคุม"
        return True, "วางจุดที่ %d" % after

    def _web_wp_execute(self, p):
        """EXECUTE ROUTE จากแท็บเล็ต — **ห้ามเปิดกล่องถามค้างบนคอกพิต**

        ของเดิมเรียก `_wp_execute()` ตรง ๆ ซึ่งเปิดกล่อง "ต้อง TAKEOFF ก่อน" /
        "ยืนยันการปล่อยของ" บนคอกพิต คนถือแท็บเล็ตกลางสนามตอบไม่ได้ และบนแท็บเล็ต
        ก็ไม่มีอะไรขึ้นเลย = กดแล้วเงียบ ตอนนี้ตรวจด่านเดียวกันที่นี่ แล้วให้แท็บเล็ต
        ถามเองด้วยข้อมูลที่มันมีอยู่แล้วใน snapshot (wp_grounded / wp_actions)
        """
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        if not self._wp_has_any_route():
            return False, "ยังไม่ได้วางจุด Waypoint"
        if self._wp_busy() or getattr(self, "_wp_takeoff_pending", False):
            return False, "กำลังบินตามเส้นทางอยู่แล้ว"

        routes = {d: r for d, r in self._wp_all_routes().items()
                  if d in self.fleet_items}
        if not routes:
            return False, "ไม่มีโดรนในเส้นทางนี้แล้ว"

        wave_on = bool(getattr(self, "_wave_enabled", False))
        if wave_on:
            if self._wp_separate:
                return False, "WAVE ใช้ได้เฉพาะโหมด GROUPED"
            groups = self._wave_selected_groups()
            if len(groups) < 2:
                return False, "WAVE ต้องเลือกอย่างน้อย 2 กลุ่ม"
            members = self._wave_member_ids(groups)
            online_groups = {self.group_of.get(int(d)) for d in members}
            if len(online_groups) < 2:
                return False, "WAVE ต้องมีอย่างน้อย 2 กลุ่มที่ออนไลน์"
            grounded = self._wp_grounded_ids(members)
        else:
            grounded = self._wp_grounded_ids(sorted(routes))

        actions = self._wp_action_points(routes)
        if actions and not p.get("confirm_actions"):
            return False, "เส้นทางนี้ปล่อยของ %d จุด — ต้องยืนยันบนแท็บเล็ตก่อน" % len(actions)

        if grounded:
            # ด่านทดสอบก่อนบิน: ปฏิเสธไปเลย ไม่เปิด dialog ค้างบนคอกพิต
            # (กฎเดียวกับ TAKEOFF ในเฟส 2)
            if hasattr(self, "_preflight") and not self._preflight.ready():
                return False, "ยังไม่ผ่านชุดทดสอบก่อนบิน — ทำที่คอมควบคุมก่อน"
            if not p.get("takeoff_first"):
                names = ", ".join("D%d" % d for d in grounded)
                return False, ("ยังไม่ขึ้นบิน %d ลำ (%s) — ยืนยัน TAKEOFF บนแท็บเล็ตก่อน"
                               % (len(grounded), names))
        if wave_on:
            if not self._wave_execute(auto=True):
                return False, "WAVE EXECUTE ไม่สำเร็จ — ดูข้อความที่คอมควบคุม"
            return True, "WAVE EXECUTE"
        self._wp_execute(auto=True)
        return True, "EXECUTE ROUTE"

    def _web_wp_clear(self, p):
        # ยืนยันเกิดบนแท็บเล็ต — ห้ามเปิด dialog ค้างบนคอกพิตที่คนถือแท็บเล็ต
        # ตอบไม่ได้ (เหมือนกฎเดียวกับ TAKEOFF ในเฟส 2)
        if not p.get("confirmed"):
            return False, "ต้องยืนยันบนแท็บเล็ตก่อน"
        if self._wp_busy():
            return False, "กำลัง EXECUTE อยู่ — ล้างเส้นทางไม่ได้"
        if not self._wp_has_any_route():
            return False, "ยังไม่มีจุดให้ล้าง"
        total = sum(len(r) for r in self._wp_all_routes().values())
        self._js("clearAllWaypoints()")
        self._waypoint_route = None
        self._wp_routes = {}
        self._log("Waypoint: ล้างเส้นทางทั้งหมด [tablet]", category="COMMAND")
        self._wp_render_points_label()
        self._field_push_state()
        return True, "ล้าง %d จุดแล้ว" % total

    def _web_swarm_start(self, p):
        if not p.get("confirmed"):
            return False, "ต้องยืนยันบนแท็บเล็ตก่อน"
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        ok, msg = self._web_formation(p, quiet=True)
        if not ok:
            return False, msg
        self._swarm_start()
        return True, "FORM UP"

    def _web_formation(self, p, quiet=False):
        fid = p.get("formation")
        if fid is not None:
            try:
                fid = int(fid)
            except (TypeError, ValueError):
                return False, "ฟอร์เมชันไม่ถูกต้อง"
            if fid not in FORMATION_NAMES:
                return False, "ฟอร์เมชันไม่ถูกต้อง"
            if hasattr(self, "form_picker"):
                self.form_picker.set_current(fid)
        sp = p.get("spacing")
        if sp is not None:
            try:
                sp = float(sp)
            except (TypeError, ValueError):
                return False, "ระยะห่างไม่ถูกต้อง"
            if not (1.0 <= sp <= 50.0):
                return False, "ระยะห่างต้องอยู่ระหว่าง 1–50 เมตร"
            if hasattr(self, "sf_spacing"):
                self.sf_spacing.setValue(sp)
        self._field_push_state()
        if quiet:
            return True, ""
        return True, "ตั้งค่าขบวนแล้ว"

    def _web_speed(self, p):
        try:
            v = float(p.get("speed"))
        except (TypeError, ValueError):
            return False, "ความเร็วไม่ถูกต้อง"
        # เว็บตั้งได้ไม่เกินเพดานของตัวเอง — คอกพิตยังปรับได้เต็มช่วงตามเดิม
        if not (0.5 <= v <= self.WEB_MOVE_MAX_SPEED):
            return False, "ความเร็วจากแท็บเล็ตต้อง 0.5–%.0f m/s" % self.WEB_MOVE_MAX_SPEED
        if hasattr(self, "sf_speed"):
            self.sf_speed.setValue(v)
        self._field_push_state()
        return True, "ความเร็ว %.1f m/s" % v

    # ── บังคับสดจากแท็บเล็ต (ข้อยกเว้นที่ตั้งใจ — docs/FIELD_TABLET_V2.md §0.1) ──
    WEB_MOVE_TTL_MS = 1200      # ไม่มีคำสั่งใหม่ภายในเท่านี้ = หยุดเอง
    WEB_MOVE_MAX_SPEED = 3.0    # เพดานความเร็วของคำสั่งที่มาจากเว็บ
    WEB_MOVE_DIRS = ("FWD", "BWD", "LEFT", "RIGHT", "UP", "DOWN", "YAW_L", "YAW_R")

    def _web_move(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        if self._manual_nav_blocked_by_core("MOVE [tablet]"):
            return False, "Core mission กำลังควบคุมอยู่ — ยกเลิก Mission ก่อนบังคับสด"
        name = str(p.get("dir") or "").upper()
        if name not in self.WEB_MOVE_DIRS:
            return False, "ทิศทางไม่ถูกต้อง"
        direction = getattr(rpc.command_pb2, "RC_DIR_" + name, None)
        if direction is None:
            return False, "ทิศทางไม่ถูกต้อง"
        spd = p.get("speed")
        if spd is not None:
            ok, msg = self._web_speed({"speed": spd})
            if not ok:
                return False, msg
        elif hasattr(self, "sf_speed") and \
                self.sf_speed.value() > self.WEB_MOVE_MAX_SPEED:
            # คอกพิตตั้งไว้เร็วกว่าเพดานเว็บ — ห้ามให้แท็บเล็ตยืมความเร็วนั้นไปใช้
            self.sf_speed.setValue(self.WEB_MOVE_MAX_SPEED)
        self._rc_press(direction)
        # ต่ออายุ deadman ทุกจังหวะที่มีคำสั่งเข้ามา
        self._web_move_timer.start(self.WEB_MOVE_TTL_MS)
        return True, "MOVE %s" % name

    def _web_move_stop(self):
        self._web_move_timer.stop()
        if getattr(self, "_rc_dir", None) is None:
            return True, "หยุดอยู่แล้ว"
        self._rc_halt(notify=False)
        self._log("MOVE STOP [tablet]", category="COMMAND")
        return True, "STOP"

    def _web_move_deadman(self):
        """ครบ TTL แล้วไม่มีคำสั่งใหม่ — สายขาดหรือคนปล่อยนิ้วแล้วข้อความหาย"""
        if getattr(self, "_rc_dir", None) is None:
            return
        self._rc_halt(notify=False)
        self._log("MOVE deadman — ขาดคำสั่งจากแท็บเล็ตเกิน %.1f วิ จึงหยุดให้เอง"
                  % (self.WEB_MOVE_TTL_MS / 1000.0),
                  category="COMMAND", severity="WARNING")
        self._show_toast("บังคับสดจากแท็บเล็ตขาดสาย — หยุดให้แล้ว", "err")

    def _web_select(self, p):
        want = []
        for v in (p.get("ids") or []):
            try:
                want.append(int(v))
            except (TypeError, ValueError):
                return False, "รายการลำไม่ถูกต้อง"
        online = set(self._connected_ids())
        picked = [d for d in sorted(set(want)) if d in online]
        if not picked:
            return False, "ลำที่เลือกไม่ได้เชื่อมต่ออยู่"
        self._selected_ids = set(picked)
        self._selected_id = picked[0]
        self._select_drone(picked[0])
        self._sync_fleet_button()
        self._refresh_selection_ui()
        self.field_hub.set_extra("selected", self._selected_or_all())
        return True, "เลือก %d ลำ" % len(picked)

    def _web_select_group(self, p):
        try:
            group = int(p.get("group"))
        except (TypeError, ValueError):
            return False, "กลุ่มไม่ถูกต้อง"
        if group == 0:
            ids = self._connected_ids()
            if not ids:
                return False, "ไม่มีลำที่เชื่อมต่ออยู่"
            self._selected_ids = set(ids)
            self._selected_id = ids[0]
            self._select_drone(ids[0])
            self._sync_fleet_button()
            self._refresh_selection_ui()
            self.field_hub.set_extra("selected", self._selected_or_all())
            return True, "เลือกทั้งฝูง %d ลำ" % len(ids)
        if group not in range(1, 7):
            return False, "กลุ่มไม่ถูกต้อง"
        before = set(self._selected_ids)
        self._select_group(group, additive=bool(p.get("additive")))
        if not self._selected_ids or (
                self._selected_ids == before
                and not any(self.group_of.get(int(d)) == group
                            for d in self._connected_ids())):
            return False, "กลุ่ม %d ไม่มีลำที่เชื่อมต่ออยู่" % group
        self.field_hub.set_extra("selected", self._selected_or_all())
        return True, "เลือกกลุ่ม %d · %d ลำ" % (group, len(self._selected_ids))

    def _web_wave(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        on = bool(p.get("on"))
        if on:
            if self._wp_separate:
                return False, "WAVE ใช้ได้เฉพาะโหมด GROUPED"
            if len(self._wave_available_groups()) < 2:
                return False, "WAVE ต้องมีอย่างน้อย 2 กลุ่มที่มีโดรน"
            if not self._wave_enabled:
                self._wave_toggle(True)
            if not self._wave_enabled:
                return False, "เปิด WAVE ไม่สำเร็จ"
            if p.get("groups") is not None:
                return self._web_wave_groups(p)
            self._field_push_state()
            return True, "เปิด WAVE"
        if not self._wave_enabled and not self._wave_executing:
            self._field_push_state()
            return True, "WAVE ปิดอยู่แล้ว"
        self._wave_toggle(False)
        self._field_push_state()
        return True, "ปิด WAVE"

    def _web_wave_groups(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        if not self._wave_enabled:
            return False, "เปิด WAVE ก่อน"
        if self._wave_executing:
            return False, "กำลัง WAVE อยู่ — เปลี่ยนกลุ่มไม่ได้"
        want = []
        for v in (p.get("groups") or []):
            try:
                want.append(int(v))
            except (TypeError, ValueError):
                return False, "รายการกลุ่มไม่ถูกต้อง"
        avail = set(self._wave_available_groups())
        picked = []
        seen = set()
        for g in want:
            if g in avail and g not in seen:
                picked.append(g)
                seen.add(g)
        if len(picked) < 2:
            return False, "ต้องเลือกอย่างน้อย 2 กลุ่มที่มีโดรน"
        for g, check in self.wave_group_checks.items():
            check.setChecked(g in picked)
        self._field_push_state()
        return True, "WAVE กลุ่ม " + " → ".join(str(g) for g in picked)

    def _web_wave_auto_next(self, p):
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        if not self._wave_enabled:
            return False, "เปิด WAVE ก่อน"
        if self._wave_executing:
            return False, "กำลัง WAVE อยู่ — เปลี่ยน AUTO NEXT GROUP ไม่ได้"
        enabled = bool(p.get("on"))
        if not hasattr(self, "chk_wave_auto_next"):
            return False, "ไม่พบตัวเลือก AUTO NEXT GROUP"
        self.chk_wave_auto_next.setChecked(enabled)
        self._field_push_state()
        return True, ("เปิด AUTO NEXT GROUP" if enabled else "ปิด AUTO NEXT GROUP")

    def _web_wave_cancel(self):
        if not self._wave_executing and not self._waypoint_executing:
            return True, "ไม่ได้กำลัง WAVE"
        self._wave_cancel()
        self._field_push_state()
        return True, "ยกเลิก WAVE"

    def _web_takeoff(self, p):
        # ยืนยันเกิดบนแท็บเล็ต (คนสั่งอยู่ตรงนั้น) แต่ต้องส่งมาให้เห็นชัด ๆ
        if not p.get("confirmed"):
            return False, "ต้องยืนยันบนแท็บเล็ตก่อน"
        # ด่านชุดทดสอบก่อนบิน: เว็บ **ปฏิเสธไปเลย** ไม่เปิด dialog ค้างบน desktop
        # ที่คนถือแท็บเล็ตตอบไม่ได้ — ให้เดินไปทำที่คอมควบคุม
        if hasattr(self, "_preflight") and not self._preflight.ready():
            return False, "ยังไม่ผ่านชุดทดสอบก่อนบิน — ทำที่คอมควบคุมก่อน"
        try:
            alt = float(p.get("alt"))
        except (TypeError, ValueError):
            return False, "ความสูงไม่ถูกต้อง"
        if not (1.0 <= alt <= 120.0) or alt != alt:
            return False, "ความสูงต้องอยู่ระหว่าง 1–120 เมตร"
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        # อย่าปล่อยให้ _target_ids fallback ไปยัง ID ที่จำจากรอบก่อนแล้วคำสั่ง
        # หายเงียบเมื่อเครื่องหลุด — หน้าแท็บเล็ตต้องรู้ทันทีว่าไม่มีเป้าหมายที่ online.
        online = set(self._connected_ids())
        if not online or not any(d in online for d in self._target_ids()):
            return False, "ไม่มีโดรนที่เชื่อมต่ออยู่สำหรับ TAKEOFF"
        self._run_cmd("TAKEOFF %.0fm [tablet]" % alt,
                      lambda ids: self.client.takeoff(ids, alt, confirmed=True),
                      dedup_key=f"TAKEOFF|alt={alt!r}|confirmed=True")
        # HTTP ตอบได้แค่ว่าคอกพิตรับคำสั่งแล้ว ผลจาก FC จะถูก mirror กลับ
        # ทาง notice ใน _on_cmd_result เพื่อไม่หลอกว่าบินขึ้นสำเร็จก่อนเวลา.
        return True, "กำลังส่ง TAKEOFF %.0f m — รอผลจากโดรน" % alt

    _WEB_FLIGHT_MODES = {
        "GUIDED": "FLIGHT_MODE_GUIDED",
        "LOITER": "FLIGHT_MODE_LOITER",
        "POSHOLD": "FLIGHT_MODE_POSHOLD",
        "STABILIZE": "FLIGHT_MODE_STABILIZE",
    }

    def _web_set_mode(self, p):
        """เปลี่ยน flight mode จากแท็บเล็ตผ่าน CoreClient เส้นเดียวกับคอกพิต."""
        if self._manual_nav_blocked_by_core("MODE [tablet]"):
            return False, "Core mission กำลังควบคุมอยู่ — ยกเลิก Mission ก่อนเปลี่ยนโหมด"
        name = str(p.get("mode") or "").strip().upper()
        enum_name = self._WEB_FLIGHT_MODES.get(name)
        if not enum_name:
            return False, "โหมดที่เลือกไม่รองรับ"
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        online = set(self._connected_ids())
        if not online or not any(d in online for d in self._target_ids()):
            return False, "ไม่มีโดรนที่เชื่อมต่ออยู่สำหรับเปลี่ยนโหมด"
        mode = getattr(rpc.common_pb2, enum_name)
        self._run_cmd("MODE %s [tablet]" % name,
                      lambda ids: self.client.set_mode(ids, mode))
        return True, "กำลังเปลี่ยนเป็น %s — รอผลจากโดรน" % name

    def _web_goto(self, p):
        if self._manual_nav_blocked_by_core("GOTO [tablet]"):
            return False, "Core mission กำลังควบคุมอยู่ — ยกเลิก Mission ก่อน GOTO"
        try:
            lat, lon = float(p.get("lat")), float(p.get("lon"))
        except (TypeError, ValueError):
            return False, "พิกัดไม่ถูกต้อง"
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) \
                or (lat == 0 and lon == 0):
            return False, "พิกัดไม่ถูกต้อง"
        if not self._guard():
            return False, "REMOTE เปิดอยู่ — สลับเป็น UI ที่คอมควบคุมก่อน"
        ids = self._target_ids()
        if not ids:
            return False, "ยังไม่ได้เลือกลำ"
        if len(ids) > 1:
            # หลายลำไปจุดเดียวกัน = เสี่ยงชน ให้เลือกลำเดียวก่อน
            return False, "GOTO จากแท็บเล็ตสั่งได้ทีละลำ — เลือกลำเดียวก่อน"
        # ใช้ความสูงปัจจุบันของลำนั้น = ไปตามพื้นราบ ไม่ไต่/ไม่ร่วงเอง
        alt = self._last_alt.get(ids[0])
        if alt is None or alt < 1.0:
            return False, "ลำนี้ยังไม่ได้บินขึ้น — GOTO จากแท็บเล็ตต้องบินอยู่ก่อน"
        self._field_goto_targets[int(ids[0])] = {"lat": round(lat, 7),
                                                   "lon": round(lon, 7)}
        self._field_push_state()
        self._run_cmd("GOTO [tablet]",
                      lambda t: self.client.goto(int(t[0]), lat, lon, float(alt)))
        return True, "GOTO"

    def _web_servo(self, p):
        label = str(p.get("channel") or "").upper()
        if label not in self.SERVO_CH:
            return False, "ช่องเซอร์โวไม่ถูกต้อง"
        if not p.get("confirmed"):
            return False, "ต้องยืนยันการปล่อยบนแท็บเล็ตก่อน"
        self._cmd_servo(label)
        return True, "SERVO %s" % label

    def _field_control_changed(self):
        """สิทธิ์ควบคุมย้ายมือ — วิ่งบน Qt main thread แล้ว (ผ่าน WebBridge)

        แบนเนอร์ต้องค้างไว้ตลอดที่สิทธิ์ไม่ได้อยู่ที่เครื่องนี้ ไม่ใช่แค่แวบเดียว
        คนที่ยืนอยู่หน้าคอมต้องรู้ตลอดเวลาว่าตอนนี้ตัวเองสั่งงานไม่ได้
        """
        if self.field is None:
            return
        st = self.field.control.status()
        if st["desktop"]:
            self._hide_banner()
            if st.get("reason"):
                self._show_toast(st["reason"], "info")
        else:
            who = st.get("label") or "แท็บเล็ต"
            self._show_banner(
                "สิทธิ์ควบคุมอยู่ที่ %s — กด “ยึดสิทธิ์คืน” ที่ปุ่ม LAN "
                "เพื่อกลับมาสั่งจากเครื่องนี้ (E-STOP ที่เครื่องนี้ยังกดได้เสมอ)"
                % who, T("amber"), persistent=True)
        self._field_paint()

    def _field_reclaim(self):
        """desktop ยึดสิทธิ์คืน — ทำได้ตลอด ไม่ต้องขออนุมัติจากแท็บเล็ต

        ไม่ส่งคำสั่งใด ๆ ถึงโดรน ย้ายแค่สิทธิ์ออกคำสั่งใหม่เท่านั้น
        """
        if self.field is None:
            return False
        self.field.control.reclaim_desktop()
        self._field_control_changed()
        self._log("desktop reclaimed control from tablet", category="COMMAND")
        return True

    def _field_paint(self):
        on = self.field is not None
        col = T("green") if on else T("dim")
        if on and not self.field.control.desktop_has_control():
            col = T("amber")            # สิทธิ์ไม่ได้อยู่ที่เครื่องนี้
        self.btn_field.setText("LAN ●" if on else "LAN OFF")
        self.btn_field.setStyleSheet(
            f"QPushButton {{ color:{col}; background:{rgba(col, 0.14)};"
            f" border:none; border-radius:6px; font-size:9px; font-weight:700; }}"
            f"QPushButton:hover {{ background:{rgba(col, 0.24)}; }}")

    def _field_start(self, host="0.0.0.0", port=None):
        """เปิดรับการเชื่อมต่อจาก LAN — สร้าง listener ตอนนี้เท่านั้น

        host/port ระบุได้เพื่อให้เทสต์ผูก 127.0.0.1:0 ได้ ค่าเริ่มต้นคือของจริง
        (ทุกอินเทอร์เฟซ + พอร์ตคงที่ ให้พิมพ์ URL บนแท็บเล็ตได้ง่าย)
        """
        if self.field is not None:
            return True, ""
        try:
            srv = FieldServer(_ASSETS, self.field_hub, self.field_sessions,
                              tiles=self.tiles, host=host,
                              port=DEFAULT_FIELD_PORT if port is None else port)
            srv.on_pair = lambda label: self._log(
                f"field tablet paired: {label or 'unknown'}")
            # คำสั่งจากเว็บวิ่งผ่าน bridge เท่านั้น — HTTP thread แตะ Qt ไม่ได้
            srv.on_command = self.web_bridge.dispatch
            srv.on_control_change = self.web_bridge.control_moved
            srv.start()
        except OSError as e:
            return False, f"เปิดไม่สำเร็จ: {e}"
        self.field = srv
        self.field_sessions.new_pin()
        # เติมข้อมูลลำที่มีอยู่แล้วทันที ไม่ต้องรอ telemetry รอบถัดไป
        for did, t in list(self._last_telem.items()):
            self._field_push(t, self._drone_names.get(int(did))
                             or getattr(t, "name", "") or f"Drone {did}")
        self._field_paint()
        self._log(f"field tablet ON (view-only) — {', '.join(srv.urls())}")
        self._show_toast("เปิด Field Tablet แล้ว — แท็บเล็ตดูได้อย่างเดียว", "ok")
        return True, ""

    def _field_stop(self):
        """ปิดรับ LAN — socket หายไปจริง และทุก session ถูกตัด"""
        srv, self.field = self.field, None
        if srv is not None:
            self.field_sessions.kick_all()
            srv.stop()
            self._log("field tablet OFF")
            self._show_toast("ปิด Field Tablet แล้ว", "info")
        self.field_hub.clear()
        self._field_paint()

    def _field_pin_info(self, regen=False):
        if regen:
            self.field_sessions.new_pin()
        urls = self.field.urls() if self.field is not None else []
        return (self.field_sessions.pin(),
                self.field_sessions.pin_seconds_left(), urls)

    def _field_dialog(self):
        dlg = FieldTabletDialog(
            self,
            is_on=lambda: self.field is not None,
            start=self._field_start,
            stop=self._field_stop,
            pin_info=self._field_pin_info,
            kick=self.field_sessions.kick_all,
            clients=self.field_sessions.count,
        )
        dlg.exec_()
        self._field_paint()

    # ══════════════════════════════════════════════════════════
    #  TELEMETRY STREAM
    # ══════════════════════════════════════════════════════════
    def _start_stream(self):
        self.telem_thread = TelemetryThread(self.client.stub)
        self.telem_thread.telemetry.connect(self._on_telemetry)
        self.telem_thread.stream_error.connect(self._on_stream_error)
        self.telem_thread.start()
        self.event_thread = EventThread(self.client.stub)
        self.event_thread.event.connect(self.event_recv)
        self.event_thread.start()
        sec = "mTLS" if getattr(self.client, "secure", False) else "PLAINTEXT"
        self._log(f"subscribed to telemetry + events [{sec}]")

    def _on_telemetry(self, t):
        if t.drone_id in self._removed_ids:
            return
        # V1 Phase 3 shadow write: ingest every accepted packet into the immutable
        # frontend read model.  This does not throttle/replace raw telemetry used
        # by flight/business logic below.
        telem_view = self._telemetry_store.update(t)
        render_widgets = self._telemetry_render_gate.should_render(telem_view)
        item = self.fleet_items.get(t.drone_id)
        display_name = self._fleet_presenter.display_name(
            t.drone_id, self._drone_names.get(int(t.drone_id)), t.name)
        if item is None:
            item = FleetItem(t.drone_id, display_name, self._pixmap)
            self._wire_fleet_item(item)
            self.fleet_items[t.drone_id] = item
            self.fleet_area.addWidget(item)
            self._reserved_ids.discard(t.drone_id)   # เข้าฝูงจริงแล้ว ปลดการจอง
            # นับ online หลังบันทึก _last_seen ท้ายเมธอด (ไม่งั้นลำที่เพิ่งเข้ามาจะยังไม่ถูกนับ)
            self._refresh_fleet_count()
            self._update_fleet_scroll_height()
            self._refresh_leader_combo()
            # _wire_fleet_item() already paints the restored group number on
            # the card.  Refresh the chips too, otherwise they still say zero
            # and remain disabled until the operator assigns the group again.
            self._refresh_group_ui()
            self._refresh_takeoff_panel()
            self._update_head_ui()
            if hasattr(self, "mlog"):
                self.mlog.set_vehicles(self.fleet_items.keys())
            self._log(f"Drone {t.drone_id} added to fleet", vehicle=f"Drone {t.drone_id}")
        if render_widgets:
            item.update_from_telemetry(telem_view)
            self._health.record_render()
        item.set_display_name(display_name)
        self._last_telem[t.drone_id] = t
        # Shadow parity check is sampled (first packets + every 100th) so the
        # comparison itself cannot become telemetry-load overhead.
        ingest_count = self._telemetry_store.ingest_count(t.drone_id)
        if ingest_count <= 3 or ingest_count % 100 == 0:
            shadow_diff = self._telemetry_store.diff_message(self._last_telem[t.drone_id])
            if shadow_diff:
                self._telemetry_shadow_mismatch_count += 1
                if self._telemetry_shadow_mismatch_count <= 3:
                    self._log(
                        f"TelemetryStore shadow mismatch D{int(t.drone_id)}: "
                        f"{','.join(shadow_diff[:6])}",
                        vehicle="System", category="STATUS", severity="WARNING")
        self._health.note_telemetry(t.drone_id)   # observability: telemetry age

        # F6: while Core owns the mission, telemetry is also the cheap reconnect/
        # presentation heartbeat. Query at most once/second; never StartMission.
        if bool(getattr(self, "_mission_core_authority", False)):
            qnow = time.monotonic()
            if qnow - float(getattr(self, "_mission_state_last_query", 0.0)) >= 1.0:
                self._mission_state_last_query = qnow
                mission_shadow.refresh_state(self, rebuild=False)

        # ส่งต่อให้ Field Tablet (ถ้าเปิด LAN อยู่) — hub มี lock ของตัวเอง
        # และรับเฉพาะ dict ล้วน จึงข้าม thread ไปให้ SSE อ่านได้ปลอดภัย
        if self.field is not None:
            self._field_push(telem_view, display_name)

        # ── เลือกลำแรกที่ "ออนไลน์จริง" ให้อัตโนมัติถ้ายังไม่ได้เลือกอะไรเลย ──
        # BUGFIX: เดิมบรรทัดนี้อยู่ในบล็อก "สร้างการ์ดใหม่" ข้างบนเท่านั้น
        # แต่ `_load_saved_ips()` สร้างการ์ดจาก IP ใน SQLite ไว้ตั้งแต่เปิดโปรแกรมแล้ว
        # ลำที่เคยต่อมาก่อนจึง **มีการ์ดอยู่แล้ว** บล็อกนั้นไม่ทำงาน → ไม่มีลำไหนถูกเลือก
        # → `_target_ids()` ตกไปที่ fallback ซึ่งเดาเอาจาก ID ต่ำสุดในฝูง
        # ซึ่งอาจเป็นลำ offline ที่กู้มาจาก SQLite (เช่นมี Drone 1 ค้างอยู่ แต่ของจริงคือ
        # Drone 3) คำสั่งจึงวิ่งไปหาลำที่ไม่ได้เชื่อมต่อแล้วเงียบหายไป จนผู้ใช้คลิก
        # การ์ดลำที่ถูกต้องเองถึงจะสั่งได้ = อาการ "กดครั้งแรกไม่ติด ต่อไปกดปุบปับ"
        if not self._selected_id:
            self._select_drone(t.drone_id)
        th = (getattr(t, "host", "") or "").strip()
        if th:
            self._remember_endpoint(t.drone_id, th, int(getattr(t, "port", 0) or 0))

        if t.drone_id == self._selected_id:
            if render_widgets:
                self.sel_card.update_from_telemetry(telem_view)
                self._update_coord(telem_view)
                if hasattr(self, "lbl_cur_mode"):
                    self._style_cur_mode(rpc.mode_name(telem_view.mode))
            self.sel_card.set_display_name(display_name)
            self.sel_card.set_quick_enabled(self._ui_mode)   # มี telemetry = สั่งได้

        self._auto_guided_after_land(t)
        self._sync_servo_from_telemetry(t)
        # อุ่นเครื่องช่อง A/B ให้เองครั้งแรกที่ลำนี้มี telemetry ครบพอจะสั่งได้
        # (ต้องมี rc_valid ไม่งั้นตรวจไม่ได้ว่า FC ตอบรับหรือยัง)
        if getattr(t, "rc_valid", False):
            self._prime_servo(t.drone_id)
        self._check_servo_primed(t)

        if t.position.lat != 0.0 or t.position.lon != 0.0:
            self._last_alt[t.drone_id] = t.position.alt_rel
            # จำจุดปล่อย (home) ตอนยังอยู่บนพื้น — ใช้ตอน RTL แยกชั้น
            if t.position.alt_rel < 1.5 and t.drone_id not in self._home_pos:
                self._home_pos[t.drone_id] = (t.position.lat, t.position.lon)
            if not self._map_followed_gps:
                self._map_followed_gps = True
                self._jump_map_to(t.position.lat, t.position.lon, set_gcs=True)
                self._log(
                    f"แผนที่ → GPS Drone {t.drone_id} "
                    f"{t.position.lat:.6f},{t.position.lon:.6f}",
                    category="STATUS")
            color = drone_color(t.drone_id)
            self._js(f"updateDrone({t.drone_id},{t.position.lat:.7f},{t.position.lon:.7f},"
                     f"{t.heading:.1f},'{color}',{display_name!r})")
            # เนื้อหา tooltip (โชว์เฉพาะตอน hover — แผนที่จึงเห็นแค่จุดสี)
            #
            # PERF: เดิมยิงเข้า QtWebEngine ทุกแพ็กเก็ต = 10 ครั้ง/วิ/ลำ (5 ลำ = 50 ครั้ง/วิ)
            # ทั้งที่ผู้ใช้เห็นก็ต่อเมื่อเอาเมาส์ไปชี้ · runJavaScript แต่ละครั้งต้อง
            # marshal ข้าม process ไปยัง renderer — เป็นภาระ GUI thread ที่ไม่ได้ใช้
            # ตอนนี้ส่งเฉพาะตอนเนื้อหาเปลี่ยนจริง และไม่ถี่กว่า 2 Hz ต่อลำ
            now_tip = time.monotonic()
            if now_tip - self._tip_at.get(t.drone_id, 0.0) >= 0.5:
                info = (f"<b>{html.escape(display_name)}</b><br>"
                        f"{rpc.status_name(t.status)} · {rpc.mode_name(t.mode)}<br>"
                        f"ALT {t.position.alt_rel:.1f} m · {t.ground_speed:.1f} m/s<br>"
                        f"BAT {t.battery_pct:.0f}% · SAT {t.sat_count}")
                self._tip_at[t.drone_id] = now_tip
                if self._tip_txt.get(t.drone_id) != info:
                    self._tip_txt[t.drone_id] = info
                    self._js(f"setDroneInfo({t.drone_id}, {info!r})")

        now = time.monotonic()
        # "เห็นล่าสุด" ต้องอัปเดตทุกแพ็กเก็ต — เดิมไปผูกอยู่ใน if ของ throttle การ log
        # (อัปเดตแค่ทุก 1.5 วิ) ทำให้ค่าที่ใช้ตัดสิน online/offline เพี้ยนตามจังหวะ log
        self._last_seen[t.drone_id] = now
        self._refresh_online_count()
        if now - self._last_telem_log.get(t.drone_id, 0) >= 1.5:
            self._last_telem_log[t.drone_id] = now
            self._log(
                f"{rpc.status_name(t.status)} {rpc.mode_name(t.mode)} "
                f"alt={t.position.alt_rel:.1f} bat={t.battery_pct:.0f}% sat={t.sat_count}",
                vehicle=f"Drone {t.drone_id}", category="TELEMETRY")
        # PERF: setStyleSheet บังคับ Qt re-polish ทั้ง widget — เดิมเรียกทุกแพ็กเก็ต
        # (10 Hz × จำนวนลำ) ทั้งที่ค่าเหมือนเดิมตลอด ทำให้ event loop หนักโดยเปล่า
        # ประโยชน์ และคลิกปุ่มแล้วรู้สึกหน่วง — ตอนนี้ทาสีเฉพาะตอนสถานะเปลี่ยนจริง
        if getattr(self, "_link_pill_state", None) != "ok":
            self._link_pill_state = "ok"
            self.pill_link.setText("LINK OK")
            self.pill_link.setStyleSheet(
                f"color:{T('green')}; background:{rgba(T('green'), 0.14)}; border-radius:6px;"
                f" padding:2px 6px; font-size:9px; font-weight:700;")
        self.lbl_status.setText("SwarmGod · LIVE")

    def _auto_guided_after_land(self, t):
        """ลงจอดเสร็จแล้ว → เข้า GUIDED อัตโนมัติ (spec ข้อ 5)

        เงื่อนไข: เคยอยู่สถานะ LANDING มาก่อน แล้วตอนนี้ disarm + ต่ำกว่า 1 m
        (= แตะพื้นจริง ไม่ใช่แค่ลดระดับ) และยังไม่ได้อยู่ GUIDED อยู่แล้ว

        สั่งแค่ครั้งเดียวต่อรอบการลง — จำไว้ใน _land_guided_done กันยิงซ้ำทุกแพ็กเก็ต
        telemetry (10 Hz) ซึ่งจะถล่ม FC ด้วย DO_SET_MODE
        """
        did = int(t.drone_id)
        st = rpc.status_name(t.status)
        if st == "LANDING":
            self._landing_seen.add(did)
            self._land_guided_done.discard(did)
            return
        if did not in self._landing_seen:
            return
        on_ground = (not t.armed) and t.position.alt_rel < 1.0
        if not on_ground or did in self._land_guided_done:
            return
        self._land_guided_done.add(did)
        self._landing_seen.discard(did)
        if rpc.mode_name(t.mode) == "GUIDED":
            return          # อยู่ GUIDED อยู่แล้ว ไม่ต้องสั่งซ้ำ
        if not self._ui_mode:
            return          # REMOTE ถือคันบังคับอยู่ — ห้าม UI แทรกโหมด
        self._log(f"ลงจอดเสร็จ → ตั้งโหมด GUIDED อัตโนมัติ",
                  vehicle=f"Drone {did}", category="COMMAND")
        mode = getattr(rpc.common_pb2, "FLIGHT_MODE_GUIDED")
        threading.Thread(target=lambda: self._safe(lambda: self._dispatch_core(
            "MODE GUIDED [auto-after-land]", lambda: self.client.set_mode([did], mode),
            source="auto-after-land", targets=[did], enforce_dedup=False)),
            daemon=True).start()

    def _update_coord(self, t):
        text = (
            f"LAT {t.position.lat:.6f}  ·  LNG {t.position.lon:.6f}  ·  "
            f"ALT {t.position.alt_rel:.1f} m  ·  GND SPD {t.ground_speed:.1f} m/s  ·  "
            f"HDG {t.heading:03.0f}°")
        self.coord.setText(text)
        self.coord.setToolTip(text)

    def _select_drone(self, drone_id):
        self._selected_id = drone_id
        if not self._selected_ids:
            self._selected_ids = {int(drone_id)} if drone_id else set()
        # ไฮไลต์ตามชุดที่เลือก (รองรับหลายลำ) ไม่ใช่แค่ลำหลัก
        for did, item in self.fleet_items.items():
            item.set_selected(did in self._selected_ids)
        # โหลดค่า ALT/SPACING ของลำนี้เข้าการ์ดล่าง
        if hasattr(self, "sel_card"):
            self.sel_card.set_selected_drone(int(drone_id or 0))
            self.sel_card.set_params(
                self._drone_alt.get(int(drone_id), self.sf_takeoff.value()),
                self._drone_spacing.get(int(drone_id), self.sf_spacing.value()))
            self.sel_card.set_head(int(drone_id) == self._head_id and self._head_id != 0)
            self.sel_card.set_servo_state(self._servo_state.get(int(drone_id)))
            self._refresh_servo_buttons()   # ปุ่ม A/B ต้องสะท้อนลำที่เพิ่งเลือก
        # Presentation reads prefer the immutable Phase-3 store.  Raw telemetry
        # remains available separately in _last_telem for flight/business paths.
        t = self._last_telem.get(drone_id)
        presentation = self._telemetry_store.get(drone_id) or t
        if presentation is not None:
            self.sel_card.update_from_telemetry(presentation)
            self.sel_card.set_display_name(
                self._drone_names.get(int(drone_id))
                or presentation.name or f"Drone {drone_id}")
            # BUGFIX: เดิมพอเคยเลือกลำที่ยังไม่มี telemetry ปุ่มจะถูกปิดค้างตลอด
            # เพราะไม่มีใครเปิดคืนตอนมี telemetry แล้ว → quick action กดไม่ได้
            self.sel_card.set_quick_enabled(self._ui_mode)
            self._update_coord(presentation)
        else:
            # จาก SQLite / memory — ยังไม่มี telem
            self.sel_card.drone_id = int(drone_id or 0)
            item = self.fleet_items.get(drone_id)
            name = getattr(item, "name", "") if item is not None else ""
            self.sel_card.set_display_name(
                self._drone_names.get(int(drone_id)) or name or f"Drone {drone_id}")
            host, port = self._endpoints.get(drone_id, ("", 0))
            if not host and item is not None:
                host = (getattr(item, "_host", "") or "").strip()
                port = int(getattr(item, "_port", 0) or 0)
            if not self.sel_card.ed_host.hasFocus():
                self.sel_card.ed_host.setText(host or "")
            if not self.sel_card.ed_port.hasFocus():
                self.sel_card.ed_port.setText(str(port) if port else "")
            self.sel_card.set_quick_enabled(False)
        self._js(f"focusDrone({drone_id})")
        self._js3d(f"map3d.focusDrone({int(drone_id)})")
        self._push_map3d_view()              # ลำที่เลือกต้องกะพริบใน 3D ด้วย
        QTimer.singleShot(200, self._auto_ping_selected)

    def _on_drone_color(self, drone_id: int, color: str):
        item = self.fleet_items.get(drone_id)
        if item is not None:
            item.set_accent(color)
        if hasattr(self, "mlog"):
            self.mlog.set_vehicles(self.fleet_items.keys())
        t = self._last_telem.get(drone_id)
        if t is not None and (t.position.lat != 0.0 or t.position.lon != 0.0):
            shown = self._drone_names.get(int(drone_id)) or f"Drone {drone_id}"
            self._js(f"updateDrone({drone_id},{t.position.lat:.7f},{t.position.lon:.7f},"
                     f"{t.heading:.1f},'{color}',{shown!r})")
            if drone_id == self._selected_id:
                self._js(f"focusDrone({drone_id})")
        self._log(f"color → {color}", vehicle=f"Drone {drone_id}", category="STATUS")

    def _ping_drone(self, drone_id: int):
        if getattr(self, "_closing", False):
            return
        host, port = "", 0
        if hasattr(self, "sel_card") and (
                not drone_id or drone_id == self._selected_id or drone_id == self.sel_card.drone_id):
            host, port = self.sel_card._read_endpoint()
        if not host and drone_id:
            t = self._last_telem.get(drone_id)
            if t is not None:
                host = (getattr(t, "host", "") or "").strip()
                port = int(getattr(t, "port", 0) or 0)
        if not host:
            if hasattr(self, "sel_card"):
                self.sel_card.set_ping_result(False, "PING no IP")
            return
        if not port:
            port = 5760
        if hasattr(self, "sel_card"):
            self.sel_card.set_ping_pending()
        if not self._ping.ping(drone_id or self._selected_id or 0, host, port):
            pass

    def _auto_ping_selected(self):
        if getattr(self, "_closing", False):
            return
        did = self._selected_id
        if not did or self._ping.busy:
            return
        self._ping_drone(did)

    def _on_ping_result(self, drone_id: int, res):
        ms = res.ms if res.ms is not None else res.tcp_ms
        if res.ok:
            tag = f"{ms:.0f} ms" if ms is not None else "ok"
            text = f"PING {tag} · {res.method.upper()}"
        else:
            text = f"PING FAIL · {res.detail}"
        if hasattr(self, "sel_card") and (
                not drone_id or drone_id == self._selected_id or drone_id == self.sel_card.drone_id):
            self.sel_card.set_ping_result(res.ok, text)
        sev = "INFO" if res.ok else "WARNING"
        self._log(text, vehicle=f"Drone {drone_id}" if drone_id else "System",
                  category="STATUS", severity=sev)

    def _refresh_leader_combo(self):
        if not hasattr(self, "cmb_leader"):
            return
        cur = self.cmb_leader.currentText()
        self.cmb_leader.blockSignals(True)
        self.cmb_leader.clear()
        self.cmb_leader.addItem("Auto")
        for did in sorted(self.fleet_items.keys()):
            self.cmb_leader.addItem(f"Drone {did}")
        idx = self.cmb_leader.findText(cur)
        if idx >= 0:
            self.cmb_leader.setCurrentIndex(idx)
        self.cmb_leader.blockSignals(False)

    def _on_stream_error(self, msg):
        self._log(f"stream error: {msg}", severity="ERROR", category="ALERT")
        self.lbl_status.setText(f"SwarmGod · stream error")
        self._link_pill_state = "down"   # ให้ _on_telemetry ทาสีเขียวใหม่ตอนกลับมา
        self.pill_link.setText("LINK DOWN")
        self.pill_link.setStyleSheet(
            f"color:{T('red')}; background:{rgba(T('red'), 0.14)}; border-radius:6px;"
            f" padding:2px 6px; font-size:9px; font-weight:700;")

    def _open_ip_scan(self):
        dlg = ScanIpDialog(self)
        dlg.connect_requested.connect(self._connect_from_scan)
        dlg.exec_()

    def _next_drone_id(self) -> int:
        """ID ว่างถัดไป — นับรวม "ที่จองไว้แล้วแต่ telemetry ยังไม่มา" ด้วย

        fleet_items จะมีสมาชิกก็ต่อเมื่อ telemetry ไหลเข้ามาแล้ว ถ้าดูแค่ dict นี้
        การกด CONNECT หลายลำรวดเดียว (เช่นเลือกทุก IP จากหน้า SCAN) จะได้ ID ซ้ำกันหมด
        """
        used = set(self.fleet_items.keys()) | self._reserved_ids
        limit = 255
        if hasattr(self, "sf_maxdrones"):
            limit = int(self.sf_maxdrones.value())
        for i in range(1, limit + 1):
            if i not in used:
                self._reserved_ids.add(i)
                return i
        nid = max(used, default=0) + 1
        self._reserved_ids.add(nid)
        return nid

    def _connect_from_scan(self, host: str, port: int):
        host = (host or "").strip()
        port = int(port or 0) or 5760
        # สแกนเจอ TCP เปิด → ใช้ TCP เป็นหลัก
        self._connect_protocol = "tcp"
        self._connect_endpoint = f"{host}:{port}"
        if self._reject_duplicate_ip(host, port):
            return
        did = self._next_drone_id()
        self._removed_ids.discard(did)
        self._remember_endpoint(did, host, port)
        name = self._drone_names.get(did) or f"Drone {did}"
        self._log(f"SCAN → CONNECT {host}:{port} as {name}",
                  vehicle=name, category="COMMAND")
        self._show_toast(f"CONNECT {host}:{port}", "info")

        def worker():
            try:
                r = self.client.connect_drone(did, name, host, int(port), protocol="tcp")
                self.cmd_result.emit(f"CONNECT: ok={r.ok} {r.message}")
            except Exception as e:
                self.cmd_result.emit(f"CONNECT: ERROR {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _on_connect_clicked(self):
        """เปิด popup CONNECT เพื่อไม่ให้ TCP/IP กินพื้นที่ใน FLEET."""
        dlg = ConnectIpDialog(
            endpoint=self._connect_endpoint,
            protocol=self._connect_protocol,
            parent=self)
        dlg.connect_requested.connect(self._connect_from_dialog)
        dlg.exec_()

    def _connect_from_dialog(self, host: str, port_i: int, proto: str):
        host = (host or "").strip()
        proto = (proto or "tcp").lower()
        port_i = int(port_i or (14550 if proto == "udp" else 5760))
        self._connect_protocol = proto
        self._connect_endpoint = f"{host}:{port_i}"
        if not host:
            return
        if self._reject_duplicate_ip(host, port_i):
            return
        did = self._next_drone_id()
        self._removed_ids.discard(did)
        self._remember_endpoint(did, host, port_i, protocol=proto)
        name = self._drone_names.get(did) or f"Drone {did}"
        try:
            res = self.client.connect_drone(
                did, name, host, port_i, protocol=proto)
            self._log(f"Connect [{proto}] {host}:{port_i} → ok={res.ok}",
                      category="COMMAND")
        except Exception as e:
            self._log(f"Connect failed: {e}", severity="ERROR")
            # ล้มเหลว → เอาออกจาก memory แต่คง SQLite ถ้าเคยเซฟไว้ก่อนแล้ว
            # (ครั้งนี้เพิ่ง remember → ลบ persist ด้วย)
            self._forget_endpoint(did, persist=True)

    def _autoconnect_n(self, n):
        for i in range(1, n + 1):
            port = 5760 + (i - 1) * 10
            host = "127.0.0.1"
            if self._reject_duplicate_ip(host, port, exclude_id=i):
                continue
            self._removed_ids.discard(i)
            self._remember_endpoint(i, host, port)
            name = self._drone_names.get(i) or f"Drone {i}"
            try:
                r = self.client.connect_drone(i, name, host, port)
                self._log(f"Connect Drone {i} (:{port}) → ok={r.ok}", category="COMMAND")
            except Exception as e:
                self._forget_endpoint(i, persist=True)
                self._log(f"Connect Drone {i} failed: {e}", severity="ERROR")

    def _demo_connect_all(self):
        self._autoconnect_n(3)

    def _demo_takeoff_parallel(self):
        self._log("TAKEOFF x3 (parallel) @ 20m", category="COMMAND")
        for i in (1, 2, 3):
            threading.Thread(target=lambda k=i: self._dispatch_core(
                "TAKEOFF 20m [demo]", lambda: self.client.takeoff([k], 20, confirmed=True),
                source="demo", targets=[k], dedup_key="TAKEOFF|alt=20|confirmed=True",
                enforce_dedup=False), daemon=True).start()

    # ── misc ──
    def _tick_clock(self):
        self.lbl_clock.setText(time.strftime("%H:%M:%S"))
        # ป้าย PREFLIGHT ต้องเปลี่ยนเองเมื่อผลเทสหมดอายุ (ไม่ได้รอให้ผู้ใช้กดอะไร)
        self._refresh_preflight_ui()
        # UI heartbeat (observe-only) — จับ event-loop stall แล้ว log เท่านั้น
        self._health.record_ui_tick()
        self._health_ticks += 1
        if self._health_ticks % self._health_log_every == 0:
            self._log_health_snapshot()

    def _on_ui_stall(self, stall_ms):
        """เรียกเมื่อ HealthMonitor เข้า STALLED — **log อย่างเดียว** (ไม่มี flight action)."""
        self._log(
            f"UI event loop stalled ~{stall_ms:.0f} ms",
            vehicle="System", category="STATUS", severity="WARNING")

    def _log_health_snapshot(self):
        """บันทึก metric สุขภาพ UI แบบความถี่ต่ำ ไว้เทียบก่อน/หลัง refactor (observe-only)."""
        snap = self._health.snapshot()
        ages = snap.get("telemetry_age_ms") or {}
        max_age = max(ages.values()) if ages else 0.0
        telem_rate = self._telemetry_store.ingest_rate()
        render_gate = self._telemetry_render_gate.stats()
        gated_total = render_gate["rendered"] + render_gate["skipped"]
        skip_pct = (100.0 * render_gate["skipped"] / gated_total) if gated_total else 0.0
        self._log(
            f"HEALTH {snap['status']} · hb={snap['heartbeat_ms']:.0f}ms · "
            f"stalls={snap['stall_count']} · telem_max_age={max_age:.0f}ms · "
            f"telem_in={telem_rate:.1f}/s · render={self._health.render_rate():.1f}/s · "
            f"telem_render_skip={skip_pct:.0f}% · "
            f"shadow_mismatch={self._telemetry_shadow_mismatch_count}",
            vehicle="System", category="STATUS", severity="INFO")

    def _log(self, msg, vehicle="System", category="STATUS", severity="INFO"):
        if hasattr(self, "mlog"):
            self.mlog.add(msg, vehicle=vehicle, category=category, severity=severity)

    def closeEvent(self, e):
        # Flip the lifecycle guard before stopping timers/threads.  Queued
        # singleShot callbacks may already be in the event queue; they must see
        # closing=True before any QtWebEngine object starts being destroyed.
        self._closing = True
        # หยุด timer ทุกตัวก่อน — ไม่งั้น callback ยังยิงต่อหลังหน้าต่างปิด
        # (watchdog/poll ที่ยิงใส่ object ที่กำลังถูกทำลาย = crash)
        self._rtl_active = False
        self._waypoint_executing = False   # กัน QTimer.singleShot ของ _wp_advance ยิงต่อ
        for name in ("_clock", "_swarm_timer", "_rc_timer", "_ping_timer",
                     "_head_timer", "_collision_timer", "_conn_timer",
                     "_banner_timer", "_toast_timer", "_web_move_timer"):
            t = getattr(self, name, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
        # PingService owns a QThread which may be inside a Windows ping subprocess.
        # Join it before Qt destroys this window/its child QObjects; otherwise the
        # full suite can tear down a live QThread and crash natively (0xC0000005).
        ping = getattr(self, "_ping", None)
        if ping is not None:
            try:
                ping.shutdown(4000)
            except Exception:
                pass
        if getattr(self, "cv_panel", None):
            self.cv_panel.shutdown()
        if getattr(self, "field", None) is not None:
            self._field_stop()      # ปิดหน้าต่าง = ปิดพอร์ต LAN ด้วยเสมอ
        # Stop both stream workers and JOIN them BEFORE closing the owned channel.
        # Closing the gRPC channel while a worker is still inside the stream
        # iterator is a native access violation, so the channel is released only
        # once both QThreads are confirmed terminated.  Signals are disconnected
        # first so no telemetry/event/error emit can land on this closing window.
        threads_terminated = True
        for name in ("telem_thread", "event_thread"):
            th = getattr(self, name, None)
            if th is None:
                continue
            for sig_name in ("telemetry", "stream_error", "event"):
                sig = getattr(th, sig_name, None)
                # Only touch real bound signals.  TelemetryThread has no ``event``
                # signal, so ``th.event`` is QObject.event() (a method) — calling
                # .disconnect() on it would raise and abort closeEvent from C++.
                if sig is None or not hasattr(sig, "disconnect"):
                    continue
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass   # already disconnected / no slots
            if not th.shutdown(2000):
                threads_terminated = False
        owned_client = getattr(self, "_owned_core_client", None)
        if owned_client is not None and threads_terminated:
            try:
                owned_client.close()
            finally:
                self._owned_core_client = None
        # If a worker did not terminate in time (unreachable with deterministic
        # cancel above), the channel is deliberately left open rather than torn
        # down under a live stream iterator; the OS reclaims it at process exit.
        super().closeEvent(e)


def main():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtGui import QFont, QIcon
    QApplication.setAttribute(_Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _icon_path = os.path.join(_ASSETS, "logo.ico")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))
    # ฟอนต์ที่ฝังมากับโปรแกรม (Sarabun + IBM Plex Mono) — ต้องลงทะเบียนก่อนสร้าง widget
    loaded = theme.load_bundled_fonts(_ASSETS)
    font = QFont("Sarabun" if any("Sarabun" in f for f in loaded) else "Segoe UI",
                 theme.BASE_PT)
    font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    app.setFont(font)

    # เปิด cockpit ตรง ๆ (ไม่ผ่าน launcher) ก็ต้องใส่รหัสเหมือนกัน
    # ถ้ามาจาก launcher จะมีธง SWARMGOD_AUTHED=1 อยู่แล้ว → ข้ามให้
    from .widgets.login_dialog import require_passcode
    if not require_passcode():
        sys.exit(0)

    win = GroundStation()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
