"""
field_server.py — Field Tablet: เสิร์ฟหน้าดูสถานะให้เครื่องในวง LAN หน้างาน

เฟส 2 = ดูได้ทุกเครื่อง + **สั่งงานได้ทีละเครื่องเดียว** (docs/FIELD_TABLET.md)

สิทธิ์สั่งงาน (§4): desktop ถือเป็นค่าเริ่มต้น แท็บเล็ตเครื่องแรกที่ขอได้เลย
ที่เหลือเป็น viewer และขอรับช่วงได้ · desktop ยึดคืนได้ตลอดโดยไม่ต้องขออนุมัติ
· ขาด heartbeat สิทธิ์เด้งกลับ desktop เอง

คำสั่ง (§3.2): ผ่าน **allowlist เท่านั้น** (PILOT_COMMANDS/SAFE_COMMANDS)
คำสั่งบังคับสด RcMove/SetYaw ไม่อยู่ในลิสต์ จึงถูกปฏิเสธเสมอแม้ยิงตรง
HOLD/E-STOP กดได้ทุกเครื่องโดยไม่ต้องถือสิทธิ์และไม่ติด rate limit เพราะ
การกันไม่ให้คนกดหยุดคือทิศทางที่ผิดของความปลอดภัย

ตัวโมดูลนี้ **ไม่ยิงคำสั่งเอง** — แค่ตรวจด่านแล้วส่งต่อให้ callback `on_command`
ที่ app.py ตั้งไว้ ซึ่งจะ marshal เข้า Qt main thread ไปวิ่งผ่าน
`_selected_or_all()` เส้นเดียวกับปุ่มบน desktop (§3.1 ข้อ 4)

ความปลอดภัย (docs/FIELD_TABLET.md §3):
  - **ไม่เปิด LAN = ไม่มี listener อยู่เลย** (ไม่ใช่แค่ bind 127.0.0.1)
    ตัวเซิร์ฟเวอร์ถูกสร้างตอนกดเปิดเท่านั้น ปิดแล้ว socket หายไปจริง
  - ต้องจับคู่ด้วย PIN 6 หลักก่อน ถึงจะได้ session cookie
  - PIN ผิดครบ MAX_PIN_TRIES → PIN ตายทันที ต้องสร้างใหม่
    (6 หลัก = 1M ชุด ถ้าไม่จำกัดจำนวนครั้ง ยิงบน LAN แตกได้ในไม่กี่นาที)
  - cookie เป็น HttpOnly + SameSite=Strict → สคริปต์อ่านไม่ได้ + กัน CSRF
  - ตรวจ Host header ต้องเป็น IP/localhost เท่านั้น → กัน DNS rebinding
    (เว็บภายนอกชี้โดเมนมาที่ IP วง LAN แล้วให้บราวเซอร์เหยื่อยิงเข้ามา)
  - ไม่ใส่ CORS header ที่ /api/* → หน้าเว็บอื่นอ่านข้ามต้นทางไม่ได้

thread: HTTP handler รันคนละ thread กับ Qt โมดูลนี้จึง **ไม่ import Qt เลย**
ข้อมูลเข้าทาง TelemetryHub ซึ่งเป็น dict ล้วน + lock เท่านั้น
"""
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8760
COOKIE_NAME = "swarmgod_field"
PIN_TTL_S = 300.0          # PIN อายุ 5 นาที
MAX_PIN_TRIES = 5          # ผิดครบเท่านี้ PIN ตาย
SESSION_TTL_S = 12 * 3600  # session 12 ชม. (จบงานหนึ่งวัน)
MAX_STREAMS = 8            # SSE ค้าง thread ละ 1 เส้น — กัน thread หมด
DEFAULT_RATE_HZ = 5.0      # เพดานส่ง telemetry (spec §5)
MAX_BODY = 4096

# ── เฟส 2: สิทธิ์สั่งงาน ──
DESKTOP = "__desktop__"    # เจ้าของสิทธิ์เริ่มต้น = คอมควบคุม
HEARTBEAT_TIMEOUT_S = 6.0  # ขาด heartbeat เท่านี้ สิทธิ์เด้งกลับ desktop
CMD_BURST = 5              # โควตาคำสั่งสะสมต่อ session
CMD_PER_SEC = 1.0          # เติมคืนวินาทีละกี่คำสั่ง
MOVE_BURST = 12            # บังคับสดต้องยิงถี่ — คนละถังกับคำสั่ง mission
MOVE_PER_SEC = 10.0

