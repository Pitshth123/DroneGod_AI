"""
theme.py — SwarmGod cockpit theme (iOS-style dark, modern & clean)
โทนดาร์คหรูสไตล์ iOS: พื้นดำสนิท (OLED), การ์ดยกระดับสีเทา, มุมโค้ง,
สีเน้นแบบ iOS system colors, ฟอนต์ sans-serif คมสะอาด
เป็น single source of truth ของสี/สไตล์ทั้ง cockpit + launcher
"""
import re as _re

# ── palette (graphite dark + emerald/blue accent) ───────────
PALETTE = {
    "bg":        "#07090b",   # พื้นหลังนอกสุด (near-black เย็น)
    "panel":     "#0f1317",   # การ์ด/แผงหลัก
    "panel2":    "#161b21",   # sub-panel / เซลล์ยกระดับ
    "panel3":    "#1e242b",   # ยกระดับอีกชั้น (hover/active)
    "line":      "#232a31",   # เส้นคั่น
    "grid":      "#0f1317",   # เส้น grid บนแผนที่
    "accent":    "#3b82f6",   # blue — selection/แบรนด์/UI toggle
    "green":     "#20c77a",   # emerald — go / primary / ready
    "green_dim": "#158a54",   # เขียวจาง
    "amber":     "#f59e0b",   # warn / armed
    "red":       "#ef4444",   # danger / low / stop
    "cyan":      "#38bdf8",   # flying / secondary
    "orange":    "#f97316",   # RTL
    "purple":    "#a855f7",
    "text":      "#f3f6fa",   # ตัวอักษรหลัก — คมตัดพื้นมืด
    "dim":       "#aab4c0",   # label รอง
    "faint":     "#7a8694",   # label จางสุด
    "yellow":    "#fbbf24",
}

# ── ฟอนต์ UI ────────────────────────────────────────────────
# Sarabun (ไทย+ละติน) + IBM Plex Mono สำหรับตัวเลข — ฝังมากับโปรแกรมใน
# assets/fonts/ จึงได้หน้าตาเหมือนกันทุกเครื่อง ไม่ขึ้นกับว่าเครื่องนั้นลงฟอนต์อะไรไว้
# (Segoe UI ไม่มีสระ/วรรณยุกต์ไทย ต้องตกไปใช้ฟอนต์สำรองซึ่งความสูงบรรทัดไม่ตรงกัน
#  พอปนไทย-อังกฤษบรรทัดเดียวกันจะเหลื่อมกันเห็นชัด)
# โหลดด้วย load_bundled_fonts() ตอนเริ่มโปรแกรม — ถ้าโหลดไม่ได้จะตกไปใช้ตัวสำรองเอง
FONT_FAMILY = ('"Sarabun","Leelawadee UI","Noto Sans Thai","Segoe UI",'
               '"Helvetica Neue",Arial,sans-serif')
FONT_MONO = ('"IBM Plex Mono","Cascadia Mono","Consolas","Courier New",monospace')

_BUNDLED_FONTS = (
    "Sarabun-Regular.ttf", "Sarabun-Medium.ttf",
    "Sarabun-SemiBold.ttf", "Sarabun-Bold.ttf",
    "IBMPlexMono-Regular.ttf", "IBMPlexMono-SemiBold.ttf",
)


# ชื่อ family ที่ใช้กับ QFont() จริง — ตั้งโดย load_bundled_fonts() ถ้าโหลดสำเร็จ
FONT_UI_NAME = "Segoe UI"


