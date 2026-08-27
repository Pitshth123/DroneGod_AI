from PIL import Image, ImageDraw, ImageFont
src = r'C:\Users\PC\Desktop\v2 swam\DroneGod\docs\cockpit_actual_windows.png'
out = r'C:\Users\PC\Desktop\v2 swam\DroneGod\docs\COCKPIT_REAL_SCREEN_GUIDE.png'
img = Image.open(src).convert('RGB')
W,H = img.size
canvas = Image.new('RGB',(W+620,H),(245,247,250))
canvas.paste(img,(0,0))
d = ImageDraw.Draw(canvas)
try:
    f_title = ImageFont.truetype(r'C:\Windows\Fonts\tahomabd.ttf',26)
    f_head = ImageFont.truetype(r'C:\Windows\Fonts\tahomabd.ttf',19)
    f_body = ImageFont.truetype(r'C:\Windows\Fonts\tahoma.ttf',17)
    f_num = ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf',18)
except Exception:
    f_title=f_head=f_body=f_num=ImageFont.load_default()
items=[
(1,(86,30),'TOP BAR','สถานะ CORE / LINK / PREFLIGHT และโหมดควบคุมหลัก'),
(2,(195,170),'FLEET','รายการโดรนและสถานะ ONLINE / READY ของแต่ละลำ'),
(3,(194,205),'GROUPS','ชิปกลุ่ม ทั้งหมด / 1–6 ใช้เลือกหลายลำเป็นชุด'),
(4,(795,310),'MAP','แผนที่หลัก: ตำแหน่งโดรน, GOTO, Geofence และ Waypoint'),
(5,(784,505),'MAP STATUS','LAT/LNG, Cursor, โหมดแผนที่ และข้อมูลตำแหน่ง'),
(6,(665,700),'MISSION LOG','บันทึก Commands / Alerts / Telemetry และผลคำสั่งย้อนหลัง'),
(7,(1050,700),'PRE-FLIGHT SUMMARY','สรุปลำที่เลือก แผน Takeoff, Waypoint/WAVE และคำสั่งล่าสุด'),
(8,(1375,145),'PRE-FLIGHT TEST','SYSTEM TEST + CHECKLIST ก่อน Takeoff จริง'),
(9,(1385,300),'FLIGHT COMMANDS','ARM / TAKEOFF / HOLD / RTL / LAND ตามลำที่เลือก'),
(10,(1385,455),'SWARM / WAYPOINT','จัด Formation, Start/Stop Swarm และ Execute Route/WAVE'),
(11,(1385,585),'SAFETY','Geofence / Safety controls และคำสั่งหยุดภารกิจ'),
(12,(1380,760),'E-STOP','หยุดฉุกเฉิน — จุดที่ต้องมองเห็นและเข้าถึงได้เร็ว'),
]
# translucent left callouts
for n,(x,y),head,desc in items:
    r=17
    d.line((x+r,y,W+28,60+n*66), fill=(36,99,235), width=3)
    d.ellipse((x-r,y-r,x+r,y+r), fill=(36,99,235), outline=(255,255,255), width=2)
    s=str(n); bb=d.textbbox((0,0),s,font=f_num); d.text((x-(bb[2]-bb[0])/2,y-(bb[3]-bb[1])/2-1),s,font=f_num,fill='white')
# right legend
x0=W+30
d.rounded_rectangle((W+15,15,W+600,H-15),radius=18,fill=(255,255,255),outline=(210,218,230),width=2)
d.text((x0,28),'คู่มือหน้าจอจริง — SwarmGod Cockpit',font=f_title,fill=(18,27,42))
d.text((x0,66),'หมายเลขชี้จากหน้าจอโปรแกรมจริง',font=f_body,fill=(90,100,115))
y=105
for n,pt,head,desc in items:
    d.ellipse((x0,y,x0+30,y+30),fill=(36,99,235))
    s=str(n); bb=d.textbbox((0,0),s,font=f_num); d.text((x0+15-(bb[2]-bb[0])/2,y+15-(bb[3]-bb[1])/2-1),s,font=f_num,fill='white')
    d.text((x0+42,y-2),head,font=f_head,fill=(18,27,42))
    # simple wrapping
    words=desc.split(); lines=[]; line=''
    for w in words:
        t=(line+' '+w).strip()
        if d.textlength(t,font=f_body)>500:
            lines.append(line); line=w
        else: line=t
    if line: lines.append(line)
    yy=y+28
    for ln in lines[:2]:
        d.text((x0+42,yy),ln,font=f_body,fill=(70,80,95)); yy+=24
    y += 66
canvas.save(out,quality=95)
print(out)