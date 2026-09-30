from __future__ import annotations

import inspect
import json
from pathlib import Path


def test_exact_v2b_tool_signatures(monkeypatch,tmp_path):
    monkeypatch.setenv("PUBLIC_URL","http://localhost:8765")
    monkeypatch.setenv("OWNER_PASSWORD","correct-horse-battery")
    monkeypatch.setenv("MCP_ROOT",str(tmp_path/"ws"))
    monkeypatch.setenv("MCP_RUNTIME_DIR",str(tmp_path/"rt"))
    import server
    root=Path(__file__).resolve().parents[2]
    lock=json.loads((root/"specs"/"v2b_prelock.json").read_text(encoding="utf-8"))
    for name,spec in lock["exact_tools"].items():
        expected=spec.split("def ",1)[-1].replace("async ","")
        actual=f"{name}{inspect.signature(getattr(server,name))}"
        assert actual==expected,(name,actual,expected)


def test_full_tool_surface_contains_v2a_and_v2b(monkeypatch,tmp_path):
    monkeypatch.setenv("PUBLIC_URL","http://localhost:8765")
    monkeypatch.setenv("OWNER_PASSWORD","correct-horse-battery")
    monkeypatch.setenv("MCP_ROOT",str(tmp_path/"ws"))
    monkeypatch.setenv("MCP_RUNTIME_DIR",str(tmp_path/"rt"))
    import server
    for name in (
        "list_dir","read_file","write_file","edit_file","search","run_command",
        "job_submit","job_get","job_wait","job_logs","job_result","job_cancel",
        "agent_register","agent_heartbeat","session_close","project_register","project_status",
        "task_create","task_claim","task_status","task_checkpoint","task_block","task_set_ready",
        "task_release","task_complete","path_lease_acquire","path_lease_release",
        "file_write_cas","file_edit_cas","task_job_submit","task_jobs","task_job_cancel",
    ):
        assert callable(getattr(server,name))