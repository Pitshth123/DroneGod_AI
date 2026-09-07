"""
preflight.py — ตรรกะ "ทดสอบก่อนบินจริง" (ไม่มี Qt ในไฟล์นี้ จึงเทสได้แบบ headless)

แยกเป็น 4 ส่วน:
  1) evaluate_static()  — ตรวจจาก snapshot ของ cockpit (telemetry + ค่าตั้ง) ทันที ไม่ยิง RPC
  2) LIVE_CHECKS        — รายการตรวจที่ต้องยิงคำสั่งจริงผ่าน core (ผู้เรียกเป็นคนยิง)
  3) parse_checklist()  — อ่านเช็คลิสต์ของ Codex (docs/REAL_FLIGHT_CHECKLIST.md)
  4) PreflightState     — จำว่า 2 ปุ่มรันไปแล้วหรือยัง / ผ่านไหม / หมดอายุยัง

หลักการ: fail-closed ในเรื่องที่ทำให้ตกหรือชน (GPS/แบต/ระยะห่าง/คำสั่งถูกล็อก)
และ warn ในเรื่องที่ "ควรมี" แต่ไม่ถึงกับห้ามบิน (geofence, home, link quality)
"""
from dataclasses import dataclass, field
from typing import List, Optional
import hashlib
import json
import os
import re
import time

# ── สถานะของแต่ละรายการ ──
PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"
PENDING = "pending"

# ── ความรุนแรง: critical = ไม่ผ่านแล้วถือว่า "เทสไม่ผ่าน" ──
CRITICAL = "critical"
ADVISORY = "advisory"

GROUPS = ("LINK", "READY", "SEPARATION", "CONFIG", "COMMAND")

GROUP_TITLE = {
    "LINK": "การเชื่อมต่อ / สิทธิ์",
    "READY": "ความพร้อมรายลำ",
    "SEPARATION": "ระยะห่างกันชน",
    "CONFIG": "ค่าตั้งการบิน",
    "COMMAND": "เส้นทางคำสั่ง + โหมด",
}

# ค่าเกณฑ์เริ่มต้น — override ได้ผ่าน snapshot["limits"]
DEFAULTS = {
    "telemetry_age_s": 3.0,     # telemetry เก่ากว่านี้ = ถือว่าไม่สด
    "min_sats": 6,              # ดาวเทียมขั้นต่ำก่อนบิน
    "min_batt_pct": 40.0,       # แบตขั้นต่ำ (critical)
    "warn_batt_pct": 55.0,      # ต่ำกว่านี้เตือน
    "min_link_quality": 40,     # link quality ต่ำกว่านี้เตือน
    "ground_alt_m": 1.5,        # สูงกว่านี้ = ถือว่าไม่ได้อยู่บนพื้น
    "max_takeoff_alt_m": 60.0,  # สูงกว่านี้เตือน (ไม่ห้าม)
    "min_rtl_base_m": 10.0,
}


@dataclass
class CheckResult:
    key: str
    title: str
    group: str
    severity: str = CRITICAL
    status: str = PENDING
    detail: str = ""

    @property
    def blocking(self) -> bool:
        """ไม่ผ่านชนิดที่ควรห้ามบิน"""
        return self.status == FAIL and self.severity == CRITICAL


def _res(key, title, group, severity, status, detail=""):
    return CheckResult(key=key, title=title, group=group, severity=severity,
                       status=status, detail=detail)


def _lim(snap, name):
    return (snap.get("limits") or {}).get(name, DEFAULTS[name])


def _names(drones, ids):
    by_id = {int(d["id"]): d for d in drones}
    out = []
    for did in ids:
        d = by_id.get(int(did), {})
        out.append(f"D{did}" + (" " + str(d["name"]) if d.get("name") else ""))
    return ", ".join(out)


