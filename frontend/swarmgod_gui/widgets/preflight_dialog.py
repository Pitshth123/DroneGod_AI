"""
preflight_dialog.py — popup 2 ใบสำหรับ "ก่อนบินจริง"

  1) PreflightDialog  — ปุ่ม «ทดสอบระบบก่อนบิน»: ไล่ตรวจเป็นรายการ
                        (สถานะเชื่อมต่อ · ความพร้อมรายลำ · ระยะห่าง · ค่าตั้ง ·
                         แล้วยิงคำสั่งจริงพิสูจน์ว่าสั่งโหมดแล้ว FC ตอบรับ)
  2) ChecklistDialog  — ปุ่ม «เช็คลิสต์ก่อนบินจริง»: เอา docs/REAL_FLIGHT_CHECKLIST.md
                        ทำเป็น checkbox ติ๊กได้จริง และรีเซ็ตใหม่ทุกครั้งที่เปิดโปรแกรม

  3) ask_before_takeoff() — กล่องถามตอนกด TAKEOFF ทั้งที่ยังไม่ได้เทส
                        (เทสก่อน / ไม่เทส บินเลย / ยกเลิก)

ทั้งหมดคุยกับ cockpit ผ่าน callback เท่านั้น (snapshot_fn / telem_fn / client)
จึงเทสได้ด้วย fake client แบบ headless
"""
import threading
import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame, QCheckBox, QMessageBox, QSizePolicy,
)

from ..core import preflight as pf
from ..core.theme import T, rgba, FONT_FAMILY, FONT_MONO, tinted_btn, ghost_btn

try:                                    # โหมด GUIDED จาก proto (fallback ถ้า import ไม่ได้)
    from ..core import rpc as _rpc
    _GUIDED = _rpc.common_pb2.FLIGHT_MODE_GUIDED
except Exception:                       # pragma: no cover
    _GUIDED = 5

_ICON = {pf.PASS: "✓", pf.FAIL: "✕", pf.WARN: "!", pf.SKIP: "–", pf.PENDING: "·"}
_COLOR = {pf.PASS: "green", pf.FAIL: "red", pf.WARN: "amber",
          pf.SKIP: "faint", pf.PENDING: "faint"}
_LABEL = {pf.PASS: "ผ่าน", pf.FAIL: "ไม่ผ่าน", pf.WARN: "เตือน",
          pf.SKIP: "ข้าม", pf.PENDING: "กำลังตรวจ…"}


def _checkbox_qss(color, size=16, font=12):
    """ช่องติ๊กที่วาดเองทั้งใบ

    ถ้าตั้งแค่ width/height ให้ ::indicator Qt จะทิ้ง indicator ของธีมเครื่องไป
    แล้ววาดกล่องเปล่า — มองไม่ออกว่าติ๊กหรือยัง ต้องกำหนดสีเองให้ครบทุกสถานะ
    """
    return (f"QCheckBox {{ color:{T('text')}; font-size:{font}px; padding:3px 2px; }}"
            f"QCheckBox:checked {{ color:{color}; }}"
            f"QCheckBox::indicator {{ width:{size}px; height:{size}px; border-radius:4px;"
            f" border:1px solid {rgba('#ffffff', 0.30)};"
            f" background:{rgba('#ffffff', 0.05)}; }}"
            f"QCheckBox::indicator:hover {{ border:1px solid {rgba(color, 0.75)}; }}"
            f"QCheckBox::indicator:checked {{ background:{color};"
            f" border:1px solid {color}; }}")


def _dialog_qss():
    return (f"QDialog {{ background:{T('bg')}; }}"
            f"QLabel {{ color:{T('text')}; font-family:{FONT_FAMILY}; }}"
            f"QScrollArea {{ border:none; background:transparent; }}"
            f"QScrollArea > QWidget > QWidget {{ background:transparent; }}")


