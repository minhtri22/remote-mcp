from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import pytest

from conftest import drive, init_git_repo, pair_node
from remotemcp.durable.errors import DurableError


async def _wait_terminal(node,node_job_id):
    await node.jobs.durable.start()
    for _ in range(300):
        state=node.jobs.durable.job_get(node_job_id)["state"]
        if state in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
            return state
        await asyncio.sleep(.02)
    raise AssertionError("job did not reach terminal state")


def test_declared_external_artifact_readback_after_terminal(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")

        project=await drive(
            g,node,g.routing.project_register_on_device(
                "project-op",dev["device_id"],"repo",4
            ),
        )
        agent=await g.multi.agent_register("agent-op","agent","install",[])
        task=await drive(
            g,node,g.routing.task_create_or_local(
                "task-op",project["project_id"],"task"
            )
        )
        claim=await drive(
            g,node,g.routing.task_claim_or_local(
                "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
            ),
        )

        artifact=tmp_path/"external-evidence"/"result.json"
        payload={"verdict":"PASS","value":17}
        artifact_text=json.dumps(payload,sort_keys=True)
        code=(
            "from pathlib import Path;"
            f"p=Path({str(artifact)!r});"
            "p.parent.mkdir(parents=True,exist_ok=True);"
            f"p.write_text({artifact_text!r},encoding='utf-8')"
        )
        job=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "science-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c",code],
                ".",
                [str(artifact)],
            ),
        )
        assert job["proxy_job_id"].startswith("rjob_")
        assert await _wait_terminal(node,job["node_job_id"])=="SUCCEEDED"

        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_read_file(
                    task["task_id"],str(artifact),0,120000
                ),
            )
        assert exc.value.code=="PATH_ESCAPE"

        status=await drive(
            g,node,g.routing.task_job_artifact_status(
                task["task_id"],job["proxy_job_id"],str(artifact)
            ),
        )
        expected=hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert status["readable"] is True
        assert status["readback_state"]=="READY"
        assert status["sha256"]==expected
        assert status["scientific_rerun_required"] is False
        assert status["automatic_scientific_rerun_for_readback_forbidden"] is True

        first=await drive(
            g,node,g.routing.task_job_artifact_read(
                task["task_id"],job["proxy_job_id"],str(artifact),expected,0,120000
            ),
        )
        assert first["readback_state"]=="READ_OK"
        assert first["content"]==artifact_text
        assert first["eof"] is True
        assert first["proxy_job_id"]==job["proxy_job_id"]

        second=await drive(
            g,node,g.routing.task_job_artifact_read(
                task["task_id"],job["proxy_job_id"],str(artifact),expected,0,120000
            ),
        )
        assert second["content"]==artifact_text

        commands=g.durable.db.query_all(
            "SELECT * FROM device_commands WHERE task_id=? AND command_type='JOB_ARTIFACT_READ'",
            (task["task_id"],),
        )
        assert len(commands)==2
        assert len(g.routing.task_jobs_or_local(task["task_id"])["jobs"])==1

        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_job_artifact_read(
                    task["task_id"],job["proxy_job_id"],str(artifact),"0"*64,0,120000
                ),
            )
        assert exc.value.code=="POSTRUN_EVIDENCE_HASH_MISMATCH"

        await node.jobs.durable.stop()

    asyncio.run(run())


def test_legacy_terminal_job_without_manifest_fails_closed_without_rerun(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt-legacy","node-legacy")

        project=await drive(
            g,node,g.routing.project_register_on_device(
                "project-op",dev["device_id"],"repo",4
            ),
        )
        agent=await g.multi.agent_register("agent-op","agent","install",[])
        task=await drive(
            g,node,g.routing.task_create_or_local(
                "task-op",project["project_id"],"task"
            )
        )
        claim=await drive(
            g,node,g.routing.task_claim_or_local(
                "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
            ),
        )

        artifact=tmp_path/"legacy-evidence"/"result.json"
        code=(
            "from pathlib import Path;"
            f"p=Path({str(artifact)!r});"
            "p.parent.mkdir(parents=True,exist_ok=True);"
            "p.write_text('legacy',encoding='utf-8')"
        )
        job=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "legacy-science-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c",code],
                ".",
            ),
        )
        assert await _wait_terminal(node,job["node_job_id"])=="SUCCEEDED"

        status=await drive(
            g,node,g.routing.task_job_artifact_status(
                task["task_id"],job["proxy_job_id"],str(artifact)
            ),
        )
        assert status["state"]=="SUCCEEDED"
        assert status["readable"] is False
        assert status["readback_state"]=="POSTRUN_EVIDENCE_PATH_UNDECLARED"
        assert status["scientific_rerun_required"] is False
        assert status["automatic_scientific_rerun_for_readback_forbidden"] is True

        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_job_artifact_read(
                    task["task_id"],job["proxy_job_id"],str(artifact),
                    hashlib.sha256(artifact.read_bytes()).hexdigest(),
                ),
            )
        assert exc.value.code=="POSTRUN_EVIDENCE_PATH_UNDECLARED"
        assert len(g.routing.task_jobs_or_local(task["task_id"])["jobs"])==1

        await node.jobs.durable.stop()

    asyncio.run(run())


def test_declared_artifact_missing_is_readback_failure_not_science_failure(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt-missing","node-missing")

        project=await drive(
            g,node,g.routing.project_register_on_device(
                "project-op",dev["device_id"],"repo",4
            ),
        )
        agent=await g.multi.agent_register("agent-op","agent","install",[])
        task=await drive(
            g,node,g.routing.task_create_or_local(
                "task-op",project["project_id"],"task"
            )
        )
        claim=await drive(
            g,node,g.routing.task_claim_or_local(
                "claim-op",task["task_id"],agent["agent_id"],agent["session_id"]
            ),
        )

        artifact=tmp_path/"missing"/"never-created.json"
        job=await drive(
            g,node,g.routing.task_job_submit_or_local(
                "science-op",
                task["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                [sys.executable,"-c","print('done')"],
                ".",
                [str(artifact)],
            ),
        )
        assert await _wait_terminal(node,job["node_job_id"])=="SUCCEEDED"

        status=await drive(
            g,node,g.routing.task_job_artifact_status(
                task["task_id"],job["proxy_job_id"],str(artifact)
            ),
        )
        assert status["state"]=="SUCCEEDED"
        assert status["readable"] is False
        assert status["readback_state"]=="POSTRUN_EVIDENCE_NOT_FOUND"
        assert status["scientific_rerun_required"] is False
        assert status["automatic_scientific_rerun_for_readback_forbidden"] is True

        await node.jobs.durable.stop()

    asyncio.run(run())
