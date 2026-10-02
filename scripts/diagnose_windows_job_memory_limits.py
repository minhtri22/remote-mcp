from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes

if os.name != "nt":
    raise SystemExit("Windows-only diagnostic")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

LIMIT_FLAG_NAMES = {
    0x00000001: "WORKING_SET",
    0x00000002: "PROCESS_TIME",
    0x00000004: "JOB_TIME",
    0x00000008: "ACTIVE_PROCESS",
    0x00000010: "AFFINITY",
    0x00000020: "PRIORITY_CLASS",
    0x00000040: "PRESERVE_JOB_TIME",
    0x00000080: "SCHEDULING_CLASS",
    0x00000100: "PROCESS_MEMORY",
    0x00000200: "JOB_MEMORY",
    0x00000400: "DIE_ON_UNHANDLED_EXCEPTION",
    0x00000800: "BREAKAWAY_OK",
    0x00001000: "SILENT_BREAKAWAY_OK",
    0x00002000: "KILL_ON_JOB_CLOSE",
}


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL


def decode_flags(flags: int) -> list[str]:
    return [name for bit, name in LIMIT_FLAG_NAMES.items() if flags & bit]


def is_process_in_any_job(pid: int) -> dict:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return {
            "query_ok": False,
            "in_any_job": None,
            "win32_error": ctypes.get_last_error(),
        }
    try:
        result = wintypes.BOOL()
        ok = kernel32.IsProcessInJob(handle, None, ctypes.byref(result))
        if not ok:
            return {
                "query_ok": False,
                "in_any_job": None,
                "win32_error": ctypes.get_last_error(),
            }
        return {
            "query_ok": True,
            "in_any_job": bool(result.value),
            "win32_error": 0,
        }
    finally:
        kernel32.CloseHandle(handle)


def query_current_immediate_job() -> dict:
    current = kernel32.GetCurrentProcess()
    membership = wintypes.BOOL()
    if not kernel32.IsProcessInJob(current, None, ctypes.byref(membership)):
        return {
            "query_ok": False,
            "in_job": None,
            "win32_error": ctypes.get_last_error(),
        }
    if not membership.value:
        return {
            "query_ok": True,
            "in_job": False,
            "win32_error": 0,
            "limit_flags": 0,
            "limit_flag_names": [],
        }

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    retlen = wintypes.DWORD()
    ok = kernel32.QueryInformationJobObject(
        None,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(retlen),
    )
    if not ok:
        return {
            "query_ok": False,
            "in_job": True,
            "win32_error": ctypes.get_last_error(),
        }

    flags = int(info.BasicLimitInformation.LimitFlags)
    return {
        "query_ok": True,
        "in_job": True,
        "win32_error": 0,
        "limit_flags": flags,
        "limit_flag_names": decode_flags(flags),
        "process_memory_limit_enabled": bool(flags & JOB_OBJECT_LIMIT_PROCESS_MEMORY),
        "job_memory_limit_enabled": bool(flags & JOB_OBJECT_LIMIT_JOB_MEMORY),
        "breakaway_ok": bool(flags & JOB_OBJECT_LIMIT_BREAKAWAY_OK),
        "silent_breakaway_ok": bool(flags & JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK),
        "process_memory_limit_bytes": int(info.ProcessMemoryLimit),
        "job_memory_limit_bytes": int(info.JobMemoryLimit),
        "peak_process_memory_used_bytes": int(info.PeakProcessMemoryUsed),
        "peak_job_memory_used_bytes": int(info.PeakJobMemoryUsed),
    }


def run_powershell(script: str) -> object:
    cp = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"PowerShell read-only query failed rc={cp.returncode}: {cp.stderr[-2000:]}"
        )
    text_out = cp.stdout.strip()
    if not text_out:
        return None
    return json.loads(text_out)


PROCESS_SNAPSHOT_PS = r"""
$ProgressPreference='SilentlyContinue'
$all=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine)
$listenerPid=$null
try {
  $l=Get-NetTCPConnection -LocalPort 8099 -State Listen -ErrorAction Stop | Select-Object -First 1
  if($l){$listenerPid=[int]$l.OwningProcess}
} catch {}
[ordered]@{gateway_pid=$listenerPid; processes=$all} | ConvertTo-Json -Depth 5 -Compress
"""


def discover_targets(snapshot: dict) -> list[dict]:
    rows = snapshot.get("processes") or []
    if isinstance(rows, dict):
        rows = [rows]

    by_pid = {int(p["ProcessId"]): p for p in rows if p.get("ProcessId") is not None}
    children: dict[int, list[int]] = {}
    for p in rows:
        pid = int(p["ProcessId"])
        ppid = int(p.get("ParentProcessId") or 0)
        children.setdefault(ppid, []).append(pid)

    roles: dict[int, str] = {}
    gateway_pid = snapshot.get("gateway_pid")
    if gateway_pid:
        roles[int(gateway_pid)] = "gateway"

    worker_pids: list[int] = []
    for p in rows:
        pid = int(p["ProcessId"])
        cmd = str(p.get("CommandLine") or "").lower()
        if "remotemcp.node" in cmd and pid not in roles:
            roles[pid] = "node"
        if "remotemcp.durable.worker" in cmd:
            roles[pid] = "worker"
            worker_pids.append(pid)

    for root in worker_pids:
        stack = [root]
        seen = {root}
        while stack:
            parent = stack.pop()
            for child in children.get(parent, []):
                if child in seen:
                    continue
                seen.add(child)
                roles.setdefault(child, "payload_descendant")
                stack.append(child)

    for pid, role in list(roles.items()):
        if role != "node":
            continue
        for child in children.get(pid, []):
            roles.setdefault(child, "node_child")

    result = []
    for pid in sorted(roles):
        p = by_pid.get(pid)
        if not p:
            continue
        result.append(
            {
                "role": roles[pid],
                "pid": pid,
                "parent_pid": int(p.get("ParentProcessId") or 0),
                "name": p.get("Name"),
                "command_line": p.get("CommandLine"),
            }
        )
    return result


