param(
    [Parameter(Mandatory=$true)][string]$OldRuntimeDir,
    [Parameter(Mandatory=$true)][string]$InfrastructureRoot,
    [Parameter(Mandatory=$true)][string]$RootDir,
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$ExpectedDeviceId,
    [Parameter(Mandatory=$true)][string]$ExpectedFingerprint,
    [Parameter(Mandatory=$true)][int]$ExpectedRouteGeneration,
    [int]$DelaySeconds = 0
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Assert-NonOsPath {
    param([string]$Path,[string]$Label)
    if (-not $Path) { throw "$Label is required." }
    if (-not [System.IO.Path]::IsPathRooted($Path) -or $Path -match '^[A-Za-z]:[^\\/]') {
        throw "$Label must be absolute."
    }
    $full=[System.IO.Path]::GetFullPath($Path)
    $drive=[System.IO.Path]::GetPathRoot($full)
    $os=[System.IO.Path]::GetPathRoot($env:SystemRoot)
    if ($drive -and $os -and ($drive.TrimEnd('\') -ieq $os.TrimEnd('\'))) {
        throw "OS_DRIVE_REMOTEMCP_INFRA_FORBIDDEN: $Label cannot be on the OS drive."
    }
    return $full
}

function Read-Identity([string]$Runtime) {
    $d=Get-Content (Join-Path $Runtime "device.json") -Raw | ConvertFrom-Json
    return [ordered]@{
        device_id=[string]$d.device_id
        key_fingerprint_sha256=[string]$d.key_fingerprint_sha256
        route_generation=[int]$d.route_generation
    }
}

function Assert-Identity([hashtable]$Identity) {
    if ($Identity.device_id -ne $ExpectedDeviceId) { throw "DEVICE_ID_MISMATCH" }
    if ($Identity.key_fingerprint_sha256 -ne $ExpectedFingerprint) { throw "DEVICE_FINGERPRINT_MISMATCH" }
    if ([int]$Identity.route_generation -ne $ExpectedRouteGeneration) { throw "DEVICE_ROUTE_GENERATION_MISMATCH" }
}

if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

$InfrastructureRoot=Assert-NonOsPath $InfrastructureRoot "InfrastructureRoot"
$RootDir=Assert-NonOsPath $RootDir "RootDir"
$OldRuntimeDir=(Resolve-Path $OldRuntimeDir).Path
$SourceDir=(Resolve-Path $SourceDir).Path

$RuntimeDir=Join-Path $InfrastructureRoot "runtime"
$VenvDir=Join-Path $InfrastructureRoot "venv"
$LogDir=Join-Path $InfrastructureRoot "logs"
$TempDir=Join-Path $InfrastructureRoot "tmp"
$CacheDir=Join-Path $InfrastructureRoot "cache"
$ControlDir=Join-Path $InfrastructureRoot "control"
foreach($p in @($InfrastructureRoot,$RuntimeDir,$VenvDir,$LogDir,$TempDir,$CacheDir,$ControlDir)){
    New-Item -ItemType Directory -Force -Path $p | Out-Null
}

$env:TEMP=$TempDir
$env:TMP=$TempDir
$env:XDG_CACHE_HOME=$CacheDir
$env:PIP_CACHE_DIR=Join-Path $CacheDir "pip"
$env:PYTHONPYCACHEPREFIX=Join-Path $CacheDir "pycache"
foreach($p in @($env:PIP_CACHE_DIR,$env:PYTHONPYCACHEPREFIX)){New-Item -ItemType Directory -Force -Path $p|Out-Null}

$Before=Read-Identity $OldRuntimeDir
Assert-Identity $Before

# Fail closed if the old node durable DB still contains real non-terminal work.
$OldPython=Join-Path $env:LOCALAPPDATA "RemoteMCP\node-venv\Scripts\python.exe"
if (-not (Test-Path $OldPython)) {
    $OldPython=(Get-Command python.exe -ErrorAction Stop).Source
}
$DurableDb=Join-Path $OldRuntimeDir "durable\runtime.db"
$active = & $OldPython -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute(\"select count(*) from jobs where state in ('QUEUED','STARTING','RUNNING','CANCELLING')\").fetchone()[0]); c.close()" $DurableDb
if ($LASTEXITCODE -ne 0) { throw "ACTIVE_JOB_AUDIT_FAILED" }
if ([int]($active|Select-Object -Last 1) -ne 0) { throw "ACTIVE_NODE_JOBS_PRESENT" }

# Stop only the exact old runtime watchdog/node. Never touch other device identities.
$escaped=[Regex]::Escape($OldRuntimeDir)
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'Watch-RemoteMCP-Node\.ps1' -and $_.CommandLine -match $escaped
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'remotemcp\.node' -and $_.CommandLine -match $escaped
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
Start-Sleep -Seconds 2

if (@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'remotemcp\.node' -and $_.CommandLine -match $escaped
}).Count -ne 0) { throw "OLD_RUNTIME_NODE_STILL_RUNNING" }

# Copy paired state only after the old node has stopped; omit the stale lock byte.
Get-ChildItem $OldRuntimeDir -Force | Where-Object {$_.Name -ne "node.lock"} | ForEach-Object {
    Copy-Item $_.FullName -Destination $RuntimeDir -Recurse -Force
}
$AfterCopy=Read-Identity $RuntimeDir
Assert-Identity $AfterCopy

$Start=Join-Path $SourceDir "Start-RemoteMCP-Node.ps1"
$Supervisor=Join-Path $SourceDir "Install-RemoteMCP-Node-Supervisor.ps1"

& $Start -RuntimeDir $RuntimeDir -RootDir $RootDir -SourceDir $SourceDir -VenvDir $VenvDir -LogDir $LogDir -TempDir $TempDir -CacheDir $CacheDir -ControlDir $ControlDir -ZeroC -Restart
if ($LASTEXITCODE -ne 0) { throw "ZERO_C_NODE_START_FAILED" }

& $Supervisor -RuntimeDir $RuntimeDir -RootDir $RootDir -SourceDir $SourceDir -NodeSourceDir $SourceDir -VenvDir $VenvDir -LogDir $LogDir -TempDir $TempDir -CacheDir $CacheDir -ControlDir $ControlDir -ZeroC -PersistenceMode Auto -StartNow
if ($LASTEXITCODE -ne 0) { throw "ZERO_C_SUPERVISOR_INSTALL_FAILED" }

Start-Sleep -Seconds 5
$After=Read-Identity $RuntimeDir
Assert-Identity $After

$newEscaped=[Regex]::Escape($RuntimeDir)
$rootEscaped=[Regex]::Escape($RootDir)
$node=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'remotemcp\.node' -and $_.CommandLine -match $newEscaped -and $_.CommandLine -match $rootEscaped
})
if ($node.Count -lt 1) { throw "ZERO_C_NODE_PROCESS_NOT_FOUND" }

