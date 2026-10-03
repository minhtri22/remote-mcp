param(
    [Parameter(Mandatory=$true)]
    [string]$RuntimeDir,
    [string]$SourceDir = $PSScriptRoot,
    [switch]$StartNow,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"

$RuntimeDir = (Resolve-Path $RuntimeDir).Path
$SourceDir = (Resolve-Path $SourceDir).Path
$Watchdog = Join-Path $SourceDir "Watch-RemoteMCP-Node.ps1"

if (-not (Test-Path $Watchdog)) {
    throw "RemoteMCP node watchdog not found: $Watchdog"
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

$TaskName = "RemoteMCP-Node-Supervisor-" + ([string]$d.device_id)
$IdentitySnapshot = [ordered]@{
    device_id = [string]$d.device_id
    key_fingerprint_sha256 = [string]$d.key_fingerprint_sha256
    route_generation = [string]$d.route_generation
}

$arg = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RuntimeDir "{1}" -SourceDir "{2}"' -f $Watchdog,$RuntimeDir,$SourceDir
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Write-Host "Static deployment candidate:"
Write-Host ("Task       : {0}" -f $TaskName)
Write-Host ("Runtime    : {0}" -f $RuntimeDir)
Write-Host ("Device id  : {0}" -f $IdentitySnapshot.device_id)
Write-Host ("Generation : {0}" -f $IdentitySnapshot.route_generation)

if ($PlanOnly) {
    Write-Host "REMOTEMCP_NODE_SUPERVISOR_PLAN_ONLY=PASS"
    return
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed per-user RemoteMCP node supervisor scheduled task."

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Node supervisor scheduled task started."
}
