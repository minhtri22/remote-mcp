from __future__ import annotations

import asyncio
import sys

import pytest

from conftest import init_git_repo,terminal_state
from remotemcp.durable.errors import DurableError


async def _git_lane(b,name="lane",max_active=4):
    init_git_repo(b.workspace/"repo")
    a=await b.multi.agent_register("reg-"+name,name,"inst-"+name,[])
    p=await b.multi.project_register("proj-"+name,"repo",max_active)
    t=await b.multi.task_create("task-"+name,p["project_id"],name)
    c=await b.multi.task_claim("claim-"+name,t["task_id"],a["agent_id"],a["session_id"])
    return p,t,c


def test_local_same_lane_successor_is_blocked_until_terminal_evidence(make_bundle):
    async def run():
        b=make_bundle(max_jobs=4,poll_ms=20)
        _,t,c=await _git_lane(b)
        await b.durable.start(); await b.multi.start()
        try:
            j1=await b.multi.task_job_submit(
                "j1",t["task_id"],c["lease_token"],c["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(.35)"],
            )
            with pytest.raises(DurableError) as exc:
                await b.multi.task_job_submit(
                    "j2-blocked",t["task_id"],c["lease_token"],c["lease_epoch"],
                    [sys.executable,"-c","print('two')"],
                )
            assert exc.value.code in {"PREDECESSOR_JOB_NOT_TERMINAL","TASK_JOB_ADMISSION_CONFLICT"}
            await terminal_state(b.durable,j1["job_id"],timeout=5)
            j2=await b.multi.task_job_submit(
                "j2",t["task_id"],c["lease_token"],c["lease_epoch"],
                [sys.executable,"-c","print('two')"],
            )
            assert j2["job_id"]!=j1["job_id"]
            rows=b.durable.db.query_all(
                "select sequence,state,job_id from task_job_admissions where task_id=? order by sequence",
                (t["task_id"],),
            )
            assert [r["sequence"] for r in rows]==[1,2]
            assert rows[0]["state"]=="TERMINAL"
            assert rows[1]["state"]=="ACTIVE"
        finally:
            await b.multi.stop(); await b.durable.stop()
    asyncio.run(run())


def test_local_exact_operation_replay_does_not_self_block(make_bundle):
    async def run():
        b=make_bundle(max_jobs=2,poll_ms=20)
        _,t,c=await _git_lane(b)
        await b.durable.start(); await b.multi.start()
        try:
            args=[sys.executable,"-c","import time; time.sleep(.2)"]
            j1=await b.multi.task_job_submit(
                "same-op",t["task_id"],c["lease_token"],c["lease_epoch"],args,
            )
            replay=await b.multi.task_job_submit(
                "same-op",t["task_id"],c["lease_token"],c["lease_epoch"],args,
            )
            assert replay["job_id"]==j1["job_id"]
            assert replay.get("replayed") is True
            rows=b.durable.db.query_all(
                "select * from task_job_admissions where task_id=?",
                (t["task_id"],),
            )
            assert len(rows)==1
        finally:
            await b.multi.stop(); await b.durable.stop()
    asyncio.run(run())


def test_local_different_task_lanes_may_run_concurrently(make_bundle):
    async def run():
        b=make_bundle(max_jobs=4,poll_ms=20)
        init_git_repo(b.workspace/"repo")
        a=await b.multi.agent_register("a","a","ia",[])
        z=await b.multi.agent_register("z","z","iz",[])
        p=await b.multi.project_register("p","repo",2)
        t1=await b.multi.task_create("t1",p["project_id"],"one")
        t2=await b.multi.task_create("t2",p["project_id"],"two")
        c1=await b.multi.task_claim("c1",t1["task_id"],a["agent_id"],a["session_id"])
        c2=await b.multi.task_claim("c2",t2["task_id"],z["agent_id"],z["session_id"])
        await b.durable.start(); await b.multi.start()
        try:
            args=[sys.executable,"-c","import time; time.sleep(.25)"]
            j1=await b.multi.task_job_submit("a1",t1["task_id"],c1["lease_token"],c1["lease_epoch"],args)
            j2=await b.multi.task_job_submit("b1",t2["task_id"],c2["lease_token"],c2["lease_epoch"],args)
            assert j1["job_id"]!=j2["job_id"]
            active=b.durable.db.query_all(
                "select task_id,state from task_job_admissions where state in ('ADMITTING','ACTIVE')"
            )
            assert {r["task_id"] for r in active}=={t1["task_id"],t2["task_id"]}
        finally:
            await b.multi.stop(); await b.durable.stop()
    asyncio.run(run())


@pytest.mark.parametrize("terminal_state",["FAILED","CANCELLED","LOST"])
def test_non_success_terminal_state_releases_lane_with_evidence(make_bundle,terminal_state):
    b=make_bundle()
    _,t,_=asyncio.run(_git_lane(b,name="terminal-"+terminal_state.lower()))
    adm,_=b.multi.job_admissions.reserve(t["task_id"],"op-"+terminal_state,"LOCAL")
    b.multi.job_admissions.bind("op-"+terminal_state,"job-synthetic")
    b.multi.job_admissions.terminalize(
        "op-"+terminal_state,terminal_state,
        {"terminal":True,"state":terminal_state,"terminal_event_id":1},
    )
    nxt,_=b.multi.job_admissions.reserve(
        t["task_id"],"next-"+terminal_state,"LOCAL",
        predecessor_job_id="job-synthetic",
    )
    assert nxt["sequence"]==2
