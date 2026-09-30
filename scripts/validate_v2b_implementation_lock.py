"""Static-only validator for the V2-B implementation execution lock."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"specs"/"v2b_implementation_lock.json"
PRELOCK=ROOT/"specs"/"v2b_prelock.json"
V1=ROOT/"specs"/"v2a_schema_v1.sql"
V2=ROOT/"specs"/"v2b_schema_v2.sql"

def git(*args:str)->str:
    return subprocess.check_output(["git","-C",str(ROOT),*args],text=True).strip()

def main():
    lock=json.loads(LOCK.read_text(encoding="utf-8"))
    pre=json.loads(PRELOCK.read_text(encoding="utf-8"))

    assert lock["lock_id"]=="REMOTE_MCP_V2B_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK"
    assert lock["status"]=="FROZEN"
    assert lock["binds"]["git_head"] in git("rev-list","HEAD").splitlines()
    assert git("hash-object","specs/v2b_prelock.json")==lock["binds"]["prelock_blob"]
    assert git("hash-object","specs/v2b_schema_v2.sql")==lock["binds"]["schema_v2_blob"]
    assert git("hash-object","docs/V2_B_MULTI_AGENT_TASK_LEASE_WORKTREE_PRELOCK.md")==lock["binds"]["prelock_doc_blob"]

    assert not (ROOT/"remotemcp"/"multiagent").exists()

    files=lock["module_layout"]["files"]
    assert len(files)==len(set(files))
    assert "remotemcp/multiagent/migrations/002_v2b.sql" in files
    assert lock["migration_002"]["byte_identity_required"] is True
    assert lock["migration_002"]["ordered_versions"]==[1,2]

    con=sqlite3.connect(":memory:")
    con.executescript(V1.read_text(encoding="utf-8"))
    con.executescript(V2.read_text(encoding="utf-8"))
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "owner_accounts" in tables
    assert "cas_mutations" in tables
    cols={r[1] for r in con.execute("PRAGMA table_info(task_leases)")}
    agent_cols={r[1] for r in con.execute("PRAGMA table_info(agents)")}
    session_cols={r[1] for r in con.execute("PRAGMA table_info(agent_sessions)")}
    assert "lease_token_nonce" in cols
    assert "owner_account_id" in agent_cols
    assert "auth_client_id" in session_cols

    token=lock["principal_and_token"]
    assert token["owner_account_id"].startswith("Stable prefix own_")
    assert "get_access_token().client_id" in token["oauth_client_role"]
    assert "same owner_account_id" in token["multi_login_device_rule"]
    assert token["clear_token_storage"] is False
    assert token["lease_key_bytes"]==32
    assert token["token_derivation"].startswith("lt1_")

    claim=lock["transaction_boundaries"]
    assert "BEGIN IMMEDIATE" in claim["task_claim_phase1"]
    assert "provision/recover" in claim["task_claim_external"][0]
    assert "derive clear token" in claim["task_claim_phase2"][-1]

    assert lock["cas_recovery"]["PREPARED"]
    assert "IN_DOUBT" in " ".join(lock["cas_recovery"]["PREPARED"])
    assert "TASK_CONTEXT_REQUIRED" in lock["legacy_guard"]["run_command"]
    assert "TASK_CONTEXT_REQUIRED" in lock["legacy_guard"]["direct_job_submit"]
    assert "TREE WRITE_EXCLUSIVE" in lock["task_job_wrappers"]["submit"][3]

    wt=lock["worktree_algorithm"]
    assert "reset --hard" in wt["forbidden_git_actions"]
    assert "git worktree list --porcelain" in wt["create_or_recover"]["expected_worktree_exists"]

    assert lock["production_freeze"]["port_8099_must_remain_untouched"] is True
    assert lock["production_freeze"]["deployment_allowed_in_this_gate"] is False
    md=lock["multi_login_devices"]
    assert md["in_scope_now"] is True
    assert "login machines" in md["minimum_qualification"].lower()
    route=lock["multi_execution_device_successor"]
    assert route["required_before_release"] is True
    assert route["gate"]=="REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK"

    tests=lock["exact_test_files"]
    assert len(tests)==12
    q=lock["qualification_scripts"]
    assert len(q)==5
    matrix="\n".join(lock["failure_injection_matrix"])
    for phrase in (
        "claim response lost",
        "CAS crash after replace",
        "two CAS writers",
        "session stale while V2-A durable payload continues",
        "server restart with expired leases",
        "two login devices with different OAuth client IDs"
    ):
        assert phrase in matrix

    # Bound prelock invariants remain present.
    assert pre["heartbeat_and_lease"]["task_lease_ttl_seconds"]==120
    assert pre["project_concurrency"]["default_max_active_tasks"]==4
    assert pre["cas_mutation"]["wildcard_expected_hash"] is False

    # Runtime and production are untouched at this gate.
    assert git("hash-object","server.py")==pre["binds"]["server_blob"]

    assert lock["next_gate_on_pass"]=="REMOTE_MCP_V2B_MULTI_AGENT_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION"

    print("PASS exact V2-B module layout")
    print("PASS migration 002/schema bindings")
    print("PASS owner-account/auth-client identity split")
    print("PASS two-login-device concurrent-session contract")
    print("PASS mandatory V2-BD multi-execution-device successor")
    print("PASS principal/HMAC lease-token contract")
    print("PASS claim/worktree transaction boundaries")
    print("PASS CAS intent/recovery boundaries")
    print("PASS legacy managed-mode bypass guards")
    print("PASS task-bound V2-A job wrapper contract")
    print("PASS worktree recovery/no-destructive-git contract")
    print("PASS full failure-injection/qualification matrix")
    print("PASS production 8099 deployment freeze")
    print("PASS no V2-B runtime implementation before authorization")

if __name__=="__main__":
    main()