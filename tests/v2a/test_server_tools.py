from __future__ import annotations

import inspect

import server


def test_exact_job_tool_signatures():
    expected={
        "job_submit":"(operation_id: str, argv: list[str], cwd: str = '.', agent_id: str = '', project_id: str = '', task_id: str = '') -> dict",
        "job_get":"(job_id: str) -> dict",
        "job_wait":"(job_id: str, subscriber_id: str, mode: str = 'terminal', after_event_id: int = 0, timeout_seconds: int = 55) -> dict",
        "job_logs":"(job_id: str, stream: str = 'stdout', cursor: int = 0, limit_bytes: int = 65536) -> dict",
        "job_result":"(job_id: str, subscriber_id: str = '', ack_event_id: int = 0, operation_id: str = '') -> dict",
        "job_cancel":"(operation_id: str, job_id: str, agent_id: str = '', project_id: str = '', task_id: str = '') -> dict",
    }
    for name,sig in expected.items():
        assert str(inspect.signature(getattr(server,name)))==sig


def test_legacy_tool_surface_still_present():
    for name in ("list_dir","read_file","write_file","edit_file","search","run_command"):
        assert callable(getattr(server,name))