def load_bundled_fonts(assets_dir: str) -> list:
    """ลงทะเบียนฟอนต์ที่ฝังมากับโปรแกรมเข้า Qt — คืนรายชื่อ family ที่โหลดได้

    ต้องเรียกหลังสร้าง QApplication แต่ก่อนสร้าง widget ตัวแรก
    ถ้าไฟล์หาย/โหลดไม่ได้ ก็แค่ตกไปใช้ฟอนต์สำรองใน FONT_FAMILY ไม่พังทั้งแอป
    """
    import os
    try:
        from PyQt5.QtGui import QFontDatabase
    except Exception:
        return []
    got = []
    for name in _BUNDLED_FONTS:
        path = os.path.join(assets_dir, "fonts", name)
        if not os.path.isfile(path):
            continue
        fid = QFontDatabase.addApplicationFont(path)
        if fid != -1:
            got.extend(QFontDatabase.applicationFontFamilies(fid))
    got = sorted(set(got))
    global FONT_UI_NAME
    for fam in got:
        if "Sarabun" in fam:
            FONT_UI_NAME = fam
            break
    return got

# ── status → สี badge (map ตรง LinkStatus ใน proto) ─────────
STATUS_COLOR = {
    "OFFLINE":      "faint",
    "CONNECTING":   "amber",
    "RECONNECTING": "amber",
    "READY":        "green",
    "ARMED":        "amber",
    "TAKEOFF":      "green",
    "FLYING":       "cyan",
    "HOLD":         "cyan",
    "MOVING":       "green",
    "LANDING":      "amber",
    "RTL":          "orange",
    "DISARMED":     "faint",
}

# ── สีต่อโดรน (Stage-style — สีชัด แยกง่าย มีพื้นจางตอนเลือก) ──
# mutable: ผู้ใช้เปลี่ยนสีจากการ์ดได้ → อัปเดต dict นี้โดยตรง
DRONE_COLORS = {
    1: "#e11d48",  # rose / Lead
    2: "#2563eb",  # blue / Prospects
    3: "#0d9488",  # teal / POC
    4: "#9f1239",  # burgundy / Closed
    5: "#db2777",  # pink
    6: "#65a30d",  # olive / Approved
}

# ชุดสีให้เลือกบนการ์ด (Stage palette)
COLOR_CHOICES = [
    "#e11d48",  # rose
    "#2563eb",  # blue
    "#0d9488",  # teal
    "#9f1239",  # burgundy
    "#db2777",  # pink
    "#65a30d",  # olive
    "#f59e0b",  # amber
    "#a855f7",  # purple
    "#38bdf8",  # cyan
    "#ef4444",  # red
]


def drone_color(drone_id: int) -> str:
    """สีปัจจุบันของโดรน (fallback หมุนตาม id ถ้ายังไม่ตั้ง)"""
    if drone_id in DRONE_COLORS:
        return DRONE_COLORS[drone_id]
    return COLOR_CHOICES[(max(1, int(drone_id)) - 1) % len(COLOR_CHOICES)]


def set_drone_color(drone_id: int, color: str) -> str:
    """ตั้งสีโดรน แล้วคืนค่าที่ใช้จริง"""
    c = (color or "").strip() or COLOR_CHOICES[0]
    if not c.startswith("#"):
        c = "#" + c
    DRONE_COLORS[int(drone_id)] = c
    return c


def stage_pill_qss(bg: str, checked: bool = False) -> str:
    """แคปซูลตัวกรอง Mission Log ที่เล็ก อ่านง่าย และไม่ล้าตา"""
    ring = f" border:1px solid #ffffff;" if checked else " border:1px solid transparent;"
    return (
        f"QPushButton {{ background:{bg}; color:#ffffff;{ring}"
        f" border-radius:9px; padding:2px 7px; font-size:{fs(8)}px; font-weight:700;"
        f" letter-spacing:0.3px; }}"
        f"QPushButton:hover {{ background:{rgba(bg, 0.88)}; }}"
        f"QPushButton:checked {{ background:{bg}; color:#ffffff;"
        f" border:1px solid #ffffff; }}"
    )


# ── ขนาดฟอนต์ทั้งระบบ (Global Font Scaling) ──────────────────
# ค่าฐาน 1.3 = ใหญ่กว่าเดิม 30% (default ของแอป — ต้องตรงกับ GroundStation._font_scale)
# ทุก helper ด้านล่างคูณด้วยค่านี้ → stylesheet ที่สร้างใหม่จะสเกลตามเสมอ
# ขนาดฟอนต์ฐานของ QApplication (pt) — ใหญ่กว่าค่าเดิม (10) ให้อ่านง่ายตั้งแต่ 100%
BASE_PT = 11

