from __future__ import annotations

import asyncio
import concurrent.futures
import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


def test_competing_claim_exactly_one_winner(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("tc",p["project_id"],"t")

        async def claim(op,agent):
            try:
                return await b.multi.task_claim(op,t["task_id"],agent["agent_id"],agent["session_id"])
            except DurableError as e:
                return e.code

        results=await asyncio.gather(claim("ca",a),claim("cz",z))
        wins=[r for r in results if isinstance(r,dict)]
        assert len(wins)==1
        assert b.multi.task_status(t["task_id"])["state"]=="RUNNING"
    asyncio.run(run())


def test_expiry_to_recoverable_takeover_increments_epoch_and_stales_old_token(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("tc",p["project_id"],"t")
        c1=await b.multi.task_claim("c1",t["task_id"],a["agent_id"],a["session_id"])
        with b.durable.db.transaction() as con:
            con.execute("UPDATE task_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
        b.multi.reconciler.expire_sessions_and_leases()
        assert b.multi.task_status(t["task_id"])["state"]=="RECOVERABLE"
        c2=await b.multi.task_claim("c2",t["task_id"],z["agent_id"],z["session_id"])
        assert c2["lease_epoch"]==c1["lease_epoch"]+1
        with pytest.raises(DurableError) as exc:
            b.multi.leases.validate(t["task_id"],c1["lease_token"],c1["lease_epoch"])
        assert exc.value.code=="LEASE_STALE"
    asyncio.run(run())