# ══════════════════════════════════════════════════════════════
#  1) การตรวจแบบทันที (ไม่ยิง RPC)
# ══════════════════════════════════════════════════════════════
def evaluate_static(snap: dict) -> List[CheckResult]:
    """snap = สรุปสถานะ cockpit ณ วินาทีนั้น (ดู GroundStation._preflight_snapshot)

    คืนผลเรียงตามลำดับที่จะแสดงใน popup
    """
    out: List[CheckResult] = []
    drones = list(snap.get("drones") or [])

    # ── LINK ────────────────────────────────────────────────
    if not drones:
        out.append(_res("link.core", "เชื่อมต่อ core + มีโดรนเป็นเป้าหมาย", "LINK",
                        CRITICAL, FAIL, "ยังไม่มีโดรนที่เลือกไว้เป็นเป้าหมาย"))
        return out

    profile = (snap.get("profile") or "").lower()
    real = profile in ("production", "hil")
    setup = profile == "setup"
    core_bits = ["mTLS" if snap.get("mtls") else "insecure",
                 "token" if snap.get("token") else "no-token",
                 "profile=" + (profile or "ไม่ระบุ")]
    core_bad = []
    if not snap.get("mtls"):
        core_bad.append("ช่อง gRPC ไม่ได้เข้ารหัส (ไม่มี cert)")
    if setup:
        core_bad.append("โหมด SETUP อ่าน telemetry ได้เท่านั้น — ตั้ง SWARMGOD_HOME_LOC แล้ว restart Core ก่อนบิน")
    if real and not snap.get("token"):
        core_bad.append("โหมดบินจริงต้องมี SWARMGOD_TOKEN")
    if real and not snap.get("home_loc"):
        core_bad.append("ยังไม่ตั้ง SWARMGOD_HOME_LOC เป็นพิกัดสนามจริง")
    out.append(_res(
        "link.core", "core + mTLS + token + profile", "LINK", CRITICAL,
        FAIL if core_bad else PASS,
        " · ".join(core_bad) if core_bad else " · ".join(core_bits)))

    stale = [d["id"] for d in drones
             if float(d.get("age_s", 99)) > _lim(snap, "telemetry_age_s")]
    out.append(_res(
        "link.telemetry", "telemetry สดครบ %d ลำ" % len(drones), "LINK", CRITICAL,
        FAIL if stale else PASS,
        "ขาดการติดต่อ: " + _names(drones, stale) if stale
        else "ทุกลำอัปเดตภายใน %.0f วิ" % _lim(snap, "telemetry_age_s")))

    weak = [d["id"] for d in drones
            if int(d.get("link_quality", 0)) < _lim(snap, "min_link_quality")]
    out.append(_res(
        "link.quality", "คุณภาพ datalink", "LINK", ADVISORY,
        WARN if weak else PASS,
        "อ่อนกว่า %d%%: %s" % (_lim(snap, "min_link_quality"), _names(drones, weak))
        if weak else "ทุกลำเหนือเกณฑ์"))

    out.append(_res(
        "link.control", "สวิตช์ CONTROL อยู่ที่ UI (คำสั่งไม่ถูกล็อก)", "LINK", CRITICAL,
        PASS if snap.get("ui_mode", True) else FAIL,
        "UI" if snap.get("ui_mode", True)
        else "REMOTE เปิดอยู่ — คำสั่งบินจาก cockpit ถูกล็อกทั้งหมด"))

    # ── READY (รายลำ) ───────────────────────────────────────
    bad_gps = []
    for d in drones:
        if int(d.get("gps_fix", 0)) < 3 or int(d.get("sats", 0)) < _lim(snap, "min_sats"):
            bad_gps.append("D%s(fix%s/%ssat)" % (d["id"], d.get("gps_fix", 0),
                                                 d.get("sats", 0)))
    out.append(_res(
        "ready.gps", "GPS 3D fix + ดาวเทียม ≥ %d" % _lim(snap, "min_sats"),
        "READY", CRITICAL, FAIL if bad_gps else PASS,
        ", ".join(bad_gps) if bad_gps else "ครบทุกลำ"))

    low, warn_b = [], []
    for d in drones:
        pct = float(d.get("batt_pct", 0.0))
        if pct < _lim(snap, "min_batt_pct"):
            low.append("D%s %.0f%%" % (d["id"], pct))
        elif pct < _lim(snap, "warn_batt_pct"):
            warn_b.append("D%s %.0f%%" % (d["id"], pct))
    out.append(_res(
        "ready.battery", "แบตเตอรี่ ≥ %.0f%%" % _lim(snap, "min_batt_pct"),
        "READY", CRITICAL, FAIL if low else (WARN if warn_b else PASS),
        ("ต่ำกว่าเกณฑ์: " + ", ".join(low)) if low
        else ("ใกล้เกณฑ์: " + ", ".join(warn_b)) if warn_b else "ทุกลำเพียงพอ"))

    armed = [d["id"] for d in drones if d.get("armed")]
    # Relative altitude comes from the FC's barometer/home origin and commonly
    # drifts a few metres while a disarmed aircraft is physically on the bench.
    # A fresh disarmed state is authoritative for the command test; retain an
    # altitude warning so the operator still sees an unexpected origin offset.
    high_disarmed = [d["id"] for d in drones
                     if not d.get("armed")
                     and float(d.get("alt", 0.0)) > _lim(snap, "ground_alt_m")]
    out.append(_res(
        "ready.grounded", "ทุกลำ disarm และอยู่บนพื้น (ก่อนเริ่มเทส)", "READY", CRITICAL,
        FAIL if armed else (WARN if high_disarmed else PASS),
        "ยัง armed อยู่: " + _names(drones, armed) if armed
        else ("disarm ครบ แต่ alt_rel สูงกว่าเกณฑ์ (barometer/home drift): "
              + _names(drones, high_disarmed)) if high_disarmed
        else "disarm และค่าความสูงอยู่ในเกณฑ์พื้นครบ"))

    no_pos = [d["id"] for d in drones
              if float(d.get("lat", 0.0)) == 0.0 and float(d.get("lon", 0.0)) == 0.0]
    out.append(_res(
        "ready.position", "มีพิกัดจริงจากทุกลำ", "READY", CRITICAL,
        FAIL if no_pos else PASS,
        "พิกัด 0,0: " + _names(drones, no_pos) if no_pos else "ครบทุกลำ"))

    no_home = [d["id"] for d in drones if not d.get("home_set")]
    out.append(_res(
        "ready.home", "จุด home ถูกบันทึกแล้ว (ใช้ตอน RTL)", "READY", ADVISORY,
        WARN if no_home else PASS,
        "ยังไม่มี home: " + _names(drones, no_home) if no_home else "ครบทุกลำ"))

    # ── SEPARATION ──────────────────────────────────────────
    out.append(_check_separation(snap, drones))

    # ── CONFIG ──────────────────────────────────────────────
    alt = float(snap.get("takeoff_alt", 0.0))
    max_alt = _lim(snap, "max_takeoff_alt_m")
    if alt < 2.0:
        st, msg = FAIL, "%.0f m ต่ำเกินไป" % alt
    elif alt > max_alt:
        st, msg = WARN, "%.0f m สูงกว่าเกณฑ์เตือน %.0f m" % (alt, max_alt)
    else:
        st, msg = PASS, "%.0f m" % alt
    out.append(_res("cfg.takeoff_alt", "ความสูง TAKEOFF ที่ตั้งไว้", "CONFIG",
                    CRITICAL if st == FAIL else ADVISORY, st, msg))

    base = float(snap.get("rtl_base", 0.0))
    gap = float(snap.get("rtl_gap", 0.0))
    rtl_bad = base < _lim(snap, "min_rtl_base_m") or gap < 3.0
    out.append(_res(
        "cfg.rtl", "ค่า RTL (ชั้นความสูง + ระยะห่างชั้น)", "CONFIG", ADVISORY,
        WARN if rtl_bad else PASS,
        "base %.0f m · gap %.0f m%s" % (base, gap,
                                        " — ต่ำกว่าที่แนะนำ" if rtl_bad else "")))

    fence_n = int(snap.get("geofence_points", 0))
    out.append(_res(
        "cfg.geofence", "Geofence ถูกบังคับใช้", "CONFIG", ADVISORY,
        PASS if fence_n >= 3 else WARN,
        "%d จุด" % fence_n if fence_n >= 3
        else "ยังไม่ได้ตั้ง — เหลือแค่เพดาน/รัศมีจาก core"))

    head = int(snap.get("head_id", 0) or 0)
    online = {int(d["id"]) for d in drones
              if float(d.get("age_s", 99)) <= _lim(snap, "telemetry_age_s")}
    if len(drones) <= 1:
        out.append(_res("cfg.head", "หัวขบวน (Head)", "CONFIG", ADVISORY, SKIP,
                        "บินลำเดียว — ไม่ต้องมีหัวขบวน"))
    elif head and head in online:
        out.append(_res("cfg.head", "หัวขบวน (Head) ออนไลน์", "CONFIG", CRITICAL, PASS,
                        "Head = D%d" % head))
    else:
        out.append(_res("cfg.head", "หัวขบวน (Head) ออนไลน์", "CONFIG", CRITICAL, FAIL,
                        "Head = D%d ไม่ออนไลน์" % head if head
                        else "ยังไม่ได้ตั้งหัวขบวน"))

    conflicts = list(snap.get("waypoint_conflicts") or [])
    if not snap.get("waypoint_planned"):
        out.append(_res("cfg.waypoint", "เส้นทาง Waypoint ไม่ตัดกัน", "CONFIG", ADVISORY,
                        SKIP, "ยังไม่ได้วางเส้นทาง"))
    else:
        out.append(_res(
            "cfg.waypoint", "เส้นทาง Waypoint ไม่ตัดกัน", "CONFIG", CRITICAL,
            FAIL if conflicts else PASS,
            "คู่เสี่ยงชน: " + ", ".join(str(c) for c in conflicts) if conflicts
            else "ตรวจแล้วไม่มีคู่เสี่ยง"))
    return out


