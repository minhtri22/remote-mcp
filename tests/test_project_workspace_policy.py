from __future__ import annotations

import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.workspace_layout import (
    classify_task_worktree_rel,
    enforce_agent_git_worktree_policy,
    legacy_task_worktree_rel,
    project_workspace_rel,
    project_worktrees_rel,
    task_worktree_rel,
)


def test_project_workspace_paths_are_deterministic_and_nested():
    p="prj_abc"
    t="tsk_xyz"
    assert project_workspace_rel(p)==".remotemcp/workspaces/prj_abc"
    assert project_worktrees_rel(p)==".remotemcp/workspaces/prj_abc/worktrees"
    assert task_worktree_rel(p,t)==".remotemcp/workspaces/prj_abc/worktrees/tsk_xyz"
    assert classify_task_worktree_rel(p,t,task_worktree_rel(p,t))=="PROJECT_WORKSPACE_V1"


def test_legacy_managed_worktree_layout_remains_readable():
    p="prj_abc"
    t="tsk_xyz"
    legacy=legacy_task_worktree_rel(p,t)
    assert classify_task_worktree_rel(p,t,legacy)=="LEGACY_REMOTE_WORKTREES"


def test_arbitrary_worktree_path_is_rejected():
    with pytest.raises(DurableError) as exc:
        classify_task_worktree_rel("prj_abc","tsk_xyz","CLDP-SIX-COMP")
    assert exc.value.code=="WORKTREE_LAYOUT_VIOLATION"


@pytest.mark.parametrize(
    "argv",
    [
        ["git","worktree","add","../junk"],
        ["git.exe","-C","repo","worktree","move","a","b"],
        ["C:/Program Files/Git/cmd/git.exe","worktree","remove","x"],
        ["git","worktree","prune"],
        ["git","worktree","repair"],
        ["git","worktree","lock","x"],
        ["git","worktree","unlock","x"],
    ],
)
def test_direct_git_worktree_mutations_are_blocked(argv):
    with pytest.raises(DurableError) as exc:
        enforce_agent_git_worktree_policy(argv)
    assert exc.value.code=="WORKTREE_MUTATION_FORBIDDEN"


def test_git_worktree_list_is_observation_only_and_allowed():
    enforce_agent_git_worktree_policy(["git","worktree","list","--porcelain"])
    enforce_agent_git_worktree_policy(["git","-C","repo","worktree","list"])
