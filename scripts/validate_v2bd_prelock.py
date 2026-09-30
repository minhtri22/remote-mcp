"""Static validator for V2-BD multi-device routing prelock."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=ROOT/"specs"/"v2bd_prelock.json"
CENTRAL=ROOT/"specs"/"v2bd_schema_v3.sql"
NODE=ROOT/"specs"/"v2bd_node_schema_v1.sql"
V1=ROOT/"specs"/"v2a_schema_v1.sql"
V2=ROOT/"specs"/"v2b_schema_v2.sql"

def git(*args:str)->str:
    return subprocess.check_output(["git","-C",str(ROOT),*args],text=True).strip()

def main():
    s=json.loads(SPEC.read_text(encoding="utf-8"))
    assert s["spec_id"]=="REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK"
    assert s["status"]=="FROZEN"
    assert s["binds"]["git_head"] in git("rev-list","HEAD").splitlines()
    assert git("hash-object","specs/two_device_production_session_smoke.json")==s["binds"]["two_device_session_smoke_blob"]
    assert git("hash-object","specs/v2b_production_release_manifest.json")==s["binds"]["v2b_production_release_manifest_blob"]

    # Prospective central migration parses after V2-A + V2-B schemas.
    con=sqlite3.connect(":memory:")
    con.executescript(V1.read_text(encoding="utf-8"))
    con.executescript(V2.read_text(encoding="utf-8"))
    con.executescript(CENTRAL.read_text(encoding="utf-8"))
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in (
        "devices","device_pairings","device_request_nonces",
        "project_device_bindings","task_device_bindings",
        "device_commands","routed_jobs","device_events"
    ):
        assert name in tables,name
    cmd_cols={r[1] for r in con.execute("PRAGMA table_info(device_commands)")}
    assert "operation_step" in cmd_cols
    idx={r[1] for r in con.execute("PRAGMA index_list(device_commands)")}
    assert "uq_device_commands_operation_step" in idx
    con.close()

    # Node schema is independently valid.
    node=sqlite3.connect(":memory:")
    node.executescript(NODE.read_text(encoding="utf-8"))
    nt={r[0] for r in node.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "node_meta","node_projects","node_tasks","node_commands",
        "node_cas_mutations","node_routed_jobs"
    }<=nt
    node.close()

    assert s["architecture"]["no_automatic_device_selection"] is True
    assert "outbound HTTPS long-poll" in s["architecture"]["topology"]
    assert s["device_identity"]["key_type"]=="Ed25519"
    assert s["pairing"]["ttl_seconds"]==600
    assert s["auth_replay_protection"]["timestamp_window_seconds"]==60
    assert s["auth_replay_protection"]["nonce_replay_retention_seconds"]==600

    states=s["device_states"]
    assert states["states"]==["ONLINE","OFFLINE","REVOKED"]
    assert states["node_heartbeat_interval_seconds"]==15
    assert states["offline_after_seconds"]==60
    assert "never reassigned" in states["OFFLINE"]
    assert "terminal" in states["REVOKED"]

    transport=s["transport"]
    assert transport["poll_long_wait_seconds"]==25
    assert transport["lease_seconds"]==45
    assert transport["command_mutation_ttl_seconds"]==30
    assert "same bound device" in transport["delivery_semantics"]
    assert "COMMAND_CONFLICT" in transport["node_duplicate_rule"]
    assert "(operation_id, operation_step)" in transport["operation_command_key"]
    assert "PROJECT_PROBE" in transport["operation_step_rule"]
    assert "PROJECT_BIND" in transport["operation_step_rule"]

    task=s["task_binding"]
    assert task["immutable"] is True
    assert task["caller_override"] is False
    assert "never changes" in task["no_silent_migration"]

    node_runtime=s["node_runtime"]
    assert node_runtime["durable_runtime_dir"].endswith("/durable")
    assert "V2-A DurableService" in node_runtime["durable_job_engine"]
    cas=s["routed_operations"]["node_cas_intent_journal"]
    assert cas["table"]=="node_cas_mutations"
    assert cas["prepare_before_replace"] is True

    project=s["project_binding"]
    assert "Forbidden" in project["offline_rebind"]
    assert "DEVICE_CONTEXT_REQUIRED" in project["direct_project_register_guard"]

    tools=s["public_tools"]
    assert len(tools)==9
    assert len(set(tools.values()))==9
    for name in (
        "device_pair_begin","device_list","device_status","device_revoke",
        "project_register_on_device","project_bind_device",
        "task_list_dir","task_read_file","task_search"
    ):
        assert name in tools

    q=s["real_two_execution_device_qualification"]
    assert q["required"] is True
    assert q["machines"]==["physical-machine-1","physical-machine-2"]
    assert len(q["gates"])>=20
    joined="\n".join(q["gates"])
    for phrase in (
        "distinct dev_* IDs",
        "DEVICE_OFFLINE",
        "REMOTEMCP_DEVICE_ID",
        "same proxy_job_id/node_job_id",
        "DEVICE_REPLAY",
        "DEVICE_SIGNATURE_STALE",
        "COMMAND_CONFLICT",
        "node CAS crash after atomic replace"
    ):
        assert phrase in joined,phrase

    # This gate must remain spec-only.
    forbidden_paths=[
        ROOT/"remotemcp"/"device",
        ROOT/"remotemcp"/"routing",
        ROOT/"remotemcp"/"multiagent"/"migrations"/"003_v2bd.sql",
    ]
    assert not any(p.exists() for p in forbidden_paths),forbidden_paths

    assert s["scope"]["implementation_allowed"] is False
    assert s["scope"]["real_device_pairing_allowed"] is False
    assert s["scope"]["qualification_execution_allowed"] is False
    assert s["next_gate"]=="REMOTE_MCP_V2BD_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK"

    print("PASS V2-BD central schema v3 prospective parse")
    print("PASS V2-BD node-local schema v1 parse")
    print("PASS stable device identity + Ed25519 pairing contract")
    print("PASS signed node request + nonce/timestamp replay protection")
    print("PASS ONLINE/OFFLINE/REVOKED state semantics")
    print("PASS outbound-only single-endpoint routing contract")
    print("PASS durable same-device command delivery/idempotency")
    print("PASS multi-step operation command journal")
    print("PASS node-local CAS crash-recovery journal")
    print("PASS node-local V2-A durable subruntime contract")
    print("PASS project->device binding and immutable task inheritance")
    print("PASS no-silent-migration/offline-no-failover contract")
    print("PASS routed-job proxy/offline survival contract")
    print("PASS exact public V2-BD tool signatures")
    print("PASS real two-execution-machine qualification matrix")
    print("PASS spec-only gate: no V2-BD runtime implementation")


if __name__=="__main__":
    main()
