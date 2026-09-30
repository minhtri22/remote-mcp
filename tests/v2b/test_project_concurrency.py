from __future__ import annotations

import asyncio
import pytest

from remotemcp.durable.errors import DurableError


def test_project_active_limit_is_transactional_and_block_releases_slot(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p",".",1)
        t1=await b.multi.task_create("t1",p["project_id"],"one")
        t2=await b.multi.task_create("t2",p["project_id"],"two")
        c1=await b.multi.task_claim("c1",t1["task_id"],a["agent_id"],a["session_id"])
        with pytest.raises(DurableError) as exc:
            await b.multi.task_claim("c2",t2["task_id"],z["agent_id"],z["session_id"])
        assert exc.value.code=="PROJECT_CONCURRENCY_LIMIT"
        await b.multi.task_block("block",t1["task_id"],c1["lease_token"],c1["lease_epoch"],"waiting")
        c2=await b.multi.task_claim("c3",t2["task_id"],z["agent_id"],z["session_id"])
        assert c2["task_id"]==t2["task_id"]
        assert b.multi.project_status(p["project_id"])["active_tasks"]==1
    asyncio.run(run())