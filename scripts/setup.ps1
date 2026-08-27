<#
    setup.ps1 — ตรวจ + ติดตั้งทุกอย่างที่ SwarmGod ต้องใช้

    เรียกผ่าน SETUP.bat (ดับเบิลคลิก) หรือรันตรง:
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -CheckOnly
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -WithSitl

    หลักการ:
      · idempotent — รันซ้ำกี่รอบก็ได้ ข้ามของที่มีอยู่แล้ว
      · ไม่ลงของหนักเอง (WSL/ArduPilot ~1.5GB + build 20 นาที) ต้องใส่ -WithSitl
      · ไม่แตะข้อมูลผู้ใช้ใน ~/.swarmgod เลย
#>
param(
    [switch]$CheckOnly,   # เช็คอย่างเดียว ไม่ติดตั้งอะไร
    [switch]$WithSitl,    # ติดตั้ง WSL + ArduPilot ด้วย (ใช้เวลานาน)
    [switch]$Proto        # ติดตั้ง protoc plugins (เฉพาะถ้าจะแก้ไฟล์ .proto)
)

$ErrorActionPreference = 'Continue'
$ROOT     = Split-Path -Parent $PSScriptRoot
$BACKEND  = Join-Path $ROOT 'backend'
$FRONTEND = Join-Path $ROOT 'frontend'
$CERTS    = Join-Path $ROOT 'certs'

$script:Fail = 0
$script:Warn = 0
$script:Todo = @()

function Head($t) {
    Write-Host ""
    Write-Host ("-" * 64) -ForegroundColor DarkGray
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ("-" * 64) -ForegroundColor DarkGray
}
function Ok($t)   { Write-Host "  [ok]   $t" -ForegroundColor Green }
function Info($t) { Write-Host "  [ .. ] $t" -ForegroundColor Gray }
function Warn($t) { Write-Host "  [warn] $t" -ForegroundColor Yellow; $script:Warn++ }
function Bad($t)  { Write-Host "  [FAIL] $t" -ForegroundColor Red;   $script:Fail++ }
function Todo($t) { $script:Todo += $t }

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host ""
Write-Host "  ██ SwarmGod — ตรวจสอบและติดตั้งระบบ" -ForegroundColor White
Write-Host "  โปรเจค: $ROOT" -ForegroundColor DarkGray
if ($CheckOnly) { Write-Host "  โหมด: ตรวจอย่างเดียว (ไม่ติดตั้งอะไร)" -ForegroundColor Yellow }

# ══════════════════════════════════════════════════════════
Head "1) เครื่องมือพื้นฐาน"

# ── Go ──
if (Have 'go') {
    Ok "Go — $((go version) -replace '^go version ','')"
} else {
    Bad "ไม่พบ Go"
    Todo "ติดตั้ง Go: winget install GoLang.Go  (แล้วเปิด terminal ใหม่)"
    if (-not $CheckOnly) {
        if (Have 'winget') {
            Info "กำลังติดตั้ง Go ผ่าน winget…"
            winget install --id GoLang.Go --silent --accept-package-agreements --accept-source-agreements
            if (Have 'go') { Ok "ติดตั้ง Go สำเร็จ" }
            else { Warn "ติดตั้งแล้วแต่ยังเรียก go ไม่ได้ — ต้องเปิด terminal ใหม่ให้ PATH อัปเดต" }
        } else {
            Warn "ไม่มี winget — ติดตั้ง Go เองจาก https://go.dev/dl/"
        }
    }
}

# ── Python ──
$py = $null
foreach ($c in @('python','py')) {
    if (Have $c) {
        $v = & $c --version 2>&1
        if ($v -match 'Python 3\.(\d+)') {
            if ([int]$Matches[1] -ge 10) { $py = $c; Ok "Python — $v (ใช้คำสั่ง '$c')"; break }
            else { Warn "พบ $v — ต้องการ 3.10 ขึ้นไป" }
        }
    }
}
if (-not $py) {
    Bad "ไม่พบ Python 3.10+"
    Todo "ติดตั้ง Python 3.12 จาก python.org (ติ๊ก 'Add python.exe to PATH')"
    if (-not $CheckOnly -and (Have 'winget')) {
        Info "กำลังติดตั้ง Python ผ่าน winget…"
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        Warn "ติดตั้งแล้ว — ต้องเปิด terminal ใหม่ แล้วรัน setup อีกครั้ง"
    }
}

# ══════════════════════════════════════════════════════════
Head "2) Python packages (cockpit)"