class _Row(QFrame):
    """หนึ่งบรรทัดผลตรวจ — ไอคอนสถานะ + หัวข้อ + รายละเอียด"""

    def __init__(self, title, detail="", status=pf.PENDING, severity=pf.CRITICAL,
                 parent=None):
        super().__init__(parent)
        self._severity = severity
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 6, 9, 6)
        lay.setSpacing(9)

        self.icon = QLabel()
        self.icon.setFixedWidth(20)
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.lb_title = QLabel(title)
        self.lb_title.setWordWrap(True)
        self.lb_title.setStyleSheet(f"color:{T('text')}; font-size:12px; font-weight:600;")
        col.addWidget(self.lb_title)
        self.lb_detail = QLabel(detail)
        self.lb_detail.setWordWrap(True)
        self.lb_detail.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        col.addWidget(self.lb_detail)
        lay.addLayout(col, 1)

        self.lb_status = QLabel()
        self.lb_status.setFixedWidth(62)
        self.lb_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.lb_status)

        self.set_status(status, detail)

    def set_status(self, status, detail=None):
        color = T(_COLOR.get(status, "faint"))
        self.icon.setText(_ICON.get(status, "·"))
        self.icon.setStyleSheet(
            f"color:{color}; font-size:14px; font-weight:800;"
            f" background:{rgba(color, 0.14)}; border-radius:10px;")
        self.icon.setFixedSize(20, 20)
        self.lb_status.setText(_LABEL.get(status, ""))
        self.lb_status.setStyleSheet(
            f"color:{color}; font-size:10px; font-weight:700; font-family:{FONT_MONO};")
        if detail is not None:
            self.lb_detail.setText(detail)
            self.lb_detail.setVisible(bool(detail))
        crit = (status == pf.FAIL and self._severity == pf.CRITICAL)
        self.setStyleSheet(
            f"QFrame {{ border-radius:7px;"
            f" background:{rgba(T('red'), 0.10) if crit else rgba('#ffffff', 0.02)};"
            f" border:1px solid {rgba(T('red'), 0.35) if crit else 'transparent'}; }}")


