"""Static validator for V2-B multi-agent prelock.

Must not import or mutate V2-B runtime state, create worktrees, or touch production.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs" / "v2b_prelock.json"
V1 = ROOT / "specs" / "v2a_schema_v1.sql"
V2 = ROOT / "specs" / "v2b_schema_v2.sql"


def git(*args: str) -> str:
    return subprocess.check_output(["git","-C",str(ROOT),*args],text=True).strip()


def transitions_closed(states, transitions):
    assert set(states) == set(transitions)
    known=set(states)
    for src,dsts in transitions.items():
        assert all(d in known for d in dsts),(src,dsts)


def main():
    s=json.loads(SPEC.read_text(encoding="utf-8"))
    assert s["spec_id"]=="REMOTE_MCP_V2B_MULTI_AGENT_TASK_LEASE_WORKTREE_PRELOCK"
    assert s["status"]=="FROZEN"
    assert s["binds"]["git_head"] in git("rev-list","HEAD").splitlines()
    assert git("hash-object","specs/v2a_qualification_manifest.json")==s["binds"]["v2a_qualification_manifest_blob"]

    transitions_closed(s["task"]["states"],s["task"]["transitions"])
    for state in s["task"]["terminal_states"]:
        assert s["task"]["transitions"][state]==[]

    hb=s["heartbeat_and_lease"]
    assert hb["heartbeat_interval_seconds"]==30
    assert hb["task_lease_ttl_seconds"]==120
    assert hb["heartbeat_interval_seconds"] < hb["task_lease_ttl_seconds"]
    assert "LEASE_STALE" in s["errors"]
    assert s["heartbeat_and_lease"]["takeover"]["force_takeover_by_normal_agent"] is False

    # Apply prospective V2-B schema after frozen V2-A schema.
    con=sqlite3.connect(":memory:")
    # Skip PRAGMAs and migration ledger insert details by using executescript;
    # the schema itself is what this prelock validates.
    con.executescript(V1.read_text(encoding="utf-8"))
    con.executescript(V2.read_text(encoding="utf-8"))
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ("agents","agent_sessions","projects","tasks","task_leases","path_leases","task_events","task_checkpoints"):
        assert name in tables,name

    tools=s["exact_tools"]
    assert len(tools)==len(set(tools.values()))
    for name,sig in tools.items():
        if name=="agent_heartbeat":
            assert "heartbeat_seq: int" in sig
            assert "operation_id" not in sig
        elif name in {
            "project_status","task_status","task_jobs"
        }:
            pass
        else:
            assert "operation_id: str" in sig,(name,sig)

    assert s["cas_mutation"]["wildcard_expected_hash"] is False
    assert "MISSING" in s["cas_mutation"]["expected_hash_values"]
    assert s["v2a_job_interaction"]["on_task_lease_expiry"].startswith("Task becomes RECOVERABLE")
    assert s["v2a_job_interaction"]["on_agent_heartbeat_loss"]=="Do not cancel/kill job."

    assert s["deployment_policy"]["production_mutation_forbidden_during_v2b_prelock"] is True
    assert "explicitly authorizes deployment" in s["deployment_policy"]["switch_rule"]

    obs=s["observability_successor"]
    assert obs["recommendation"]=="YES"
    assert obs["successor_gate"]=="REMOTE_MCP_OBSERVABILITY_AGENT_GROUPED_VIEW_PRELOCK"
    assert "read-only" in obs["design"]["security"][1].lower()
    assert "implement observability UI/endpoint" in s["prelock_forbidden"]

    # No V2-B runtime mutation is allowed in this gate.
    assert git("hash-object","server.py")==s["binds"]["server_blob"]
    assert not (ROOT/"remotemcp"/"multiagent").exists()

    print("PASS V2-B prelock identity/task state machines")
    print("PASS heartbeat/lease/takeover invariants")
    print("PASS prospective schema v2 parses after V2-A schema")
    print("PASS exact V2-B tool mutation/idempotency rules")
    print("PASS CAS/workspace isolation contract")
    print("PASS durable-job independence from lease lifetime")
    print("PASS production deployment freeze")
    print("PASS observability successor recorded but not implemented")
    print("PASS no V2-B runtime implementation in prelock")


if __name__=="__main__":
    main()
