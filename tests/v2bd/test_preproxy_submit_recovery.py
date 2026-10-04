from __future__ import annotations

import asyncio
import hashlib
import sys

import pytest

from conftest import drive, init_git_repo, pair_node
from remotemcp.durable.errors import DurableError


async def _wait_terminal(node,node_job_id):
    await node.jobs.durable.start()
    for _ in range(400):
        state=node.jobs.durable.job_get(node_job_id)["state"]
        if state in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
            return state
        await asyncio.sleep(.01)
    raise AssertionError("job did not reach terminal state")


async def _setup(make_gateway,tmp_path,name="node"):
    g=make_gateway()
    root=tmp_path/name
    root.mkdir()
    init_git_repo(root/"repo","NODE")
    node,dev=await pair_node(g,root,tmp_path/(name+"-rt"),name)
    project=await drive(
        g,node,g.routing.project_register_on_device(
            "project-op",dev["device_id"],"repo",4
        ),
    )
    agent=await g.multi.agent_register("agent-op","agent","install",[])
    task=await g.routing.task_create_or_local(
        "task-op",project["project_id"],"task"
    )
    claim=await drive(
        g,node,g.routing.task_claim_or_local(
            "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
        ),
    )
    return g,node,dev,project,agent,task,claim


def test_preproxy_admission_failure_recovers_same_logical_submit(make_gateway,tmp_path):
    async def run():
        g,node,dev,project,agent,task,claim=await _setup(make_gateway,tmp_path)

        first=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "first-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(0.35); print('first')"],
                ".",
            ),
        )
        assert first["node_job_id"].startswith("job_")

        artifact=tmp_path/"recovered-evidence"/"qa-result.json"
        second_argv=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                f"p=Path({str(artifact)!r});"
                "p.parent.mkdir(parents=True,exist_ok=True);"
                "p.write_text('qa-pass',encoding='utf-8')"
            ),
        ]
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_job_submit_or_local(
                    "qa-op",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    second_argv,
                    ".",
                    [str(artifact)],
                ),
            )
        assert exc.value.code=="PREDECESSOR_JOB_NOT_TERMINAL"
        assert g.routing.routed_jobs.by_operation("qa-op") is None

        status=g.routing.task_job_submit_failure_status(task["task_id"],"qa-op")
        assert status["phase"]=="BEFORE_ADMISSION"
        assert status["intent_available"] is True
        assert status["preproxy_recoverable"] is True
        assert status["scientific_execution_started"] is False
        assert status["recommended_action"]=="RECOVER_PREPROXY_SUBMIT"
        frozen=status["frozen_submit_intent"]
        assert frozen["argv_sha256"]

        assert await _wait_terminal(node,first["node_job_id"])=="SUCCEEDED"

        recovered=await drive(
            g,node,g.routing.task_job_recover_preproxy_submit(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                "qa-op",
                frozen["argv_sha256"],
            ),
        )
        assert recovered["recovery_kind"]=="PREPROXY_SAME_LOGICAL_SUBMIT"
        assert recovered["original_operation_id"]=="qa-op"
        assert recovered["same_logical_submit_preserved"] is True
        assert recovered["scientific_execution_was_started_before_recovery"] is False
        assert recovered["proxy_job_id"].startswith("rjob_")
        assert recovered["node_job_id"].startswith("job_")

        assert await _wait_terminal(node,recovered["node_job_id"])=="SUCCEEDED"
        result=await drive(
            g,node,g.routing.task_job_result(
                task["task_id"],recovered["proxy_job_id"]
            ),
        )
        assert result["terminal"] is True
        assert result["state"]=="SUCCEEDED"

        artifact_status=await drive(
            g,node,g.routing.task_job_artifact_status(
                task["task_id"],recovered["proxy_job_id"],str(artifact)
            ),
        )
        assert artifact_status["readback_state"]=="READY"
        expected=hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert artifact_status["sha256"]==expected
        artifact_read=await drive(
            g,node,g.routing.task_job_artifact_read(
                task["task_id"],recovered["proxy_job_id"],str(artifact),expected
            ),
        )
        assert artifact_read["content"]=="qa-pass"

        jobs=g.routing.task_jobs_or_local(task["task_id"])["jobs"]
        assert len(jobs)==2
        recovered_rows=[j for j in jobs if j["proxy_job_id"]==recovered["proxy_job_id"]]
        assert len(recovered_rows)==1

        after=g.routing.task_job_submit_failure_status(task["task_id"],"qa-op")
        assert after["phase"]=="TERMINAL"
        assert after["recommended_action"]=="POSTRUN_EVIDENCE_OR_ADJUDICATION"
        assert after["scientific_execution_started"] is True

        await node.jobs.durable.stop()

    asyncio.run(run())


