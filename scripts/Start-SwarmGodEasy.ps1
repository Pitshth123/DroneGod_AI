param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$setupScript = Join-Path $PSScriptRoot 'setup.ps1'
$tokenScript = Join-Path $PSScriptRoot 'Set-SwarmGodToken.ps1'
$startBat = Join-Path $root 'START_SWARMGOD.bat'
$localData = Join-Path $env:LOCALAPPDATA 'SwarmGod\data'
$localDatabase = Join-Path $localData 'swarmgod.db'

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath"
}

function Get-PythonCommand {
    foreach ($name in @('python', 'py')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    return $null
}

function Test-PythonPackages([string]$PythonCommand) {
    if (-not $PythonCommand) { return $false }
    & $PythonCommand -c 'import PyQt5, PyQt5.QtWebEngineWidgets, grpc, google.protobuf, cv2, numpy, matplotlib' 2>$null
    return $LASTEXITCODE -eq 0
}

Update-ProcessPath
$goCommand = Get-Command go -ErrorAction SilentlyContinue
$pythonCommand = Get-PythonCommand
$packagesReady = Test-PythonPackages $pythonCommand

if (-not $goCommand -or -not $pythonCommand -or -not $packagesReady) {
    Write-Host 'First-time setup: installing or repairing SwarmGod prerequisites.'
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoLogo -NoProfile -ExecutionPolicy Bypass -File $setupScript
    Update-ProcessPath
    $goCommand = Get-Command go -ErrorAction SilentlyContinue
    $pythonCommand = Get-PythonCommand
    $packagesReady = Test-PythonPackages $pythonCommand
}

if (-not $goCommand) { throw 'Go is not ready. Run SETUP.bat once, then retry.' }
if (-not $pythonCommand) { throw 'Python 3 is not ready. Run SETUP.bat once, then retry.' }
if (-not $packagesReady) { throw 'Python packages are not ready. Run SETUP.bat once, then retry.' }

$requiredCerts = @(
    'ca.crt', 'server.crt', 'server.key', 'client.crt', 'client.key', 'mavlink_key'
)
$missingCerts = @($requiredCerts | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $root "certs\$_"))
})
if ($missingCerts.Count -gt 0) {
    throw "Certificates are missing: $($missingCerts -join ', '). Copy the complete certs folder from the authorized computer or run SETUP.bat for SITL."
}

if ($CheckOnly) {
    Write-Host 'SwarmGod easy-start prerequisites are ready.'
    exit 0
}

New-Item -ItemType Directory -Path $localData -Force | Out-Null
$env:SWARMGOD_DB = $localDatabase
& $tokenScript -Database $localDatabase
if (-not $env:SWARMGOD_TOKEN) { throw 'Automatic session token creation failed.' }

# The local passcode dialog only prevents casual clicks. The Core still checks
# the generated bearer token and mTLS for command authorization.
$env:SWARMGOD_AUTHED = '1'
$env:SWARMGOD_LAUNCHER_AUTO = $null
$env:SWARMGOD_LAUNCHER_AUTOSTART = $null

Write-Host 'Starting SwarmGod with automatic local authentication.'
& $startBat
