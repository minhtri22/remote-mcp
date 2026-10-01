param(
    [int]$IntervalSeconds = 15,
    [int]$FailureThreshold = 3,
    [int]$HealthTimeoutSec = 5
)

$ErrorActionPreference = "Continue"
$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$ConfigFile = Join-Path $Base "gateway-config.json"
$StartScript = Join-Path $PSScriptRoot "Start-RemoteMCP-Gateway.ps1"
$WatchdogLog = Join-Path $Base "gateway-watchdog.log"

function Log([string]$Message) {
    Add-Content -Encoding UTF8 -Path $WatchdogLog -Value ("{0} {1}" -f (Get-Date).ToString("o"),$Message)
}

if (-not (Test-Path $ConfigFile)) {
    Log "configuration missing; watchdog exiting"
    exit 2
}

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$failures = 0
Log "watchdog started for port $($cfg.port)"

while ($true) {
    $healthy = $false
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$($cfg.port)/.well-known/oauth-authorization-server" -TimeoutSec $HealthTimeoutSec
        $healthy = ($r.StatusCode -eq 200)
    } catch {}

    if ($healthy) {
        if ($failures -gt 0) { Log "gateway recovered before restart; consecutive_failures=$failures" }
        $failures = 0
    } else {
        $failures += 1
        Log "gateway health failure $failures/$FailureThreshold"
        if ($failures -ge $FailureThreshold) {
            try {
                Log "restart threshold reached; invoking out-of-band gateway recovery"
                & $StartScript -Restart -HealthTimeoutSec $HealthTimeoutSec *>> $WatchdogLog
                Log "gateway restart completed"
            } catch {
                Log ("gateway restart failed: " + $_.Exception.Message)
            }
            $failures = 0
        }
    }
    Start-Sleep -Seconds ([Math]::Max(5,$IntervalSeconds))
}
