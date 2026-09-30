"""Validate the implemented V2-A runtime against the frozen execution lock."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",r"C:\Users\minht")
os.environ.setdefault("PUBLIC_URL","http://localhost:8765")
os.environ.setdefault("OWNER_PASSWORD","correct-horse-battery")
os.environ.setdefault("MCP_ROOT",str(ROOT/".v2test-root"))
os.environ.setdefault("MCP_STATE",str(ROOT/".v2test-state.json"))
os.environ.setdefault("MCP_RUNTIME_DIR",str(ROOT/".v2test-runtime-impl"))

LOCK=json.loads((ROOT/"specs"/"v2a_implementation_lock.json").read_text(encoding="utf-8"))

def main():
    expected=LOCK["module_layout"]["files"]
    missing=[p for p in expected if not (ROOT/p).exists()]
    assert not missing,missing

    schema=(ROOT/"specs"/"v2a_schema_v1.sql").read_bytes()
    migration=(ROOT/"remotemcp"/"durable"/"migrations"/"001_v2a.sql").read_bytes()
    assert schema==migration
    assert hashlib.sha256(schema).hexdigest()==hashlib.sha256(migration).hexdigest()

    durable_root=ROOT/"remotemcp"/"durable"
    for path in durable_root.rglob("*.py"):
        text=path.read_text(encoding="utf-8")
        assert "shell=True" not in text,path
        assert "os.system(" not in text,path
        if path.name not in {"__init__.py"}:
            assert "import server" not in text,path
            assert "import oauth_provider" not in text,path
            assert "from server" not in text,path
            assert "from oauth_provider" not in text,path
            assert "@mcp.tool" not in text,path

    import server
    tools=LOCK["tool_signatures"]
    for name in ("job_submit","job_get","job_wait","job_logs","job_result","job_cancel"):
        expected_sig=tools[name]["python"].split("def ",1)[-1]
        actual=f"{name}{inspect.signature(getattr(server,name))}"
        expected_norm=expected_sig.replace("async ","")
        assert actual==expected_norm,(name,actual,expected_norm)

    from remotemcp.durable.config import DEFAULT_DURABLE_ALLOWED_CMDS
    for required in (
        "ollama","llama","llama-cli","llama-server","llama-bench",
        "cmake","ctest","ninja","uv","ffmpeg",
    ):
        assert required in DEFAULT_DURABLE_ALLOWED_CMDS,required
    for shell in ("powershell","cmd","bash"):
        assert shell not in DEFAULT_DURABLE_ALLOWED_CMDS,shell

    print("PASS exact durable module layout")
    print("PASS migration byte identity")
    print("PASS forbidden dependency/shell scan")
    print("PASS exact job_* implementation signatures")
    print("PASS project-tool default allowlist")

if __name__=="__main__":
    main()
