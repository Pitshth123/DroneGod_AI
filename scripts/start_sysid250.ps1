<#
    start_sysid250.ps1 — เปิด SwarmGod โดยตั้ง MAVLink System ID = 250

    ทำไม: ArduPilot รับ RC_CHANNELS_OVERRIDE เฉพาะจาก GCS ที่ sysid ตรงกับ
    พารามิเตอร์ SYSID_MYGCS ที่ตั้งไว้ใน FC — ถ้าไม่ตรง คำสั่งถูกทิ้งเงียบ ๆ
    ไม่มี error กลับมาเลย

    เครื่องนี้: SYSID_MYGCS = 250 (ตั้งจากรีโมท G12 / แอป MX330)
    แต่ค่าเริ่มต้นของ core คือ 255 (มาตรฐาน GCS ทั่วไป) จึงต้องตั้งให้ตรง

    ถ้าเปลี่ยน SYSID_MYGCS ที่ FC เมื่อไหร่ ต้องแก้เลขในไฟล์นี้ให้ตรงกันด้วย
#>

$env:SWARMGOD_MAVLINK_SYSID = "250"

Write-Host ""
Write-Host "  SwarmGod - MAVLink System ID = 250" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ตั้งให้ตรงกับ SYSID_MYGCS ที่ FC (ค่าเริ่มต้นของ core คือ 255)"
Write-Host "  ถ้าไม่ตรง ArduPilot จะทิ้งคำสั่ง RC override เงียบ ๆ"
Write-Host ""

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & ".\START_SWARMGOD.bat"
} finally {
    Pop-Location
}