def _check_separation(snap, drones) -> CheckResult:
    pairs = list(snap.get("close_pairs") or [])       # [(a, b, dist_m, level)]
    min_sep = float(snap.get("min_sep", 6.0))
    title = "ระยะห่างบนพื้น ≥ %.0f m" % min_sep
    if len(drones) < 2:
        return _res("sep.spacing", "ระยะห่างระหว่างลำบนพื้น", "SEPARATION", ADVISORY,
                    SKIP, "บินลำเดียว")
    crit = [p for p in pairs if p[3] == "critical"]
    warn = [p for p in pairs if p[3] != "critical"]
    if crit:
        return _res("sep.spacing", title, "SEPARATION", CRITICAL, FAIL,
                    "ใกล้เกินไป: " + ", ".join("D%s-D%s %.1fm" % (a, b, d)
                                               for a, b, d, _ in crit))
    if warn:
        return _res("sep.spacing", title, "SEPARATION", ADVISORY, WARN,
                    "ชิด: " + ", ".join("D%s-D%s %.1fm" % (a, b, d)
                                        for a, b, d, _ in warn))
    return _res("sep.spacing", title, "SEPARATION", ADVISORY, PASS, "ทุกคู่ห่างพอ")


# ══════════════════════════════════════════════════════════════
#  2) รายการตรวจที่ต้องยิงคำสั่งจริง
# ══════════════════════════════════════════════════════════════
@dataclass
class LiveCheck:
    key: str
    title: str
    group: str = "COMMAND"
    severity: str = CRITICAL
    bench_only: bool = False        # ต้องถอดใบพัดก่อนถึงจะรัน
    note: str = ""


