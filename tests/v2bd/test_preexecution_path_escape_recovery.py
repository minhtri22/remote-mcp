from __future__ import annotations

import asyncio
import sys

import pytest

from conftest import drive, init_git_repo, pair_node
from remotemcp.durable.errors import DurableError


def test_path_escape_recovery_preserves_proxy_and_original_argv(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")

        project=await drive(
            g,node,
            g.routing.project_register_on_device(
                "project-op",dev["device_id"],"repo",4
            ),
        )
        agent=await g.multi.agent_register("agent-op","agent","install",[])
        task=await g.routing.task_create_or_local(
            "task-op",project["project_id"],"task"
        )
        claim=await drive(
            g,node,
            g.routing.task_claim_or_local(
                "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
            ),
        )

        artifact=tmp_path/"recovery-evidence.json"
        code=(
            "from pathlib import Path;"
            f"Path({str(artifact)!r}).write_text('RECOVERED',encoding='utf-8')"
        )
        argv=[sys.executable,"-c",code]
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,
                g.routing.task_job_submit_or_local(
                    "science-one-shot",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    argv,
                    "../../../../outside-task-root",
                    [str(artifact)],
                ),
            )
        assert exc.value.code=="PATH_ESCAPE"

        listed=g.routing.task_jobs_or_local(task["task_id"])["jobs"]
        assert len(listed)==1
        original=listed[0]
        assert original["node_job_id"] is None
        assert original["state"]=="QUEUED"
        proxy=original["proxy_job_id"]

        status=g.routing.task_job_recovery_status(task["task_id"],proxy)
        assert status["recoverable"] is True
        assert status["recovery_kind"]=="PATH_ESCAPE_TO_MANAGED_TASK_ROOT"
        assert status["original_submit"]["error_code"]=="PATH_ESCAPE"
        assert status["original_submit"]["cwd"]=="../../../../outside-task-root"
        argv_sha=status["original_submit"]["argv_sha256"]
        assert argv_sha

        with pytest.raises(DurableError) as exc:
            await g.routing.task_job_recover_path_escape(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                proxy,
                argv_sha,
                False,
            )
        assert exc.value.code=="RECOVERY_ACK_REQUIRED"

        with pytest.raises(DurableError) as exc:
            await g.routing.task_job_recover_path_escape(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                proxy,
                "0"*64,
                True,
            )
        assert exc.value.code=="COMMAND_CONFLICT"

        recovered=await drive(
            g,node,
            g.routing.task_job_recover_path_escape(
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                proxy,
                argv_sha,
                True,
            ),
        )
        assert recovered["proxy_job_id"]==proxy
        assert recovered["node_job_id"].startswith("job_")
        assert recovered["same_proxy_identity_preserved"] is True
        assert recovered["recovery_kind"]=="PATH_ESCAPE_TO_MANAGED_TASK_ROOT"
        assert recovered["original_cwd"]=="../../../../outside-task-root"
        assert recovered["recovery_cwd"]=="."
        node_job_id=recovered["node_job_id"]

        for _ in range(300):
            state=node.jobs.durable.job_get(node_job_id)["state"]
            if state in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
                break
            await asyncio.sleep(.02)
        assert node.jobs.durable.job_get(node_job_id)["state"]=="SUCCEEDED"

        result=await drive(
            g,node,g.routing.task_job_result(task["task_id"],proxy)
        )
        assert result["terminal"] is True
        assert result["state"]=="SUCCEEDED"

        after=g.routing.task_jobs_or_local(task["task_id"])["jobs"]
        assert len(after)==1
        assert after[0]["proxy_job_id"]==proxy
        assert after[0]["node_job_id"]==node_job_id

        replay=await g.routing.task_job_recover_path_escape(
            task["task_id"],
            claim["lease_token"],
            claim["lease_epoch"],
            proxy,
            argv_sha,
            True,
        )
        assert replay["proxy_job_id"]==proxy
        assert replay["node_job_id"]==node_job_id
        assert replay["replayed"] is True

        post=g.routing.task_job_recovery_status(task["task_id"],proxy)
        assert post["recoverable"] is False
        assert post["recovery_reason"]=="NODE_JOB_ALREADY_MAPPED"

        artifact_status=await drive(
            g,node,g.routing.task_job_artifact_status(
                task["task_id"],proxy,str(artifact)
            )
        )
        assert artifact_status["readable"] is True
        assert artifact_status["readback_state"]=="READY"

        await node.jobs.durable.stop()

    asyncio.run(run())


def test_path_escape_recovery_rejects_non_path_escape_failure(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt2","node2")

        project=await drive(
            g,node,
            g.routing.project_register_on_device(
                "project-op",dev["device_id"],"repo",4
            ),
        )
        agent=await g.multi.agent_register("agent-op","agent","install",[])
        task=await g.routing.task_create_or_local(
            "task-op",project["project_id"],"task"
        )
        claim=await drive(
            g,node,
            g.routing.task_claim_or_local(
                "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
            ),
        )

        with pytest.raises(DurableError):
            await drive(
                g,node,
                g.routing.task_job_submit_or_local(
                    "bad-exec",
                    task["task_id"],
                    claim["lease_token"],
                    claim["lease_epoch"],
                    ["definitely-not-an-allowed-executable"],
                    ".",
                ),
            )

        row=g.routing.task_jobs_or_local(task["task_id"])["jobs"][0]
        status=g.routing.task_job_recovery_status(
            task["task_id"],row["proxy_job_id"]
        )
        assert status["recoverable"] is False
        assert status["recovery_reason"]=="UNSUPPORTED_PREEXECUTION_FAILURE"

    asyncio.run(run())
