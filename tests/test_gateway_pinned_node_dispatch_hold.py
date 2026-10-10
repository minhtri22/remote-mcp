"""Regression guards: science agents cannot use old or quarantined node."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest

from remotemcp.routing.commands import CommandRepository
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


class DB:
    def __init__(self):
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE device_commands("
            "command_id TEXT PRIMARY KEY,device_id TEXT,route_generation INTEGER,"
            "command_type TEXT,state TEXT,command_expires_at_ms INTEGER,"
            "created_at_ms INTEGER,updated_at_ms INTEGER,"
            "lease_expires_at_ms INTEGER,finished_at_ms INTEGER,"
            "delivery_attempt INTEGER DEFAULT 0,error_code TEXT);"
        )

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self.conn

    def query_one(self,sql,params=()):
        return self.conn.execute(sql,params).fetchone()


class Devices:
    def __init__(self,sha):
        self.sha=sha

    def require_online(self,_device_id):
        return {"route_generation":1,"state":"ONLINE"}

    def status(self,_device_id):
        return {
            "state":"ONLINE",
            "capacity_signal_fresh":True,
            "node_attestation":{
                "release_commit":self.sha,
                "supported_node_diagnostics":["process_inspect_v1"],
            },
        }


SHA="a"*40


def repository(monkeypatch, *,release="old",hold="1"):
    monkeypatch.setenv("REMOTEMCP_REQUIRED_NODE_RELEASE_COMMIT_SHA",SHA)
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH",hold)
    db=DB()
    devices=Devices(release)
    config=SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120)
    repo=CommandRepository(config,db,devices)
    return repo,db,devices


def insert_command(db,command_id,kind,timestamp):
    db.conn.execute(
        "INSERT INTO device_commands("
        "command_id,device_id,route_generation,command_type,state,"
        "command_expires_at_ms,created_at_ms,updated_at_ms)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (command_id,"device1",1,kind,"QUEUED",timestamp+120_000,timestamp,timestamp),
    )
    db.conn.commit()


def test_old_node_never_receives_original_queued_science_command(monkeypatch):
    repo,db,_=repository(monkeypatch,release="old",hold="0")
    t=now_ms()
    insert_command(db,"cmd_job","JOB_SUBMIT",t-50)
    insert_command(db,"cmd_diag","NODE_PROCESS_INSPECT",t-10)
    assert repo.poll("device1",1) is None
    # Old node receives NO agent commands, including pre-existing reads.
    assert db.conn.execute(
        "SELECT delivery_attempt FROM device_commands WHERE command_id='cmd_diag'"
    ).fetchone()[0]==0
    held=db.conn.execute("SELECT state,delivery_attempt FROM device_commands WHERE command_id='cmd_job'").fetchone()
    assert tuple(held)==("QUEUED",0)


def test_operator_quarantine_holds_science_even_after_node_is_upgraded(monkeypatch):
    repo,db,_=repository(monkeypatch,release=SHA,hold="1")
    t=now_ms()
    insert_command(db,"cmd_job","JOB_SUBMIT",t-50)
    insert_command(db,"cmd_inventory","TASK_LIST_DIR",t-20)
    assert repo.poll("device1",1)["command_id"]=="cmd_inventory"
    assert repo.poll("device1",1) is None
    assert db.conn.execute("SELECT state FROM device_commands WHERE command_id='cmd_job'").fetchone()[0]=="QUEUED"


def test_fail_closed_submit_before_any_sql_write(monkeypatch):
    repo,db,device=repository(monkeypatch,release="old",hold="0")
    with db.transaction() as con:
        with pytest.raises(DurableError) as exc:
            repo.create_in_tx(con,"device1","JOB_SUBMIT",{"argv":["python"]},route_generation=1)
    assert exc.value.code=="NODE_RELEASE_PIN_MISMATCH"
    device.sha=SHA
    repo.science_dispatch_hold=True
    # Both new submissions and recoveries must be blocked before any write;
    # otherwise a recovery can remain queued and run after the hold is lifted.
    for kind in ("JOB_SUBMIT", "JOB_RECOVER_ROUTED_JOB"):
        with db.transaction() as con:
            with pytest.raises(DurableError) as exc:
                repo.create_in_tx(
                    con,"device1",kind,{"argv":["python"]},route_generation=1,
                )
        assert exc.value.code=="SCIENCE_DISPATCH_QUARANTINED"
    assert db.conn.execute("SELECT COUNT(*) FROM device_commands").fetchone()[0]==0


def test_only_explicit_release_pin_and_quarantine_clear_can_dispatch(monkeypatch):
    repo,db,_=repository(monkeypatch,release=SHA,hold="0")
    t=now_ms()
    insert_command(db,"cmd_job","JOB_SUBMIT",t)
    assert repo.poll("device1",1)["command_id"]=="cmd_job"


def test_old_attestation_without_capability_does_not_dispatch(monkeypatch):
    repo,db,dev=repository(monkeypatch,release=SHA,hold="0")
    dev.status=lambda _: {
        "state":"ONLINE","capacity_signal_fresh":True,
        "node_attestation":{"release_commit":SHA},
    }
    t=now_ms()
    insert_command(db,"cmd_job","JOB_SUBMIT",t)
    assert repo.poll("device1",1) is None
    assert repo._science_dispatch_blocker("device1")=="NODE_DIAGNOSTIC_CAPABILITY_MISSING"


def test_device_status_reports_effective_old_release_denial_to_every_agent(monkeypatch):
    from remotemcp.routing.service import RoutingService

    repo,db,dev=repository(monkeypatch,release="old",hold="0")
    service=object.__new__(RoutingService)
    service.devices=dev
    service.commands=repo
    service._gateway_attestation=lambda: {"release_commit":"gateway"}
    observed=service.device_status("device1")
    gate=observed["science_job_dispatch_gate"]
    assert gate["release_pin_enforced"] is True
    assert gate["minimum_exact_release_sha"]==SHA
    assert gate["new_job_admission_allowed"] is False
    assert gate["reason"]=="NODE_RELEASE_PIN_MISMATCH"
    dev.sha=SHA
    qualified=service.device_status("device1")["science_job_dispatch_gate"]
    assert qualified["new_job_admission_allowed"] is True
    assert qualified["reason"]=="PINNED_NODE_RELEASE_QUALIFIED"
    repo.science_dispatch_hold=True
    held=service.device_status("device1")["science_job_dispatch_gate"]
    assert held["new_job_admission_allowed"] is False
    assert held["command_quarantine_active"] is True


def test_old_node_rejects_readonly_agent_command_as_well_as_job(monkeypatch):
    repo,db,_=repository(monkeypatch,release="old",hold="0")
    with db.transaction() as con:
        with pytest.raises(DurableError) as exc:
            repo.create_in_tx(
                con,"device1","TASK_LIST_DIR",{"task_id":"task1"},
                route_generation=1,
            )
    assert exc.value.code=="NODE_RELEASE_PIN_MISMATCH"
    assert db.conn.execute("SELECT COUNT(*) FROM device_commands").fetchone()[0]==0


def test_persistent_gateway_pin_survives_legacy_watchdog_environment(tmp_path,monkeypatch):
    import json

    file=tmp_path/"gateway-config.json"
    file.write_text(json.dumps({
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":SHA,
        "hold_science_job_dispatch":True,
    }),encoding="utf-8")
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH",str(file))
    monkeypatch.delenv("REMOTEMCP_REQUIRED_NODE_RELEASE_COMMIT_SHA",raising=False)
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH","0")
    db=DB()
    device=Devices("old")
    config=SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120)
    repo=CommandRepository(config,db,device)
    assert repo.required_release_sha==SHA
    assert repo.science_dispatch_hold is True
    assert repo._science_dispatch_blocker("device1")=="NODE_RELEASE_PIN_MISMATCH"
    device.sha=SHA
    assert repo._science_dispatch_blocker("device1")=="SCIENCE_DISPATCH_QUARANTINED"


def test_bad_durable_release_pin_is_startup_fatal(tmp_path,monkeypatch):
    import json

    file=tmp_path/"gateway-config.json"
    file.write_text(json.dumps({
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":"bad",
    }),encoding="utf-8")
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH",str(file))
    db=DB()
    device=Devices("old")
    config=SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120)
    with pytest.raises(DurableError) as exc:
        CommandRepository(config,db,device)
    assert exc.value.code=="GATEWAY_RELEASE_PIN_INVALID"


def test_explicit_durable_release_preserves_pin_and_ignores_environment_hold(tmp_path,monkeypatch):
    import json

    file=tmp_path/"gateway-config.json"
    file.write_text(json.dumps({
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":SHA,
        "hold_science_job_dispatch":False,
    }),encoding="utf-8")
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH",str(file))
    monkeypatch.setenv("REMOTEMCP_REQUIRED_NODE_RELEASE_COMMIT_SHA","b"*40)
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH","1")
    db=DB()
    devices=Devices(SHA)
    repo=CommandRepository(
        SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120),
        db,devices,
    )
    assert repo.required_release_sha==SHA
    assert repo.science_dispatch_hold is False
    assert repo._science_dispatch_blocker("device1") is None
    # Unqualified node still blocked, even with explicit dispatch release.
    devices.sha="old"
    assert repo._science_dispatch_blocker("device1")=="NODE_RELEASE_PIN_MISMATCH"
    devices.sha=SHA
    t=now_ms()
    insert_command(db,"cmd_qualified","JOB_SUBMIT",t)
    assert repo.poll("device1",1)["command_id"]=="cmd_qualified"


def test_missing_durable_hold_defaults_to_quarantine(tmp_path,monkeypatch):
    import json
    file=tmp_path/"gateway-config.json"
    file.write_text(json.dumps({
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":SHA,
    }),encoding="utf-8")
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH",str(file))
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH","0")
    repo=CommandRepository(
        SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120),
        DB(),Devices(SHA),
    )
    assert repo.science_dispatch_hold is True
    assert repo._science_dispatch_blocker("device1")=="SCIENCE_DISPATCH_QUARANTINED"


@pytest.mark.parametrize("invalid_hold",[None,0,1,"false","true",[],{}])
def test_invalid_durable_hold_fails_closed_at_startup(tmp_path,monkeypatch,invalid_hold):
    import json
    file=tmp_path/"gateway-config.json"
    file.write_text(json.dumps({
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":SHA,
        "hold_science_job_dispatch":invalid_hold,
    }),encoding="utf-8")
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH",str(file))
    with pytest.raises(DurableError) as exc:
        CommandRepository(
            SimpleNamespace(command_lease_seconds=30,mutation_ttl_seconds=120),
            DB(),Devices(SHA),
        )
    assert exc.value.code=="GATEWAY_SCIENCE_QUARANTINE_CONFIG_INVALID"
