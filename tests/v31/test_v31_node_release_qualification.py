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
