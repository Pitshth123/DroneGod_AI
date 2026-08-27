"""
swarm_logic.py — สมองล้วน ๆ ของฟีเจอร์ swarm (ไม่มี Qt / ไม่มี gRPC)

โมดูลนี้ตั้งใจให้ "framework-free" เพื่อให้ unit-test ได้แบบ headless
ทุกฟังก์ชันเป็น pure function / โครงสร้างข้อมูลเล็ก ๆ — UI ใน app.py เรียกใช้แล้ว
เอาผลไป render/สั่งงานต่อ (ตามหลัก "UI คือกระจก ไม่ใช่สมอง")

ครอบคลุมสเปก:
  1. Head / Leader — เลือกหัว + auto-reassign เมื่อหัวหลุด            → choose_head / next_head_after_loss
  2. Take off 2 โหมด (All / Sequential) + ความสูงต่อลำ                → plan_takeoff / TakeoffStep
  3. Take off ในหน้า Swarm = แนบ Form-up อัตโนมัติ                    → build_swarm_takeoff_ops
  6. กล่องสรุปคำสั่งก่อนบิน (Pre-flight Command Summary)              → CommandSummary
  7. ลำดับการเคลื่อนที่กันชน (Collision-Avoidance Movement Order)     → movement_order
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# ทิศทางที่ตรงกับ RC_DIR_* ใน command.proto (เก็บเป็นสตริงเพื่อความอิสระจาก proto)
DIR_RIGHT = "RIGHT"
DIR_LEFT = "LEFT"
DIR_FWD = "FWD"
DIR_BWD = "BWD"
DIR_UP = "UP"
DIR_DOWN = "DOWN"

# alias ให้เรียกด้วยชื่อยาวได้
_DIR_ALIAS = {
    "FORWARD": DIR_FWD, "FWD": DIR_FWD, "F": DIR_FWD,
    "BACK": DIR_BWD, "BACKWARD": DIR_BWD, "BWD": DIR_BWD, "B": DIR_BWD,
    "RIGHT": DIR_RIGHT, "R": DIR_RIGHT, "EAST": DIR_RIGHT,
    "LEFT": DIR_LEFT, "L": DIR_LEFT, "WEST": DIR_LEFT,
    "UP": DIR_UP, "DOWN": DIR_DOWN,
}

DEFAULT_ALT = 20.0


def _norm_ids(ids: Iterable) -> List[int]:
    """คืน list int ที่ไม่ซ้ำ โดยคงลำดับที่เจอครั้งแรก"""
    out: List[int] = []
    seen = set()
    for i in ids:
        try:
            v = int(i)
        except (TypeError, ValueError):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _priority_key(priority: Optional[Iterable[int]]):
    """คืน key function: เรียงตาม priority ที่ให้มาก่อน แล้วค่อยตาม id น้อย→มาก"""
    plist = _norm_ids(priority) if priority else []
    pindex = {p: idx for idx, p in enumerate(plist)}

    def key(d: int):
        if d in pindex:
            return (0, pindex[d], d)
        return (1, len(plist), d)

    return key


# ══════════════════════════════════════════════════════════════
#  1. HEAD / LEADER + AUTO-REASSIGN (spec 1)
# ══════════════════════════════════════════════════════════════
def choose_head(connected_ids: Iterable[int],
                preferred: Optional[int] = None,
                priority: Optional[Iterable[int]] = None) -> int:
    """เลือกหัวขบวน

    - ถ้า preferred ยังเชื่อมต่ออยู่ → ใช้ preferred (เคารพการเลือกของผู้ใช้)
    - ไม่งั้น → ตัวแรกตาม priority (ดีฟอลต์ = id น้อยสุด)
    - ถ้าไม่มีโดรนเชื่อมต่อเลย → 0
    """
    ids = _norm_ids(connected_ids)
    if not ids:
        return 0
    if preferred is not None and int(preferred) in ids:
        return int(preferred)
    return sorted(ids, key=_priority_key(priority))[0]


def next_head_after_loss(current_head: Optional[int],
                         connected_ids: Iterable[int],
                         priority: Optional[Iterable[int]] = None
                         ) -> Tuple[int, bool]:
    """Auto-Reassign Head (spec 1)

    ถ้าหัวปัจจุบันยังเชื่อมต่ออยู่ → ไม่เปลี่ยน
    ถ้าหัวหลุด (ไม่อยู่ใน connected) → เลื่อนลำถัดไปที่ยังเชื่อมต่อขึ้นเป็นหัว
    (ลำถัดไปตาม priority — ดีฟอลต์คือลำที่ 2 ที่ยังเชื่อมต่อ)

    คืน (head_ใหม่, changed) โดย changed=True ถ้ามีการเปลี่ยนหัว
    """
    ids = _norm_ids(connected_ids)
    ch = int(current_head or 0)
    if ch and ch in ids:
        return ch, False           # หัวยังอยู่ ไม่ต้องทำอะไร
    if not ids:
        return 0, (ch != 0)        # ไม่มีใครเชื่อมต่อแล้ว
    new_head = sorted(ids, key=_priority_key(priority))[0]
    return new_head, (new_head != ch)


# ══════════════════════════════════════════════════════════════
#  2. TAKE OFF PLANNING — All / Sequential (spec 2)
# ══════════════════════════════════════════════════════════════
@dataclass
class TakeoffStep:
    drone_id: int
    alt: float
    order: int          # 0-based; โหมด all ทุกลำ order=0 (พร้อมกัน)

    def __iter__(self):
        # ให้ unpack แบบ (id, alt) ได้สะดวก
        yield self.drone_id
        yield self.alt


def order_drones(ids: Iterable[int], head_id: int = 0,
                 priority: Optional[Iterable[int]] = None) -> List[int]:
    """เรียงโดรน: หัวก่อน (ถ้าอยู่ในชุด) แล้วที่เหลือตาม priority/id"""
    uniq = _norm_ids(ids)
    rest = sorted(uniq, key=_priority_key(priority))
    head = int(head_id or 0)
    if head in rest:
        rest.remove(head)
        return [head] + rest
    return rest


def plan_takeoff(mode: str, drones: Iterable[int], head_id: int = 0,
                 default_alt: float = DEFAULT_ALT,
                 alts: Optional[Dict[int, float]] = None,
                 priority: Optional[Iterable[int]] = None) -> List[TakeoffStep]:
    """วางแผน take off

    mode = 'all'        → ทุกลำขึ้นพร้อมกัน (order=0 ทั้งหมด)
    mode = 'sequential' → หัวก่อน แล้วลูกไล่ตามลำดับ (order 0,1,2,...)

    alts = dict {drone_id: alt} ระบุความสูงรายลำ; ลำที่ไม่ระบุใช้ default_alt (20m)
    คืน list[TakeoffStep] เรียงตามลำดับที่จะสั่ง
    """
    seq = order_drones(drones, head_id, priority)
    if not seq:
        return []
    alts = alts or {}

    def alt_of(d: int) -> float:
        a = alts.get(d)
        try:
            a = float(a)
        except (TypeError, ValueError):
            a = None
        if a is None or a <= 0:
            a = float(default_alt)
        return a

    m = (mode or "").strip().lower()
    if m in ("all", "parallel", "together"):
        return [TakeoffStep(d, alt_of(d), 0) for d in seq]
    # sequential (ดีฟอลต์เมื่อไม่รู้จักโหมด)
    return [TakeoffStep(d, alt_of(d), i) for i, d in enumerate(seq)]


# ══════════════════════════════════════════════════════════════
#  3. SWARM TAKE OFF = แนบ FORM-UP อัตโนมัติ (spec 3)
# ══════════════════════════════════════════════════════════════
@dataclass
class SwarmOp:
    kind: str                       # 'form_up' | 'takeoff'
    payload: dict = field(default_factory=dict)


def build_swarm_takeoff_ops(drones: Iterable[int], head_id: int = 0,
                            default_alt: float = DEFAULT_ALT,
                            spacing: Optional[float] = None,
                            formation: Optional[int] = None,
                            mode: str = "sequential",
                            alts: Optional[Dict[int, float]] = None,
                            priority: Optional[Iterable[int]] = None
                            ) -> List[SwarmOp]:
    """สร้างชุดคำสั่งเมื่อผู้ใช้กด Take off ในหน้า Swarm

    สเปก 3: ต้องแนบ 'Form up' (ดึงค่าทั้งหมดของ swarm) เข้าไปให้อัตโนมัติก่อน take off
    คืนลำดับ: [form_up(config), takeoff step1, takeoff step2, ...]
    """
    ops: List[SwarmOp] = [SwarmOp("form_up", {
        "spacing": spacing,
        "formation": formation,
        "head_id": int(head_id or 0),
    })]
    for step in plan_takeoff(mode, drones, head_id, default_alt, alts, priority):
        ops.append(SwarmOp("takeoff", {
            "drone_id": step.drone_id, "alt": step.alt, "order": step.order}))
    return ops


# ══════════════════════════════════════════════════════════════
#  7. COLLISION-AVOIDANCE MOVEMENT ORDER (spec 7)
# ══════════════════════════════════════════════════════════════
# เวกเตอร์ทิศทางในระนาบ (x=ตะวันออก/east/lon, y=เหนือ/north/lat)
_DIR_VEC = {
    DIR_RIGHT: (1.0, 0.0),
    DIR_LEFT: (-1.0, 0.0),
    DIR_FWD: (0.0, 1.0),
    DIR_BWD: (0.0, -1.0),
}


def movement_order(positions: Dict[int, Tuple[float, float]],
                   direction: str) -> List[int]:
    """จัดคิวลำดับการเคลื่อนที่ไม่ให้เส้นทางทับกัน (spec 7)

    positions : dict {drone_id: (x, y)} โดย x=east(lon), y=north(lat)
                (ใช้ lat/lon ดิบได้เพราะเราสนใจแค่ลำดับ ไม่ใช่ระยะจริง)
    direction : 'RIGHT'|'LEFT'|'FWD'|'BWD' (รับ alias เช่น FORWARD/BACK/EAST/WEST)

    กติกา: "ลำที่อยู่ไกลสุดในทิศที่จะไป ขยับก่อน"
      - ไปขวา  → ลำขวาสุดขยับก่อน แล้วไล่มาซ้าย  (5→4→3→2→1)
      - ไปซ้าย  → ลำซ้ายสุดขยับก่อน แล้วไล่ไปขวา
    ทำให้ตัวหน้าในทิศเดินทางเคลียร์พื้นที่ก่อน ตัวข้างหลังจึงตามไม่ชน

    ทิศขึ้น/ลง (UP/DOWN) หรือทิศที่ไม่รู้จัก → คืนลำดับตาม id (ไม่ต้องจัดคิวแนวราบ)
    """
    ids = _norm_ids(positions.keys())
    d = _DIR_ALIAS.get((direction or "").strip().upper(), (direction or "").strip().upper())
    vec = _DIR_VEC.get(d)
    if vec is None:
        return sorted(ids)
    dx, dy = vec

    def proj(i: int) -> float:
        x, y = positions[i]
        return dx * float(x) + dy * float(y)

    # โปรเจกชันมาก = ไกลสุดในทิศเดินทาง → ไปก่อน (มาก→น้อย); เสมอกัน tie-break ด้วย id
    return sorted(ids, key=lambda i: (-proj(i), i))


def movement_plan(positions: Dict[int, Tuple[float, float]],
                  direction: str,
                  head_id: int = 0) -> List[int]:
    """เหมือน movement_order แต่ยังคงอ้างอิงเป้าหมายจาก Head เป็นหลัก:
    Head ถูกจัดคิวตามตำแหน่งจริงเช่นกัน (ไม่ยกเว้น) เพื่อกันชนจริง
    — ฟังก์ชันนี้มีไว้เผื่อ logic อนาคต ตอนนี้ = movement_order ตรง ๆ
    """
    return movement_order(positions, direction)


# ══════════════════════════════════════════════════════════════
#  GROUP GOTO — คลิกแผนที่แล้วทุกลำที่เลือกบินไปพร้อมกัน
# ══════════════════════════════════════════════════════════════
_M_PER_DEG_LAT = 111320.0


def _m_per_deg_lon(lat: float) -> float:
    return _M_PER_DEG_LAT * max(0.01, math.cos(math.radians(lat)))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """ระยะทางบนผิวโลก (เมตร) ระหว่าง 2 พิกัด"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def group_goto_targets(positions: Dict[int, Tuple[float, float]],
                       target_lat: float, target_lon: float,
                       keep_formation: bool = True
                       ) -> Dict[int, Tuple[float, float]]:
    """คลิกแผนที่ 1 จุด → พิกัดปลายทางของ "ทุกลำที่เลือก"

    positions = {drone_id: (lat, lon)} ของลำที่เลือก (เฉพาะลำที่มี GPS แล้ว)

    keep_formation=True (ดีฟอลต์):
        ย้ายทั้งกลุ่มแบบยกขบวน — หา "จุดกึ่งกลางกลุ่ม" แล้วเลื่อนทุกลำด้วย
        เวกเตอร์เดียวกันไปยังเป้าหมาย → ระยะห่าง/รูปขบวนเดิมคงอยู่เป๊ะ
        และไม่มีลำไหนบินไปทับจุดเดียวกัน
    keep_formation=False:
        ทุกลำมุ่งไปพิกัดเดียวกัน (เสี่ยงชน — ใช้เมื่อมีลำเดียว)

    คืน {drone_id: (lat, lon)}
    """
    ids = _norm_ids(positions.keys())
    if not ids:
        return {}
    if len(ids) == 1 or not keep_formation:
        return {i: (float(target_lat), float(target_lon)) for i in ids}

    # จุดกึ่งกลางกลุ่ม
    clat = sum(positions[i][0] for i in ids) / len(ids)
    clon = sum(positions[i][1] for i in ids) / len(ids)
    dlat = float(target_lat) - clat
    dlon = float(target_lon) - clon
    return {i: (positions[i][0] + dlat, positions[i][1] + dlon) for i in ids}