class PreflightDialog(QDialog):
    """ทดสอบระบบก่อนบิน — ตรวจแบบทันที + ยิงคำสั่งจริงพิสูจน์เส้นทางสั่งการ"""

    _row_done = pyqtSignal(object)      # CheckResult
    _all_done = pyqtSignal()

    def __init__(self, parent=None, *, client=None, snapshot_fn=None,
                 telem_fn=None, target_ids=None, log_fn=None, dispatch_fn=None):
        super().__init__(parent)
        self.setWindowTitle("ทดสอบระบบก่อนบินจริง")
        self.setMinimumSize(600, 620)
        self.setStyleSheet(_dialog_qss())

        self._client = client
        self._snapshot_fn = snapshot_fn or (lambda: {})
        self._telem_fn = telem_fn or (lambda did: {})
        self._target_ids = list(target_ids or [])
        self._log = log_fn or (lambda *a, **k: None)
        # Optional observable command boundary supplied by GroundStation. Tests
        # and standalone dialog use can omit it and retain the exact legacy
        # direct-client behavior.
        self._dispatch_fn = dispatch_fn

        self.results = []               # CheckResult ที่ตรวจเสร็จแล้ว
        self.passed = False
        self.summary = pf.Summary()
        self._rows = {}                 # key -> _Row
        self._running = False

        self._row_done.connect(self._on_row_done)
        self._all_done.connect(self._on_all_done)
        self._build()

    # ── UI ───────────────────────────────────────────────
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title = QLabel("ทดสอบระบบก่อนบินจริง")
        title.setStyleSheet(f"color:{T('text')}; font-size:17px; font-weight:800;")
        v.addWidget(title)
        sub = QLabel("ตรวจเฉพาะข้อที่มีผลต่อความปลอดภัยและความถูกต้องของการสั่งการ · "
                     "ไม่ใช่การรับรองว่า airframe/FC ผ่านการตรวจแล้ว")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(sub)

        tgt = ", ".join("D%d" % d for d in self._target_ids) or "— ยังไม่ได้เลือกลำ —"
        self.lb_target = QLabel("เป้าหมายที่จะตรวจ: " + tgt)
        self.lb_target.setWordWrap(True)
        self.lb_target.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-family:{FONT_MONO};"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:6px 9px;")
        v.addWidget(self.lb_target)

        self.cb_bench = QCheckBox("ถอดใบพัดแล้ว — ให้ทดสอบ ARM/DISARM และการปฏิเสธ "
                                  "TAKEOFF ที่ไม่ยืนยันด้วย (bench test)")
        self.cb_bench.setStyleSheet(_checkbox_qss(T("amber"), size=15, font=11))
        v.addWidget(self.cb_bench)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 6, 0)
        self._body_lay.setSpacing(4)
        self._body_lay.addStretch(1)
        self.scroll.setWidget(self._body)
        v.addWidget(self.scroll, 1)

        self.lb_result = QLabel("ยังไม่ได้เริ่มทดสอบ — กด RUN TEST")
        self.lb_result.setWordWrap(True)
        self.lb_result.setStyleSheet(
            f"color:{T('dim')}; font-size:12px; font-weight:700;"
            f" background:{rgba('#ffffff', 0.04)}; border-radius:8px; padding:9px 12px;")
        v.addWidget(self.lb_result)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_run = QPushButton("RUN TEST")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setStyleSheet(tinted_btn(T("green"), radius=9, font=13))
        self.btn_run.clicked.connect(self.run_tests)
        row.addWidget(self.btn_run, 2)
        self.btn_close = QPushButton("CLOSE")
        self.btn_close.setMinimumHeight(38)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setStyleSheet(ghost_btn(radius=9, font=12))
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_close, 1)
        v.addLayout(row)

    def _clear_rows(self):
        while self._body_lay.count() > 1:
            item = self._body_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._rows.clear()

    def _add_group(self, name):
        lb = QLabel(name + "  ·  " + pf.GROUP_TITLE.get(name, ""))
        lb.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:800; letter-spacing:1.2px;"
            f" padding:8px 2px 2px 2px;")
        self._body_lay.insertWidget(self._body_lay.count() - 1, lb)

    def _add_row(self, key, title, detail, status, severity):
        row = _Row(title, detail, status, severity)
        self._rows[key] = row
        self._body_lay.insertWidget(self._body_lay.count() - 1, row)
        return row

    # ── รันเทส ────────────────────────────────────────────
    def run_tests(self):
        if self._running:
            return
        bench = self.cb_bench.isChecked()
        if bench and not _confirm(
                self, "ยืนยัน bench test",
                "รายการ bench จะสั่ง ARM จริง — มอเตอร์จะหมุน\n\n"
                "ยืนยันว่าถอดใบพัดออกหมดแล้วทุกลำ?",
                ok_text="PROPS OFF · RUN"):
            return

        self._running = True
        self.btn_run.setEnabled(False)
        self.btn_run.setText("TESTING…")
        # ปิดทางออกระหว่างรัน — worker thread ยัง emit สัญญาณกลับมาที่ dialog นี้อยู่
        # ถ้าปล่อยให้ปิดกลางคัน object ฝั่ง C++ จะถูกลบก่อน signal มาถึง
        self.btn_close.setEnabled(False)
        self.results = []
        self._clear_rows()

        # 1) ตรวจแบบทันที (ไม่ยิง RPC)
        snap = self._snapshot_fn() or {}
        static = pf.evaluate_static(snap)
        group = None
        for r in static:
            if r.group != group:
                group = r.group
                self._add_group(group)
            self._add_row(r.key, r.title, r.detail, r.status, r.severity)
            self.results.append(r)

        # 2) รายการที่ต้องยิงคำสั่งจริง — ขึ้นแถวรอไว้ก่อน
        self._add_group("COMMAND")
        live = pf.live_checks(bench)
        for c in pf.LIVE_CHECKS:
            self._add_row(c.key, c.title, c.note, pf.PENDING, c.severity)
        skipped = [c for c in pf.LIVE_CHECKS if c not in live]
        for c in skipped:
            r = pf.CheckResult(c.key, c.title, c.group, c.severity, pf.SKIP,
                               "ข้าม — ต้องติ๊ก «ถอดใบพัดแล้ว» ก่อนถึงจะทดสอบข้อนี้")
            self.results.append(r)
            self._rows[c.key].set_status(pf.SKIP, r.detail)

        ids = [int(d) for d in self._target_ids]
        blocking_now = [r for r in static if r.blocking]
        if blocking_now or not ids or self._client is None:
            # มีข้อ critical ไม่ผ่านตั้งแต่ยังไม่ยิงคำสั่ง → ไม่สั่งอะไรกับโดรนเลย
            why = ("ข้าม — ยังมีข้อสำคัญไม่ผ่าน จึงไม่ยิงคำสั่งใด ๆ ใส่โดรน"
                   if blocking_now else "ข้าม — ไม่มีโดรนเป้าหมาย/ยังไม่ได้ต่อ core")
            for c in live:
                r = pf.CheckResult(c.key, c.title, c.group, c.severity, pf.SKIP, why)
                self.results.append(r)
                self._rows[c.key].set_status(pf.SKIP, why)
            self._on_all_done()
            return

        threading.Thread(target=self._live_worker, args=(live, ids), daemon=True).start()

    def _live_worker(self, live, ids):
        for c in live:
            try:
                status, detail = self._run_live(c, ids)
            except Exception as e:                       # pragma: no cover
                status, detail = pf.FAIL, "ERROR %s" % e
            self._row_done.emit(
                pf.CheckResult(c.key, c.title, c.group, c.severity, status, detail))
        self._all_done.emit()

    def _dispatch_live(self, label, ids, invoke):
        """Route preflight flight-mutating checks through the cockpit gateway.

        Preflight is an intentional verification sequence, not a double-click UI
        action, so the supplied dispatcher must preserve execution order and may
        disable frontend dedup while still attaching correlation/observability.
        Standalone/tests without a dispatcher keep legacy direct-client behavior.
        """
        if callable(self._dispatch_fn):
            return self._dispatch_fn(label, invoke, list(ids))
        return invoke()

    def _run_live(self, check, ids):
        cl = self._client
        if check.key == "cmd.hold":
            r = self._dispatch_live("HOLD [preflight]", ids, lambda: cl.hold(ids))
            ok = bool(getattr(r, "ok", False))
            return (pf.PASS if ok else pf.WARN,
                    "core ตอบรับ" if ok else "core ปฏิเสธ: %s"
                    % (getattr(r, "message", "") or "ไม่ทราบสาเหตุ"))

        if check.key == "cmd.mode_guided":
            r = self._dispatch_live(
                "MODE GUIDED [preflight]", ids, lambda: cl.set_mode(ids, _GUIDED))
            if not bool(getattr(r, "ok", False)):
                return pf.FAIL, "core ปฏิเสธ: %s" % (getattr(r, "message", "") or "-")
            bad = self._await(ids, lambda t: (t.get("mode_name") or "").upper() == "GUIDED",
                              8.0)
            if bad:
                return pf.FAIL, ("สั่งแล้ว FC ไม่รายงานโหมด GUIDED ใน 8 วิ: "
                                 + ", ".join("D%d" % d for d in bad))
            return pf.PASS, "ทุกลำรายงานโหมด GUIDED กลับมาแล้ว"

        if check.key == "cmd.reject_unconfirmed":
            r = self._dispatch_live(
                "TAKEOFF UNCONFIRMED [preflight]", ids,
                lambda: cl.takeoff(ids, 2.0, confirmed=False))
            if bool(getattr(r, "ok", False)):
                # อันตราย: core ยอมรับ takeoff ที่ไม่ยืนยัน → ดับมอเตอร์ทันที
                try:
                    self._dispatch_live(
                        "DISARM RECOVERY [preflight]", ids,
                        lambda: cl.disarm(ids, confirmed=True))
                except Exception:
                    pass
                return pf.FAIL, ("core ยอมรับ TAKEOFF ที่ไม่ได้ยืนยัน — "
                                 "สั่ง DISARM กลับแล้ว ห้ามบินจนกว่าจะแก้ที่ core")
            return pf.PASS, "ถูกปฏิเสธตามที่ควรเป็น: %s" % (
                getattr(r, "message", "") or "rejected")

        if check.key == "cmd.arm_disarm":
            r = self._dispatch_live("ARM [preflight]", ids, lambda: cl.arm(ids))
            if not bool(getattr(r, "ok", False)):
                return pf.FAIL, "ARM ไม่ผ่าน: %s" % (getattr(r, "message", "") or "-")
            not_armed = self._await(ids, lambda t: bool(t.get("armed")), 10.0)
            self._dispatch_live(
                "DISARM [preflight]", ids, lambda: cl.disarm(ids, confirmed=True))
            still_armed = self._await(ids, lambda t: not t.get("armed"), 10.0)
            if not_armed:
                return pf.FAIL, ("สั่ง ARM แล้วไม่ armed จริง: "
                                 + ", ".join("D%d" % d for d in not_armed))
            if still_armed:
                return pf.FAIL, ("⚠ DISARM ไม่สำเร็จ ยัง armed อยู่: "
                                 + ", ".join("D%d" % d for d in still_armed))
            return pf.PASS, "ARM ผ่าน pre-arm และ DISARM กลับได้ครบทุกลำ"

        return pf.SKIP, "ไม่รู้จักรายการนี้"

    def _await(self, ids, cond, timeout_s):
        """รอจน cond(telemetry) เป็นจริงครบทุกลำ — คืน list ของลำที่ยังไม่ผ่าน"""
        deadline = time.monotonic() + timeout_s
        pending = list(ids)
        while pending and time.monotonic() < deadline:
            pending = [d for d in pending if not cond(self._telem_fn(d) or {})]
            if pending:
                time.sleep(0.25)
        return pending

    # ── สรุป ─────────────────────────────────────────────
    def _on_row_done(self, res):
        self.results.append(res)
        row = self._rows.get(res.key)
        if row is not None:
            row.set_status(res.status, res.detail)

    def reject(self):
        if self._running:               # Esc ระหว่างรัน — ไม่ให้ปิด
            return
        super().reject()

    def closeEvent(self, e):
        if self._running:               # ปุ่ม X ระหว่างรัน — ไม่ให้ปิด
            e.ignore()
            return
        super().closeEvent(e)

    def _on_all_done(self):
        self._running = False
        self.btn_run.setEnabled(True)
        self.btn_close.setEnabled(True)
        self.btn_run.setText("RUN TEST AGAIN")
        self.summary = pf.summarize(self.results)
        self.passed = self.summary.ok
        color = T("green") if self.passed else T("red")
        head = "ผ่านการทดสอบ — พร้อมบิน" if self.passed else "ไม่ผ่าน — ห้ามบินจนกว่าจะแก้"
        fails = [r.title for r in self.results if r.blocking]
        detail = ("\nข้อที่ต้องแก้: " + " · ".join(fails)) if fails else ""
        self.lb_result.setText("%s\n%s%s" % (head, self.summary.text(), detail))
        self.lb_result.setStyleSheet(
            f"color:{color}; font-size:12px; font-weight:700;"
            f" background:{rgba(color, 0.12)}; border:1px solid {rgba(color, 0.35)};"
            f" border-radius:8px; padding:9px 12px;")
        self._log("PREFLIGHT SELF-TEST: %s · %s"
                  % ("ผ่าน" if self.passed else "ไม่ผ่าน", self.summary.text()))


