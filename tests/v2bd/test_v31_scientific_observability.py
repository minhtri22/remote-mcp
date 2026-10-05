from __future__ import annotations

import asyncio
import sys

import pytest

from conftest import drive, init_git_repo, pair_node
from remotemcp.durable.errors import DurableError


async def _setup_routed_git(g,tmp_path,name="v31"):
    root=tmp_path/name
    root.mkdir()
    init_git_repo(root/"repo","V31")
    node,dev=await pair_node(g,root,tmp_path/(name+"-rt"),name)
    project=await drive(
        g,node,
        g.routing.project_register_on_device(
            "project-op",dev["device_id"],"repo",4
        ),
    )
    agent=await g.multi.agent_register("agent-op","agent","client",[])
    task=await drive(
        g,node,
        g.routing.task_create_or_local(
            "task-op",project["project_id"],"science","HEAD"
        ),
    )
    claim=await drive(
        g,node,
        g.routing.task_claim_or_local(
            "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
        ),
    )
    return node,dev,project,task,claim


def test_v31_schema_is_applied(make_gateway):
    g=make_gateway()
    row=g.durable.db.query_one(
        "select version from schema_migrations where version=5"
    )
    assert row is not None
    routed={
        r["name"] for r in g.durable.db.query_all("pragma table_info(routed_jobs)")
    }
    assert {"execution_key","argv_sha256","cwd"}<=routed
    bindings={
        r["name"]
        for r in g.durable.db.query_all("pragma table_info(project_device_bindings)")
    }
    assert {"lifecycle_state","superseded_by_project_id"}<=bindings