if ($py) {
    $req = Join-Path $FRONTEND 'requirements.txt'
    if (-not (Test-Path $req)) {
        Bad "ไม่พบ $req"
    } else {
        # เช็คแพ็กเกจหลักด้วยการ import จริง (เร็วกว่า pip list)
        $probe = @'
import importlib, sys
mods = {"PyQt5":"PyQt5","PyQt5.QtWebEngineWidgets":"PyQtWebEngine","grpc":"grpcio",
        "grpc_tools":"grpcio-tools","google.protobuf":"protobuf","cv2":"opencv-python",
        "numpy":"numpy","matplotlib":"matplotlib"}
missing=[]
for m,p in mods.items():
    try: importlib.import_module(m)
    except Exception: missing.append(p)
print(",".join(missing))
'@
        $missing = (& $py -c $probe 2>$null)
        if ([string]::IsNullOrWhiteSpace($missing)) {
            Ok "Python packages ครบแล้ว"
        } else {
            Warn "ขาด: $missing"
            Todo "ติดตั้ง deps: cd frontend; pip install -r requirements.txt"
            if (-not $CheckOnly) {
                Info "กำลังติดตั้ง Python deps… (อาจใช้เวลาสักครู่)"
                Push-Location $FRONTEND
                & $py -m pip install --disable-pip-version-check -r requirements.txt
                Pop-Location
                $again = (& $py -c $probe 2>$null)
                if ([string]::IsNullOrWhiteSpace($again)) { Ok "ติดตั้ง Python deps ครบแล้ว" }
                else { Bad "ยังขาด: $again" }
            }
        }
    }
} else {
    Warn "ข้ามการเช็ค Python packages (ยังไม่มี Python)"
}

# ══════════════════════════════════════════════════════════
Head "3) Certificates (mTLS + MAVLink signing)"

$needCerts = @('ca.crt','server.crt','server.key','client.crt','client.key') |
             Where-Object { -not (Test-Path (Join-Path $CERTS $_)) }
if ($needCerts.Count -eq 0) {
    Ok "certs ครบแล้ว ($CERTS)"
} else {
    Warn "certs ยังไม่ครบ (ขาด: $($needCerts -join ', '))"
    Todo "สร้าง certs: cd backend; go run ./cmd/gencerts -out ../certs"
    if (-not $CheckOnly -and (Have 'go')) {
        Info "กำลังสร้าง certs…"
        Push-Location $BACKEND
        go run ./cmd/gencerts -out ../certs
        Pop-Location
        if ((Test-Path (Join-Path $CERTS 'server.crt'))) { Ok "สร้าง certs สำเร็จ" }
        else { Bad "สร้าง certs ไม่สำเร็จ" }
    }
}

# ══════════════════════════════════════════════════════════
Head "4) Build Go core"

if (Have 'go') {
    $exe = Join-Path $BACKEND 'bin\swarmgod-core.exe'
    if (-not $CheckOnly) {
        Info "กำลัง build core…"
        Push-Location $BACKEND
        go build -o bin/swarmgod-core.exe ./cmd/swarmgod-core
        $rc = $LASTEXITCODE
        Pop-Location
        if ($rc -eq 0 -and (Test-Path $exe)) {
            $t = (Get-Item $exe).LastWriteTime.ToString('yyyy-MM-dd HH:mm')
            Ok "build สำเร็จ — bin\swarmgod-core.exe ($t)"
        } else { Bad "build ไม่ผ่าน (rc=$rc)" }
    } elseif (Test-Path $exe) {
        $t = (Get-Item $exe).LastWriteTime.ToString('yyyy-MM-dd HH:mm')
        Ok "มี binary อยู่แล้ว ($t)"
        Warn "อย่าลืม rebuild ทุกครั้งที่แก้โค้ด Go ไม่งั้นจะเจอ UNIMPLEMENTED"
    } else {
        Warn "ยังไม่ได้ build core"
        Todo "build core: cd backend; go build -o bin/swarmgod-core.exe ./cmd/swarmgod-core"
    }
} else {
    Warn "ข้ามการ build (ยังไม่มี Go)"
}

# ══════════════════════════════════════════════════════════
Head "5) protoc plugins (เฉพาะถ้าจะแก้ไฟล์ .proto)"

$goBin = Join-Path $env:USERPROFILE 'go\bin'
$hasP1 = Test-Path (Join-Path $goBin 'protoc-gen-go.exe')
$hasP2 = Test-Path (Join-Path $goBin 'protoc-gen-go-grpc.exe')
if ($hasP1 -and $hasP2) {
    Ok "protoc plugins พร้อม (protoc-gen-go + protoc-gen-go-grpc)"
} elseif ($Proto -and -not $CheckOnly -and (Have 'go')) {
    Info "กำลังติดตั้ง protoc plugins…"
    go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
    go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
    if ((Test-Path (Join-Path $goBin 'protoc-gen-go.exe'))) { Ok "ติดตั้ง protoc plugins สำเร็จ" }
    else { Warn "ติดตั้งไม่สำเร็จ" }
} else {
    Info "ยังไม่มี protoc plugins — ไม่จำเป็นถ้าไม่แก้ .proto (stubs อยู่ใน git แล้ว)"
    Info "ถ้าต้องการ: รัน setup ใหม่ด้วย -Proto"
}