# ══════════════════════════════════════════════════════════════
#  RTL แบบแยกชั้นความสูง (Altitude Staggering) — กันชนตอนกลับฐาน
# ══════════════════════════════════════════════════════════════
@dataclass
class RtlLayer:
    drone_id: int
    alt: float        # ความสูงของชั้นที่ลำนี้ต้องไปรักษาไว้ (m)
    index: int        # 0 = ชั้นต่ำสุด → ลงจอดเป็นลำแรก

    def __iter__(self):
        yield self.drone_id
        yield self.alt


def plan_rtl_layers(current_alts: Dict[int, float],
                    base_alt: float = 15.0,
                    layer_gap: float = 5.0,
                    priority: Optional[Iterable[int]] = None) -> List[RtlLayer]:
    """แบ่งชั้นความสูงให้โดรนแต่ละลำก่อนบินกลับฐาน (spec 2 — Step 1)

    current_alts = {drone_id: ความสูงปัจจุบัน (m)}
    base_alt     = ความสูงของชั้นล่างสุด
    layer_gap    = ระยะห่างระหว่างชั้น (เช่น 5 m)

    กติกาจัดชั้น: ลำที่ "บินต่ำอยู่แล้ว" ได้ชั้นล่าง, ลำที่บินสูงได้ชั้นบน
    → ทุกลำไต่/ลดระดับน้อยที่สุด และเส้นทางแนวดิ่งไม่ตัดข้ามกัน
    เสมอกันตัดสินด้วย priority (ดีฟอลต์ = id น้อยก่อน)

    คืน list[RtlLayer] เรียงจากชั้นล่างสุด (index 0) ขึ้นไป
    """
    ids = _norm_ids(current_alts.keys())
    if not ids:
        return []
    pkey = _priority_key(priority)

    def alt_of(d):
        try:
            return float(current_alts[d])
        except (TypeError, ValueError):
            return 0.0

    ordered = sorted(ids, key=lambda d: (alt_of(d), pkey(d)))
    gap = max(1.0, float(layer_gap))
    return [RtlLayer(d, float(base_alt) + i * gap, i)
            for i, d in enumerate(ordered)]


