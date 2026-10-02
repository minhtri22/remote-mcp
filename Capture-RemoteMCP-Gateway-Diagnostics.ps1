param(
    [int]$Port = 8099,
    [int]$SampleSeconds = 2,
    [int]$GatewayLogTail = 80,
    [int]$GatewayErrorTail = 120
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$result = [ordered]@{
    observed_at = (Get-Date).ToString("o")
    mutation_free = $true
    port = $Port
    stages = [ordered]@{}
}

function Stage {
    param([string]$Name,[scriptblock]$Body)
    Write-Host "[RemoteMCP diagnostic] $Name ..."
    $sw=[System.Diagnostics.Stopwatch]::StartNew()
    try {
        $value=& $Body
        $sw.Stop()
        $result.stages[$Name]=[ordered]@{
            ok=$true
            elapsed_ms=[int]$sw.ElapsedMilliseconds
            error=$null
        }
        return $value
    } catch {
        $sw.Stop()
        $result.stages[$Name]=[ordered]@{
            ok=$false
            elapsed_ms=[int]$sw.ElapsedMilliseconds
            error=$_.Exception.Message
        }
        return $null
    }
}

function Get-ProcessSnapshot {
    param([int]$ProcessId)
    $p=Get-Process -Id $ProcessId -ErrorAction Stop
    return [ordered]@{
        pid=[int]$p.Id
        process_name=[string]$p.ProcessName
        cpu_seconds=[double]$p.CPU
        thread_count=[int]$p.Threads.Count
        handle_count=[int]$p.Handles
        working_set_bytes=[int64]$p.WorkingSet64
        private_memory_bytes=[int64]$p.PrivateMemorySize64
        virtual_memory_bytes=[int64]$p.VirtualMemorySize64
        start_time=$p.StartTime.ToString("o")
    }
}

$listener=Stage "listener" {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
        Select-Object -First 1
}

if (-not $listener) {
    $result.listener=$false
    $result.classification="NO_LISTENER_OR_LISTENER_QUERY_FAILED"
    Write-Output ($result | ConvertTo-Json -Depth 12)
    return
}

$result.listener=$true
$pidToCheck=[int]$listener.OwningProcess
$result.pid=$pidToCheck

$cim=Stage "process_identity" {
    Get-CimInstance Win32_Process -Filter "ProcessId=$pidToCheck" -ErrorAction Stop
}

$first=Stage "process_sample_1" {
    Get-ProcessSnapshot -ProcessId $pidToCheck
}

Write-Host "[RemoteMCP diagnostic] sampling CPU for $([Math]::Max(1,$SampleSeconds))s ..."
Start-Sleep -Seconds ([Math]::Max(1,$SampleSeconds))

$second=Stage "process_sample_2" {
    Get-ProcessSnapshot -ProcessId $pidToCheck
}

$cpuDelta=0.0
$sampleWindow=[Math]::Max(1,$SampleSeconds)
$logicalCpu=[Math]::Max(1,[Environment]::ProcessorCount)
if ($first -and $second) {
    $cpuDelta=[Math]::Max(0.0,([double]$second.cpu_seconds-[double]$first.cpu_seconds))
}

$connections=Stage "tcp_connections" {
    @(Get-NetTCPConnection -OwningProcess $pidToCheck -ErrorAction Stop)
}
if ($null -eq $connections) { $connections=@() }

$stateCounts=@{}
foreach ($g in ($connections | Group-Object State)) {
    $stateCounts[[string]$g.Name]=[int]$g.Count
}

$base=Join-Path $env:LOCALAPPDATA "RemoteMCP"
$stdoutPath=Join-Path $base "gateway.log"
$stderrPath=Join-Path $base "gateway-error.log"

$stdoutInfo=Get-Item $stdoutPath -ErrorAction SilentlyContinue
$stderrInfo=Get-Item $stderrPath -ErrorAction SilentlyContinue

$stdoutTail=Stage "stdout_tail" {
    if ($stdoutInfo) {
        @(Get-Content $stdoutPath -Tail $GatewayLogTail -ErrorAction Stop)
    } else {
        @()
    }
}
if ($null -eq $stdoutTail) { $stdoutTail=@() }

$stderrTail=Stage "stderr_tail" {
    if ($stderrInfo) {
        @(Get-Content $stderrPath -Tail $GatewayErrorTail -ErrorAction Stop)
    } else {
        @()
    }
}
if ($null -eq $stderrTail) { $stderrTail=@() }

$classification="REMOTEMCP_PROCESS_PRESENT"
if (-not $cim -or -not $cim.CommandLine -or $cim.CommandLine -notmatch "server\.py") {
    $classification="PORT_OWNERSHIP_CONFLICT_OR_UNKNOWN_PROCESS"
}

$result.classification=$classification
$result.process=[ordered]@{
    pid=$pidToCheck
    executable_path=if ($cim) { $cim.ExecutablePath } else { $null }
    command_line=if ($cim) { $cim.CommandLine } else { $null }
    creation_date=if ($cim) { [string]$cim.CreationDate } else { $null }
    first=$first
    second=$second
    sample_seconds=$sampleWindow
    cpu_delta_seconds=[Math]::Round($cpuDelta,6)
    cpu_percent_one_core_equivalent=[Math]::Round(($cpuDelta/$sampleWindow)*100.0,2)
    cpu_percent_machine_equivalent=[Math]::Round((($cpuDelta/$sampleWindow)*100.0)/$logicalCpu,2)
    logical_processors=$logicalCpu
}
$result.tcp=[ordered]@{
    total_connections=[int]$connections.Count
    state_counts=$stateCounts
}
$result.logs=[ordered]@{
    stdout=[ordered]@{
        path=$stdoutPath
        exists=[bool]$stdoutInfo
        size_bytes=if ($stdoutInfo) { [int64]$stdoutInfo.Length } else { 0 }
        last_write_time=if ($stdoutInfo) { $stdoutInfo.LastWriteTime.ToString("o") } else { $null }
        tail=$stdoutTail
    }
    stderr=[ordered]@{
        path=$stderrPath
        exists=[bool]$stderrInfo
        size_bytes=if ($stderrInfo) { [int64]$stderrInfo.Length } else { 0 }
        last_write_time=if ($stderrInfo) { $stderrInfo.LastWriteTime.ToString("o") } else { $null }
        tail=$stderrTail
    }
}

Write-Host "[RemoteMCP diagnostic] complete"
Write-Output ($result | ConvertTo-Json -Depth 12)
