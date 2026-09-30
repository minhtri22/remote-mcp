from __future__ import annotations

import json
import os

from remotemcp.durable.db import Database
from remotemcp.durable.events import EventRepository
from remotemcp.durable.jobs import JobRepository
from remotemcp.durable.models import JobState
from remotemcp.durable.operations import OperationRepository
from remotemcp.durable.process import atomic_write_json, command_sha256, fingerprint_process, worker_argv
from remotemcp.durable.reconcile import Reconciler


def build(tmp_path):
    runtime=tmp_path/"runtime"; db=Database(runtime); db.bootstrap()
    ops=OperationRepository(db); jobs=JobRepository(db); events=EventRepository(db)
    rec=Reconciler(runtime_dir=runtime,jobs=jobs,events=events,operations=ops,starting_grace_seconds=0)
    return runtime,db,ops,jobs,events,rec


def test_executing_operation_without_evidence_becomes_in_doubt(tmp_path):
    _,_,ops,_,_,rec=build(tmp_path)
    ops.reserve("opx","EVENT_ACK",{"a":1}); ops.mark_executing("opx")
    rec.reconcile_operations()
    assert ops.get("opx")["state"]=="IN_DOUBT"


def test_terminal_marker_terminalizes_exactly_once(tmp_path):
    runtime,_,ops,jobs,events,rec=build(tmp_path)
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    job,_=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    jobs.transition(job["job_id"],("QUEUED",),"RUNNING")
    d=runtime/"jobs"/job["job_id"]; d.mkdir(parents=True)
    atomic_write_json(d/"terminal.json",{
        "job_id":job["job_id"],"launch_nonce":job["launch_nonce"],
        "terminal_state":"SUCCEEDED","exit_code":0,
    })
    rec.reconcile_jobs(); rec.reconcile_jobs()
    assert jobs.get(job["job_id"])["state"]=="SUCCEEDED"
    rows=events.db.query_all("SELECT * FROM events WHERE job_id=? AND terminal=1",(job["job_id"],))
    assert len(rows)==1


def test_missing_launch_commit_is_republished_without_second_worker(tmp_path):
    runtime,_,ops,jobs,_,rec=build(tmp_path)
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    job,_=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    expected=command_sha256(worker_argv(runtime,job["job_id"],job["launch_nonce"]))
    fp=fingerprint_process(os.getpid(),expected); assert fp
    jobs.transition(job["job_id"],("QUEUED",),"RUNNING",
                    worker_fingerprint_json=json.dumps(fp.to_dict()),started_at_ms=1)
    d=runtime/"jobs"/job["job_id"]; d.mkdir(parents=True)
    atomic_write_json(d/"worker.json",{
        "job_id":job["job_id"],"launch_nonce":job["launch_nonce"],
        "phase":"WAITING_FOR_COMMIT","worker_fingerprint":fp.to_dict(),
    })
    assert not (d/"launch.commit").exists()
    rec.reconcile_jobs()
    assert (d/"launch.commit").exists()
    assert jobs.get(job["job_id"])["state"]=="RUNNING"


def test_invalid_running_identity_becomes_lost_and_never_relaunches(tmp_path):
    _,_,ops,jobs,_,rec=build(tmp_path)
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    job,_=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    jobs.transition(job["job_id"],("QUEUED",),"RUNNING",
                    worker_fingerprint_json=json.dumps({
                        "pid":999999,"start_token":"1","executable_canonical":"x","command_sha256":"h"
                    }),
                    started_at_ms=1,last_heartbeat_at_ms=1)
    rec.reconcile_jobs()
    assert jobs.get(job["job_id"])["state"]=="LOST"
    rec.reconcile_jobs()
    assert jobs.get(job["job_id"])["state"]=="LOST"

def test_starting_handshake_command_mismatch_becomes_lost_without_signal(tmp_path):
    runtime,_,ops,jobs,_,rec=build(tmp_path)
    ops.reserve("opm","JOB_SUBMIT",{"x":1}); ops.mark_executing("opm")
    job,_=jobs.create("opm",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    jobs.transition(job["job_id"],("QUEUED",),"STARTING",spawn_started_at_ms=1)
    fp=fingerprint_process(os.getpid(),"wrong-command-hash"); assert fp
    d=runtime/"jobs"/job["job_id"]; d.mkdir(parents=True)
    atomic_write_json(d/"worker.json",{
        "job_id":job["job_id"],"launch_nonce":job["launch_nonce"],
        "phase":"WAITING_FOR_COMMIT","worker_fingerprint":fp.to_dict(),
    })
    rec.reconcile_jobs()
    assert jobs.get(job["job_id"])["state"]=="LOST"


def test_running_valid_worker_is_preserved(tmp_path):
    runtime,_,ops,jobs,_,rec=build(tmp_path)
    ops.reserve("opv","JOB_SUBMIT",{"x":1}); ops.mark_executing("opv")
    job,_=jobs.create("opv",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    expected=command_sha256(worker_argv(runtime,job["job_id"],job["launch_nonce"]))
    fp=fingerprint_process(os.getpid(),expected); assert fp
    jobs.transition(job["job_id"],("QUEUED",),"RUNNING",
                    worker_fingerprint_json=json.dumps(fp.to_dict()),
                    started_at_ms=1,last_heartbeat_at_ms=1)
    d=runtime/"jobs"/job["job_id"]; d.mkdir(parents=True)
    atomic_write_json(d/"worker.json",{
        "job_id":job["job_id"],"launch_nonce":job["launch_nonce"],
        "phase":"PAYLOAD_RUNNING","worker_fingerprint":fp.to_dict(),
    })
    rec.reconcile_jobs()
    assert jobs.get(job["job_id"])["state"]=="RUNNING"
