from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from remotemcp.durable.config import safe_child_env
from remotemcp.durable.db import Database
from remotemcp.durable.jobs import JobRepository
from remotemcp.durable.operations import OperationRepository
from remotemcp.durable.process import worker_argv


def test_worker_cannot_spawn_payload_before_launch_commit(tmp_path):
    runtime=tmp_path/"runtime"; root=tmp_path/"workspace"; root.mkdir()
    db=Database(runtime); db.bootstrap()
    ops=OperationRepository(db); jobs=JobRepository(db)
    marker=root/"payload-started.txt"
    code=f"from pathlib import Path; Path(r'{marker}').write_text('started')"
    ops.reserve("op1","JOB_SUBMIT",{"x":1}); ops.mark_executing("op1")
    job,_=jobs.create("op1",{"argv":[sys.executable,"-c",code],"workspace_root":str(root)},".")
    jobs.transition(job["job_id"],("QUEUED",),"STARTING",spawn_started_at_ms=1)

    argv=worker_argv(runtime,job["job_id"],job["launch_nonce"])
    env=safe_child_env(root)
    env["PYTHONPATH"]=str(Path(__file__).resolve().parents[2])
    proc=subprocess.Popen(argv,cwd=str(root),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        worker_json=runtime/"jobs"/job["job_id"]/"worker.json"
        deadline=time.time()+5
        while time.time()<deadline and not worker_json.exists():
            time.sleep(0.05)
        assert worker_json.exists()
        meta=json.loads(worker_json.read_text(encoding="utf-8"))
        assert meta["phase"]=="WAITING_FOR_COMMIT"
        time.sleep(0.3)
        assert not marker.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
