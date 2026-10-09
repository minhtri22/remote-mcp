"""Adversarial tests: offline gateway proof neither mutates nor dispatches."""
import hashlib
import json
import sqlite3

import pytest

from scripts.read_gateway_exact_receipts import inspect_gateway


def fixture(tmp_path):
    db=tmp_path/"runtime.db"
    c=sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE device_commands (command_id TEXT PRIMARY KEY,device_id TEXT,"
        "task_id TEXT,project_id TEXT,route_generation INTEGER,operation_id TEXT,"
        "operation_step INTEGER,command_type TEXT,request_hash TEXT,payload_json TEXT,"
        "state TEXT,delivery_attempt INTEGER,lease_expires_at_ms INTEGER,"
        "command_expires_at_ms INTEGER,created_at_ms INTEGER,updated_at_ms INTEGER,"
        "finished_at_ms INTEGER,error_code TEXT);"
        "CREATE TABLE routed_jobs (proxy_job_id TEXT,node_job_id TEXT,"
        "last_known_state TEXT,operation_id TEXT,task_id TEXT,device_id TEXT);"
    )
    c.execute(
        "INSERT INTO device_commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("cmd_a","dev_known","tsk_one","prj_one",1,"op_one",0,
         "JOB_SUBMIT","a"*64,json.dumps({"proxy_job_id":"rjob_a",
         "argv":["private-never-emit"],"secret":"do-not-emit"}),
         "QUEUED",1,None,200,100,125,None,None),
    )
    c.execute(
        "INSERT INTO device_commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("cmd_other","dev_known","tsk_other","prj_one",1,"op_other",0,
         "JOB_SUBMIT","b"*64,"{}","FAILED",1,None,200,100,125,150,"NOT_FOUND"),
    )
    c.execute(
        "INSERT INTO routed_jobs VALUES(?,?,?,?,?,?)",
        ("rjob_a",None,"QUEUED","op_one","tsk_one","dev_known"),
    )
    c.commit()
    c.close()
    return db


def test_exact_scoped_original_receipt_and_zero_mutations(tmp_path):
    db=fixture(tmp_path)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    r=inspect_gateway(
        db,device_id="dev_known",task_id="tsk_one",command_ids=["cmd_a","cmd_other"],
    )
    assert r["read_only"] is True
    assert r["node_command_dispatch_performed"] is False
    assert r["scientific_job_replay_authorized"] is False
    assert r["node_upgrade_authorized"] is False
    assert r["receipts"][0]["request_hash"]=="a"*64
    assert r["receipts"][0]["delivery_attempt"]==1
    assert r["receipts"][0]["state"]=="QUEUED"
    assert r["receipts"][0]["routed_proxy_job_id"]=="rjob_a"
    assert r["receipts"][1]["status"]=="NOT_FOUND_OR_NOT_OWNED"
    assert "private-never-emit" not in json.dumps(r)
    assert "do-not-emit" not in json.dumps(r)
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_missing_database_is_not_created(tmp_path):
    db=tmp_path/"does-not-exist.db"
    with pytest.raises(ValueError):
        inspect_gateway(
            db,device_id="dev_known",task_id="tsk_one",command_ids=["cmd_a"],
        )
    assert not db.exists()


def test_invalid_and_duplicate_ids_rejected(tmp_path):
    db=fixture(tmp_path)
    for ids in ([],["cmd_a","cmd_a"],["INVALID"],["cmd_x"]*17):
        with pytest.raises(ValueError):
            inspect_gateway(
                db,device_id="dev_known",task_id="tsk_one",command_ids=ids,
            )
