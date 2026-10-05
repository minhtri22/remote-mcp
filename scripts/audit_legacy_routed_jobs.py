from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


TERMINAL_JOB_STATES={"SUCCEEDED","FAILED","CANCELLED","LOST"}
TERMINAL_COMMAND_STATES={"SUCCEEDED","FAILED","CANCELLED","IN_DOUBT"}


def _connect_ro(path:Path)->sqlite3.Connection:
    uri=f"file:{path.resolve().as_posix()}?mode=ro"
    con=sqlite3.connect(uri,uri=True,timeout=5.0)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _table_exists(con:sqlite3.Connection,name:str)->bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)
    ).fetchone() is not None


def _json(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def audit(db_path:Path)->dict:
    raw=db_path.read_bytes()
    report={
        "schema":"remotemcp.legacy-routed-job-inventory.v1",
        "database_sha256":hashlib.sha256(raw).hexdigest(),
        "database_size_bytes":len(raw),
        "summary":{},
        "rows":[],
    }
    con=_connect_ro(db_path)
    try:
        required={"routed_jobs","devices","device_commands"}
        missing=sorted(t for t in required if not _table_exists(con,t))
        if missing:
            raise RuntimeError("missing required routing tables: "+",".join(missing))

        all_jobs=con.execute(
            "SELECT * FROM routed_jobs ORDER BY created_at_ms,proxy_job_id"
        ).fetchall()
        nonterminal=[
            r for r in all_jobs if str(r["last_known_state"]) not in TERMINAL_JOB_STATES
        ]

        by_state=Counter()
        by_device_state=Counter()
        by_classification=Counter()
        blocked_tasks=set()
        later_terminal_count=0
        no_node_job_id_count=0

        for row in nonterminal:
            task_id=str(row["task_id"])
            blocked_tasks.add(task_id)
            state=str(row["last_known_state"])
            by_state[state]+=1

            dev=con.execute(
                "SELECT state FROM devices WHERE device_id=?",(row["device_id"],)
            ).fetchone()
            device_state=str(dev["state"]) if dev is not None else "MISSING"
            by_device_state[device_state]+=1

            cmd=con.execute(
                "SELECT * FROM device_commands "
                "WHERE operation_id=? AND command_type='JOB_SUBMIT' "
                "ORDER BY operation_step DESC,created_at_ms DESC LIMIT 1",
                (row["operation_id"],),
            ).fetchone()
            command_state=str(cmd["state"]) if cmd is not None else None
            command_result=_json(cmd["result_json"]) if cmd is not None else None
            command_node_job_id=(
                command_result.get("node_job_id")
                if isinstance(command_result,dict)
                else None
            )

            later=con.execute(
                "SELECT proxy_job_id,last_known_state,created_at_ms "
                "FROM routed_jobs WHERE task_id=? AND "
                "(created_at_ms>? OR (created_at_ms=? AND proxy_job_id>?)) "
                "ORDER BY created_at_ms,proxy_job_id",
                (
                    task_id,int(row["created_at_ms"]),
                    int(row["created_at_ms"]),row["proxy_job_id"],
                ),
            ).fetchall()
            later_terminal=[
                r for r in later if str(r["last_known_state"]) in TERMINAL_JOB_STATES
            ]
            has_later_terminal=bool(later_terminal)
            if has_later_terminal:
                later_terminal_count+=1

            node_job_id=row["node_job_id"]
            if node_job_id is None:
                no_node_job_id_count+=1

            if node_job_id:
                classification="NODE_BOUND_REFRESH_REQUIRED"
            elif command_state=="SUCCEEDED" and command_node_job_id:
                classification="COMMAND_RESULT_CAN_REPAIR_MAPPING"
            elif command_state in TERMINAL_COMMAND_STATES:
                classification="TERMINAL_SUBMIT_COMMAND_WITHOUT_JOB_MAPPING"
            elif device_state=="ONLINE":
                classification="ONLINE_PROXY_REFRESH_REQUIRED"
            else:
                classification="UNRESOLVED_OFFLINE_OR_MISSING_MAPPING"

            by_classification[classification]+=1

            report["rows"].append({
                "proxy_job_id":row["proxy_job_id"],
                "operation_id":row["operation_id"],
                "task_id":task_id,
                "project_id":row["project_id"],
                "device_id":row["device_id"],
                "device_state":device_state,
                "state":state,
                "node_job_id":node_job_id,
                "created_at_ms":int(row["created_at_ms"]),
                "last_seen_at_ms":int(row["last_seen_at_ms"]),
                "submit_command_state":command_state,
                "submit_command_node_job_id":command_node_job_id,
                "classification":classification,
                "has_later_terminal_successor":has_later_terminal,
                "later_job_count":len(later),
                "later_terminal_job_count":len(later_terminal),
                "admission_blocking":True,
            })

        report["summary"]={
            "total_routed_jobs":len(all_jobs),
            "total_nonterminal_routed_jobs":len(nonterminal),
            "blocked_task_lanes":len(blocked_tasks),
            "nonterminal_without_node_job_id":no_node_job_id_count,
            "nonterminal_with_later_terminal_successor":later_terminal_count,
            "by_state":dict(sorted(by_state.items())),
            "by_device_state":dict(sorted(by_device_state.items())),
            "by_classification":dict(sorted(by_classification.items())),
        }
        return report
    finally:
        con.close()


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--output")
    args=p.parse_args()
    report=audit(Path(args.db))
    text=json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)
    if args.output:
        Path(args.output).write_text(text+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
