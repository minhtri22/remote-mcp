param(
    [Parameter(Mandatory=$true)]
    [string]$RuntimeDir,
    [Parameter(Mandatory=$true)]
    [string]$RootDir,
    [string]$SourceDir = $PSScriptRoot,
    [Parameter(Mandatory=$true)]
    [string]$NodeSourceDir,
    [int]$IntervalSeconds = 15,
    [int]$MissingProcessThreshold = 2,
    [int]$PostStartGraceSeconds = 3
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$Base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$StartScript = Join-Path $SourceDir "Start-RemoteMCP-Node.ps1"

function Log([string]$Path,[string]$Message) {
    Add-Content -Encoding UTF8 -Path $Path -Value ("{0} {1}" -f (Get-Date).ToString("o"),$Message)
}

function Read-Identity([string]$Runtime) {
    $DeviceFile = Join-Path $Runtime "device.json"
    $KeyFile = Join-Path $Runtime "device-ed25519.pem"

    if (-not (Test-Path $DeviceFile) -or -not (Test-Path $KeyFile)) {
        throw "Runtime does not contain a paired RemoteMCP device identity: $Runtime"
    }

    $d = Get-Content $DeviceFile -Raw | ConvertFrom-Json
    if (-not $d.device_id -or -not $d.key_fingerprint_sha256 -or $null -eq $d.route_generation) {
        throw "Runtime identity is incomplete: $Runtime"
    }

    return [ordered]@{
        device_id = [string]$d.device_id
        key_fingerprint_sha256 = [string]$d.key_fingerprint_sha256
        route_generation = [string]$d.route_generation
    }
}

function Get-NodeProcesses([string]$Runtime) {
    $escaped = [Regex]::Escape($Runtime)
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "remotemcp\.node" -and
        $_.CommandLine -match "--runtime-dir" -and
        $_.CommandLine -match $escaped
    })
}

function Get-LogicalNodeRoots([array]$Processes) {
    $ids = @{}
    foreach ($p in @($Processes)) {
        $ids[[int]$p.ProcessId] = $true
    }
    return @($Processes | Where-Object {
        -not $ids.ContainsKey([int]$_.ParentProcessId)
    })
}

$RuntimeDir = (Resolve-Path $RuntimeDir).Path
$RootDir = (Resolve-Path $RootDir).Path
$SourceDir = (Resolve-Path $SourceDir).Path
$NodeSourceDir = (Resolve-Path $NodeSourceDir).Path
if (-not (Test-Path $StartScript)) {
    throw "RemoteMCP node starter not found: $StartScript"
}
if (-not (Test-Path (Join-Path $NodeSourceDir "remotemcp\node\__main__.py"))) {
    throw "RemoteMCP node source not found under: $NodeSourceDir"
}

$Expected = Read-Identity -Runtime $RuntimeDir
New-Item -ItemType Directory -Force -Path $Base | Out-Null
$WatchdogLog = Join-Path $Base ("node-watchdog-" + $Expected.device_id + ".log")

function Assert-IdentityUnchanged {
    $Current = Read-Identity -Runtime $RuntimeDir
    foreach ($Field in @("device_id","key_fingerprint_sha256","route_generation")) {
        if ([string]$Current[$Field] -ne [string]$Expected[$Field]) {
            throw "Identity changed while watchdog was running: $Field"
        }
    }
}

$threshold = [Math]::Max(1,$MissingProcessThreshold)
$interval = [Math]::Max(5,$IntervalSeconds)
$grace = [Math]::Max(1,$PostStartGraceSeconds)
$missing = 0

Log $WatchdogLog ("watchdog started device_id={0} runtime={1} root={2}" -f $Expected.device_id,$RuntimeDir,$RootDir)

while ($true) {
    try {
        Assert-IdentityUnchanged
    } catch {
        Log $WatchdogLog ("identity safety stop: " + $_.Exception.Message)
        exit 3
    }

    $existing = @(Get-NodeProcesses -Runtime $RuntimeDir)
    $roots = @(Get-LogicalNodeRoots -Processes $existing)

    if ($roots.Count -gt 1) {
        Log $WatchdogLog (
            "safety stop: multiple independent exact-runtime node roots detected roots={0} raw_processes={1}" -f
            $roots.Count,$existing.Count
        )
        exit 4
    }

    if ($roots.Count -eq 1) {
        if ($missing -gt 0) {
            Log $WatchdogLog ("node process recovered without supervisor start; consecutive_missing={0}" -f $missing)
        }
        $missing = 0
    } else {
        $missing += 1
        Log $WatchdogLog ("node process absent {0}/{1}" -f $missing,$threshold)

        if ($missing -ge $threshold) {
            try {
                Assert-IdentityUnchanged
                Log $WatchdogLog "process absence threshold reached; invoking state-preserving node start"
                & $StartScript -RuntimeDir $RuntimeDir -RootDir $RootDir -SourceDir $NodeSourceDir *>> $WatchdogLog
                Start-Sleep -Seconds $grace
                Assert-IdentityUnchanged

                $after = @(Get-NodeProcesses -Runtime $RuntimeDir)
                $afterRoots = @(Get-LogicalNodeRoots -Processes $after)
                if ($afterRoots.Count -eq 1) {
                    Log $WatchdogLog (
                        "logical node process restored root_pid={0} raw_processes={1}" -f
                        $afterRoots[0].ProcessId,$after.Count
                    )
                } elseif ($afterRoots.Count -gt 1) {
                    Log $WatchdogLog (
                        "safety stop after restore: multiple independent node roots roots={0} raw_processes={1}" -f
                        $afterRoots.Count,$after.Count
                    )
                    exit 4
                } else {
                    Log $WatchdogLog "starter returned but logical node process is still absent"
                }
            } catch {
                Log $WatchdogLog ("node start failed: " + $_.Exception.Message)
            }
            $missing = 0
        }
    }

    Start-Sleep -Seconds $interval
}
