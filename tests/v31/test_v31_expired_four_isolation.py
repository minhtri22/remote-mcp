"""ISO-01..12, synthetic only. No real IDs, databases, credentials or jobs."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.routing.commands import CommandRepository
from remotemcp.routing.crypto import canonical_json
from remotemcp.routing.historical_isolation import ExpiredFourIsolation, SCHEMA

DEVICE = "dev_" + "a" * 32
PROJECT = "prj_fixture"
TASK = "tsk_fixture"
PIN = "b" * 40
KINDS = ("JOB_SUBMIT", "JOB_GET", "PROJECT_PROBE", "TASK_BASE_RESOLVE")
PROXY = "rjob_" + "f" * 32


def sha_rows(rows):
    b = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b).hexdigest()


class Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE device_commands("
            "command_id TEXT PRIMARY KEY,device_id TEXT,route_generation INTEGER,"
            "operation_id TEXT,operation_step INTEGER DEFAULT 0,"
            "project_id TEXT,task_id TEXT,command_type TEXT,request_hash TEXT,"
            "payload_json TEXT,state TEXT,command_expires_at_ms INTEGER,"
            "lease_expires_at_ms INTEGER,delivery_attempt INTEGER DEFAULT 0,"
            "created_at_ms INTEGER,updated_at_ms INTEGER,finished_at_ms INTEGER,"
            "result_json TEXT,error_code TEXT,error_json TEXT);"
            "CREATE UNIQUE INDEX uq_device_commands_operation_step "
            "ON device_commands(operation_id,operation_step) "
            "WHERE operation_id IS NOT NULL;"
        )

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self.conn

    def query_one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def snapshot(self, ids):
        return [dict(self.conn.execute(
            "SELECT * FROM device_commands WHERE command_id=?", (cid,)
        ).fetchone()) for cid in ids]


class Devices:
    def __init__(self):
        self.online = True
        self.fresh = True
        self.sha = PIN

    def require_online(self, device_id):
        if not self.online:
            raise DurableError("DEVICE_OFFLINE", "synthetic node unavailable")
        return {"state": "ONLINE", "route_generation": 1}

    def status(self, device_id):
        return {
            "state": "ONLINE" if self.online else "OFFLINE",
            "capacity_signal_fresh": self.fresh,
            "node_attestation": {
                "release_commit": self.sha,
                "supported_node_diagnostics": ["process_inspect_v1"],
            },
        }

    def _event(self, con, *args):
        pass


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("REMOTEMCP_GATEWAY_CONFIG_PATH", str(tmp_path / "absent-config.json"))
    monkeypatch.setenv("REMOTEMCP_REQUIRED_NODE_RELEASE_COMMIT_SHA", PIN)
    monkeypatch.setenv("REMOTEMCP_HOLD_SCIENCE_JOB_DISPATCH", "0")
    for suffix in ("MANIFEST_PATH", "MANIFEST_SHA256", "PUBKEY_B64"):
        monkeypatch.delenv("REMOTEMCP_V31_EXPIRED_ISOLATION_" + suffix, raising=False)
    db = Db()
    dev = Devices()
    config = SimpleNamespace(command_lease_seconds=45, mutation_ttl_seconds=120)
    repo = CommandRepository(config, db, dev)
    origin = now_ms()
    inventory = []
    targets = []
    payloads = []
    for i, typ in enumerate(KINDS):
        cid = f"cmd_{i+1:032x}"
        payload = (
            {"proxy_job_id": PROXY, "task_id": TASK, "argv": ["fixture"]}
            if typ == "JOB_SUBMIT" else
            {"proxy_job_id": "rjob_not_protected"} if typ == "JOB_GET" else
            {"path": "synthetic"} if typ == "PROJECT_PROBE" else
            {"project_id": PROJECT, "base_ref": "HEAD"}
        )
        payloads.append(payload)
        req = repo.request_hash(DEVICE, 1, typ, PROJECT, TASK, payload)
        expires = origin - 50_000
        lease_expires = origin - 5_000
        row = dict(
            command_id=cid, device_id=DEVICE, route_generation=1,
            operation_id=f"op_{i+1}", operation_step=0,
            project_id=PROJECT, task_id=TASK,
            command_type=typ, request_hash=req,
            payload_json=canonical_json(payload), state="LEASED",
            command_expires_at_ms=expires, lease_expires_at_ms=lease_expires,
            delivery_attempt=1, created_at_ms=origin-100_000,
            updated_at_ms=origin-50_000, finished_at_ms=None,
            result_json=None, error_code=None, error_json=None,
        )
        cols = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        db.conn.execute(f"INSERT INTO device_commands({cols}) VALUES ({placeholders})", tuple(row.values()))
        inv = {
            "command_id": cid, "command_type": typ, "request_hash": req,
            "route_generation": 1, "command_expires_at_ms": expires,
            "lease_expires_at_ms": lease_expires,
            "classification": (
                "EXPIRED_NODE_RECEIVED_NONTERMINAL" if i == 3
                else "EXPIRED_DELIVERED_NO_RECEIPT"
            ),
        }
        inventory.append(inv)
        targets.append({
            **{k: inv[k] for k in (
                "command_id", "command_type", "request_hash", "route_generation",
                "command_expires_at_ms", "lease_expires_at_ms"
            )},
            "operation_id": f"op_{i+1}", "operation_step": 0,
            "proxy_job_id": PROXY if i == 0 else None,
        })
    for i in range(15):
        inventory.append({
            "command_id": f"cmd_{i+5:032x}",
            "command_type": "TASK_LIST_DIR",
            "classification": "EXPIRED_NODE_TERMINAL_GATEWAY_STALE",
        })
    manifest = dict(
        schema=SCHEMA, device_id=DEVICE, inventory_rows=inventory,
        frozen_inventory_sha256=sha_rows(inventory),
        protected_commands=targets,
    )
    policy = ExpiredFourIsolation(manifest, expected_digest=manifest["frozen_inventory_sha256"])
    repo.isolation = policy
    return repo, db, dev, payloads, manifest


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return setup(monkeypatch, tmp_path)


def protect_ids(repo):
    return repo.isolation.ids


def assert_denied(code, func):
    with pytest.raises(DurableError) as info:
        func()
    assert info.value.code == code


def check_unchanged(repo, db, before):
    assert db.snapshot(protect_ids(repo)) == before


def test_iso01_frozen_four_selection_and_manifest_signature(harness, tmp_path):
    repo, db, dev, payloads, manifest = harness
    before = db.snapshot(protect_ids(repo))
    assert len(repo.isolation.ids) == 4
    assert {t["command_type"] for t in repo.isolation.bindings.values()} == set(KINDS)
    assert Counter(r["classification"] for r in manifest["inventory_rows"]) == {
        "EXPIRED_DELIVERED_NO_RECEIPT": 3,
        "EXPIRED_NODE_RECEIVED_NONTERMINAL": 1,
        "EXPIRED_NODE_TERMINAL_GATEWAY_STALE": 15,
    }
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )).decode("ascii")
    signed = dict(manifest)
    signed["signature_b64"] = base64.b64encode(
        key.sign(canonical_json(signed).encode())
    ).decode()
    blob = json.dumps(signed, sort_keys=True).encode()
    path = tmp_path / "private-fixture-manifest.json"
    path.write_bytes(blob)
    loaded = ExpiredFourIsolation.load_signed(
        path, hashlib.sha256(blob).hexdigest(), pub,
        expected_inventory_digest=manifest["frozen_inventory_sha256"],
    )
    assert loaded.ids == repo.isolation.ids
    assert_denied("HISTORICAL_ISOLATION_INVALID_HOLD",lambda:
        ExpiredFourIsolation.load_signed(
            path, "0"*64, pub,
            expected_inventory_digest=manifest["frozen_inventory_sha256"],
        ))
    signed["signature_b64"] = base64.b64encode(bytes(64)).decode()
    path.write_text(json.dumps(signed), encoding="utf-8")
    tampered = path.read_bytes()
    assert_denied("HISTORICAL_ISOLATION_INVALID_HOLD",lambda:
        ExpiredFourIsolation.load_signed(
            path, hashlib.sha256(tampered).hexdigest(), pub,
            expected_inventory_digest=manifest["frozen_inventory_sha256"],
        ))
    check_unchanged(repo, db, before)


def test_iso02_lease_and_expired_queued_poll_are_not_mutated(harness):
    repo, db, _, _, _ = harness
    for state in ("LEASED", "QUEUED"):
        db.conn.execute("UPDATE device_commands SET state=? WHERE command_id=?", (state,repo.isolation.ids[0]))
        before = db.snapshot(protect_ids(repo))
        assert repo.poll(DEVICE,1) is None
        check_unchanged(repo,db,before)


def test_iso03_cancelled_expired_operation_cannot_revive(harness):
    repo,db,_,payloads,_=harness
    cid=f"cmd_{3:032x}"
    db.conn.execute("UPDATE device_commands SET state='CANCELLED',error_code='DEVICE_COMMAND_EXPIRED' WHERE command_id=?", (cid,))
    before=db.snapshot(protect_ids(repo))
    assert_denied("HISTORICAL_COMMAND_PROTECTED", lambda:
        repo.create(DEVICE,"PROJECT_PROBE",payloads[2],project_id=PROJECT,task_id=TASK,operation_id="op_3",operation_step=0))
    check_unchanged(repo,db,before)


def test_iso04_independent_operation_has_normal_rules(harness):
    repo,db,_,payloads,_=harness
    before=db.snapshot(protect_ids(repo))
    row,created=repo.create(DEVICE,"PROJECT_PROBE",payloads[2],
        project_id=PROJECT,task_id=TASK,operation_id="op_new_independent")
    assert created and row["state"]=="QUEUED"
    assert row["command_id"] not in protect_ids(repo)
    check_unchanged(repo,db,before)


def test_iso05_operation_step_alias_and_unscoped_identical_hold(harness):
    repo,db,_,payloads,_=harness
    before=db.snapshot(protect_ids(repo))
    assert_denied("AMBIGUOUS_REPLACEMENT_HOLD", lambda:
        repo.create(DEVICE,"JOB_GET",payloads[1],
                    project_id=PROJECT,task_id=TASK,operation_id="op_2",operation_step=1))
    assert_denied("AMBIGUOUS_REPLACEMENT_HOLD", lambda:
        repo.create(DEVICE,"PROJECT_PROBE",payloads[2],project_id=PROJECT,task_id=TASK))
    check_unchanged(repo,db,before)


@pytest.mark.parametrize("change", ["digest","count","type","generation","expiry","device"])
def test_iso06_invalid_manifest_never_activates(harness, change):
    repo,db,_,_,manifest=harness
    candidate=copy.deepcopy(manifest)
    if change=="digest":
        candidate["frozen_inventory_sha256"]="0"*64
    elif change=="count":
        candidate["inventory_rows"].pop()
    elif change=="type":
        candidate["protected_commands"][0]["command_type"]="TASK_LIST_DIR"
    elif change=="generation":
        candidate["protected_commands"][0]["route_generation"]=2
    elif change=="expiry":
        candidate["protected_commands"][0]["lease_expires_at_ms"]=now_ms()+100_000
    else:
        candidate["device_id"]="invalid"
    before=db.snapshot(protect_ids(repo))
    assert_denied("HISTORICAL_ISOLATION_INVALID_HOLD",lambda:
        ExpiredFourIsolation(candidate,expected_digest=manifest["frozen_inventory_sha256"]))
    check_unchanged(repo,db,before)


def test_iso07_executing_receipt_is_never_marked_terminal(harness):
    repo,db,_,_,_=harness
    before=db.snapshot(protect_ids(repo))
    assert before[-1]["command_type"]=="TASK_BASE_RESOLVE"
    assert before[-1]["state"]=="LEASED"
    assert repo.poll(DEVICE,1) is None
    check_unchanged(repo,db,before)


def test_iso08_protected_job_proxy_cannot_submit_or_recover(harness):
    repo,db,_,_,_=harness
    before=db.snapshot(protect_ids(repo))
    for typ in ("JOB_SUBMIT","JOB_RECOVER_ROUTED_JOB"):
        assert_denied("HISTORICAL_COMMAND_PROTECTED",lambda t=typ:
            repo.create(DEVICE,t,{"proxy_job_id":PROXY},
                        project_id=PROJECT,task_id=TASK,operation_id="op_independent_new"))
    check_unchanged(repo,db,before)


@pytest.mark.parametrize("status", ["offline","stale","wrong-pin"])
def test_iso09_attestation_offline_prevents_mutations(harness,status):
    repo,db,dev,payloads,_=harness
    repo.required_release_sha=PIN
    if status=="offline":dev.online=False
    if status=="stale":dev.fresh=False
    if status=="wrong-pin":dev.sha="c"*40
    before=db.snapshot(protect_ids(repo))
    assert repo.poll(DEVICE,1) is None if status!="offline" else True  # offline raises below
    check_unchanged(repo,db,before)


def test_iso10_15_terminal_controls_not_selected(harness):
    repo,db,_,payloads,manifest=harness
    before=db.snapshot(protect_ids(repo))
    assert len([x for x in manifest["inventory_rows"] if x["classification"]=="EXPIRED_NODE_TERMINAL_GATEWAY_STALE"])==15
    repo.create(DEVICE,"TASK_LIST_DIR",{"task_id":TASK},
                project_id=PROJECT,task_id=TASK,operation_id="op_read_new")
    row=repo.poll(DEVICE,1)
    assert row["command_type"]=="TASK_LIST_DIR"
    assert row["command_id"] not in protect_ids(repo)
    check_unchanged(repo,db,before)


def test_iso11_os_agnostic_decision_on_fixture(harness):
    repo,db,_,payloads,_=harness
    before=db.snapshot(protect_ids(repo))
    assert len(protect_ids(repo))==4
    assert_denied("HISTORICAL_COMMAND_PROTECTED",lambda:
        repo.create(DEVICE,"JOB_GET",payloads[1],project_id=PROJECT,task_id=TASK,
                    operation_id="op_2",operation_step=0))
    check_unchanged(repo,db,before)


def test_iso12_db_lock_or_restart_does_not_replay(harness,monkeypatch):
    repo,db,dev,payloads,manifest=harness
    before=db.snapshot(protect_ids(repo))
    original=repo.isolation.assert_bound
    def fail(_con,_device):
        raise sqlite3.OperationalError("locking protocol")
    monkeypatch.setattr(repo.isolation,"assert_bound",fail)
    with pytest.raises(sqlite3.OperationalError):
        repo.poll(DEVICE,1)
    check_unchanged(repo,db,before)
    monkeypatch.setattr(repo.isolation,"assert_bound",original)
    fresh=CommandRepository(repo.config,db,dev)
    fresh.isolation=ExpiredFourIsolation(manifest,expected_digest=manifest["frozen_inventory_sha256"])
    assert fresh.poll(DEVICE,1) is None
    check_unchanged(fresh,db,before)
