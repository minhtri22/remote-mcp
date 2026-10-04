from __future__ import annotations

from pathlib import PurePosixPath

from remotemcp.durable.errors import DurableError

PROJECT_WORKSPACE_PREFIX = ".remotemcp/workspaces"
LEGACY_WORKTREE_PREFIX = ".remote-worktrees"


def _segment(value: str, label: str) -> str:
    value = str(value)
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise DurableError(
            "WORKSPACE_IDENTITY_INVALID",
            f"{label} is not a single safe path segment",
            **{label: value},
        )
    return value


def project_workspace_rel(project_id: str) -> str:
    project_id = _segment(project_id, "project_id")
    return f"{PROJECT_WORKSPACE_PREFIX}/{project_id}"


def project_worktrees_rel(project_id: str) -> str:
    return f"{project_workspace_rel(project_id)}/worktrees"


def task_worktree_rel(project_id: str, task_id: str) -> str:
    task_id = _segment(task_id, "task_id")
    return f"{project_worktrees_rel(project_id)}/{task_id}"


def legacy_task_worktree_rel(project_id: str, task_id: str) -> str:
    project_id = _segment(project_id, "project_id")
    task_id = _segment(task_id, "task_id")
    return f"{LEGACY_WORKTREE_PREFIX}/{project_id}/{task_id}"


def classify_task_worktree_rel(project_id: str, task_id: str, worktree_rel: str) -> str:
    rel = str(PurePosixPath(str(worktree_rel).replace("\\", "/")))
    if rel == task_worktree_rel(project_id, task_id):
        return "PROJECT_WORKSPACE_V1"
    if rel == legacy_task_worktree_rel(project_id, task_id):
        return "LEGACY_REMOTE_WORKTREES"
    raise DurableError(
        "WORKTREE_LAYOUT_VIOLATION",
        "task worktree is outside its deterministic project workspace",
        project_id=project_id,
        task_id=task_id,
        worktree_rel=worktree_rel,
        expected_worktree_rel=task_worktree_rel(project_id, task_id),
    )


def enforce_agent_git_worktree_policy(argv: list[str]) -> None:
    """Allow observation but forbid direct worktree lifecycle mutations.

    Worktree lifecycle belongs to RemoteMCP's task APIs.  This guard is not a
    filesystem sandbox; it prevents the normal managed-job path from bypassing
    the project-workspace invariant with a direct git worktree mutation.
    """
    if not argv:
        return
    exe = PurePosixPath(str(argv[0]).replace("\\", "/")).name.lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe != "git":
        return

    tokens = [str(x) for x in argv[1:]]
    try:
        idx = next(i for i, token in enumerate(tokens) if token.lower() == "worktree")
    except StopIteration:
        return

    tail = tokens[idx + 1 :]
    subcommand = None
    for token in tail:
        if token == "--":
            continue
        if token.startswith("-"):
            continue
        subcommand = token.lower()
        break

    if subcommand == "list":
        return
    raise DurableError(
        "WORKTREE_MUTATION_FORBIDDEN",
        "direct git worktree mutation is forbidden; use RemoteMCP task/workspace lifecycle APIs",
        requested_subcommand=subcommand or "",
    )