# ตัวคูณ "ฐาน" ที่ทำให้ตัวหนังสือใหญ่พออ่านสบายตั้งแต่ 100% โดยไม่ต้องไปกดขยาย
#
# แยกจาก _UI_SCALE ตั้งใจ: ตัวนี้เป็นขนาดที่ออกแบบไว้ ส่วน _UI_SCALE คือค่าที่
# ผู้ใช้ปรับเอง ถ้ายัด 1.3 เป็นค่าเริ่มต้นของ _UI_SCALE เหมือนเดิม พอผู้ใช้กด
# "รีเซ็ตเป็น 100%" ตัวหนังสือจะหดลงเล็กกว่าที่ออกแบบไว้ทันที
BASE_BOOST = 1.28

_UI_SCALE = [1.0]


def ui_scale() -> float:
    return _UI_SCALE[0]


def set_ui_scale(scale: float) -> float:
    """ตั้งตัวคูณขนาดฟอนต์ทั้งระบบ (ใช้กับ stylesheet ที่ generate หลังจากนี้)"""
    _UI_SCALE[0] = max(0.8, min(2.5, float(scale or 1.0)))
    return _UI_SCALE[0]


def fs(px: float) -> int:
    """แปลงขนาดฟอนต์ฐาน (px) → ขนาดจริง (ขนาดที่ออกแบบไว้ × ค่าที่ผู้ใช้ปรับ)"""
    return max(9, int(round(float(px) * BASE_BOOST * _UI_SCALE[0])))


_FONT_SIZE_RE = _re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")


def scale_stylesheet(qss: str, scale: float) -> str:
    """คูณทุกค่า font-size:Npx ใน stylesheet ด้วย scale (ใช้กับ QSS ที่มีอยู่แล้ว)"""
    if not qss:
        return qss

    def _rep(m):
        return f"font-size:{max(8, int(round(float(m.group(1)) * scale)))}px"

    return _FONT_SIZE_RE.sub(_rep, qss)


def apply_font_scale(root, scale: float) -> int:
    """ไล่สเกลฟอนต์ให้ "ทุก widget" ในหน้าต่าง — ครอบคลุมการ์ด/เมนู/แผงควบคุม/log

    เก็บ stylesheet ต้นฉบับไว้ใน property `_qss_base` ครั้งแรก แล้วสเกลจากต้นฉบับเสมอ
    (idempotent — กดเพิ่ม/ลดหลายรอบขนาดไม่เพี้ยนสะสม)
    คืนจำนวน widget ที่ถูกปรับ
    """
    from PyQt5.QtWidgets import QWidget

    widgets = [root] + root.findChildren(QWidget)
    n = 0
    for w in widgets:
        base = w.property("_qss_base")
        if base is None:
            base = w.styleSheet() or ""
            w.setProperty("_qss_base", base)
        if not base or "font-size" not in base:
            continue
        w.setStyleSheet(scale_stylesheet(base, scale))
        n += 1
    return n


def T(key: str) -> str:
    """คืน hex ของสีจาก palette"""
    return PALETTE.get(key, "#ffffff")


def _rgb(hexc: str):
    """hex '#rrggbb' → (r,g,b)"""
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgba(hexc: str, a: float) -> str:
    """'#rrggbb', alpha → 'rgba(r,g,b,a)' สำหรับใช้ใน QSS"""
    r, g, b = _rgb(hexc)
    return f"rgba({r},{g},{b},{a})"


def hairline() -> str:
    return rgba("#ffffff", 0.08)