# Remove old per-user Startup fallback and old node-only LocalAppData artifacts only after new node verification.
$startup=Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\Startup\RemoteMCP-Node-Supervisor-"+$ExpectedDeviceId+".cmd")
if (Test-Path $startup) { Remove-Item -Force $startup }

$oldBase=Join-Path $env:LOCALAPPDATA "RemoteMCP"
foreach($p in @(
    $OldRuntimeDir,
    (Join-Path $oldBase "node-venv"),
    (Join-Path $oldBase "node.log"),
    (Join-Path $oldBase "node-error.log"),
    (Join-Path $oldBase ("node-watchdog-"+$ExpectedDeviceId+".log")),
    (Join-Path $oldBase "active-runtime.txt")
)){
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

$report=[ordered]@{
    status="REMOTEMCP_MACHINE1_ZERO_C_MIGRATION_PASS"
    device_id=$After.device_id
    key_fingerprint_sha256=$After.key_fingerprint_sha256
    route_generation=$After.route_generation
    execution_root=$RootDir
    infrastructure_root=$InfrastructureRoot
    runtime_dir=$RuntimeDir
    venv_dir=$VenvDir
    log_dir=$LogDir
    temp_dir=$TempDir
    cache_dir=$CacheDir
    control_dir=$ControlDir
    source_dir=$SourceDir
    old_runtime_removed=(-not (Test-Path $OldRuntimeDir))
    timestamp=(Get-Date).ToString("o")
}
$reportPath=Join-Path $ControlDir "zero-c-migration-report.json"
$report|ConvertTo-Json -Depth 5|Set-Content -Encoding UTF8 $reportPath
$report|ConvertTo-Json -Depth 5
