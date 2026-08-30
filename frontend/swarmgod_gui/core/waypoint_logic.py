"""
waypoint_logic.py — สมองล้วน ๆ ของฟีเจอร์ Waypoint Route Planning (ไม่มี Qt / ไม่มี gRPC)

หลักการเดียวกับ swarm_logic.py — "UI คือกระจก ไม่ใช่สมอง" (ดู ARCHITECTURE.md §1.4)
ทุกฟังก์ชัน/คลาสในไฟล์นี้ต้อง test ได้แบบ headless

ครอบคลุม:
  - เก็บรายการจุด Waypoint ที่ผู้ใช้พล็อตไว้ (WaypointRoute) + undo/clear
  - คำนวณพิกัดเป้าหมายของแต่ละลำใน swarm เมื่อบินไปยัง waypoint 1 จุด
    (reuse หลักการเดียวกับ swarm_logic.group_goto_targets — คงรูปขบวน/ระยะห่าง)
  - ตรวจการชนของเส้นทางก่อนสั่งบิน (check_route_conflicts) — โหมด SEPARATE
    ที่แต่ละลำมีเส้นทางของตัวเอง เส้นทางอาจตัดกันได้ ต้องกันไว้ก่อน
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from . import swarm_logic


# ── WAIT ราย Waypoint (spec §3) ──────────────────────────────
# WAIT เป็น metadata แยกจาก action A/B เดิม — ห้ามยัดรวมกัน
# ผู้ใช้กรอกเป็น "นาที" (1..10) แต่โมเดลเก็บเป็น "วินาที" (60..600)
# 0 = ไม่มี WAIT. หนึ่ง route มี WAIT ได้สูงสุด MAX_WAIT_POINTS จุด
MIN_WAIT_SECONDS = 60
MAX_WAIT_SECONDS = 600
MAX_WAIT_POINTS = 5


class WaitLimitError(ValueError):
    """route มี WAIT ครบ MAX_WAIT_POINTS จุดแล้ว — เพิ่มจุดใหม่ไม่ได้

    เป็น subclass ของ ValueError เพื่อให้ผู้เรียกที่จับ ValueError กว้าง ๆ
    ยังกันไว้ได้ แต่ UI จับ WaitLimitError เจาะจงเพื่อขึ้นข้อความเฉพาะได้
    """


def _validate_wait_seconds(seconds: int) -> int:
    """คืนค่าวินาทีที่ผ่านการตรวจ (0 = ไม่รอ); โยน ValueError ถ้านอกช่วง

    รับ 0 (ไม่รอ) หรือ 60..600 เท่านั้น — ต่ำกว่า 1 นาทีหรือเกิน 10 นาทีไม่ได้
    """
    seconds = int(seconds)
    if seconds == 0:
        return 0
    if not (MIN_WAIT_SECONDS <= seconds <= MAX_WAIT_SECONDS):
        raise ValueError(
            f"WAIT ต้องเป็น 0 หรือ {MIN_WAIT_SECONDS}..{MAX_WAIT_SECONDS} วินาที "
            f"(1..{MAX_WAIT_SECONDS // 60} นาที)")
    return seconds


@dataclass
class Waypoint:
    index: int
    lat: float
    lon: float
    action: str = ""       # "" | "servo_a" | "servo_b"
    wait_seconds: int = 0  # 0 = ไม่รอ; 60..600 = HOLD/WAIT ก่อนทำ action/ไปจุดถัดไป

    def __iter__(self):
        yield self.lat
        yield self.lon

    @property
    def wait_minutes(self) -> int:
        """WAIT เป็นจำนวนเต็มนาที (ปัดลง) — สำหรับ label/summary"""
        return self.wait_seconds // 60

    @property
    def has_wait(self) -> bool:
        return self.wait_seconds > 0


class WaypointRoute:
    """เส้นทางที่ผู้ใช้พล็อตไว้บนแผนที่ — ลำดับจุดที่จะบินผ่านทีละจุด

    drone_ids เก็บไว้เพื่อจำว่าตอนเริ่มวาดเส้นทาง ผู้ใช้เลือกลำไหนไว้
    (single-select → เส้นทางของลำนั้นลำเดียว; multi-select/FLEET → Leader Path
    ที่ทุกลำในขบวนบินตามพร้อมรักษารูปขบวน — ดู spec ข้อ 3)
    """

    def __init__(self, drone_ids: Iterable[int] = ()):
        self.points: List[Waypoint] = []
        self.drone_ids: List[int] = swarm_logic._norm_ids(drone_ids)
        self._next_index = 0

    @property
    def is_swarm(self) -> bool:
        return len(self.drone_ids) > 1

    def add(self, lat: float, lon: float, action: str = "",
            wait_seconds: int = 0) -> Waypoint:
        action = str(action or "").strip().lower()
        if action not in ("", "servo_a", "servo_b"):
            raise ValueError("waypoint action must be '', 'servo_a' or 'servo_b'")
        wait_seconds = _validate_wait_seconds(wait_seconds)
        wp = Waypoint(self._next_index, float(lat), float(lon), action, wait_seconds)
        self.points.append(wp)
        self._next_index += 1
        return wp

    def set_action(self, index: int, action: str = "") -> Waypoint:
        """ตั้งการกระทำของจุดตาม index ถาวร (index ไม่จำเป็นต้องตรงตำแหน่ง list)"""
        action = str(action or "").strip().lower()
        if action not in ("", "servo_a", "servo_b"):
            raise ValueError("waypoint action must be '', 'servo_a' or 'servo_b'")
        for wp in self.points:
            if wp.index == int(index):
                wp.action = action
                return wp
        raise IndexError(f"waypoint index {index} not found")

    # ── WAIT ราย Waypoint ─────────────────────────────────────
    def _find(self, index: int) -> Waypoint:
        for wp in self.points:
            if wp.index == int(index):
                return wp
        raise IndexError(f"waypoint index {index} not found")

    def wait_points(self) -> List[Waypoint]:
        """จุดที่มี WAIT จริง — คำนวณจาก route เสมอ (ไม่มี counter แยก)"""
        return [wp for wp in self.points if wp.wait_seconds > 0]

    def wait_count(self) -> int:
        return len(self.wait_points())

    def set_wait(self, index: int, seconds: int) -> Waypoint:
        """ตั้ง WAIT ของจุดตาม index (วินาที)

        seconds = 0 → ล้าง WAIT ของจุดนั้น (เทียบเท่า clear_wait)
        seconds = 60..600 → ตั้ง WAIT
        กติกา:
          - ต่ำกว่า 1 นาที (60s) หรือเกิน 10 นาที (600s) → ValueError
          - route มี WAIT ครบ MAX_WAIT_POINTS แล้ว และจุดนี้ยังไม่มี WAIT
            → WaitLimitError (แก้เวลาจุดเดิมยังทำได้เสมอ)
        """
        wp = self._find(index)
        seconds = int(seconds)
        if seconds == 0:
            wp.wait_seconds = 0
            return wp
        seconds = _validate_wait_seconds(seconds)
        if wp.wait_seconds == 0 and self.wait_count() >= MAX_WAIT_POINTS:
            raise WaitLimitError(
                f"ตั้ง WAIT ได้สูงสุด {MAX_WAIT_POINTS} จุดต่อ Route")
        wp.wait_seconds = seconds
        return wp

    def set_wait_minutes(self, index: int, minutes: int) -> Waypoint:
        """ตั้ง WAIT เป็นจำนวนนาที (1..10) — ตัวช่วยของ UI popup"""
        minutes = int(minutes)
        if not (1 <= minutes <= MAX_WAIT_SECONDS // 60):
            raise ValueError(
                f"WAIT ต้องอยู่ระหว่าง 1 ถึง {MAX_WAIT_SECONDS // 60} นาที")
        return self.set_wait(index, minutes * 60)

    def clear_wait(self, index: int) -> Waypoint:
        wp = self._find(index)
        wp.wait_seconds = 0
        return wp

    def wait_summary(self) -> List[Tuple[int, int]]:
        """[(ตำแหน่งจุด 1-based, นาที), ...] เรียงตามลำดับจุด — สำหรับ summary"""
        return [(i + 1, wp.wait_minutes)
                for i, wp in enumerate(self.points) if wp.wait_seconds > 0]

    def remove_last(self) -> "Waypoint | None":
        """Undo — ลบจุดสุดท้าย; คืน Waypoint ที่ถูกลบ หรือ None ถ้าว่างอยู่แล้ว"""
        if not self.points:
            return None
        wp = self.points.pop()
        # ไม่ลด _next_index — ดัชนีของจุดถัดไปที่เพิ่มใหม่เดินหน้าต่อเสมอ
        # (กันสับสนกรณี undo แล้ววาดใหม่ ยังไม่ชนเลขจุดเดิมที่เคยลบไปแล้ว)
        return wp

    def clear(self) -> None:
        self.points.clear()
        self._next_index = 0

    def is_empty(self) -> bool:
        return len(self.points) == 0

    def as_pairs(self) -> List[Tuple[float, float]]:
        return [(wp.lat, wp.lon) for wp in self.points]

    def __len__(self) -> int:
        return len(self.points)


def waypoint_swarm_targets(positions: Dict[int, Tuple[float, float]],
                          wp_lat: float, wp_lon: float,
                          keep_formation: bool = True
                          ) -> Dict[int, Tuple[float, float]]:
    """คำนวณพิกัดเป้าหมายของแต่ละลำใน swarm เมื่อบินไปยัง waypoint 1 จุด

    ใช้หลักการเดียวกับ swarm_logic.group_goto_targets: หาจุดกึ่งกลางของกลุ่ม
    ปัจจุบัน แล้วเลื่อนทุกลำด้วยเวกเตอร์เดียวกันไปยัง waypoint → รูปขบวน/
    ระยะห่างเดิมคงอยู่เป๊ะทุกจุดตลอดเส้นทาง (ไม่ใช่แค่จุดเดียว)

    positions = {drone_id: (lat, lon)} ตำแหน่งปัจจุบันของลำที่เลือก
    คืน {drone_id: (lat, lon)} เป้าหมายของแต่ละลำสำหรับ waypoint นี้
    """
    return swarm_logic.group_goto_targets(
        positions, wp_lat, wp_lon, keep_formation=keep_formation)



def grouped_goto_order(ids: Iterable[int],
                       positions: Dict[int, Tuple[float, float]],
                       wp_lat: float, wp_lon: float) -> List[int]:
    """Legacy front-of-travel dispatch order used by app.py.

    Only drones with a known position participate in movement_order; missing
    participants are appended in caller order.
    """
    ordered_ids = [int(x) for x in ids]
    if len(ordered_ids) <= 1 or not positions:
        return ordered_ids
    known = {int(d): (float(p[0]), float(p[1]))
             for d, p in positions.items() if int(d) in ordered_ids}
    if not known:
        return ordered_ids
    clat = sum(p[0] for p in known.values()) / len(known)
    clon = sum(p[1] for p in known.values()) / len(known)
    dlat, dlon = float(wp_lat) - clat, float(wp_lon) - clon
    if abs(dlat) < 1e-9 and abs(dlon) < 1e-9:
        return ordered_ids
    if abs(dlon) >= abs(dlat):
        direction = swarm_logic.DIR_RIGHT if dlon > 0 else swarm_logic.DIR_LEFT
    else:
        direction = swarm_logic.DIR_FWD if dlat > 0 else swarm_logic.DIR_BWD
    xy = {d: (p[1], p[0]) for d, p in known.items()}
    front = swarm_logic.movement_order(xy, direction)
    return front + [d for d in ordered_ids if d not in front]


def grouped_dispatch_plan(ids: Iterable[int],
                          positions: Dict[int, Tuple[float, float]],
                          wp_lat: float, wp_lon: float
                          ) -> Tuple[Dict[int, Tuple[float, float]], List[int]]:
    """Return the exact Legacy GROUPED targets and 150ms dispatch order."""
    ordered_ids = [int(x) for x in ids]
    known = {int(d): (float(p[0]), float(p[1]))
             for d, p in positions.items() if int(d) in ordered_ids}
    targets = waypoint_swarm_targets(
        known, float(wp_lat), float(wp_lon), keep_formation=True)
    for drone_id in ordered_ids:
        targets.setdefault(drone_id, (float(wp_lat), float(wp_lon)))
    return targets, grouped_goto_order(ordered_ids, known, wp_lat, wp_lon)


def grouped_arrival_update(arrived: Iterable[int], participants: Iterable[int],
                           drone_id: int) -> Tuple[set, bool, bool]:
    """Apply the production GROUPED arrival barrier bookkeeping.

    Returns (new_arrived, complete, accepted). A rejection/non-arrival never calls
    this helper, so it cannot advance the barrier; unknown drone events are ignored.
    """
    required = {int(x) for x in participants}
    new_arrived = {int(x) for x in arrived}
    drone_id = int(drone_id)
    if drone_id not in required:
        return new_arrived, False, False
    new_arrived.add(drone_id)
    return new_arrived, new_arrived >= required, True


def grouped_dispatch_generation_valid(waypoint_executing: bool,
                                      wave_executing: bool,
                                      scheduled_generation: int,
                                      current_generation: int) -> bool:
    """Legacy QTimer guard: normal waypoint callbacks remain valid; stale WAVE
    callbacks are suppressed after generation changes."""
    return bool(waypoint_executing) and (
        not bool(wave_executing)
        or int(scheduled_generation) == int(current_generation))


# ══════════════════════════════════════════════════════════════
#  ตรวจการชนของเส้นทางก่อนสั่งบิน (โหมด SEPARATE)
#  "ห้ามสั่งให้ไปเลย — คำนวณก่อนว่าโดรนระดับเดียวกันไหม เดี๋ยวจะชนกัน"
# ══════════════════════════════════════════════════════════════
@dataclass
class RouteConflict:
    a: int
    b: int
    dist: float        # ระยะใกล้สุดระหว่าง 2 เส้นทาง (m)
    alt_gap: float     # ต่างระดับความสูง (m) — น้อย = อยู่ชั้นเดียวกัน

    def key(self) -> Tuple[int, int]:
        return (min(self.a, self.b), max(self.a, self.b))

    def describe(self) -> str:
        return (f"D{self.a} ↔ D{self.b} · เส้นทางเข้าใกล้กัน {self.dist:.1f} m "
                f"ที่ระดับความสูงต่างกันแค่ {self.alt_gap:.1f} m")


def _latlon_to_xy(lat: float, lon: float, lat0: float) -> Tuple[float, float]:
    """แปลง lat/lon → ระนาบเมตร (equirectangular รอบ lat0) — พอสำหรับระยะสั้น"""
    m = swarm_logic._M_PER_DEG_LAT
    x = lon * m * math.cos(math.radians(lat0))
    y = lat * m
    return x, y


def _seg_seg_dist(p1, p2, p3, p4) -> float:
    """ระยะใกล้สุดระหว่างส่วนของเส้นตรง 2 เส้นในระนาบ 2D"""
    def dot(u, v):
        return u[0] * v[0] + u[1] * v[1]

    def sub(u, v):
        return (u[0] - v[0], u[1] - v[1])

    def point_seg(p, a, b):
        ab = sub(b, a)
        den = dot(ab, ab)
        if den <= 1e-12:
            return math.hypot(*sub(p, a))
        t = max(0.0, min(1.0, dot(sub(p, a), ab) / den))
        proj = (a[0] + ab[0] * t, a[1] + ab[1] * t)
        return math.hypot(*sub(p, proj))

    r = sub(p2, p1)
    s = sub(p4, p3)
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) > 1e-12:
        qp = sub(p3, p1)
        t = (qp[0] * s[1] - qp[1] * s[0]) / denom
        u = (qp[0] * r[1] - qp[1] * r[0]) / denom
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            return 0.0          # ตัดกันจริง
    return min(point_seg(p1, p3, p4), point_seg(p2, p3, p4),
               point_seg(p3, p1, p2), point_seg(p4, p1, p2))


def route_min_distance(path_a: List[Tuple[float, float]],
                       path_b: List[Tuple[float, float]]) -> float:
    """ระยะใกล้สุด (m) ระหว่าง 2 เส้นทาง (list ของ (lat, lon))

    เส้นทางที่มีจุดเดียวถือเป็นจุด — เทียบระยะจุดกับเส้นได้
    ไม่มีจุดเลย → inf (ไม่มีเส้นทาง = ไม่ชน)
    """
    if not path_a or not path_b:
        return float("inf")
    lat0 = path_a[0][0]
    A = [_latlon_to_xy(la, lo, lat0) for la, lo in path_a]
    B = [_latlon_to_xy(la, lo, lat0) for la, lo in path_b]
    if len(A) == 1 and len(B) == 1:
        return math.hypot(A[0][0] - B[0][0], A[0][1] - B[0][1])
    if len(A) == 1:
        A = [A[0], A[0]]
    if len(B) == 1:
        B = [B[0], B[0]]
    best = float("inf")
    for i in range(len(A) - 1):
        for j in range(len(B) - 1):
            d = _seg_seg_dist(A[i], A[i + 1], B[j], B[j + 1])
            if d < best:
                best = d
                if best <= 0.0:
                    return 0.0
    return best


def check_route_conflicts(routes: Dict[int, List[Tuple[float, float]]],
                          alts: Dict[int, float],
                          start_positions: Optional[Dict[int, Tuple[float, float]]] = None,
                          alt_sep_m: float = 2.0,
                          min_dist_m: float = 6.0) -> List[RouteConflict]:
    """ตรวจก่อน Execute ว่ามีคู่ไหนเสี่ยงชนกันไหม

    เกณฑ์: นับเป็น "เสี่ยง" เมื่อ **ทั้งสองเงื่อนไขเป็นจริงพร้อมกัน**
      1) ต่างระดับความสูงไม่เกิน alt_sep_m  → ถือว่าอยู่ชั้นเดียวกัน
      2) เส้นทางเข้าใกล้กันน้อยกว่า min_dist_m (รวมกรณีตัดกัน = 0 m)
    ถ้าบินคนละชั้นความสูงชัดเจน → ไม่นับว่าชน แม้เส้นทางจะทับกันบนแผนที่

    routes          = {drone_id: [(lat, lon), ...]} เส้นทางที่วางไว้
    alts            = {drone_id: ความสูงที่จะบิน (m)}
    start_positions = {drone_id: (lat, lon)} ตำแหน่งปัจจุบัน — ถ้าให้มาจะ
                      เอามาต่อหัวเส้นทาง (ช่วงบินจากที่อยู่ปัจจุบันไปจุดแรกก็เสี่ยงได้)

    คืนรายการคู่ที่เสี่ยง เรียงจากใกล้สุดก่อน
    """
    ids = [d for d in swarm_logic._norm_ids(routes.keys()) if routes.get(d)]
    full: Dict[int, List[Tuple[float, float]]] = {}
    for d in ids:
        path = list(routes[d])
        if start_positions and d in start_positions:
            full[d] = [tuple(start_positions[d])] + path
        else:
            full[d] = path

    out: List[RouteConflict] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            gap = abs(float(alts.get(a, 0.0)) - float(alts.get(b, 0.0)))
            if gap > alt_sep_m:
                continue            # คนละชั้นความสูง — ไม่ชนกัน
            d = route_min_distance(full[a], full[b])
            if d < min_dist_m:
                out.append(RouteConflict(a, b, d, gap))
    out.sort(key=lambda c: c.dist)
    return out
