#!/usr/bin/env bash
# launch 1 SITL copter ผ่าน sim_vehicle.py (จัดการ lockstep ถูกต้อง)
# ไม่ใช้ mavproxy (--no-mavproxy) → SITL เปิด tcp:5760 ให้ต่อตรง
set -e
pkill -9 -x arducopter 2>/dev/null || true
pkill -9 -f mavproxy 2>/dev/null || true
sleep 1
cd ~/ardupilot
exec Tools/autotest/sim_vehicle.py -v ArduCopter -I0 --no-rebuild --no-mavproxy \
  --custom-location=14.9581695,102.0986187,0,0