# ลำดับสำคัญ: จบด้วย GUIDED เสมอ เพราะเป็นโหมดที่ TAKEOFF ต้องใช้
LIVE_CHECKS = [
    LiveCheck("cmd.hold", "สั่ง HOLD แล้ว core ตอบรับ",
              severity=ADVISORY,
              note="เส้นทางหยุด/ลอยค้างระดับคำสั่งใช้งานได้"),
    LiveCheck("cmd.mode_guided", "สั่งเปลี่ยนโหมด GUIDED แล้ว FC ตอบรับจริง",
              note="พิสูจน์ว่าเส้นทางคำสั่ง cockpit → core → FC → telemetry ครบวง"),
    LiveCheck("cmd.reject_unconfirmed", "TAKEOFF ที่ไม่ยืนยันถูก core ปฏิเสธ",
              bench_only=True,
              note="ยิง Takeoff(confirmed=false) — core ต้องไม่ส่งต่อให้ FC"),
    LiveCheck("cmd.arm_disarm", "ARM ผ่าน pre-arm แล้ว DISARM กลับได้",
              bench_only=True,
              note="ถอดใบพัดแล้วเท่านั้น — มอเตอร์จะหมุนจริง"),
]


def live_checks(bench: bool) -> List[LiveCheck]:
    """bench=False → ข้ามรายการที่ต้องถอดใบพัด (แสดงเป็น 'ข้าม' ไม่ใช่ 'ไม่ผ่าน')"""
    return list(LIVE_CHECKS) if bench else [c for c in LIVE_CHECKS if not c.bench_only]