def plan_rtl_climb(current_alts: Dict[int, float],
                   offset: float = 2.0,
                   min_gap: Optional[float] = None,
                   priority: Optional[Iterable[int]] = None) -> List[RtlLayer]:
    """แผน RTL แบบ "บวกเพิ่มจากความสูงปัจจุบันของแต่ละลำ"

    กติกาตามที่ผู้ใช้กำหนด:
      ลำที่บินอยู่ 10 m + offset 2 → ไต่ไป 12 m
      ลำที่บินอยู่ 12 m + offset 2 → ไต่ไป 14 m
    ทุกลำไต่ขึ้นจากที่ตัวเองอยู่ ไม่ต้องวิ่งไปที่ระดับกลางร่วมกัน (ไต่น้อย ประหยัดเวลา)

    กันชนเพิ่ม: ถ้าสองลำบินอยู่ระดับ "เท่ากัน" การบวกเท่ากันจะยังชนกันอยู่
    จึงบังคับให้แต่ละชั้นห่างกันอย่างน้อย min_gap (ดีฟอลต์ = offset)
      เช่น 3 ลำอยู่ 20 m เท่ากัน, offset 2 → 22, 24, 26

    คืน list[RtlLayer] เรียงจากชั้นล่างสุดขึ้นไป (index 0 = ต่ำสุด)
    """
    ids = _norm_ids(current_alts.keys())
    if not ids:
        return []
    gap = float(min_gap) if min_gap is not None else float(offset)
    gap = max(0.0, gap)
    off = float(offset)
    pkey = _priority_key(priority)

    def alt_of(d):
        try:
            return float(current_alts[d])
        except (TypeError, ValueError):
            return 0.0

    # เรียงจากลำที่บินต่ำสุดขึ้นไป → ไต่ไม่ตัดข้ามกัน
    ordered = sorted(ids, key=lambda d: (alt_of(d), pkey(d)))
    out: List[RtlLayer] = []
    prev = None
    for i, d in enumerate(ordered):
        target = alt_of(d) + off
        if prev is not None and target < prev + gap:
            target = prev + gap        # บังคับระยะห่างขั้นต่ำ
        prev = target
        out.append(RtlLayer(d, target, i))
    return out


