"""Frontend telemetry read model for Low-Risk Architecture Migration V1 Phase 3.

This module is intentionally presentation-only:
- no Qt
- no CoreClient/gRPC
- no command/failsafe/mission authority
- immutable primitive snapshots only

The legacy ``GroundStation._last_telem`` path remains in place while this store is
introduced as a shadow/read model. Flight/business decisions must not migrate to
this module merely because a snapshot is convenient to read.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Dict, Mapping, Optional, Tuple


@dataclass(frozen=True, slots=True)
class GeoView:
    lat: float = 0.0
    lon: float = 0.0
    alt_rel: float = 0.0
    alt_abs: float = 0.0


@dataclass(frozen=True, slots=True)
class VectorView:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass(frozen=True, slots=True)
class TelemetryView:
    drone_id: int
    name: str
    status: int
    mode: int
    armed: bool
    position: GeoView
    velocity: VectorView
    ground_speed: float
    heading: float
    roll: float
    pitch: float
    battery_pct: float
    voltage: float
    current: float
    gps_fix: int
    sat_count: int
    total_distance: float
    flight_time: float
    connect_elapsed: float
    telemetry_verified: bool
    host: str
    port: int
    timestamp_ms: int
    rssi: int
    rssi_valid: bool
    link_quality: int
    drop_rate: int
    servo_ch7_pwm: int
    servo_ch8_pwm: int
    servo_valid: bool
    rc_ch7_raw: int
    rc_ch8_raw: int
    rc_valid: bool
    ovr_ch7: bool
    ovr_ch8: bool
    received_at: float

    @classmethod
    def from_message(cls, telemetry, received_at: float) -> "TelemetryView":
        p = getattr(telemetry, "position", None)
        v = getattr(telemetry, "velocity", None)
        return cls(
            drone_id=int(getattr(telemetry, "drone_id", 0) or 0),
            name=str(getattr(telemetry, "name", "") or ""),
            status=int(getattr(telemetry, "status", 0) or 0),
            mode=int(getattr(telemetry, "mode", 0) or 0),
            armed=bool(getattr(telemetry, "armed", False)),
            position=GeoView(
                lat=float(getattr(p, "lat", 0.0) or 0.0),
                lon=float(getattr(p, "lon", 0.0) or 0.0),
                alt_rel=float(getattr(p, "alt_rel", 0.0) or 0.0),
                alt_abs=float(getattr(p, "alt_abs", 0.0) or 0.0),
            ),
            velocity=VectorView(
                x=float(getattr(v, "x", 0.0) or 0.0),
                y=float(getattr(v, "y", 0.0) or 0.0),
                z=float(getattr(v, "z", 0.0) or 0.0),
            ),
            ground_speed=float(getattr(telemetry, "ground_speed", 0.0) or 0.0),
            heading=float(getattr(telemetry, "heading", 0.0) or 0.0),
            roll=float(getattr(telemetry, "roll", 0.0) or 0.0),
            pitch=float(getattr(telemetry, "pitch", 0.0) or 0.0),
            battery_pct=float(getattr(telemetry, "battery_pct", 0.0) or 0.0),
            voltage=float(getattr(telemetry, "voltage", 0.0) or 0.0),
            current=float(getattr(telemetry, "current", 0.0) or 0.0),
            gps_fix=int(getattr(telemetry, "gps_fix", 0) or 0),
            sat_count=int(getattr(telemetry, "sat_count", 0) or 0),
            total_distance=float(getattr(telemetry, "total_distance", 0.0) or 0.0),
            flight_time=float(getattr(telemetry, "flight_time", 0.0) or 0.0),
            connect_elapsed=float(getattr(telemetry, "connect_elapsed", 0.0) or 0.0),
            telemetry_verified=bool(getattr(telemetry, "telemetry_verified", False)),
            host=str(getattr(telemetry, "host", "") or ""),
            port=int(getattr(telemetry, "port", 0) or 0),
            timestamp_ms=int(getattr(telemetry, "timestamp_ms", 0) or 0),
            rssi=int(getattr(telemetry, "rssi", 0) or 0),
            rssi_valid=bool(getattr(telemetry, "rssi_valid", False)),
            link_quality=int(getattr(telemetry, "link_quality", 0) or 0),
            drop_rate=int(getattr(telemetry, "drop_rate", 0) or 0),
            servo_ch7_pwm=int(getattr(telemetry, "servo_ch7_pwm", 0) or 0),
            servo_ch8_pwm=int(getattr(telemetry, "servo_ch8_pwm", 0) or 0),
            servo_valid=bool(getattr(telemetry, "servo_valid", False)),
            rc_ch7_raw=int(getattr(telemetry, "rc_ch7_raw", 0) or 0),
            rc_ch8_raw=int(getattr(telemetry, "rc_ch8_raw", 0) or 0),
            rc_valid=bool(getattr(telemetry, "rc_valid", False)),
            ovr_ch7=bool(getattr(telemetry, "ovr_ch7", False)),
            ovr_ch8=bool(getattr(telemetry, "ovr_ch8", False)),
            received_at=float(received_at),
        )

    def comparison_values(self) -> Dict[str, object]:
        """Flatten immutable values used by shadow parity checks."""
        return {
            "drone_id": self.drone_id,
            "name": self.name,
            "status": self.status,
            "mode": self.mode,
            "armed": self.armed,
            "position.lat": self.position.lat,
            "position.lon": self.position.lon,
            "position.alt_rel": self.position.alt_rel,
            "position.alt_abs": self.position.alt_abs,
            "velocity.x": self.velocity.x,
            "velocity.y": self.velocity.y,
            "velocity.z": self.velocity.z,
            "ground_speed": self.ground_speed,
            "heading": self.heading,
            "roll": self.roll,
            "pitch": self.pitch,
            "battery_pct": self.battery_pct,
            "voltage": self.voltage,
            "current": self.current,
            "gps_fix": self.gps_fix,
            "sat_count": self.sat_count,
            "total_distance": self.total_distance,
            "flight_time": self.flight_time,
            "connect_elapsed": self.connect_elapsed,
            "telemetry_verified": self.telemetry_verified,
            "host": self.host,
            "port": self.port,
            "timestamp_ms": self.timestamp_ms,
            "rssi": self.rssi,
            "rssi_valid": self.rssi_valid,
            "link_quality": self.link_quality,
            "drop_rate": self.drop_rate,
            "servo_ch7_pwm": self.servo_ch7_pwm,
            "servo_ch8_pwm": self.servo_ch8_pwm,
            "servo_valid": self.servo_valid,
            "rc_ch7_raw": self.rc_ch7_raw,
            "rc_ch8_raw": self.rc_ch8_raw,
            "rc_valid": self.rc_valid,
            "ovr_ch7": self.ovr_ch7,
            "ovr_ch8": self.ovr_ch8,
        }


class TelemetryStore:
    """Thread-safe latest-value store for frontend presentation reads."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._latest: Dict[int, TelemetryView] = {}
        self._ingest_total = 0
        self._per_drone_total: Dict[int, int] = {}
        self._window_count = 0
        self._window_started = float(self._clock())

    def update(self, telemetry) -> TelemetryView:
        now = float(self._clock())
        view = TelemetryView.from_message(telemetry, now)
        with self._lock:
            self._latest[view.drone_id] = view
            self._ingest_total += 1
            self._per_drone_total[view.drone_id] = self._per_drone_total.get(view.drone_id, 0) + 1
            self._window_count += 1
        return view

    def get(self, drone_id: int) -> Optional[TelemetryView]:
        with self._lock:
            return self._latest.get(int(drone_id))

    def snapshot(self) -> Dict[int, TelemetryView]:
        with self._lock:
            return dict(self._latest)

    def forget(self, drone_id: int) -> None:
        did = int(drone_id)
        with self._lock:
            self._latest.pop(did, None)
            self._per_drone_total.pop(did, None)

    def clear(self) -> None:
        with self._lock:
            self._latest.clear()
            self._per_drone_total.clear()

    def ingest_rate(self) -> float:
        """Packets/second since the previous rate sample; sampling resets only the window."""
        now = float(self._clock())
        with self._lock:
            elapsed = now - self._window_started
            rate = (self._window_count / elapsed) if elapsed > 0 else 0.0
            self._window_started = now
            self._window_count = 0
            return rate

    def ingest_count(self, drone_id: int) -> int:
        with self._lock:
            return int(self._per_drone_total.get(int(drone_id), 0))

    def stats(self) -> Dict[str, object]:
        with self._lock:
            return {
                "latest_count": len(self._latest),
                "ingest_total": self._ingest_total,
                "per_drone_total": dict(self._per_drone_total),
            }

    def diff_message(self, telemetry) -> Tuple[str, ...]:
        """Compare one legacy protobuf-like message to the current immutable view."""
        did = int(getattr(telemetry, "drone_id", 0) or 0)
        with self._lock:
            view = self._latest.get(did)
        if view is None:
            return ("missing",)
        legacy = TelemetryView.from_message(telemetry, view.received_at)
        a = view.comparison_values()
        b = legacy.comparison_values()
        return tuple(key for key in a if a[key] != b[key])

    def mismatches_against(self, legacy: Mapping[int, object]) -> Dict[int, Tuple[str, ...]]:
        mismatches: Dict[int, Tuple[str, ...]] = {}
        for did, message in legacy.items():
            diff = self.diff_message(message)
            if diff:
                mismatches[int(did)] = diff
        with self._lock:
            for did in self._latest:
                if did not in legacy:
                    mismatches.setdefault(did, ("legacy-missing",))
        return mismatches


