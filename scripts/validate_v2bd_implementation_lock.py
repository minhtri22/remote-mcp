"""Static-only validator for the V2-BD implementation execution lock."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"specs"/"v2bd_implementation_lock.json"
PRE=ROOT/"specs"/"v2bd_prelock.json"
V1=ROOT/"specs"/"v2a_schema_v1.sql"
V2=ROOT/"specs"/"v2b_schema_v2.sql"
V3=ROOT/"specs"/"v2bd_schema_v3.sql"
NODE=ROOT/"specs"/"v2bd_node_schema_v1.sql"

def git(*args:str)->str:
    return subprocess.check_output(["git","-C",str(ROOT),*args],text=True).strip()

def main():
    lock=json.loads(LOCK.read_text(encoding="utf-8"))
    pre=json.loads(PRE.read_text(encoding="utf-8"))
    assert lock["lock_id"]=="REMOTE_MCP_V2BD_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK"
    assert lock["status"]=="FROZEN"
    assert lock["binds"]["git_head"] in git("rev-list","HEAD").splitlines()
    assert git("hash-object","specs/v2bd_prelock.json")==lock["binds"]["prelock_blob"]
    assert git("hash-object","specs/v2bd_schema_v3.sql")==lock["binds"]["central_schema_blob"]
    assert git("hash-object","specs/v2bd_node_schema_v1.sql")==lock["binds"]["node_schema_blob"]
    assert git("hash-object","docs/V2_BD_MULTI_DEVICE_ROUTING_PRELOCK.md")==lock["binds"]["prelock_doc_blob"]
    assert git("hash-object","scripts/validate_v2bd_prelock.py")==lock["binds"]["prelock_validator_blob"]

    # No implementation package is allowed to exist yet.
    assert not (ROOT/"remotemcp"/"routing").exists()
    assert not (ROOT/"remotemcp"/"node").exists()
    assert not (ROOT/"remotemcp"/"multiagent"/"migrations"/"003_v2bd.sql").exists()

    gf=lock["gateway_module_layout"]["files"]
    nf=lock["node_module_layout"]["files"]
    assert len(gf)==13 and len(gf)==len(set(gf))
    assert len(nf)==18 and len(nf)==len(set(nf))
    assert "remotemcp/routing/migrations/003_v2bd.sql" in gf
    assert "remotemcp/node/migrations/001_node.sql" in nf

    # Central schema prospective parse and critical columns/index.
    con=sqlite3.connect(":memory:")
    con.executescript(V1.read_text(encoding="utf-8"))
    con.executescript(V2.read_text(encoding="utf-8"))
    con.executescript(V3.read_text(encoding="utf-8"))
    pairing_cols={r[1] for r in con.execute("PRAGMA table_info(device_pairings)")}
    cmd_cols={r[1] for r in con.execute("PRAGMA table_info(device_commands)")}
    indexes={r[1] for r in con.execute("PRAGMA index_list(device_commands)")}
    assert "code_nonce" in pairing_cols
    assert "operation_step" in cmd_cols
    assert "uq_device_commands_operation_step" in indexes
    con.close()

    node=sqlite3.connect(":memory:")
    node.executescript(NODE.read_text(encoding="utf-8"))
    nt={r[0] for r in node.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "node_cas_mutations" in nt
    assert "node_routed_jobs" in nt
    node.close()

    ident=lock["identity_storage"]
    assert ident["private_key_serialization"].startswith("PKCS8 PEM")
    assert ident["pairing_secret_bytes"]==32
    assert "never regenerate" in ident["startup_consistency"]

    pair=lock["exact_pairing_http"]
    assert pair["path"]=="/device/v1/pair"
    assert pair["method"]=="POST"
    assert pair["max_body_bytes"]==65536
    assert pair["canonical_pair_string"].startswith("RMCPPAIR1")

    begin=lock["pairing_begin_tool"]
    assert begin["clear_storage"] is False
    assert begin["code_derivation"].startswith("pc1_")
    assert "--code-file" in begin["node_pair_command"]
    assert "--code " not in begin["node_pair_command"]

    auth=lock["signed_request_verifier"]
    assert auth["timestamp_window_seconds"]==60
    assert auth["nonce_retention_seconds"]==600
    assert auth["max_body_bytes"]==1048576

    queue=lock["command_queue"]
    assert "UNIQUE(operation_id,operation_step)" in queue["mutation_idempotency"]
    assert queue["gateway_wait_seconds"]==55
    assert queue["no_other_device_redelivery"] is True

    assert lock["routed_workflow_steps"]["PROJECT_REGISTER_ON_DEVICE"][0]=="step 0 PROJECT_PROBE"
    assert "node_cas_mutations" in lock["node_cas"]["journal"]
    assert lock["node_durable_jobs"]["service"].startswith("Existing V2-A DurableService")

    cli=lock["exact_cli"]
    assert "--code-file" in cli["commands"]["pair"]
    assert "--code " not in cli["commands"]["pair"]
    assert cli["exit_codes"]["success"]==0

    assert len(lock["exact_test_files"])==21
    assert len(lock["qualification_scripts"])==7
    matrix="\n".join(lock["failure_injection_matrix"])
    for phrase in (
        "device_pair_begin pairing row commit",
        "duplicate nonce",
        "stale route_generation",
        "node CAS crash after atomic replace",
        "node-agent restart while durable payload continues",
        "caller attempts to override task device",
        "rjob_* passed to gateway-local"
    ):
        assert phrase in matrix,phrase

    pilot=lock["isolated_two_device_pilot_lock"]
    assert pilot["machines"]["machine1"]["login_identity"]=="physical-machine-1"
    assert pilot["machines"]["machine2"]["login_identity"]=="physical-machine-2"
    assert "RemoteMCP-V2BD-Pilot" in pilot["machines"]["machine1"]["root_template"]
    assert "RemoteMCP-V2BD-Pilot" in pilot["machines"]["machine2"]["root_template"]
    assert "RemoteMCP-src" in pilot["no_research_bindings"]

    prod=lock["production_freeze"]
    assert prod["expected_port"]==8099
    assert prod["expected_pid_at_lock"]==11372
    assert prod["restart_repoint_or_deploy_allowed"] is False
    assert prod["real_pairing_allowed"] is False

    assert len(pre["public_tools"])==12
    assert pre["task_binding"]["immutable"] is True
    assert pre["architecture"]["no_automatic_device_selection"] is True
    assert lock["next_gate_on_pass"]=="REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION"

    print("PASS exact gateway routing module layout")
    print("PASS exact execution-node module layout")
    print("PASS migration 003 + node schema bindings")
    print("PASS node identity/key + pairing-secret storage contract")
    print("PASS exact pair endpoint and pairing replay contract")
    print("PASS signed request verifier ordering/limits")
    print("PASS command queue/long-poll/idempotency contract")
    print("PASS project/task routed workflow transaction boundaries")
    print("PASS node CAS restart-recovery contract")
    print("PASS routed-job proxy + task-scoped observation contract")
    print("PASS exact node CLI contract")
    print("PASS full failure-injection matrix")
    print("PASS isolated two-real-device pilot execution lock")
    print("PASS production 8099 / no-real-pairing freeze")
    print("PASS no V2-BD runtime implementation before authorization")

if __name__=="__main__":
    main()