def rtl_landing_order(layers: Iterable[RtlLayer]) -> List[int]:
    """ลำดับลงจอด (spec 2 — Step 3): ชั้นต่ำสุดลงก่อน ไล่ขึ้นไปทีละชั้น"""
    return [l.drone_id for l in sorted(layers, key=lambda x: (x.index, x.alt))]


def rtl_layer_alts(layers: Iterable[RtlLayer]) -> Dict[int, float]:
    return {l.drone_id: l.alt for l in layers}


def layers_are_separated(layers: Iterable[RtlLayer], min_gap: float) -> bool:
    """ตรวจว่าทุกชั้นห่างกันอย่างน้อย min_gap เมตรจริง (ใช้ยืนยันความปลอดภัย)"""
    alts = sorted(l.alt for l in layers)
    return all(b - a >= min_gap - 1e-9 for a, b in zip(alts, alts[1:]))


# ══════════════════════════════════════════════════════════════
#  COLLISION DETECTION — เตือนเมื่อโดรนเข้าใกล้กันเกินไป
# ══════════════════════════════════════════════════════════════
@dataclass
class CollisionPair:
    a: int
    b: int
    dist: float          # ระยะราบ (m)
    vertical: float      # ต่างระดับความสูง (m)
    level: str           # 'critical' = เสี่ยงชน/ซ้อนทับ, 'warn' = ใกล้เกินไป

    def key(self) -> Tuple[int, int]:
        return (min(self.a, self.b), max(self.a, self.b))

    def describe(self) -> str:
        tag = "ชนกัน/ซ้อนทับ" if self.level == "critical" else "ใกล้เกินไป"
        return f"D{self.a} ↔ D{self.b} {tag} · ห่าง {self.dist:.1f} m"


