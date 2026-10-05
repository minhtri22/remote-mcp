from __future__ import annotations

import asyncio
import sys

import pytest

from conftest import drive,init_git_repo,pair_node
from remotemcp.durable.errors import DurableError


async def _routed_lane(g,node,dev):
    p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",4))
    a=await g.multi.agent_register("a","agent","install",[])
    t=await drive(g,node,g.routing.task_create_or_local("t",p["project_id"],"task"))
    c=await drive(g,node,g.routing.task_claim_or_local("c",t["task_id"],a["agent_id"],a["session_id"]))
    return p,t,c


def test_routed_same_lane_blocks_successor_then_reconciles_terminal(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")
        _,t,c=await _routed_lane(g,node,dev)
        await node.jobs.durable.start()
        try:
            j1=await drive(g,node,g.routing.task_job_submit_or_local(
                "j1",t["task_id"],c["lease_token"],c["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(.35)"],".",
            ))
            with pytest.raises(DurableError) as exc:
                await drive(g,node,g.routing.task_job_submit_or_local(
                    "j2-blocked",t["task_id"],c["lease_token"],c["lease_epoch"],
                    [sys.executable,"-c","print('two')"],".",
                ))
            assert exc.value.code in {"PREDECESSOR_JOB_NOT_TERMINAL","TASK_JOB_ADMISSION_CONFLICT"}
            for _ in range(200):
                st=node.jobs.durable.job_get(j1["node_job_id"])
                if st["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
                    break
                await asyncio.sleep(.02)
            j2=await drive(g,node,g.routing.task_job_submit_or_local(
                "j2",t["task_id"],c["lease_token"],c["lease_epoch"],
                [sys.executable,"-c","print('two')"],".",
            ))
            assert j2["proxy_job_id"]!=j1["proxy_job_id"]
            first=g.durable.db.query_one(
                "select * from task_job_admissions where operation_id='j1'"
            )
            assert first["state"]=="TERMINAL"
            assert first["terminal_evidence_json"]
        finally:
            await node.jobs.durable.stop()
    asyncio.run(run())


def test_routed_unresolved_legacy_predecessor_fails_closed(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt2","node2")
        p,t,c=await _routed_lane(g,node,dev)
        op,_=g.routing._reserve(
            "legacy-op","TASK_JOB_SUBMIT",
            {"task_id":t["task_id"],"lease_epoch":int(c["lease_epoch"]),"argv":["x"],"cwd":"."},
            agent_id="",project_id=p["project_id"],task_id=t["task_id"],
        )
        with g.durable.db.transaction() as con:
            g.routing.routed_jobs.create(
                con,"legacy-op",t["task_id"],p["project_id"],dev["device_id"]
            )
        with pytest.raises(DurableError) as exc:
            await drive(g,node,g.routing.task_job_submit_or_local(
                "successor",t["task_id"],c["lease_token"],c["lease_epoch"],
                [sys.executable,"-c","print('never')"],".",
            ))
        assert exc.value.code=="PREDECESSOR_STATE_UNRESOLVED"
        assert g.routing.routed_jobs.by_operation("successor") is None
    asyncio.run(run())


def test_routed_different_task_lanes_can_admit_concurrently(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt3","node3")
        p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",2))
        a=await g.multi.agent_register("a","a","ia",[])
        z=await g.multi.agent_register("z","z","iz",[])
        t1=await drive(g,node,g.routing.task_create_or_local("t1",p["project_id"],"one"))
        t2=await drive(g,node,g.routing.task_create_or_local("t2",p["project_id"],"two"))
        c1=await drive(g,node,g.routing.task_claim_or_local("c1",t1["task_id"],a["agent_id"],a["session_id"]))
        c2=await drive(g,node,g.routing.task_claim_or_local("c2",t2["task_id"],z["agent_id"],z["session_id"]))
        await node.jobs.durable.start()
        try:
            args=[sys.executable,"-c","import time; time.sleep(.2)"]
            j1=await drive(g,node,g.routing.task_job_submit_or_local("a1",t1["task_id"],c1["lease_token"],c1["lease_epoch"],args,"."))
            j2=await drive(g,node,g.routing.task_job_submit_or_local("b1",t2["task_id"],c2["lease_token"],c2["lease_epoch"],args,"."))
            assert j1["proxy_job_id"]!=j2["proxy_job_id"]
        finally:
            await node.jobs.durable.stop()
    asyncio.run(run())
