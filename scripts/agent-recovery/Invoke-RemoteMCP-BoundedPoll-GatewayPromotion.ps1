# Exact pinned, gateway-only promotion. No scientific replay or node mutation.
param(
 [ValidateSet('Preflight','Promote')][string]$Mode='Preflight',
 [Parameter(Mandatory=$true)][string]$SourceRepo,
 [Parameter(Mandatory=$true)][string]$InfrastructureDir,
 [Parameter(Mandatory=$true)][string]$OldGatewaySha,
 [Parameter(Mandatory=$true)][string]$NewGatewaySha,
 [Parameter(Mandatory=$true)][string]$NewTreeSha,
 [Parameter(Mandatory=$true)][string]$NodeSha,
 [Parameter(Mandatory=$true)][string]$DeviceId,
 [Parameter(Mandatory=$true)][string]$Fingerprint,
 [switch]$AuthorizePromotion
)
$ErrorActionPreference='Stop'
$base=Join-Path $env:LOCALAPPDATA 'RemoteMCP'
$runtime=Join-Path $base 'runtime'
$cfgPath=Join-Path $base 'gateway-config.json'
$control=Join-Path $InfrastructureDir 'control'
$py=Join-Path $InfrastructureDir ('venv\'+$NodeSha.Substring(0,7)+'\Scripts\python.exe')
$audit=Join-Path $control 'read_only_live_gateway_gate_v1.py'
$admission=Join-Path $control 'Invoke-RemoteMCP-ScienceAdmission.ps1'
$report=@{schema='remotemcp.bounded-poll.promotion.v1';mode=$Mode
  old_gateway=$OldGatewaySha;new_gateway=$NewGatewaySha;node_sha=$NodeSha
  scientific_replay_authorized=$false;node_restart_authorized=$false}
function Assert([bool]$ok,[string]$reason) {if(-not $ok){throw $reason}}
function Log([string]$event) {
 New-Item -ItemType Directory -Path $control -Force|Out-Null
 $report.event=$event;$report.utc=[DateTimeOffset]::UtcNow.ToString('o')
 $out=Join-Path $control ('bounded-poll-promotion-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)+'.json')
 [IO.File]::WriteAllText($out,($report|ConvertTo-Json -Depth 6),(New-Object System.Text.UTF8Encoding($false)))
 Write-Host "EVIDENCE=$out"
}
function Inspect([string]$gatewaySha,[bool]$hold) {
 $cfg=Get-Content $cfgPath -Raw|ConvertFrom-Json
 $source=[string]$cfg.source_dir
 $expected=Join-Path (Split-Path -Parent $source) $gatewaySha.Substring(0,7)
 Assert ($source -ieq $expected) 'GATEWAY_SOURCE_SHA_DRIFT'
 $marker=Get-Content (Join-Path $source '.remotemcp-release.json') -Raw|ConvertFrom-Json
 Assert ($marker.commit -ceq $gatewaySha) 'GATEWAY_MARKER_MISMATCH'
 Assert ($cfg.node_release_gate_enabled -eq $true -and
    $cfg.required_node_release_commit_sha -ceq $NodeSha -and
    $cfg.hold_science_job_dispatch -is [bool] -and
    $cfg.hold_science_job_dispatch -eq $hold) 'GATEWAY_PIN_OR_QUARANTINE_DRIFT'
 $nodePath=Join-Path (Split-Path -Parent $source) $NodeSha.Substring(0,7)
 $nodeMarker=Get-Content (Join-Path $nodePath '.remotemcp-release.json') -Raw|ConvertFrom-Json
 Assert ($nodeMarker.commit -ceq $NodeSha) 'NODE_RELEASE_MARKER_DRIFT'
 $dev=Get-Content (Join-Path $runtime 'device.json') -Raw|ConvertFrom-Json
 Assert ($dev.device_id -ceq $DeviceId -and
    $dev.key_fingerprint_sha256 -ceq $Fingerprint -and
    [int]$dev.route_generation -eq 1) 'PAIRING_OR_ROUTE_DRIFT'
 $startup=Join-Path ([Environment]::GetFolderPath('Startup')) "RemoteMCP-Node-Supervisor-$DeviceId.vbs"
 Assert ((Test-Path $startup -PathType Leaf) -and
    (-not (Test-Path ([IO.Path]::ChangeExtension($startup,'.cmd'))))) 'SILENT_STARTUP_MISMATCH'
 $all=@(Get-CimInstance Win32_Process -ErrorAction Stop)
 $rx=[regex]::Escape($runtime)
 $nodes=@($all|Where-Object{$_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'remotemcp\.node run' -and $_.CommandLine -match $rx})
 $watch=@($all|Where-Object{$_.Name -match '^(powershell|pwsh)\.exe$' -and
    $_.CommandLine -match 'Watch-RemoteMCP-Node\.ps1' -and $_.CommandLine -match $rx})
 $ids=@{};foreach($n in $nodes){$ids[[int]$n.ProcessId]=$true}
 $roots=@($nodes|Where-Object{-not $ids.ContainsKey([int]$_.ParentProcessId)})
 Assert ($roots.Count -eq 1 -and $watch.Count -eq 1 -and
    $watch[0].CommandLine -match [regex]::Escape($NodeSha.Substring(0,7))) 'NODE_WATCHDOG_TOPOLOGY_DRIFT'
 Assert ((Test-Path $py -PathType Leaf) -and (Test-Path $audit -PathType Leaf)) 'READONLY_JOURNAL_EXECUTOR_MISSING'
 $raw=@(& $py $audit $runtime ([string]$cfg.runtime_dir) $DeviceId)
 Assert ($LASTEXITCODE -eq 0 -and $raw.Count -eq 1) 'READONLY_JOURNAL_AUDIT_FAILED'
 $j=$raw[0]|ConvertFrom-Json
 Assert ($null -ne $j.node_durable_nonterminal -and
    $null -ne $j.node_routed_nonterminal -and $null -ne $j.gateway_unexpired_commands) 'JOURNAL_FIELDS_MISSING'
 Assert ([int]$j.node_durable_nonterminal -eq 0 -and
    [int]$j.node_routed_nonterminal -eq 0 -and
    [int]$j.gateway_unexpired_commands -eq 0) 'ACTIVE_JOB_OR_UNEXPIRED_COMMAND_HOLD'
 $report.journal=$j
}
try{
 foreach($sha in @($OldGatewaySha,$NewGatewaySha,$NewTreeSha,$NodeSha)){
   Assert ($sha -cmatch '^[0-9a-f]{40}$') 'SHA_FORMAT_INVALID'
 }
 Assert ($Mode -ne 'Promote' -or [bool]$AuthorizePromotion) 'EXPLICIT_PROMOTION_AUTHORIZATION_REQUIRED'
 Inspect $OldGatewaySha $false
 $dirty=@(& git -C $SourceRepo status --porcelain)
 Assert ($LASTEXITCODE -eq 0 -and $dirty.Count -eq 0) 'SOURCE_GIT_DIRTY_OR_MISSING'
 $head=(& git -C $SourceRepo rev-parse HEAD).Trim()
 $tree=(& git -C $SourceRepo rev-parse 'HEAD^{tree}').Trim()
 Assert ($head -ceq $NewGatewaySha -and $tree -ceq $NewTreeSha) 'FROZEN_SOURCE_INTEGRITY_FAILURE'
 $update=Join-Path $SourceRepo 'Update-RemoteMCP-Gateway.ps1'
 $updateArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$update,
   '-SourceRepo',$SourceRepo,'-Ref',$NewGatewaySha,'-SkipFetch','-PinnedNodeReleaseSha',$NodeSha)
 & powershell.exe @($updateArgs+@('-PreflightOnly'))
 Assert ($LASTEXITCODE -eq 0) 'GATEWAY_ISOLATED_PREFLIGHT_FAILED'
 if($Mode -eq 'Preflight'){
   Log 'PREFLIGHT_PASS_UNCHANGED'
   Write-Host 'BOUND_POLL_GATEWAY_PREFLIGHT=PASS'
   exit 0
 }
 # Fresh validation immediately before production mutation.
 Inspect $OldGatewaySha $false
 Log 'JUST_BEFORE_PROMOTION_SAFETY_PASS'
 & powershell.exe @updateArgs
 Assert ($LASTEXITCODE -eq 0) 'GATEWAY_UPGRADE_FAILED_LEAVE_QUARANTINE'
 Inspect $NewGatewaySha $true
 Log 'GATEWAY_PROMOTED_QUARANTINED'
 Assert (Test-Path $admission -PathType Leaf) 'ADMISSION_SCRIPT_MISSING_LEAVE_QUARANTINE'
 $blob=(& git hash-object -- $admission).Trim()
 Assert ($LASTEXITCODE -eq 0 -and $blob -ceq 'ec8f698ea741b7ea8961e7ed72524f58e499acb6') 'ADMISSION_SCRIPT_BLOB_MISMATCH_LEAVE_QUARANTINE'
 $adArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$admission,
   '-InfrastructureDir',$InfrastructureDir,'-ExpectedGatewaySha',$NewGatewaySha,
   '-ExpectedNodeSha',$NodeSha,'-DeviceId',$DeviceId,'-ExpectedFingerprint',$Fingerprint)
 foreach($m in @('Audit','Release','Verify')){
   & powershell.exe @($adArgs+@('-Mode',$m))
   Assert ($LASTEXITCODE -eq 0) ('ADMISSION_'+$m+'_FAILED')
 }
 Inspect $NewGatewaySha $false
 Log 'LOCAL_PROMOTION_AND_ADMISSION_PASS'
 Write-Host 'REMOTEMCP_BOUNDED_POLL_GATEWAY_PROMOTION_LOCAL=PASS'
 Write-Host 'SIGNED_ATTESTATION_AND_LIVE_SIX_READS=PENDING'
 Write-Host 'NODE_AND_SCIENTIFIC_JOBS=UNTOUCHED'
 exit 0
}catch{
 $report.error=$_.Exception.Message
 try{Log 'HOLD_NO_AUTOMATIC_ROLLBACK'}catch{}
 Write-Error ('GATEWAY_PROMOTION_HOLD: '+$_.Exception.Message)
 exit 1
}
