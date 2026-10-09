"""Zero-science read-only PID ownership audit; no real processes or job reruns."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from remotemcp.durable.models import ProcessFingerprint
from remotemcp.node import process_ownership_audit as audit


def fp(pid: int, start: str = "starts-one", executable: str = "python.exe"):
    return ProcessFingerprint(
        pid=pid,
        start_token=start,
        executable_canonical=executable,
        command_sha256="c" * 64,
    )


def fixture(tmp_path: Path, *, missing_link: bool = False, state: str = "RUNNING"):
    rt = tmp_path / "runtime"
    (rt / "durable").mkdir(parents=True)
    node = sqlite3.connect(rt / "node.db")
    node.execute(
        "CREATE TABLE node_routed_jobs("
        "proxy_job_id TEXT,node_job_id TEXT,task_id TEXT,project_id TEXT,state TEXT)"
    )
    node.execute(
        "CREATE TABLE node_commands("
        "command_id TEXT,route_generation INTEGER,request_hash TEXT,"
        "command_type TEXT,payload_json TEXT,state TEXT,received_at_ms INTEGER)"
    )
    if not missing_link:
        node.execute(
            "INSERT INTO node_routed_jobs VALUES(?,?,?,?,?)",
            ("rjob_existing", "job_existing", "tsk_exact", "prj_exact", state),
        )
    node.execute(
        "INSERT INTO node_commands VALUES(?,?,?,?,?,?,?)",
        (
            "cmd_exact", 1, "d" * 64, "JOB_SUBMIT",
            json.dumps({"proxy_job_id": "rjob_existing", "task_id": "tsk_exact"}),
            "SUCCEEDED", 200,
        ),
    )
    node.commit()
    node.close()
    durable = sqlite3.connect(rt / "durable" / "runtime.db")
    durable.execute(
        "CREATE TABLE jobs ("
        "job_id TEXT,operation_id TEXT,state TEXT,worker_fingerprint_json TEXT,"
        "payload_fingerprint_json TEXT,created_at_ms INTEGER,"
        "started_at_ms INTEGER,finished_at_ms INTEGER)"
    )
    durable.execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
        (
            "job_existing",
            "v2bd-node-job:dev_existing:cmd_exact",
            state,
            json.dumps(fp(3301).to_dict()),
            json.dumps(fp(3302).to_dict()),
            100, 110, None,
        ),
    )
    durable.commit()
    durable.close()
    return rt


def snap_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_bidirectional_mapping_without_any_database_write(tmp_path, monkeypatch):
    rt = fixture(tmp_path)
    monkeypatch.setattr(audit, "fingerprint_process", lambda pid, h: fp(pid))
    paths = (rt / "node.db", rt / "durable" / "runtime.db")
    before = [snap_hash(p) for p in paths]
    result = audit.audit_process_ownership(rt, (3302,))
    after = [snap_hash(p) for p in paths]
    assert before == after
    assert result["status"] == "READ_ONLY_OBSERVATION"
    assert result["pid_queries"][0]["state"] == "MATCHED_DURABLE_FINGERPRINT"
    assert result["pid_queries"][0]["matches"][0]["proxy_job_id"] == "rjob_existing"
    assert result["process_mappings"][0]["command_id"] == "cmd_exact"
    assert result["process_mappings"][0]["journal_proxy_matches"] is True
    assert all(p["verification"] == "EXACT_LIVE_PROCESS"
               for p in result["process_mappings"][0]["processes"])
    assert result["science_rerun_authorized"] is False
    assert result["process_kill_authorized"] is False
    assert result["mutation_performed"] is False


def test_pid_reuse_is_not_evidence_of_original_job(tmp_path, monkeypatch):
    rt = fixture(tmp_path)
    monkeypatch.setattr(
        audit, "fingerprint_process",
        lambda pid, h: fp(pid, start="different-process"),
    )
    result = audit.audit_process_ownership(rt, (3302,))
    roles = result["process_mappings"][0]["processes"]
    assert roles[1]["verification"] == "PID_REUSED_OR_START_CHANGED"
    assert all(role["verification"] != "EXACT_LIVE_PROCESS" for role in roles)
    assert result["cutover_authorized"] is False


def test_unknown_running_pid_has_no_invented_proxy_mapping(tmp_path, monkeypatch):
    rt = fixture(tmp_path)
    monkeypatch.setattr(audit, "fingerprint_process", lambda pid, h: fp(pid))
    result = audit.audit_process_ownership(rt, (9152,))
    assert result["pid_queries"][0]["state"] == "UNMAPPED_OS_PROCESS_VISIBLE"
    assert result["pid_queries"][0]["matches"] == []
    assert result["science_rerun_authorized"] is False


def test_missing_journal_mapping_is_explicit_warning(tmp_path, monkeypatch):
    rt = fixture(tmp_path, missing_link=True)
    monkeypatch.setattr(audit, "fingerprint_process", lambda pid, h: fp(pid))
    result = audit.audit_process_ownership(rt, (3302,))
    assert result["status"] == "OBSERVED_WITH_INTEGRITY_WARNINGS"
    assert "DURABLE_JOB_WITHOUT_PROXY_LINK" in result["integrity_warnings"]
    assert "COMMAND_RECEIPT_UNMAPPED" in result["integrity_warnings"]
    assert result["unmapped_command_receipts"][0]["command_id"] == "cmd_exact"
    assert result["process_mappings"][0]["proxy_job_id"] is None


def test_terminal_job_live_process_never_silently_trusted(tmp_path, monkeypatch):
    rt = fixture(tmp_path, state="SUCCEEDED")
    monkeypatch.setattr(audit, "fingerprint_process", lambda pid, h: fp(pid))
    result = audit.audit_process_ownership(rt, (3302,))
    assert result["status"] == "OBSERVED_WITH_INTEGRITY_WARNINGS"
    assert "TERMINAL_JOB_WITH_LIVE_PROCESS" in result["integrity_warnings"]


def test_missing_db_is_hard_fail_without_create(tmp_path):
    rt = tmp_path / "missing-runtime"
    with pytest.raises(FileNotFoundError):
        audit.audit_process_ownership(rt, (9152,))
    assert not rt.exists()


def test_invalid_or_excessive_pid_query_rejected(tmp_path):
    rt = fixture(tmp_path)
    for pids in [(-1,), (0,), tuple(range(1, 67))]:
        with pytest.raises(ValueError):
            audit.audit_process_ownership(rt, pids)


def test_probe_failure_is_not_evidence_process_ended(tmp_path, monkeypatch):
    rt = fixture(tmp_path)
    monkeypatch.setattr(audit, "fingerprint_process", lambda pid, h: None)
    result = audit.audit_process_ownership(rt, (9152,))
    assert result["pid_queries"][0]["state"] == "UNMAPPED_OR_NOT_OBSERVABLE"
    assert result["process_mappings"][0]["processes"][1]["verification"] == "NOT_OBSERVABLE_OR_EXITED"


def test_cli_parser_accepts_read_only_node_audit():
    from remotemcp.node.cli import parser
    args = parser().parse_args(
        ["process-audit", "--runtime-dir", "C:/runtime", "--pid", "9152", "--pid", "24528"]
    )
    assert args.command == "process-audit"
    assert args.pid == [9152, 24528]
