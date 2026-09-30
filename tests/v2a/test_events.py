from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from remotemcp.durable.db import Database
from remotemcp.durable.events import EventRepository
from remotemcp.durable.jobs import JobRepository
from remotemcp.durable.operations import OperationRepository


def setup_job(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()
    ops = OperationRepository(db)
    jobs = JobRepository(db)
    events = EventRepository(db)
    ops.reserve("op1", "JOB_SUBMIT", {"argv": ["python"]})
    ops.mark_executing("op1")
    job, _ = jobs.create("op1", {"argv": ["python"], "workspace_root": str(tmp_path)}, ".")
    ops.succeed("op1", {"job_id": job["job_id"]})
    return db, jobs, events, job


def test_terminal_event_unique_under_concurrent_insert(tmp_path):
    _, _, events, job = setup_job(tmp_path)

    def insert():
        return events.ensure_terminal(
            job["job_id"], "op1", "SUCCEEDED", {"exit_code": 0}
        )

    with ThreadPoolExecutor(max_workers=2) as ex:
        ids = list(ex.map(lambda _: insert(), range(2)))
    assert ids[0] == ids[1]


def test_ack_is_monotonic(tmp_path):
    _, _, events, job = setup_job(tmp_path)
    event_id = events.ensure_terminal(
        job["job_id"], "op1", "SUCCEEDED", {"exit_code": 0}
    )
    assert events.ack("sub", job["job_id"], event_id) == event_id
    assert events.ack("sub", job["job_id"], event_id - 1) == event_id
    assert events.cursor("sub", job["job_id"]) == event_id
