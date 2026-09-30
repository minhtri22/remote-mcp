from __future__ import annotations

import asyncio
import pytest

from remotemcp.durable.errors import DurableError


def test_non_overlapping_files_can_lease_but_tree_conflicts(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p",".",4)
        t1=await b.multi.task_create("t1",p["project_id"],"one")
        t2=await b.multi.task_create("t2",p["project_id"],"two")
        c1=await b.multi.task_claim("c1",t1["task_id"],a["agent_id"],a["session_id"])
        c2=await b.multi.task_claim("c2",t2["task_id"],z["agent_id"],z["session_id"])
        l1=await b.multi.path_lease_acquire("l1",t1["task_id"],c1["lease_token"],c1["lease_epoch"],"a.txt","FILE")
        l2=await b.multi.path_lease_acquire("l2",t2["task_id"],c2["lease_token"],c2["lease_epoch"],"b.txt","FILE")
        assert l1["path"]=="a.txt" and l2["path"]=="b.txt"
        with pytest.raises(DurableError) as exc:
            await b.multi.path_lease_acquire("l3",t2["task_id"],c2["lease_token"],c2["lease_epoch"],".","TREE")
        assert exc.value.code=="LEASE_CONFLICT"
    asyncio.run(run())


def test_expired_path_lease_cannot_authorize_mutation(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("t",p["project_id"],"one")
        c=await b.multi.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        await b.multi.path_lease_acquire("l",t["task_id"],c["lease_token"],c["lease_epoch"],"a.txt","FILE")
        with b.durable.db.transaction() as con:
            con.execute("UPDATE path_leases SET expires_at_ms=0 WHERE task_id=?",(t["task_id"],))
        assert not b.multi.leases.has_covering(t["task_id"],"a.txt")
    asyncio.run(run())