def test_exactly_once_scientific_submit_deduplicates_across_operation_ids(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        node,_,_,task,claim=await _setup_routed_git(g,tmp_path,"dedupe")
        await node.jobs.durable.start()
        try:
            argv=[sys.executable,"-c","import time; time.sleep(1.0)"]
            first=await drive(
                g,node,
                g.routing.task_job_submit_once_or_local(
                    "science-op-1",task["task_id"],claim["lease_token"],
                    claim["lease_epoch"],"LLMRNP2_E",argv,".",[]
                ),
            )
            second=await g.routing.task_job_submit_once_or_local(
                "science-op-2",task["task_id"],claim["lease_token"],
                claim["lease_epoch"],"LLMRNP2_E",argv,".",[]
            )
            assert first["proxy_job_id"]==second["proxy_job_id"]
            assert first["execution_key"]==second["execution_key"]
            assert second["exactly_once"] is True
            assert second["adopted_existing_execution"] is True
            rows=g.durable.db.query_all(
                "select * from routed_jobs where execution_key=?",
                (first["execution_key"],),
            )
            assert len(rows)==1
        finally:
            await node.jobs.durable.stop()
    asyncio.run(run())


def test_zero_command_job_inspect_uses_signed_active_job_summary(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        node,dev,_,task,claim=await _setup_routed_git(g,tmp_path,"observe")
        await node.jobs.durable.start()
        try:
            job=await drive(
                g,node,
                g.routing.task_job_submit_once_or_local(
                    "science-observe",task["task_id"],claim["lease_token"],
                    claim["lease_epoch"],"LLMRNP2_R",
                    [sys.executable,"-c","import time; time.sleep(1.0)"],".",[]
                ),
            )
            capacity=node.jobs.capacity_snapshot()
            g.routing.heartbeat_http(
                g.routing.devices.get(dev["device_id"]),
                {
                    "active_node_jobs":capacity["active_node_jobs"],
                    "active_node_jobs_unresolved":capacity["unresolved_node_jobs"],
                    "capacity_reconciliation_complete":capacity[
                        "capacity_reconciliation_complete"
                    ],
                    "candidate_nonterminal_routed_jobs":capacity[
                        "candidate_nonterminal_routed_jobs"
                    ],
                    "active_job_summaries":capacity["active_job_summaries"],
                    "node_attestation":{
                        "source_dir":"test-source",
                        "release_commit":"a"*40,
                        "execution_root":str(node.config.root),
                        "runtime_dir":str(node.config.runtime_dir),
                        "schema_version":1,
                    },
                },
            )
            before=g.durable.db.query_one(
                "select count(*) as n from device_commands"
            )["n"]
            observed=g.routing.task_job_inspect(task["task_id"],job["proxy_job_id"])
            after=g.durable.db.query_one(
                "select count(*) as n from device_commands"
            )["n"]
            assert after==before
            assert observed["observation_refresh_command_created"] is False
            assert observed["lease_required"] is False
            assert observed["scientific_rerun_required"] is False
            assert observed["execution_key"]==job["execution_key"]
            assert observed["active_job_match"]["proxy_job_id"]==job["proxy_job_id"]
            assert observed["observation_source"]=="SIGNED_NODE_HEARTBEAT_ACTIVE_JOB"
            assert observed["provenance_resolved"] is True
            status=g.routing.device_status(dev["device_id"])
            assert status["node_attestation"]["execution_root"]==str(node.config.root)
            assert status["active_job_summaries"]
        finally:
            await node.jobs.durable.stop()
    asyncio.run(run())


def test_pending_readback_returns_observation_state_not_execution_failure(make_gateway,tmp_path,monkeypatch):
    async def run():
        g=make_gateway()
        node,_,_,task,claim=await _setup_routed_git(g,tmp_path,"pending")
        job=await drive(
            g,node,
            g.routing.task_job_submit_or_local(
                "ordinary-job",task["task_id"],claim["lease_token"],
                claim["lease_epoch"],[sys.executable,"-c","print('x')"],"."
            ),
        )

        async def pending(*args,**kwargs):
            raise DurableError("DEVICE_COMMAND_PENDING","refresh still pending")

        monkeypatch.setattr(g.routing,"_route_step",pending)
        got=await g.routing.task_job_get(task["task_id"],job["proxy_job_id"],True)
        logs=await g.routing.task_job_logs(task["task_id"],job["proxy_job_id"])
        result=await g.routing.task_job_result(task["task_id"],job["proxy_job_id"])
        assert got["readback_state"]=="OBSERVATION_REFRESH_PENDING"
        assert logs["readback_state"]=="OBSERVATION_REFRESH_PENDING"
        assert result["result_readback_state"]=="OBSERVATION_REFRESH_PENDING"
        assert got["scientific_rerun_required"] is False
        assert logs["automatic_scientific_rerun_for_readback_forbidden"] is True
    asyncio.run(run())


def test_historical_binding_blocks_new_tasks_but_preserves_project(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"historical"
        root.mkdir()
        init_git_repo(root/"old","OLD")
        init_git_repo(root/"new","NEW")
        node,dev=await pair_node(g,root,tmp_path/"historical-rt","historical")
        oldp=await drive(
            g,node,g.routing.project_register_on_device(
                "old-project",dev["device_id"],"old",4
            )
        )
        newp=await drive(
            g,node,g.routing.project_register_on_device(
                "new-project",dev["device_id"],"new",4
            )
        )
        dep=g.routing.project_binding_deprecate(
            "deprecate-old",oldp["project_id"],newp["project_id"]
        )
        assert dep["binding_lifecycle_state"]=="HISTORICAL"
        assert dep["eligible_for_new_tasks"] is False
        status=g.routing.project_status_or_local(oldp["project_id"])
        assert status["eligible_for_new_tasks"] is False
        assert status["superseded_by_project_id"]==newp["project_id"]
        with pytest.raises(DurableError) as exc:
            await g.routing.task_create_or_local(
                "forbidden-task",oldp["project_id"],"must-not-open","HEAD"
            )
        assert exc.value.code=="PROJECT_BINDING_HISTORICAL"
    asyncio.run(run())
