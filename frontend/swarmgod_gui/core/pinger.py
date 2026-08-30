"""
pinger.py — ICMP ping + TCP port probe สำหรับทดสอบลิงก์โดรน
รันใน background thread แล้วส่งผลกลับผ่าน callback / Qt signal
"""
from __future__ import annotations

import platform
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal


@dataclass
class PingResult:
    host: str
    ok: bool
    ms: Optional[float]          # ICMP RTT (None ถ้า ICMP ล้ม)
    tcp_ms: Optional[float]      # TCP connect latency
    port: int
    method: str                  # "icmp" | "tcp" | "fail"
    detail: str


_TIME_RE = re.compile(
    r"(?:time[=<]|Average\s*=\s*|เฉลี่ย\s*=\s*)(\d+(?:[.,]\d+)?)\s*ms",
    re.IGNORECASE,
)


def icmp_ping(host: str, timeout_ms: int = 1200,
              cancel_event: Optional[threading.Event] = None) -> tuple[bool, Optional[float], str]:
    """ส่ง ICMP echo 1 ครั้ง คืน (ok, rtt_ms, detail).

    ``cancel_event`` lets a closing cockpit terminate the child process and join
    the PingWorker deterministically instead of destroying a live QThread that is
    still blocked inside ``subprocess.run`` / its Windows pipe reader threads.
    """
    if not host:
        return False, None, "no host"
    system = platform.system().lower()
    try:
        if system == "windows":
            # -n 1 = 1 packet, -w timeout ms
            cmd = ["ping", "-n", "1", "-w", str(int(timeout_ms)), host]
        else:
            # -c 1, -W timeout seconds (Linux)
            sec = max(1, int(round(timeout_ms / 1000.0)))
            cmd = ["ping", "-c", "1", "-W", str(sec), host]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if system == "windows" else 0,
        )
        deadline = time.monotonic() + (timeout_ms / 1000.0) + 2.0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.communicate(timeout=0.5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.communicate(timeout=0.5)
                    except Exception:
                        pass
                return False, None, "icmp cancelled"
            try:
                out, err = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.communicate(timeout=0.5)
                    except Exception:
                        pass
                    return False, None, "icmp timeout"

        text = (out or "") + (err or "")
        m = _TIME_RE.search(text)
        if m:
            ms = float(m.group(1).replace(",", "."))
            return True, ms, f"icmp {ms:.0f} ms"
        if proc.returncode == 0:
            return True, None, "icmp ok"
        return False, None, "icmp timeout"
    except Exception as e:
        return False, None, f"icmp err: {e}"


def tcp_probe(host: str, port: int, timeout_s: float = 1.2) -> tuple[bool, Optional[float], str]:
    """วัดเวลา connect TCP ไปยัง host:port"""
    if not host or not port:
        return False, None, "no host/port"
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            ms = (time.perf_counter() - t0) * 1000.0
            return True, ms, f"tcp :{port} {ms:.0f} ms"
    except OSError as e:
        return False, None, f"tcp fail: {e}"


def probe(host: str, port: int = 0, timeout_ms: int = 1200,
          cancel_event: Optional[threading.Event] = None) -> PingResult:
    """ลอง ICMP ก่อน ถ้าไม่ได้ใช้ TCP เป็น fallback."""
    host = (host or "").strip()
    if not host:
        return PingResult(host="", ok=False, ms=None, tcp_ms=None,
                          port=port, method="fail", detail="no host")

    ok_i, ms_i, det_i = icmp_ping(host, timeout_ms=timeout_ms,
                                  cancel_event=cancel_event)
    if ok_i:
        return PingResult(host=host, ok=True, ms=ms_i, tcp_ms=None,
                          port=port, method="icmp", detail=det_i)
    if cancel_event is not None and cancel_event.is_set():
        return PingResult(host=host, ok=False, ms=None, tcp_ms=None,
                          port=port, method="fail", detail="cancelled")

    if port:
        ok_t, ms_t, det_t = tcp_probe(host, port, timeout_s=timeout_ms / 1000.0)
        if ok_t:
            return PingResult(host=host, ok=True, ms=None, tcp_ms=ms_t,
                              port=port, method="tcp", detail=det_t)
        return PingResult(host=host, ok=False, ms=None, tcp_ms=None,
                          port=port, method="fail",
                          detail=f"{det_i} · {det_t}")

    return PingResult(host=host, ok=False, ms=None, tcp_ms=None,
                      port=port, method="fail", detail=det_i)


class PingWorker(QThread):
    """QThread ปิงครั้งเดียว แล้วจบ — ยกเลิกได้ระหว่าง cockpit shutdown."""
    finished_result = pyqtSignal(int, object)  # drone_id, PingResult

    def __init__(self, drone_id: int, host: str, port: int = 0, parent=None):
        super().__init__(parent)
        self.drone_id = drone_id
        self.host = host
        self.port = port
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()
        self.requestInterruption()

    def run(self):
        res = probe(self.host, self.port, cancel_event=self._cancel_event)
        if not self._cancel_event.is_set():
            self.finished_result.emit(self.drone_id, res)


class PingService(QObject):
    """คิวปิงไม่ซ้อน — ปิงทีละ host และ shutdown worker แบบ deterministic."""
    result = pyqtSignal(int, object)  # drone_id, PingResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._closing = False
        self._worker: Optional[PingWorker] = None

    @property
    def busy(self) -> bool:
        return self._busy

    def ping(self, drone_id: int, host: str, port: int = 0) -> bool:
        if self._closing or self._busy or not host:
            return False
        self._busy = True
        w = PingWorker(drone_id, host, port, parent=self)
        w.finished_result.connect(self._on_done)
        self._worker = w
        w.start()
        return True

    def _on_done(self, drone_id: int, res: PingResult):
        self._busy = False
        w = self._worker
        self._worker = None
        if not self._closing:
            self.result.emit(drone_id, res)
        if w is not None:
            w.deleteLater()

    def shutdown(self, timeout_ms: int = 4000) -> bool:
        """Cancel and join the active PingWorker before this QObject is destroyed."""
        self._closing = True
        w = self._worker
        if w is None:
            self._busy = False
            return True
        try:
            w.finished_result.disconnect(self._on_done)
        except (TypeError, RuntimeError):
            pass
        w.cancel()
        stopped = bool(w.wait(max(0, int(timeout_ms))))
        if stopped:
            self._worker = None
            self._busy = False
            w.deleteLater()
        return stopped
