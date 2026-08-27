@echo off
chcp 65001 >nul 2>&1
title SwarmGod - Build ArduPilot SITL (WSL)
cd /d "%~dp0"

if "%SWARMGOD_WSL_DISTRO%"=="" set SWARMGOD_WSL_DISTRO=Ubuntu

echo.
echo   ═══════════════════════════════════════════════════════════
echo    ติดตั้ง + build ArduCopter SITL ใน WSL (%SWARMGOD_WSL_DISTRO%)
echo   ═══════════════════════════════════════════════════════════
echo.
echo    ทำครั้งเดียว จำเป็นเฉพาะโหมด SITL (โดรนจำลอง)
echo    ใช้เวลาประมาณ 15-30 นาที
echo    ระหว่างทางจะถามรหัสผ่าน sudo ของ Linux 1 ครั้ง
echo.
pause

wsl.exe -d %SWARMGOD_WSL_DISTRO% -e bash -lc "tr -d '\r' < scripts/wsl_build_sitl.sh > /tmp/swarmgod_build_sitl.sh && bash /tmp/swarmgod_build_sitl.sh"

echo.
if errorlevel 1 (
  echo   ✗ ยังไม่สำเร็จ — อ่านข้อความด้านบนเพื่อดูว่าติดตรงไหน
) else (
  echo   ✓ เสร็จแล้ว — เปิด START_SWARMGOD.bat แล้วกด START ได้เลย
)
echo.
pause