# ══════════════════════════════════════════════════════════════
#  สรุปผล
# ══════════════════════════════════════════════════════════════
@dataclass
class Summary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    warned: int = 0
    skipped: int = 0
    blocking: int = 0

    @property
    def ok(self) -> bool:
        """ผ่าน = ไม่มีข้อ critical ที่ fail (warn/skip ไม่บล็อก)"""
        return self.blocking == 0 and self.total > 0

    def text(self) -> str:
        return ("ผ่าน %d · เตือน %d · ไม่ผ่าน %d · ข้าม %d"
                % (self.passed, self.warned, self.failed, self.skipped))


def summarize(results) -> Summary:
    s = Summary()
    for r in results:
        if r.status == PENDING:
            continue
        s.total += 1
        if r.status == PASS:
            s.passed += 1
        elif r.status == FAIL:
            s.failed += 1
            if r.severity == CRITICAL:
                s.blocking += 1
        elif r.status == WARN:
            s.warned += 1
        elif r.status == SKIP:
            s.skipped += 1
    return s


# ══════════════════════════════════════════════════════════════
#  3) เช็คลิสต์ของ Codex (docs/REAL_FLIGHT_CHECKLIST.md)
# ══════════════════════════════════════════════════════════════
_ITEM_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s+(.*\S)\s*$")
_SEC_RE = re.compile(r"^##\s+(.*\S)\s*$")


