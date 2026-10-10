# REMOTEMCP_EXPLICIT_ADMISSION_V1
# Modes: Audit (read-only), Release (operator consent), Verify (read-only).
# Never submits/retries a scientific job, changes device identity, or disables the exact release pin.
param(
    [ValidateSet('Audit','Release','Verify')][string]$Mode='Audit',
    [Parameter(Mandatory=$true)][string]$InfrastructureDir,
    [Parameter(Mandatory=$true)][string]$ExpectedGatewaySha,
    [Parameter(Mandatory=$true)][string]$ExpectedNodeSha,
    [Parameter(Mandatory=$true)][string]$DeviceId,
    [Parameter(Mandatory=$true)][string]$ExpectedFingerprint
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$base=Join-Path $env:LOCALAPPDATA 'RemoteMCP'
$configFile=Join-Path $base 'gateway-config.json'
$deviceFile=Join-Path $base 'runtime\device.json'
$nodeRuntime=Join-Path $base 'runtime'
$control=Join-Path $InfrastructureDir 'control'
$logs=Join-Path $InfrastructureDir 'logs'
$auditScript=Join-Path $control 'read_only_live_gateway_gate_v1.py'
$oldPy=Join-Path $InfrastructureDir ('venv\'+$ExpectedNodeSha.Substring(0,7)+'\Scripts\python.exe')
$written=$false
$backupFile=$null
$gatewaySource=$null
$report=@{
    schema='remotemcp.explicit-admission-v1'
    observed_utc=[DateTimeOffset]::UtcNow.ToString('o')
    mode=$Mode;device_id=$DeviceId;expected_gateway_sha=$ExpectedGatewaySha
    expected_node_sha=$ExpectedNodeSha;quarantine_modified=$false
    science_replay_authorized=$false
}
function Fail([string]$Message) { throw $Message }
function Check-LocalIdentity {
    if ($ExpectedGatewaySha -cnotmatch '^[0-9a-f]{40}$' -or $ExpectedNodeSha -cnotmatch '^[0-9a-f]{40}$') {
        Fail 'INVALID_IMMUTABLE_RELEASE_SHA'
    }
    $identity=Get-Content -LiteralPath $deviceFile -Raw | ConvertFrom-Json
    if ($identity.device_id -cne $DeviceId -or
        $identity.key_fingerprint_sha256 -cne $ExpectedFingerprint -or
        [int]$identity.route_generation -ne 1) { Fail 'DEVICE_IDENTITY_OR_ROUTE_MISMATCH' }
    $script:cfg=Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    if ($cfg.source_dir -ne $gatewaySource -or
        $cfg.required_node_release_commit_sha -cne $ExpectedNodeSha -or
        $cfg.node_release_gate_enabled -ne $true -or
        $cfg.hold_science_job_dispatch -isnot [bool]) { Fail 'GATEWAY_PIN_OR_BOOLEAN_GATE_MISMATCH' }
    $g=Get-Content -LiteralPath (Join-Path $gatewaySource '.remotemcp-release.json') -Raw | ConvertFrom-Json
    if ($g.commit -cne $ExpectedGatewaySha) { Fail 'GATEWAY_RELEASE_MARKER_MISMATCH' }
    $nodeRelease=Join-Path (Split-Path -Parent $gatewaySource) $ExpectedNodeSha.Substring(0,7)
    $n=Get-Content -LiteralPath (Join-Path $nodeRelease '.remotemcp-release.json') -Raw | ConvertFrom-Json
    if ($n.commit -cne $ExpectedNodeSha) { Fail 'PINNED_NODE_MARKER_MISMATCH' }
    $startup=Join-Path ([Environment]::GetFolderPath('Startup')) "RemoteMCP-Node-Supervisor-$DeviceId.vbs"
    if (-not (Test-Path -LiteralPath $startup -PathType Leaf) -or
        (Test-Path -LiteralPath ([IO.Path]::ChangeExtension($startup,'.cmd')))) {
        Fail 'HIDDEN_STARTUP_REGISTRATION_MISMATCH'
    }
}
function Check-ProcessAndHealth {
    $p=[int]$cfg.port
    $status=Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$p/.well-known/oauth-authorization-server" -TimeoutSec 10
    if ([int]$status.StatusCode -ne 200) { Fail 'GATEWAY_HEALTH_NOT_200' }
    $record=Get-Content -LiteralPath (Join-Path $base 'gateway-process.json') -Raw | ConvertFrom-Json
    $owners=@(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1 -or [int]$owners[0] -ne [int]$record.pid -or
        $record.source_dir -ne $gatewaySource) { Fail 'GATEWAY_LISTENER_RELEASE_OR_PID_MISMATCH' }
    $all=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $rx=[regex]::Escape($nodeRuntime)
    $nodes=@($all | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'remotemcp\.node run' -and $_.CommandLine -match $rx
    })
    $watch=@($all | Where-Object {
        $_.Name -match '^(powershell|pwsh)\.exe$' -and
        $_.CommandLine -match 'Watch-RemoteMCP-Node\.ps1' -and
        $_.CommandLine -match $rx
    })
    $ids=@{};foreach($x in $nodes){$ids[[int]$x.ProcessId]=$true}
    $roots=@($nodes | Where-Object {-not $ids.ContainsKey([int]$_.ParentProcessId)})
    if ($roots.Count -ne 1 -or $watch.Count -ne 1) { Fail 'NODE_WATCHDOG_TOPOLOGY_CHANGED' }
    if ($watch[0].CommandLine -notmatch [regex]::Escape($ExpectedNodeSha.Substring(0,7))) {
        Fail 'WATCHDOG_NOT_PINNED_NODE_RELEASE'
    }
    $script:report.node_roots=$roots.Count
    $script:report.watchdogs=$watch.Count
    $script:report.gateway_listener_pid=[int]$record.pid
}
function FreshReadOnlyJournal {
    if (-not (Test-Path -LiteralPath $oldPy -PathType Leaf) -or -not
        (Test-Path -LiteralPath $auditScript -PathType Leaf)) { Fail 'AUDIT_EXECUTABLE_MISSING' }
    $raw=@(& $oldPy $auditScript $nodeRuntime ([string]$cfg.runtime_dir) $DeviceId)
    if ($LASTEXITCODE -ne 0 -or $raw.Count -ne 1) { Fail 'READONLY_EXACT_JOURNAL_EXECUTION_FAILED' }
    $j=($raw[0] | ConvertFrom-Json)
    if ($null -eq $j.gateway_unexpired_commands -or
        $null -eq $j.node_durable_nonterminal -or
        $null -eq $j.node_routed_nonterminal) { Fail 'READONLY_JOURNAL_FIELDS_MISSING' }
    if ([int]$j.gateway_unexpired_commands -ne 0 -or
        [int]$j.node_durable_nonterminal -ne 0 -or
        [int]$j.node_routed_nonterminal -ne 0) { Fail 'ACTIVE_JOB_OR_UNEXPIRED_COMMAND_HOLD' }
    $script:report.gateway_pending_total=[int]$j.gateway_pending_total
    $script:report.gateway_unexpired_commands=0
    $script:report.node_nonterminal=0
    $script:report.journal_observed_at_ms=$j.observed_at_ms
}
function Write-Evidence([string]$Event) {
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $report.event=$Event
    $report.reported_utc=[DateTimeOffset]::UtcNow.ToString('o')
    Add-Content -LiteralPath (Join-Path $logs 'science-admission.jsonl') -Value ($report | ConvertTo-Json -Depth 6 -Compress) -Encoding UTF8
}
try {
    if ($ExpectedGatewaySha -notmatch '^[0-9a-f]{40}$') { Fail 'GATEWAY_SHA_MALFORMED' }
    if ($ExpectedNodeSha -notmatch '^[0-9a-f]{40}$') { Fail 'NODE_SHA_MALFORMED' }
    $baseSource=Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    $gatewaySource=[string]$baseSource.source_dir
    Check-LocalIdentity
    Check-ProcessAndHealth
    if ($Mode -eq 'Verify') {
        if ($cfg.hold_science_job_dispatch -ne $false) { Fail 'DISPATCH_REMAINS_QUARANTINED' }
        Write-Evidence 'VERIFY_PASS'
        Write-Host 'REMOTEMCP_SCIENCE_ADMISSION_LOCAL_VERIFY=PASS'
        Write-Host 'SIGNED_GATEWAY_NODE_STATUS=REQUIRES_INDEPENDENT_REMOTE_CHECK'
        exit 0
    }
    if ($cfg.hold_science_job_dispatch -ne $true) { Fail 'PRE_RELEASE_QUARANTINE_ALREADY_NOT_HELD' }
    FreshReadOnlyJournal
    Write-Evidence 'AUDIT_PASS'
    Write-Host 'REMOTEMCP_SCIENCE_ADMISSION_AUDIT=PASS'
    Write-Host "GATEWAY_QUEUED_OR_LEASED_TOTAL=$($report.gateway_pending_total)"
    Write-Host 'UNEXPIRED_COMMANDS=0'
    Write-Host 'NODE_NONTERMINAL=0'
    if ($Mode -eq 'Audit') { exit 0 }

    # Final guarded read-only refresh immediately before changing the config.
    Check-LocalIdentity
    Check-ProcessAndHealth
    FreshReadOnlyJournal
    $oldText=Get-Content -LiteralPath $configFile -Raw
    $nextCfg=$oldText|ConvertFrom-Json
    if ($nextCfg.source_dir -ne $gatewaySource -or
        $nextCfg.required_node_release_commit_sha -cne $ExpectedNodeSha -or
        $nextCfg.hold_science_job_dispatch -ne $true) { Fail 'LAST_MINUTE_CONFIG_DRIFT' }
    $nextCfg.hold_science_job_dispatch=$false
    $temp=Join-Path $base ('gateway-config.admission-'+[guid]::NewGuid().ToString('N')+'.tmp')
    $backupFile=Join-Path $base ('gateway-config.pre-admission-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json')
    [IO.File]::WriteAllText($temp,($nextCfg|ConvertTo-Json -Depth 45),[Text.UTF8Encoding]::new($false))
    [IO.File]::Replace($temp,$configFile,$backupFile)
    $written=$true
    $report.quarantine_modified=$true
    $report.backup_file=$backupFile
    Write-Evidence 'CONFIG_RELEASED_RESTART_PENDING'
    $restart=Join-Path $gatewaySource 'Restart-RemoteMCP-Gateway.ps1'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $restart
    if ($LASTEXITCODE -ne 0) { Fail 'RELEASED_GATEWAY_RESTART_FAILED' }
    Check-LocalIdentity
    Check-ProcessAndHealth
    if ($cfg.hold_science_job_dispatch -ne $false) { Fail 'CONFIG_ADMISSION_DID_NOT_PERSIST' }
    Write-Evidence 'LOCAL_RELEASE_PASS'
    Write-Host 'REMOTEMCP_SCIENCE_ADMISSION_LOCAL_RELEASE=PASS'
    Write-Host 'SIGNED_REMOTE_ADMISSION_VERIFICATION=PENDING'
    Write-Host 'HISTORICAL_COMMAND_REPLAY=NOT_AUTHORIZED'
    exit 0
} catch {
    $original=$_.Exception.Message
    if ($written -and $backupFile -and (Test-Path -LiteralPath $backupFile)) {
        try {
            $restoreTmp=Join-Path $base ('gateway-config.restore-'+[guid]::NewGuid().ToString('N')+'.tmp')
            Copy-Item -LiteralPath $backupFile -Destination $restoreTmp -ErrorAction Stop
            $failedCfg=Join-Path $base ('gateway-config.failed-admission-'+[guid]::NewGuid().ToString('N')+'.json')
            [IO.File]::Replace($restoreTmp,$configFile,$failedCfg)
            & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $gatewaySource 'Restart-RemoteMCP-Gateway.ps1')
            if ($LASTEXITCODE -ne 0) { throw 'ROLLBACK_GATEWAY_RESTART_FAILED' }
            Check-LocalIdentity
            Check-ProcessAndHealth
            if ($cfg.hold_science_job_dispatch -ne $true) { throw 'QUARANTINE_NOT_RESTORED' }
            Write-Evidence 'ROLLBACK_TO_QUARANTINE_PASS'
            Write-Warning "ADMISSION_RELEASE_FAILED_AND_QUARANTINE_RESTORED: $original"
        } catch {
            Write-Error ("CRITICAL_ADMISSION_ROLLBACK_UNVERIFIED: "+$original+" / "+$_.Exception.Message)
            exit 2
        }
    } else {
        try { Write-Evidence 'FAIL_BEFORE_MUTATION' } catch {}
        Write-Error $original
    }
    exit 1
}
