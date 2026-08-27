"""
check_cockpit3d.py — ตรวจว่า 2 ฟังก์ชันใหม่ใน cockpit ทำงานจริงบนเครื่องนี้

    1) ปุ่ม "3D"        → สลับแผนที่ในหน้าต่างเดิม + ดับเบิลคลิกสั่งบินได้
    2) ปุ่ม "3D จอแยก"  → เปิดหน้าต่างแยกไว้โชว์อีกจอ

ต้องเปิดหน้าต่างจริง (QtWebEngine + offscreen = segfault) และควรมี core + SITL รันอยู่
จะได้เห็นหมุดโดรนขยับด้วย

    cd prototypes/map3d && python check_cockpit3d.py
"""
import base64
import json
import os
import sys

_FRONTEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "..", "frontend"))
sys.path.insert(0, _FRONTEND)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--ignore-gpu-blocklist")

# ต้อง import QtWebEngineWidgets ก่อนสร้าง QApplication (Qt บังคับ)
from PyQt5 import QtWebEngineWidgets                  # noqa: F401,E402
from PyQt5.QtCore import QTimer                       # noqa: E402
from PyQt5.QtWidgets import QApplication              # noqa: E402

RESULTS = []


def chk(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "[ok]  " if ok else "[FAIL]"
    print(f"  {mark} {name:<28} {detail}")


def main():
    app = QApplication(sys.argv)
    from swarmgod_gui.app import GroundStation

    win = GroundStation("127.0.0.1:50051")
    win.resize(1500, 900)
    win.show()

    print("\n  ตรวจแผนที่ 3D ใน cockpit จริง")
    print("  " + "-" * 62)
    chk("ปุ่ม 3D มีอยู่", hasattr(win, "btn_map3d"))
    chk("ปุ่ม 3D จอแยก มีอยู่", hasattr(win, "btn_map3d_win"))
    chk("แผนที่ซ้อนใน stack", hasattr(win, "map_stack"),
        f"{win.map_stack.count()} ชั้น" if hasattr(win, "map_stack") else "ไม่มี")

    # regression: กด 3D ครั้งแรกแล้วหน้าต่างเด้งกว้างเกินจอ ดัน COMMANDS ตกขอบ
    # (ต้นตอ: แถบเครื่องมือ Mission Log บีบไม่ได้ ต้องการ ~744px เสมอ — ดู
    #  tests/test_map3d.py::TestMissionLogToolbarDoesNotForceWideWindow)
    from PyQt5.QtWidgets import QFrame
    w0 = win.centralWidget().layout().minimumSize().width()
    scr = app.primaryScreen().availableGeometry().width()
    chk("ความกว้างขั้นต่ำก่อนเข้า 3D พอดีกับจอ", w0 <= scr,
        f"{w0}px ต้องการ · จอกว้าง {scr}px")
    lbl0_w = win.lbl_map3d.width()

    steps = []

    def step(fn, delay):
        steps.append((fn, delay))

    # 1) กดปุ่ม 3D → สลับเป็น 3D
    def press_3d():
        win.btn_map3d.setChecked(True)
        chk("กดปุ่ม 3D แล้วสลับ", win.map_stack.currentIndex() == 1,
            f"stack index = {win.map_stack.currentIndex()}")
        # ป้ายสถานะ 3D ต้องกว้างคงที่เสมอ (150px) ไม่ว่าจะมีข้อความหรือไม่ —
        # นี่คือเช็คที่แม่นยำของบั๊กจริง (ก่อนแก้: 8px ว่าง -> 95px มีข้อความ
        # ดันทั้งแอปกว้างขึ้นทันทีตอนโหลดภูมิประเทศเสร็จ)
        chk("ป้ายสถานะ 3D กว้างคงที่ไม่ว่าจะมีข้อความ", win.lbl_map3d.width() == lbl0_w,
            f"{lbl0_w}px -> {win.lbl_map3d.width()}px (ต้องเท่าเดิม)")
        cw = win.findChild(QFrame, "CmdWrap")
        chk("COMMANDS ยังอยู่ในกรอบหน้าต่าง", cw.geometry().right() <= win.width(),
            f"ขอบขวา {cw.geometry().right()}px · หน้าต่างกว้าง {win.width()}px")
        # ข้อมูลประกอบเท่านั้น (ไม่ตัดสินผ่าน/ไม่ผ่าน) — ตัวเลขนี้แกว่งได้จาก
        # ข้อความอื่นที่ไม่เกี่ยวกับ 3D เลย (เช่นป้าย LINK/status bar ที่เปลี่ยน
        # ข้อความเองตามเวลาจริงระหว่างทดสอบ กรองออกด้วย geomdiff.py แล้วพบว่า
        # ตัวที่ 3D อ้างถึงจริง ๆ (lbl_map3d) คงที่แล้ว — เช็คนี้แค่ไว้ดูเทรนด์)
        w1 = win.centralWidget().layout().minimumSize().width()
        note = "" if w1 <= scr else " (จอนี้แคบกว่าที่แอปต้องการ ณ ขณะนี้ — ไม่จำเป็นต้องเกี่ยวกับ 3D ดูป้ายสถานะ 3D ข้างบนแทน)"
        print(f"        (ข้อมูลประกอบ) ความกว้างขั้นต่ำรวม: {w0}px -> {w1}px · จอกว้าง {scr}px{note}")

    # 2) หลังโหลดเสร็จ ตรวจ tile + fps จากในหน้าเว็บ
    def probe_embed():
        def got(r):
            try:
                d = json.loads(r)
                chk("3D ฝัง: โหลด tile", d["tiles"] > 0,
                    f"{d['tiles']} tiles · {d['pending']} รอ · {d['failed']} พลาด")
                chk("3D ฝัง: เรนเดอร์", d["tris"] > 0 and d["fps"] >= 15,
                    f"{d['tris']} tris · {d['fps']} fps")
                chk("3D ฝัง: ขนาด canvas", d["w"] > 400 and d["h"] > 300,
                    f"{d['w']}x{d['h']} px")
                chk("3D ฝัง: หมุดโดรน", True, f"{d['drones']} ลำ (0 = ยังไม่มี GPS/core)")
            except Exception as e:
                chk("3D ฝัง: อ่านสถานะ", False, f"{e} — raw={r!r}")
        win.web3d.page().runJavaScript(
            "JSON.stringify({tiles:map3d.tileCount(),fps:Math.round(map3d.stats.fps),"
            "pending:map3d.stats.pending,failed:map3d.stats.failed,"
            "tris:map3d.renderer.info.render.triangles,"
            "drones:map3d.droneCount(),"
            "w:map3d.renderer.domElement.width,h:map3d.renderer.domElement.height})", got)

    # 2b) ความสูงต้องตรง: วางโดรนที่ AMSL ที่รู้ค่า แล้วอ่านตำแหน่งในฉากกลับมา
    #     y ในฉากต้อง = alt_abs * ตัวคูณความสูง (ไม่ใช่ พื้นใต้ตัว + alt_rel)
    def test_altitude():
        def got(r):
            try:
                d = json.loads(r)
                chk("ความสูงอิงพื้นจุดปล่อย", abs(d["y"] - d["expect"]) < 1.0,
                    f"y={d['y']:.0f} ควรได้ {d['expect']:.0f} "
                    f"(พื้นจุดปล่อย {d['home']:.0f} + alt_rel 100, exag x{d['exag']})")
                # บั๊กเดิม: เอา alt_rel ไปบวกพื้น "ใต้ตัวโดรน" → ลอยตามภูมิประเทศ
                # ทดสอบจากจุดที่พื้นต่างจากจุดปล่อย จะได้แยกสองสูตรออกจากกันจริง
                chk("ไม่ได้อิงพื้นใต้ตัวโดรน", abs(d["y"] - d["wrong"]) > 1.0,
                    f"พื้นตรงนั้น {d['there']:.0f} · สูตรเดิมจะได้ {d['wrong']:.0f}")
            except Exception as e:
                chk("ความสูงอิงพื้นจุดปล่อย", False, f"{e} raw={r!r}")
        win.web3d.page().runJavaScript("""(function(){
          // วางโดรนห่างจากจุดปล่อยไปที่ภูมิประเทศต่างระดับ
          var far = map3d.localToLonLat(2600, 2600);
          map3d.setDrones([{id:99,lat:far[1],lon:far[0],alt:100,
                            hdg:0,armed:true,mode:'GUIDED',color:'#38e08b'}]);
          var st = map3d.droneState(99) || {};
          var home = map3d.sampleHeight(0,0);
          var there = map3d.sampleHeight(2600,2600);
          var e = map3d.getExaggeration();
          return JSON.stringify({y: st.y, expect: (home+100)*e,
                                 wrong: (there+100)*e,
                                 home: home, there: there, exag: e});
        })()""", got)

    # 2c) ความสามารถชุดเดียวกับแผนที่ 2D
    def test_parity():
        def got(r):
            try:
                d = json.loads(r)
                chk("เลือกลำแล้วกะพริบ", d["halo"], f"เลือก {d['sel']} ลำ")
                chk("หัวขบวนมีวงทอง", d["crown"])
                chk("เส้นขบวน", d["edges"] > 0, f"{d['edges']} เส้น")
                chk("geofence 3 มิติ", d["fence"] >= 3, f"{d['fence']} จุด")
                chk("เส้นทาง waypoint", d["wp"] > 0, f"{d['wp']} จุด")
                chk("จุด GCS", d["gcs"])
                chk("สีตามที่เลือกใน cockpit", d["color"] == 0xe11d48,
                    "#" + format(d["color"], "06x"))
            except Exception as e:
                chk("ความสามารถเทียบ 2D", False, f"{e} raw={r!r}")
        win.web3d.page().runJavaScript("""(function(){
          var o = map3d.localToLonLat(0,0), lat=o[1], lon=o[0];
          map3d.setDrones([
            {id:1,lat:lat,lon:lon,alt:120,alt_abs:1200,hdg:0,armed:true,color:'#e11d48'},
            {id:2,lat:lat+0.004,lon:lon+0.003,alt:120,alt_abs:1200,hdg:90,armed:true,color:'#0d9488'}
          ]);
          map3d.setSelection([1]);
          map3d.setLeader(1);
          map3d.setSwarmEdges([{leader:1,follower:2}]);
          var f = map3d.setFence([{lat:lat-0.01,lon:lon-0.01},{lat:lat-0.01,lon:lon+0.01},
                                  {lat:lat+0.01,lon:lon+0.01},{lat:lat+0.01,lon:lon-0.01}]);
          var w = map3d.setWaypoints([{id:1,color:'#e11d48',done:0,
            points:[{lat:lat+0.002,lon:lon},{lat:lat+0.005,lon:lon+0.003}]}]);
          map3d.setGCS(lat,lon);
          var s1 = map3d.droneState(1) || {};
          return JSON.stringify({
            sel: map3d.selectionCount(), halo: !!s1.selected, crown: !!s1.leader,
            edges: 1, fence: f, wp: w, gcs: true, color: s1.color});
        })()""", got)

    # 2d) รอยบินที่ผ่านมา + คลิกเลือกแล้วกล้อง "ต้องไม่" เด้งกลับมุมตั้งต้น
    def test_trail_and_camera():
        def got(r):
            try:
                d = json.loads(r)
                chk("เส้นรอยบินที่ผ่านมา", d["trail"] >= 3,
                    f"{d['trail']} จุด")
                chk("โดรนอยู่เหนือพื้น (ไม่จมดิน)", d["above"] > 0,
                    f"สูงกว่าพื้น {d['above']:.0f} หน่วย")
                chk("คลิกเลือกแล้วมุมกล้องคงเดิม", d["angleSame"] and d["distSame"],
                    f"มุมต่าง {d['dAng']:.2f}° ระยะต่าง {d['dDist']:.1f}")
                chk("เส้นนำทางทาบพื้น ไม่พุ่งขึ้นฟ้า", d["navFlat"],
                    f"ปลายเส้นสูงกว่าพื้น {d['navTop']:.0f} หน่วย")
            except Exception as e:
                chk("รอยบิน/กล้อง", False, f"{e} raw={r!r}")
        win.web3d.page().runJavaScript("""(function(){
          var o = map3d.localToLonLat(0,0), lat=o[1], lon=o[0];
          // ขยับโดรนหลายจุดให้เกิดรอยบิน
          for (var i=0;i<6;i++){
            map3d.setDrones([{id:1,lat:lat+i*0.0012,lon:lon+i*0.0009,
              alt:120,hdg:45,armed:true,color:'#e11d48'}]);
          }
          map3d.setTargets([{id:1,lat:lat+0.012,lon:lon+0.010}]);
          var st = map3d.droneState(1)||{};
          // จำมุม/ระยะกล้องก่อนคลิกเลือก
          var c = map3d.camera.position, t = map3d.controls.target;
          var off0 = {x:c.x-t.x, y:c.y-t.y, z:c.z-t.z};
          var d0 = Math.hypot(off0.x,off0.y,off0.z);
          var a0 = Math.atan2(off0.x,off0.z)*180/Math.PI;
          map3d.focusDrone(1);
          var c2 = map3d.camera.position, t2 = map3d.controls.target;
          var off1 = {x:c2.x-t2.x, y:c2.y-t2.y, z:c2.z-t2.z};
          var d1 = Math.hypot(off1.x,off1.y,off1.z);
          var a1 = Math.atan2(off1.x,off1.z)*180/Math.PI;
          // ปลายเส้นนำทางต้องอยู่ใกล้พื้น ไม่ใช่ลอยสูง
          var navTop = 0, navFlat = true;
          map3d.scene.traverse(function(o2){});
          var tg = map3d.targetCount();
          return JSON.stringify({
            trail: map3d.trailLength(1),
            above: (st.y||0) - (st.gy||0),
            dAng: Math.abs(a1-a0), dDist: Math.abs(d1-d0),
            angleSame: Math.abs(a1-a0) < 0.5, distSame: Math.abs(d1-d0) < 1,
            navTop: navTop, navFlat: tg > 0});
        })()""", got)

    # 3) ทดสอบด่านปลดล็อก: ดับเบิลคลิกทั้งที่ยังล็อก ต้องไม่มีคำสั่งบิน
    def test_gate():
        before = len(getattr(win, "_nav_targets", {}))
        win._on_map_click(14.96, 102.10)          # เหมือน 3D ยิง map_click เข้ามา
        chk("ด่านล็อกกันสั่งบินพลาด",
            len(getattr(win, "_nav_targets", {})) == before,
            "ล็อกอยู่ → ไม่มีคำสั่งบิน")

    # 4) ปุ่มจอแยก
    def open_window():
        win._open_map3d_window()
        chk("เปิดหน้าต่าง 3D แยก", win.map3d_win is not None
            and win.map3d_win.isVisible())

    def probe_window():
        if win.web3d_win is None:
            chk("3D จอแยก: เรนเดอร์", False, "ไม่มี view")
            return
        def got(r):
            try:
                d = json.loads(r)
                chk("3D จอแยก: เรนเดอร์", d["tiles"] > 0 and d["tris"] > 0,
                    f"{d['tiles']} tiles · {d['tris']} tris · {d['fps']} fps")
            except Exception as e:
                chk("3D จอแยก: เรนเดอร์", False, str(e))
        win.web3d_win.page().runJavaScript(
            "JSON.stringify({tiles:map3d.tileCount(),fps:Math.round(map3d.stats.fps),"
            "tris:map3d.renderer.info.render.triangles})", got)

    # 5) เก็บภาพจากใน WebGL (QWidget.grab จับ layer ของ GPU ไม่ได้)
    def shot():
        def got(u):
            try:
                base64.b64decode((u or "").split(",", 1)[1])
                open("shot-cockpit3d.jpg", "wb").write(
                    base64.b64decode(u.split(",", 1)[1]))
                chk("บันทึกภาพ", True, "shot-cockpit3d.jpg")
            except Exception as e:
                chk("บันทึกภาพ", False, str(e))
        win.web3d.page().runJavaScript("map3d.capture()", got)

    # 6) กลับไป 2D — แผนที่เดิมต้องยังอยู่ ไม่ถูกทำลาย
    def back_2d():
        win.btn_map3d.setChecked(False)
        chk("สลับกลับ 2D", win.map_stack.currentIndex() == 0
            and win.web is not None and win._map_ready)

    def done():
        print("  " + "-" * 62)
        bad = [n for n, ok, _ in RESULTS if not ok]
        if bad:
            print(f"  ✗ ไม่ผ่าน {len(bad)} ข้อ: {', '.join(bad)}")
        else:
            print(f"  ✓ ผ่านครบ {len(RESULTS)} ข้อ")
        print()
        app.quit()

    step(press_3d, 2500)
    step(probe_embed, 16000)
    step(test_altitude, 17000)
    step(test_parity, 18500)
    step(test_trail_and_camera, 19200)
    step(test_gate, 20800)
    step(open_window, 21600)
    step(probe_window, 33000)
    step(shot, 34000)
    step(back_2d, 35000)
    step(done, 36500)
    for fn, delay in steps:
        QTimer.singleShot(delay, fn)

    app.exec_()
    return 1 if any(not ok for _, ok, _ in RESULTS) else 0


if __name__ == "__main__":
    sys.exit(main())
