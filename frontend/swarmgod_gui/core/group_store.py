"""group_store.py — เก็บหมายเลขกลุ่มของโดรนใน SQLite ไฟล์เดียวกับ IpStore"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from contextlib import closing
from typing import Dict, Optional


def _default_db_path() -> str:
    """ใช้กติกา path เดียวกับ ip_store โดย test/headless ไม่แตะ DB ผู้ใช้จริง"""
    root = os.getenv("SWARMGOD_DATA_DIR", "").strip()
    if not root and os.getenv("SWARMGOD_NO_MAP") == "1":
        root = os.path.join(tempfile.gettempdir(), f"swarmgod-test-{os.getpid()}")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".swarmgod")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "fleet_ips.db")


class GroupStore:
    """เก็บกลุ่ม 1..6 ของโดรน; group_no=0 หมายถึงเอาออกจากกลุ่ม"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_db_path()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drone_groups (
                    drone_id  INTEGER PRIMARY KEY,
                    group_no  INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def set_group(self, drone_id: int, group_no: int) -> None:
        did = int(drone_id or 0)
        group = int(group_no or 0)
        if did <= 0:
            return
        with closing(self._connect()) as conn, conn:
            if group == 0:
                conn.execute("DELETE FROM drone_groups WHERE drone_id=?", (did,))
            elif 1 <= group <= 6:
                conn.execute(
                    """
                    INSERT INTO drone_groups (drone_id, group_no, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(drone_id) DO UPDATE SET
                        group_no=excluded.group_no,
                        updated_at=excluded.updated_at
                    """,
                    (did, group, time.time()),
                )
            else:
                raise ValueError("group_no must be in 0..6")
            conn.commit()

    def get_all(self) -> Dict[int, int]:
        """คืนเฉพาะแถวที่ถูกต้อง และล้างข้อมูลเสียจากฐานข้อมูลไปพร้อมกัน"""
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM drone_groups WHERE drone_id <= 0 OR group_no < 1 OR group_no > 6")
            rows = conn.execute(
                "SELECT drone_id, group_no FROM drone_groups ORDER BY drone_id"
            ).fetchall()
            conn.commit()
        return {int(row["drone_id"]): int(row["group_no"]) for row in rows}

    def clear_drone(self, drone_id: int) -> None:
        self.set_group(drone_id, 0)