class TelemetryRenderGate:
    """Coalesce continuous widget repaint bursts without throttling telemetry ingest.

    Discrete/operator-visible state changes render immediately. Continuous motion
    fields (position/speed/heading) may be coalesced inside ``min_interval_s``.
    This gate never delays map/mission/command processing; callers must use it only
    around presentation widget updates.
    """

    def __init__(self, clock=time.monotonic, min_interval_s: float = 0.10):
        self._clock = clock
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._last_at: Dict[int, float] = {}
        self._last_discrete: Dict[int, tuple] = {}
        self._rendered = 0
        self._skipped = 0

    @staticmethod
    def _discrete(view: TelemetryView) -> tuple:
        return (
            view.name,
            view.status,
            view.mode,
            view.armed,
            int(view.battery_pct),
            round(view.voltage, 1),
            view.gps_fix,
            view.sat_count,
            view.telemetry_verified,
            view.host,
            view.port,
            view.rssi,
            view.rssi_valid,
            view.link_quality,
        )

    def should_render(self, view: TelemetryView) -> bool:
        did = int(view.drone_id)
        now = float(self._clock())
        discrete = self._discrete(view)
        previous_at = self._last_at.get(did)
        previous_discrete = self._last_discrete.get(did)
        due = (
            previous_at is None
            or previous_discrete != discrete
            or now - previous_at >= self._min_interval_s
        )
        if due:
            self._last_at[did] = now
            self._last_discrete[did] = discrete
            self._rendered += 1
        else:
            self._skipped += 1
        return due

    def forget(self, drone_id: int) -> None:
        did = int(drone_id)
        self._last_at.pop(did, None)
        self._last_discrete.pop(did, None)

    def stats(self) -> Dict[str, int]:
        return {"rendered": self._rendered, "skipped": self._skipped}