class ChecklistDialog(QDialog):
    """เช็คลิสต์ก่อนบินจริงที่ Codex เขียนไว้ — docs/REAL_FLIGHT_CHECKLIST.md"""
    _session_checked = {}

    def __init__(self, parent=None, *, sections=None, checked=None,
                 store_path=None, log_fn=None):
        super().__init__(parent)
        self.setWindowTitle("เช็คลิสต์ก่อนบินจริง")
        self.setMinimumSize(620, 660)
        self.setStyleSheet(_dialog_qss())

        self._sections = sections if sections is not None else pf.load_checklist()
        self._store_path = store_path
        # Checklist เป็นสถานะเฉพาะการเปิดโปรแกรมครั้งนี้เท่านั้น
        # ไม่โหลดค่าที่เคยติ๊กจากดิสก์ เพราะสภาพแบต/พื้นที่/airframe เปลี่ยนทุกเที่ยวบิน
        self._checked = dict(checked if checked is not None else self._session_checked)
        self._log = log_fn or (lambda *a, **k: None)
        self._boxes = {}                # item_key -> QCheckBox
        self.completed = False
        self.done_n = 0
        self.total_n = 0
        self._build()
        self._refresh_progress()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title = QLabel("เช็คลิสต์ก่อนบินจริง")
        title.setStyleSheet(f"color:{T('text')}; font-size:17px; font-weight:800;")
        v.addWidget(title)
        sub = QLabel("ตรวจด้วยตัวเองในสนาม · ต้องครบทุกข้อจึงผ่าน · "
                     "รายการจะเริ่มใหม่เมื่อเปิดโปรแกรมรอบใหม่")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(4)

        if not self._sections:
            miss = QLabel("อ่านไฟล์เช็คลิสต์ไม่ได้ — ตรวจว่ามี docs/REAL_FLIGHT_CHECKLIST.md")
            miss.setWordWrap(True)
            miss.setStyleSheet(f"color:{T('red')}; font-size:12px;")
            lay.addWidget(miss)

        for sec_title, items in self._sections:
            head = QLabel(sec_title)
            head.setWordWrap(True)
            head.setStyleSheet(
                f"color:{T('cyan')}; font-size:11px; font-weight:800; letter-spacing:1px;"
                f" padding:9px 2px 2px 2px;")
            lay.addWidget(head)
            for it in items:
                key = pf.item_key(it)
                cb = QCheckBox(it)
                cb.setToolTip(it)           # QCheckBox ตัดบรรทัดเองไม่ได้ — hover อ่านเต็ม
                cb.setChecked(bool(self._checked.get(key)))
                # ใช้ native checkbox indicator ของ Windows/Qt เพื่อให้เครื่องหมายติ๊กแสดงชัดจริง
                cb.setStyleSheet(
                    f"QCheckBox {{ color:{T('text')}; font-size:12px; padding:5px 2px; }}"
                    f"QCheckBox:checked {{ color:{T('green')}; font-weight:700; }}")
                cb.toggled.connect(lambda on, k=key: self._on_toggle(k, on))
                self._boxes[key] = cb
                lay.addWidget(cb)

        lay.addStretch(1)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)

        self.lb_progress = QLabel("")
        self.lb_progress.setWordWrap(True)
        v.addWidget(self.lb_progress)

        row = QHBoxLayout()
        row.setSpacing(8)
        btn_clear = QPushButton("CLEAR ALL")
        btn_clear.setMinimumHeight(36)
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet(ghost_btn(radius=9, font=11))
        btn_clear.clicked.connect(self._clear_all)
        row.addWidget(btn_clear, 1)
        btn_ok = QPushButton("SAVE & CLOSE")
        btn_ok.setMinimumHeight(36)
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.setStyleSheet(tinted_btn(T("green"), radius=9, font=12))
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_ok, 2)
        v.addLayout(row)

    def _on_toggle(self, key, on):
        if on:
            self._checked[key] = time.time()
        else:
            self._checked.pop(key, None)
        type(self)._session_checked = dict(self._checked)
        self._refresh_progress()

    def _clear_all(self):
        if not _confirm(self, "ล้างเช็คลิสต์",
                        "ล้างเครื่องหมายทั้งหมด แล้วเริ่มติ๊กใหม่?",
                        ok_text="CLEAR ALL", danger=True):
            return
        for cb in self._boxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._checked.clear()
        type(self)._session_checked.clear()
        self._refresh_progress()

    def _refresh_progress(self):
        self.done_n, self.total_n = pf.checklist_progress(self._sections, self._checked)
        self.completed = self.total_n > 0 and self.done_n >= self.total_n
        color = T("green") if self.completed else T("amber")
        left = self.total_n - self.done_n
        msg = ("ครบทุกข้อแล้ว (%d/%d)" % (self.done_n, self.total_n) if self.completed
               else "ติ๊กแล้ว %d/%d — เหลืออีก %d ข้อ" % (self.done_n, self.total_n, left))
        self.lb_progress.setText(msg)
        self.lb_progress.setStyleSheet(
            f"color:{color}; font-size:12px; font-weight:700;"
            f" background:{rgba(color, 0.12)}; border-radius:8px; padding:8px 11px;")

    def accept(self):
        self._log("PREFLIGHT CHECKLIST: %d/%d ข้อ%s"
                  % (self.done_n, self.total_n, " (ครบ)" if self.completed else ""))
        super().accept()


