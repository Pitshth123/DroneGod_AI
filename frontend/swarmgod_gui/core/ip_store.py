"""
ip_store.py — จำ IP โดรนที่เคย add ไว้ใน SQLite (~/.swarmgod/fleet_ips.db)
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional


def _default_db_path() -> str:
    root = os.getenv("SWARMGOD_DATA_DIR", "").strip()
    if not root and os.getenv("SWARMGOD_NO_MAP") == "1":
        # headless/test mode ต้องไม่แตะ fleet database ของผู้ใช้จริง
        root = os.path.join(tempfile.gettempdir(), f"swarmgod-test-{os.getpid()}")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".swarmgod")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "fleet_ips.db")


@dataclass
class SavedIp:
    drone_id: int
    host: str
    port: int = 5760
    protocol: str = "tcp"
    name: str = ""
    updated_at: float = 0.0

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{int(self.port)}"


class IpStore:
    """CRUD เบา ๆ สำหรับรายการ IP ที่เคยเชื่อม"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_db_path()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_ips (
                    drone_id   INTEGER PRIMARY KEY,
                    host       TEXT    NOT NULL,
                    port       INTEGER NOT NULL DEFAULT 5760,
                    protocol   TEXT    NOT NULL DEFAULT 'tcp',
                    name       TEXT    NOT NULL DEFAULT '',
                    updated_at REAL    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_saved_ips_host ON saved_ips(host)"
            )
            conn.commit()

    def upsert(self, drone_id: int, host: str, port: int = 5760,
               protocol: str = "tcp", name: str = "") -> None:
        did = int(drone_id or 0)
        host = (host or "").strip()
        if not did or not host:
            return
        port = int(port or 0) or 5760
        protocol = (protocol or "tcp").strip().lower() or "tcp"
        name = (name or "").strip() or f"Drone {did}"
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_ips (drone_id, host, port, protocol, name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(drone_id) DO UPDATE SET
                    host=excluded.host,
                    port=excluded.port,
                    protocol=excluded.protocol,
                    name=excluded.name,
                    updated_at=excluded.updated_at
                """,
                (did, host, port, protocol, name, now),
            )
            conn.commit()

    def delete(self, drone_id: int) -> None:
        did = int(drone_id or 0)
        if not did:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM saved_ips WHERE drone_id=?", (did,))
            conn.commit()

    def get(self, drone_id: int) -> Optional[SavedIp]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM saved_ips WHERE drone_id=?", (int(drone_id),)
            ).fetchone()
        return self._row(row) if row else None

    def list_all(self) -> List[SavedIp]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_ips ORDER BY updated_at DESC, drone_id ASC"
            ).fetchall()
        return [self._row(r) for r in rows]

    def find_by_host(self, host: str, port: int = 0,
                     exclude_id: int = 0) -> Optional[SavedIp]:
        """หา IP ซ้ำ — LAN เทียบ host · localhost เทียบ host+port"""
        host_n = (host or "").strip().lower()
        if host_n in ("localhost", "::1"):
            host_n = "127.0.0.1"
        if not host_n:
            return None
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM saved_ips").fetchall()
        for row in rows:
            s = self._row(row)
            if exclude_id and s.drone_id == int(exclude_id):
                continue
            eh = s.host.strip().lower()
            if eh in ("localhost", "::1"):
                eh = "127.0.0.1"
            if host_n == "127.0.0.1":
                if eh == host_n and int(s.port) == int(port or 0):
                    return s
            elif eh == host_n:
                return s
        return None

    @staticmethod
    def _row(row: sqlite3.Row) -> SavedIp:
        return SavedIp(
            drone_id=int(row["drone_id"]),
            host=str(row["host"] or ""),
            port=int(row["port"] or 5760),
            protocol=str(row["protocol"] or "tcp"),
            name=str(row["name"] or ""),
            updated_at=float(row["updated_at"] or 0),
        )