# ══════════════════════════════════════════════════════════
Head "6) WSL + ArduPilot SITL (ใช้เฉพาะโหมดจำลอง)"

if (-not (Have 'wsl')) {
    Warn "ไม่พบ WSL — ใช้ SITL ไม่ได้ (ต่อโดรนจริงยังใช้ได้ปกติ)"
    Todo "ติดตั้ง WSL: wsl --install -d Ubuntu   (ต้องรีสตาร์ตเครื่อง)"
} else {
    $distros = (wsl -l -q 2>$null) -replace "`0","" | Where-Object { $_.Trim() }
    if (-not $distros) {
        Warn "มี WSL แต่ยังไม่มี distro"
        Todo "ติดตั้ง Ubuntu: wsl --install -d Ubuntu"
    } else {
        Ok "WSL distro: $($distros -join ', ')"
        $envDistro = $env:SWARMGOD_WSL_DISTRO
        if (-not $envDistro) { $envDistro = 'Ubuntu' }
        if ($distros -notcontains $envDistro) {
            Warn "launcher จะใช้ distro ชื่อ '$envDistro' แต่เครื่องนี้มี: $($distros -join ', ')"
            Todo "ตั้ง env ใน START_SWARMGOD.bat:  set SWARMGOD_WSL_DISTRO=$($distros[0])"
        }
        # ArduPilot อยู่ไหม
        $apDir = $env:SWARMGOD_AP_DIR; if (-not $apDir) { $apDir = '~/ardupilot' }
        $apCheck = wsl -d $envDistro -e bash -lc "test -x $apDir/build/sitl/bin/arducopter && echo YES || echo NO" 2>$null
        if ($apCheck -match 'YES') {
            Ok "ArduPilot SITL พร้อมใช้งาน ($apDir)"
        } else {
            Warn "ยังไม่มี ArduPilot SITL ที่ build แล้วใน WSL"
            Todo "รัน BUILD_SITL.bat (ครั้งเดียว ~15-30 นาที, ถามรหัส sudo ของ Linux 1 ครั้ง)"
            if ($WithSitl -and -not $CheckOnly) {
                Warn "การ build ArduPilot ใช้เวลานาน (~15-30 นาที) และโหลดของเพิ่มหลายร้อย MB"
                Info "สคริปต์นี้ไม่ทำให้อัตโนมัติ — ใช้ BUILD_SITL.bat แทน (ต้องใส่รหัส sudo เอง)"
            }
        }
    }
}

# ══════════════════════════════════════════════════════════
Head "7) ตรวจว่า cockpit import ได้จริง"

if ($py) {
    Push-Location $FRONTEND
    $env:QT_QPA_PLATFORM = 'offscreen'; $env:SWARMGOD_NO_MAP = '1'
    $out = & $py -c "import swarmgod_gui.app; print('OK')" 2>&1
    Pop-Location
    Remove-Item Env:\QT_QPA_PLATFORM, Env:\SWARMGOD_NO_MAP -ErrorAction SilentlyContinue
    if ($out -match 'OK') { Ok "cockpit import ผ่าน" }
    else {
        Bad "cockpit import ไม่ผ่าน"
        Write-Host "        $($out | Select-Object -Last 3)" -ForegroundColor DarkRed
    }
} else {
    Warn "ข้าม (ยังไม่มี Python)"
}

# ══════════════════════════════════════════════════════════
Head "สรุป"

if ($script:Fail -eq 0 -and $script:Warn -eq 0) {
    Write-Host "  ✓ พร้อมใช้งานครบทุกอย่าง" -ForegroundColor Green
} elseif ($script:Fail -eq 0) {
    Write-Host "  ✓ ใช้งานได้ (มีคำเตือน $($script:Warn) ข้อ)" -ForegroundColor Yellow
} else {
    Write-Host "  ✗ ยังไม่พร้อม — ผิดพลาด $($script:Fail) ข้อ, เตือน $($script:Warn) ข้อ" -ForegroundColor Red
}

if ($script:Todo.Count -gt 0) {
    Write-Host ""
    Write-Host "  สิ่งที่ต้องทำต่อ:" -ForegroundColor White
    foreach ($t in $script:Todo) { Write-Host "    • $t" -ForegroundColor Yellow }
}

Write-Host ""
if ($script:Fail -eq 0) {
    Write-Host "  ต่อไป: ดับเบิลคลิก START_SWARMGOD.bat เพื่อเปิดใช้งาน" -ForegroundColor Cyan
}
Write-Host ""
exit $script:Fail
