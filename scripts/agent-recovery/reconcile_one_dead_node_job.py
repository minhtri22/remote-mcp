"""Targeted, fail-closed dead job reconciliation for RemoteMCP V3.1.

Requires exact durable/node journal IDs, no live worker/payload fingerprints,
and an old heartbeat. Distinguishes a dead job (LOST) from user-cancelled work.
Never kills processes, reruns experiments, deletes worktrees, or edits science.
"""
import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path


def readonly(p: Path):
    if not p.is_file():
        raise RuntimeError("MISSING_DB:" + str(p))
    con = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--node-runtime", type=Path, required=True)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--proxy-job-id", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--mode", choices=["Audit", "ReconcileDead"], default="Audit")
    a = p.parse_args()
    if not (a.job_id.startswith("job_") and a.proxy_job_id.startswith("rjob_") and a.task_id.startswith("tsk_") and a.project_id.startswith("prj_")):
        raise RuntimeError("TARGET_ID_FORMAT_INVALID")
    release_marker = json.loads((a.release / ".remotemcp-release.json").read_text("utf-8-sig"))
    if release_marker["commit"] != "dabce9a76502dbae541a0eddff0096fb8dfe908a":
        raise RuntimeError("PINNED_NODE_CODE_MISMATCH")
    sys.path.insert(0, str(a.release))

    from remotemcp.durable.db import Database
    from remotemcp.durable.jobs import JobRepository
    from remotemcp.durable.events import EventRepository
    from remotemcp.durable.operations import OperationRepository
    from remotemcp.durable.reconcile import Reconciler
    from remotemcp.durable.process import read_json, verify_fingerprint
    from remotemcp.durable.models import ProcessFingerprint, now_ms

    node_db = a.node_runtime / "node.db"
    durable_dir = a.node_runtime / "durable"
    durable_db = durable_dir / "runtime.db"

    with closing(readonly(durable_db)) as d, closing(readonly(node_db)) as nd:
        row = d.execute("SELECT * FROM jobs WHERE job_id=?", (a.job_id,)).fetchone()
        route = nd.execute(
            "SELECT * FROM node_routed_jobs WHERE proxy_job_id=?",
            (a.proxy_job_id,),
        ).fetchone()
        if row is None or route is None:
            raise RuntimeError("EXACT_NODE_JOB_OR_PROXY_MAPPING_MISSING")
        if route["node_job_id"] != a.job_id or route["task_id"] != a.task_id or route["project_id"] != a.project_id:
            raise RuntimeError("JOB_IDENTITY_OR_TASK_PROVENANCE_MISMATCH")
        all_live=d.execute(
            "SELECT job_id,state FROM jobs "
            "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')"
        ).fetchall()
        other=[x["job_id"] for x in all_live if x["job_id"]!=a.job_id]
        if other: raise RuntimeError("OTHER_NONTERMINAL_JOBS_EXIST")
        routed_live=nd.execute(
            "SELECT proxy_job_id,node_job_id FROM node_routed_jobs "
            "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')"
        ).fetchall()
        if any(x["proxy_job_id"]!=a.proxy_job_id for x in routed_live):
            raise RuntimeError("OTHER_NONTERMINAL_ROUTED_JOBS_EXIST")
        last=int(row["last_heartbeat_at_ms"] or row["started_at_ms"] or row["updated_at_ms"] or row["created_at_ms"])
        if now_ms()-last < 10000:
            raise RuntimeError("JOB_HEARTBEAT_NOT_STALE")
        job_dir=durable_dir / "jobs" / a.job_id
        marker=read_json(job_dir / "terminal.json")
        meta=read_json(job_dir / "worker.json")
        worker_json=(meta or {}).get("worker_fingerprint")
        payload_json=json.loads(row["payload_fingerprint_json"]) if row["payload_fingerprint_json"] else None
        if not worker_json and not payload_json and not marker:
            raise RuntimeError("NO_PROCESS_FINGERPRINT_EVIDENCE")
        for name, fp_json in (("WORKER",worker_json),("PAYLOAD",payload_json)):
            if fp_json:
                try: fp=ProcessFingerprint(**fp_json)
                except TypeError: raise RuntimeError(name+"_FINGERPRINT_INVALID")
                if verify_fingerprint(fp):
                    raise RuntimeError(name+"_PROCESS_IS_STILL_ALIVE")
        summary={"schema":"remotemcp.dead-job-reconciliation.v1","job_id":a.job_id,
                 "proxy_job_id":a.proxy_job_id,"task_id":a.task_id,"project_id":a.project_id,
                 "old_durable_state":row["state"],"old_routed_state":route["state"],
                 "last_heartbeat_ms":last,"worker_fingerprint_live":False,
                 "payload_fingerprint_live":False,"terminal_marker_present":bool(marker),
                 "other_nonterminal_jobs":0,"mode":a.mode,
                 "mutation_authorized":a.mode=="ReconcileDead",
                 "science_replay_authorized":False}

    a.control.mkdir(parents=True, exist_ok=True)
    stamp=str(now_ms())
    evidence=a.control / ("dead-job-reconciliation-"+stamp+"-"+a.job_id+".json")
    if a.mode=="Audit":
        summary["result"]="AUDIT_PASS_NO_MUTATION"
    else:
        if row["state"] not in ("RUNNING","CANCELLING","SUCCEEDED","FAILED","CANCELLED","LOST"):
            raise RuntimeError("UNEXPECTED_DURABLE_JOB_STATE")
        if row["state"] in ("SUCCEEDED","FAILED","CANCELLED","LOST"):
            summary["result"]="ALREADY_TERMINAL_NO_MUTATION"
        else:
            # sqlite backup preserves journal evidence including WAL-consistent contents.
            backup=a.control / ("node-durable-pre-reconcile-"+stamp+".sqlite")
            with sqlite3.connect(durable_db) as con, sqlite3.connect(backup) as dest:
                con.backup(dest)
            summary["pre_mutation_sqlite_backup"]=str(backup)
            db=Database(durable_dir)
            jobs=JobRepository(db); events=EventRepository(db)
            operations=OperationRepository(db)
            reconciler=Reconciler(runtime_dir=durable_dir,jobs=jobs,events=events,
                                  operations=operations,starting_grace_seconds=2)
            current=jobs.get(a.job_id)
            if current["state"] != row["state"] or current["version"] != row["version"]:
                raise RuntimeError("TARGET_JOB_CHANGED_BETWEEN_AUDIT_AND_RECONCILIATION")
            if marker:
                if not reconciler._terminalize_from_marker(current,marker):
                    raise RuntimeError("INVALID_TERMINAL_MARKER_NO_MUTATION")
            else:
                reconciler._mark_lost(current, "RUNNING worker identity is no longer valid")
            resolved=jobs.get(a.job_id)
            if resolved["state"] not in ("SUCCEEDED","FAILED","CANCELLED","LOST"):
                raise RuntimeError("DURABLE_JOB_DID_NOT_TERMINALIZE")
            # Reconcile only the exact paired proxy row, never unrelated jobs.
            with sqlite3.connect(node_db,timeout=8) as con:
                cur=con.execute(
                    "UPDATE node_routed_jobs SET state=?,updated_at_ms=?,"
                    "terminal_at_ms=COALESCE(terminal_at_ms,?) "
                    "WHERE proxy_job_id=? AND node_job_id=? AND task_id=? AND project_id=? "
                    "AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
                    (resolved["state"],now_ms(),now_ms(),a.proxy_job_id,a.job_id,a.task_id,a.project_id),
                )
                if cur.rowcount not in (0,1):
                    raise RuntimeError("ROUTED_RECONCILIATION_CARDINALITY_FAILURE")
            summary["result"]="TARGET_JOB_TERMINALIZED"
            summary["new_job_state"]=resolved["state"]
    with evidence.open("x",encoding="utf-8") as f:
        json.dump(summary,f,indent=2,sort_keys=True)
    print(json.dumps(summary,sort_keys=True))
    print("EVIDENCE="+str(evidence))


if __name__=="__main__":
    main()
