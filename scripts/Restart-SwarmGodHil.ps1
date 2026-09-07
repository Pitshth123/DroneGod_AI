$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localData = Join-Path $env:LOCALAPPDATA 'SwarmGod\data'
$localDatabase = Join-Path $localData 'swarmgod.db'
$homeCache = Join-Path $localData 'hil-home.txt'
$legacyHomeCache = Join-Path $root 'backend\logs\hil-home.txt'
New-Item -ItemType Directory -Path $localData -Force | Out-Null

# A setup/HIL Core accepts a missing token, while presenting a token from a
# different per-machine DB is rejected. Capture HOME before issuing the new
# machine-local token.
$previousToken = $env:SWARMGOD_TOKEN
Remove-Item Env:SWARMGOD_TOKEN -ErrorAction SilentlyContinue
$ErrorActionPreference = 'Continue'
$hilHome = & python (Join-Path $PSScriptRoot 'capture_hil_home.py')
$captureExitCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($captureExitCode -eq 0 -and $hilHome -match '^-?\d+\.\d+,-?\d+\.\d+,0,0$') {
    $hilHome | Set-Content -LiteralPath $homeCache -NoNewline
} elseif ((Test-Path -LiteralPath $homeCache) -and
          ((Get-Date) - (Get-Item -LiteralPath $homeCache).LastWriteTime).TotalMinutes -le 30) {
    $hilHome = (Get-Content -LiteralPath $homeCache -Raw).Trim()
    Write-Host "Using recently verified HIL HOME: $hilHome"
} elseif ((Test-Path -LiteralPath $legacyHomeCache) -and
          ((Get-Date) - (Get-Item -LiteralPath $legacyHomeCache).LastWriteTime).TotalMinutes -le 30) {
    $hilHome = (Get-Content -LiteralPath $legacyHomeCache -Raw).Trim()
    $hilHome | Set-Content -LiteralPath $homeCache -NoNewline
    Write-Host "Migrated recently verified HIL HOME: $hilHome"
} else {
    $env:SWARMGOD_TOKEN = $previousToken
    throw 'Cannot establish HOME from current verified telemetry. Keep the setup Core connected with the aircraft disarmed, then retry.'
}

$env:SWARMGOD_DB = $localDatabase
& (Join-Path $PSScriptRoot 'Set-SwarmGodToken.ps1') -Database $localDatabase
if (-not $env:SWARMGOD_TOKEN) {
    throw 'Token creation failed.'
}

$env:SWARMGOD_PROFILE = 'hil'
$env:SWARMGOD_HOME_LOC = $hilHome
$env:SWARMGOD_LAUNCHER_AUTOSTART = '1'
$env:SWARMGOD_LAUNCHER_AUTO = $null

# The new launcher owns the replacement Core. Stop only the existing SwarmGod
# launcher/Core processes after HOME has been captured from their live telemetry.
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and
        $_.CommandLine -match '(?i)-m\s+swarmgod_gui(?:\.launcher)?(?:\s|$)'
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process 'swarmgod-core' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Write-Host "Starting authenticated HIL with HOME $hilHome"
& (Join-Path $root 'START_SWARMGOD.bat')
