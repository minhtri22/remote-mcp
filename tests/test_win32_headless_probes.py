"""Windows subprocess probes must not open a visible terminal window."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

from remotemcp.node import process_safety as ps
from remotemcp.node import process_diagnostic as pd


def test_cim_process_safety_launch_is_windowless(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps([{
                "pid": 1,
                "ppid": 0,
                "name": "python.exe",
                "command_line": "python.exe -m remotemcp.node run",
                "created_at_ms": 1,
            }]), stderr=""
        )

    monkeypatch.setattr(ps.subprocess, "run", run)
    rows = ps.ProcessSafetyProbe._windows_processes()
    assert rows[0]["pid"] == 1
    assert calls[0][0][0] == "powershell.exe"
    assert calls[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_direct_cim_diagnostic_launch_is_windowless(monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(pd, "_is_windows", lambda: True)
    result = pd.inspect_windows_research_processes([], runner=run)
    assert result["mutation_performed"] is False
    assert seen[0][0][0] == "powershell.exe"
    assert seen[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
