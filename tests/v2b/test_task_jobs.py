from __future__ import annotations

import asyncio
import sys
import pytest

from conftest import terminal_state
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


def test_non_git_task_job_requires_root_tree_lease_and_survives_takeover(make_bundle):
    async def run():
        b=make_bundle(poll_ms=20)
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("t",p["project_id"],"one")
        c1=await b.multi.task_claim("c1",t["task_id"],a["agent_id"],a["session_id"])
        with pytest.raises(DurableError) as exc:
            await b.multi.task_job_submit("jbad",t["task_id"],c1["lease_token"],c1["lease_epoch"],[sys.executable,"-c","print('x')"])
        assert exc.value.code=="LEASE_REQUIRED"
        await b.multi.path_lease_acquire("root",t["task_id"],c1["lease_token"],c1["lease_epoch"],".","TREE")
        await b.durable.start(); await b.multi.start()
        try:
            job=await b.multi.task_job_submit(
                "job",t["task_id"],c1["lease_token"],c1["lease_epoch"],
                [sys.executable,"-c","import time; print('start',flush=True); time.sleep(2); print('done',flush=True)"]
            )
            for _ in range(100):
                if b.durable.job_get(job["job_id"])["state"]=="RUNNING": break
                await asyncio.sleep(.03)
            with b.durable.db.transaction() as con:
                con.execute("UPDATE task_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
                con.execute("UPDATE path_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
            b.multi.reconciler.expire_sessions_and_leases()
            assert b.multi.task_status(t["task_id"])["state"]=="RECOVERABLE"
            assert b.durable.job_get(job["job_id"])["state"] in {"STARTING","RUNNING","SUCCEEDED"}
            c2=await b.multi.task_claim("c2",t["task_id"],z["agent_id"],z["session_id"])
            assert any(x["job_id"]==job["job_id"] for x in b.multi.task_jobs(t["task_id"])["jobs"])
            with pytest.raises(DurableError):
                await b.multi.task_job_cancel("cancel-old",t["task_id"],c1["lease_token"],c1["lease_epoch"],job["job_id"])
            await terminal_state(b.durable,job["job_id"],timeout=8)
        finally:
            await b.multi.stop(); await b.durable.stop()
    asyncio.run(run())