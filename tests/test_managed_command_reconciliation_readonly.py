from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "scripts/agent-recovery/readonly_managed_command_reconciliation.py"
spec = importlib.util.spec_from_file_location("managed_audit", SOURCE)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def test_exact_delivery_path_classification_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module.time, "time_ns", lambda: 1_800_000_000_000_000_000)
    gateway = tmp_path / "gateway.db"
    node = tmp_path / "node.db"
    with sqlite3.connect(gateway) as db:
        db.execute(
            "CREATE TABLE device_commands (command_id TEXT, device_id TEXT, "
            "task_id TEXT, operation_id TEXT, command_type TEXT, state TEXT, "
            "delivery_attempt INTEGER, route_generation INTEGER, request_hash TEXT, "
            "lease_expires_at_ms INTEGER, command_expires_at_ms INTEGER, "
            "created_at_ms INTEGER, updated_at_ms INTEGER, finished_at_ms INTEGER, error_code TEXT)"
        )
        db.executemany(
            "INSERT INTO device_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("cmd_1","dev_1","tsk_alpha","op1","TASK_LIST_DIR","QUEUED",0,1,"h1",
                 None,1800000005000,1799999999000,1799999999000,None,None),
                ("cmd_2","dev_1","tsk_alpha","op2","TASK_LIST_DIR","LEASED",1,1,"h2",
                 1800000002000,1800000005000,1799999998000,1799999999000,None,None),
                ("cmd_3","dev_1","tsk_alpha","op3","JOB_SUBMIT","CANCELLED",0,1,"h3",
                 None,1799999990000,1799999980000,1799999981000,1799999990000,
                 "DEVICE_COMMAND_EXPIRED"),
            ],
        )
    with sqlite3.connect(node) as db:
        db.execute(
            "CREATE TABLE node_commands (command_id TEXT, route_generation INTEGER, "
            "request_hash TEXT, command_type TEXT, state TEXT, received_at_ms INTEGER, "
            "started_at_ms INTEGER, finished_at_ms INTEGER, error_code TEXT)"
        )
        db.execute("INSERT INTO node_commands VALUES (?,?,?,?,?,?,?,?,?)",
                   ("cmd_2",1,"h2","TASK_LIST_DIR","SUCCEEDED",
                    1799999999000,1799999999500,1800000000000,None))
    original_gw = gateway.read_bytes()
    original_node = node.read_bytes()
    snapshot = audit_module.audit(gateway,node,"dev_1",["tsk_alpha"])
    assert snapshot["snapshot_read_only"] is True
    assert snapshot["original_commands_mutated"] is False
    assert gateway.read_bytes() == original_gw
    assert node.read_bytes() == original_node
    by_id = {c["command_id"]:c for c in snapshot["command_trace"]}
    assert by_id["cmd_1"]["delivery_classification"] == "AWAITING_DELIVERY"
    assert by_id["cmd_1"]["node_receipt_present"] is False
    assert by_id["cmd_2"]["delivery_classification"] == "NODE_TERMINAL_GATEWAY_NOT_ACKED"
    assert by_id["cmd_2"]["node_receipt_identity_consistent"] is True
    assert by_id["cmd_3"]["delivery_classification"] == "EXPIRED_HISTORICAL_DO_NOT_REPLAY"
    assert snapshot["live_unexpired_commands_in_returned_trace"] == 2


def test_missing_database_fails_closed_without_creating_a_file(tmp_path):
    gateway = tmp_path / "nonexistent-gateway.db"
    node = tmp_path / "nonexistent-node.db"
    try:
        audit_module.audit(gateway,node,"dev_1",["tsk_alpha"])
    except RuntimeError as exc:
        assert "DATABASE_MISSING" in str(exc)
    else:
        raise AssertionError("missing database unexpectedly accepted")
    assert not gateway.exists()
    assert not node.exists()


def test_rejects_empty_or_non_task_identity_before_sql_access(tmp_path):
    for ids in ([], ["wrong"]):
        try:
            audit_module.audit(tmp_path/"no",tmp_path/"no2","dev",ids)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid task selection accepted")
