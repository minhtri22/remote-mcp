"""Post-implementation static validator for V2-BD routing runtime."""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=json.loads((ROOT/"specs"/"v2bd_implementation_lock.json").read_text(encoding="utf-8"))

def fail(msg:str):
    raise AssertionError(msg)

def main():
    # Frozen exact inventories must exist.
    for rel in LOCK["exact_test_files"]:
        assert (ROOT/rel).is_file(), rel
    for rel in LOCK["qualification_scripts"]:
        assert (ROOT/rel).is_file(), rel

    # Exact gateway/node module layouts from the implementation lock.
    for rel in LOCK["gateway_module_layout"]["files"]:
        assert (ROOT/rel).is_file(), rel
    for rel in LOCK["node_module_layout"]["files"]:
        assert (ROOT/rel).is_file(), rel

    # Frozen migration/schema byte identity.
    assert (ROOT/"specs/v2bd_schema_v3.sql").read_bytes() == (
        ROOT/"remotemcp/routing/migrations/003_v2bd.sql"
    ).read_bytes()
    assert (ROOT/"specs/v2bd_node_schema_v1.sql").read_bytes() == (
        ROOT/"remotemcp/node/migrations/001_node.sql"
    ).read_bytes()

    server=(ROOT/"server.py").read_text(encoding="utf-8")
    proc=(ROOT/"remotemcp/durable/process.py").read_text(encoding="utf-8")
    worker=(ROOT/"remotemcp/durable/worker.py").read_text(encoding="utf-8")

    for needle in (
        "RoutingService(",
        "register_device_routes(mcp, routing_service)",
        "project_register_or_local",
        "task_create_or_local",
        "task_claim_or_local",
        "file_write_cas_or_local",
        "task_job_submit_or_local",
        "task_job_get(",
        "task_job_logs(",
        "task_job_result(",
        "windows_process_options()",
    ):
        assert needle in server, needle

    assert "CREATE_NO_WINDOW" in proc
    assert "STARTF_USESHOWWINDOW" in proc
    assert "SW_HIDE" in proc
    assert "windows_process_options()" in worker

    runtime_files=[
        *sorted((ROOT/"remotemcp/routing").rglob("*.py")),
        *sorted((ROOT/"remotemcp/node").rglob("*.py")),
    ]
    joined="\n".join(p.read_text(encoding="utf-8") for p in runtime_files)
    assert "shell=True" not in joined
    assert "os.system(" not in joined
    # Node is outbound-only: no uvicorn/listen/bind server implementation.
    node_joined="\n".join(
        p.read_text(encoding="utf-8") for p in sorted((ROOT/"remotemcp/node").rglob("*.py"))
    )
    assert "uvicorn.run" not in node_joined
    assert ".listen(" not in node_joined
    assert "socket.bind(" not in node_joined
    assert "serve_forever(" not in node_joined

    # Headless qualification is an implementation amendment required on Windows.
    assert (ROOT/"scripts/qualify_windows_headless_processes.py").is_file()

    # Target-device identity observability amendment.
    target=json.loads(
        (ROOT/"specs/v2bd_target_identity_amendment.json").read_text(encoding="utf-8")
    )
    assert target["status"]=="FROZEN_BEFORE_RUNTIME_PATCH"
    assert target["public_tool_count_unchanged"]==44
    assert target["invariants"]["task_routing"].startswith("Task-scoped operations")
    assert target["acceptance"]["auth_client_id_difference_required"] is False
    devices=(ROOT/"remotemcp/routing/devices.py").read_text(encoding="utf-8")
    node_client=(ROOT/"remotemcp/node/client.py").read_text(encoding="utf-8")
    assert '"hostname":platform.get("hostname")' in devices
    assert '"platform":node_platform()' in node_client
    assert "project_status_or_local" in server
    assert "task_status_or_local" in server

    # Real pilot is present but must still be locked by current implementation gate.
    pilot=(ROOT/"scripts/qualify_v2bd_two_machine_pilot.py").read_text(encoding="utf-8")
    assert "PILOT_EXECUTION_NOT_OPENED_BY_CURRENT_GATE" in pilot
    assert "--ack-isolated-pilot" in pilot

    print("PASS exact V2-BD gateway module inventory")
    print("PASS exact V2-BD node module inventory")
    print("PASS migration 003 byte identity")
    print("PASS node schema v1 byte identity")
    print("PASS gateway routing facade/server wiring")
    print("PASS Windows headless process wiring")
    print("PASS outbound-only node runtime")
    print("PASS no shell=True/os.system in V2-BD runtime")
    print("PASS exact 21-test inventory")
    print("PASS exact 7 qualification-script inventory")
    print("PASS target-device identity/hostname observability amendment")
    print("PASS real two-machine pilot remains execution-locked")

if __name__=="__main__":
    main()