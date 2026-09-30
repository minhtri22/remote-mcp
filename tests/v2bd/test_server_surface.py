from __future__ import annotations

import importlib,inspect,json,os,sys
from pathlib import Path


def test_exact_v2bd_server_surface(monkeypatch,tmp_path):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PUBLIC_URL","http://127.0.0.1:8765")
    monkeypatch.setenv("OWNER_PASSWORD","correct-horse-battery")
    monkeypatch.setenv("MCP_ROOT",str(tmp_path/"ws"))
    monkeypatch.setenv("MCP_RUNTIME_DIR",str(tmp_path/"rt"))
    monkeypatch.setenv("MCP_STATE",str(tmp_path/"oauth.json"))
    (tmp_path/"ws").mkdir()
    sys.modules.pop("server",None)
    import server
    pre=json.loads((root/"specs"/"v2bd_prelock.json").read_text(encoding="utf-8"))
    for name,spec in pre["public_tools"].items():
        expected=spec.split("def ",1)[-1].replace("async ","")
        actual=f"{name}{inspect.signature(getattr(server,name))}"
        assert actual==expected,(name,actual,expected)
    for name in (
        "project_register","task_create","task_claim","task_complete",
        "file_write_cas","file_edit_cas","task_job_submit","task_jobs","task_job_cancel"
    ):
        assert callable(getattr(server,name))
    assert server.routing_service.db.query_one("select version from schema_migrations where version=3")
