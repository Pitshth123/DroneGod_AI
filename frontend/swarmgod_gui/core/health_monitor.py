"""HealthMonitor — UI Watchdog / runtime observability แบบ **observe-only**.

Phase 1 ของ Low-Risk Architecture Migration (ดู docs/LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md §7).

หน้าที่: วัดและบันทึกสุขภาพของ Cockpit UI เพื่อให้รอบ refactor ถัดไปมี metric เทียบได้
ไม่ต้องเดาว่า "น่าจะเร็วขึ้น".

ขอบเขตที่จงใจจำกัด (สำคัญด้านความปลอดภัย):
- เป็น pure model — **ไม่ผูก Qt, ไม่มี CoreClient, ไม่มีทางส่ง flight command**
- ตรวจจับ UI stall แล้ว "log/รายงาน" เท่านั้น ห้าม restart, ห้าม RTL, ห้าม HOLD, ห้าม trigger failsafe
- lightweight มาก (คณิตศาสตร์ไม่กี่บรรทัดต่อ tick) เพื่อไม่ให้ watchdog เองเป็นต้นเหตุ freeze

การวัด UI stall: main-thread QTimer ยิงทุก ~heartbeat_ms; ถ้า event loop ถูกบล็อก
callback จะมาช้า → gap ระหว่าง tick จริงมากกว่าที่ตั้งไว้ = ระยะที่ loop ค้าง (lateness).
"""
import time


class HealthMonitor:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALLED = "STALLED"

    def __init__(
        self,
        clock=time.monotonic,
        heartbeat_ms=1000,
        degraded_ms=250,
        stalled_ms=1000,
        on_stall=None,
    ):
        """
        clock        : callable คืนเวลา monotonic (วินาที) — inject ได้ตอนทดสอบ
        heartbeat_ms : คาบที่ตั้งใจให้ heartbeat ยิง
        degraded_ms  : lateness (ms) ที่ถือว่า DEGRADED
        stalled_ms   : lateness (ms) ที่ถือว่า STALLED
        on_stall(ms) : callback เมื่อเข้า STALLED (รับ stall duration ms) — ใช้ log เท่านั้น
        """
        self._clock = clock
        self._heartbeat_ms = float(heartbeat_ms)
        self._degraded_ms = float(degraded_ms)
        self._stalled_ms = float(stalled_ms)
        self._on_stall = on_stall

        self._last_tick = None
        self._last_heartbeat_ms = 0.0
        self._status = self.HEALTHY

        self._stall_count = 0
        self._last_stall_ms = 0.0
        self._total_stall_ms = 0.0

        self._telem_ts = {}          # drone_id -> เวลา monotonic ที่ได้ telemetry ล่าสุด
        self._core_connected = False
        self._last_rpc = None        # (name, duration_ms, ts)

        self._render_total = 0
        self._render_window_count = 0
        self._render_window_start = self._clock()

    # ---- UI heartbeat / stall ------------------------------------------

    def record_ui_tick(self):
        """เรียกจาก main-thread heartbeat timer ทุก ~heartbeat_ms.

        คืนสถานะปัจจุบัน (HEALTHY/DEGRADED/STALLED). observe-only — ไม่ทำ action อื่น.
        """
        now = self._clock()
        if self._last_tick is None:
            self._last_tick = now
            self._status = self.HEALTHY
            return self._status

        gap_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        self._last_heartbeat_ms = gap_ms
        lateness = gap_ms - self._heartbeat_ms
        if lateness < 0:
            lateness = 0.0

        if lateness >= self._stalled_ms:
            self._status = self.STALLED
            self._stall_count += 1
            self._last_stall_ms = lateness
            self._total_stall_ms += lateness
            if self._on_stall is not None:
                try:
                    self._on_stall(lateness)
                except Exception:
                    # watchdog ต้องไม่ล้มเพราะ logger เอง
                    pass
        elif lateness >= self._degraded_ms:
            self._status = self.DEGRADED
        else:
            self._status = self.HEALTHY
        return self._status

    def status(self):
        return self._status

    # ---- telemetry age --------------------------------------------------

    def note_telemetry(self, drone_id, ts=None):
        self._telem_ts[drone_id] = self._clock() if ts is None else ts

    def telemetry_age_ms(self, drone_id):
        ts = self._telem_ts.get(drone_id)
        if ts is None:
            return None
        return (self._clock() - ts) * 1000.0

    def telemetry_ages_ms(self):
        now = self._clock()
        return {did: (now - ts) * 1000.0 for did, ts in self._telem_ts.items()}

    def forget_drone(self, drone_id):
        self._telem_ts.pop(drone_id, None)

    # ---- misc runtime metrics ------------------------------------------

    def note_core_connected(self, connected):
        self._core_connected = bool(connected)

    def note_rpc(self, name, duration_ms=None):
        self._last_rpc = (name, duration_ms, self._clock())

    def record_render(self):
        self._render_total += 1
        self._render_window_count += 1

    def render_rate(self):
        """renders/วินาที นับตั้งแต่ครั้งที่เรียก render_rate ล่าสุด แล้วรีเซ็ต window."""
        now = self._clock()
        elapsed = now - self._render_window_start
        rate = (self._render_window_count / elapsed) if elapsed > 0 else 0.0
        self._render_window_start = now
        self._render_window_count = 0
        return rate

    # ---- snapshot -------------------------------------------------------

    def snapshot(self):
        return {
            "status": self._status,
            "heartbeat_ms": self._last_heartbeat_ms,
            "stall_count": self._stall_count,
            "last_stall_ms": self._last_stall_ms,
            "total_stall_ms": self._total_stall_ms,
            "core_connected": self._core_connected,
            "telemetry_age_ms": self.telemetry_ages_ms(),
            "last_rpc": self._last_rpc,
            "render_total": self._render_total,
        }
