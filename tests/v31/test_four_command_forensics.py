"""Cross-journal synthetic evidence test: exact four historical command IDs only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "v31_four_command_forensics.py"
spec = importlib.util.spec_from_file_location("v31_four_command_forensics", SCRIPT)
assert spec is not None and spec.loader is not None
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


def setup(tmp_path, monkeypatch, *, with_durable_job=False):
    four = list(f.FOUR.items())
    rows = []
    for i, (cid, command_type) in enumerate(four):
        rows.append({
            "command_id": cid, "command_type": command_type,
            "task_id": "tsk_fixture" if command_type != "PROJECT_PROBE" else None,
            "gateway_state": "LEASED",
            "delivery_attempt": 1, "request_hash": f"{i+1:064x}",
            "route_generation": 1,
        })
    for i in range(15):
        rows.append({"command_id": "cmd_extra_"+str(i), "command_type":"JOB_GET"})
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    monkeypatch.setattr(f, "FROZEN_DIGEST", digest)
    frozen = tmp_path / "inventory.json"
    frozen.write_text(json.dumps({
        "schema": "remotemcp.v31.leased-command-evidence.v1",
        "device_id": f.DEVICE, "actual_leased": 19,
        "lease_inventory_digest_sha256": digest, "rows": rows,
    }), encoding="utf-8")

    gateway = tmp_path / "gateway.db"
    node = tmp_path / "node.db"
    durable = tmp_path / "durable.db"
    with sqlite3.connect(gateway) as con:
        con.execute(
            "CREATE TABLE device_commands (command_id TEXT PRIMARY KEY,device_id TEXT,"
            "task_id TEXT,project_id TEXT,operation_id TEXT,state TEXT,"
            "command_type TEXT,request_hash TEXT,route_generation INT,"
            "delivery_attempt INT,command_expires_at_ms INT,"
            "lease_expires_at_ms INT,payload_json TEXT)"
        )
        con.execute(
            "CREATE TABLE routed_jobs (proxy_job_id TEXT PRIMARY KEY,task_id TEXT,"
            "project_id TEXT,node_job_id TEXT,last_known_state TEXT)"
        )
        for row in rows[:4]:
            p = {"proxy_job_id":"rjob_fixture"} if row["command_type"] == "JOB_SUBMIT" else {}
            con.execute("INSERT INTO device_commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                row["command_id"],f.DEVICE,row["task_id"],"prj_fixture",None,"LEASED",
                row["command_type"],row["request_hash"],1,1,100,120,json.dumps(p),
            ))
        if with_durable_job:
            con.execute("INSERT INTO routed_jobs VALUES(?,?,?,?,?)", (
                "rjob_fixture","tsk_fixture","prj_fixture","job_fixture","SUCCEEDED"))
    with sqlite3.connect(node) as con:
        con.execute(
            "CREATE TABLE node_commands (command_id TEXT PRIMARY KEY,command_type TEXT,"
            "request_hash TEXT,route_generation INTEGER,state TEXT,received_at_ms INTEGER,"
            "started_at_ms INTEGER,finished_at_ms INTEGER,error_code TEXT)"
        )
        con.execute(
            "CREATE TABLE node_routed_jobs (proxy_job_id TEXT PRIMARY KEY,task_id TEXT,"
            "project_id TEXT,node_job_id TEXT,state TEXT)"
        )
        task_resolve = rows[3]
        con.execute("INSERT INTO node_commands VALUES(?,?,?,?,?,?,?,?,?)", (
            task_resolve["command_id"],task_resolve["command_type"],
            task_resolve["request_hash"],1,"EXECUTING",90,91,None,None
        ))
        if with_durable_job:
            con.execute("INSERT INTO node_routed_jobs VALUES(?,?,?,?,?)", (
                "rjob_fixture","tsk_fixture","prj_fixture","job_fixture","SUCCEEDED"))
    with sqlite3.connect(durable) as con:
        con.execute("CREATE TABLE operations (operation_id TEXT PRIMARY KEY,state TEXT)")
        con.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY,operation_id TEXT,state TEXT,"
            "finished_at_ms INTEGER,worker_fingerprint_json TEXT,"
            "payload_fingerprint_json TEXT)"
        )
        con.execute(
            "CREATE TABLE events (job_id TEXT,terminal INTEGER)"
        )
        if with_durable_job:
            opid = f"v2bd-node-job:{f.DEVICE}:{four[0][0]}"
            con.execute("INSERT INTO operations VALUES(?,?)", (opid,"SUCCEEDED"))
            con.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (
                "job_fixture",opid,"SUCCEEDED",95,"{fingerprint}","{payload-fingerprint}"
            ))
            con.execute("INSERT INTO events VALUES(?,?)", ("job_fixture",1))
    return frozen,gateway,node,durable


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_four_unknown_commands_exact_readonly(tmp_path,monkeypatch):
    paths = setup(tmp_path,monkeypatch)
    before = [sha(p) for p in paths[1:]]
    out = f.forensic_snapshot(*paths)
    assert out["schema"] == "remotemcp.v31.four-command-forensics.v1"
    assert out["all_four_accounted_for"]
    assert not out["all_fully_reconciled"]
    assert len(out["rows"]) == 4
    assert sum(x["verdict"] == "NO_PROVEN_EXECUTION_UNRESOLVED" for x in out["rows"]) == 3
    assert out["rows"][3]["verdict"] == "NODE_NONTERMINAL_STALE_UNRESOLVED"
    assert all(not x["mutation_authorized"] and not x["replay_authorized"] for x in out["rows"])
    assert not out["replay_authorized"] and out["database_changes"] == 0
    assert [sha(p) for p in paths[1:]] == before


def test_durable_job_provenance_found_but_no_auto_reconcile(tmp_path,monkeypatch):
    paths = setup(tmp_path,monkeypatch,with_durable_job=True)
    report = f.forensic_snapshot(*paths)
    submit = report["rows"][0]
    assert submit["verdict"] == "DURABLE_JOB_PRESENT_RECONCILIATION_REQUIRED"
    assert submit["proxy_id"] == "rjob_fixture"
    assert submit["durable_job"]["job_state"] == "SUCCEEDED"
    assert submit["durable_terminal_event_count"] == 1
    assert submit["worker_fingerprint_evidence_present"]
    assert submit["payload_fingerprint_evidence_present"]
    assert not submit["mutation_authorized"] and not submit["replay_authorized"]
    assert report["all_fully_reconciled"] is False


def test_frozen_inventory_tamper_fails_closed(tmp_path,monkeypatch):
    paths = setup(tmp_path,monkeypatch)
    frozen = paths[0]
    record = json.loads(frozen.read_text())
    record["rows"][0]["request_hash"] = "f"*64
    frozen.write_text(json.dumps(record),encoding="utf-8")
    with pytest.raises(RuntimeError, match="FROZEN_ROWS_DIGEST_INVALID"):
        f.forensic_snapshot(*paths)


def test_receipt_mismatch_is_hold_not_a_repair(tmp_path,monkeypatch):
    paths = setup(tmp_path,monkeypatch)
    with sqlite3.connect(paths[2]) as con:
        con.execute(
            "UPDATE node_commands SET request_hash=? WHERE command_id=?",
            ("f"*64, list(f.FOUR)[3]),
        )
    before = [sha(p) for p in paths[1:]]
    report = f.forensic_snapshot(*paths)
    assert report["rows"][3]["verdict"] == "IDENTITY_CONFLICT_HOLD"
    assert not report["rows"][3]["node_receipt_identity_consistent"]
    assert [sha(p) for p in paths[1:]] == before
