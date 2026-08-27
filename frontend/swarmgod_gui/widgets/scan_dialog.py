"""
scan_dialog.py — หน้าต่างสแกน IP ในวง LAN หาโดรน (พอร์ต MAVLink/SITL)
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QComboBox,
)

from ..core.theme import T, rgba, FONT_MONO, tinted_btn, ghost_btn, hairline
from ..core.ip_scan import (IpScanWorker, suggest_subnet, ScanHit, DEFAULT_PORTS,
                            split_host_port)


class ConnectIpDialog(QDialog):
    """หน้าต่างเชื่อมต่อโดยตรง ใช้รูปแบบเดียวกับหน้าสแกน แต่ไม่กินพื้นที่ Fleet."""
    connect_requested = pyqtSignal(str, int, str)  # host, port, protocol

    def __init__(self, endpoint="127.0.0.1:5760", protocol="tcp", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect Drone")
        self.setModal(True)
        self.resize(420, 220)
        self.setStyleSheet(
            f"QDialog {{ background:{T('panel')}; color:{T('text')}; }}"
            f"QLineEdit, QComboBox {{ background:{T('panel2')}; border:1px solid {hairline()};"
            f" border-radius:6px; padding:6px 8px; color:{T('text')};"
            f" font-family:{FONT_MONO}; font-size:12px; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        title = QLabel("CONNECT DRONE")
        title.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:700; letter-spacing:1.2px;")
        v.addWidget(title)
        hint = QLabel("Enter an IP address or host:port to connect directly.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.cmb_proto = QComboBox()
        self.cmb_proto.addItems(["TCP", "UDP"])
        self.cmb_proto.setCurrentText((protocol or "tcp").upper())
        self.cmb_proto.setFixedWidth(74)
        self.cmb_proto.currentTextChanged.connect(self._sync_default_port)
        row.addWidget(self.cmb_proto)
        self.ed_endpoint = QLineEdit(endpoint or "127.0.0.1:5760")
        self.ed_endpoint.setPlaceholderText("host:port")
        row.addWidget(self.ed_endpoint, 1)
        v.addLayout(row)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet(f"color:{T('red')}; font-size:10px;")
        v.addWidget(self.lbl_error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("CANCEL")
        cancel.setFixedHeight(32)
        cancel.setStyleSheet(ghost_btn(radius=7, font=11))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        connect = QPushButton("CONNECT")
        connect.setFixedHeight(32)
        connect.setStyleSheet(tinted_btn(T("green"), radius=7, font=11))
        connect.clicked.connect(self._connect)
        buttons.addWidget(connect)
        v.addLayout(buttons)

    def _sync_default_port(self, proto):
        current = self.ed_endpoint.text().strip()
        if proto == "UDP" and current in ("127.0.0.1:5760", ""):
            self.ed_endpoint.setText("127.0.0.1:14550")
        elif proto == "TCP" and current in ("127.0.0.1:14550", ""):
            self.ed_endpoint.setText("127.0.0.1:5760")

    def _connect(self):
        proto = self.cmb_proto.currentText().lower()
        default_port = 14550 if proto == "udp" else 5760
        host, port = split_host_port(self.ed_endpoint.text(), default_port)
        if not host:
            self.lbl_error.setText("Enter an IP address or host before connecting.")
            return
        self.connect_requested.emit(host, int(port), proto)
        self.accept()


class ScanIpDialog(QDialog):
    """สแกนวง → เลือกรายการ → ส่ง endpoint กลับไป connect"""
    connect_requested = pyqtSignal(str, int)  # host, port

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan LAN · หาโดรน")
        self.setModal(True)
        self.resize(420, 460)
        self.setStyleSheet(
            f"QDialog {{ background:{T('panel')}; color:{T('text')}; }}"
            f"QListWidget {{ background:{T('panel2')}; border:1px solid {hairline()};"
            f" border-radius:8px; color:{T('text')}; font-family:{FONT_MONO}; font-size:12px; }}"
            f"QListWidget::item {{ padding:8px 10px; }}"
            f"QListWidget::item:selected {{ background:{rgba(T('accent'), 0.25)}; }}"
            f"QLineEdit {{ background:{T('panel2')}; border:1px solid {hairline()};"
            f" border-radius:6px; padding:6px 8px; color:{T('text')};"
            f" font-family:{FONT_MONO}; font-size:12px; }}"
            f"QProgressBar {{ background:{T('panel2')}; border:1px solid {hairline()};"
            f" border-radius:6px; text-align:center; color:{T('dim')}; height:16px; }}"
            f"QProgressBar::chunk {{ background:{T('green')}; border-radius:5px; }}"
        )

        self._worker: IpScanWorker | None = None
        self._hits = []  # type: list[ScanHit]

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        title = QLabel("SCAN IP IN LAN")
        title.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:700; letter-spacing:1.2px;")
        v.addWidget(title)

        hint = QLabel(
            "สแกน TCP พอร์ต MAVLink/SITL ในวงเดียวกัน "
            f"({', '.join(str(p) for p in DEFAULT_PORTS[:6])}…)"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        lab = QLabel("Subnet")
        lab.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        row.addWidget(lab)
        self.ed_cidr = QLineEdit(suggest_subnet())
        self.ed_cidr.setPlaceholderText("192.168.1.0/24")
        row.addWidget(self.ed_cidr, 1)
        v.addLayout(row)

        self.lbl_status = QLabel("พร้อมสแกน")
        self.lbl_status.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-family:{FONT_MONO};")
        v.addWidget(self.lbl_status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        v.addWidget(self.bar)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._accept_item)
        self.list.itemChanged.connect(lambda _i: self._update_sel_count())
        self.list.currentItemChanged.connect(lambda *_: self._update_sel_count())
        v.addWidget(self.list, 1)

        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.btn_scan = QPushButton("SCAN")
        self.btn_scan.setFixedHeight(32)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.setStyleSheet(tinted_btn(T("green"), radius=7, font=11))
        self.btn_scan.clicked.connect(self._toggle_scan)
        brow.addWidget(self.btn_scan)

        self.btn_all = QPushButton("SELECT ALL")
        self.btn_all.setFixedHeight(32)
        self.btn_all.setCursor(Qt.PointingHandCursor)
        self.btn_all.setToolTip("ติ๊ก/ยกเลิกทุก IP ที่เจอ")
        self.btn_all.setStyleSheet(ghost_btn(radius=7, font=11))
        self.btn_all.clicked.connect(self._toggle_select_all)
        brow.addWidget(self.btn_all)

        self.btn_use = QPushButton("CONNECT")
        self.btn_use.setFixedHeight(32)
        self.btn_use.setCursor(Qt.PointingHandCursor)
        self.btn_use.setStyleSheet(tinted_btn(T("accent"), radius=7, font=11))
        self.btn_use.clicked.connect(self._connect_selected)
        brow.addWidget(self.btn_use)

        self.btn_close = QPushButton("CLOSE")
        self.btn_close.setFixedHeight(32)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setStyleSheet(ghost_btn(radius=7, font=11))
        self.btn_close.clicked.connect(self.reject)
        brow.addWidget(self.btn_close)
        v.addLayout(brow)

    def showEvent(self, e):
        super().showEvent(e)
        if self.list.count() == 0 and self._worker is None:
            # เปิดแล้วเริ่มสแกนอัตโนมัติครั้งแรก
            self._start_scan()

    def closeEvent(self, e):
        self._stop_scan()
        super().closeEvent(e)

    def _toggle_scan(self):
        if self._worker and self._worker.isRunning():
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        self._stop_scan()
        self.list.clear()
        self._hits.clear()
        self._update_sel_count()
        self.bar.setValue(0)
        self.btn_scan.setText("STOP")
        self.lbl_status.setText("scanning…")
        self._worker = IpScanWorker(cidr=self.ed_cidr.text().strip() or suggest_subnet())
        self._worker.progress.connect(self._on_progress)
        self._worker.found.connect(self._on_found)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _stop_scan(self):
        w = self._worker
        self._worker = None
        if w is not None:
            w.stop()
            w.wait(1500)
        self.btn_scan.setText("SCAN")

    def _on_progress(self, done: int, total: int, msg: str):
        pct = int(100 * done / max(1, total))
        self.bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_found(self, hit: ScanHit):
        self._hits.append(hit)
        item = QListWidgetItem(f"{hit.endpoint}   ·   {hit.ms:.0f} ms")
        item.setData(Qt.UserRole, (hit.host, hit.port))
        # ติ๊กเลือกได้รายแถว — เดิมเลือกได้ทีละแถว (currentItem) แล้วต่อได้ทีละลำเท่านั้น
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        self.list.addItem(item)
        self._update_sel_count()

    def _checked_endpoints(self):
        """คืน [(host, port), ...] ของแถวที่ติ๊กไว้"""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                data = it.data(Qt.UserRole)
                if data:
                    out.append((data[0], int(data[1])))
        return out

    def _set_all_checked(self, on: bool):
        state = Qt.Checked if on else Qt.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self._update_sel_count()

    def _toggle_select_all(self):
        # ถ้ายังติ๊กไม่ครบ → ติ๊กทั้งหมด, ถ้าครบแล้ว → เอาออกทั้งหมด
        total = self.list.count()
        self._set_all_checked(len(self._checked_endpoints()) < total)

    def _update_sel_count(self):
        n = len(self._checked_endpoints())
        total = self.list.count()
        self.btn_all.setText("CLEAR ALL" if n and n == total else "SELECT ALL")
        self.btn_use.setText(f"CONNECT ({n})" if n else "CONNECT")
        self.btn_use.setEnabled(bool(n) or self.list.currentItem() is not None)

    def _on_done(self, n: int):
        self.btn_scan.setText("SCAN")
        self.bar.setValue(100)
        self.lbl_status.setText(f"done · found {n} endpoint(s)")
        self._worker = None

    def _on_fail(self, err: str):
        self.btn_scan.setText("SCAN")
        self.lbl_status.setText(f"fail · {err}")
        self._worker = None

    def _selected_endpoint(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _accept_item(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole)
        if data:
            self.connect_requested.emit(data[0], int(data[1]))
            self.accept()

    def _connect_selected(self):
        """เชื่อมต่อทุก IP ที่ติ๊กไว้พร้อมกัน (ถ้าไม่ได้ติ๊ก ใช้แถวที่ไฮไลต์อยู่)"""
        targets = self._checked_endpoints()
        if not targets:
            data = self._selected_endpoint()
            if not data:
                self.lbl_status.setText("ติ๊กเลือก IP ก่อน (หรือกด SELECT ALL) แล้วกด CONNECT")
                return
            targets = [(data[0], int(data[1]))]
        for host, port in targets:
            self.connect_requested.emit(host, int(port))
        self.accept()
