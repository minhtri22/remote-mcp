from __future__ import annotations

from pathlib import Path

import pytest

from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService


@pytest.fixture
def make_service(tmp_path):
    made = []

    def factory(*, max_parallel_jobs=1, poll_ms=50):
        idx = len(made)
        base = tmp_path / f"svc-{idx}"
        root = base / "workspace"
        runtime = base / "runtime"
        root.mkdir(parents=True)
        cfg = DurableConfig(
            workspace_root=root,
            runtime_dir=runtime,
            allowed_cmds=DEFAULT_DURABLE_ALLOWED_CMDS,
            max_parallel_jobs=max_parallel_jobs,
            poll_ms=poll_ms,
            starting_grace_seconds=2,
        )
        svc = DurableService(cfg)
        made.append((svc, root, runtime))
        return svc, root, runtime

    return factory
