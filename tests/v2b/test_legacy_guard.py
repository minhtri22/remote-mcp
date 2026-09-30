from __future__ import annotations

import asyncio
import pytest

from remotemcp.durable.errors import DurableError


def test_legacy_guards_activate_after_project_registration(make_bundle):
    async def run():
        b=make_bundle()
        await b.multi.project_register("p",".",4)
        with pytest.raises(DurableError) as exc:
            b.multi.guard.guard_run_command()
        assert exc.value.code=="TASK_CONTEXT_REQUIRED"
        with pytest.raises(DurableError) as exc:
            b.multi.guard.guard_job_submit()
        assert exc.value.code=="TASK_CONTEXT_REQUIRED"
        with pytest.raises(DurableError) as exc:
            b.multi.guard.guard_file_mutation(b.workspace/"x.txt")
        assert exc.value.code=="LEASE_REQUIRED_USE_CAS"
    asyncio.run(run())


def test_unmanaged_mode_preserves_legacy_paths(make_bundle):
    b=make_bundle()
    b.multi.guard.guard_run_command()
    b.multi.guard.guard_job_submit()
    b.multi.guard.guard_file_mutation(b.workspace/"x.txt")