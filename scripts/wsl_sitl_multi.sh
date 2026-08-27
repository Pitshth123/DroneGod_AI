#!/usr/bin/env bash
# launch 3 SITL copters (sysid 1-3) ผ่าน sim_vehicle.py สำหรับทดสอบ swarm
# ports: TCP 5760 (UAV_1), 5770 (UAV_2), 5780 (UAV_3)
set -e
pkill -9 -x arducopter 2>/dev/null || true
pkill -9 -f sim_vehicle.py 2>/dev/null || true
sleep 2
cd ~/ardupilot
exec Tools/autotest/sim_vehicle.py -v ArduCopter -I0 --count 3 --auto-sysid \
  --no-rebuild --no-mavproxy \
  --custom-location=14.9581695,102.0986187,0,0 --auto-offset-line 90,15
