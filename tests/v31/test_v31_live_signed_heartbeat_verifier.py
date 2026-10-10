"""Real RMCPNODE1 protocol signature fixture, not signed summary JSON."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from remotemcp.routing.crypto import b64u,canonical_node
from tools.v31_live_signed_heartbeat_verifier import evaluate_raw_signed_heartbeat_pair

NOW=1800000000000
DEVICE="dev_"+"a"*32
RELEASE="d"*40

def sample():
    key=Ed25519PrivateKey.generate()
    pub=key.public_key().public_bytes_raw()
    pk=b64u(pub)
    packets=[]
    for index,ts in enumerate((NOW-55000,NOW-12000)):
        payload={
            "node_time_ms":ts,
            "active_node_jobs":0,"active_node_jobs_unresolved":0,
            "capacity_reconciliation_complete":True,
            "node_attestation":{
                "release_commit":RELEASE,
                "physical_process_safety_resolved":1,
                "physical_process_blocker_count":0,
                "physical_process_residual_job_count":0,
                "physical_process_active_job_tree_count":0,
            },
        }
        body=json.dumps(payload,sort_keys=True,separators=(",",":")).encode()
        nonce=b64u(bytes([index+1])*18)
        signed=canonical_node(DEVICE,1,"POST","/device/v1/heartbeat",ts,nonce,body)
        packets.append({
            "method":"POST","path":"/device/v1/heartbeat",
            "headers":{
                "X-RMCP-Device":DEVICE,
                "X-RMCP-Route-Generation":"1",
                "X-RMCP-Timestamp":str(ts),
                "X-RMCP-Nonce":nonce,
                "X-RMCP-Signature":b64u(key.sign(signed)),
            },
            "body_b64":base64.b64encode(body).decode(),
        })
    return pk,{
        "schema":"remotemcp.v31.raw-signed-node-heartbeat-pair.v1",
        "expected":{
            "device_id":DEVICE,"route_generation":1,
            "node_release_commit":RELEASE,
            "device_key_fingerprint_sha256":hashlib.sha256(pub).hexdigest(),
        },
        "signed_heartbeat_requests":packets,
    }


def test_true_node_protocol_signatures_verify_but_release_stays_hold():
    pk,evidence=sample()
    r=evaluate_raw_signed_heartbeat_pair(
        evidence,pinned_node_public_key_b64=pk,now_ms=NOW,
    )
    assert r["node_signed_pair"]=="PASS"
    assert r["verified_heartbeats"]==2
    assert r["release_gate"]=="HOLD"
    assert r["operator_authorized"] is False
    assert r["production_deployment_permitted"] is False
    assert "GATEWAY_SIGNED_UNEXPIRED_COMMAND_COUNT_MISSING" in r["blockers"]


@pytest.mark.parametrize("attack",[
    "missing_external_key","self_signed_replacement","changed_body",
    "changed_route","wrong_release","not_fresh","too_close","same_nonce",
    "job_not_zero","physical_blocker","bad_path","missing_headers",
])
def test_node_protocol_fail_closed(attack):
    pk,e=sample()
    first,second=e["signed_heartbeat_requests"]
    if attack=="missing_external_key":
        pk=None
    elif attack=="self_signed_replacement":
        attacker=Ed25519PrivateKey.generate()
        e["expected"]["device_key_fingerprint_sha256"]=hashlib.sha256(
            attacker.public_key().public_bytes_raw()
        ).hexdigest()
        pk=b64u(attacker.public_key().public_bytes_raw())
    elif attack=="changed_body":
        body=json.loads(base64.b64decode(second["body_b64"]))
        body["active_node_jobs"]=1
        second["body_b64"]=base64.b64encode(json.dumps(body).encode()).decode()
    elif attack=="changed_route":
        second["headers"]["X-RMCP-Route-Generation"]="2"
    elif attack=="wrong_release":
        e["expected"]["node_release_commit"]="f"*40
    elif attack=="not_fresh":
        second["headers"]["X-RMCP-Timestamp"]=str(NOW-120000)
    elif attack=="too_close":
        second["headers"]["X-RMCP-Timestamp"]=str(NOW-45000)
    elif attack=="same_nonce":
        second["headers"]["X-RMCP-Nonce"]=first["headers"]["X-RMCP-Nonce"]
    elif attack=="job_not_zero":
        body=json.loads(base64.b64decode(second["body_b64"]))
        body["active_node_jobs"]=1
        second["body_b64"]=base64.b64encode(json.dumps(body).encode()).decode()
    elif attack=="physical_blocker":
        body=json.loads(base64.b64decode(second["body_b64"]))
        body["node_attestation"]["physical_process_blocker_count"]=2
        second["body_b64"]=base64.b64encode(json.dumps(body).encode()).decode()
    elif attack=="bad_path":
        second["path"]="/device/v1/poll"
    elif attack=="missing_headers":
        second["headers"].pop("X-RMCP-Signature")
    verdict=evaluate_raw_signed_heartbeat_pair(
        e,pinned_node_public_key_b64=pk,now_ms=NOW,
    )
    assert verdict["node_signed_pair"]=="HOLD"
    assert verdict["release_gate"]=="HOLD"


def test_signed_semantics_missing_gateway_evidence_cannot_authorize_anything():
    pk,e=sample()
    for field in ("gateway_release_commit","unexpired_commands",
                  "operator_approval","historical_command_reconciliation"):
        e[field]="FAKE_APPROVED"
    r=evaluate_raw_signed_heartbeat_pair(
        e,pinned_node_public_key_b64=pk,now_ms=NOW,
    )
    assert r["node_signed_pair"]=="PASS"
    assert r["release_gate"]=="HOLD"
    assert r["production_deployment_permitted"] is False
