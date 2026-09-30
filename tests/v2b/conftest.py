from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService


class ServiceBundle:
    def __init__(self, durable, multi, workspace, runtime, auth):
        self.durable=durable
        self.multi=multi
        self.workspace=workspace
        self.runtime=runtime
        self.auth=auth


@pytest.fixture
def make_bundle(tmp_path):
    made=[]

    def factory(*, max_jobs=2, poll_ms=30):
        idx=len(made)
        workspace=tmp_path/f"ws-{idx}"
        runtime=tmp_path/f"rt-{idx}"
        workspace.mkdir(parents=True)
        auth={"client":"oauth-client-a"}
        durable=DurableService(
            DurableConfig(
                workspace_root=workspace,
                runtime_dir=runtime,
                allowed_cmds=DEFAULT_DURABLE_ALLOWED_CMDS,
                max_parallel_jobs=max_jobs,
                poll_ms=poll_ms,
                starting_grace_seconds=2,
            )
        )
        multi=MultiAgentService(
            MultiAgentConfig(
                workspace_root=workspace,
                runtime_dir=runtime,
                task_lease_ttl_seconds=2,
                poll_ms=poll_ms,
            ),
            durable,
            auth_client_resolver=lambda: auth["client"],
        )
        b=ServiceBundle(durable,multi,workspace,runtime,auth)
        made.append(b)
        return b

    return factory


def init_git_repo(path:Path):
    path.mkdir(parents=True,exist_ok=True)
    cmds=[
        ["git","init",str(path)],
        ["git","-C",str(path),"config","user.email","test@example.com"],
        ["git","-C",str(path),"config","user.name","RemoteMCP Test"],
    ]
    for cmd in cmds:
        subprocess.run(cmd,check=True,capture_output=True,text=True)
    (path/"README.md").write_text("base\n",encoding="utf-8")
    subprocess.run(["git","-C",str(path),"add","README.md"],check=True)
    subprocess.run(["git","-C",str(path),"commit","-m","base"],check=True,capture_output=True,text=True)
    return path


async def terminal_state(durable,job_id,timeout=10):
    loops=int(timeout/0.05)
    for _ in range(loops):
        row=durable.job_get(job_id)
        if row["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("job did not reach terminal state")