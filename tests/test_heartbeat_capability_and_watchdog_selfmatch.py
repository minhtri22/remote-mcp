"""Regression: signed node diagnostics survive the gateway heartbeat and watchdog argv is not science."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.node.process_safety import ProcessSafetyProbe
from remotemcp.routing.service import RoutingService
from remotemcp.routing.commands import CommandRepository


SHA = "4f0d849fdd005b72488e7349ef5efffe531467d5"


class HeartbeatDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE devices(device_id TEXT PRIMARY KEY,"
            "capabilities_json TEXT,platform_json TEXT)"
        )
        self.conn.execute(
            "INSERT INTO devices VALUES(?,?,?)", ("dev_test", "{}", "{}")
        )

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self.conn


def make_service():
    db = HeartbeatDB()
    service = object.__new__(RoutingService)
    service.db = db
    service.config = SimpleNamespace(heartbeat_seconds=15)
    row = {
        "device_id": "dev_test", "route_generation": 1,
        "capabilities_json": "{}", "platform_json": "{}",
    }
    return service, db, row


def send_heartbeat(service, row, att):
    return service.heartbeat_http(row, {
        "active_node_jobs": 0,
        "active_node_jobs_unresolved": 0,
        "capacity_reconciliation_complete": True,
        "active_job_summaries": [],
        "node_attestation": att,
    })


def test_signed_diagnostic_capability_survives_gateway_storage_and_release_gate(monkeypatch):
    service, db, row = make_service()
    att = {"release_commit": SHA, "supported_node_diagnostics": ["process_inspect_v1"]}
    assert send_heartbeat(service, row, att)["state"] == "ONLINE"
    stored = json.loads(db.conn.execute(
        "SELECT capabilities_json FROM devices WHERE device_id='dev_test'"
    ).fetchone()[0])
    observed = stored["_remotemcp_node_attestation"]
    assert observed["supported_node_diagnostics"] == ["process_inspect_v1"]
    assert observed["release_commit"] == SHA
    assert stored["_remotemcp_active_node_jobs"] == 0
    assert stored["_remotemcp_capacity_reconciliation_complete"] is True
    monkeypatch.setenv("REMOTEMCP_REQUIRED_NODE_RELEASE_COMMIT_SHA", SHA)
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH", "1")
    fake_devices = SimpleNamespace(status=lambda device_id: {
        "state": "ONLINE", "capacity_signal_fresh": True,
        "node_attestation": observed,
    })
    repo = CommandRepository(
        SimpleNamespace(command_lease_seconds=30),
        db, fake_devices,
    )
    # Pin check passes; quarantine remains explicitly enabled.
    assert repo._node_release_blocker("dev_test") is None
    assert repo._science_dispatch_blocker("dev_test") == "SCIENCE_DISPATCH_QUARANTINED"


@pytest.mark.parametrize("capabilities", [
    "process_inspect_v1", [19], ["process_inspect_v1"] * 2,
    ["process_inspect_v1"] * 9, ["bad tag"], ["x" * 65],
])
def test_malformed_signed_capabilities_are_rejected_without_db_mutation(capabilities):
    service, db, row = make_service()
    with pytest.raises(DurableError) as exc:
        send_heartbeat(service, row, {
            "release_commit": SHA, "supported_node_diagnostics": capabilities,
        })
    assert exc.value.code == "INVALID_ARGUMENT"
    stored = db.conn.execute(
        "SELECT capabilities_json FROM devices WHERE device_id='dev_test'"
    ).fetchone()[0]
    assert stored == "{}"


def test_old_node_without_capability_still_fails_closed():
    service, db, row = make_service()
    send_heartbeat(service, row, {"release_commit": SHA})
    saved = json.loads(db.conn.execute(
        "SELECT capabilities_json FROM devices WHERE device_id='dev_test'"
    ).fetchone()[0])["_remotemcp_node_attestation"]
    assert "supported_node_diagnostics" not in saved


def test_watchdog_argv_declaration_is_not_a_science_process(monkeypatch):
    monkeypatch.setenv(
        "REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS", "ru0_c_u3_executor.py"
    )
    processes = [
        {"pid": 27036, "ppid": 1156, "name": "powershell.exe",
         "command_line": (
             r'powershell.exe -NoProfile -File '
             r'"D:\2.RemoteMCP-releases\4f0d849\Watch-RemoteMCP-Node.ps1" '
             r'-RuntimeDir C:\Users\minht\AppData\Local\RemoteMCP\runtime '
             r'-DeclaredLongLivedProcessPatterns ru0_c_u3_executor.py'
         )},
        {"pid": 27037, "ppid": 1, "name": "python.exe",
         "command_line": r'python.exe D:\WORK\RESEARCH\ru0_c_u3_executor.py'},
        {"pid": 27038, "ppid": 1, "name": "powershell.exe",
         "command_line": (
             r'powershell.exe -File D:\WORK\RESEARCH\worker.ps1 '
             r'-Note ru0_c_u3_executor.py'
         )},
    ]
    probe = ProcessSafetyProbe(SimpleNamespace())
    monkeypatch.setattr(probe, "_enumerate_processes", lambda: processes)
    monkeypatch.setattr(probe, "_job_roots", lambda: ([], 0))
    snap = probe.snapshot()
    blockers = json.loads(snap["physical_process_blocker_summary_json"])
    assert snap["physical_process_safety_resolved"] == 1
    assert snap["physical_process_blocker_count"] == 2
    assert {b["pid"] for b in blockers} == {27037, 27038}
    assert all(b["source"] == "declared_long_lived" for b in blockers)


def test_watchdog_alone_is_not_a_process_safety_blocker(monkeypatch):
    monkeypatch.setenv(
        "REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS", "ru0_c_u3_executor.py"
    )
    probe = ProcessSafetyProbe(SimpleNamespace())
    monkeypatch.setattr(probe, "_enumerate_processes", lambda: [
        {"pid": 27036, "ppid": 1156, "name": "powershell.exe",
         "command_line": (
             r'powershell.exe -File '
             r'"D:\2.RemoteMCP-releases\4f0d849\Watch-RemoteMCP-Node.ps1" '
             r'-DeclaredLongLivedProcessPatterns ru0_c_u3_executor.py'
         )},
    ])
    monkeypatch.setattr(probe, "_job_roots", lambda: ([], 0))
    snap = probe.snapshot()
    assert snap["physical_process_safety_resolved"] == 1
    assert snap["physical_process_blocker_count"] == 0
    assert snap["physical_process_declared_pattern_count"] == 1
