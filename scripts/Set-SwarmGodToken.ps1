param(
    [string]$Database = (Join-Path $PSScriptRoot '..\backend\logs\swarmgod.db')
)

$ErrorActionPreference = 'Stop'
$Database = [IO.Path]::GetFullPath($Database)
if (Test-Path -LiteralPath $Database) {
    $Database = (Get-Item -LiteralPath $Database).FullName
}
$backend = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\backend'))
$hash = [Security.Cryptography.SHA256]::Create()
try {
    $key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Database.ToLowerInvariant()))).Replace('-', '')
} finally { $hash.Dispose() }
$secretDir = Join-Path $env:LOCALAPPDATA "SwarmGod\credentials\$key"
$passwordFile = Join-Path $secretDir 'admin.password.dpapi'
$tokenFile = Join-Path $secretDir 'session.token.dpapi'
$previousDb = $env:SWARMGOD_DB

function New-RandomSecurePassword {
    $randomBytes = New-Object byte[] 32
    $randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $randomGenerator.GetBytes($randomBytes) } finally { $randomGenerator.Dispose() }
    return ConvertTo-SecureString ([Convert]::ToBase64String($randomBytes)) -AsPlainText -Force
}

function Set-OrCreateLocalAdmin([Security.SecureString]$SecurePassword) {
    $env:SWARMGOD_NEW_PASSWORD = [Net.NetworkCredential]::new('', $SecurePassword).Password
    $createErrorFile = [IO.Path]::GetTempFileName()
    try {
        $ErrorActionPreference = 'Continue'
        & go run ./cmd/swarmadmin user add admin operator 2> $createErrorFile
        $createExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        $createError = Get-Content -LiteralPath $createErrorFile -Raw
    } finally {
        $ErrorActionPreference = 'Stop'
        Remove-Item -LiteralPath $createErrorFile -ErrorAction SilentlyContinue
    }

    if ($createExitCode -eq 0) {
        Write-Host 'Created local admin account for this SwarmGod database.'
        return
    }
    if ($createError -notmatch 'username already exists') {
        throw "Account recovery failed: $createError"
    }

    Write-Host 'Local admin already exists; rotating it to a new protected random password.'
    & go run ./cmd/swarmadmin user reset-password admin
    if ($LASTEXITCODE -ne 0) { throw 'Password recovery failed.' }
}

Push-Location $backend
try {
    $env:SWARMGOD_DB = $Database
    if (Test-Path -LiteralPath $passwordFile) {
        try {
            $securePassword = (Get-Content -LiteralPath $passwordFile -Raw).Trim() | ConvertTo-SecureString
        } catch {
            Remove-Item -LiteralPath $passwordFile -ErrorAction SilentlyContinue
            $securePassword = New-RandomSecurePassword
        }
    } else {
        $securePassword = New-RandomSecurePassword
        New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
        # Save before account creation so a later session failure remains recoverable.
        $securePassword | ConvertFrom-SecureString | Set-Content -LiteralPath $passwordFile -NoNewline
        try {
            Set-OrCreateLocalAdmin $securePassword
        } catch {
            Remove-Item -LiteralPath $passwordFile -ErrorAction SilentlyContinue
            throw
        }
    }
    $env:SWARMGOD_NEW_PASSWORD = [Net.NetworkCredential]::new('', $securePassword).Password
    $ErrorActionPreference = 'Continue'
    $issuedToken = & go run ./cmd/swarmadmin session new admin 12
    $sessionExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($sessionExitCode -ne 0) {
        Write-Host 'Saved admin credential is invalid or the local admin is missing; recovering it automatically.'
        $securePassword = New-RandomSecurePassword
        Set-OrCreateLocalAdmin $securePassword
        $securePassword | ConvertFrom-SecureString | Set-Content -LiteralPath $passwordFile -NoNewline
        $issuedToken = & go run ./cmd/swarmadmin session new admin 12
        if ($LASTEXITCODE -ne 0) { throw 'Session creation failed after password recovery.' }
    }
    $issuedToken = ($issuedToken -join '').Trim()
    if ($issuedToken -notmatch '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$') { throw 'Unexpected token format.' }
    $securePassword | ConvertFrom-SecureString | Set-Content -LiteralPath $passwordFile -NoNewline
    ConvertTo-SecureString $issuedToken -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $tokenFile -NoNewline
    $env:SWARMGOD_TOKEN = $issuedToken
    Write-Host 'SWARMGOD_TOKEN set for this PowerShell session (12 hours).'
    Write-Host "Database: $Database"
} finally {
    Remove-Item Env:SWARMGOD_NEW_PASSWORD -ErrorAction SilentlyContinue
    $securePassword = $null
    $issuedToken = $null
    $env:SWARMGOD_DB = $previousDb
    Pop-Location
}
