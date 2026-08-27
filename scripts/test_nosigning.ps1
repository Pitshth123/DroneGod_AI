<#
    test_nosigning.ps1 — ทดสอบว่าดีเลย์คำสั่งแรกเกิดจาก MAVLink signing หรือไม่

    เปิด SwarmGod โดย **ไม่เซ็น** MAVLink frame แล้วดูผล 2 แบบ:
      ดีเลย์หายไป      → signing timestamp คือต้นเหตุ (FC ไม่ได้บังคับ signing)
      สั่งอะไรไม่ได้เลย → FC บังคับ signing อยู่ ต้องเปิดไว้ ดีเลย์มาจากเรื่องอื่น

    เป็นโหมดทดสอบชั่วคราว ปิดหน้าต่างแล้วกลับไปใช้ START_SWARMGOD.bat ตามปกติ
#>

$env:SWARMGOD_MAVLINK_SIGNING = "off"
$env:SWARMGOD_RC_DEBUG = "1"

Write-Host ""
Write-Host "  SwarmGod - ทดสอบปิด MAVLink signing (ชั่วคราว)" -ForegroundColor Cyan
Write-Host ""
Write-Host "  โหมดนี้ไม่เซ็นเฟรมที่ส่งออก เพื่อพิสูจน์ว่าดีเลย์มาจาก signing หรือไม่"
Write-Host ""
Write-Host "  วิธีดูผล:" -ForegroundColor Yellow
Write-Host "    1. เชื่อมต่อโดรน แล้วกดปุ่ม B ทันที (ไม่ต้องรอ)"
Write-Host "    2. ดูผลที่ backend\logs\core-*.log"
Write-Host ""
Write-Host "    กดแล้วทำงานทันที     = signing timestamp คือต้นเหตุ" -ForegroundColor Green
Write-Host "    กดแล้วไม่ทำงานเลย     = FC บังคับ signing ต้องเปิดไว้" -ForegroundColor Red
Write-Host ""
Write-Host "  ระวัง: ลิงก์ MAVLink จะไม่มีลายเซ็นระหว่างทดสอบ ใช้เฉพาะตอนทดสอบ" -ForegroundColor DarkYellow
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
Write-Host "  เปิด launcher แล้ว — ทดสอบตามขั้นตอนด้านบน" -ForegroundColor Green
Write-Host ""
Read-Host "กด Enter เพื่อปิดหน้าต่างนี้"
