"""Zero-science, no-host-command QA for independent process diagnostic lane."""
from __future__ import annotations

import asyncio
import os
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remotemcp.node import process_diagnostic as pd
from remotemcp.routing.models import ALLOWED_COMMANDS, MUTATING_COMMANDS
from remotemcp.routing.service import RoutingService
from remotemcp.durable.errors import DurableError


def test_fixed_cim_command_returns_only_matched_research_process(monkeypatch):
    monkeypatch.setattr(pd, "_is_windows", lambda: True)
    inputs = []
    raw = [
        {"pid": 9152, "parent_pid": 24528,
         "created_at": "2026-10-06T07:38:15+07:00",
         "executable": "C:/miniforge3/python.exe",
         "command_line": r"C:\python.exe D:\WORK\RESEARCH\2.CQG-RU0G\ru0_c_u3_executor.py --token shouldhide"},
        {"pid": 500, "parent_pid": 1, "created_at": "2026-10-09",
         "executable": "C:/python.exe", "command_line": r"python.exe D:\WORK\RESEARCH\SIX_XMOD\run.py"},
        {"pid": 501, "parent_pid": 1, "created_at": "2026-10-09",
         "executable": "C:/python.exe", "command_line": r"python.exe D:\WORK\RESEARCH\IRIS_P2\probe.py"},
        {"pid": 503, "parent_pid": 1, "created_at": "2026-10-09",
         "executable": "C:/python.exe", "command_line": "python.exe unrelated.py"},
    ]

    def runner(argv, **kwargs):
        inputs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(raw), stderr="")

    report = pd.inspect_windows_research_processes([9152], runner=runner)
    assert report["status"] == "READ_ONLY_SNAPSHOT"
    assert [p["pid"] for p in report["processes"]] == [9152, 500, 501]
    assert "shouldhide" not in report["processes"][0]["command_preview"]
    assert "[REDACTED]" in report["processes"][0]["command_preview"]
    assert report["processes"][0]["parent_pid"] == 24528
    assert report["requested_pid_not_seen"] == []
    assert len(inputs) == 1
    assert inputs[0][0][:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-CimInstance Win32_Process" in inputs[0][0][4]
    assert "9152" not in inputs[0][0][4]  # no argument/script injection
    assert inputs[0][1]["timeout"] <= 12
    assert report["mutation_performed"] is False
    assert report["command_execution_authorized"] is False
    assert report["science_rerun_authorized"] is False


def test_invalid_pid_and_unavailable_platform_fail_closed(monkeypatch):
    monkeypatch.setattr(pd, "_is_windows", lambda: True)
    for values in [[0], [-1], ["1"], [True], list(range(1, 18)), "9152"]:
        with pytest.raises(ValueError):
            pd.inspect_windows_research_processes(values, runner=lambda *a, **kw: None)
    monkeypatch.setattr(pd, "_is_windows", lambda: False)
    report = pd.inspect_windows_research_processes([9152])
    assert report["status"] == "UNSUPPORTED_PLATFORM"
    assert report["mutation_performed"] is False


def test_cim_failure_and_unobserved_pid_do_not_trigger_restart(monkeypatch):
    monkeypatch.setattr(pd, "_is_windows", lambda: True)
    result = pd.inspect_windows_research_processes(
        [9152], runner=lambda a, **kw: subprocess.CompletedProcess(a, 0, stdout="[]", stderr=""))
    assert result["requested_pid_not_seen"] == [9152]
    assert result["process_kill_authorized"] is False
    err = pd.inspect_windows_research_processes(
        [9152], runner=lambda a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="error"))
    assert err["status"] == "CIM_QUERY_FAILED"
    assert err["science_rerun_authorized"] is False


def test_diagnostic_is_allowed_but_not_mutating_and_preempts_job_submit():
    assert "NODE_PROCESS_INSPECT" in ALLOWED_COMMANDS
    assert "NODE_PROCESS_INSPECT" not in MUTATING_COMMANDS


