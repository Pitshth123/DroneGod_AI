"""
probe.py — พิสูจน์ว่า QWebEngine ของเครื่องนี้รัน WebGL + Three.js + terrain.bin ได้จริงไหม

    cd prototypes/map3d
    python probe.py              # เปิดหน้าต่างให้ดู + สรุปผลลง console
    python probe.py --headless   # ไม่เปิดหน้าต่าง (ใช้ใน CI) — ดูผลจาก exit code

ทำไมต้องมีตัวนี้: DroneGod มี QWebEngineView อยู่แล้ว (Leaflet 2D รันในนั้น) ถ้าจะเอาแผนที่ 3D
แบบ SIAHRA มาใช้ ต้องรู้ก่อนว่า WebGL ในนั้นใช้ GPU จริงหรือตกไปเป็น SwiftShader (ซอฟต์แวร์ล้วน)
เพราะถ้าเป็นอย่างหลัง เฟรมเรตจะไม่พอสำหรับ cockpit ที่ต้องดู telemetry สด

**ไม่แตะโค้ดหลัก** — ไฟล์ในโฟลเดอร์นี้แยกจาก swarmgod_gui ทั้งหมด
"""
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8791

# ── ใช้ TileServer ตัวจริงของ cockpit (อ่านอย่างเดียว ไม่แก้ไฟล์ไหน) ──
# จุดประสงค์: พิสูจน์ว่า tile proxy + offline cache ที่มีอยู่แล้วเสิร์ฟภาพให้แผนที่ 3D
# ได้เลย เพราะ SIAHRA กับ DroneGod ใช้ Esri World Imagery ตัวเดียวกันเป๊ะ
_FRONTEND = os.path.abspath(os.path.join(HERE, "..", "..", "frontend"))
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)


