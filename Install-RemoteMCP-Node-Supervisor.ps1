param(
    [Parameter(Mandatory=$true)]
    [string]$RuntimeDir,
    [Parameter(Mandatory=$true)]
    [string]$RootDir,
    [string]$SourceDir = $PSScriptRoot,
    [Parameter(Mandatory=$true)]
    [string]$NodeSourceDir,
    [string]$VenvDir = "",
    [string]$LogDir = "",
    [string]$TempDir = "",
    [string]$CacheDir = "",
    [string]$ControlDir = "",
    [switch]$ZeroC,
    [switch]$StartNow,
    [switch]$PlanOnly,
    [ValidateSet("Auto","ScheduledTask","RegistryRun","Startup")]
    [string]$PersistenceMode = "Auto"
)

$ErrorActionPreference = "Stop"

function Assert-NonOsPath {
    param([string]$Path,[string]$Label)
    if (-not $Path) { throw "$Label is required." }
    if (
        -not [System.IO.Path]::IsPathRooted($Path) -or
        $Path -match '^[A-Za-z]:[^\\/]'
    ) {
        throw "$Label must be an absolute path."
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = [System.IO.Path]::GetPathRoot($full)
    $osDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot)
    if ($drive -and $osDrive -and ($drive.TrimEnd('\') -ieq $osDrive.TrimEnd('\'))) {
        throw "OS_DRIVE_REMOTEMCP_INFRA_FORBIDDEN: $Label cannot be on the Windows OS drive."
    }
    return $full
}

$RuntimeDir = (Resolve-Path $RuntimeDir).Path
$RootDir = (Resolve-Path $RootDir).Path
$RootDrive = [System.IO.Path]::GetPathRoot($RootDir)
$OsDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot)
if ($RootDrive -and $OsDrive -and ($RootDrive.TrimEnd('\') -ieq $OsDrive.TrimEnd('\'))) {
    throw "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN: supervisor root cannot be on the Windows OS drive."
}
$SourceDir = (Resolve-Path $SourceDir).Path
$NodeSourceDir = (Resolve-Path $NodeSourceDir).Path
if ($ZeroC) {
    $RuntimeDir = Assert-NonOsPath -Path $RuntimeDir -Label "RuntimeDir"
    $VenvDir = Assert-NonOsPath -Path $VenvDir -Label "VenvDir"
    $LogDir = Assert-NonOsPath -Path $LogDir -Label "LogDir"
    $TempDir = Assert-NonOsPath -Path $TempDir -Label "TempDir"
    $CacheDir = Assert-NonOsPath -Path $CacheDir -Label "CacheDir"
    $ControlDir = Assert-NonOsPath -Path $ControlDir -Label "ControlDir"
}
$Watchdog = Join-Path $SourceDir "Watch-RemoteMCP-Node.ps1"

if (-not (Test-Path $Watchdog)) {
    throw "RemoteMCP node watchdog not found: $Watchdog"
}
if (-not (Test-Path (Join-Path $NodeSourceDir "remotemcp\node\__main__.py"))) {
    throw "RemoteMCP node source not found under: $NodeSourceDir"
}

$DeviceFile = Join-Path $RuntimeDir "device.json"
$KeyFile = Join-Path $RuntimeDir "device-ed25519.pem"
if (-not (Test-Path $DeviceFile) -or -not (Test-Path $KeyFile)) {
    throw "The requested runtime does not contain a paired RemoteMCP device identity: $RuntimeDir"
}

$d = Get-Content $DeviceFile -Raw | ConvertFrom-Json
if (-not $d.device_id -or -not $d.key_fingerprint_sha256 -or $null -eq $d.route_generation) {
    throw "The requested runtime identity is incomplete: $RuntimeDir"
}

$DeviceId = [string]$d.device_id
$TaskName = "RemoteMCP-Node-Supervisor-" + $DeviceId
$IdentitySnapshot = [ordered]@{
    device_id = $DeviceId
    key_fingerprint_sha256 = [string]$d.key_fingerprint_sha256
    route_generation = [string]$d.route_generation
}

$arg = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -RuntimeDir "{1}" -RootDir "{2}" -SourceDir "{3}" -NodeSourceDir "{4}"' -f $Watchdog,$RuntimeDir,$RootDir,$SourceDir,$NodeSourceDir
if ($VenvDir) { $arg += ' -VenvDir "' + $VenvDir + '"' }
if ($LogDir) { $arg += ' -LogDir "' + $LogDir + '"' }
if ($TempDir) { $arg += ' -TempDir "' + $TempDir + '"' }
if ($CacheDir) { $arg += ' -CacheDir "' + $CacheDir + '"' }
if ($ControlDir) { $arg += ' -ControlDir "' + $ControlDir + '"' }
if ($ZeroC) { $arg += ' -ZeroC' }

function Get-NodeProcesses([string]$Runtime) {
    $runtimeEscaped = [Regex]::Escape($Runtime)
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "remotemcp\.node" -and
        $_.CommandLine -match "--runtime-dir" -and
        $_.CommandLine -match $runtimeEscaped
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

function Get-WatchdogProcess {
    $watchdogEscaped = [Regex]::Escape($Watchdog)
    $runtimeEscaped = [Regex]::Escape($RuntimeDir)
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "powershell" -and
        $_.CommandLine -match $watchdogEscaped -and
        $_.CommandLine -match $runtimeEscaped
    })
}

function Start-WatchdogIfNeeded {
    $existing = @(Get-WatchdogProcess)
    if ($existing.Count -gt 0) {
        Write-Host ("Node supervisor watchdog already running PID(s): {0}" -f (($existing.ProcessId) -join ","))
        return
    }

    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $arg -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    if ($p.HasExited) {
        throw "Node supervisor watchdog exited during startup with code $($p.ExitCode)"
    }

    $after = @(Get-WatchdogProcess)
    if ($after.Count -eq 0) {
        throw "Node supervisor watchdog process could not be verified after start"
    }

    Write-Host ("Node supervisor watchdog started PID(s): {0}" -f (($after.ProcessId) -join ","))
}

function Install-StartupPersistence {
    if (-not $env:APPDATA) {
        throw "APPDATA is not available for per-user Startup persistence"
    }

    $StartupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null
    $StartupFile = Join-Path $StartupDir ($TaskName + ".cmd")

    $cmd = '@echo off' + [Environment]::NewLine +
        'start "" /min powershell.exe ' + $arg + [Environment]::NewLine
    [IO.File]::WriteAllText($StartupFile,$cmd,(New-Object Text.UTF8Encoding($false)))

    if (-not (Test-Path $StartupFile)) {
        throw "Per-user Startup persistence file was not created: $StartupFile"
    }

    Write-Host ("Persistence : Startup")
    Write-Host ("Startup file: {0}" -f $StartupFile)
    return $StartupFile
}

function Install-RegistryRunPersistence {
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (-not (Test-Path $RunKey)) {
        New-Item -Path $RunKey -Force | Out-Null
    }
    $ValueName = $TaskName
    $Value = 'powershell.exe ' + $arg
    New-ItemProperty -Path $RunKey -Name $ValueName -Value $Value -PropertyType String -Force | Out-Null
    $verify = (Get-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction Stop).$ValueName
    if ([string]$verify -ne [string]$Value) {
        throw "Registry Run persistence verification failed: $ValueName"
    }
    Write-Host "Persistence : RegistryRun"
    Write-Host ("Registry value: HKCU\Software\Microsoft\Windows\CurrentVersion\Run\{0}" -f $ValueName)
    return $ValueName
}

function Install-ScheduledTaskPersistence {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null

    $registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($null -eq $registered) {
        throw "Scheduled Task registration could not be verified: $TaskName"
    }

    Write-Host ("Persistence : ScheduledTask")
    Write-Host ("Task        : {0}" -f $TaskName)
}

Write-Host "Static deployment candidate:"
Write-Host ("Runtime    : {0}" -f $RuntimeDir)
Write-Host ("Root       : {0}" -f $RootDir)
Write-Host ("Device id  : {0}" -f $IdentitySnapshot.device_id)
Write-Host ("Generation : {0}" -f $IdentitySnapshot.route_generation)
Write-Host ("Requested persistence: {0}" -f $PersistenceMode)

if ($PlanOnly) {
    Write-Host "REMOTEMCP_NODE_SUPERVISOR_PLAN_ONLY=PASS"
    return
}

$nodeProcesses = @(Get-NodeProcesses -Runtime $RuntimeDir)
$nodeRoots = @(Get-LogicalNodeRoots -Processes $nodeProcesses)
if ($nodeRoots.Count -ne 1) {
    throw (
        "Supervisor deployment requires exactly one logical node root for the exact runtime; " +
        "found roots=" + $nodeRoots.Count + ", raw_processes=" + $nodeProcesses.Count +
        ". Resolve node-process ambiguity before installation."
    )
}
Write-Host (
    "Logical node process group: root_pid={0}, raw_processes={1}" -f
    $nodeRoots[0].ProcessId,$nodeProcesses.Count
)

$InstalledMode = $null
if ($PersistenceMode -in @("Auto","ScheduledTask")) {
    try {
        Install-ScheduledTaskPersistence
        $InstalledMode = "ScheduledTask"
    } catch {
        if ($PersistenceMode -eq "ScheduledTask") {
            throw
        }
        Write-Warning ("Scheduled Task persistence unavailable; falling back to per-user Startup: " + $_.Exception.Message)
    }
}

if (-not $InstalledMode -and ($PersistenceMode -eq "RegistryRun" -or ($PersistenceMode -eq "Auto" -and $ZeroC))) {
    Install-RegistryRunPersistence | Out-Null
    $InstalledMode = "RegistryRun"
}

if (-not $InstalledMode) {
    if ($ZeroC) {
        throw "ZERO_C_PERSISTENCE_UNAVAILABLE: Startup-folder persistence is forbidden in ZeroC mode."
    }
    Install-StartupPersistence | Out-Null
    $InstalledMode = "Startup"
}

if ($StartNow) {
    Start-WatchdogIfNeeded
}

Write-Host "REMOTEMCP_NODE_SUPERVISOR_INSTALL=PASS"
Write-Host ("Installed persistence mode: {0}" -f $InstalledMode)