def checklist_path() -> str:
    """docs/REAL_FLIGHT_CHECKLIST.md — ไต่จาก core/ ขึ้นไปที่รากโปรเจค"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "docs", "REAL_FLIGHT_CHECKLIST.md")


def parse_checklist(text: str):
    """คืน [(หัวข้อ, [รายการ, ...]), ...] เฉพาะหัวข้อที่มี checkbox"""
    sections = []
    cur = None
    for line in (text or "").splitlines():
        m = _SEC_RE.match(line)
        if m:
            cur = (m.group(1), [])
            sections.append(cur)
            continue
        m = _ITEM_RE.match(line)
        if m and cur is not None:
            cur[1].append(m.group(2))
    return [(title, items) for title, items in sections if items]


def load_checklist(path: Optional[str] = None):
    path = path or checklist_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_checklist(f.read())
    except OSError:
        return []


def item_key(text: str) -> str:
    """คีย์คงที่ต่อ 1 รายการ — ทนต่อการสลับลำดับ/แก้หัวข้อ"""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def checklist_store_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".swarmgod", "preflight_checklist.json")


def load_checked(path: Optional[str] = None) -> dict:
    """{item_key: epoch ที่ติ๊ก} — เก็บเวลาไว้ด้วยเพื่อบอกว่าติ๊กไว้นานแค่ไหนแล้ว"""
    path = path or checklist_store_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_checked(data: dict, path: Optional[str] = None) -> bool:
    path = path or checklist_store_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True
    except OSError:
        return False


def checklist_progress(sections, checked: dict):
    """(ติ๊กแล้ว, ทั้งหมด) — นับเฉพาะรายการที่ยังอยู่ในเอกสารปัจจุบัน"""
    done = total = 0
    for _title, items in sections:
        for it in items:
            total += 1
            if checked.get(item_key(it)):
                done += 1
    return done, total


# ══════════════════════════════════════════════════════════════
#  4) สถานะ "รันเทสไปแล้วหรือยัง"
# ══════════════════════════════════════════════════════════════
@dataclass
class RunRecord:
    ts: float = 0.0
    ok: bool = False
    detail: str = ""


@dataclass
class PreflightState:
    """จำผลของ 2 ปุ่ม — อยู่ตลอด "การเปิดโปรแกรม 1 ครั้ง"

    `ttl_s = 0` (ค่าเริ่มต้น) = ไม่หมดอายุระหว่างที่โปรแกรมยังเปิดอยู่
    ตัว state เก็บในหน่วยความจำอย่างเดียว ปิดโปรแกรมแล้วหายไปเอง = ต้องเทสใหม่
    (ตั้งค่า ttl_s > 0 ได้ถ้าอยากบังคับให้หมดอายุตามเวลาด้วย)
    """
    ttl_s: float = 0.0
    selftest: RunRecord = field(default_factory=RunRecord)
    checklist: RunRecord = field(default_factory=RunRecord)
    bypassed: bool = False          # ผู้ใช้เคยเลือก "ไม่เทส บินเลย" ในรอบนี้
    bypass_count: int = 0

    # ── บันทึกผล ──
    def mark_selftest(self, ok: bool, detail: str = "", now: float = None):
        self.selftest = RunRecord(now or time.time(), bool(ok), detail)

    def mark_checklist(self, ok: bool, detail: str = "", now: float = None):
        self.checklist = RunRecord(now or time.time(), bool(ok), detail)

    def mark_bypass(self):
        self.bypassed = True
        self.bypass_count += 1

    # ── อ่านสถานะ ──
    def _fresh(self, rec: RunRecord, now: float = None) -> bool:
        if rec.ts <= 0:
            return False
        if self.ttl_s <= 0:
            return True            # อยู่ได้ตลอดรอบที่โปรแกรมเปิดอยู่
        return ((now or time.time()) - rec.ts) <= self.ttl_s

    def selftest_ready(self, now=None) -> bool:
        return self.selftest.ok and self._fresh(self.selftest, now)

    def checklist_ready(self, now=None) -> bool:
        return self.checklist.ok and self._fresh(self.checklist, now)

    def ready(self, now=None) -> bool:
        """ผ่านครบทั้ง 2 ปุ่มและยังไม่หมดอายุ"""
        return self.selftest_ready(now) and self.checklist_ready(now)

    def missing(self, now=None) -> List[str]:
        """รายการสิ่งที่ยังขาด — ใช้เขียนข้อความเตือน"""
        out = []
        if not self.selftest_ready(now):
            if self.selftest.ts <= 0:
                out.append("ยังไม่ได้รัน SYSTEM TEST")
            elif not self.selftest.ok:
                out.append("SYSTEM TEST ยังไม่ผ่าน")
            else:
                out.append("ผล SYSTEM TEST หมดอายุแล้ว")
        if not self.checklist_ready(now):
            if self.checklist.ts <= 0:
                out.append("ยังไม่ได้กรอก CHECKLIST")
            elif not self.checklist.ok:
                out.append("CHECKLIST ยังติ๊กไม่ครบ")
            else:
                out.append("ผล CHECKLIST หมดอายุแล้ว")
        return out

    def badge(self, now=None):
        """(ข้อความ, ระดับ) สำหรับป้ายบน topbar — ระดับ: ok | warn | bad

        ข้อความสั้นคงที่เหมือนป้าย CORE — สถานะอ่านจากสี (เขียว/เหลือง/แดง)
        รายละเอียดว่าขาดอะไรอยู่ใน tooltip และในหมวด PRE-FLIGHT
        """
        if self.ready(now):
            return "● PREFLIGHT", "ok"
        if self.selftest.ts <= 0 and self.checklist.ts <= 0:
            return "⚠ PREFLIGHT", "bad"
        return "● PREFLIGHT", "warn"


def age_text(ts: float, now: float = None) -> str:
    if ts <= 0:
        return "ยังไม่เคยรัน"
    secs = max(0.0, (now or time.time()) - ts)
    if secs < 60:
        return "เมื่อ %d วิที่แล้ว" % int(secs)
    if secs < 3600:
        return "เมื่อ %d นาทีที่แล้ว" % int(secs // 60)
    return "เมื่อ %d ชั่วโมงที่แล้ว" % int(secs // 3600)