# ══════════════════════════════════════════════════════════════
#  กล่องถามตอนกด TAKEOFF ทั้งที่ยังไม่ได้เทส
# ══════════════════════════════════════════════════════════════
def _style_box(box, accent):
    box.setStyleSheet(
        f"QMessageBox {{ background:{T('panel')}; }}"
        f"QMessageBox QLabel {{ color:{T('text')}; font-family:{FONT_FAMILY};"
        f" font-size:13px; }}"
        f"QPushButton {{ background:{rgba('#ffffff', 0.06)};"
        f" border:1px solid {rgba('#ffffff', 0.14)}; border-radius:7px;"
        f" color:{T('text')}; padding:7px 16px; font-weight:600; min-width:96px; }}"
        f"QPushButton:hover {{ background:{rgba('#ffffff', 0.12)}; }}"
        f"QPushButton:default {{ background:{rgba(accent, 0.22)};"
        f" border:1px solid {rgba(accent, 0.5)}; }}")


def _confirm(parent, title, text, *, ok_text="ตกลง", danger=False):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    ok = box.addButton(ok_text, QMessageBox.AcceptRole)
    cancel = box.addButton("CANCEL", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    _style_box(box, T("red") if danger else T("accent"))
    box.exec_()
    return box.clickedButton() is ok


def ask_before_takeoff(parent, missing, action="TAKEOFF"):
    """ถามก่อนสั่งขึ้นบินทั้งที่ยังไม่ได้เทส

    คืน "test" (เทสให้เลย) · "skip" (ไม่เทส สั่งต่อ) · "cancel" (ไม่สั่งอะไร)
    """
    box = QMessageBox(parent)
    box.setWindowTitle("ยังไม่ได้ทดสอบก่อนบิน")
    box.setIcon(QMessageBox.Warning)
    box.setText("กำลังจะสั่ง %s แต่ยังไม่ได้ทดสอบก่อนบิน" % action)
    box.setInformativeText(
        "\n".join("• " + m for m in (missing or ["ยังไม่ได้รันชุดทดสอบก่อนบิน"]))
        + "\n\nจะทดสอบก่อนไหม?")
    b_test = box.addButton("RUN TEST", QMessageBox.AcceptRole)
    b_skip = box.addButton("SKIP · " + action, QMessageBox.DestructiveRole)
    b_cancel = box.addButton("CANCEL", QMessageBox.RejectRole)
    box.setDefaultButton(b_test)
    box.setEscapeButton(b_cancel)
    _style_box(box, T("green"))
    box.exec_()
    clicked = box.clickedButton()
    if clicked is b_test:
        return "test"
    if clicked is b_skip:
        return "skip"
    return "cancel"
