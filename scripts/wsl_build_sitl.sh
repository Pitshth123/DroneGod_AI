#!/usr/bin/env bash
# wsl_build_sitl.sh — ติดตั้ง prerequisites + build ArduCopter SITL ใน WSL
#
# รันจาก Windows:  BUILD_SITL.bat   (หรือใน WSL:  bash scripts/wsl_build_sitl.sh)
#
# ทำอะไรบ้าง
#   1) ติดตั้งคอมไพเลอร์ + python deps เท่าที่ SITL ต้องใช้ (apt, ต้องใส่รหัส sudo 1 ครั้ง)
#   2) clone ArduPilot ถ้ายังไม่มี / sync submodules
#   3) ./waf configure --board sitl && ./waf copter
#   4) ตรวจว่าได้ไฟล์ build/sitl/bin/arducopter จริง
#
# ตั้งใจไม่ใช้ install-prereqs-ubuntu.sh ของ ArduPilot เพราะ:
#   - มันลง MAVProxy/wxPython/OpenCV ซึ่ง launcher ไม่ได้ใช้ (--no-mavproxy) เสียเวลามาก
#   - บน Ubuntu 24.04 มันสร้าง venv แล้วเขียนลง ~/.profile ซึ่ง launcher (bash -lc) อาจไม่ได้โหลด
#   สคริปต์นี้ลง python deps จาก apt ตรงๆ → ใช้ได้กับทุก shell ไม่ต้อง activate อะไร
set -euo pipefail

AP_DIR="${SWARMGOD_AP_DIR_LINUX:-$HOME/ardupilot}"
AP_REPO="${SWARMGOD_AP_REPO:-https://github.com/ArduPilot/ardupilot.git}"
JOBS="${SWARMGOD_BUILD_JOBS:-$(nproc)}"

say()  { echo -e "\n\033[1;36m▌ $*\033[0m"; }
ok()   { echo -e "  \033[1;32m✓\033[0m $*"; }
bad()  { echo -e "  \033[1;31m✗\033[0m $*"; }

PKGS="build-essential ccache g++ gawk make wget pkg-config git rsync
      python3-dev python3-setuptools python3-numpy
      python3-pexpect python3-empy python3-future python3-lxml python3-serial"

say "1/5 ติดตั้ง prerequisites (apt) — ขอรหัสผ่าน sudo"
if ! sudo -v; then
    bad "sudo ไม่ผ่าน — ต้องใช้สิทธิ์ root ในการติดตั้ง"
    exit 1
fi
sudo apt-get update -qq
# shellcheck disable=SC2086
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $PKGS
ok "prerequisites ครบ"

say "2/5 ตรวจ ArduPilot source ที่ $AP_DIR"
if [ ! -d "$AP_DIR/.git" ]; then
    echo "  ยังไม่มี — clone ใหม่ (~1.5 GB ใช้เวลาสักครู่)"
    git clone --recurse-submodules "$AP_REPO" "$AP_DIR"
fi
cd "$AP_DIR"
git submodule update --init --recursive
ok "source พร้อม ($(git log --oneline -1))"

say "3/5 ตรวจ python module ที่ build ต้องใช้"
# ระวัง: empy import ด้วยชื่อ `em` (ไฟล์ /usr/lib/python3/dist-packages/em.py)
MISSING=""
for entry in "em:empy" "pexpect:pexpect" "future:future" "lxml:lxml"; do
    mod="${entry%%:*}"; pkg="${entry##*:}"
    if python3 -c "import $mod" 2>/dev/null; then ok "$pkg"; else bad "$pkg"; MISSING="$MISSING $pkg"; fi
done
if [ -n "$MISSING" ]; then
    bad "ยังขาด:$MISSING — apt ลงไม่สำเร็จ หยุดก่อน"
    exit 1
fi

say "4/5 build ArduCopter SITL (-j$JOBS) — ประมาณ 10-25 นาที"
./waf configure --board sitl
./waf copter -j"$JOBS"

say "5/5 ตรวจผล"
BIN="$AP_DIR/build/sitl/bin/arducopter"
if [ -x "$BIN" ]; then
    ok "สร้างสำเร็จ: $BIN"
    echo
    echo -e "\033[1;32m  พร้อมใช้งานแล้ว — กลับไปกด START ที่ launcher ได้เลย\033[0m"
else
    bad "ไม่พบ $BIN — build ไม่สำเร็จ"
    exit 1
fi
