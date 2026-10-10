"""Only synthetic signed snapshots and local temp-file rollback (zero science)."""
from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from remotemcp.routing.crypto import canonical_json
from tools.v31_node_release_qualifier import evaluate_private_signed_pair

NOW = 1_800_000_000_000


def panel():
    key=Ed25519PrivateKey.generate()
    pub=base64.b64encode(key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode()
    expected={
        "device_id":"dev_"+("a"*32),
        "route_generation":1,
        "device_key_fingerprint_sha256":"b"*64,
        "gateway_release_commit":"c"*40,
        "node_release_commit":"d"*40,
        "frozen_inventory_sha256":"e"*64,
    }
    xs=[]
    for t in (NOW-50_000,NOW-10_000):
        row={
            **expected,
            "observed_at_ms":t,
            "process_epoch":"same-epoch",
            "node_online":True,
            "capacity_signal_fresh":True,
            "physical_process_safety_resolved":True,
            "physical_process_singleton":1,
            "active_node_jobs":0,
            "unresolved_node_jobs":0,
            "unexpired_commands":0,
            "protected_command_replay_count":0,
        }
        xs.append({"payload":row,"signature_b64":base64.b64encode(
            key.sign(canonical_json(row).encode())).decode()})
    evidence={
        "schema":"remotemcp.v31.private-node-release-preflight.v1",
        "expected":expected,"trusted_node_public_key_b64":pub,
        "snapshots":xs,
    }
    return key,evidence


def resigned(key,evidence,index):
    row=evidence["snapshots"][index]["payload"]
    evidence["snapshots"][index]["signature_b64"]=base64.b64encode(
        key.sign(canonical_json(row).encode())).decode()


def test_signed_pair_is_eligible_for_review_but_never_deploy_permission():
    _,evidence=panel()
    verdict=evaluate_private_signed_pair(
        evidence,now_ms=NOW,
        pinned_trust_anchor_b64=evidence["trusted_node_public_key_b64"],
    )
    assert verdict["verdict"]=="SYNTHETIC_SUMMARY_VALID_ONLY"
    assert verdict["actual_rmcpnode1_signature_verified"] is False
    assert verdict["signed_snapshots_verified"]==2
    assert verdict["operator_authorized"] is False
    assert verdict["production_deployment_permitted"] is False
    assert verdict["live_six_pass"] is False


@pytest.mark.parametrize("case",[
    "signature_tamper","too_close","stale","wrong_release",
    "unexpired_command","physical_job","unresolved_job",
    "replay","process_changed","changed_fingerprint","no_trust",
    "not_singleton","not_fresh","anchor_mismatch","bad_timestamp","bad_route",
])
def test_fail_closed_panel_synthetic_cases(case):
    key,evidence=panel()
    x=evidence["snapshots"][1]["payload"]
    if case=="signature_tamper":
        x["active_node_jobs"]=1
    elif case=="too_close":
        x["observed_at_ms"]=evidence["snapshots"][0]["payload"]["observed_at_ms"]+20_000
    elif case=="stale":
        x["observed_at_ms"]=NOW-120_000
    elif case=="wrong_release":
        x["node_release_commit"]="f"*40
    elif case=="unexpired_command":
        x["unexpired_commands"]=1
    elif case=="physical_job":
        x["active_node_jobs"]=1
    elif case=="unresolved_job":
        x["unresolved_node_jobs"]=1
    elif case=="replay":
        x["protected_command_replay_count"]=1
    elif case=="process_changed":
        x["process_epoch"]="restart-without-proof"
    elif case=="changed_fingerprint":
        x["device_key_fingerprint_sha256"]="f"*64
    elif case=="no_trust":
        evidence.pop("trusted_node_public_key_b64")
    elif case=="anchor_mismatch":
        evidence["trusted_node_public_key_b64"]=base64.b64encode(b"z"*32).decode()
    elif case=="not_singleton":
        x["physical_process_singleton"]=2
    elif case=="not_fresh":
        x["capacity_signal_fresh"]=False
    elif case=="bad_timestamp":
        x["observed_at_ms"]="2026-not-a-timestamp"
    elif case=="bad_route":
        x["route_generation"]="1"
    if case not in ("signature_tamper","no_trust"):
        resigned(key,evidence,1)
    anchor=base64.b64encode(key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode()
    result=evaluate_private_signed_pair(
        evidence,now_ms=NOW,
        pinned_trust_anchor_b64=(None if case=="no_trust" else anchor),
    )
    assert result["verdict"]=="HOLD"
    assert result["operator_authorized"] is False


def test_simulated_rollback_restores_source_and_ledger_exactly(tmp_path):
    """No real watchdog, process or gateway: tempdir-backed rollback fixture."""
    baseline=tmp_path/"baseline"
    stage=tmp_path/"candidate"
    live=tmp_path/"live"
    baseline.mkdir();stage.mkdir();live.mkdir()
    old=(b"v31-prod-old-release",b"state-preserved")
    new=b"v31-new-candidate"
    (baseline/"binary.bin").write_bytes(old[0])
    (stage/"binary.bin").write_bytes(new)
    (live/"binary.bin").write_bytes(old[0])
    (live/"ledger.db").write_bytes(old[1])
    old_digest=hashlib.sha256((live/"binary.bin").read_bytes()).hexdigest()
    ledger_digest=hashlib.sha256((live/"ledger.db").read_bytes()).hexdigest()
    # A temporary atomic staging and rollback proof; not an operational script.
    temp=live/"binary.new"
    temp.write_bytes((stage/"binary.bin").read_bytes())
    temp.replace(live/"binary.bin")
    assert (live/"binary.bin").read_bytes()==new
    rollback=live/"binary.rollback"
    rollback.write_bytes((baseline/"binary.bin").read_bytes())
    rollback.replace(live/"binary.bin")
    assert hashlib.sha256((live/"binary.bin").read_bytes()).hexdigest()==old_digest
    assert hashlib.sha256((live/"ledger.db").read_bytes()).hexdigest()==ledger_digest



def test_sqlite_consistent_checkpoint_rollback_keeps_19_rows_and_release_pin(tmp_path):
    """Windows/Linux disk-backed SQLite backup; no production process/files."""
    import sqlite3

    live=tmp_path/"synthetic-live"
    saved=tmp_path/"rollback-checkpoint"
    stage=tmp_path/"candidate"
    for d in (live,saved,stage):
        d.mkdir()
    db_path=live/"gateway.db"
    con=sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE device_commands(command_id TEXT PRIMARY KEY,state TEXT,"
        "delivery_attempt INTEGER,request_hash TEXT)"
    )
    frozen=[
        (f"cmd_{i:032x}","LEASED",1,f"{i:064x}") for i in range(19)
    ]
    con.executemany("INSERT INTO device_commands VALUES(?,?,?,?)",frozen)
    con.commit()
    backup=sqlite3.connect(saved/"gateway.db")
    con.backup(backup)
    assert backup.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    backup.close()
    con.close()

    original_release=b"gateway-production-old"
    new_release=b"gateway-candidate-dryrun"
    original_config={
        "node_release_gate_enabled":True,
        "required_node_release_commit_sha":"a"*40,
        "hold_science_job_dispatch":True,
        "historical_isolation_gate_enabled":True,
        "historical_isolation_manifest_sha256":"b"*64,
    }
    (live/"release.bin").write_bytes(original_release)
    (stage/"release.bin").write_bytes(new_release)
    (live/"gateway-config.json").write_text(
        json.dumps(original_config,sort_keys=True),encoding="utf-8"
    )
    (saved/"release.bin").write_bytes(original_release)
    (saved/"gateway-config.json").write_bytes(
        (live/"gateway-config.json").read_bytes()
    )
    snap=lambda p: [
        list(row) for row in sqlite3.connect(p).execute(
            "SELECT * FROM device_commands ORDER BY command_id"
        ).fetchall()
    ]
    before=snap(saved/"gateway.db")
    assert len(before)==19 and before==snap(db_path)
    # Dry-run an upgrade and injected rejection before the pin can be altered.
    (live/"release.bin").write_bytes((stage/"release.bin").read_bytes())
    try:
        raise RuntimeError("synthetic post-cutover attestation failure")
    except RuntimeError:
        (live/"release.bin").write_bytes((saved/"release.bin").read_bytes())
        (live/"gateway-config.json").write_bytes(
            (saved/"gateway-config.json").read_bytes()
        )
    assert (live/"release.bin").read_bytes()==original_release
    assert (live/"gateway-config.json").read_bytes()==(
        saved/"gateway-config.json"
    ).read_bytes()
    assert snap(db_path)==before
    assert original_config["hold_science_job_dispatch"] is True
    assert original_config["historical_isolation_gate_enabled"] is True
    for path in (db_path,saved/"gateway.db"):
        x=sqlite3.connect(path)
        assert x.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        x.close()