# คำสั่งที่เปิดให้สั่งผ่านเว็บได้ — **allowlist เท่านั้น ห้ามเปลี่ยนเป็น blocklist**
# ของใหม่ที่ไม่ได้ใส่ในนี้จะถูกปฏิเสธโดยปริยาย ซึ่งเป็นทิศทางที่ปลอดภัย
#
# เกณฑ์: ต้องเป็นคำสั่งระดับ mission ที่โดรนบินต่อเองได้ถ้าลิงก์หลุด
PILOT_COMMANDS = frozenset({
    "arm", "takeoff", "set_mode", "goto", "waypoint_execute", "rtl", "land", "servo",
    "select",   # เปลี่ยนลำเป้าหมาย = เปลี่ยนว่าคำสั่งถัดไปไปลงที่ใคร จึงต้องถือสิทธิ์
    "select_group",  # เลือกทั้งกลุ่ม / ทั้งฝูง — เทียบเท่าคลิกชิปกลุ่มบนคอกพิต
    # ── เฟส 3 (docs/FIELD_TABLET_V2.md) ──
    "waypoint_mode", "waypoint_add", "waypoint_undo", "waypoint_clear",
    "swarm_start", "swarm_stop", "swarm_return", "formation", "speed",
    "wave", "wave_groups", "wave_auto_next",
})
# ทุกคนกดได้เสมอ ไม่ต้องถือสิทธิ์ ไม่ติด rate limit
# หยุดคือทิศทางที่ปลอดภัยเสมอ — กดพลาด = ภารกิจช้า ส่วนกดบินขึ้นพลาด = คนเจ็บ
# `hold_all` ยกเลิกการนำทาง/ขบวนแล้วสั่งทุกลำ HOLD โดยไม่ตัดมอเตอร์
SAFE_COMMANDS = frozenset({"hold", "hold_all", "estop", "move_stop", "wave_cancel"})

# บังคับสดผ่านเว็บ — **ข้อยกเว้นที่ตั้งใจ** ของกฎเดิม (docs/FIELD_TABLET_V2.md §0.1)
# เดิมห้ามเด็ดขาดเพราะ WiFi กระตุก 300ms ตอนบังคับสดคืออันตรายจริง
# เปิดได้เพราะมีตัวกัน 3 ชั้นที่ของเดิมไม่มี:
#   1) ฝั่ง Python มี deadman — คำสั่งหนึ่งจังหวะมีอายุจำกัด ขาดสายแล้วหยุดเอง
#   2) ต้องถือสิทธิ์ PILOT + กดปลดล็อกบนแท็บเล็ตก่อน
#   3) เพดานความเร็วต่ำกว่าคอกพิต
# ถ้าจะปิดฟีเจอร์นี้: ทำให้เซ็ตนี้ว่าง แล้วทุกอย่างที่เหลือยังทำงานปกติ
MOVE_COMMANDS = frozenset({"move"})
ALL_COMMANDS = PILOT_COMMANDS | SAFE_COMMANDS | MOVE_COMMANDS


