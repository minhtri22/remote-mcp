from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.errors import DurableError
from remotemcp.durable.service import DurableService
from remotemcp.node.db import NodeDatabase
from remotemcp.node.jobs import NodeJobs
from remotemcp.node.projects import NodeProjects
from remotemcp.node.worktrees import NodeWorktrees


def _seed_project(db, project_id: str, task_id: str, root_rel: str, *, kind: str = "NON_GIT"):
    with db.transaction() as con:
        con.execute(
            "INSERT INTO node_projects(project_id,binding_generation,root_rel,project_kind,created_at_ms,updated_at_ms) "
            "VALUES(?,1,?,?,1,1)",
            (project_id, root_rel, kind),
        )
        con.execute(
            "INSERT INTO node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) "
            "VALUES(?,?,1,NULL,NULL,'ACTIVE',1,1)",
            (task_id, project_id),
        )


def test_pre_dual_root_project_stays_on_historical_legacy_root(tmp_path):
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    runtime = tmp_path / "runtime"
    (canonical / "SIX").mkdir(parents=True)
    (legacy / "SIX").mkdir(parents=True)

    db = NodeDatabase(runtime)
    db.bootstrap()
    _seed_project(db, "prj_old", "tsk_old", "SIX")

    projects = NodeProjects(
        db,
        canonical,
        legacy_roots=(legacy,),
        historical_root=legacy,
    )
    worktrees = NodeWorktrees(db, canonical, projects)

    assert projects.path("prj_old") == (legacy / "SIX").resolve()
    info = projects.root_info("prj_old")
    assert info["root_namespace"] == "LEGACY1"
    assert info["legacy_compatibility"] is True
    assert worktrees.execution_root("tsk_old") == (legacy / "SIX").resolve()


def test_new_registration_uses_canonical_namespace_without_rebinding_legacy(tmp_path):
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    runtime = tmp_path / "runtime"
    (canonical / "SIX").mkdir(parents=True)
    (legacy / "SIX").mkdir(parents=True)

    db = NodeDatabase(runtime)
    db.bootstrap()
    _seed_project(db, "prj_old", "tsk_old", "SIX")

    projects = NodeProjects(
        db,
        canonical,
        legacy_roots=(legacy,),
        historical_root=legacy,
    )
    probe = projects.probe("SIX")
    assert probe["root_namespace"] == "CANONICAL"
    assert probe["root_rel"] == "SIX"

    bound = projects.bind(
        "prj_new",
        1,
        probe["root_rel"],
        probe["project_kind"],
    )
    assert bound["root_namespace"] == "CANONICAL"
    assert bound["root_rel"] == "SIX"
    assert projects.path("prj_new") == (canonical / "SIX").resolve()
    assert projects.path("prj_old") == (legacy / "SIX").resolve()


def test_legacy_namespace_must_be_explicitly_configured(tmp_path):
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    runtime = tmp_path / "runtime"
    canonical.mkdir()
    (legacy / "SIX").mkdir(parents=True)

    db = NodeDatabase(runtime)
    db.bootstrap()
    _seed_project(db, "prj_old", "tsk_old", "SIX")

    projects = NodeProjects(db, canonical, legacy_roots=(), historical_root=legacy)
    # Historical plain rows cannot silently escape to an unapproved root.
    with pytest.raises(DurableError) as exc:
        projects.path("prj_old")
    assert exc.value.code in {"NOT_FOUND", "PROJECT_ROOT_NAMESPACE_UNAVAILABLE"}


def test_durable_workspace_override_is_fail_closed(tmp_path):
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    outside = tmp_path / "outside"
    runtime = tmp_path / "durable"
    for p in (canonical, legacy, outside):
        p.mkdir()

    svc = DurableService(
        DurableConfig(
            workspace_root=canonical,
            runtime_dir=runtime,
            allowed_cmds=DEFAULT_DURABLE_ALLOWED_CMDS,
            approved_workspace_roots=(canonical, legacy),
            poll_ms=20,
        )
    )
    with pytest.raises(DurableError) as exc:
        svc._workspace_root(outside)
    assert exc.value.code == "PATH_ESCAPE"
    assert svc._workspace_root(legacy) == legacy.resolve()


def test_node_jobs_execute_old_task_on_legacy_and_new_task_on_canonical(tmp_path):
    async def run():
        canonical = tmp_path / "canonical"
        legacy = tmp_path / "legacy"
        runtime = tmp_path / "runtime"
        (canonical / "SIX").mkdir(parents=True)
        (legacy / "SIX").mkdir(parents=True)

        db = NodeDatabase(runtime)
        db.bootstrap()
        _seed_project(db, "prj_old", "tsk_old", "SIX")

        projects = NodeProjects(
            db,
            canonical,
            legacy_roots=(legacy,),
            historical_root=legacy,
        )
        probe = projects.probe("SIX")
        projects.bind("prj_new", 1, probe["root_rel"], "NON_GIT")
        with db.transaction() as con:
            con.execute(
                "INSERT INTO node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) "
                "VALUES('tsk_new','prj_new',1,NULL,NULL,'ACTIVE',1,1)"
            )

        worktrees = NodeWorktrees(db, canonical, projects)
        jobs = NodeJobs(db, canonical, runtime, "dev_test", worktrees)
        await jobs.start()
        try:
            cases = [
                ("tsk_old", "prj_old", "rjob_old", "cmd_old", legacy / "SIX"),
                ("tsk_new", "prj_new", "rjob_new", "cmd_new", canonical / "SIX"),
            ]
            for task_id, project_id, proxy_id, command_id, expected in cases:
                submitted = await jobs.submit(
                    proxy_id,
                    command_id,
                    {
                        "proxy_job_id": proxy_id,
                        "task_id": task_id,
                        "project_id": project_id,
                        "argv": [
                            sys.executable,
                            "-c",
                            "import os; print(os.getcwd())",
                        ],
                        "cwd": ".",
                    },
                )
                node_job_id = submitted["node_job_id"]
                state = None
                for _ in range(400):
                    state = jobs.durable.job_get(node_job_id)["state"]
                    if state in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}:
                        break
                    await asyncio.sleep(0.02)
                assert state == "SUCCEEDED"
                logs = jobs.durable.job_logs(node_job_id, stream="stdout")
                observed = Path(logs["data"].strip()).resolve()
                assert os.path.normcase(str(observed)) == os.path.normcase(str(expected.resolve()))
        finally:
            await jobs.stop()

    asyncio.run(run())