# ── ปุ่มสไตล์เครื่องมือ (โปร / เงียบ) ─────────────────────────
def tinted_btn(hexc: str, radius: int = 7, font: int = 12, weight: int = 600) -> str:
    """ปุ่มรอง: พื้นจางมาก + ตัวอักษรสีสถานะ"""
    return (
        f"QPushButton {{ background:{rgba(hexc, 0.10)}; border:1px solid {rgba(hexc, 0.22)};"
        f" border-radius:{radius}px; color:{hexc}; font-weight:{weight}; font-size:{fs(font)}px;"
        f" padding:7px 10px; letter-spacing:0.3px; }}"
        f"QPushButton:hover {{ background:{rgba(hexc, 0.18)}; border:1px solid {rgba(hexc, 0.4)}; }}"
        f"QPushButton:pressed {{ background:{rgba(hexc, 0.28)}; }}"
        f"QPushButton:disabled {{ background:{rgba('#ffffff', 0.03)}; color:{T('faint')};"
        f" border:1px solid {rgba('#ffffff', 0.05)}; }}"
    )


def filled_btn(hexc: str, radius: int = 7, font: int = 12, weight: int = 700) -> str:
    """ปุ่มหลัก: พื้นทึบ ใช้เฉพาะคำสั่งสำคัญ"""
    return (
        f"QPushButton {{ background:{hexc}; border:none; border-radius:{radius}px;"
        f" color:#0a0c0e; font-weight:{weight}; font-size:{fs(font)}px; padding:8px 12px;"
        f" letter-spacing:0.4px; }}"
        f"QPushButton:hover {{ background:{rgba(hexc, 0.88)}; }}"
        f"QPushButton:pressed {{ background:{rgba(hexc, 0.72)}; }}"
        f"QPushButton:disabled {{ background:{rgba('#ffffff', 0.06)}; color:{T('faint')}; }}"
    )


def ghost_btn(radius: int = 7, font: int = 12, weight: int = 600) -> str:
    """ปุ่มกลาง ๆ — ขอบ hairline โปร่ง"""
    return (
        f"QPushButton {{ background:transparent; border:1px solid {hairline()};"
        f" border-radius:{radius}px; color:{T('text')}; font-weight:{weight};"
        f" font-size:{fs(font)}px; padding:7px 10px; letter-spacing:0.3px; }}"
        f"QPushButton:hover {{ background:{rgba('#ffffff', 0.05)}; border:1px solid {rgba('#ffffff', 0.16)}; }}"
        f"QPushButton:pressed {{ background:{rgba('#ffffff', 0.1)}; }}"
        f"QPushButton:disabled {{ color:{T('faint')}; border:1px solid {rgba('#ffffff', 0.04)}; }}"
    )


def pad_btn(radius: int = 8, font: int = 14) -> str:
    """ปุ่ม RC-pad — เงียบ กดแล้วเน้นเล็กน้อย"""
    g = T("green")
    return (
        f"QPushButton {{ background:{rgba('#ffffff', 0.04)}; border:1px solid {hairline()};"
        f" border-radius:{radius}px; color:{T('dim')}; font-size:{fs(font)}px; font-weight:600; }}"
        f"QPushButton:hover {{ background:{rgba('#ffffff', 0.08)}; color:{T('text')}; }}"
        f"QPushButton:pressed {{ background:{rgba(g, 0.18)}; color:{g}; border:1px solid {rgba(g, 0.35)}; }}"
    )


def card_qss(selector: str, radius: int = 10, bg_key: str = "panel") -> str:
    """QSS แผงด้านข้างแบบโปร — มุมไม่กลมเกิน + ขอบ hairline"""
    return (f"{selector} {{ background:{T(bg_key)}; border:1px solid {hairline()};"
            f" border-radius:{radius}px; }}")


def section_label_qss() -> str:
    return (f"color:{T('faint')}; font-size:{fs(10)}px; font-weight:600;"
            f" letter-spacing:1.4px;")


