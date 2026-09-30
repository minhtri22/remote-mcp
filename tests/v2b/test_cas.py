from __future__ import annotations

import asyncio
import hashlib
import os
import pytest

from remotemcp.durable.errors import DurableError


def h(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def test_cas_create_update_and_mismatch_zero_write(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("t",p["project_id"],"one")
        c=await b.multi.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        await b.multi.path_lease_acquire("l",t["task_id"],c["lease_token"],c["lease_epoch"],"x.txt","FILE")
        r1=await b.multi.file_write_cas("w1",t["task_id"],c["lease_token"],c["lease_epoch"],"x.txt","MISSING","one")
        assert r1["before_hash"]=="MISSING"
        before=h(b"one")
        r2=await b.multi.file_edit_cas("w2",t["task_id"],c["lease_token"],c["lease_epoch"],"x.txt",before,"one","two")
        assert r2["after_hash"]==h(b"two")
        with pytest.raises(DurableError) as exc:
            await b.multi.file_write_cas("w3",t["task_id"],c["lease_token"],c["lease_epoch"],"x.txt",before,"bad")
        assert exc.value.code=="CAS_MISMATCH"
        assert (b.workspace/"x.txt").read_text(encoding="utf-8")=="two"
    asyncio.run(run())


def test_cas_reconcile_after_replace_before_operation_result(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("t",p["project_id"],"one")
        c=await b.multi.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        await b.multi.path_lease_acquire("l",t["task_id"],c["lease_token"],c["lease_epoch"],"x.txt","FILE")
        target=b.workspace/"x.txt"; target.write_text("old",encoding="utf-8")
        expected=h(b"old"); intended=h(b"new")
        op,_=b.durable.operations.reserve("crash-op","FILE_WRITE_CAS",{"task_id":t["task_id"],"path":"x.txt","expected_sha256":expected,"content_sha256":intended},agent_id=a["agent_id"],project_id=p["project_id"],task_id=t["task_id"])
        b.durable.operations.mark_executing("crash-op")
        temp=b.workspace/".x.txt.crash-op.cas.tmp"; temp.write_text("new",encoding="utf-8")
        b.multi.cas._prepare("crash-op",t["task_id"],"x.txt",expected,intended,temp.name)
        os.replace(temp,target)
        row=b.durable.db.query_one("SELECT * FROM cas_mutations WHERE operation_id='crash-op'")
        out=b.multi.cas.reconcile_one(row)
        assert out["after_hash"]==intended
        assert b.durable.operations.get("crash-op")["state"]=="SUCCEEDED"
        assert target.read_text(encoding="utf-8")=="new"
    asyncio.run(run())