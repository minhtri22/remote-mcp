param(
    [int]$GatewayPort = 8099
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Read-only diagnostic. It intentionally creates no files or directories and
# performs no process/job mutation. Add-Type has no OutputAssembly.

$nativeSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class RemoteMCPJobDiagNative
{
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const int JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool IsProcessInJob(IntPtr processHandle, IntPtr jobHandle, out bool result);

    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool QueryInformationJobObject(
        IntPtr hJob,
        int infoClass,
        out JOBOBJECT_EXTENDED_LIMIT_INFORMATION info,
        uint infoLength,
        IntPtr returnLength
    );

    static ulong UPtr(UIntPtr value)
    {
        return UIntPtr.Size == 8 ? value.ToUInt64() : value.ToUInt32();
    }

    public static bool IsPidInAnyJob(int pid, out int error)
    {
        error = 0;
        IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, (uint)pid);
        if (h == IntPtr.Zero)
        {
            error = Marshal.GetLastWin32Error();
            return false;
        }
        try
        {
            bool inJob;
            if (!IsProcessInJob(h, IntPtr.Zero, out inJob))
            {
                error = Marshal.GetLastWin32Error();
                return false;
            }
            return inJob;
        }
        finally
        {
            CloseHandle(h);
        }
    }

    public static object QueryCurrentImmediateJob()
    {
        bool inJob;
        if (!IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out inJob))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "IsProcessInJob(current) failed");

        if (!inJob)
            return new {
                in_job = false,
                query_ok = true,
                limit_flags = 0u,
                process_memory_limit_bytes = 0ul,
                job_memory_limit_bytes = 0ul,
                peak_process_memory_used_bytes = 0ul,
                peak_job_memory_used_bytes = 0ul
            };

        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info;
        uint size = (uint)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        if (!QueryInformationJobObject(IntPtr.Zero, JobObjectExtendedLimitInformation, out info, size, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryInformationJobObject(NULL) failed");

        return new {
            in_job = true,
            query_ok = true,
            limit_flags = info.BasicLimitInformation.LimitFlags,
            process_memory_limit_bytes = UPtr(info.ProcessMemoryLimit),
            job_memory_limit_bytes = UPtr(info.JobMemoryLimit),
            peak_process_memory_used_bytes = UPtr(info.PeakProcessMemoryUsed),
            peak_job_memory_used_bytes = UPtr(info.PeakJobMemoryUsed)
        };
    }
}
'@

if (-not ("RemoteMCPJobDiagNative" -as [type])) {
    Add-Type -TypeDefinition $nativeSource -Language CSharp
}

function Decode-LimitFlags {
    param([uint32]$Flags)
    $map = [ordered]@{
        WORKING_SET = 0x00000001
        PROCESS_TIME = 0x00000002
        JOB_TIME = 0x00000004
        ACTIVE_PROCESS = 0x00000008
        AFFINITY = 0x00000010
        PRIORITY_CLASS = 0x00000020
        PRESERVE_JOB_TIME = 0x00000040
        SCHEDULING_CLASS = 0x00000080
        PROCESS_MEMORY = 0x00000100
        JOB_MEMORY = 0x00000200
        DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
        BREAKAWAY_OK = 0x00000800
        SILENT_BREAKAWAY_OK = 0x00001000
        KILL_ON_JOB_CLOSE = 0x00002000
    }
    $out = @()
    foreach ($kv in $map.GetEnumerator()) {
        if (($Flags -band [uint32]$kv.Value) -ne 0) { $out += $kv.Key }
    }
    return $out
}

function Safe-GetNamedJobInfo {
    param([Microsoft.Management.Infrastructure.CimInstance]$ProcessCim)

    $items = @()
    try {
        $jobs = @(Get-CimAssociatedInstance -InputObject $ProcessCim -Association Win32_NamedJobObjectProcess -ResultClassName Win32_NamedJobObject -ErrorAction Stop)
    } catch {
        return [ordered]@{ query_ok = $false; error = $_.Exception.Message; jobs = @() }
    }

    foreach ($job in $jobs) {
        $limits = @()
        try {
            $settings = @(Get-CimAssociatedInstance -InputObject $job -Association Win32_NamedJobObjectLimit -ResultClassName Win32_NamedJobObjectLimitSetting -ErrorAction Stop)
            foreach ($s in $settings) {
                $flags = [uint32]$s.LimitFlags
                $limits += [ordered]@{
                    setting_id = [string]$s.SettingID
                    limit_flags = $flags
                    limit_flag_names = @(Decode-LimitFlags $flags)
                    process_memory_limit_kib = [uint64]$s.ProcessMemoryLimit
                    job_memory_limit_kib = [uint64]$s.JobMemoryLimit
                    process_memory_limit_enabled = (($flags -band 0x00000100) -ne 0)
                    job_memory_limit_enabled = (($flags -band 0x00000200) -ne 0)
                    breakaway_ok = (($flags -band 0x00000800) -ne 0)
                    silent_breakaway_ok = (($flags -band 0x00001000) -ne 0)
                }
            }
        } catch {
            $limits += [ordered]@{ query_error = $_.Exception.Message }
        }

        $items += [ordered]@{
            collection_id = [string]$job.CollectionID
            caption = [string]$job.Caption
            limits = $limits
        }
    }

    return [ordered]@{ query_ok = $true; error = $null; jobs = $items }
}

