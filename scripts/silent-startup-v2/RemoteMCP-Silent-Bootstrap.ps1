# REMOTEMCP_SILENT_BOOTSTRAP_V2
# Per-user Windows logon bootstrap. Never pair, stop/restart a node, or admit scientific jobs.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$infra = 'D:\WORK\RESEARCH\.remotemcp\machine-1'
$logDir = Join-Path $infra 'logs'
$logFile = Join-Path $logDir 'silent-bootstrap.jsonl'
$runtime = Join-Path $env:LOCALAPPDATA 'RemoteMCP\runtime'
$configFile = Join-Path $env:LOCALAPPDATA 'RemoteMCP\gateway-config.json'
$deviceId = 'dev_dd73ebfa742f468f2d212bade88c175b'
$fingerprint = 'b657d5395e393e0957a9ed358bb5a1fe1588fde5d3a44be71295a68d1e73def9'
$root = 'D:\WORK\RESEARCH'

function Write-BootEvent {
    param([string]$EventName, [string]$Detail)
    New-Item -ItemType Directory -Path $logDir -Force -ErrorAction Stop | Out-Null
    $line = [ordered]@{
        utc = [DateTimeOffset]::UtcNow.ToString('o')
        event = $EventName
        detail = $Detail
        device_id = $deviceId
    } | ConvertTo-Json -Compress -Depth 3
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 -ErrorAction Stop
}

function Quote-Arg([string]$Text) {
    if ($Text.Contains('"')) { throw 'UNSAFE_QUOTE_IN_LAUNCH_ARGUMENT' }
    return '"' + $Text + '"'
}

try {
    Write-BootEvent 'BOOTSTRAP_BEGIN' 'silent logon startup'
    $device = Get-Content -LiteralPath (Join-Path $runtime 'device.json') -Raw | ConvertFrom-Json
    $cfg = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    if ($device.device_id -ne $deviceId -or
        $device.key_fingerprint_sha256 -ne $fingerprint -or
        [int]$device.route_generation -ne 1) {
        throw 'DEVICE_IDENTITY_MISMATCH'
    }
    if ($cfg.node_release_gate_enabled -ne $true -or
        $cfg.hold_science_job_dispatch -ne $true) {
        throw 'SCIENCE_QUARANTINE_OR_RELEASE_GATE_NOT_ENFORCED'
    }
    $source = [string]$cfg.source_dir
    $sha = [string]$cfg.required_node_release_commit_sha
    if ($source -notmatch '^D:\\2\.RemoteMCP-releases\\[A-Za-z0-9_-]+$' -or
        $sha -notmatch '^[0-9a-f]{40}$') {
        throw 'SOURCE_OR_SHA_UNEXPECTED'
    }
    $marker = Get-Content -LiteralPath (Join-Path $source '.remotemcp-release.json') -Raw | ConvertFrom-Json
    if ($marker.commit -ne $sha) { throw 'PINNED_RELEASE_SHA_MISMATCH' }
    $watchScript = Join-Path $source 'Watch-RemoteMCP-Node.ps1'
    if (-not (Test-Path -LiteralPath $watchScript -PathType Leaf)) {
        throw 'WATCHDOG_SCRIPT_MISSING'
    }

    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $rx = [regex]::Escape($runtime)
    $watches = @($all | Where-Object {
        $_.Name -match '^(powershell|pwsh)\.exe$' -and
        $_.CommandLine -match 'Watch-RemoteMCP-Node\.ps1' -and
        $_.CommandLine -match $rx
    })
    $nodes = @($all | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match 'remotemcp\.node run' -and
        $_.CommandLine -match $rx
    })
    if ($watches.Count -gt 1) { throw 'MULTIPLE_WATCHDOGS' }
    if ($watches.Count -eq 1) {
        if ($watches[0].CommandLine -notmatch [regex]::Escape($source)) {
            throw 'WATCHDOG_EXISTING_WRONG_RELEASE'
        }
        Write-BootEvent 'NOOP_PINNED_WATCHDOG_ALREADY_RUNNING' 'no process changed'
        exit 0
    }
    if ($nodes.Count -gt 0) {
        throw 'NODE_PRESENT_WATCHDOG_ABSENT_HOLD_NO_RESTART'
    }

    $short = $sha.Substring(0,7)
    $venv = Join-Path $infra "venv\$short"
    $env:REMOTEMCP_NODE_VENV = $venv
    $env:REMOTEMCP_LOG_DIR = $logDir
    $env:REMOTEMCP_TEMP_DIR = Join-Path $infra 'tmp'
    $env:REMOTEMCP_CACHE_DIR = Join-Path $infra 'cache'
    $env:REMOTEMCP_CONTROL_DIR = Join-Path $infra 'control'
    $env:REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS = 'ru0_c_u3_executor.py'
    foreach ($dir in @($venv,$logDir,$env:REMOTEMCP_TEMP_DIR,
        $env:REMOTEMCP_CACHE_DIR,$env:REMOTEMCP_CONTROL_DIR)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    # Check again immediately before launch; do not spawn a second watchdog.
    $recent = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    if (@($recent | Where-Object {
        $_.CommandLine -match 'Watch-RemoteMCP-Node\.ps1' -and
        $_.CommandLine -match $rx
    }).Count -gt 0) { throw 'WATCHDOG_APPEARED_DURING_BOOTSTRAP' }
    if (@($recent | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match 'remotemcp\.node run' -and
        $_.CommandLine -match $rx
    }).Count -gt 0) { throw 'NODE_APPEARED_DURING_BOOTSTRAP' }

    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' +
        (Quote-Arg $watchScript) +
        ' -RuntimeDir ' + (Quote-Arg $runtime) +
        ' -RootDir ' + (Quote-Arg $root) +
        ' -SourceDir ' + (Quote-Arg $source) +
        ' -NodeSourceDir ' + (Quote-Arg $source) +
        ' -VenvDir ' + (Quote-Arg $venv) +
        ' -LogDir ' + (Quote-Arg $logDir) +
        ' -TempDir ' + (Quote-Arg $env:REMOTEMCP_TEMP_DIR) +
        ' -CacheDir ' + (Quote-Arg $env:REMOTEMCP_CACHE_DIR) +
        ' -ControlDir ' + (Quote-Arg $env:REMOTEMCP_CONTROL_DIR) +
        ' -DeclaredLongLivedProcessPatterns ru0_c_u3_executor.py'
    Write-BootEvent 'WATCHDOG_START_REQUESTED' "release=$sha"
    $p = Start-Process -FilePath $ps -ArgumentList $arguments -WindowStyle Hidden -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 3
    $confirmed = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ProcessId -eq $p.Id -and
        $_.CommandLine -match [regex]::Escape($watchScript) -and
        $_.CommandLine -match $rx
    })
    if ($confirmed.Count -ne 1) { throw 'PINNED_WATCHDOG_NOT_CONFIRMED' }
    Write-BootEvent 'WATCHDOG_STARTED' "release=$sha; watchdog_pid=$($p.Id); signed_heartbeat_pending=true"
    exit 0
} catch {
    try { Write-BootEvent 'BOOTSTRAP_ERROR' $_.Exception.Message } catch {}
    exit 1
}
