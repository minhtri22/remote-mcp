"""Static validator for the V2-A prelock. It must not import RemoteMCP runtime."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "specs" / "v2a_prelock.json"
DDL_PATH = ROOT / "specs" / "v2a_schema_v1.sql"


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True
    ).strip()


def validate_transitions(states: list[str], transitions: dict[str, list[str]]) -> None:
    assert set(states) == set(transitions), (states, transitions.keys())
    known = set(states)
    for source, targets in transitions.items():
        assert all(t in known for t in targets), (source, targets)


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    assert spec["spec_id"] == "REMOTE_MCP_V2A_DURABLE_JOB_AND_OPERATION_PRELOCK"
    assert spec["spec_version"] == 1
    assert spec["status"] == "FROZEN"

    validate_transitions(
        spec["operation"]["states"], spec["operation"]["transitions"]
    )
    validate_transitions(spec["job"]["states"], spec["job"]["transitions"])

    terminal = set(spec["job"]["terminal_states"])
    for state in terminal:
        assert spec["job"]["transitions"][state] == [], state

    # Parse and exercise the frozen DDL in-memory.
    con = sqlite3.connect(":memory:")
    con.executescript(DDL_PATH.read_text(encoding="utf-8"))

    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for required in spec["storage"]["tables"]:
        assert required in tables, (required, tables)

    # Prove the unique terminal-event constraint.
    con.execute(
        "INSERT INTO operations(operation_id,kind,request_hash,state,created_at_ms,updated_at_ms) "
        "VALUES('op1','job_submit','h','SUCCEEDED',1,1)"
    )
    con.execute(
        "INSERT INTO jobs(job_id,operation_id,state,command_json,cwd_rel,env_profile,"
        "launch_nonce,stdout_path,stderr_path,result_path,created_at_ms,updated_at_ms) "
        "VALUES('j1','op1','SUCCEEDED','{}','.','safe','nonce1',"
        "'jobs/j1/stdout.log','jobs/j1/stderr.log','jobs/j1/result.json',1,1)"
    )
    con.execute(
        "INSERT INTO events(job_id,operation_id,event_type,terminal,payload_json,created_at_ms) "
        "VALUES('j1','op1','JOB_TERMINAL',1,'{}',1)"
    )
    try:
        con.execute(
            "INSERT INTO events(job_id,operation_id,event_type,terminal,payload_json,created_at_ms) "
            "VALUES('j1','op1','JOB_TERMINAL_DUP',1,'{}',2)"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("schema allowed two terminal events for one job")

    # Baseline file bindings.
    expected = spec["baseline"]
    assert git("hash-object", "server.py") == expected["server_blob"]
    assert git("hash-object", "oauth_provider.py") == expected["oauth_provider_blob"]
    assert (
        git("hash-object", "docs/V2_0_BASELINE_FREEZE.md")
        == expected["v2_0_freeze_blob"]
    )

    # HEAD may advance because this prelock itself is committed; the baseline
    # commit must remain an ancestor.
    ancestors = git("rev-list", "HEAD").splitlines()
    assert expected["git_head"] in ancestors

    policies = {e["code"]: e["policy"] for e in spec["retry"]["error_classes"]}
    assert len(policies) == len(spec["retry"]["error_classes"])
    assert policies["OPERATION_IN_DOUBT"] == "RECONCILE_ONLY"
    assert policies["OPERATION_CONFLICT"] == "NEVER"

    assert spec["terminal_events"]["delivery_guarantee"].startswith("at-least-once")
    assert spec["job"]["relaunch_after_lost"] is False

    print("PASS V2-A prelock JSON structure")
    print("PASS SQLite schema v1 parses")
    print("PASS state-machine closure")
    print("PASS one terminal event per job")
    print("PASS V2-0 baseline hash bindings")
    print("PASS retry/error policy uniqueness")
    print("PASS no-auto-relaunch-after-LOST contract")


if __name__ == "__main__":
    main()