def build_stylesheet(scale: float = None) -> str:
    """QSS หลักของทั้งแอป — เรียกครั้งเดียวแล้ว setStyleSheet
    scale = ตัวคูณขนาดฟอนต์; ไม่ส่ง = ใช้ค่า global ปัจจุบัน (set_ui_scale)"""
    p = PALETTE
    hair = rgba("#ffffff", 0.08)
    _s = float(scale) if scale else _UI_SCALE[0]

    def fs(px: int) -> int:
        return max(8, int(round(px * _s)))

    return f"""
    * {{
        font-family: {FONT_FAMILY};
        font-size: {fs(13)}px;
        color: {p['text']};
        outline: none;
    }}
    QMainWindow, QWidget {{
        background: {p['bg']};
        font-family: {FONT_FAMILY};
    }}
    QLabel, QPushButton, QLineEdit, QComboBox, QTableWidget {{
        font-family: {FONT_FAMILY};
        font-size: {fs(13)}px;
    }}

    QToolTip {{
        background: {p['panel2']}; color: {p['text']};
        border: 1px solid {hair}; border-radius: 6px; padding: 5px 9px; font-size: {fs(12)}px;
    }}

    QMenu {{ font-size: {fs(13)}px; }}

    QPushButton {{
        background: transparent;
        border: 1px solid {hair};
        border-radius: 7px;
        color: {p['text']}; padding: 7px 12px; font-weight: 600;
    }}
    QPushButton:hover   {{ background: {rgba('#ffffff', 0.05)}; }}
    QPushButton:pressed {{ background: {rgba('#ffffff', 0.1)}; }}
    QPushButton:disabled{{ color: {p['faint']}; border-color: {rgba('#ffffff', 0.04)}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {p['panel2']}; border: 1px solid {hair};
        border-radius: 6px; color: {p['text']}; padding: 5px 9px;
        selection-background-color: {p['accent']}; selection-color: #ffffff;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {rgba(p['accent'], 0.65)};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{ width: 0; height: 0; }}
    QComboBox QAbstractItemView {{
        background: {p['panel2']}; border: 1px solid {hair}; border-radius: 6px;
        selection-background-color: {rgba(p['accent'], 0.85)}; color: {p['text']}; padding: 4px;
    }}
    QDoubleSpinBox::up-button, QSpinBox::up-button,
    QDoubleSpinBox::down-button, QSpinBox::down-button {{
        background: transparent; border: none; width: 16px;
    }}

    QProgressBar {{
        background: {rgba('#ffffff', 0.08)}; border: none; border-radius: 2px; height: 4px;
    }}
    QProgressBar::chunk {{ background: {p['green']}; border-radius: 2px; }}

    QLabel {{ background: transparent; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 1px 2px 0; }}
    QScrollBar::handle:vertical {{
        background: {rgba('#ffffff', 0.14)}; border-radius: 4px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {rgba('#ffffff', 0.26)}; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 2px 1px 2px; }}
    QScrollBar::handle:horizontal {{
        background: {rgba('#ffffff', 0.14)}; border-radius: 4px; min-width: 28px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QPlainTextEdit {{ background: transparent; border: none; color: {p['text']}; }}
    """


def badge_style(status: str) -> str:
    """สถานะแบบโปร — ขอบบาง ตัวเล็ก ไม่ใช่ลูกกวาด

    หมายเหตุ: ป้ายนี้ใช้ในแถว FLEET ที่แคบมาก จึงล็อกขนาดฟอนต์ไม่ให้สเกลตามทั้งระบบ
    (เดิมใช้ fs(9) พอผู้ใช้ตั้งฟอนต์ 130% ป้าย "OFFLINE"/"RECONNECTING" จะบานจนล้นแถว
    อ่านไม่ออก) — letter-spacing/padding ก็ลดลงด้วยเพื่อให้คำยาว ๆ พอดีแถว
    """
    color = T(STATUS_COLOR.get(status, "faint"))
    return (f"color:{color}; background:transparent; border:1px solid {rgba(color, 0.35)};"
            f" border-radius:3px; font-size:9px; font-weight:600;"
            f" letter-spacing:0.3px; padding:1px 4px;")
