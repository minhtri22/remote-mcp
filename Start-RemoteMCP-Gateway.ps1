param(
    [switch]$Restart,
    [int]$HealthTimeoutSec = 5
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
$SecretFile = Join-Path $Base "gateway-owner-password.txt"
$PidFile = Join-Path $Base "gateway-process.json"
$LogFile = Join-Path $Base "gateway.log"
$ErrFile = Join-Path $Base "gateway-error.log"

if (-not (Test-Path $ConfigFile) -or -not (Test-Path $SecretFile)) {
    throw "Gateway recovery is not configured. Run .\Configure-RemoteMCP-Gateway.ps1 once first."
}

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$secure = Get-Content $SecretFile -Raw | ConvertTo-SecureString
$ownerPassword = [System.Net.NetworkCredential]::new("",$secure).Password

function Test-GatewayHealth {
    param([int]$Port,[int]$TimeoutSec)
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/.well-known/oauth-authorization-server" -TimeoutSec $TimeoutSec
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Get-GatewayListener {
    param([int]$Port)
    try {
        return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    } catch { return $null }
}

$port = [int]$cfg.port
$listener = Get-GatewayListener -Port $port
$healthy = Test-GatewayHealth -Port $port -TimeoutSec $HealthTimeoutSec

if ($healthy -and -not $Restart) {
    Write-Host "RemoteMCP gateway is already healthy on port $port."
    exit 0
}

if ($listener) {
    $listenerPid = [int]$listener.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
    if (-not $proc -or -not $proc.CommandLine -or $proc.CommandLine -notmatch "server\.py") {
        throw "Port $port is owned by PID $listenerPid, but it does not look like RemoteMCP server.py. Refusing to kill it."
    }
    if (-not $healthy) { Write-Host "RemoteMCP gateway is unresponsive; restarting PID $listenerPid..." }
    elseif ($Restart) { Write-Host "Restarting healthy RemoteMCP gateway PID $listenerPid by request..." }
    Stop-Process -Id $listenerPid -Force
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $listener = Get-GatewayListener -Port $port
    } while ($listener -and (Get-Date) -lt $deadline)
    if ($listener) { throw "Port $port did not close after stopping the old gateway." }
}

$env:PUBLIC_URL = [string]$cfg.public_url
$env:OWNER_PASSWORD = $ownerPassword
$env:MCP_ROOT = [string]$cfg.mcp_root
$env:MCP_STATE = [string]$cfg.state_file
$env:MCP_RUNTIME_DIR = [string]$cfg.runtime_dir
$env:ALLOWED_REDIRECT_HOSTS = [string]$cfg.allowed_redirect_hosts
$env:PORT = [string]$port

foreach ($p in @($cfg.mcp_root,$cfg.runtime_dir,(Split-Path $cfg.state_file -Parent))) {
    if ($p) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

$flags = 0
$process = Start-Process -FilePath ([string]$cfg.python_exe) -ArgumentList @("server.py") -WorkingDirectory ([string]$cfg.source_dir) -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru
$ownerPassword = $null
$env:OWNER_PASSWORD = ""

$deadline = (Get-Date).AddSeconds(25)
$ok = $false
do {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "RemoteMCP gateway exited during startup with code $($process.ExitCode). See $ErrFile"
    }
    $ok = Test-GatewayHealth -Port $port -TimeoutSec $HealthTimeoutSec
} while (-not $ok -and (Get-Date) -lt $deadline)

if (-not $ok) {
    throw "RemoteMCP gateway did not become healthy within the verification window. See $ErrFile"
}

$listener = Get-GatewayListener -Port $port
$record = [ordered]@{
    pid = if ($listener) { [int]$listener.OwningProcess } else { $process.Id }
    started_at = (Get-Date).ToString("o")
    source_dir = [string]$cfg.source_dir
    port = $port
}
$record | ConvertTo-Json | Set-Content -Encoding UTF8 $PidFile

Write-Host "RemoteMCP gateway ONLINE"
Write-Host "PID    : $($record.pid)"
Write-Host "Port   : $port"
Write-Host "Source : $($cfg.source_dir)"
Write-Host "State and runtime paths were reused; OAuth/device registries were not recreated."
