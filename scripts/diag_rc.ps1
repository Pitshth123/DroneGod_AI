<#
    diag_rc.ps1 — แสดงวิธีใช้โหมดตรวจสอบ RC/SERVO แล้วเปิด launcher ให้

    ทำไมแยกมาเป็น .ps1 แทนเขียน Thai text ตรงใน .bat:
    cmd.exe แปลไฟล์ .bat แบบ UTF-8 (แม้ตั้ง chcp 65001) ไม่เสถียรกับข้อความไทย
    ยาวๆ — ตัดคำมั่วกลางบรรทัดจนรันไม่ได้ (พิสูจน์แล้วว่าเกิดกับ SETUP.bat ด้วย
    ไม่ใช่แค่ไฟล์นี้) ทางที่เสถียรกว่าคือให้ PowerShell พิมพ์ข้อความแทน ตามที่
    scripts\setup.ps1 ทำอยู่แล้วสำหรับงานส่วนใหญ่ของโปรเจค
#>

$env:SWARMGOD_RC_DEBUG = "1"

Write-Host ""
Write-Host "  SwarmGod - โหมดตรวจสอบ RC / SERVO ทุกช่อง" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ใช้หาว่า `"กลไกปล่อยของ`" ผูกอยู่กับช่องไหนจริง ๆ"
Write-Host "  เพราะ cockpit ติดตามแค่ CH7 (ปุ่ม A) กับ CH8 (ปุ่ม B) เท่านั้น"
Write-Host ""
Write-Host "  วิธีใช้ — ทำตามลำดับนี้ แล้วเทียบผลกัน:" -ForegroundColor Yellow
Write-Host "    1. รอจนโดรนเชื่อมต่อเรียบร้อย"
Write-Host "    2. โยกสวิตช์ B ที่รีโมท เปิดแล้วปิด — จดว่าช่องไหนขยับ"
Write-Host "    3. กดปุ่ม B ที่ cockpit เปิดแล้วปิด — จดว่าช่องไหนขยับ"
Write-Host "    4. ถ้าสองอย่างขยับไม่เหมือนกัน = กลไกไม่ได้อยู่บนช่องที่ UI สั่ง"
Write-Host ""
Write-Host "  ผลอยู่ในไฟล์ log ของ core บรรทัดที่ขึ้นต้นด้วย [rcdbg]:"
Write-Host "    backend\logs\core-YYYYMMDD.log" -ForegroundColor Gray
Write-Host ""
Read-Host "กด Enter เพื่อเปิด launcher"

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & ".\START_SWARMGOD.bat"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "  เปิด launcher แล้ว — ทำตามขั้นตอนด้านบน" -ForegroundColor Green
Write-Host "  เสร็จแล้วเปิดไฟล์ backend\logs\core-*.log ดูบรรทัด [rcdbg]"
Write-Host ""
Read-Host "กด Enter เพื่อปิดหน้าต่างนี้"
