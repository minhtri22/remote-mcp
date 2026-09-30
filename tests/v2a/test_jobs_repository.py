from __future__ import annotations

from remotemcp.durable.db import Database
from remotemcp.durable.jobs import JobRepository
from remotemcp.durable.models import JobState
from remotemcp.durable.operations import OperationRepository


def test_duplicate_operation_creates_one_job_and_launch_nonce(tmp_path):
    db=Database(tmp_path/"runtime"); db.bootstrap()
    ops=OperationRepository(db); jobs=JobRepository(db)
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    j1,c1=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    j2,c2=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    assert c1 is True and c2 is False
    assert j1["job_id"]==j2["job_id"]
    assert j1["launch_nonce"]==j2["launch_nonce"]
    assert j1["launch_nonce"].startswith("ln_")


def test_terminal_job_does_not_change_on_second_terminalize(tmp_path):
    db=Database(tmp_path/"runtime"); db.bootstrap()
    ops=OperationRepository(db); jobs=JobRepository(db)
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    job,_=jobs.create("op1",{"argv":["python"],"workspace_root":str(tmp_path)},".")
    row,changed=jobs.terminalize(job["job_id"],JobState.LOST.value,exit_code=None)
    assert changed and row["state"]=="LOST"
    row2,changed2=jobs.terminalize(job["job_id"],JobState.SUCCEEDED.value,exit_code=0)
    assert changed2 is False
    assert row2["state"]=="LOST"