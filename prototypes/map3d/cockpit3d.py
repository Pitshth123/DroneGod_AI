"""
cockpit3d.py — ต่อ telemetry สดจาก Go core เข้าแผนที่ 3D

พิสูจน์ข้อสุดท้ายที่เหลือ: หมุดโดรนบนภูมิประเทศ 3D ขยับตามโดรนจริงได้ไหม
โดยใช้ **สะพานแบบเดียวกับที่ cockpit ใช้กับ Leaflet อยู่แล้ว** (page.runJavaScript)

    # ต้องมี core รันอยู่ก่อน:  cd backend && ./bin/swarmgod-core.exe
    # และ SITL:                 wsl -d Ubuntu -e bash scripts/wsl_sitl.sh
    python cockpit3d.py --sitl 127.0.0.1:5760

    # ไม่มี core/SITL ก็ทดสอบสะพานได้ (โดรนจำลองบินวนเป็นวงกลม):
    python cockpit3d.py --demo

**ไม่แก้โค้ดหลัก** — import `swarmgod_gui.core.grpc_client` / `tile_cache` มาใช้อย่างเดียว
"""
import argparse
import json
import math
import os
import sys
import time

from probe import HERE, PORT, serve, start_tiles, _FRONTEND  # noqa: F401  (ใช้ซ้ำ)

