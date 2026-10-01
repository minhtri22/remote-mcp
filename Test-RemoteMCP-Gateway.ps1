param([int]$Port = 8099,[int]$TimeoutSec = 5)

$ErrorActionPreference = "Continue"
$listener = $null
try { $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1 } catch {}
$health = $false
$httpStatus = $null
$errorText = $null
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/.well-known/oauth-authorization-server" -TimeoutSec $TimeoutSec
    $httpStatus = $r.StatusCode
    $health = ($r.StatusCode -eq 200)
} catch {
    $errorText = $_.Exception.Message
}

$proc = $null
if ($listener) {
    try { $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$listener.OwningProcess)" } catch {}
}

[ordered]@{
    port = $Port
    listener = [bool]$listener
    pid = if ($listener) { [int]$listener.OwningProcess } else { $null }
    command_line = if ($proc) { $proc.CommandLine } else { $null }
    metadata_http_status = $httpStatus
    healthy = $health
    error = $errorText
} | ConvertTo-Json -Depth 4

if (-not $health) { exit 1 }
