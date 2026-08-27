@echo off
chcp 65001 >nul 2>&1
title SwarmGod - Setup
cd /d "%~dp0"

echo.
echo   SwarmGod - ตรวจสอบและติดตั้งระบบ
echo.
echo   [1] ตรวจอย่างเดียว (ไม่ติดตั้งอะไร)
echo   [2] ตรวจ + ติดตั้งสิ่งที่ขาด   ^<-- แนะนำ
echo   [3] ตรวจ + ติดตั้ง + protoc plugins (เฉพาะคนที่จะแก้ไฟล์ .proto)
echo.
set /p MODE="  เลือก [1/2/3] (ค่าเริ่มต้น 2): "
if "%MODE%"=="" set MODE=2

if "%MODE%"=="1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" -CheckOnly
) else if "%MODE%"=="3" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" -Proto
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
)

echo.
pause