def test_gateway_fails_closed_against_legacy_node_without_queue():
    service = object.__new__(RoutingService)
    service.devices = SimpleNamespace(
        status=lambda _: {
            "state":"ONLINE", "capacity_signal_fresh": True,
            "node_attestation":{"release_commit":"80fe818", "supported_node_diagnostics":[]},
        }
    )
    service._route_step = AsyncMock()
    with pytest.raises(DurableError):
        asyncio.run(service.device_process_inspect("dev_machine1", [9152]))
    service._route_step.assert_not_awaited()


def test_gateway_uses_signed_fixed_priority_without_research_task():
    service = object.__new__(RoutingService)
    service.devices = SimpleNamespace(
        status=lambda _: {
            "state":"ONLINE", "capacity_signal_fresh": True,
            "node_attestation":{"supported_node_diagnostics":["process_inspect_v1"]},
        }
    )
    service._route_step = AsyncMock(
        return_value=(
            {"status":"READ_ONLY_SNAPSHOT", "processes":[]},
            {"command_id":"cmd_diag1", "route_generation":1},
        )
    )
    reply = asyncio.run(service.device_process_inspect("dev_machine1", [9152]))
    service._route_step.assert_awaited_once_with(
        "dev_machine1", "NODE_PROCESS_INSPECT", {"pids":[9152]}
    )
    assert reply["command_id"] == "cmd_diag1"
    assert reply["task_created"] is False
    assert reply["scientific_job_created"] is False


def test_gateway_rejects_bad_pids_before_any_side_effects():
    service = object.__new__(RoutingService)
    service.devices = SimpleNamespace(status=lambda _: {"state":"ONLINE","capacity_signal_fresh":True})
    service._route_step = AsyncMock()
    for value in [[0],[-1],[True],["9152"],list(range(1,18))]:
        with pytest.raises(DurableError):
            asyncio.run(service.device_process_inspect("dev_machine1",value))
    service._route_step.assert_not_awaited()


@pytest.mark.skipif(os.name != "nt", reason="Real Win32_Process CIM check only on Windows")
def test_real_windows_cim_reports_existing_current_python_pid_read_only():
    report = pd.inspect_windows_research_processes([os.getpid()])
    assert report["status"] == "READ_ONLY_SNAPSHOT"
    assert any(item["pid"] == os.getpid() for item in report["processes"])
    assert report["mutation_performed"] is False
    assert report["science_rerun_authorized"] is False


def test_heartbeat_inventory_bypasses_gateway_commands_and_redacts_argv(monkeypatch):
    import asyncio
    from remotemcp.node import service as node_service

    calls = []
    def scan(_pids):
        calls.append(True)
        return {
            "status": "READ_ONLY_SNAPSHOT",
            "processes": [{
                "pid": 9152, "parent_pid": 24528,
                "created_at": "2026-10-06T07:38:15+07:00",
                "executable": "python.exe",
                "command_line_sha256": "a" * 64,
                "command_preview": "secret-in-commandline",
            }],
            "truncated": False,
        }

    monkeypatch.setattr(node_service, "inspect_windows_research_processes", scan)
    node = object.__new__(node_service.NodeService)
    node._research_inventory_task = None
    node._research_inventory_snapshot = None
    node._research_inventory_last_start = 0.0

    async def inspect():
        result = await node._research_inventory_for_heartbeat()
        assert result["research_process_inventory_status"] == "PENDING"
        for _ in range(50):
            await asyncio.sleep(0.01)
            result = await node._research_inventory_for_heartbeat()
            if result["research_process_inventory_status"] == "READ_ONLY_SNAPSHOT":
                break
        assert result["research_process_inventory_status"] == "READ_ONLY_SNAPSHOT"
        assert result["research_process_inventory"][0]["pid"] == 9152
        assert result["research_process_inventory"][0]["parent_pid"] == 24528
        assert "secret-in-commandline" not in str(result)
        assert len(calls) == 1

    asyncio.run(inspect())