def detect_collisions(positions: Dict[int, Tuple[float, float, float]],
                      critical_m: float = 3.0,
                      warn_m: float = 6.0,
                      vertical_m: float = 2.0) -> List[CollisionPair]:
    """ตรวจทุกคู่โดรนแบบ real-time ว่ามีคู่ไหนใกล้กันจนเสี่ยงชนไหม

    positions = {drone_id: (lat, lon, alt_rel)}
    critical_m : ใกล้กว่านี้ = เสี่ยงชน/ซ้อนทับ (แดง)
    warn_m     : ใกล้กว่านี้ = เตือน (เหลือง)
    vertical_m : ถ้าต่างระดับความสูงเกินนี้ ถือว่าคนละชั้น ไม่ชนกัน

    คืนรายการคู่ที่เข้าเกณฑ์ เรียงจากอันตรายสุด (ใกล้สุด) ก่อน
    """
    ids = _norm_ids(positions.keys())
    out: List[CollisionPair] = []
    for idx, a in enumerate(ids):
        for b in ids[idx + 1:]:
            la, lo, aa = positions[a]
            lb, lb_lon, ab = positions[b]
            dv = abs(float(aa) - float(ab))
            if dv > vertical_m:
                continue          # บินคนละชั้น — ไม่นับว่าเสี่ยง
            d = haversine_m(la, lo, lb, lb_lon)
            if d <= critical_m:
                out.append(CollisionPair(a, b, d, dv, "critical"))
            elif d <= warn_m:
                out.append(CollisionPair(a, b, d, dv, "warn"))
    out.sort(key=lambda p: p.dist)
    return out