# ══════════════════════════════════════════════════════════════
#  TelemetryHub — ที่พักข้อมูลระหว่าง Qt main thread กับ HTTP thread
# ══════════════════════════════════════════════════════════════
class TelemetryHub:
    """เก็บ snapshot ล่าสุดต่อลำ แบบ thread-safe

    เขียนจาก Qt main thread (`app._on_telemetry`) อ่านจาก HTTP thread (SSE)
    เก็บ **dict ล้วน** เท่านั้น ห้ามเก็บ Qt object หรือ protobuf message
    (อายุสั้น/ไม่ thread-safe) — ผู้เรียกต้องแปลงเป็น dict ก่อนส่งเข้ามา
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._drones = {}
        self._extra = {}
        self._seq = 0

    def update(self, drone_id, data):
        now = time.monotonic()
        with self._lock:
            d = dict(data)
            d["id"] = int(drone_id)
            d["_t"] = now
            self._drones[int(drone_id)] = d
            self._seq += 1

    def remove(self, drone_id):
        with self._lock:
            if self._drones.pop(int(drone_id), None) is not None:
                self._seq += 1

    def set_extra(self, key, value):
        """ข้อมูลระดับฝูงที่ไม่ผูกกับลำใดลำหนึ่ง (home, geofence, ...)"""
        with self._lock:
            if self._extra.get(key) != value:
                self._extra[key] = value
                self._seq += 1

    def clear(self):
        with self._lock:
            self._drones.clear()
            self._extra.clear()
            self._seq += 1

    def snapshot(self):
        """คืน dict พร้อม serialize — มี `age` วินาทีต่อลำ ให้ฝั่ง tablet
        แยกออกว่าข้อมูลสดหรือค้าง (ข้อมูลค้างที่ดูเหมือนสดคืออันตราย §5)"""
        now = time.monotonic()
        with self._lock:
            drones = []
            for d in self._drones.values():
                e = {k: v for k, v in d.items() if not k.startswith("_")}
                e["age"] = round(now - d["_t"], 1)
                drones.append(e)
            snap = {"seq": self._seq, "drones": drones}
            snap.update(self._extra)
        drones.sort(key=lambda x: x.get("id", 0))
        return snap


# ══════════════════════════════════════════════════════════════
#  SessionStore — PIN pairing + session token
# ══════════════════════════════════════════════════════════════
class SessionStore:
    def __init__(self, pin_ttl=PIN_TTL_S, max_tries=MAX_PIN_TRIES,
                 session_ttl=SESSION_TTL_S):
        self._lock = threading.Lock()
        self._pin = ""
        self._pin_exp = 0.0
        self._tries = 0
        self._tokens = {}
        self._pin_ttl = pin_ttl
        self._max_tries = max_tries
        self._session_ttl = session_ttl

    # ── PIN ──
    def new_pin(self):
        with self._lock:
            self._pin = "%06d" % secrets.randbelow(1_000_000)
            self._pin_exp = time.monotonic() + self._pin_ttl
            self._tries = 0
            return self._pin

    def pin(self):
        """PIN ที่ใช้ได้ตอนนี้ ('' = ไม่มี/หมดอายุ/ถูกล็อก) — ไว้โชว์บน desktop"""
        with self._lock:
            if self._pin and time.monotonic() <= self._pin_exp:
                return self._pin
            return ""

    def pin_seconds_left(self):
        with self._lock:
            if not self._pin:
                return 0
            return max(0, int(self._pin_exp - time.monotonic()))

    def pair(self, pin, label=""):
        """ตรวจ PIN → คืน token ใหม่ หรือ None ถ้าไม่ผ่าน"""
        now = time.monotonic()
        with self._lock:
            if not self._pin or now > self._pin_exp:
                return None
            if self._tries >= self._max_tries:
                return None
            if not hmac.compare_digest(str(pin or ""), self._pin):
                self._tries += 1
                if self._tries >= self._max_tries:
                    self._pin = ""      # ยิงเดา PIN ครบโควตา → ตายทันที
                    self._pin_exp = 0.0
                return None
            tok = secrets.token_urlsafe(32)
            self._tokens[tok] = {
                "label": str(label or "")[:40],
                "since": now,
                "exp": now + self._session_ttl,
            }
            return tok

    # ── session ──
    def valid(self, token):
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            s = self._tokens.get(token)
            if not s:
                return False
            if now > s["exp"]:
                del self._tokens[token]
                return False
            return True

    def kick_all(self):
        """ตัดทุกเครื่องทันที + ฆ่า PIN (spec §3.3)"""
        with self._lock:
            n = len(self._tokens)
            self._tokens.clear()
            self._pin = ""
            self._pin_exp = 0.0
            return n

    def count(self):
        now = time.monotonic()
        with self._lock:
            return sum(1 for s in self._tokens.values() if now <= s["exp"])


# ══════════════════════════════════════════════════════════════
#  ControlToken — ใครสั่งงานได้ (docs/FIELD_TABLET.md §4)
# ══════════════════════════════════════════════════════════════
class ControlToken:
    """มีผู้ถือสิทธิ์สั่งงานได้ **ทีละหนึ่งเท่านั้น** ตลอดเวลา

    กติกา:
      - เริ่มต้น desktop ถือ
      - แท็บเล็ตเครื่องแรกที่ขอ ได้เลย (auto-grant)
      - เครื่องถัดไปเป็น viewer ขอ handoff ได้ ผู้ถืออยู่ต้องอนุมัติ
      - **desktop ยึดคืนได้ตลอดเวลา ไม่ต้องขออนุมัติ** — เครื่องที่มี E-STOP
        และคนคุมยืนอยู่ต้องชนะเสมอ
      - ขาด heartbeat เกิน timeout สิทธิ์เด้งกลับ desktop เอง มิฉะนั้นแท็บเล็ต
        ที่เดินหลุดระยะ WiFi จะถือสิทธิ์ค้าง = สั่งอะไรไม่ได้จากที่ไหนเลย

    การย้าย/คืนสิทธิ์ **ไม่สั่งอะไรโดรนทั้งสิ้น** ย้ายแค่สิทธิ์ออกคำสั่งใหม่
    โดรนทำสิ่งที่ทำค้างอยู่ต่อไปตามปกติ (§3.1 ข้อ 6)
    """

    def __init__(self, timeout=HEARTBEAT_TIMEOUT_S):
        self._lock = threading.Lock()
        self._timeout = timeout
        self._holder = DESKTOP
        self._label = ""
        self._beat = 0.0
        self._pending = None      # (session, label, ts) คำขอ handoff ที่ค้างอยู่
        self._reason = ""
        self._gen = 0             # เปลี่ยนทุกครั้งที่สถานะสิทธิ์ขยับ (ให้ SSE ดัน)

    @property
    def gen(self):
        with self._lock:
            self._expire()
            return self._gen

    # ── ภายใน (ต้องถือ lock แล้ว) ──
    def _expire(self):
        """คืนสิทธิ์ให้ desktop ถ้าผู้ถือขาดการติดต่อ — เรียกต้นทุก operation"""
        if self._holder != DESKTOP and \
                time.monotonic() - self._beat > self._timeout:
            self._holder, self._label = DESKTOP, ""
            self._pending = None
            self._reason = "heartbeat ขาด — สิทธิ์กลับไปที่คอมควบคุม"
            self._gen += 1
            return True
        return False

    # ── อ่านสถานะ ──
    def status(self):
        with self._lock:
            self._expire()
            p = self._pending
            return {
                "holder": self._holder,
                "label": self._label,
                "desktop": self._holder == DESKTOP,
                "pending": ({"label": p[1]} if p else None),
                "reason": self._reason,
                # แท็บเล็ตเทียบ gen เพื่อวาด UI สิทธิ์ใหม่เฉพาะตอนขยับจริง
                # (snapshot ไหลมา 5 ครั้ง/วิ — วาดใหม่ทุกครั้งคือต้นเหตุความหน่วง)
                "gen": self._gen,
            }

    def is_pilot(self, session):
        with self._lock:
            self._expire()
            return bool(session) and self._holder == session

    def desktop_has_control(self):
        with self._lock:
            self._expire()
            return self._holder == DESKTOP

    # ── ย้ายสิทธิ์ ──
    def claim(self, session, label=""):
        """แท็บเล็ตขอสิทธิ์ — ได้เมื่อ desktop ถืออยู่เท่านั้น"""
        with self._lock:
            self._expire()
            if self._holder == session:
                self._beat = time.monotonic()
                return True, "ถืออยู่แล้ว"
            if self._holder != DESKTOP:
                return False, "มีเครื่องอื่นถือสิทธิ์อยู่ — ขอรับช่วงได้"
            self._holder, self._label = session, str(label or "")[:40]
            self._beat = time.monotonic()
            self._pending = None
            self._reason = ""
            self._gen += 1
            return True, "ได้รับสิทธิ์ควบคุม"

    def heartbeat(self, session):
        with self._lock:
            expired = self._expire()
            if self._holder != session:
                return False
            self._beat = time.monotonic()
            return not expired

    def release(self, session):
        """แท็บเล็ตคืนสิทธิ์เอง"""
        with self._lock:
            self._expire()
            if self._holder == session:
                self._holder, self._label = DESKTOP, ""
                self._pending = None
                self._reason = "แท็บเล็ตคืนสิทธิ์แล้ว"
                self._gen += 1
                return True
            return False

    def reclaim_desktop(self, reason="คอมควบคุมยึดสิทธิ์คืน"):
        """desktop ยึดคืน — ทำได้ตลอด ไม่ต้องขออนุมัติจากใคร"""
        with self._lock:
            self._holder, self._label = DESKTOP, ""
            self._pending = None
            self._reason = reason
            self._gen += 1
            return True

    # ── ขอรับช่วงจากเครื่องที่ถืออยู่ ──
    def request_handoff(self, session, label=""):
        with self._lock:
            self._expire()
            if self._holder == DESKTOP:
                return False, "ยังไม่มีใครถือ — กดขอสิทธิ์ได้เลย"
            if self._holder == session:
                return False, "ถืออยู่แล้ว"
            self._pending = (session, str(label or "")[:40], time.monotonic())
            self._gen += 1
            return True, "ส่งคำขอแล้ว รอเครื่องที่ถืออยู่อนุมัติ"

    def approve_handoff(self):
        with self._lock:
            self._expire()
            if not self._pending:
                return False
            s, lb, _ = self._pending
            self._holder, self._label = s, lb
            self._beat = time.monotonic()
            self._pending = None
            self._reason = "รับช่วงสิทธิ์แล้ว"
            self._gen += 1
            return True

    def deny_handoff(self):
        with self._lock:
            if not self._pending:
                return False
            self._pending = None
            self._reason = "คำขอรับช่วงถูกปฏิเสธ"
            self._gen += 1
            return True

    def pending_for(self, session):
        with self._lock:
            self._expire()
            return bool(self._pending) and self._pending[0] == session


# ══════════════════════════════════════════════════════════════
#  RateLimiter — กันยิงคำสั่งรัวจากแท็บเล็ต
# ══════════════════════════════════════════════════════════════
class RateLimiter:
    """token bucket แยกต่อ session

    ใช้กับคำสั่งของ PILOT เท่านั้น — **ห้ามใช้กับ HOLD/E-STOP**
    เพราะการกันไม่ให้คนกดหยุดคือทิศทางที่ผิดของความปลอดภัย
    """

    def __init__(self, burst=CMD_BURST, per_sec=CMD_PER_SEC):
        self._lock = threading.Lock()
        self._burst = float(burst)
        self._rate = float(per_sec)
        self._buckets = {}        # session -> (tokens, last_ts)

    def allow(self, session):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(session, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[session] = (tokens, now)
                return False
            self._buckets[session] = (tokens - 1.0, now)
            return True

    def forget(self, session):
        with self._lock:
            self._buckets.pop(session, None)

    def clear(self):
        with self._lock:
            self._buckets.clear()


# ══════════════════════════════════════════════════════════════
#  HTTP handler
# ══════════════════════════════════════════════════════════════
def _host_header_ok(raw):
    """Host ต้องเป็น IP หรือ localhost เท่านั้น — ชื่อโดเมน = ปฏิเสธ

    กัน DNS rebinding: เว็บภายนอกตั้งโดเมนให้ resolve มาที่ IP วง LAN
    แล้วหลอกให้บราวเซอร์ของคนหน้างานยิงคำขอเข้ามาแทนตัวเอง
    """
    h = (raw or "").strip()
    if not h:
        return False
    if h.startswith("["):                 # IPv6 ในวงเล็บ [::1]:8760
        end = h.find("]")
        if end < 0:
            return False
        h = h[1:end]
    else:
        h = h.split(":")[0]
    if h == "localhost":
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


class _FieldHandler(BaseHTTPRequestHandler):
    server_version = "SwarmGodField/1.0"
    protocol_version = "HTTP/1.1"

    # โฟลเดอร์ใน assets/ ที่ยอมให้เสิร์ฟ (allowlist — ไม่เปิดทั้ง assets)
    _ASSET_DIRS = ("tablet/", "leaflet/")

    def log_message(self, *a):
        pass

    # ── routing ──
    def do_GET(self):
        if not _host_header_ok(self.headers.get("Host")):
            return self._fail(421, "bad host")
        path = self.path.split("?")[0]
        if path in ("/", "/tablet.html"):
            return self._serve_asset("tablet.html", "text/html; charset=utf-8")
        if any(path.startswith("/" + d) for d in self._ASSET_DIRS):
            return self._serve_asset(path.lstrip("/"), self._mime(path))
        if path.startswith("/tile/"):
            return self._serve_tile(path)
        if path == "/api/hello":
            tok = self._cookie_token()
            return self._json(200, {
                "paired": self._authed(),
                "phase": 2,
                "control": self.server.control.status(),
                "pilot": self.server.control.is_pilot(tok),
                "commands": sorted(PILOT_COMMANDS),
                "safe_commands": sorted(SAFE_COMMANDS),
                # แท็บเล็ตใช้ตัดสินว่าจะโชว์แท็บ MOVE ไหม — ถ้าโปรเจกต์ปิด
                # บังคับสดผ่านเว็บ ลิสต์นี้ว่าง แล้วหน้าเว็บซ่อนแท็บให้เอง
                "move_commands": sorted(MOVE_COMMANDS),
            })
        if path == "/api/state":
            if not self._authed():
                return self._fail(401, "not paired")
            return self._json(200, self._snapshot_for(self._cookie_token()))
        if path == "/api/events":
            return self._serve_events()
        self._fail(404, "not found")

    def do_POST(self):
        if not _host_header_ok(self.headers.get("Host")):
            return self._fail(421, "bad host")
        path = self.path.split("?")[0]
        if path == "/api/pair":
            return self._pair()
        if path == "/api/control":
            return self._control()
        if path == "/api/command":
            return self._command()
        # ทุกอย่างนอกเหนือจากนี้คือ 404 — ไม่มีทางลัดอื่นเข้าถึงคำสั่ง
        self._fail(404, "not found")

    # ── สิทธิ์ควบคุม (§4) ──
    def _control(self):
        if not self._authed():
            return self._fail(401, "not paired")
        body = self._body()
        if body is None:
            return self._fail(400, "bad json")
        tok = self._cookie_token()
        ctl = self.server.control
        act = str(body.get("action") or "")
        label = body.get("label") or ""

        gen_before = ctl.gen
        if act == "claim":
            ok, msg = ctl.claim(tok, label)
        elif act == "release":
            ok, msg = ctl.release(tok), "คืนสิทธิ์แล้ว"
        elif act == "beat":
            ok, msg = ctl.heartbeat(tok), ""
        elif act == "request":
            ok, msg = ctl.request_handoff(tok, label)
        elif act in ("approve", "deny"):
            # อนุมัติ/ปฏิเสธคำขอรับช่วงได้เฉพาะเครื่องที่ถือสิทธิ์อยู่จริง
            if not ctl.is_pilot(tok):
                return self._fail(403, "not the pilot")
            ok = ctl.approve_handoff() if act == "approve" else ctl.deny_handoff()
            msg = "" if ok else "ไม่มีคำขอค้างอยู่"
        else:
            return self._fail(400, "unknown control action")

        # แจ้ง desktop เฉพาะตอนสถานะ "ขยับจริง" — heartbeat เข้ามาทุก 2 วินาที
        # ถ้าแจ้งทุกครั้งจะวาดแบนเนอร์บนคอกพิตใหม่ตลอดเวลา (กะพริบ + เปลืองเปล่า)
        if ctl.gen != gen_before:
            self.server.on_control_change()
        return self._json(200 if ok else 409,
                          {"ok": bool(ok), "message": msg,
                           "control": ctl.status(),
                           "pilot": ctl.is_pilot(tok)})

    # ── สั่งงาน (§3.2) ──
    def _command(self):
        if not self._authed():
            return self._fail(401, "not paired")
        body = self._body()
        if body is None:
            return self._fail(400, "bad json")
        tok = self._cookie_token()
        action = str(body.get("action") or "")

        # 1) allowlist — ของที่ไม่ได้อยู่ในลิสต์ถูกปฏิเสธโดยปริยาย
        #    SetYaw/ChangeSpeed และของใหม่ที่ยังไม่ได้พิจารณา ตกด่านนี้เสมอ
        if action not in ALL_COMMANDS:
            return self._fail(403, "command not allowed over the web")

        # 2) HOLD/E-STOP/หยุดบังคับสด: ใครก็กดได้ ไม่ต้องถือสิทธิ์ ไม่ติด rate limit
        if action in SAFE_COMMANDS:
            ok, msg = self.server.on_command(tok, action, body.get("params") or {})
            return self._json(200 if ok else 409, {"ok": bool(ok), "message": msg})

        # 3) คำสั่งที่เหลือต้องถือสิทธิ์
        ctl = self.server.control
        if not ctl.is_pilot(tok):
            st = ctl.status()
            return self._fail(403, "สิทธิ์ควบคุมอยู่ที่ %s — กดขอสิทธิ์ก่อน"
                              % ("คอมควบคุม" if st["desktop"]
                                 else (st.get("label") or "อีกเครื่อง")))

        # 4) rate limit — บังคับสดใช้ถังแยก เพราะต้องยิงซ้ำถี่กว่าคำสั่ง mission มาก
        #    ถ้าใช้ถังเดียวกัน กดค้างไม่กี่วินาทีก็กินโควตาจนกด LAND ไม่ออก
        if action in MOVE_COMMANDS:
            if not self.server.move_limiter.allow(tok):
                return self._fail(429, "move too fast")
        elif not self.server.limiter.allow(tok):
            return self._fail(429, "too many commands — slow down")

        ok, msg = self.server.on_command(tok, action, body.get("params") or {})
        return self._json(200 if ok else 409, {"ok": bool(ok), "message": msg})

    def _snapshot_for(self, token):
        snap = self.server.hub.snapshot()
        ctl = self.server.control
        snap["control"] = ctl.status()
        snap["pilot"] = ctl.is_pilot(token)
        snap["handoff_for_me"] = ctl.pending_for(token)
        return snap

    # ── auth ──
    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v.strip()
        return ""

    def _authed(self):
        return self.server.sessions.valid(self._cookie_token())

    def _body(self):
        """อ่าน JSON body แบบจำกัดขนาด — คืน None ถ้าไม่ผ่าน"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            obj = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _pair(self):
        body = self._body()
        if body is None:
            return self._fail(400, "bad body")
        tok = self.server.sessions.pair(body.get("pin"), body.get("label"))
        if not tok:
            # ไม่บอกว่าผิดเพราะอะไร (PIN ผิด/หมดอายุ/ถูกล็อก) — ลดข้อมูลให้คนเดา
            return self._fail(403, "pair failed")
        self.server.on_pair(body.get("label") or "")
        payload = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header(
            "Set-Cookie",
            "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
            % (COOKIE_NAME, tok, int(SESSION_TTL_S)))
        self.end_headers()
        self.wfile.write(payload)

    # ── SSE telemetry ──
    def _serve_events(self):
        if not self._authed():
            return self._fail(401, "not paired")
        srv = self.server
        if not srv.claim_stream():
            return self._fail(503, "too many streams")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            period = 1.0 / max(0.5, srv.rate_hz)
            tok = self._cookie_token()
            last_seq, last_write = None, 0.0
            while not srv.stopping.is_set():
                snap = self._snapshot_for(tok)
                now = time.monotonic()
                # สถานะสิทธิ์ต้องถึงแท็บเล็ตทันทีเท่ากับ telemetry — ไม่งั้น
                # เครื่องที่เพิ่งเสียสิทธิ์จะยังโชว์ว่าตัวเองคุมอยู่
                key = (snap["seq"], srv.control.gen)
                if key != last_seq:
                    last_seq = key
                    last_write = now
                    blob = json.dumps(snap, separators=(",", ":"))
                    self.wfile.write(("data: %s\n\n" % blob).encode())
                    self.wfile.flush()
                elif now - last_write > 10.0:
                    last_write = now       # keepalive กันบราวเซอร์ตัดสาย
                    self.wfile.write(b": ka\n\n")
                    self.wfile.flush()
                srv.stopping.wait(period)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass                            # tablet เดินออกนอกระยะ = ปกติ
        finally:
            srv.release_stream()

    # ── static / tiles ──
    def _serve_asset(self, rel, mime):
        root = os.path.abspath(self.server.assets_dir)
        full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
        # กัน path traversal — ".." ต้องพาออกนอก assets/ ไม่ได้
        try:
            outside = os.path.commonpath([root, full]) != root
        except ValueError:                  # คนละไดรฟ์บน Windows
            outside = True
        if outside or not os.path.isfile(full):
            return self._fail(404, "not found")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_tile(self, path):
        tiles = self.server.tiles
        if tiles is None:
            return self._fail(404, "no tile server")
        parts = path.strip("/").split("/")   # tile, layer, z, x, y.png
        if len(parts) != 5:
            return self._fail(400, "bad tile path")
        _, layer, z, x, yp = parts
        try:
            data = tiles.get_tile(layer, z, x, yp.replace(".png", ""))
        except Exception:
            return self._fail(404, "tile error")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    # ── helpers ──
    def _json(self, code, obj):
        payload = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _fail(self, code, msg):
        payload = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _mime(path):
        if path.endswith(".css"):
            return "text/css; charset=utf-8"
        if path.endswith(".js"):
            return "application/javascript; charset=utf-8"
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        if path.endswith(".png"):
            return "image/png"
        if path.endswith(".svg"):
            return "image/svg+xml"
        if path.endswith(".ttf"):
            return "font/ttf"
        return "application/octet-stream"


