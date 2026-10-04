from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"audit_legacy_routed_jobs.py"


def _load():
    spec=importlib.util.spec_from_file_location("legacy_audit",SCRIPT)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _db(path:Path):
    con=sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE devices(
          device_id TEXT PRIMARY KEY,
          state TEXT NOT NULL
        );
        CREATE TABLE device_commands(
          command_id TEXT PRIMARY KEY,
          operation_id TEXT,
          operation_step INTEGER NOT NULL,
          command_type TEXT NOT NULL,
          state TEXT NOT NULL,
          result_json TEXT,
          error_code TEXT,
          created_at_ms INTEGER NOT NULL
        );
        CREATE TABLE routed_jobs(
          proxy_job_id TEXT PRIMARY KEY,
          operation_id TEXT NOT NULL UNIQUE,
          task_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          device_id TEXT NOT NULL,
          node_job_id TEXT,
          last_known_state TEXT NOT NULL,
          terminal_result_json TEXT,
          last_seen_at_ms INTEGER NOT NULL,
          created_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER NOT NULL,
          terminal_at_ms INTEGER
        );
        """
    )
    return con


def test_inventory_classifies_full_nonterminal_population_without_writes(tmp_path):
    p=tmp_path/"runtime.db"
    con=_db(p)
    con.executemany(
        "INSERT INTO devices(device_id,state) VALUES(?,?)",
        [("d-online","ONLINE"),("d-offline","OFFLINE")],
    )
    rows=[
        ("r1","op1","t1","p","d-online","job1","RUNNING",None,10,10,10,None),
        ("r2","op2","t2","p","d-online",None,"QUEUED",None,20,20,20,None),
        ("r3","op3","t3","p","d-offline",None,"QUEUED",None,30,30,30,None),
        ("r4","op4","t4","p","d-online",None,"QUEUED",None,40,40,40,None),
        ("r5","op5","t4","p","d-online","job5","SUCCEEDED","{}",50,50,50,50),
    ]
    con.executemany(
        "INSERT INTO routed_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.executemany(
        "INSERT INTO device_commands VALUES(?,?,?,?,?,?,?,?)",
        [
            ("c2","op2",0,"JOB_SUBMIT","SUCCEEDED",json.dumps({"node_job_id":"job2","state":"QUEUED"}),None,20),
            ("c3","op3",0,"JOB_SUBMIT","CANCELLED",None,"DEVICE_COMMAND_EXPIRED",30),
        ],
    )
    con.commit()
    before=p.read_bytes()
    con.close()

    report=_load().audit(p)
    assert p.read_bytes()==before
    assert report["summary"]["total_routed_jobs"]==5
    assert report["summary"]["total_nonterminal_routed_jobs"]==4
    assert report["summary"]["blocked_task_lanes"]==4
    assert report["summary"]["nonterminal_without_node_job_id"]==3
    assert report["summary"]["nonterminal_with_later_terminal_successor"]==1

    by={r["proxy_job_id"]:r for r in report["rows"]}
    assert by["r1"]["classification"]=="NODE_BOUND_REFRESH_REQUIRED"
    assert by["r2"]["classification"]=="COMMAND_RESULT_CAN_REPAIR_MAPPING"
    assert by["r3"]["classification"]=="TERMINAL_SUBMIT_COMMAND_WITHOUT_JOB_MAPPING"
    assert by["r4"]["classification"]=="ONLINE_PROXY_REFRESH_REQUIRED"
    assert by["r4"]["has_later_terminal_successor"] is True


def test_inventory_marks_offline_missing_mapping_unresolved(tmp_path):
    p=tmp_path/"runtime.db"
    con=_db(p)
    con.execute("INSERT INTO devices(device_id,state) VALUES('d','OFFLINE')")
    con.execute(
        "INSERT INTO routed_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("r","op","t","p","d",None,"QUEUED",None,1,1,1,None),
    )
    con.commit();con.close()
    report=_load().audit(p)
    row=report["rows"][0]
    assert row["classification"]=="UNRESOLVED_OFFLINE_OR_MISSING_MAPPING"
    assert row["admission_blocking"] is True
