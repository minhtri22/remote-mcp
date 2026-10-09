# RemoteMCP Out-of-Band Read-Only Inventory, v1.0
# This script NEVER starts/stops RemoteMCP nodes or scientific workers.
# Install mode creates one Scheduled Task under the CURRENT interactive account.
param([ValidateSet('Capture','Install')][string]$Mode='Capture')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$TaskName='RemoteMCP-OOB-ReadOnly-Inventory'
$Snapshot=Join-Path $PSScriptRoot 'process-inventory.json'
$Protected=@(9152,24528)

function Get-ResearchHint([string]$Value) {
    if ($Value -match '(?i)ru0_c_u3_executor[.]py') {return 'PROTECTED_CQG_RU'}
    if ($Value -match '(?i)(\\|/|^)SIX(\\|/|_|$)') {return 'SIX'}
    if ($Value -match '(?i)(\\|/|^)IRIS(\\|/|_|$)') {return 'IRIS'}
    if ($Value -match '(?i)Watch-RemoteMCP-Node[.]ps1') {return 'NODE_WATCHDOG'}
    if ($Value -match '(?i)remotemcp[.]node\s+run') {return 'NODE'}
    return ''
}
function Capture-ReadOnly {
    $now=[DateTimeOffset]::UtcNow
    $os=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $rows=@()
    foreach ($p in $os) {
        $id=[int]$p.ProcessId
        $cmd=[string]$p.CommandLine
        $hint=Get-ResearchHint $cmd
        if ($id -notin $Protected -and !$hint) {continue}
        $cpu=$null
        try {
            $gp=Get-Process -Id $id -ErrorAction Stop
            $cpu=[double]$gp.CPU
        } catch {}
        $hash=[Security.Cryptography.SHA256]::Create()
        try {
            $digest=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($cmd)))).Replace('-','').ToLowerInvariant()
        } finally { $hash.Dispose() }
        $rows+= [ordered]@{
            pid=$id
            parent_pid=[int]$p.ParentProcessId
            created_at=if ($p.CreationDate) {$p.CreationDate.ToString('o')} else {$null}
            executable=[string]$p.ExecutablePath
            cpu_seconds=$cpu
            role=$hint
            command_sha256=$digest
            protected=($id -in $Protected)
        }
    }
    $runtime=Join-Path $env:LOCALAPPDATA 'RemoteMCP\runtime'
    $manifest=[ordered]@{
        schema='remotemcp-oob-inventory-v1'
        observed_at_utc=$now.ToString('o')
        machine=$env:COMPUTERNAME
        runtime_dir=$runtime
        runtime_lock_exists=(Test-Path (Join-Path $runtime 'node.lock') -PathType Leaf)
        protected_pid_presence=@(
            foreach($id in $Protected) {
                [ordered]@{pid=$id;present=[bool](@($rows | Where-Object {$_.pid -eq $id}).Count)}
            }
        )
        process_count=$rows.Count
        process_rows=@($rows | Sort-Object pid)
        observation_only=$true
        restart_authorized=$false
        science_rerun_authorized=$false
    }
    $temp=Join-Path $PSScriptRoot ('.process-inventory.'+[guid]::NewGuid().ToString('N')+'.tmp')
    try {
        $payload=ConvertTo-Json -InputObject $manifest -Depth 8
        [IO.File]::WriteAllText($temp,$payload,[Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Snapshot -Force
    } finally {
        if (Test-Path $temp) {Remove-Item -LiteralPath $temp -Force}
    }
    Write-Host ('OOB_INVENTORY_CAPTURED='+$now.ToString('o'))
}
if ($Mode -eq 'Install') {
    $source=(Resolve-Path -LiteralPath $PSCommandPath).Path
    $currentUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$source+'" -Mode Capture')
    $trigger=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 365)
    $principal=New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Capture-ReadOnly
    Write-Host ('OOB_READ_ONLY_TASK_REGISTERED='+$TaskName)
} else {
    Capture-ReadOnly
}