Write-Host "[RemoteMCP job diagnostic] enumerating process tree ..."
$all = @(Get-CimInstance Win32_Process)
$byPid = @{}
$children = @{}
foreach ($p in $all) {
    $pidKey = [int]$p.ProcessId
    $byPid[$pidKey] = $p
    $ppid = [int]$p.ParentProcessId
    if (-not $children.ContainsKey($ppid)) { $children[$ppid] = @() }
    $children[$ppid] += $pidKey
}

$gatewayPid = $null
try {
    $listener = Get-NetTCPConnection -LocalPort $GatewayPort -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($listener) { $gatewayPid = [int]$listener.OwningProcess }
} catch {}

$roles = @{}
if ($gatewayPid) { $roles[$gatewayPid] = "gateway" }

$workerPids = @()
foreach ($p in $all) {
    $cmd = [string]$p.CommandLine
    $pid = [int]$p.ProcessId
    if ($cmd -match '(?i)remotemcp[.]node') {
        if (-not $roles.ContainsKey($pid)) { $roles[$pid] = "node" }
    }
    if ($cmd -match '(?i)remotemcp[.]durable[.]worker') {
        $roles[$pid] = "worker"
        $workerPids += $pid
    }
}

function Mark-Descendants {
    param([int]$RootPid)
    $stack = New-Object System.Collections.Stack
    $stack.Push($RootPid)
    while ($stack.Count -gt 0) {
        $current = [int]$stack.Pop()
        if ($children.ContainsKey($current)) {
            foreach ($child in @($children[$current])) {
                if (-not $roles.ContainsKey($child)) { $roles[$child] = "payload_descendant" }
                $stack.Push([int]$child)
            }
        }
    }
}
foreach ($wpid in $workerPids) { Mark-Descendants $wpid }

$nodePids = @($roles.Keys | Where-Object { $roles[$_] -eq "node" })
foreach ($npid in $nodePids) {
    if ($children.ContainsKey([int]$npid)) {
        foreach ($child in @($children[[int]$npid])) {
            if (-not $roles.ContainsKey($child)) { $roles[$child] = "node_child" }
        }
    }
}

Write-Host "[RemoteMCP job diagnostic] checking Job Object membership and named-job limits ..."
$targets = @()
foreach ($pid in @($roles.Keys | Sort-Object)) {
    if (-not $byPid.ContainsKey([int]$pid)) { continue }
    $p = $byPid[[int]$pid]
    $err = 0
    $inAnyJob = [RemoteMCPJobDiagNative]::IsPidInAnyJob([int]$pid, [ref]$err)
    $named = Safe-GetNamedJobInfo $p
    $g = Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue

    $targets += [ordered]@{
        role = [string]$roles[[int]$pid]
        pid = [int]$pid
        parent_pid = [int]$p.ParentProcessId
        name = [string]$p.Name
        command_line = [string]$p.CommandLine
        in_any_job = [bool]$inAnyJob
        is_process_in_job_win32_error = [int]$err
        private_memory_bytes = if ($g) { [int64]$g.PrivateMemorySize64 } else { $null }
        working_set_bytes = if ($g) { [int64]$g.WorkingSet64 } else { $null }
        named_job_information = $named
        unnamed_or_uninstrumented_job_possible = ([bool]$inAnyJob -and @($named.jobs).Count -eq 0)
    }
}

Write-Host "[RemoteMCP job diagnostic] querying immediate Job Object of this probe process ..."
$probeJob = $null
$probeJobError = $null
try {
    $probeJob = [RemoteMCPJobDiagNative]::QueryCurrentImmediateJob()
} catch {
    $probeJobError = $_.Exception.Message
}

$confirmedMemoryLimit = $false
$membershipUnresolvedLimit = $false
foreach ($t in $targets) {
    foreach ($j in @($t.named_job_information.jobs)) {
        foreach ($l in @($j.limits)) {
            if ($l.process_memory_limit_enabled -eq $true -or $l.job_memory_limit_enabled -eq $true) {
                $confirmedMemoryLimit = $true
            }
        }
    }
    if ($t.in_any_job -and @($t.named_job_information.jobs).Count -eq 0) {
        $membershipUnresolvedLimit = $true
    }
}

$classification = if ($confirmedMemoryLimit) {
    "NAMED_JOB_MEMORY_LIMIT_CONFIRMED"
} elseif ($membershipUnresolvedLimit) {
    "JOB_MEMBERSHIP_CONFIRMED_BUT_UNNAMED_LIMITS_UNRESOLVED"
} elseif (@($targets | Where-Object { $_.in_any_job }).Count -eq 0) {
    "NO_REMOTEMCP_TARGET_IN_WINDOWS_JOB"
} else {
    "JOB_MEMBERSHIP_PRESENT_NO_NAMED_MEMORY_LIMIT_OBSERVED"
}

$result = [ordered]@{
    observed_at = (Get-Date).ToString("o")
    mutation_free = $true
    temp_artifacts_created = $false
    temp_cleanup_required = $false
    gateway_port = $GatewayPort
    classification = $classification
    public_api_boundary = "IsProcessInJob can test arbitrary target membership. QueryInformationJobObject(NULL) can query only the calling process immediate job. WMI exposes named jobs only; unnamed-job limits remain unresolved without a job handle or in-process probe."
    probe_process_immediate_job = [ordered]@{
        result = $probeJob
        error = $probeJobError
        limit_flag_names = if ($probeJob -and $probeJob.in_job) { @(Decode-LimitFlags ([uint32]$probeJob.limit_flags)) } else { @() }
    }
    targets = $targets
}

Write-Host "[RemoteMCP job diagnostic] complete; no temporary files/directories were created."
$result | ConvertTo-Json -Depth 14
