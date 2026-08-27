"""
help_dialog.py — คู่มือการใช้งาน + แผนที่ความสัมพันธ์ระหว่างฟังก์ชัน (แบบ modal)

เปิดจากปุ่ม "?" บน Top bar หรือเมนู ⚙ → "Help / คู่มือการใช้งาน"
เนื้อหาทั้งหมด self-contained (ไม่มีลิงก์ออกนอก, ไม่พึ่ง CDN) — ใช้งาน offline ได้
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QWidget,
)

from ..core.theme import T, rgba, hairline, FONT_FAMILY, FONT_MONO, ghost_btn, filled_btn


def _help_html() -> str:
    c_text = T("text")
    c_dim = T("dim")
    c_faint = T("faint")
    c_accent = T("accent")
    c_green = T("green")
    c_amber = T("amber")
    c_red = T("red")
    c_cyan = T("cyan")
    c_yellow = T("yellow")
    line = rgba("#ffffff", 0.10)
    row_bg = rgba("#ffffff", 0.03)

    def h2(txt, color=None):
        color = color or c_accent
        return (f'<h2 style="color:{color}; font-size:15px; font-weight:800; '
                f'letter-spacing:0.4px; margin:22px 0 8px 0; '
                f'border-bottom:1px solid {line}; padding-bottom:6px;">{txt}</h2>')

    def h3(txt, color=None):
        color = color or c_text
        return (f'<h3 style="color:{color}; font-size:12.5px; font-weight:700; '
                f'margin:14px 0 4px 0;">{txt}</h3>')

    def p(txt, color=None):
        color = color or c_dim
        return f'<p style="color:{color}; font-size:11.5px; line-height:1.6; margin:4px 0 10px 0;">{txt}</p>'

    def alert(title, body, color):
        return (f'<div style="background:{rgba(color, 0.10)}; border:1px solid {rgba(color, 0.4)}; '
                f'border-left:4px solid {color}; border-radius:6px; padding:9px 12px; margin:8px 0 14px 0;">'
                f'<div style="color:{color}; font-weight:800; font-size:11.5px; margin-bottom:3px;">'
                f'⚠ {title}</div>'
                f'<div style="color:{c_dim}; font-size:11px; line-height:1.55;">{body}</div></div>')

    def table(rows, headers):
        head = "".join(
            f'<th style="text-align:left; color:{c_faint}; font-size:10px; '
            f'letter-spacing:0.6px; padding:6px 10px; border-bottom:1px solid {line};">{hh}</th>'
            for hh in headers)
        body = ""
        for r in rows:
            cells = "".join(
                f'<td style="color:{c_dim}; font-size:11px; padding:7px 10px; '
                f'border-bottom:1px solid {rgba("#ffffff", 0.05)}; vertical-align:top;">{cell}</td>'
                for cell in r)
            body += f'<tr>{cells}</tr>'
        return (f'<table style="width:100%; border-collapse:collapse; background:{row_bg}; '
                f'border-radius:8px; margin:6px 0 16px 0;"><thead><tr>{head}</tr></thead>'
                f'<tbody>{body}</tbody></table>')

    def mono(txt, color=None):
        color = color or c_cyan
        return f'<code style="font-family:{FONT_MONO}; color:{color}; font-size:10.5px;">{txt}</code>'

    return f"""
    <div style="font-family:{FONT_FAMILY}; color:{c_text};">

    <h1 style="color:{c_text}; font-size:19px; font-weight:800; margin:0 0 4px 0;">
        ◆ คู่มือการใช้งาน SwarmGod Cockpit</h1>
    <p style="color:{c_faint}; font-size:11px; margin:0 0 18px 0;">
        อธิบายวิธีใช้แต่ละฟีเจอร์ + แผนที่ว่าปุ่มไหนผูกกับปุ่มไหน + เงื่อนไขที่มักทำให้งง
        + Field Tablet, แผนที่ 3D, Failsafe, ความปลอดภัย, ฉุกเฉิน + วิธีแก้ปัญหา</p>

    {h2("ก่อนบินจริง — PRE-FLIGHT TEST", c_amber)}
    {p("หมวดบนสุดของแผงขวา มี 2 ปุ่มที่ต้องผ่านก่อนปล่อยบินจริง "
       "ป้าย PREFLIGHT อยู่หัวแผง FLEET ข้าง ONLINE "
       "สีแดง = ยังไม่เทส (มีแถบเตือนค้าง) · เหลือง = ทำไปบางส่วน · เขียว = ผ่านครบ")}
    {table([
        ["SYSTEM TEST",
         "เครื่องตรวจให้เอง: core/mTLS/token · telemetry สด · GPS/แบต/อยู่บนพื้น · "
         "ระยะห่างกันชน · ค่า TAKEOFF/RTL/Geofence/หัวขบวน — แล้วยิงคำสั่งจริง "
         "(HOLD + สั่งโหมด GUIDED) ดูว่า FC ตอบรับกลับมาจริงไหม"],
        ["CHECKLIST",
         "รายการจาก docs/REAL_FLIGHT_CHECKLIST.md ที่ต้องใช้ตาคนตรวจ — ติ๊กเอง "
         "ระบบจำไว้ข้ามรอบเปิดโปรแกรม"],
        ["ติ๊ก «ถอดใบพัดแล้ว»",
         "ปลดล็อกอีก 2 ข้อที่สั่งของจริง: ARM→DISARM และตรวจว่า core ปฏิเสธ "
         "TAKEOFF ที่ไม่ยืนยัน — ห้ามติ๊กถ้าใบพัดยังอยู่"],
    ], ["ปุ่ม", "ทำอะไร"])}
    {alert("ยังไม่ได้เทสแล้วกด TAKEOFF",
           "ระบบจะถามก่อนทุกครั้ง: RUN TEST (เปิดชุดทดสอบให้ทันที แล้วบินต่อให้ถ้าผ่าน) · "
           "SKIP · TAKEOFF (บินได้ แต่ขึ้น banner แดงและบันทึกไว้ใน log ว่าข้ามด่าน) · "
           "CANCEL — ผลเทสอยู่ตลอดการเปิดโปรแกรมครั้งนั้น ปิดแล้วเปิดใหม่ต้องเทสใหม่",
           c_amber)}

    {h2("1. การเลือกโดรน")}
    {table([
        ["คลิกการ์ดฝั่งซ้าย 1 ครั้ง", "เลือกลำเดียว (ยกเลิกลำอื่นที่เคยเลือกไว้)"],
        ["คลิกรายการในการ์ด ‘โดรน’ บน Map 3D", "เลือกลำนั้นและเปิดรายละเอียดในการ์ดล่างทันที"],
        ["Ctrl + คลิก", "เพิ่ม/เอาลำนั้นออกจากชุดที่เลือก (เลือกได้หลายลำ)"],
        ["ปุ่ม FLEET (หมวด FLIGHT)", "เปิด = เลือกทุกลำอัตโนมัติ · ปิด = ล้างการเลือกทั้งหมด"],
    ], ["วิธีทำ", "ผลลัพธ์"])}
    {p("เลือกเองจนครบทุกลำ → ปุ่ม FLEET จะติดเองอัตโนมัติ · เลือกไม่ครบ → ปุ่มดับเอง (sync กันตลอดเวลา)")}
    {p("กดปุ่มดินสอข้างชื่อในการ์ดล่างเพื่อแก้ชื่อโดรน ชื่อจะใช้ในรายการและแผนที่ พร้อมบันทึกกับ endpoint เดิม")}

    {h2("2. Head / Leader (หัวขบวน)")}
    {p("กดดาว ☆ บนการ์ด หรือปุ่ม SET HEAD ที่การ์ดล่าง → ต้องกดยืนยันใน pop-up ก่อนเสมอ")}
    {p("ถ้าหัวขบวนหลุดการเชื่อมต่อ ระบบจะเลื่อนลำถัดไปที่ยังออนไลน์ขึ้นเป็นหัวให้อัตโนมัติ (Auto-Reassign)")}

    {h2("3. Take off")}
    {table([
        ["All", "ทุกลำที่เลือกขึ้นบินพร้อมกัน"],
        ["Sequential", "หัวขบวนขึ้นก่อน แล้วลูกขบวนทยอยขึ้นตามลำดับ (เว้นช่วง ~2.5 วิ/ลำ)"],
        ["กด TAKE OFF ในหมวด Swarm", "แนบคำสั่ง Form Up ให้อัตโนมัติ ไม่ต้องกดเอง"],
    ], ["โหมด", "พฤติกรรม"])}
    {p(f"ความสูงดีฟอลต์ {mono('20 m')} ทุกลำ — ตั้งรายลำได้ที่ช่อง ALT ในการ์ดล่างของแต่ละลำ")}
    {p(f"{mono('TAKEOFF ALT')} คือความสูงเป้าหมายหลังขึ้นบิน · {mono('SPACING')} คือระยะห่างจากลำหน้าเมื่อบินเป็นขบวน ไม่ใช่ความสูงหรือระยะ GOTO")}

    {h2("4. RTL (กลับฐาน) — แยกชั้นความสูงกันชน")}
    {p(f"ตั้งค่าที่หน้า SAFETY & SYSTEM: {mono('RTL BASE ALTITUDE')} = ค่าที่บวกเพิ่มจากความสูงปัจจุบันของแต่ละลำ, "
       f"{mono('RTL MIN GAP')} = ระยะห่างขั้นต่ำระหว่างชั้น กันชนตอนบินอยู่ระดับเดียวกัน")}
    {p("แต่ละลำไต่ระดับ → บินกลับ home ที่ความสูงของตัวเอง → ลงจอด อิสระไม่ต้องรอกัน "
       "ลำไหนถึงก่อนก็บินกลับ/ลงจอดได้เลย")}

    {h2("5. Waypoint Route Planning (วางแผนเส้นทางบิน)")}
    {p("เปิดสวิตช์ Waypoint Mode ในแผงขวา แล้วคลิกแผนที่เพื่อวางจุด (ไม่บินทันที) — "
       "จุด/เส้นประจะมีสีตรงกับโดรนที่วางแผนให้")}
    {table([
        [f"<b style='color:{c_green}'>GROUPED</b> (ดีฟอลต์)", "เส้นทางเดียวร่วมกัน · ทุกลำไปจุดเดียวกันพร้อมกัน "
         "คงรูปขบวน/ระยะห่าง · รอครบทุกลำก่อนไปจุดถัดไป"],
        [f"<b style='color:{c_cyan}'>SEPARATE</b>", "แต่ละลำมีเส้นทางของตัวเอง — จุดที่คลิกจะเข้าเส้นทางของ "
         "<i>ลำที่กำลังโฟกัสอยู่</i> (คลิกการ์ดซ้ายเพื่อสลับ) · บินอิสระไม่ต้องรอกัน"],
    ], ["โหมด", "พฤติกรรม"])}
    {alert("โหมด Swarm ใช้ SEPARATE ไม่ได้",
           "ระหว่างโหมด Swarm กำหนดเส้นทางได้เฉพาะ <b>ลำแม่ (Head)</b> เท่านั้น (Leader Path) "
           "ทั้งขบวนจะบินตามพร้อมกันโดยอัตโนมัติ ต้องออกจากโหมด Swarm ก่อนถึงจะแยกเส้นทางรายลำได้ "
           "— กดสลับไป SEPARATE ตอน Swarm เปิดอยู่ ระบบจะปฏิเสธและเด้งปุ่มกลับให้เอง", c_amber)}
    {alert("กันชนก่อนบิน (เฉพาะโหมด SEPARATE)",
           "กด EXECUTE แล้วระบบจะตรวจก่อนว่ามีคู่ไหน <b>อยู่ระดับความสูงเดียวกัน</b> "
           "(ต่างกันไม่เกิน 2 m) <b>และ</b> เส้นทางเข้าใกล้/ตัดกัน (น้อยกว่า 6 m) หรือไม่ "
           "— ถ้าเจอจะ <b>บล็อกไม่ให้บิน</b> ทันที พร้อมบอกคู่ที่เสี่ยง แก้ได้โดยตั้งความสูง (ALT) "
           "ของแต่ละลำในการ์ดฝั่งซ้ายให้ต่างกัน", c_red)}
    {p("ปุ่ม UNDO ลบจุดสุดท้าย (ของลำที่โฟกัสอยู่ถ้าเป็น SEPARATE) · CLEAR ล้างทุกจุดทุกลำ (ถามยืนยันก่อน)")}
    {h3("กลุ่มโดรน")}
    {p("ใช้ชิป ทั้งหมด / 1–6 เหนือรายการโดรนเพื่อเลือกทั้งกลุ่ม (เช่น 1·2 = กลุ่ม 1 มี 2 ลำ) · Ctrl+คลิกเลือกเพิ่มหลายกลุ่ม "
       "· คลิกขวาที่ชิปเพื่อกำหนดลำที่เลือก หรือคลิกขวาที่การ์ดเพื่อย้ายลำเดียว "
       "· คีย์ 1–6 = เลือก, Ctrl+1–6 = กำหนดกลุ่ม กลุ่มเปลี่ยนแค่ selection ไม่สั่งบินเอง")}
    {h3("WAVE และการปล่อยของตาม Waypoint")}
    {p("WAVE อยู่ใน WAYPOINT ROUTE และใช้ได้เฉพาะ GROUPED: เลือกอย่างน้อย 2 กลุ่มแล้ว EXECUTE "
       "ระบบจะให้แต่ละกลุ่มบินเส้นทางเดียวกันตามเลขกลุ่ม กลับฐานและลงจอด "
       "กลุ่มถัดไปเริ่มต่อเมื่อกลุ่มเดิม disarm ครบ · timeout 5 นาทีจะหยุดทั้งชุด")}
    {p("คลิกขวาที่จุด Waypoint หรือรายการจุด เลือก ปล่อย A (CH7) / B (CH8) / ไม่ทำอะไร "
       "เมื่อถึงจุดระบบ HOLD → ปล่อย → รอ 2 วินาที → ไปต่อ โดยผลปล่อย A/B จะแสดง ✓ ในแท็บ ACTIVE")}
    {p("ใน WAVE สามารถติ๊ก AUTO NEXT GROUP เพื่อให้กลุ่มถัดไป TAKEOFF และทำงานต่อเอง "
       "โดยไม่ถามยืนยัน TAKEOFF ซ้ำ; กลุ่มแรกและแผนปล่อยของยังต้องยืนยันตามปกติ")}
    {alert("EXECUTE ตรวจ TAKEOFF ให้อัตโนมัติ",
           "ถ้าโดรนเป้าหมายยังอยู่พื้น ระบบจะถามพร้อมรายชื่อและความสูง เมื่อยืนยันจะ TAKEOFF "
           "แล้วรอจน Armed + สูงพ้น 1.5 m ก่อนเริ่ม GOTO (ไม่เกิน 90 วินาที) · WAVE ตรวจใหม่ทุกกลุ่ม "
           "กลุ่มที่บินครบแล้วไม่ถามซ้ำ · ปุ่ม TAKE OFF / RTL / LAND / GOTO / ARM / HOLD / E-STOP ยังทำงานเหมือนเดิม", c_amber)}
    {p("กล่อง PRE-FLIGHT SUMMARY แสดงลำ/กลุ่มที่เลือก, จุด Waypoint และ A/B, ลำดับ WAVE, "
       "แผน TAKEOFF, EXECUTE และรายการยกเลิกล่าสุด เช่น Cancel Nav, Undo/Clear, ปิด WAVE หรือยกเลิก Servo")}

    {h2("6. Tactical Planning / Geofence")}
    {p("Tactical = วางแผนอย่างเดียว (เส้น/รูปหลายเหลี่ยม/วงกลม/ข้อความ/สัญลักษณ์อัปโหลดเอง) "
       "ไม่ส่งไปที่โดรน · บันทึก/โหลดเป็นไฟล์ GeoJSON ได้")}
    {p("Geofence = เขตห้ามบิน บังคับใช้จริงที่ Go core — วาดแยกจาก Tactical โดยสิ้นเชิง")}

    {h2("7. Collision Detection")}
    {p(f"ตรวจระยะห่างทุกคู่โดรนแบบ real-time ทุก 0.7 วิ — ≤3 m = "
       f"<span style='color:{c_red}; font-weight:700;'>วิกฤต (สีแดง)</span>, "
       f"≤6 m = <span style='color:{c_amber}; font-weight:700;'>เตือน (สีเหลือง)</span> "
       f"(ต่างระดับความสูงเกิน 2 m ถือว่าคนละชั้น ไม่นับว่าเสี่ยง)")}

    {h2("8. แผนที่ 2D / 3D และแถบสถานะ")}
    {table([
        ["3D", "สลับพื้นที่แผนที่เดิมเป็นภูมิประเทศ 3 มิติ · กดซ้ำกลับเป็น 2D — ไม่เปิดเอง"],
        ["3D ⧉", "เปิดแผนที่ 3D เป็นหน้าต่างแยกสำหรับจอที่สอง"],
        ["ไปที่ GPS", "กระโดดแผนที่ไปพิกัดโดรนที่เลือก (ต่อโดรนแล้วระบบกระโดดให้เองครั้งแรก)"],
        ["ใส่พิกัด", "พิมพ์ lat, lon เพื่อดูพื้นที่อื่น แล้วกด CACHE AREA ถ้าจะเก็บไว้ใช้ offline"],
        ["CACHE AREA", "ดาวน์โหลดภาพดาวเทียมของพื้นที่บนจอ + ความสูง 3D — ใช้เน็ตเฉพาะตอนนี้ ไม่กี่ MB ต่อพื้นที่"],
        ["การ์ด ‘โดรน’ มุมขวาบน", "แสดงโดรนแต่ละลำ สีประจำลำ ความสูง และโหมดบิน · กดลูกศรเพื่อพับ/กาง และคลิกรายการเพื่อเลือกโดรน"],
        ["แถบ LAT/LNG ใต้แผนที่", "ย่ออัตโนมัติเมื่อพื้นที่แคบ · ชี้เมาส์ค้างเพื่ออ่านข้อความเต็ม"],
    ], ["ส่วนควบคุม", "ความหมาย"])}
    {p("ลากเมาส์บน 3D เพื่อหมุน · ใช้ล้อเพื่อซูม · ปลดล็อกปุ่มโหมดสั่งบินก่อนดับเบิลคลิกพื้นเพื่อ GOTO")}
    {p("ป้าย CORE และ LINK บนแถบบนเป็นสถานะย่อ: CORE = core พร้อมทำงาน · LINK OK = ได้รับ telemetry จากโดรน")}
    {p("Mission Log จัดเครื่องมือเป็นสองแถวคงที่และไม่มีการเลื่อนแนวนอน: แถวบนเลือก All / Commands / Alerts / Telemetry แถวล่างค้นหา/Pause/Clear/Export และมีตัวกรอง System/รายโดรน")}

    {h3("รีเซ็ตแบตเตอรี่ระหว่างซ้อม SITL")}
    {p("เปิดเมนูจุดสามจุดที่หัวแผง COMMANDS → ‘รีเซ็ตแบตเตอรี่จำลอง (SITL)…’ → เลือกแรงดันและยืนยัน ใช้เมนูนี้เฉพาะโดรนจำลองและต้อง disarm ก่อน ห้ามใช้กับ Flight Controller จริง")}

    {h2("9. หยุด/ยกเลิกคำสั่ง")}
    {table([
        ["Cancel Nav (หมวด FLIGHT)", "ล้างเป้าหมาย/เส้นทาง Waypoint ที่ค้างอยู่ทั้งหมด แล้วสั่งโดรน "
         "<b>หยุดลอยค้างที่เดิม</b> (Hold)"],
        ["HOLD (Quick Action บนการ์ด)", "สั่งลำนั้นลำเดียวหยุดลอยค้างที่เดิม โดยไม่ยุ่งกับเป้าหมายบนแผนที่"],
        ["Emergency Stop (การ์ด/แผงขวา)", "หยุดทันที (รายลำ/ALL) — ตัดทั้ง RTL และ Waypoint ที่ค้างอยู่ด้วย "
         "ต้องยืนยัน 2 ชั้นก่อนเสมอ"],
    ], ["ปุ่ม", "ผลลัพธ์"])}
    {h3("HOLD ทำงานยังไง — และทำไมถึงไม่ทำให้โดรนตก")}
    {p(f"กด Cancel Nav / HOLD ระหว่างบิน Waypoint แล้วโดรนจะ <b>ค้างอยู่กับที่ ความสูงเท่าเดิม</b> "
       f"โดยยังอยู่โหมด {mono('GUIDED')} — สั่งบินต่อได้ทันทีโดยไม่ต้องเปลี่ยนโหมดอะไรอีก")}
    {alert("ทำไมไม่ใช้โหมด LOITER",
           f"{mono('LOITER')} / {mono('ALT_HOLD')} / {mono('POSHOLD')} ของ ArduPilot เอา "
           "<b>อัตราไต่-ลง จากสติ๊กคันเร่งของรีโมท</b> ไม่ใช่จากตัวคุมอัตโนมัติ "
           "ระหว่างบินอัตโนมัติสติ๊กคันเร่งอยู่ตำแหน่งต่ำสุดเสมอ (หรือไม่มี RC ต่ออยู่เลยอย่างใน SITL) "
           "ถ้าสลับไปโหมดพวกนี้กลางอากาศ FC จะอ่านว่า <b>“นักบินสั่งลง”</b> แล้วร่วงลงด้วยอัตราสูงสุดทันที "
           "— เดิม Hold ใช้ LOITER จึงเกิดอาการ “กดยกเลิกแล้วโดรนตก” · ตอนนี้เปลี่ยนเป็นตรึงพิกัดใน GUIDED แล้ว",
           c_amber)}
    {p("ถ้าลำนั้น <b>ยังไม่มีพิกัด GPS</b> ระบบจะสั่งความเร็ว 0 แทนการตรึงพิกัด (กันไหลต่อ) "
       "และถ้ายังไม่ armed จะไม่ไปยุ่งกับโหมดเลย")}


    {h2("10. SERVO A / B (กลไกปล่อยของ)")}
    {p(f"ปุ่ม {mono('A')} = CH7 · {mono('B')} = CH8 (หมวด FLIGHT ฝั่งขวา) — สั่งผ่าน "
       f"{mono('RC_CHANNELS_OVERRIDE')} จึงใช้ร่วมกับสวิตช์บนรีโมทได้ทั้งสองทาง")}
    {table([
        [f"<b>{mono('A ⌛')}</b>", "<b>โปรแกรมกำลังเตรียมช่องให้เอง</b> (อัตโนมัติหลังเชื่อมต่อ) — "
         "กดได้เลยถ้าต้องการ แต่คำสั่งแรกอาจหน่วง · รอจนกลับเป็นสีปกติ = กดแล้วติดทันที"],
        [f"<b>{mono('A')}</b>", "ห้องว่าง พร้อมใช้งาน — กดเพื่อเปิด"],
        [f"<b>{mono('A ⋯')}</b>", "<b>สั่งไปแล้ว กำลังรอเครื่องบินตอบรับ</b> — ระบบส่งซ้ำให้เอง "
         "ทุก 0.5 วิ <b>ไม่ต้องกดซ้ำ</b> (กดซ้ำ = ยกเลิกคำสั่งที่รออยู่)"],
        [f"<b>{mono('A ●')}</b>", "เครื่องบินยืนยันแล้ว (ขาเซอร์โวขยับจริง) — กดซ้ำเพื่อปิดและคืนช่องให้รีโมท"],
        [f"<b>{mono('A 🔒')}</b>", "รีโมทถือห้องอยู่ — UI สั่งไม่ได้ ต้องปิดสวิตช์ที่รีโมทก่อน"],
    ], ["หน้าปุ่ม", "ความหมาย"])}
    {p("กติกา “1 ห้อง”: ช่องหนึ่งมีเจ้าของได้ทีละฝ่าย — ฝ่ายที่ไม่ได้ถืออยู่สั่งไม่ได้ "
       "จนกว่าเจ้าของจะปิดงานของตัวเองก่อน")}
    {p("<b>ไฟปุ่มติดทันทีที่กด</b> ไม่ต้องรอ telemetry รอบถัดไป — ถ้าค่าจริงจากเครื่องบิน "
       "กลับมาไม่ตรงกับที่สั่ง (เช่น สวิตช์บนรีโมทค้างที่ตำแหน่งเปิด) ปุ่มจะเด้งกลับเป็น "
       "สถานะจริงภายใน 1 วินาที พร้อมขึ้น banner เตือน")}
    {alert("โปรแกรมเตรียมช่อง A/B ให้เองอัตโนมัติ",
           "Flight Controller ใช้เวลารับคำสั่ง RC <b>ครั้งแรก</b>หลังเชื่อมต่อ 1-25 วินาที "
           "(วัดจากโดรนจริงหลายรอบ — เป็นพฤติกรรมฝั่ง FC ไม่ใช่โปรแกรมค้าง คำสั่งออกจาก "
           "โปรแกรมภายใน ~10 ms เสมอ) พอผ่านครั้งแรกแล้วติดทันทีตลอด<br><br>"
           "โปรแกรมจึง<b>ส่งคำสั่งเตรียมช่องให้เองเงียบ ๆ ทันทีที่โดรนเข้าฝูง</b> "
           "แล้วรอจนเครื่องบินตอบรับ ผู้ใช้ไม่ต้องกดทิ้งเองอีก<br><br>"
           "<b>ปลอดภัย</b>: ค่าที่ใช้เตรียมอยู่ใน<b>ย่านปิด</b> ไม่ใช่ค่าเปิด — กลไกไม่ปล่อยของ · "
           "คืนช่องให้รีโมททันทีที่เตรียมเสร็จ · ข้ามช่องที่รีโมทถืออยู่<br><br>"
           "ดูสถานะที่ปุ่ม: <b>⌛ = กำลังเตรียม</b> → <b>ปกติ = พร้อม</b> → "
           "<b>⋯ = สั่งแล้วรอ</b> → <b>● = ยืนยันแล้ว</b>", c_amber)}
    {alert("A/B ยิงทันที ไม่มีกล่องยืนยัน",
           "ต่างจากปุ่มอื่น (DISARM / E-STOP / Clear Waypoint) ที่ยังถามยืนยันอยู่ — "
           "A/B ตัดกล่องยืนยันออกเพื่อความเร็วในการใช้งานจริง <b>กดแล้วสั่งเลย</b> "
           "สิ่งที่กันพลาดแทนคือ เป็นปุ่ม toggle (กดซ้ำ = ยกเลิกทันที), กติกา 1 ห้อง, "
           "โหมด REMOTE บล็อกทั้งหมด และมี toast + Mission Log บันทึกทุกครั้งที่กด",
           c_amber)}


    {h2("แผนที่ความสัมพันธ์ระหว่างฟังก์ชัน", c_yellow)}
    {p("ปุ่ม/โหมดต่าง ๆ ในระบบถูกออกแบบให้ <b>กันชนกันเองอัตโนมัติ</b> เกือบทุกจุด — เปิดอันหนึ่ง "
       "จะปิดอีกอันที่ขัดแย้งกันให้เอง ไม่ต้องกังวลว่ากดพร้อมกันแล้วจะพัง")}

    {h3("กลุ่ม “แตะแผนที่” — ใช้ได้ทีละระบบเท่านั้น")}
    {p("ลำดับที่ระบบเช็คตอนคลิกแผนที่ (ตัดสินใจว่าจะทำอะไร):")}
    {table([
        ["1", "Waypoint Mode เปิดอยู่ไหม?", "ใช่ → วางจุดบนเส้นทาง จบเลย"],
        ["2", "Tactical Tool เปิดอยู่ไหม?", "ใช่ → วาดแผนยุทธวิธี จบเลย"],
        ["3", "Geofence Draw Tool เปิดอยู่ไหม?", "ใช่ → วาดรั้ว จบเลย"],
        ["4", "ไม่มีอันไหนเปิด", "คลิก = สั่งบินตรงไปจุดนั้นทันที (Direct Flight)"],
    ], ["ลำดับ", "เงื่อนไข", "ผล"])}
    {p("เปิด Waypoint → ปิด Tactical + Geofence ให้เอง · เลือกเครื่องมือ Tactical/Geofence → ปิด Waypoint ให้เอง")}

    {h3("กลุ่ม “โหมดการบิน” — ผูกกันแน่น")}
    {table([
        ["เปิดโหมด Swarm ระหว่าง Waypoint เป็น SEPARATE อยู่", "บังคับสลับกลับ GROUPED ทันที"],
        ["กด SEPARATE ตอน Swarm เปิดอยู่", "ถูกปฏิเสธ + ปุ่มเด้งกลับ GROUPED เอง"],
        ["เปลี่ยน GROUPED ↔ SEPARATE", "ล้างจุด Waypoint ที่วางค้างไว้ทิ้งทันที (คนละรูปแบบ เก็บต่อกันไม่ได้)"],
    ], ["เหตุการณ์", "ผลลัพธ์"])}
    {p(f"ป้ายโหมดการบินบน Top bar โชว์ได้ทีละสถานะ เรียงความสำคัญ: "
       f"<b style='color:{c_red}'>RTL</b> &gt; <b style='color:{c_green}'>Waypoint</b> &gt; "
       f"<b style='color:{c_amber}'>Swarm</b> &gt; Flight ปกติ")}

    {h3("กลุ่ม “ปุ่มหยุด/ยกเลิก” — ยิ่งแรงยิ่งตัดทุกอย่างที่ค้างอยู่")}
    {table([
        ["Cancel Nav", "ตัด Waypoint Execute + ล้างเป้าหมายบนแผนที่ + สั่ง Hold "
         "(ค้างที่เดิม ความสูงเท่าเดิม ไม่ลดระดับ)"],
        ["Emergency Stop", "ตัด RTL + ตัด Waypoint Execute + สั่งหยุดทันที (แรงที่สุด)"],
    ], ["ปุ่ม", "ครอบคลุมอะไรบ้าง"])}
    {p("ทั้งสองปุ่มนี้ครอบทุกโหมดการบิน ไม่ว่ากำลัง RTL / Waypoint / Swarm อยู่ก็ตัดได้หมด — กดตัวไหนก็ปลอดภัย")}

    {h3("กลุ่มที่ไม่ผูกกันเลย (กดพร้อมกันได้สบาย)")}
    {p("เปลี่ยนสีโดรน · ตั้ง ALT/SPACING รายลำ · ปุ่ม FLEET กับคลิกเลือกเอง (sync กันตลอด) · "
       "ขยายฟอนต์ · เมนู Save/Load · โลโก้ — เป็น UI ล้วน ๆ ไม่แตะ state การบินเลย")}


    {h2("เงื่อนไขพิเศษเดี่ยว ๆ ที่มักทำให้งง", c_yellow)}

    {alert("เปลี่ยน Head ไม่ได้ — เพราะอะไร?",
           "ระบบจะ<b>ปฏิเสธการเปลี่ยน Head ทันที</b> (ขึ้น toast + banner สีแดงบอกเหตุผล) เมื่อ:"
           "<ul style='margin:6px 0 0 18px; padding:0;'>"
           "<li>กำลังทำ <b>RTL</b> อยู่ (ทุกลำกำลังกลับฐาน)</li>"
           "<li>มีลำไหนกำลัง <b>TAKEOFF</b> หรือ <b>LANDING</b> อยู่ (ช่วงเปลี่ยนสถานะที่บอบบาง)</li>"
           "<li>ขบวน Swarm <b>กำลังเคลื่อนที่จริง</b> (ตรวจจาก ground speed ของโดรน ไม่ใช่แค่โหมด Swarm เปิดอยู่)</li>"
           "</ul>"
           "<b>ไม่ใช่บั๊ก</b> — เป็นการล็อกความปลอดภัยตั้งใจ เพราะเปลี่ยนหัวขบวนกลางอากาศตอนโดรนกำลัง "
           "เปลี่ยนสถานะ/เคลื่อนที่ อาจทำให้คำนวณเป้าหมายผิดพลาดจนโดรนชนกันได้ "
           "รอให้โดรนนิ่ง (ลอยอยู่กับที่) หรือ RTL/Takeoff/Landing เสร็จก่อน ค่อยเปลี่ยน Head",
           c_red)}

    {alert("โหมด REMOTE — ทำไมกดปุ่มอื่นไม่ติด?",
           "สวิตช์ <b>UI / REMOTE</b> บน Top bar ควบคุมว่าใครเป็นคนสั่งโดรน — "
           "เปิด <b>REMOTE</b> = มอบการควบคุมให้รีโมทจริง (เช่น นักบินถือจอยคุมเอง) "
           "ระบบจะ<b>ล็อกคำสั่งบินทั้งหมดจาก UI ทันที</b> "
           "(Arm / Disarm / Takeoff / RC / Waypoint / RTL / Swarm — กดไม่ติดทุกปุ่ม) "
           "พร้อมขึ้น <b>banner สีเหลืองค้างไว้ตลอดเวลา</b> ที่ใต้ Top bar จนกว่าจะสลับกลับ UI "
           "และถ้าเผลอกดปุ่มคำสั่งใด ๆ ตอน REMOTE เปิดอยู่ banner จะเด้งย้ำเตือนซ้ำทุกครั้ง "
           "— สลับกลับเป็น <b>UI</b> ที่สวิตช์บน Top bar เพื่อสั่งงานจาก cockpit ได้ตามปกติ",
           c_amber)}


    {h2("แผนที่ 3D", c_cyan)}
    {p("สลับแผนที่ในหน้าต่างเดิมเป็น 3 มิติ หรือเปิดหน้าต่างแยกลากไปจอที่ 2 ได้ "
       "เปิดพร้อมกันทั้งสองบานได้ รับตำแหน่งโดรนชุดเดียวกัน")}
    {table([
        ["3D", "สลับแผนที่ในหน้าต่างเดิมเป็น 3 มิติ (กดซ้ำกลับ 2D)"],
        ["3D จอแยก", "เปิดหน้าต่างใหม่ ลากไปจอที่ 2 ได้"],
    ], ["ปุ่ม", "ทำอะไร"])}
    {p("<b>ใน 3D:</b> ลาก = หมุน · ล้อ = ซูม · <b>ดับเบิลคลิกพื้น = สั่งบินไปจุดนั้น</b> "
       "(ดับเบิล ไม่ใช่คลิกเดี่ยว เพราะคลิกเดี่ยวใช้หมุนกล้อง)")}
    {p("การ์ด <b>โดรน</b> มุมขวาบน แสดงจำนวน + สีประจำลำ + ความสูง + โหมดบิน "
       "กดลูกศรเพื่อพับ/กาง คลิกรายการเพื่อเลือกลำนั้น")}
    {p(f"<b>ตัวคูณความสูง</b> (มุมล่างขวา): คลิกเพื่อวน {mono('x1 → x1.5 → x2 → x3 → x4')} "
       "ช่วยเห็นความต่างระดับในพื้นที่ราบ — <b>ห้ามเอาไปวัดความสูงจริง</b>")}
    {table([
        ["ความสูง (DEM)", "AWS Terrarium tile — สูตร (R×256 + G + B/256) − 32768"],
        ["ภาพพื้น", "Esri World Imagery (อันเดียวกับ 2D)"],
    ], ["ชั้นข้อมูล", "แหล่ง"])}
    {p("ทั้งสองชั้นใช้ tile cache/offline เดียวกับแผนที่ 2D ทั้งหมด ปุ่ม CACHE THIS AREA "
       "ดึงภาพทั้ง 2D และ DEM ให้พร้อมกัน")}
    {alert("ความสูงโดรนใน 3D",
           "ใช้สูตร <b>ความสูงพื้นที่จุดปล่อย + alt_rel</b> ไม่ใช่ alt_abs — "
           "เพราะ GPS ให้ความสูงเทียบทรงรี WGS84 แต่ DEM เทียบ geoid (ในไทยต่างกันราว −30m) "
           "เอามาวางตรง ๆ โดรนจมดินได้ มีตัวกันจมดินอีกชั้น: โดรนถูกบังคับไม่ต่ำกว่าผิวภูมิประเทศเสมอ",
           c_amber)}


    {h2("Field Tablet (แท็บเล็ตสนาม)", c_green)}
    {p("เว็บแอปสำหรับใช้บนแท็บเล็ตในสนาม เชื่อมผ่าน <b>LAN เท่านั้น</b> (ไม่ออกอินเทอร์เน็ต) "
       "ให้คนหน้างานดูสถานะและสั่งงานพื้นฐานได้")}
    {h3("เปิดใช้งาน")}
    {p("ในเมนู ⚙ → เปิด Field Server → ได้ PIN 6 หลัก "
       "บนแท็บเล็ตเปิดบราวเซอร์ไปที่ http://&lt;IP cockpit&gt;:8760 → ใส่ PIN "
       "→ ได้ token เป็น PILOT หรือ VIEWER")}
    {table([
        ["ดู telemetry + แผนที่", "PILOT + VIEWER"],
        ["E-STOP / HOLD", "PILOT + VIEWER (ห้ามกันออก)"],
        ["Waypoint / Swarm / GOTO", "PILOT เท่านั้น"],
        ["Movement (D-pad)", "PILOT เท่านั้น"],
    ], ["ฟังก์ชัน", "สิทธิ์ที่ใช้ได้"])}
    {h3("4 แท็บคำสั่ง")}
    {table([
        ["FLIGHT", "TAKEOFF / RTL / LAND / HOLD / ARM / DISARM / Servo A-B / Speed"],
        ["WAYPOINT", "เปิด/ปิด WP Mode · แตะแผนที่วางจุด · UNDO / CLEAR / EXECUTE"],
        ["SWARM", "เปิด/ปิด Swarm · เลือกรูปขบวน · FORM UP / STOP / RETURN+LAND"],
        ["MOVE", "D-pad 8 ทิศ + UP/DOWN · ต้องปลดล็อกก่อน · ≤ 3 m/s · deadman 1.2 วิ"],
    ], ["แท็บ", "ฟังก์ชัน"])}
    {h3("Fleet Health — ชิปสุขภาพฝูง")}
    {p("ชิปบนแถบบนแสดงสถานะแย่ที่สุดในฝูง:")}
    {table([
        [f"<b style='color:{c_red}'>วิกฤต</b>",
         "BAT < 15% หรือ link > 10 วินาที — ชื่อลำ + สาเหตุ กะพริบแดง"],
        [f"<b style='color:{c_amber}'>เตือน</b>",
         "BAT ≤ 25% หรือ link > 3 วินาที — จำนวน + สาเหตุ สีเหลือง"],
        [f"<b style='color:{c_green}'>ปกติ</b>",
         "ทุกลำดี — สีเขียว"],
    ], ["ระดับ", "เงื่อนไข"])}
    {alert("ความปลอดภัย Field Tablet",
           "<b>LAN เท่านั้น</b> ห้าม port forward ห้ามออกเน็ต "
           "· ทุกคำสั่งผ่าน allowlist (ไม่ใช่ blocklist) "
           "· ไม่ยิง gRPC ตรงจาก HTTP — ผ่าน WebBridge → dispatch → เมธอดเดิม "
           "· ไม่แตะ Qt object จาก HTTP thread "
           "· Rate limit: คำสั่งปกติ 2/วิ, movement 10/วิ (bucket แยก) "
           "· VIEWER กด HOLD/E-STOP ได้เสมอ — ห้ามกันออก",
           c_green)}


    {h2("Failsafe อัตโนมัติ", c_red)}
    {p("ระบบ failsafe 2 ชั้นทำงานอัตโนมัติโดยไม่ต้องกดปุ่ม:")}
    {h3("Core Failsafe (Go — 1 Hz)")}
    {table([
        ["ไม่ได้รับ telemetry > 3 วินาที", "RECONNECTING + alarm"],
        ["ไม่ได้รับ > 10 วินาที", "Link lost → RTL อัตโนมัติ"],
        ["Battery < 15%", "RTL อัตโนมัติ + alarm"],
        ["โดรนแตะ/ข้ามเส้น Geofence", "ดึงกลับเข้าเขต 2m"],
        ["Command ไม่มี ACK", "ถือว่าล้มเหลว + retry มีขอบเขต"],
    ], ["เหตุการณ์", "การกระทำ"])}
    {h3("FC-side Failsafe (ตั้งที่ Flight Controller — backstop)")}
    {p("เมื่อ core เองก็ดับ FC-side failsafe เป็นด่านสุดท้าย:")}
    {table([
        [f"{mono('FS_THR_ENABLE')}", "สัญญาณ RC หาย → RTL/LAND"],
        [f"{mono('FS_GCS_ENABLE=1')}", "GCS หาย → RTL"],
        [f"{mono('BATT_LOW_VOLT / BATT_CRT_VOLT')}", "แรงดันต่ำ → RTL/LAND"],
    ], ["พารามิเตอร์", "เมื่อทริกเกอร์"])}
    {alert("ต้องตั้ง FC-side failsafe เสมอ",
           "ไม่ว่า core จะทำงานดีแค่ไหน — FC failsafe เป็นด่านสุดท้ายที่ทำงานโดยไม่พึ่ง GCS "
           "ตั้งค่าผ่าน Mission Planner หรือ MAVProxy ก่อนบินจริงทุกครั้ง",
           c_red)}


    {h2("ความปลอดภัย — 6 ชั้นป้องกัน", c_red)}
    {p("ระบบใช้หลัก defense in depth — มีกำแพงหลายชั้นซ้อนกัน ถ้าชั้นหนึ่งพังก็มีชั้นถัดไปรับ")}
    {table([
        ["1. gRPC", "mTLS + session token + bind localhost + idempotency — "
         "process แปลกปลอมต่อไม่ได้แม้อยู่เครื่องเดียวกัน"],
        ["2. Safety Envelope", "ตรวจทุก command ก่อนส่ง: ความสูง, ระยะ, geofence, แบต, GPS, "
         "ระยะห่างระหว่างลำ, ความเร็ว, armed — ไม่ผ่าน = ไม่ส่งออกไปโดรน"],
        ["3. Confirm Dialog", "คำสั่งเสี่ยง (KILL, DISARM กลางอากาศ, เปลี่ยน geofence) "
         "ต้อง double-action"],
        ["4. MAVLink Signing", "HMAC-SHA256 กัน spoofing บน LAN/RF"],
        ["5. Failsafe", "Link loss, battery critical, core crash → RTL/LAND อัตโนมัติ"],
        ["6. Audit Log", "Append-only JSONL ทุกคำสั่ง + ผลลัพธ์ + เวลา — สอบสวนย้อนหลังได้"],
    ], ["ชั้น", "กลไก"])}


    {h2("การจัดการฉุกเฉิน", c_red)}
    {table([
        ["อยากให้ค้างกลางอากาศ", f"<b>{mono('HOLD')}</b> (ตรึงตำแหน่งใน GUIDED)"],
        ["หยุดขบวน/เส้นทาง", f"<b>{mono('Cancel Nav')}</b> (ตัด WP + Hold)"],
        ["หยุด Swarm", f"<b>{mono('STOP')}</b> (swarm)"],
        ["อันตรายเฉียบพลัน", f"<b style='color:{c_red}'>{mono('E-STOP')}</b> "
         "(ตัดทุกอย่าง + หยุดทันที)"],
        ["ดับมอเตอร์ทันที", f"<b style='color:{c_red}'>{mono('E-KILL')}</b> "
         "(โดรน<b>ตก</b>ทันที — ใช้เมื่อจำเป็นจริง ๆ เท่านั้น)"],
        ["สัญญาณขาด", "Core RTL อัตโนมัติ (&gt;10s) + FC failsafe เป็น backstop"],
        ["แบตวิกฤต", "Core RTL อัตโนมัติ (&lt;15%) + แถบเตือน"],
    ], ["สถานการณ์", "ทำ"])}
    {p(f"ลำดับความรุนแรง: {mono('HOLD')} → {mono('Cancel Nav')} → "
       f"<b style='color:{c_red}'>{mono('E-STOP')}</b> → "
       f"<b style='color:{c_red}'>{mono('E-KILL')}</b>")}
    {p("<b>E-STOP และ HOLD ครอบทุกโหมด</b> ไม่ว่าจะ RTL / Waypoint / Swarm กดได้หมด "
       "VIEWER บน Field Tablet ก็กด HOLD / E-STOP ได้เสมอ")}


    {h2("แก้ปัญหาเบื้องต้น (Troubleshooting)", c_amber)}
    {table([
        ["Badge ค้าง OFFLINE", "ตรวจ IP/port · FC เปิดอยู่ไหม · ลอง PING"],
        ["SAT ขึ้นน้อย / ไม่ล็อก 3D", "รอ 30–60 วิ ที่กลางแจ้ง"],
        ["ARM ถูกปฏิเสธ", "ดู toast/Mission Log — EKF/GPS/BAT ไม่ผ่าน"],
        ["กดปุ่มไม่ติด", "อาจอยู่โหมด REMOTE → สลับกลับ UI"],
        ["เปลี่ยน Head ไม่ได้", "กำลัง RTL/TAKEOFF/LANDING/Swarm → รอจนนิ่ง"],
        [f"{mono('UNIMPLEMENTED')} method", "Core binary เก่า → rebuild + restart"],
        ["Field Tablet เชื่อมไม่ได้", "ตรวจว่าเปิด Field Server แล้ว + อยู่ LAN เดียวกัน"],
        ["Servo ⌛ ค้างนาน", "FC ครั้งแรกใช้เวลา 1–25 วิ — รอ ไม่ต้องกดซ้ำ"],
    ], ["อาการ", "แก้ไข"])}
    {h3("ไฟล์ log สำคัญ")}
    {table([
        [f"{mono('backend/logs/core-YYYYMMDD.log')}", "Log หลักของ core"],
        [f"{mono('backend/logs/audit/audit-YYYYMMDD.jsonl')}", "Audit ทุกคำสั่ง"],
        [f"{mono('backend/logs/core-stdout.log')}", "stdout/stderr ดิบจาก launcher"],
    ], ["ไฟล์", "เนื้อหา"])}
    {p(f"Audit ใช้เวลา UTC · Cockpit แสดงเวลาเครื่อง (ไทย = UTC+7) "
       f"เช่น UI 15:05:42 → หาใน audit ที่ 08:05:42")}


    {h2("คู่มือฉบับเต็ม")}
    {p(f"เอกสารนี้ครอบคลุมฟีเจอร์หลัก ๆ ในระบบ สำหรับรายละเอียดเพิ่มเติมดูที่:")}
    {table([
        [f"{mono('docs/MANUAL.md')}", "คู่มือฉบับสมบูรณ์ทุกหัวข้อ"],
        [f"{mono('docs/ARCHITECTURE.md')}", "สถาปัตยกรรมระบบ 3 ชั้น"],
        [f"{mono('docs/SECURITY.md')}", "รายละเอียดความปลอดภัย 6 ชั้น"],
        [f"{mono('docs/RUNBOOK.md')}", "วิธีรัน + Pre-flight โดรนจริง"],
        [f"{mono('docs/REAL_FLIGHT_CHECKLIST.md')}", "เช็คลิสต์ก่อนบินจริง"],
        [f"{mono('docs/FIELD_TABLET_V2.md')}", "สเปกแท็บเล็ตสนามฉบับเต็ม"],
    ], ["ไฟล์", "เนื้อหา"])}

    </div>
    """


class HelpDialog(QDialog):
    """หน้าต่างคู่มือการใช้งาน (modal) — เปิดจากปุ่ม '?' บน Top bar"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("คู่มือการใช้งาน — SwarmGod Cockpit")
        self.setModal(True)
        self.resize(760, 660)
        self.setStyleSheet(f"QDialog {{ background:{T('bg')}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet(
            f"QTextBrowser {{ background:{T('panel')}; border:none; padding:20px 26px; }}"
            f"QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}"
            f"QScrollBar::handle:vertical {{ background:{rgba('#ffffff', 0.16)}; "
            f"border-radius:4px; min-height:28px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}")
        browser.setHtml(_help_html())
        v.addWidget(browser, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(16, 10, 16, 12)
        hint = QLabel("เลื่อนดูได้ทั้งหน้า · เอกสารนี้ครอบคลุมทุกฟีเจอร์ในระบบ")
        hint.setStyleSheet(f"color:{T('faint')}; font-size:10px;")
        footer.addWidget(hint)
        footer.addStretch(1)
        btn_close = QPushButton("ปิด")
        btn_close.setFixedSize(90, 32)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(filled_btn(T("accent"), radius=7, font=11))
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)

        footer_wrap = QWidget()
        footer_wrap.setStyleSheet(
            f"background:{T('panel2')}; border-top:1px solid {hairline()};")
        footer_wrap.setLayout(footer)
        v.addWidget(footer_wrap, 0)
