#!/usr/bin/env bash
# เช็ค dependency สำหรับ sim_vehicle.py ใน WSL
pkill -9 -x arducopter 2>/dev/null
echo -n "python3: "; python3 --version
# หมายเหตุ: empy import ด้วยชื่อ `em` (เช็คด้วย `import empy` จะ MISSING เสมอ)
for m in pymavlink pexpect em future; do
  if python3 -c "import $m" 2>/dev/null; then echo "  $m OK"; else echo "  $m MISSING"; fi
done
if command -v mavproxy.py >/dev/null 2>&1; then echo "mavproxy OK"; else echo "mavproxy MISSING"; fi
echo -n "sim_vehicle: "; ls ~/ardupilot/Tools/autotest/sim_vehicle.py >/dev/null 2>&1 && echo OK || echo MISSING
