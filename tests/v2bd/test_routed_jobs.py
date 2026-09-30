from __future__ import annotations

import asyncio,sys
import pytest

from conftest import pair_node,drive,init_git_repo
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


def test_routed_job_proxy_observation_terminal_cache_and_local_guard(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")
        p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",4))
        a=await g.multi.agent_register("a","agent","install",[])
        t=await g.routing.task_create_or_local("t",p["project_id"],"task")
        c=await drive(g,node,g.routing.task_claim_or_local("c",t["task_id"],a["agent_id"],a["session_id"]))
        job=await drive(g,node,g.routing.task_job_submit_or_local(
            "j",t["task_id"],c["lease_token"],c["lease_epoch"],
            [sys.executable,"-c","import os; print(os.environ['REMOTEMCP_DEVICE_ID']); print(os.environ['REMOTEMCP_PROJECT_ID'])"],"."
        ))
        assert job["proxy_job_id"].startswith("rjob_") and job["node_job_id"].startswith("job_")
        await node.jobs.durable.start()
        try:
            for _ in range(200):
                st=node.jobs.durable.job_get(job["node_job_id"])
                if st["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:break
                await asyncio.sleep(.03)
            got=await drive(g,node,g.routing.task_job_get(t["task_id"],job["proxy_job_id"],True))
            logs=await drive(g,node,g.routing.task_job_logs(t["task_id"],job["proxy_job_id"]))
            result=await drive(g,node,g.routing.task_job_result(t["task_id"],job["proxy_job_id"]))
            assert got["state"]=="SUCCEEDED"
            assert dev["device_id"] in logs["data"] and p["project_id"] in logs["data"]
            assert result["terminal"] is True
            with g.durable.db.transaction() as con:
                con.execute("update devices set state='OFFLINE',last_seen_at_ms=? where device_id=?",(now_ms()-120000,dev["device_id"]))
            cached=await g.routing.task_job_result(t["task_id"],job["proxy_job_id"])
            assert cached["terminal"] is True
            with pytest.raises(DurableError) as exc:g.routing.guard_gateway_job_id(job["proxy_job_id"])
            assert exc.value.code=="ROUTED_JOB_USE_TASK_TOOLS"
        finally:
            await node.jobs.durable.stop()
    asyncio.run(run())
