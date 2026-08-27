"""
cv_track_panel.py — แผง CV Target Tracking ใน cockpit
ยังไม่ต่อกล้องจริง — ใช้ Demo scene หรือไฟล์วิดีโอ
ลากเมาส์บนภาพเพื่อเลือก ROI แล้วกด TRACK
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt5.QtGui import QPixmap, QPainter, QPen, QColor, QImage
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QFileDialog, QSizePolicy, QFrame,
)

from ..core.theme import T, rgba, tinted_btn, filled_btn, ghost_btn, FONT_MONO
from ..core.cv_tracker import TrackerWorker, TrackState


class VideoView(QLabel):
    """แสดงเฟรม + ลากเลือก ROI (พิกัดเป็นสเกลของภาพที่โชว์ → map กลับไปขนาดเฟรมจริง)"""
    roi_selected = pyqtSignal(int, int, int, int)  # x,y,w,h ในพิกัดเฟรมต้นฉบับ

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(168)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"background:{T('bg')}; border:1px solid {T('line')}; border-radius:10px;")
        self.setCursor(Qt.CrossCursor)
        self._pix = QPixmap()
        self._src_w = 640
        self._src_h = 360
        self._dragging = False
        self._origin = QPoint()
        self._rubber = QRect()

    def set_frame(self, img: QImage, src_w: int = 0, src_h: int = 0):
        if src_w and src_h:
            self._src_w, self._src_h = src_w, src_h
        elif not img.isNull():
            self._src_w, self._src_h = img.width(), img.height()
        self._pix = QPixmap.fromImage(img)
        self._paint_display()

    def _paint_display(self):
        if self._pix.isNull():
            self.setText("NO SIGNAL")
            return
        scaled = self._pix.scaled(self.width() - 4, self.height() - 4,
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self._dragging and not self._rubber.isNull():
            canvas = QPixmap(scaled)
            p = QPainter(canvas)
            pen = QPen(QColor(T("accent")))
            pen.setWidth(2)
            p.setPen(pen)
            # rubber is in widget coords relative to centered pixmap — approx using label
            p.drawRect(self._rubber)
            p.end()
            self.setPixmap(canvas)
        else:
            self.setPixmap(scaled)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._paint_display()

    def _content_rect(self) -> QRect:
        """พื้นที่ภาพจริงภายใน label (หลัง KeepAspectRatio)"""
        if self._pix.isNull():
            return self.rect()
        pw, ph = self._pix.width(), self._pix.height()
        avail_w, avail_h = max(1, self.width() - 4), max(1, self.height() - 4)
        scale = min(avail_w / pw, avail_h / ph)
        dw, dh = int(pw * scale), int(ph * scale)
        x = (self.width() - dw) // 2
        y = (self.height() - dh) // 2
        return QRect(x, y, dw, dh)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._origin = e.pos()
            self._rubber = QRect(self._origin, self._origin)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._rubber = QRect(self._origin, e.pos()).normalized()
            self._paint_display()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.LeftButton:
            self._dragging = False
            cr = self._content_rect()
            r = self._rubber.intersected(cr)
            self._rubber = QRect()
            self._paint_display()
            if r.width() >= 8 and r.height() >= 8 and cr.width() > 0 and cr.height() > 0:
                # map widget → source frame
                sx = (r.x() - cr.x()) / cr.width() * self._src_w
                sy = (r.y() - cr.y()) / cr.height() * self._src_h
                sw = r.width() / cr.width() * self._src_w
                sh = r.height() / cr.height() * self._src_h
                self.roi_selected.emit(int(sx), int(sy), int(sw), int(sh))
        super().mouseReleaseEvent(e)


class CvTrackPanel(QWidget):
    """แผงควบคุม CV tracking — emit log/status ขึ้น mission log ได้"""
    logged = pyqtSignal(str, str)          # msg, severity
    track_update = pyqtSignal(object)      # TrackState

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: TrackerWorker | None = None
        self._roi = None
        self._last_state = TrackState()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        hint = QLabel("ลากบนภาพเลือกเป้า → TRACK  ·  กล้องจริงยังไม่เปิดในเฟสนี้")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        root.addWidget(hint)

        self.view = VideoView()
        self.view.setFixedHeight(168)
        self.view.roi_selected.connect(self._on_roi)
        root.addWidget(self.view)

        # source + algo
        row = QHBoxLayout()
        row.setSpacing(6)
        self.cmb_src = QComboBox()
        self.cmb_src.addItem("Demo", "demo")
        self.cmb_src.addItem("File…", "file")
        # camera reserved — visible but disabled
        self.cmb_src.addItem("Camera (soon)", "camera")
        # disable camera item
        model = self.cmb_src.model()
        item = model.item(2)
        if item is not None:
            item.setEnabled(False)
        self.cmb_src.setStyleSheet(self._combo_qss())
        self.cmb_src.currentIndexChanged.connect(self._on_src_changed)
        row.addWidget(self.cmb_src, 1)

        self.cmb_algo = QComboBox()
        from ..core.cv_tracker import available_trackers
        for name in available_trackers():
            self.cmb_algo.addItem(name, name)
        # prefer MIL ถ้ามี ไม่งั้น COLOR
        prefer = "MIL" if self.cmb_algo.findData("MIL") >= 0 else "COLOR"
        idx = self.cmb_algo.findData(prefer)
        if idx >= 0:
            self.cmb_algo.setCurrentIndex(idx)
        self.cmb_algo.setStyleSheet(self._combo_qss())
        row.addWidget(self.cmb_algo)
        root.addLayout(row)

        self.lbl_file = QLabel("")
        self.lbl_file.setStyleSheet(f"color:{T('dim')}; font-size:10px;")
        self.lbl_file.setVisible(False)
        root.addWidget(self.lbl_file)

        # buttons
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.btn_start = QPushButton("▶  PREVIEW")
        self.btn_start.setFixedHeight(34)
        self.btn_start.setStyleSheet(tinted_btn(T("cyan"), radius=9, font=12))
        self.btn_start.clicked.connect(self.start_preview)

        self.btn_track = QPushButton("TRACK")
        self.btn_track.setFixedHeight(34)
        self.btn_track.setStyleSheet(filled_btn(T("green"), radius=9, font=12))
        self.btn_track.clicked.connect(self.start_track)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setFixedHeight(34)
        self.btn_stop.setStyleSheet(ghost_btn(radius=9, font=12))
        self.btn_stop.clicked.connect(self.stop_all)
        brow.addWidget(self.btn_start)
        brow.addWidget(self.btn_track)
        brow.addWidget(self.btn_stop)
        root.addLayout(brow)

        # status strip
        strip = QFrame()
        strip.setStyleSheet(
            f"background:{rgba('#ffffff', 0.04)}; border-radius:8px;")
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(8, 6, 8, 6)
        sl.setSpacing(2)
        self.lbl_status = QLabel("idle")
        self.lbl_status.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:600;")
        self.lbl_metrics = QLabel("dx --  ·  dy --  ·  fps --")
        self.lbl_metrics.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        sl.addWidget(self.lbl_status)
        sl.addWidget(self.lbl_metrics)
        root.addWidget(strip)

        self._file_path = ""

    def _combo_qss(self) -> str:
        return (
            f"QComboBox {{ background:{rgba('#ffffff', 0.06)}; color:{T('text')};"
            f" border:none; border-radius:8px; padding:6px 10px; font-size:12px; }}"
            f"QComboBox::drop-down {{ border:none; width:20px; }}"
            f"QComboBox QAbstractItemView {{ background:{T('panel2')}; color:{T('text')};"
            f" selection-background-color:{T('accent')}; }}"
        )

    def _on_src_changed(self, _i=None):
        src = self.cmb_src.currentData()
        if src == "file":
            path, _ = QFileDialog.getOpenFileName(
                self, "เลือกไฟล์วิดีโอ", "",
                "Video (*.mp4 *.avi *.mov *.mkv);;All (*.*)")
            if path:
                self._file_path = path
                self.lbl_file.setText(os.path.basename(path))
                self.lbl_file.setVisible(True)
            else:
                self.cmb_src.setCurrentIndex(0)
                self.lbl_file.setVisible(False)
        else:
            self.lbl_file.setVisible(False)

    def _on_roi(self, x, y, w, h):
        self._roi = (x, y, w, h)
        self.lbl_status.setText(f"ROI {w}×{h} @ ({x},{y})")
        self.lbl_status.setStyleSheet(
            f"color:{T('accent')}; font-size:11px; font-weight:600;")
        if self._worker and self._worker.isRunning():
            self._worker.set_roi(self._roi)
        self.logged.emit(f"CV ROI selected {w}×{h}", "info")

    def start_preview(self):
        self.stop_all()
        src = self.cmb_src.currentData() or "demo"
        if src == "camera":
            self.lbl_status.setText("กล้องยังไม่เปิดใช้")
            return
        if src == "file" and not self._file_path:
            self._on_src_changed()
            if not self._file_path:
                return
        self._worker = TrackerWorker()
        self._worker.configure(
            source=src,
            file_path=self._file_path,
            algo=self.cmb_algo.currentData() or "MIL",
        )
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.status.connect(self._on_worker_status)
        self._worker.start()
        self.lbl_status.setText("preview…")
        self.logged.emit(f"CV preview ({src}/{self.cmb_algo.currentData()})", "info")

    def start_track(self):
        if self._worker is None or not self._worker.isRunning():
            self.start_preview()
        if self._roi is None:
            # demo: auto ROI รอบเป้าเริ่มต้น
            if (self.cmb_src.currentData() or "demo") == "demo":
                # กลางจอดำเนินการประมาณ — ให้ user ลากดีกว่า แต่ช่วย hint
                self.lbl_status.setText("ลากเลือกเป้าบนภาพก่อน")
                self.lbl_status.setStyleSheet(
                    f"color:{T('amber')}; font-size:11px; font-weight:600;")
                self.logged.emit("CV track: ยังไม่ได้เลือก ROI", "warn")
                return
            self.lbl_status.setText("ลากเลือกเป้าบนภาพก่อน")
            return
        self._worker.configure(algo=self.cmb_algo.currentData() or "MIL")
        self._worker.set_roi(self._roi)
        self._worker.start_tracking()
        self.lbl_status.setText("TRACKING")
        self.lbl_status.setStyleSheet(
            f"color:{T('green')}; font-size:11px; font-weight:700;")
        self.logged.emit("CV tracking started", "success")

    def stop_all(self):
        if self._worker is not None:
            self._worker.stop_tracking()
            self._worker.request_stop()
            self._worker.wait(1500)
            self._worker = None
        self.lbl_status.setText("stopped")
        self.lbl_status.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:600;")
        self.lbl_metrics.setText("dx --  ·  dy --  ·  fps --")

    def _on_frame(self, qimg: QImage, state: TrackState):
        self._last_state = state
        self.view.set_frame(qimg, qimg.width(), qimg.height())
        if state.ok and not state.lost:
            self.lbl_metrics.setText(
                f"dx {state.dx:+.2f}  ·  dy {state.dy:+.2f}  ·  fps {state.fps:.0f}")
            self.lbl_status.setText("LOCKED")
            self.lbl_status.setStyleSheet(
                f"color:{T('green')}; font-size:11px; font-weight:700;")
        elif state.lost:
            self.lbl_status.setText("LOST")
            self.lbl_status.setStyleSheet(
                f"color:{T('red')}; font-size:11px; font-weight:700;")
        self.track_update.emit(state)

    def _on_worker_status(self, msg: str):
        if msg and not (self._last_state.ok and not self._last_state.lost):
            self.lbl_status.setText(msg[:60])

    def shutdown(self):
        self.stop_all()
