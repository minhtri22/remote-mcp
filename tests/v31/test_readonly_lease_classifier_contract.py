"""Zero-science prereg fixture: 19 LEASED rows, no production database access."""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "v31_readonly_lease_classifier.py"
spec = importlib.util.spec_from_file_location("v31_readonly_lease_classifier", SCRIPT)
assert spec and spec.loader
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)

DEV = lease.DEVICE_ID


def command(i, *, expires=100, lease_expires=100, attempts=1, state="LEASED"):
    return {
        "command_id": "cmd_" + f"{i:032x}",
        "device_id": DEV,
        "project_id": "prj_fixture",
        "task_id": "tsk_fixture",
        "command_type": "TASK_LIST_DIR",
        "state": state,
        "delivery_attempt": attempts,
        "route_generation": 1,
        "request_hash": f"{i:064x}",
        "created_at_ms": 50,
        "command_expires_at_ms": expires,
        "lease_expires_at_ms": lease_expires,
    }


def receipt(row, state="SUCCEEDED", mismatch=False):
    return {
        "command_id": row["command_id"],
        "request_hash": ("0" * 64 if mismatch else row["request_hash"]),
        "route_generation": row["route_generation"],
        "command_type": row["command_type"],
        "state": state,
        "received_at_ms": 70,
        "started_at_ms": 80,
        "finished_at_ms": 90,
        "error_code": None,
    }


@pytest.mark.parametrize(("row", "r", "now", "expected"), [
    (command(1), receipt(command(1)), 200, "EXPIRED_NODE_TERMINAL_GATEWAY_STALE"),
    (command(2), receipt(command(2), "FAILED"), 200, "EXPIRED_NODE_TERMINAL_GATEWAY_STALE"),
    (command(3), receipt(command(3), "EXECUTING"), 200, "EXPIRED_NODE_RECEIVED_NONTERMINAL"),
    (command(4), None, 200, "EXPIRED_DELIVERED_NO_RECEIPT"),
    (command(5, attempts=0), None, 200, "EXPIRED_NEVER_DELIVERED"),
    (command(6, expires=300), None, 200, "UNEXPIRED_LEASE_HOLD"),
    (command(7, lease_expires=300), None, 200, "UNEXPIRED_LEASE_HOLD"),
    (command(8), receipt(command(8), mismatch=True), 200, "IDENTITY_MISMATCH_HOLD"),
    (command(9, lease_expires=None), None, 200, "UNRESOLVED_HOLD"),
    (command(10), receipt(command(10), "UNKNOWN"), 200, "UNRESOLVED_HOLD"),
    (command(11, state="SUCCEEDED"), None, 200, "UNRESOLVED_HOLD"),
])
def test_prelocked_classification(row, r, now, expected):
    assert lease.classify_command(row, r, now) == expected


def create_fixture_databases(tmp):
    gateway = tmp / "gateway.db"
    node = tmp / "node.db"
    with sqlite3.connect(gateway) as con:
        con.execute(
            "CREATE TABLE device_commands (command_id TEXT PRIMARY KEY,device_id TEXT,"
            "project_id TEXT,task_id TEXT,command_type TEXT,state TEXT,"
            "delivery_attempt INTEGER,route_generation INTEGER,request_hash TEXT,"
            "created_at_ms INTEGER,command_expires_at_ms INTEGER,lease_expires_at_ms INTEGER)"
        )
        for i in range(1, 20):
            row = command(i, attempts=0 if i % 5 == 0 else 1)
            con.execute(
                "INSERT INTO device_commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row.values()),
            )
    with sqlite3.connect(node) as con:
        con.execute(
            "CREATE TABLE node_commands (command_id TEXT PRIMARY KEY,"
            "request_hash TEXT,route_generation INTEGER,command_type TEXT,"
            "state TEXT,received_at_ms INTEGER,started_at_ms INTEGER,"
            "finished_at_ms INTEGER,error_code TEXT)"
        )
        for i in range(1, 20):
            if i % 3:
                row = receipt(command(i), "FAILED" if i % 4 == 0 else "SUCCEEDED")
                con.execute(
                    "INSERT INTO node_commands VALUES(?,?,?,?,?,?,?,?,?)",
                    tuple(row.values()),
                )
    return gateway, node


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_19_lease_snapshot_exact_readonly(tmp_path):
    gateway, node = create_fixture_databases(tmp_path)
    before = (sha(gateway), sha(node))
    report = lease.snapshot(gateway, node, now_ms=200, expected=19)
    assert report["actual_leased"] == 19
    assert not report["inventory_drift"]
    assert len(report["rows"]) == 19
    assert sum(report["classification_counts"].values()) == 19
    assert all(row["gateway_state"] == "LEASED" for row in report["rows"])
    assert all(not row["mutation_authorized"] and not row["auto_replay_authorized"] for row in report["rows"])
    assert all(row["fingerprint_consistent"] is True for row in report["rows"] if row["node_receipt_present"])
    assert report["read_only"] and not report["gateway_db_mutated"] and not report["node_db_mutated"]
    assert (sha(gateway), sha(node)) == before
    assert report["lease_inventory_digest_sha256"] == lease.snapshot(
        gateway, node, now_ms=200, expected=19
    )["lease_inventory_digest_sha256"]


def test_snapshot_inventory_drift_does_not_modify_sqlite(tmp_path):
    gateway, node = create_fixture_databases(tmp_path)
    before = (sha(gateway), sha(node))
    report = lease.snapshot(gateway, node, now_ms=200, expected=20)
    assert report["inventory_drift"] and report["actual_leased"] == 19
    assert (sha(gateway), sha(node)) == before


def test_identity_mismatch_hard_hold_and_no_secret_payload(tmp_path):
    gateway, node = create_fixture_databases(tmp_path)
    with sqlite3.connect(node) as con:
        con.execute("UPDATE node_commands SET request_hash=? WHERE command_id=?",
                    ("f" * 64, command(1)["command_id"]))
    report = lease.snapshot(gateway, node, now_ms=200, expected=19)
    assert report["hard_safety_hold"]
    assert report["rows"][0]["classification"] == "IDENTITY_MISMATCH_HOLD"
    assert report["rows"][0]["fingerprint_consistent"] is False
    assert "payload_json" not in str(report) and "lease_token" not in str(report)


def test_not_found_and_invalid_device_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="DATABASE_NOT_PRESENT"):
        lease.snapshot(tmp_path / "missing.db", tmp_path / "missing2.db")
    with pytest.raises(ValueError, match="INVALID_DEVICE_ID"):
        lease.snapshot(tmp_path / "missing.db", tmp_path / "missing2.db", device_id="not-a-device")


def test_expired_lease_does_not_mean_replay_permission(tmp_path):
    gateway, node = create_fixture_databases(tmp_path)
    report = lease.snapshot(gateway, node, now_ms=900_000, expected=19)
    assert all(not item["auto_replay_authorized"] for item in report["rows"])
    assert report["historical_command_replay_authorized"] is False
    assert report["science_task_mutation_authorized"] is False