# ══════════════════════════════════════════════════════════════
#  FieldServer
# ══════════════════════════════════════════════════════════════
class FieldServer(ThreadingHTTPServer):
    daemon_threads = True

    # POSIX: SO_REUSEADDR แค่ข้าม TIME_WAIT ของ socket ที่ปิดไปแล้ว แย่ง listener
    #   ที่ยังทำงานอยู่ไม่ได้ → เปิดไว้ เพื่อให้กดปิด/เปิด LAN ซ้ำ ๆ ไม่ติดพอร์ตค้าง
    # Windows: SO_REUSEADDR **แย่งพอร์ตที่มีคนใช้อยู่ได้จริง** ตัวที่เปิดทีหลังจะ
    #   ดูเหมือนเปิดสำเร็จ แต่คำขอยังวิ่งไปหาตัวเก่า → แท็บเล็ตอาจไปคุยกับคอกพิต
    #   คนละตัวที่คุมโดรนคนละชุด โดยไม่มีอะไรฟ้อง จึงปิดไว้ ให้ bind ซ้ำล้มเหลว
    #   เสียงดังแทน (ผู้ใช้เห็นข้อความ "เปิดไม่สำเร็จ" ที่กล่อง Field Tablet)
    allow_reuse_address = (os.name != "nt")

    def __init__(self, assets_dir, hub, sessions, tiles=None,
                 host="0.0.0.0", port=DEFAULT_PORT, rate_hz=DEFAULT_RATE_HZ):
        super().__init__((host, port), _FieldHandler)
        self.assets_dir = assets_dir
        self.hub = hub
        self.sessions = sessions
        self.tiles = tiles
        self.rate_hz = rate_hz
        self.stopping = threading.Event()
        self.control = ControlToken()
        self.limiter = RateLimiter()
        self.move_limiter = RateLimiter(burst=MOVE_BURST, per_sec=MOVE_PER_SEC)
        self.on_pair = lambda label: None   # callback ให้ desktop โชว์ว่ามีใครเข้า
        self.on_control_change = lambda: None
        # ค่าเริ่มต้นปฏิเสธทุกคำสั่ง — ผู้ฝัง (app.py) ต้องตั้งทับเอง
        # ถ้าลืมตั้ง เว็บจะสั่งอะไรไม่ได้เลย ซึ่งเป็นทิศทางที่ปลอดภัย
        self.on_command = lambda session, action, params: (
            False, "ยังไม่ได้ต่อกับคอกพิต")
        self._streams = 0
        self._slock = threading.Lock()
        self._started = False

    # ── โควตา SSE (แต่ละเส้นกิน 1 thread ค้างไว้) ──
    def claim_stream(self):
        with self._slock:
            if self._streams >= MAX_STREAMS:
                return False
            self._streams += 1
            return True

    def release_stream(self):
        with self._slock:
            self._streams = max(0, self._streams - 1)

    @property
    def stream_count(self):
        with self._slock:
            return self._streams

    def handle_error(self, request, client_address):
        """สายขาดกลางคันไม่ใช่ข้อผิดพลาด — บราวเซอร์ยกเลิก tile ที่ค้างอยู่ทุกครั้ง
        ที่ผู้ใช้เลื่อน/ซูมแผนที่ ปล่อยให้ traceback ขึ้นคือถล่ม console ของ
        คอกพิตด้วยเรื่องปกติ จนกลบข้อความที่สำคัญจริง
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    @property
    def port(self):
        return self.server_address[1]

    def urls(self):
        """URL ที่พิมพ์บน tablet ได้ — ไว้โชว์บนหน้าจอ desktop"""
        p = self.port
        out = ["http://%s:%d" % (ip, p) for ip in lan_ips()]
        return out or ["http://127.0.0.1:%d" % p]

    def start(self):
        t = threading.Thread(target=self.serve_forever, daemon=True,
                             name="field-server")
        self._started = True
        t.start()
        return self.urls()

    def stop(self):
        """ปิดจริง — socket หายไป ไม่ใช่แค่ปฏิเสธคำขอ

        เรียกซ้ำได้ และเรียกทั้งที่ยังไม่เคย start() ก็ได้
        (สำคัญ: `shutdown()` ของ socketserver **บล็อกถาวร** ถ้า `serve_forever()`
        ไม่เคยถูกเรียก — เมธอดนี้ถูกเรียกจาก Qt main thread ตอนกดปิด LAN
        ถ้าบล็อกตรงนั้นคือคอกพิตค้างทั้งตัว)
        """
        self.stopping.set()          # ปลุก SSE loop ทุกเส้นให้ออก
        if self._started:
            self._started = False
            try:
                self.shutdown()
            except Exception:
                pass
        try:
            self.server_close()
        except Exception:
            pass


def lan_ips():
    """IP ของเครื่องในวง LAN (ไม่รวม loopback)"""
    found = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # ไม่ได้ส่งแพ็กเก็ตจริง แค่ให้ OS เลือก interface ที่ใช้ออกนอก
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            found.append(ip)
    except Exception:
        pass
    finally:
        s.close()
    if not found:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                ip = info[4][0]
                if ip and not ip.startswith("127.") and ip not in found:
                    found.append(ip)
        except Exception:
            pass
    return found
