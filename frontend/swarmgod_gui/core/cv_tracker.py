"""
cv_tracker.py — OpenCV target tracking (Phase 4b ส่วน tracking ก่อน กล้องยังไม่ต่อ)

แหล่งภาพตอนนี้:
  - demo  : สร้างฉากจำลอง + เป้าเคลื่อนที่ (ทดสอบ tracker ได้ทันที)
  - file  : เปิดไฟล์วิดีโอในเครื่อง
  - camera: จองไว้ใน API แล้ว แต่ยังไม่เปิดใช้ (รอขั้นตอนกล้อง)

อัลกอริทึม: MIL (มีใน OpenCV 5) / COLOR (ตามสี ROI) + CSRT/KCF/MOSSE ถ้า build มี
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5.QtCore import QMutex, QThread, pyqtSignal
from PyQt5.QtGui import QImage


TrackerKind = str  # "MIL" | "COLOR" | "CSRT" | "KCF" | "MOSSE" (ตามที่ OpenCV build มี)
SourceKind = str   # "demo" | "file" | "camera"


@dataclass
class TrackState:
    ok: bool = False
    lost: bool = False
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x,y,w,h
    cx: float = 0.0
    cy: float = 0.0
    # offset จากกลางเฟรม ช่วงประมาณ -1..1 (ขวา+/ล่าง+)
    dx: float = 0.0
    dy: float = 0.0
    fps: float = 0.0
    frame_i: int = 0
    message: str = ""


class ColorCentroidTracker:
    """tracker เบาๆ: ตามสีใน ROI เริ่มต้น (เหมาะกับเป้าสว่างใน Demo ไม่ต้องมี model)"""

    def __init__(self):
        self._hsv_lo = None
        self._hsv_hi = None
        self._wh = (40, 40)

    def init(self, frame, roi):
        x, y, w, h = [int(v) for v in roi]
        patch = frame[y:y + h, x:x + w]
        if patch.size == 0:
            raise RuntimeError("empty ROI")
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        mean = hsv.reshape(-1, 3).mean(axis=0)
        # tolerance around mean hue/sat/val
        self._hsv_lo = np.array([
            max(0, mean[0] - 18), max(40, mean[1] - 60), max(40, mean[2] - 60)
        ], dtype=np.uint8)
        self._hsv_hi = np.array([
            min(179, mean[0] + 18), 255, 255
        ], dtype=np.uint8)
        self._wh = (max(8, w), max(8, h))
        return True

    def update(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self._hsv_lo, self._hsv_hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return False, (0, 0, 0, 0)
        c = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(c) < 40:
            return False, (0, 0, 0, 0)
        x, y, w, h = cv2.boundingRect(c)
        return True, (x, y, w, h)


def available_trackers() -> list:
    """รายชื่ออัลกอริทึมที่ใช้ได้บน OpenCV build นี้"""
    found = ["COLOR"]  # custom เสมอ
    probes = [
        ("MIL", lambda: cv2.TrackerMIL_create()),
        ("CSRT", lambda: cv2.TrackerCSRT_create()),
        ("KCF", lambda: cv2.TrackerKCF_create()),
        ("MOSSE", lambda: cv2.TrackerMOSSE_create()),
    ]
    # OpenCV 4.5+ legacy module / OpenCV 5 may differ
    try:
        legacy = getattr(cv2, "legacy", None)
        if legacy is not None:
            probes.extend([
                ("CSRT", lambda: legacy.TrackerCSRT_create()),
                ("KCF", lambda: legacy.TrackerKCF_create()),
                ("MOSSE", lambda: legacy.TrackerMOSSE_create()),
            ])
    except Exception:
        pass
    seen = set(found)
    for name, fn in probes:
        if name in seen:
            continue
        try:
            fn()
            found.append(name)
            seen.add(name)
        except Exception:
            continue
    return found


def _make_tracker(kind: TrackerKind):
    kind = (kind or "MIL").upper()
    if kind == "COLOR":
        return ColorCentroidTracker()

    creators = {
        "MIL": [
            lambda: cv2.TrackerMIL_create(),
        ],
        "CSRT": [
            lambda: cv2.TrackerCSRT_create(),
            lambda: getattr(cv2, "legacy").TrackerCSRT_create(),
        ],
        "KCF": [
            lambda: cv2.TrackerKCF_create(),
            lambda: getattr(cv2, "legacy").TrackerKCF_create(),
        ],
        "MOSSE": [
            lambda: cv2.TrackerMOSSE_create(),
            lambda: getattr(cv2, "legacy").TrackerMOSSE_create(),
        ],
    }
    # default fallback chain for unknown / missing
    chain = creators.get(kind, []) + creators.get("MIL", [])
    last_err = None
    for fn in chain:
        try:
            return fn()
        except Exception as e:
            last_err = e
            continue
    # last resort
    try:
        return ColorCentroidTracker()
    except Exception:
        pass
    raise RuntimeError(f"OpenCV tracker '{kind}' unavailable: {last_err}")


class DemoScene:
    """ฉากจำลอง: พื้นมืด + เป้าสี่เหลี่ยมเรืองแสงเคลื่อนที่ (ไม่มีกล้องก็เทส tracking ได้)"""

    def __init__(self, w: int = 640, h: int = 360):
        self.w, self.h = w, h
        self.t0 = time.time()
        self.target_wh = (56, 40)

    def frame(self) -> np.ndarray:
        t = time.time() - self.t0
        img = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        # soft vignette / grid atmosphere
        img[:] = (12, 14, 18)
        for x in range(0, self.w, 40):
            cv2.line(img, (x, 0), (x, self.h), (22, 26, 32), 1)
        for y in range(0, self.h, 40):
            cv2.line(img, (0, y), (self.w, y), (22, 26, 32), 1)

        # moving target (figure-8 + drift)
        cx = int(self.w * 0.5 + math.sin(t * 0.9) * self.w * 0.28)
        cy = int(self.h * 0.5 + math.sin(t * 1.7) * self.h * 0.22)
        tw, th = self.target_wh
        x1, y1 = cx - tw // 2, cy - th // 2
        x2, y2 = x1 + tw, y1 + th
        cv2.rectangle(img, (x1, y1), (x2, y2), (40, 220, 180), -1)
        cv2.rectangle(img, (x1, y1), (x2, y2), (180, 255, 240), 2)
        cv2.circle(img, (cx, cy), 4, (255, 255, 255), -1)
        cv2.putText(img, "DEMO TARGET", (x1, max(18, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 200, 190), 1, cv2.LINE_AA)
        return img

    def suggested_roi(self) -> Tuple[int, int, int, int]:
        """ROI รอบเป้าตอนเฟรมแรก — ใช้ auto-arm สำหรับ demo"""
        img = self.frame()
        # reset time so first real frame aligns roughly
        self.t0 = time.time()
        t = 0.0
        cx = int(self.w * 0.5 + math.sin(t * 0.9) * self.w * 0.28)
        cy = int(self.h * 0.5 + math.sin(t * 1.7) * self.h * 0.22)
        tw, th = self.target_wh
        pad = 8
        return (cx - tw // 2 - pad, cy - th // 2 - pad, tw + pad * 2, th + pad * 2)


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    return QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()


def draw_overlay(frame: np.ndarray, state: TrackState, roi_draft=None) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    # crosshair center
    cv2.drawMarker(out, (w // 2, h // 2), (90, 100, 120),
                   markerType=cv2.MARKER_CROSS, markerSize=18, thickness=1)
    if roi_draft is not None:
        x, y, rw, rh = [int(v) for v in roi_draft]
        cv2.rectangle(out, (x, y), (x + rw, y + rh), (80, 180, 255), 2)
    if state.ok and not state.lost:
        x, y, bw, bh = state.bbox
        cv2.rectangle(out, (x, y), (x + bw, y + bh), (60, 220, 140), 2)
        cv2.circle(out, (int(state.cx), int(state.cy)), 5, (60, 220, 140), -1)
        # line from center to target
        cv2.line(out, (w // 2, h // 2), (int(state.cx), int(state.cy)),
                 (60, 180, 255), 1, cv2.LINE_AA)
        label = f"LOCK  dx={state.dx:+.2f} dy={state.dy:+.2f}"
        cv2.putText(out, label, (x, max(16, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 220, 140), 1, cv2.LINE_AA)
    elif state.lost:
        cv2.putText(out, "TARGET LOST", (16, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 240), 2, cv2.LINE_AA)
    if state.message:
        cv2.putText(out, state.message[:48], (16, h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 190, 200), 1, cv2.LINE_AA)
    return out


class TrackerWorker(QThread):
    """ดึงเฟรม → track → ส่ง QImage + TrackState กลับ UI"""
    frame_ready = pyqtSignal(object, object)  # QImage, TrackState
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._running = False
        self._tracking = False
        self._source: SourceKind = "demo"
        self._file_path = ""
        self._camera_index = 0
        self._algo: TrackerKind = "MIL"
        self._roi: Optional[Tuple[int, int, int, int]] = None
        self._pending_roi: Optional[Tuple[int, int, int, int]] = None
        self._cap = None
        self._demo: Optional[DemoScene] = None
        self._tracker = None
        self._fps_limit = 20.0

    # ── public API (thread-safe-ish via mutex) ──
    def configure(self, source: SourceKind = "demo", file_path: str = "",
                  algo: TrackerKind = "MIL", camera_index: int = 0):
        self._mutex.lock()
        self._source = source
        self._file_path = file_path or ""
        self._algo = algo
        self._camera_index = int(camera_index)
        self._mutex.unlock()

    def set_roi(self, roi: Tuple[int, int, int, int]):
        self._mutex.lock()
        self._pending_roi = tuple(int(v) for v in roi)
        self._mutex.unlock()

    def start_tracking(self):
        self._mutex.lock()
        self._tracking = True
        self._mutex.unlock()

    def stop_tracking(self):
        self._mutex.lock()
        self._tracking = False
        self._tracker = None
        self._roi = None
        self._pending_roi = None
        self._mutex.unlock()

    def request_stop(self):
        self._mutex.lock()
        self._running = False
        self._mutex.unlock()

    def run(self):
        self._mutex.lock()
        self._running = True
        source = self._source
        file_path = self._file_path
        algo = self._algo
        cam_i = self._camera_index
        self._mutex.unlock()

        state = TrackState(message="starting…")
        try:
            if source == "demo":
                self._demo = DemoScene()
                self.status.emit("Demo scene ready — ลากเลือกเป้าแล้วกด TRACK")
            elif source == "file":
                if not file_path or not os.path.isfile(file_path):
                    self.status.emit("ไม่พบไฟล์วิดีโอ")
                    return
                self._cap = cv2.VideoCapture(file_path)
                if not self._cap.isOpened():
                    self.status.emit("เปิดไฟล์วิดีโอไม่ได้")
                    return
                self.status.emit(f"File: {os.path.basename(file_path)}")
            elif source == "camera":
                # จองไว้ — ยังไม่เปิดใช้ตามคำสั่งผู้ใช้
                self.status.emit("กล้องยังไม่เปิดใช้ในเฟสนี้ — เลือก Demo หรือ File")
                return
            else:
                self.status.emit(f"unknown source: {source}")
                return

            t_last = 0.0
            frame_i = 0
            while True:
                self._mutex.lock()
                running = self._running
                tracking = self._tracking
                pending = self._pending_roi
                self._pending_roi = None
                self._mutex.unlock()
                if not running:
                    break

                frame = self._read_frame()
                if frame is None:
                    state.message = "end of stream"
                    self.frame_ready.emit(bgr_to_qimage(
                        draw_overlay(np.zeros((360, 640, 3), np.uint8), state)), state)
                    break

                h, w = frame.shape[:2]
                if pending is not None:
                    x, y, bw, bh = pending
                    x = max(0, min(x, w - 2))
                    y = max(0, min(y, h - 2))
                    bw = max(4, min(bw, w - x))
                    bh = max(4, min(bh, h - y))
                    self._roi = (x, y, bw, bh)
                    try:
                        self._tracker = _make_tracker(algo)
                        self._tracker.init(frame, self._roi)
                        state = TrackState(ok=True, bbox=self._roi,
                                           cx=x + bw / 2, cy=y + bh / 2,
                                           message=f"tracker {algo} armed")
                        self.status.emit(f"Tracking ({algo})")
                    except Exception as e:
                        state = TrackState(message=f"init failed: {e}")
                        self.status.emit(str(e))
                        self._tracker = None

                if tracking and self._tracker is not None and self._roi is not None:
                    ok, box = self._tracker.update(frame)
                    if ok:
                        x, y, bw, bh = [int(v) for v in box]
                        cx, cy = x + bw / 2.0, y + bh / 2.0
                        state = TrackState(
                            ok=True, lost=False, bbox=(x, y, bw, bh),
                            cx=cx, cy=cy,
                            dx=(cx - w / 2) / (w / 2),
                            dy=(cy - h / 2) / (h / 2),
                            frame_i=frame_i,
                            message="locked",
                        )
                    else:
                        state = TrackState(ok=False, lost=True, frame_i=frame_i,
                                           message="lost — กด STOP แล้วเลือกเป้าใหม่")
                        self.status.emit("Target lost")
                else:
                    state.ok = False
                    if not state.message:
                        state.message = "idle — ลากเลือก ROI บนภาพ"

                # fps limit
                now = time.time()
                dt = now - t_last
                min_dt = 1.0 / self._fps_limit
                if dt < min_dt:
                    time.sleep(min_dt - dt)
                    now = time.time()
                state.fps = (1.0 / (now - t_last)) if t_last else 0.0
                t_last = now
                frame_i += 1
                state.frame_i = frame_i

                vis = draw_overlay(frame, state, roi_draft=self._roi if (not tracking) else None)
                self.frame_ready.emit(bgr_to_qimage(vis), state)

        finally:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self._tracker = None
            self.status.emit("stopped")

    def _read_frame(self) -> Optional[np.ndarray]:
        if self._demo is not None:
            return self._demo.frame()
        if self._cap is not None:
            ok, frame = self._cap.read()
            if not ok:
                # loop file for continuous demo feel
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
                if not ok:
                    return None
            return frame
        return None
