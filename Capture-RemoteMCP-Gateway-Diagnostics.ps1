param(
    [int]$Port = 8099,
    [int]$SampleSeconds = 2,
    [int]$GatewayLogTail = 80,
    [int]$GatewayErrorTail = 120
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

function Get-ProcessSnapshot {
    param([int]$ProcessId)

    $p = Get-Process -Id $ProcessId -ErrorAction Stop
    return [ordered]@{
        pid = [int]$p.Id
        process_name = [string]$p.ProcessName
        cpu_seconds = [double]$p.CPU
        thread_count = [int]$p.Threads.Count
        handle_count = [int]$p.Handles
        working_set_bytes = [int64]$p.WorkingSet64
        private_memory_bytes = [int64]$p.PrivateMemorySize64
        virtual_memory_bytes = [int64]$p.VirtualMemorySize64
        start_time = $p.StartTime.ToString("o")
    }
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1

if (-not $listener) {
    [ordered]@{
        observed_at = (Get-Date).ToString("o")
        mutation_free = $true
        port = $Port
        listener = $false
        classification = "NO_LISTENER"
    } | ConvertTo-Json -Depth 8
    exit 0
}

$pidToCheck = [int]$listener.OwningProcess
$cim = Get-CimInstance Win32_Process -Filter "ProcessId=$pidToCheck" -ErrorAction SilentlyContinue

$first = Get-ProcessSnapshot -ProcessId $pidToCheck
Start-Sleep -Seconds ([Math]::Max(1,$SampleSeconds))
$second = Get-ProcessSnapshot -ProcessId $pidToCheck

$cpuDelta = [Math]::Max(0.0,([double]$second.cpu_seconds - [double]$first.cpu_seconds))
$logicalCpu = [Math]::Max(1,[Environment]::ProcessorCount)
$sampleWindow = [Math]::Max(1,$SampleSeconds)
$cpuPercentOneCore = ($cpuDelta / $sampleWindow) * 100.0
$cpuPercentMachine = $cpuPercentOneCore / $logicalCpu

$connections = @(Get-NetTCPConnection -OwningProcess $pidToCheck -ErrorAction SilentlyContinue)
$stateCounts = @{}
foreach ($g in ($connections | Group-Object State)) {
    $stateCounts[[string]$g.Name] = [int]$g.Count
}

$base = Join-Path $env:LOCALAPPDATA "RemoteMCP"
$stdoutPath = Join-Path $base "gateway.log"
$stderrPath = Join-Path $base "gateway-error.log"

$stdoutInfo = Get-Item $stdoutPath -ErrorAction SilentlyContinue
$stderrInfo = Get-Item $stderrPath -ErrorAction SilentlyContinue

$stdoutTail = @()
if ($stdoutInfo) {
    $stdoutTail = @(Get-Content $stdoutPath -Tail $GatewayLogTail -ErrorAction SilentlyContinue)
}

$stderrTail = @()
if ($stderrInfo) {
    $stderrTail = @(Get-Content $stderrPath -Tail $GatewayErrorTail -ErrorAction SilentlyContinue)
}

$classification = "REMOTEMCP_PROCESS_PRESENT"
if (-not $cim -or -not $cim.CommandLine -or $cim.CommandLine -notmatch "server\.py") {
    $classification = "PORT_OWNERSHIP_CONFLICT_OR_UNKNOWN_PROCESS"
}

[ordered]@{
    observed_at = (Get-Date).ToString("o")
    mutation_free = $true
    port = $Port
    listener = $true
    classification = $classification
    process = [ordered]@{
        pid = $pidToCheck
        executable_path = if ($cim) { $cim.ExecutablePath } else { $null }
        command_line = if ($cim) { $cim.CommandLine } else { $null }
        creation_date = if ($cim) { [string]$cim.CreationDate } else { $null }
        first = $first
        second = $second
        sample_seconds = $sampleWindow
        cpu_delta_seconds = [Math]::Round($cpuDelta,6)
        cpu_percent_one_core_equivalent = [Math]::Round($cpuPercentOneCore,2)
        cpu_percent_machine_equivalent = [Math]::Round($cpuPercentMachine,2)
        logical_processors = $logicalCpu
    }
    tcp = [ordered]@{
        total_connections = [int]$connections.Count
        state_counts = $stateCounts
    }
    logs = [ordered]@{
        stdout = [ordered]@{
            path = $stdoutPath
            exists = [bool]$stdoutInfo
            size_bytes = if ($stdoutInfo) { [int64]$stdoutInfo.Length } else { 0 }
            last_write_time = if ($stdoutInfo) { $stdoutInfo.LastWriteTime.ToString("o") } else { $null }
            tail = $stdoutTail
        }
        stderr = [ordered]@{
            path = $stderrPath
            exists = [bool]$stderrInfo
            size_bytes = if ($stderrInfo) { [int64]$stderrInfo.Length } else { 0 }
            last_write_time = if ($stderrInfo) { $stderrInfo.LastWriteTime.ToString("o") } else { $null }
            tail = $stderrTail
        }
    }
} | ConvertTo-Json -Depth 12
