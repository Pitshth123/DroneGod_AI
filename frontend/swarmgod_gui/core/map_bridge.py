"""map_bridge.py — QWebChannel bridge ระหว่าง map.html (JS) กับ Python"""
import json
import re

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

_LATLON_SPLIT = re.compile(r"[,/\s]+")


class MapBridge(QObject):
    map_click = pyqtSignal(float, float)   # lat, lon → GOTO
    target_reached = pyqtSignal(int)       # drone_id — ถึงเป้าแล้ว (แผนที่ล้างเป้าเอง)
    mouse_move = pyqtSignal(float, float)  # lat, lon ใต้เมาส์ (real-time)
    mouse_out = pyqtSignal()               # เมาส์ออกนอกแผนที่
    waypoint_click = pyqtSignal(float, float)  # lat, lon — คลิกวางจุดใน Waypoint Mode
    waypoint_context = pyqtSignal(int, int)    # route key, waypoint index — คลิกขวาตั้ง action
    goto_arm = pyqtSignal(bool)            # กดปุ่มปลดล็อก/ล็อก "คลิกแผนที่สั่งบิน"
    drone_select = pyqtSignal(int)          # คลิกรายการโดรนบนแผนที่ 3D

    @pyqtSlot(str)
    def on_map_event(self, payload: str):
        try:
            d = json.loads(payload)
            ev = d.get("event")
            if ev == "map_click":
                self.map_click.emit(float(d["lat"]), float(d["lon"]))
            elif ev == "target_reached":
                self.target_reached.emit(int(d.get("drone_id") or 0))
            elif ev == "mouse_move":
                self.mouse_move.emit(float(d["lat"]), float(d["lon"]))
            elif ev == "mouse_out":
                self.mouse_out.emit()
            elif ev == "waypoint_click":
                self.waypoint_click.emit(float(d["lat"]), float(d["lon"]))
            elif ev == "waypoint_context":
                self.waypoint_context.emit(
                    int(d.get("drone_id") or 0), int(d.get("index") or 0))
            elif ev == "goto_arm":
                self.goto_arm.emit(bool(d.get("on")))
            elif ev == "drone_select":
                self.drone_select.emit(int(d.get("drone_id") or 0))
        except Exception as e:  # pragma: no cover
            print("MapBridge error:", e)


def parse_latlon(text):
    """แปลงข้อความผู้ใช้เป็น (lat, lon) — คืน None ถ้าใช้ไม่ได้

    รับ '13.7563, 100.5018' หรือคั่นด้วยช่องว่าง/ทับ ไม่รับ (0,0)
    """
    if text is None:
        return None
    raw = str(text).strip().replace("\u00a0", " ").replace(";", ",")
    parts = [p for p in _LATLON_SPLIT.split(raw) if p]
    if len(parts) < 2:
        return None
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        return None
    if lat != lat or lon != lon:  # NaN
        return None
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)
