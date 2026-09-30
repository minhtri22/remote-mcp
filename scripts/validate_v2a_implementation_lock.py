"""Static-only validator for the V2-A implementation execution lock.

This validator must not import RemoteMCP runtime modules and must not create
runtime DB/job artifacts.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "specs" / "v2a_implementation_lock.json"
PRELOCK = ROOT / "specs" / "v2a_prelock.json"
SCHEMA = ROOT / "specs" / "v2a_schema_v1.sql"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    prelock = json.loads(PRELOCK.read_text(encoding="utf-8"))

    assert lock["lock_id"] == "REMOTE_MCP_V2A_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK"
    assert lock["lock_version"] == 1
    assert lock["status"] == "FROZEN"

    # Bind to the exact prospective prelock.
    assert git("hash-object", "specs/v2a_prelock.json") == lock["binds"]["prelock_blob"]
    assert git("hash-object", "specs/v2a_schema_v1.sql") == lock["binds"]["schema_blob"]
    assert git("hash-object", "docs/V2_A_DURABLE_JOB_AND_OPERATION_PRELOCK.md") == lock["binds"]["prelock_doc_blob"]
    assert lock["binds"]["git_head"] in git("rev-list", "HEAD").splitlines()

    # No implementation package may exist yet.
    assert not (ROOT / "remotemcp").exists(), "runtime package exists before execution lock"

    files = lock["module_layout"]["files"]
    assert len(files) == len(set(files))
    assert "remotemcp/durable/migrations/001_v2a.sql" in files
    assert lock["bootstrap"]["schema_source"] == "specs/v2a_schema_v1.sql"
    assert lock["bootstrap"]["migration_rule"].startswith("001_v2a.sql MUST be byte-for-byte")

    # Worker must include a pre-payload durable barrier.
    phases = lock["worker_protocol"]["phases"]
    wait_i = next(i for i,p in enumerate(phases) if "MUST NOT spawn payload" in p)
    commit_i = next(i for i,p in enumerate(phases) if "launch.commit" in p and "writes" in p)
    payload_i = next(i for i,p in enumerate(phases) if "worker spawns payload" in p)
    assert wait_i < commit_i < payload_i

    # shell execution is forbidden everywhere in the lock.
    assert lock["command_contract"]["no_shell"] is True
    assert "shell=True" in lock["module_layout"]["forbidden_dependencies"]

    # Exact tool set/signatures.
    tools = lock["tool_signatures"]
    assert set(tools) == {
        "response_encoding", "job_submit", "job_get", "job_wait",
        "job_logs", "job_result", "job_cancel"
    }
    signatures = [
        tools[name]["python"]
        for name in ("job_submit","job_get","job_wait","job_logs","job_result","job_cancel")
    ]
    assert len(signatures) == len(set(signatures))
    assert all("-> dict" in sig for sig in signatures)
    assert "argv: list[str]" in tools["job_submit"]["python"]
    assert "operation_id: str" in tools["job_submit"]["python"]
    assert "operation_id: str" in tools["job_cancel"]["python"]

    assert tools["job_wait"]["timeout_bounds_seconds"] == [0, 55]
    assert tools["job_logs"]["limit_bounds_bytes"] == [1, 262144]
    assert "does not ACK" in tools["job_wait"]["semantics"]
    assert "ACK" in tools["job_result"]["mutation"]
    assert "operation_id: str" in tools["job_result"]["python"]
    assert "operation_id is mandatory" in tools["job_result"]["mutation"]
    assert "republish launch.commit" in lock["worker_protocol"]["recovery_rule"]
    assert "MUST NOT spawn another worker" in lock["worker_protocol"]["recovery_rule"]

    # Prelock invariants must remain represented in tests.
    required_cases = set(lock["implementation_test_matrix"]["required_cases"])
    required_fragments = {
        "same operation_id + different hash conflicts",
        "IN_DOUBT never executes",
        "worker cannot spawn payload before launch.commit",
        "PID reuse simulation fails ownership verification",
        "LOST never auto-relaunches",
        "terminal event unique constraint under concurrent insert attempt",
        "unacked terminal event redelivers",
        "acked terminal event does not redeliver for same subscriber",
        "legacy run_command output/allowlist/timeout compatibility",
        "V2-0 17-test regression remains PASS",
        "OAuth integration remains PASS",
        "public OAuth smoke remains PASS",
    }
    assert required_fragments <= required_cases

    # Prelock state names are not silently redesigned.
    assert prelock["operation"]["states"] == [
        "RESERVED","EXECUTING","SUCCEEDED","FAILED_RETRYABLE","FAILED_FINAL","IN_DOUBT"
    ]
    assert prelock["job"]["states"] == [
        "QUEUED","STARTING","RUNNING","CANCELLING","SUCCEEDED","FAILED","CANCELLED","LOST"
    ]

    # Scope barriers.
    forbidden = "\n".join(lock["execution_boundaries"]["forbidden"])
    for phrase in ("OAuth", "V2-B", "V2-C", "MCP Tasks", "auto-relaunch LOST"):
        assert phrase in forbidden

    # Existing runtime sources must remain untouched relative to bound HEAD.
    for path in ("server.py", "oauth_provider.py"):
        current = git("hash-object", path)
        bound_hash = git("rev-parse", f"{lock['binds']['git_head']}:{path}")
        assert current == bound_hash, (path, current, bound_hash)

    assert lock["implementation_acceptance_before_v2a_close"]["required"][-1].startswith("README updated")
    assert lock["next_gate_on_pass"] == "REMOTE_MCP_V2A_DURABILITY_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION"

    print("PASS implementation-lock structure")
    print("PASS exact prelock/schema bindings")
    print("PASS no runtime package before authorization")
    print("PASS module/responsibility layout")
    print("PASS worker durable launch barrier")
    print("PASS exact job_* signatures and bounds")
    print("PASS implementation test-matrix coverage")
    print("PASS scope/execution boundaries")
    print("PASS runtime source unchanged")
    print("PASS next-gate binding")


if __name__ == "__main__":
    main()