def test_preproxy_recovery_waits_for_authoritative_predecessor(make_gateway,tmp_path):
    async def run():
        g,node,dev,project,agent,task,claim=await _setup(
            make_gateway,tmp_path,"node-wait"
        )

        first=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "first-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(0.4)"],
                ".",
            ),
        )
        with pytest.raises(DurableError):
            await drive(
                g,node,g.routing.task_job_submit_or_local(
                    "blocked-op",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    [sys.executable,"-c","print('later')"],
                    ".",
                ),
            )
        status=g.routing.task_job_submit_failure_status(
            task["task_id"],"blocked-op"
        )
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_job_recover_preproxy_submit(
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    "blocked-op",
                    status["frozen_submit_intent"]["argv_sha256"],
                ),
            )
        assert exc.value.code=="PREDECESSOR_JOB_NOT_TERMINAL"
        assert g.routing.routed_jobs.by_operation("blocked-op") is None

        assert await _wait_terminal(node,first["node_job_id"])=="SUCCEEDED"
        await node.jobs.durable.stop()

    asyncio.run(run())


def test_legacy_preproxy_intent_can_only_be_adopted_by_exact_request_hash(make_gateway,tmp_path):
    async def run():
        g,node,dev,project,agent,task,claim=await _setup(
            make_gateway,tmp_path,"node-legacy-preproxy"
        )

        first=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "first-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(0.3)"],
                ".",
            ),
        )
        original_argv=[sys.executable,"-c","print('legacy-qa')"]
        with pytest.raises(DurableError):
            await drive(
                g,node,g.routing.task_job_submit_or_local(
                    "legacy-qa-op",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    original_argv,
                    ".",
                ),
            )

        intent_id=g.routing._submit_intent_operation_id("legacy-qa-op")
        with g.durable.db.transaction() as con:
            con.execute("DELETE FROM operations WHERE operation_id=?",(intent_id,))

        status=g.routing.task_job_submit_failure_status(
            task["task_id"],"legacy-qa-op"
        )
        assert status["intent_available"] is False
        assert status["recommended_action"]=="ADOPT_LEGACY_SUBMIT_INTENT"

        with pytest.raises(DurableError) as exc:
            g.routing.task_job_adopt_legacy_submit_intent(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                "legacy-qa-op",
                [sys.executable,"-c","print('different')"],
                ".",
                [],
            )
        assert exc.value.code=="LEGACY_SUBMIT_INTENT_MISMATCH"

        adopted=g.routing.task_job_adopt_legacy_submit_intent(
            task["task_id"],
            claim["lease_token"],
            claim["lease_epoch"],
            "legacy-qa-op",
            original_argv,
            ".",
            [],
        )
        assert adopted["intent_adopted"] is True
        assert adopted["argv_sha256"]

        assert await _wait_terminal(node,first["node_job_id"])=="SUCCEEDED"
        recovered=await drive(
            g,node,g.routing.task_job_recover_preproxy_submit(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                "legacy-qa-op",
                adopted["argv_sha256"],
            ),
        )
        assert recovered["same_logical_submit_preserved"] is True
        assert await _wait_terminal(node,recovered["node_job_id"])=="SUCCEEDED"

        await node.jobs.durable.stop()

    asyncio.run(run())


def test_preproxy_recovery_rejects_wrong_argv_hash(make_gateway,tmp_path):
    async def run():
        g,node,dev,project,agent,task,claim=await _setup(
            make_gateway,tmp_path,"node-hash"
        )
        first=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "first-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c","import time; time.sleep(0.3)"],
                ".",
            ),
        )
        with pytest.raises(DurableError):
            await drive(
                g,node,g.routing.task_job_submit_or_local(
                    "qa-op",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    [sys.executable,"-c","print('qa')"],
                    ".",
                ),
            )
        assert await _wait_terminal(node,first["node_job_id"])=="SUCCEEDED"

        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_job_recover_preproxy_submit(
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    "qa-op",
                    "0"*64,
                ),
            )
        assert exc.value.code=="COMMAND_CONFLICT"
        assert g.routing.routed_jobs.by_operation("qa-op") is None

        await node.jobs.durable.stop()

    asyncio.run(run())