def start_tiles():
    """คืน (base_url, err) — ถ้าเริ่มไม่ได้ให้ prototype เดินต่อแบบไม่มีภาพดาวเทียม"""
    try:
        from swarmgod_gui.core.tile_cache import TileServer
    except Exception as e:
        return None, f"import ไม่ได้: {e}"
    try:
        assets = os.path.join(_FRONTEND, "swarmgod_gui", "assets")
        # cache เดียวกับ cockpit — tile ที่เคยโหลดไว้แล้วเอามาใช้ซ้ำได้ทันที
        cache = os.path.join(os.path.expanduser("~"), ".swarmgod", "tiles")
        srv = TileServer(assets, cache)
        return srv.start(), None
    except Exception as e:
        return None, f"start ไม่ได้: {e}"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".bin": "application/octet-stream",
    ".css": "text/css; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    """เสิร์ฟไฟล์ในโฟลเดอร์ prototype ผ่าน http://

    ต้องเป็น http ไม่ใช่ file:// เพราะ QWebEngine บล็อก fetch() ข้าม file://
    (และ ES module + importmap ก็ต้องการ origin จริง)
    """

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        # /aoi/... = ข้อมูลภูมิประเทศ อยู่ใต้ data/ (ให้ URL ตรงกับที่ manifest ประกาศไว้
        # เป๊ะ ๆ จะได้ใช้ manifest ของ SIAHRA ได้โดยไม่ต้องแก้ path ข้างใน)
        if path.startswith("/aoi/"):
            path = "/data" + path
        # กัน path traversal — เสิร์ฟเฉพาะไฟล์ใต้โฟลเดอร์นี้เท่านั้น
        full = os.path.normpath(os.path.join(HERE, path.lstrip("/")))
        if not full.startswith(HERE) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true",
                    help="ไม่เปิดหน้าต่าง (ผลออกทาง console + exit code)")
    ap.add_argument("--timeout", type=int, default=45, help="วินาที")
    ap.add_argument("--page", default="index.html",
                    help="หน้าที่จะเปิด (ใช้ทดสอบหน้าอื่นในโฟลเดอร์นี้ได้)")
    ap.add_argument("--shot", metavar="PNG",
                    help="บันทึกภาพหน้าจอหลัง probe จบ (ต้องไม่ใช่ --headless)")
    ap.add_argument("--zoom", type=int, default=10,
                    help="ระดับ zoom ของภาพดาวเทียมที่จะแปะ (10 = เร็ว, 11-12 = ละเอียดกว่า)")
    ap.add_argument("--no-imagery", action="store_true",
                    help="ไม่แปะภาพดาวเทียม (ใช้สีไล่ตามความสูงแทน)")
    args = ap.parse_args()

    if args.headless:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # ต้องตั้ง flag ก่อน import QtWebEngine — บาง GPU/driver บน Windows ต้องบังคับ
    # ให้ Chromium ใช้ GPU (ค่าเริ่มต้นบางเครื่องตกไป SwiftShader เงียบ ๆ)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                          "--ignore-gpu-blocklist --enable-gpu-rasterization")

    from PyQt5.QtCore import QTimer, QUrl
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

    results = {}
    verdict = {"value": None}

    class Page(QWebEnginePage):
        """ดัก console.log ของหน้าเว็บ — probe ฝั่ง JS ส่งผลออกมาทางนี้"""

        def javaScriptConsoleMessage(self, level, msg, line, src):
            if msg.startswith("#PROBE# "):
                d = json.loads(msg[len("#PROBE# "):])
                mark = {"ok": "[ok]  ", "bad": "[FAIL]", "warn": "[warn]"}.get(d["c"], "      ")
                print(f"  {mark} {d['k']:<16} {d['v']}")
                results[d["k"]] = d["v"]
            elif msg.startswith("#PROBE-DONE# "):
                d = json.loads(msg[len("#PROBE-DONE# "):])
                verdict["value"] = d["verdict"]
                if args.shot and not args.headless:
                    # หน่วงหนึ่งเฟรมให้ WebGL วาดเสร็จก่อนค่อย grab
                    QTimer.singleShot(300, save_shot)
                else:
                    app.quit()
            else:
                print(f"  [js]   {msg}")

    def save_shot():
        path = os.path.abspath(args.shot)
        if view.grab().save(path):
            print(f"  [shot] บันทึกภาพ -> {path}")
        else:
            print(f"  [shot] บันทึกไม่สำเร็จ: {path}")
        app.quit()

    app = QApplication(sys.argv)
    srv = serve()
    print(f"\n  SwarmGod — 3D terrain probe (http://127.0.0.1:{PORT})")
    print("  " + "-" * 62)

    # tile proxy ของ cockpit — ส่ง base_url ให้หน้าเว็บผ่าน query string
    tiles_url, tiles_err = (None, "ปิดด้วย --no-imagery") if args.no_imagery else start_tiles()
    if tiles_url:
        print(f"  [ok]   tile proxy       {tiles_url} (cache ~/.swarmgod/tiles)")
    else:
        print(f"  [warn] tile proxy       ใช้ไม่ได้ — {tiles_err}")

    view = QWebEngineView()
    view.setPage(Page(view))
    view.resize(1280, 800)
    view.setWindowTitle("SwarmGod — 3D terrain probe")
    q = f"?zoom={args.zoom}" + (f"&tiles={tiles_url}" if tiles_url else "")
    view.load(QUrl(f"http://127.0.0.1:{PORT}/{args.page.lstrip('/')}{q}"))
    if not args.headless:
        view.show()

    # กันค้าง: ถ้า probe ไม่รายงานภายในเวลาที่กำหนด ถือว่าล้มเหลว
    QTimer.singleShot(args.timeout * 1000, app.quit)
    app.exec_()
    srv.shutdown()

    print("  " + "-" * 62)
    v = verdict["value"]
    if v == "PASS":
        print("  ✓ ผ่าน — QWebEngine เรนเดอร์ terrain 3D ได้ที่เฟรมเรตใช้งานได้")
        code = 0
    elif v == "SLOW":
        print("  ! เรนเดอร์ได้ แต่เฟรมเรตต่ำ — ดู 'gpu' ข้างบนว่าตกไป SwiftShader ไหม")
        code = 2
    elif v == "FAIL":
        print("  ✗ ไม่ผ่าน — ดูบรรทัด [FAIL] ข้างบนว่าพังขั้นไหน")
        code = 1
    else:
        print(f"  ✗ ไม่ได้ผลใน {args.timeout}s (หน้าอาจค้างหรือ JS พังก่อนรายงาน)")
        code = 1
    print()
    return code


if __name__ == "__main__":
    sys.exit(main())
