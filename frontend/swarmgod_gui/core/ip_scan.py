"""
ip_scan.py — สแกน IP ในวง LAN เพื่อหาพอร์ต MAVLink / SITL ของโดรน
ใช้ TCP connect เร็วแบบ concurrent (UDP ไม่มี handshake จึงสแกน TCP เป็นหลัก)
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

# พอร์ตที่มักใช้กับ ArduPilot / companion / SITL
DEFAULT_PORTS: Tuple[int, ...] = (
    5760, 5770, 5780, 5790, 5800,  # SITL TCP (ลำ 1–5)
    5761, 5762, 5763,
    14550, 14551, 14552, 14555,    # MAVLink UDP บ่อย แต่ลอง TCP ด้วย
    57600,  # บาง companion
)


@dataclass(frozen=True)
class ScanHit:
    host: str
    port: int
    ms: float

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


def local_ipv4() -> str:
    """IP ของเครื่องในวง LAN (fallback 127.0.0.1)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def suggest_subnet(cidr_bits: int = 24) -> str:
    """เช่น 192.168.1.0/24 จาก IP เครื่อง"""
    ip = local_ipv4()
    try:
        net = ipaddress.ip_network(f"{ip}/{cidr_bits}", strict=False)
        return str(net)
    except ValueError:
        return "192.168.1.0/24"


def hosts_from_cidr(cidr: str, include_localhost: bool = True) -> List[str]:
    """รายการ host ใน subnet (ตัด network/broadcast ของ /24+)"""
    out: List[str] = []
    if include_localhost:
        out.append("127.0.0.1")
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return out
    if net.num_addresses <= 2:
        out.extend(str(h) for h in net.hosts())
    else:
        # จำกัดไม่เกิน /22 (~1022 hosts) กันค้างเครื่อง
        if net.prefixlen < 22:
            net = ipaddress.ip_network(f"{net.network_address}/22", strict=False)
        for h in net.hosts():
            s = str(h)
            if s not in out:
                out.append(s)
    return out


def probe_tcp(host: str, port: int, timeout: float = 0.35) -> Optional[ScanHit]:
    t0 = socket.getdefaulttimeout()
    try:
        import time
        start = time.perf_counter()
        with socket.create_connection((host, int(port)), timeout=timeout):
            ms = (time.perf_counter() - start) * 1000.0
            return ScanHit(host=host, port=int(port), ms=ms)
    except OSError:
        return None
    finally:
        socket.setdefaulttimeout(t0)


class IpScanWorker(QThread):
    """สแกน subnet × ports ในพื้นหลัง"""
    progress = pyqtSignal(int, int, str)   # done, total, status
    found = pyqtSignal(object)             # ScanHit
    finished_ok = pyqtSignal(int)          # hit count
    failed = pyqtSignal(str)

    def __init__(
        self,
        cidr: str = "",
        ports: Optional[Sequence[int]] = None,
        timeout: float = 0.30,
        workers: int = 64,
        parent=None,
    ):
        super().__init__(parent)
        self.cidr = (cidr or suggest_subnet()).strip()
        self.ports = tuple(ports) if ports else DEFAULT_PORTS
        self.timeout = float(timeout)
        self.workers = max(8, min(128, int(workers)))
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            hosts = hosts_from_cidr(self.cidr, include_localhost=True)
            jobs = [(h, p) for h in hosts for p in self.ports]
            total = len(jobs)
            if total == 0:
                self.failed.emit("ไม่มี host ให้สแกน")
                return
            self.progress.emit(0, total, f"scan {self.cidr} · {len(hosts)} hosts × {len(self.ports)} ports")
            hits = 0
            done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
                futs = {
                    pool.submit(probe_tcp, h, p, self.timeout): (h, p)
                    for h, p in jobs
                }
                for fut in concurrent.futures.as_completed(futs):
                    if self._stop:
                        for f in futs:
                            f.cancel()
                        break
                    done += 1
                    try:
                        hit = fut.result()
                    except Exception:
                        hit = None
                    if hit is not None:
                        hits += 1
                        self.found.emit(hit)
                    if done % 25 == 0 or done == total:
                        self.progress.emit(done, total, f"{done}/{total} · found {hits}")
            self.finished_ok.emit(hits)
        except Exception as e:
            self.failed.emit(str(e))


def split_host_port(text: str, default_port: int = 5760) -> Tuple[str, int]:
    """แยก "host:port" ที่ผู้ใช้พิมพ์ → (host, port)

    ต้องมีเพราะช่อง IP รับได้ทั้ง "192.168.1.10" และ "192.168.1.10:5760"
    (แอปเองก็เติมรูปแบบหลังลงช่องนั้นหลัง SCAN) ถ้าเอาข้อความไปใช้เป็น host ดิบ ๆ
    แล้วต่อพอร์ตเข้าไปอีก จะได้ "192.168.1.10:5760:5760" ซึ่ง dial ไม่ติด
    และ reader ตายภายในไม่กี่วินาทีโดยไม่มีข้อความบอกสาเหตุ

    รองรับ IPv6: "[::1]:5760" แยกถูก · "::1" เปล่า ๆ ไม่ถูกตัดผิด
    """
    s = (text or "").strip()
    if not s:
        return "", int(default_port)

    if s.startswith("["):                       # [::1]:5760 หรือ [::1]
        host, _, rest = s.partition("]")
        p = rest.lstrip(":").strip()
        return host[1:].strip(), int(p) if p.isdigit() else int(default_port)

    if s.count(":") == 1:                       # host:port (IPv4/ชื่อโฮสต์)
        h, _, p = s.partition(":")
        p = p.strip()
        if p.isdigit():
            return h.strip(), int(p)

    return s, int(default_port)                 # host เปล่า หรือ IPv6 ไม่มีวงเล็บ