def query_named_job_info(pids: list[int]) -> dict[int, dict]:
    if not pids:
        return {}

    pid_csv = ",".join(str(int(pid)) for pid in pids)
    ps = rf"""
$ProgressPreference='SilentlyContinue'
$pids=@({pid_csv})
$out=@()
foreach($pidValue in $pids){{
  $entry=[ordered]@{{pid=[int]$pidValue; query_ok=$true; error=$null; jobs=@()}}
  try {{
    $proc=Get-CimInstance Win32_Process -Filter ("ProcessId="+$pidValue) -ErrorAction Stop
    $jobs=@(Get-CimAssociatedInstance -InputObject $proc -Association Win32_NamedJobObjectProcess -ResultClassName Win32_NamedJobObject -ErrorAction Stop)
    foreach($job in $jobs){{
      $jobEntry=[ordered]@{{collection_id=[string]$job.CollectionID; caption=[string]$job.Caption; limits=@()}}
      try {{
        $settings=@(Get-CimAssociatedInstance -InputObject $job -Association Win32_NamedJobObjectLimit -ResultClassName Win32_NamedJobObjectLimitSetting -ErrorAction Stop)
        foreach($s in $settings){{
          $flags=[uint32]$s.LimitFlags
          $jobEntry.limits += [ordered]@{{
            setting_id=[string]$s.SettingID
            limit_flags=$flags
            process_memory_limit_kib=[uint64]$s.ProcessMemoryLimit
            job_memory_limit_kib=[uint64]$s.JobMemoryLimit
            process_memory_limit_enabled=(($flags -band 0x100) -ne 0)
            job_memory_limit_enabled=(($flags -band 0x200) -ne 0)
            breakaway_ok=(($flags -band 0x800) -ne 0)
            silent_breakaway_ok=(($flags -band 0x1000) -ne 0)
          }}
        }}
      }} catch {{
        $jobEntry.limits += [ordered]@{{query_error=$_.Exception.Message}}
      }}
      $entry.jobs += $jobEntry
    }}
  }} catch {{
    $entry.query_ok=$false
    $entry.error=$_.Exception.Message
  }}
  $out += $entry
}}
$out | ConvertTo-Json -Depth 10 -Compress
"""
    raw = run_powershell(ps)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        raw = [raw]
    return {int(row["pid"]): row for row in raw}


def main() -> int:
    print("[RemoteMCP job diagnostic] process discovery ...", file=sys.stderr, flush=True)
    snapshot = run_powershell(PROCESS_SNAPSHOT_PS)
    targets = discover_targets(snapshot or {})

    print("[RemoteMCP job diagnostic] Win32 Job membership ...", file=sys.stderr, flush=True)
    for target in targets:
        target["job_membership"] = is_process_in_any_job(int(target["pid"]))

    print("[RemoteMCP job diagnostic] named Job Object limits ...", file=sys.stderr, flush=True)
    named = query_named_job_info([int(t["pid"]) for t in targets])
    for target in targets:
        info = named.get(
            int(target["pid"]),
            {"query_ok": False, "error": "no named-job query result", "jobs": []},
        )
        for job in info.get("jobs") or []:
            for limit in job.get("limits") or []:
                flags = int(limit.get("limit_flags") or 0)
                limit["limit_flag_names"] = decode_flags(flags)
        target["named_job_information"] = info
        in_job = target["job_membership"].get("in_any_job")
        target["unnamed_or_uninstrumented_job_possible"] = bool(
            in_job and not (info.get("jobs") or [])
        )

    print("[RemoteMCP job diagnostic] probe-process immediate job ...", file=sys.stderr, flush=True)
    probe_job = query_current_immediate_job()

    named_memory_limit = False
    unresolved_unnamed = False
    any_in_job = False
    for target in targets:
        membership = target["job_membership"]
        if membership.get("in_any_job") is True:
            any_in_job = True
        if target["unnamed_or_uninstrumented_job_possible"]:
            unresolved_unnamed = True
        for job in target["named_job_information"].get("jobs") or []:
            for limit in job.get("limits") or []:
                if (
                    limit.get("process_memory_limit_enabled") is True
                    or limit.get("job_memory_limit_enabled") is True
                ):
                    named_memory_limit = True

    if named_memory_limit:
        classification = "NAMED_JOB_MEMORY_LIMIT_CONFIRMED"
    elif unresolved_unnamed:
        classification = "JOB_MEMBERSHIP_CONFIRMED_BUT_UNNAMED_LIMITS_UNRESOLVED"
    elif not any_in_job:
        classification = "NO_REMOTEMCP_TARGET_IN_WINDOWS_JOB"
    else:
        classification = "JOB_MEMBERSHIP_PRESENT_NO_NAMED_MEMORY_LIMIT_OBSERVED"

    result = {
        "mutation_free": True,
        "temp_artifacts_created": False,
        "temp_cleanup_required": False,
        "classification": classification,
        "public_api_boundary": (
            "IsProcessInJob can test arbitrary process membership. "
            "QueryInformationJobObject(NULL) queries only the calling process immediate job. "
            "The Windows WMI Job Object provider exposes named jobs only; unnamed-job limits "
            "remain unresolved from an external read-only probe without a job handle."
        ),
        "probe_process_immediate_job": probe_job,
        "targets": targets,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "[RemoteMCP job diagnostic] complete; no temporary files/directories were created.",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