# throttle การยิงเข้า JS — core ส่ง telemetry ~10 Hz ต่อลำ ถ้ายิงทุก frame
# ต่อทุกลำจะเป็น N×10 ครั้ง/วิ ทั้งที่ตาคนดูไม่ทัน (cockpit เดิมก็ throttle เหมือนกัน)
PUSH_HZ = 10.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="127.0.0.1:50051")
    ap.add_argument("--sitl", default="", metavar="HOST:PORT",
                    help="สั่ง core ให้ต่อโดรนที่ SITL นี้ก่อน (เช่น 127.0.0.1:5760)")
    ap.add_argument("--drones", type=int, default=1, help="จำนวนลำ (SITL เรียง port +10)")
    ap.add_argument("--demo", action="store_true",
                    help="ไม่ต่อ core — ใช้โดรนจำลองบินวนทดสอบสะพาน")
    ap.add_argument("--fly", action="store_true",
                    help="สั่ง takeoff + goto จริง (SITL เท่านั้น) เพื่อดูหมุดขยับ")
    ap.add_argument("--alt", type=float, default=60.0, help="ความสูง takeoff (m)")
    ap.add_argument("--zoom", type=int, default=11)
    ap.add_argument("--aoi", default="30")
    ap.add_argument("--seconds", type=int, default=0,
                    help="ปิดอัตโนมัติหลังกี่วินาที (0 = ไม่ปิด)")
    ap.add_argument("--shot", metavar="PNG")
    args = ap.parse_args()

    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                          "--ignore-gpu-blocklist --enable-gpu-rasterization")

    from PyQt5.QtCore import QTimer, QUrl
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

    app = QApplication(sys.argv)
    srv = serve()
    tiles_url, tiles_err = start_tiles()

    print(f"\n  SwarmGod 3D — live telemetry bridge")
    print("  " + "-" * 62)
    if tiles_url:
        print(f"  [ok]   tile proxy       {tiles_url}")
    else:
        print(f"  [warn] tile proxy       {tiles_err}")

    ready = {"v": False}
    latest = {}          # drone_id -> dict ที่จะส่งเข้า JS
    counters = {"rx": 0, "push": 0}

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, msg, line, src):
            if msg.startswith("#PROBE# "):
                d = json.loads(msg[len("#PROBE# "):])
                mark = {"ok": "[ok]  ", "bad": "[FAIL]", "warn": "[warn]"}.get(d["c"], "      ")
                print(f"  {mark} {d['k']:<16} {d['v']}")
            elif msg == "#GCS-READY#":
                ready["v"] = True
            elif msg.startswith("#PROBE-DONE# "):
                finish(json.loads(msg[len("#PROBE-DONE# "):])["verdict"])
            else:
                print(f"  [js]   {msg}")

    view = QWebEngineView()
    view.setPage(Page(view))
    view.resize(1400, 880)
    view.setWindowTitle("SwarmGod — 3D live telemetry")
    q = f"?aoi={args.aoi}&zoom={args.zoom}" + (f"&tiles={tiles_url}" if tiles_url else "")
    view.load(QUrl(f"http://127.0.0.1:{PORT}/live.html{q}"))
    view.show()

    verdict = {"v": None}

    def finish(v):
        verdict["v"] = v
        if not args.shot:
            app.quit()
            return
        # ดึงภาพจากใน WebGL (canvas.toDataURL) ไม่ใช่ QWidget.grab()
        # เพราะ grab() จับ layer ที่ GPU composite ไม่ได้ → ได้ภาพดำล้วน
        path = os.path.abspath(args.shot)

        def got(data_url):
            import base64
            try:
                b64 = (data_url or "").split(",", 1)[1]
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                print(f"  [shot] บันทึกภาพ -> {path}")
            except Exception as e:
                print(f"  [shot] ล้มเหลว: {e}")
            app.quit()

        view.page().runJavaScript("gcs.capture()", got)

    # ── แหล่ง telemetry ─────────────────────────────────────────
    tele = None
    if args.demo:
        # โดรนจำลอง 3 ลำ บินวนรอบจุด home ของ SITL — ทดสอบสะพานโดยไม่ต้องมี core
        HOME = (14.9581695, 102.0986187)
        t0 = time.monotonic()

        def demo_tick():
            el = time.monotonic() - t0
            for i in range(1, 4):
                a = el * 0.35 + i * 2.09
                r = 0.012 + i * 0.004
                latest[i] = {
                    "id": i,
                    "lat": HOME[0] + r * math.sin(a),
                    "lon": HOME[1] + r * math.cos(a),
                    "alt": 90 + i * 35 + 20 * math.sin(el * 0.7 + i),
                    "hdg": (math.degrees(-a) + 90) % 360,
                    "mode": "GUIDED", "armed": True,
                }
                counters["rx"] += 1
        demo_timer = QTimer()
        demo_timer.timeout.connect(demo_tick)
        demo_timer.start(100)
        print("  [ok]   แหล่งข้อมูล      demo (โดรนจำลอง 3 ลำ)")
    else:
        sys.path.insert(0, _FRONTEND)
        from swarmgod_gui.core.grpc_client import CoreClient, TelemetryThread
        from swarmgod_gui.core import rpc

        try:
            client = CoreClient(args.core)
        except Exception as e:
            print(f"  [FAIL] core             ต่อไม่ได้: {e}")
            return 1
        print(f"  [ok]   core             {args.core} "
              f"({'mTLS' if client.secure else 'plaintext'})")

        if args.sitl:
            host, _, ps = args.sitl.partition(":")
            base = int(ps or 5760)
            for i in range(1, args.drones + 1):
                port = base + (i - 1) * 10
                try:
                    r = client.connect_drone(i, f"UAV_{i}", host, port)
                    ok = getattr(r, "ok", False)
                    print(f"  [{'ok' if ok else 'warn'}]{'  ' if ok else ''} "
                          f"connect UAV_{i}   {host}:{port} — "
                          f"{getattr(r, 'message', '')}")
                except Exception as e:
                    print(f"  [warn] connect UAV_{i}   {e}")

        def on_tel(t):
            counters["rx"] += 1
            p = t.position
            latest[int(t.drone_id)] = {
                "id": int(t.drone_id),
                "lat": p.lat, "lon": p.lon, "alt": p.alt_rel,
                "hdg": t.heading,
                "mode": rpc.mode_name(t.mode).replace("FLIGHT_MODE_", ""),
                "armed": bool(t.armed),
            }

        tele = TelemetryThread(client.stub)
        tele.telemetry.connect(on_tel)
        tele.stream_error.connect(lambda m: print(f"  [FAIL] telemetry stream  {m}"))
        tele.start()
        print("  [ok]   แหล่งข้อมูล      telemetry stream จาก core")

        # ── สั่งบินจริง เพื่อพิสูจน์ว่าหมุด "ขยับ" ไม่ใช่แค่ "ขึ้น" ──
        # คำสั่งทั้งหมดวิ่งผ่าน core → ผ่าน safety envelope ตามปกติ
        # (core อาจปฏิเสธถ้า GPS/แบตไม่พร้อม — เป็นพฤติกรรมที่ถูกแล้ว)
        if args.fly:
            import threading

            def fly():
                ids = [1]
                for _ in range(60):            # รอ GPS fix ก่อนสั่ง
                    d = latest.get(1)
                    if d and (d["lat"] or d["lon"]):
                        break
                    time.sleep(1)
                home = latest.get(1)
                if not home:
                    print("  [warn] fly              ไม่มีพิกัด — ข้ามการสั่งบิน")
                    return
                try:
                    r = client.takeoff(ids, args.alt, confirmed=True)
                    print(f"  [{'ok' if r.ok else 'warn'}]{'  ' if r.ok else ''} "
                          f"takeoff {args.alt:.0f}m     {r.message}")
                    if not r.ok:
                        return
                    time.sleep(12)
                    # บินไปทางเหนือ ~2 km ให้เห็นหมุดเคลื่อนบนภูมิประเทศชัด ๆ
                    tlat = home["lat"] + 0.018
                    r = client.goto(1, tlat, home["lon"], args.alt)
                    print(f"  [{'ok' if r.ok else 'warn'}]{'  ' if r.ok else ''} "
                          f"goto +2km เหนือ   {r.message}")
                except Exception as e:
                    print(f"  [warn] fly              {e}")

            threading.Thread(target=fly, daemon=True).start()

    # ── สะพาน Python -> JS (เหมือน _js() ของ cockpit เดิม) ──────
    def push():
        if not ready["v"] or not latest:
            return
        payload = json.dumps(list(latest.values()))
        view.page().runJavaScript(f"gcs.setDrones({payload})")
        counters["push"] += 1

    pusher = QTimer()
    pusher.timeout.connect(push)
    pusher.start(int(1000 / PUSH_HZ))

    if args.seconds:
        QTimer.singleShot(args.seconds * 1000,
                          lambda: view.page().runJavaScript("report()"))

    app.exec_()
    if tele:
        tele.stop()
        tele.wait(2000)
    srv.shutdown()

    print("  " + "-" * 62)
    print(f"  telemetry ที่รับได้ {counters['rx']} · ยิงเข้า JS {counters['push']} ครั้ง")
    v = verdict["v"]
    if v == "PASS":
        print("  ✓ ผ่าน — หมุดโดรนบนแผนที่ 3D ขยับตาม telemetry สดได้จริง")
        return 0
    if v == "FAIL":
        print("  ✗ ไม่ผ่าน — ดูบรรทัด [FAIL] ข้างบน")
        return 1
    print("  (ปิดหน้าต่างเอง — ไม่ได้สรุปผล)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
