from __future__ import annotations

import asyncio
import subprocess

import pytest

from conftest import drive, init_git_repo, pair_node
from remotemcp.durable.errors import DurableError


def _head(path):
    return subprocess.run(
        ["git","-C",str(path),"rev-parse","HEAD"],
        check=True,capture_output=True,text=True,
    ).stdout.strip().lower()


def test_bootstrap_non_git_project_materializes_exact_git_without_replacement(
    make_gateway,tmp_path
):
    async def run():
        g=make_gateway()
        source=init_git_repo(tmp_path/"source","SOURCE")
        expected=_head(source)

        root=tmp_path/"node-root"
        root.mkdir()
        project=root/"bootstrap-project"
        project.mkdir()
        (project/".remotemcp-bootstrap").write_text("bootstrap\n",encoding="utf-8")
        node,dev=await pair_node(g,root,tmp_path/"node-rt","materialize-node")

        p0=await drive(
            g,node,
            g.routing.project_register_on_device(
                "register-bootstrap",dev["device_id"],"bootstrap-project",4
            ),
        )
        assert p0["project_kind"]=="NON_GIT"
        project_id=p0["project_id"]
        generation=p0["binding_generation"]

        p1=await drive(
            g,node,
            g.routing.project_materialize_git_on_device(
                "materialize-bootstrap",
                project_id,
                str(source),
                "HEAD",
                expected,
            ),
            timeout=20,
        )
        assert p1["project_id"]==project_id
        assert p1["binding_generation"]==generation
        assert p1["project_kind"]=="GIT"
        assert p1["head_commit"]==expected
        assert p1["exact_commit_pinned"] is True
        assert not (project/".remotemcp-bootstrap").exists()
        assert _head(project)==expected

        central=g.multi.projects.get(project_id)
        assert central["project_kind"]=="GIT"
        node_row=node.projects.get(project_id)
        assert node_row["project_kind"]=="GIT"

        task=await drive(
            g,node,
            g.routing.task_create_or_local(
                "task-after-materialization",project_id,"materialized task","HEAD"
            ),
            timeout=20,
        )
        assert task["base_commit"]==expected

    asyncio.run(run())


def test_bootstrap_materialization_commit_mismatch_preserves_original_root(
    make_gateway,tmp_path
):
    async def run():
        g=make_gateway()
        source=init_git_repo(tmp_path/"source-mismatch","SOURCE")
        root=tmp_path/"node-root-mismatch"
        root.mkdir()
        project=root/"bootstrap-project"
        project.mkdir()
        marker=project/".remotemcp-bootstrap"
        marker.write_text("keep-me\n",encoding="utf-8")
        node,dev=await pair_node(g,root,tmp_path/"node-rt-mismatch","materialize-mismatch")

        p0=await drive(
            g,node,
            g.routing.project_register_on_device(
                "register-mismatch",dev["device_id"],"bootstrap-project",4
            ),
        )
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,
                g.routing.project_materialize_git_on_device(
                    "materialize-mismatch",
                    p0["project_id"],
                    str(source),
                    "HEAD",
                    "0"*40,
                ),
                timeout=20,
            )
        assert exc.value.code=="PROJECT_MATERIALIZATION_COMMIT_MISMATCH" or exc.value.code=="PROJECT_GIT_MATERIALIZATION_COMMIT_MISMATCH"
        assert marker.read_text(encoding="utf-8")=="keep-me\n"
        assert not (project/".git").exists()
        assert g.multi.projects.get(p0["project_id"])["project_kind"]=="NON_GIT"
        assert node.projects.get(p0["project_id"])["project_kind"]=="NON_GIT"

    asyncio.run(run())


def test_bootstrap_materialization_refuses_existing_task_semantics(
    make_gateway,tmp_path
):
    async def run():
        g=make_gateway()
        source=init_git_repo(tmp_path/"source-task","SOURCE")
        expected=_head(source)
        root=tmp_path/"node-root-task"
        root.mkdir()
        project=root/"bootstrap-project"
        project.mkdir()
        (project/".remotemcp-bootstrap").write_text("bootstrap\n",encoding="utf-8")
        node,dev=await pair_node(g,root,tmp_path/"node-rt-task","materialize-task")

        p0=await drive(
            g,node,
            g.routing.project_register_on_device(
                "register-task",dev["device_id"],"bootstrap-project",4
            ),
        )
        await drive(
            g,node,
            g.routing.task_create_or_local(
                "non-git-task",p0["project_id"],"existing non-git task"
            ),
        )
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,
                g.routing.project_materialize_git_on_device(
                    "materialize-after-task",
                    p0["project_id"],
                    str(source),
                    "HEAD",
                    expected,
                ),
            )
        assert exc.value.code=="PROJECT_GIT_MATERIALIZATION_HAS_TASKS"
        assert not (project/".git").exists()
        assert g.multi.projects.get(p0["project_id"])["project_kind"]=="NON_GIT"

    asyncio.run(run())
