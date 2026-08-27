"""
icons.py — ไอคอนรูปทรงเรขาคณิตเรียบ ๆ วาดด้วย QPainter

ทำไมวาดเอง: ไม่ต้องพึ่งไฟล์ภาพ/ฟอนต์พิเศษ/ไลบรารีเสริม (offline-safe)
คมทุก DPI และเปลี่ยนสีตามธีมได้ทันที

ใช้คู่กับปุ่มที่ "ไม่มีข้อความ" — คำอธิบายโผล่เป็น tooltip ตอน hover เท่านั้น
เพื่อให้หน้าจอโล่งที่สุด
"""
from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# ชื่อรูปทรงที่รองรับ (ใช้เป็น key ตอนเรียก geo_icon)
SHAPES = (
    "square", "circle", "line", "polyline", "polygon", "triangle",
    "cross", "target", "text", "image", "scan", "link", "dots",
    "arrow", "minus", "plus", "check", "save", "folder", "download",
)


def _pen(p: QPainter, color: QColor, w: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    return pen


def _draw(p: QPainter, shape: str, c: QColor, s: int):
    """วาดรูปทรงลงบน painter — พิกัดอิงกรอบ s x s"""
    m = s * 0.24            # margin
    a, b = m, s - m         # ซ้าย/บน, ขวา/ล่าง
    mid = s / 2.0
    lw = max(1.5, s * 0.085)

    _pen(p, c, lw)
    p.setBrush(Qt.NoBrush)

    if shape == "square":
        p.drawRoundedRect(QRectF(a, a, b - a, b - a), s * 0.06, s * 0.06)

    elif shape == "circle":
        p.drawEllipse(QRectF(a, a, b - a, b - a))

    elif shape == "line":
        p.drawLine(QPointF(a, b), QPointF(b, a))

    elif shape == "minus":
        p.drawLine(QPointF(a, mid), QPointF(b, mid))

    elif shape == "plus":
        p.drawLine(QPointF(a, mid), QPointF(b, mid))
        p.drawLine(QPointF(mid, a), QPointF(mid, b))

    elif shape == "polyline":
        path = QPainterPath(QPointF(a, b))
        path.lineTo(QPointF(mid - s * 0.06, mid + s * 0.04))
        path.lineTo(QPointF(mid + s * 0.10, mid - s * 0.10))
        path.lineTo(QPointF(b, a))
        p.drawPath(path)

    elif shape in ("polygon", "triangle"):
        path = QPainterPath()
        if shape == "triangle":
            pts = [(mid, a), (b, b), (a, b)]
        else:                                   # ห้าเหลี่ยม
            pts = [(mid, a), (b, mid - s * 0.06),
                   (b - s * 0.10, b), (a + s * 0.10, b), (a, mid - s * 0.06)]
        path.moveTo(QPointF(*pts[0]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x, y))
        path.closeSubpath()
        p.drawPath(path)

    elif shape == "cross":
        p.drawLine(QPointF(a, a), QPointF(b, b))
        p.drawLine(QPointF(b, a), QPointF(a, b))

    elif shape == "target":
        p.drawEllipse(QRectF(a, a, b - a, b - a))
        r2 = (b - a) * 0.22
        p.setBrush(c)
        p.drawEllipse(QRectF(mid - r2, mid - r2, r2 * 2, r2 * 2))
        p.setBrush(Qt.NoBrush)
        _pen(p, c, lw * 0.85)
        p.drawLine(QPointF(mid, 0.06 * s), QPointF(mid, a))
        p.drawLine(QPointF(mid, b), QPointF(mid, 0.94 * s))
        p.drawLine(QPointF(0.06 * s, mid), QPointF(a, mid))
        p.drawLine(QPointF(b, mid), QPointF(0.94 * s, mid))

    elif shape == "text":
        p.drawLine(QPointF(a, a), QPointF(b, a))          # ขีดบนของตัว T
        p.drawLine(QPointF(mid, a), QPointF(mid, b))      # ขาตั้ง

    elif shape == "image":
        p.drawRoundedRect(QRectF(a, a, b - a, b - a), s * 0.06, s * 0.06)
        _pen(p, c, lw * 0.8)
        path = QPainterPath(QPointF(a + s * 0.04, b - s * 0.06))
        path.lineTo(QPointF(mid - s * 0.02, mid))
        path.lineTo(QPointF(b - s * 0.04, b - s * 0.06))
        p.drawPath(path)

    elif shape == "scan":                                  # วงซ้อน = สแกนหา
        p.drawEllipse(QRectF(a, a, b - a, b - a))
        in_m = (b - a) * 0.28
        p.drawEllipse(QRectF(a + in_m, a + in_m,
                             (b - a) - in_m * 2, (b - a) - in_m * 2))
        p.setBrush(c)
        r2 = s * 0.045
        p.drawEllipse(QRectF(mid - r2, mid - r2, r2 * 2, r2 * 2))
        p.setBrush(Qt.NoBrush)

    elif shape == "link":                                  # จุด–เส้น–จุด
        r2 = s * 0.10
        p.setBrush(c)
        p.drawEllipse(QRectF(a - r2 * 0.2, mid - r2, r2 * 2, r2 * 2))
        p.drawEllipse(QRectF(b - r2 * 1.8, mid - r2, r2 * 2, r2 * 2))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(a + r2 * 1.9, mid), QPointF(b - r2 * 1.9, mid))

    elif shape == "dots":
        r2 = s * 0.062
        p.setBrush(c)
        for x in (mid - s * 0.20, mid, mid + s * 0.20):
            p.drawEllipse(QRectF(x - r2, mid - r2, r2 * 2, r2 * 2))
        p.setBrush(Qt.NoBrush)

    elif shape == "arrow":
        p.drawLine(QPointF(a, mid), QPointF(b, mid))
        p.drawLine(QPointF(b, mid), QPointF(b - s * 0.16, mid - s * 0.14))
        p.drawLine(QPointF(b, mid), QPointF(b - s * 0.16, mid + s * 0.14))

    elif shape == "check":
        path = QPainterPath(QPointF(a, mid))
        path.lineTo(QPointF(mid - s * 0.06, b - s * 0.04))
        path.lineTo(QPointF(b, a))
        p.drawPath(path)

    elif shape == "save":                                  # ลูกศรลงกล่อง
        p.drawLine(QPointF(mid, a), QPointF(mid, mid + s * 0.10))
        p.drawLine(QPointF(mid, mid + s * 0.10),
                   QPointF(mid - s * 0.12, mid - s * 0.02))
        p.drawLine(QPointF(mid, mid + s * 0.10),
                   QPointF(mid + s * 0.12, mid - s * 0.02))
        p.drawLine(QPointF(a, b), QPointF(b, b))

    elif shape == "download":
        p.drawLine(QPointF(a, b), QPointF(b, b))
        p.drawLine(QPointF(mid, a), QPointF(mid, mid + s * 0.12))
        p.drawLine(QPointF(mid, mid + s * 0.12), QPointF(mid - s * 0.12, mid))
        p.drawLine(QPointF(mid, mid + s * 0.12), QPointF(mid + s * 0.12, mid))

    elif shape == "folder":
        path = QPainterPath(QPointF(a, b))
        path.lineTo(QPointF(a, a + s * 0.06))
        path.lineTo(QPointF(mid - s * 0.06, a + s * 0.06))
        path.lineTo(QPointF(mid + s * 0.02, a + s * 0.16))
        path.lineTo(QPointF(b, a + s * 0.16))
        path.lineTo(QPointF(b, b))
        path.closeSubpath()
        p.drawPath(path)

    else:                                                  # fallback = สี่เหลี่ยม
        p.drawRoundedRect(QRectF(a, a, b - a, b - a), s * 0.06, s * 0.06)


def geo_pixmap(shape: str, color: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    """คืน QPixmap ของรูปทรง (วาดที่ความละเอียด dpr เท่า เพื่อความคม)"""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    _draw(p, shape, QColor(color), size)
    p.end()
    return px


def geo_icon(shape: str, color: str, size: int = 18) -> QIcon:
    """คืน QIcon ของรูปทรงเรขาคณิต — ใช้กับ QPushButton.setIcon()"""
    return QIcon(geo_pixmap(shape, color, size))
