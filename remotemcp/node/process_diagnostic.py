"""Bounded, fixed-command Windows process inventory for node diagnostics.

This is a high-priority *read-only diagnostic*, not an arbitrary remote shell.
The Python method launches only a constant PowerShell command (no user text
is interpolated) and never creates/cancels a RemoteMCP research job.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from typing import Callable


MAX_REQUESTED_PIDS = 16
MAX_RETURNED = 96
MAX_JSON_BYTES = 4_000_000
PROJECT_PATTERN = re.compile(r"(?i)(?:SIX|IRIS|ru0_c_u3_executor[.]py)")
SECRET_VALUE = re.compile(r"(?i)(--(?:token|secret|password|api-key|apikey|authorization)(?:=|\s+))([^\s\"']+)")
# The script is constant. PID input is filtered *after* the CIM snapshot.
_WINDOWS_PROCESS_SNAPSHOT_PS = r"""
$ErrorActionPreference = 'Stop'
$rows = @(
  Get-CimInstance Win32_Process -ErrorAction Stop |
    Where-Object { $_.Name -match '^(python|pythonw|node|pwsh|powershell)[.]exe$' } |
    ForEach-Object {
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        parent_pid = [int]$_.ParentProcessId
        created_at = if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { $null }
        executable = [string]$_.ExecutablePath
        command_line = [string]$_.CommandLine
      }
    }
)
ConvertTo-Json -InputObject $rows -Compress -Depth 4
"""


def _is_windows() -> bool:
    return os.name == "nt"


def inspect_windows_research_processes(
    pids: list[int] | tuple[int, ...] | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Return limited process candidates and *no* job execution permissions."""
    if not _is_windows():
        return {
            "schema": "remotemcp.node-process-diagnostic.v1",
            "status": "UNSUPPORTED_PLATFORM",
            "read_only": True, "mutation_performed": False,
            "science_rerun_authorized": False,
        }
    if not isinstance(pids, (list, tuple)):
        raise ValueError("INVALID_PID_ARGUMENT")
    if len(pids) > MAX_REQUESTED_PIDS or any(type(x) is not int or x <= 0 for x in pids):
        raise ValueError("INVALID_PID_ARGUMENT")
    requested = set(pids)
    completed = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         _WINDOWS_PROCESS_SNAPSHOT_PS],
        capture_output=True, text=True, timeout=12, check=False,
    )
    if completed.returncode != 0:
        return {
            "schema": "remotemcp.node-process-diagnostic.v1",
            "status": "CIM_QUERY_FAILED",
            "error_kind": "POWERSHELL_CIM_EXIT_NONZERO",
            "read_only": True, "mutation_performed": False,
            "science_rerun_authorized": False,
        }
    if len(completed.stdout.encode("utf-8")) > MAX_JSON_BYTES:
        return {
            "schema": "remotemcp.node-process-diagnostic.v1",
            "status": "OUTPUT_LIMIT_EXCEEDED",
            "read_only": True, "mutation_performed": False,
            "science_rerun_authorized": False,
        }
    raw = json.loads(completed.stdout or "[]")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("INVALID_CIM_RESPONSE")
    filtered = []
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        try:
            pid = int(obj["pid"])
            parent = int(obj.get("parent_pid") or 0)
        except (KeyError, ValueError, TypeError):
            continue
        command = str(obj.get("command_line") or "")
        if pid not in requested and not PROJECT_PATTERN.search(command):
            continue
        masked = SECRET_VALUE.sub(r"\1[REDACTED]", command)
        filtered.append({
            "pid": pid,
            "parent_pid": parent,
            "created_at": str(obj.get("created_at") or ""),
            "executable": str(obj.get("executable") or "")[:512],
            "command_preview": masked[:1500],
            "command_line_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "matched_explicit_pid": pid in requested,
            "matched_research_pattern": bool(PROJECT_PATTERN.search(command)),
        })
        if len(filtered) >= MAX_RETURNED:
            break
    returned = {r["pid"] for r in filtered}
    return {
        "schema": "remotemcp.node-process-diagnostic.v1",
        "status": "READ_ONLY_SNAPSHOT",
        "processes": filtered,
        "requested_pid_not_seen": sorted(requested - returned),
        "truncated": len(filtered) >= MAX_RETURNED,
        "read_only": True,
        "mutation_performed": False,
        "science_rerun_authorized": False,
        "process_kill_authorized": False,
        "command_execution_authorized": False,
    }
