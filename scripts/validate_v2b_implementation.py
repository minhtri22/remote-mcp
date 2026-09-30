"""Post-implementation validator for RemoteMCP V2-B.

Validates source/runtime contracts only. It never deploys or restarts production.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",r"C:\Users\minht")

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

LOCK=json.loads((ROOT/"specs"/"v2b_implementation_lock.json").read_text(encoding="utf-8"))
PRE=json.loads((ROOT/"specs"/"v2b_prelock.json").read_text(encoding="utf-8"))


def validate_source():
    missing=[p for p in LOCK["module_layout"]["files"] if not (ROOT/p).exists()]
    assert not missing,missing

    assert (ROOT/"specs"/"v2b_schema_v2.sql").read_bytes()==(
        ROOT/"remotemcp"/"multiagent"/"migrations"/"002_v2b.sql"
    ).read_bytes()

    multi=ROOT/"remotemcp"/"multiagent"
    for path in multi.rglob("*.py"):
        src=path.read_text(encoding="utf-8")
        assert "@mcp.tool" not in src,path
        assert "shell=True" not in src,path
        assert "os.system(" not in src,path
    wt=(multi/"worktrees.py").read_text(encoding="utf-8")
    for forbidden in ("reset --hard","branch -D"," worktree prune "," rebase "," merge "):
        assert forbidden not in wt,forbidden

    server=(ROOT/"server.py").read_text(encoding="utf-8")
    for needle in (
        "multiagent_service.guard.guard_file_mutation",
        "multiagent_service.guard.guard_run_command",
        "multiagent_service.guard.guard_job_submit",
        "multiagent_service.guard.guard_job_cancel",
    ):
        assert needle in server,needle

    print("PASS exact V2-B module layout")
    print("PASS migration 002 byte identity")
    print("PASS no FastMCP decorators/shell execution inside multiagent package")
    print("PASS no destructive Git recovery commands")
    print("PASS legacy managed-mode guards wired in server.py")


def validate_bootstrap_and_tools():
    from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
    from remotemcp.durable.db import Database
    from remotemcp.durable.service import DurableService
    from remotemcp.multiagent.config import MultiAgentConfig
    from remotemcp.multiagent.service import MultiAgentService

    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-validator-") as td:
        base=Path(td); ws=base/"ws";rt=base/"rt";ws.mkdir()
        db=Database(rt)
        db.bootstrap()
        assert [r["version"] for r in db.query_all("SELECT version FROM schema_migrations ORDER BY version")]==[1]

        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS))
        m=MultiAgentService(MultiAgentConfig(ws,rt),d,auth_client_resolver=lambda:"validator-client")
        assert [r["version"] for r in d.db.query_all("SELECT version FROM schema_migrations ORDER BY version")]==[1,2]
        assert m.identity.owner_account_id.startswith("own_")
        assert (rt/"owner-account.json").exists()
        assert (rt/"lease-token.key").stat().st_size==32

    # Import server under isolated paths and validate exact tool signatures.
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-server-") as td:
        base=Path(td)
        os.environ["PUBLIC_URL"]="http://localhost:8765"
        os.environ["OWNER_PASSWORD"]="correct-horse-battery"
        os.environ["MCP_ROOT"]=str(base/"ws")
        os.environ["MCP_RUNTIME_DIR"]=str(base/"rt")
        import server
        for name,sig in PRE["exact_tools"].items():
            expected=sig.split("def ",1)[-1].replace("async ","")
            actual=f"{name}{inspect.signature(getattr(server,name))}"
            assert actual==expected,(name,actual,expected)

    print("PASS V2-A bootstrap remains schema v1 by default")
    print("PASS MultiAgentService explicitly upgrades ordered schema 001 -> 002")
    print("PASS stable owner-account + 32-byte lease HMAC key bootstrap")
    print("PASS exact 20 V2-B MCP tool signatures")


def validate_production(expected_pid:int|None):
    if expected_pid is None:
        print("SKIP production PID assertion (no expected PID supplied)")
        return
    import psutil
    listeners=[
        c for c in psutil.net_connections(kind="tcp")
        if c.status=="LISTEN" and getattr(c.laddr,"port",None)==8099
    ]
    assert len(listeners)==1,listeners
    assert listeners[0].pid==expected_pid,(listeners[0].pid,expected_pid)
    proc=psutil.Process(expected_pid)
    assert proc.is_running()
    print(f"PASS production 8099 unchanged at PID {expected_pid}")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--expected-production-pid",type=int)
    args=ap.parse_args()
    validate_source()
    validate_bootstrap_and_tools()
    validate_production(args.expected_production_pid)
    print("PASS V2-B post-implementation static validation")


if __name__=="__main__":
    main()