# ══════════════════════════════════════════════════════════════
#  6. PRE-FLIGHT COMMAND SUMMARY (spec 6)
# ══════════════════════════════════════════════════════════════
class CommandSummary:
    """เก็บสรุปคำสั่ง/ค่าที่ผู้ใช้ตั้งไว้ก่อนกด Take off เพื่อให้รีวิวก่อนบินจริง

    keyed entries — set ซ้ำ key เดิมจะทับค่า (ไม่บวมรก) และคงลำดับที่เพิ่มครั้งแรก
    """

    def __init__(self):
        self._items: Dict[str, Tuple[str, str]] = {}
        self._order: List[str] = []

    def set(self, key: str, label: str, value) -> None:
        if key not in self._items:
            self._order.append(key)
        self._items[key] = (str(label), str(value))

    def remove(self, key: str) -> None:
        if key in self._items:
            del self._items[key]
            self._order = [k for k in self._order if k != key]

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()

    def rows(self) -> List[Tuple[str, str]]:
        # Flight plan is read top-to-bottom by operational importance, never by
        # the incidental order in which a pilot happened to click UI controls.
        priority = {
            "target": 0, "head": 1, "takeoff": 2, "swarm": 3,
            "waypoint": 4, "payload": 5,
            "wait": 5.5,             # WAIT อยู่ถัดจาก PAYLOAD A/B ก่อน WAVE (spec §11)
            "wave": 6,
            "return_plan": 7, "geofence": 8,
        }
        keys = [k for k in self._order if k in self._items]
        keys.sort(key=lambda key: (priority.get(key, 99), self._order.index(key)))
        return [(self._items[k][0], self._items[k][1]) for k in keys]

    def as_text(self) -> str:
        return "\n".join(f"{lab}: {val}" for lab, val in self.rows())

    def __len__(self) -> int:
        return len(self._items)
