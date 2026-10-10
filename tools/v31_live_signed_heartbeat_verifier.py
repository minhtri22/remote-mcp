"""Offline-only verifier of real RemoteMCP node HTTP heartbeat signatures.

Uses RMCPNODE1 canonical_node over the *original* POST body bytes, not a
synthetic signature over a JSON summary. This tool cannot capture new node
requests or authorize production changes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from remotemcp.durable.errors import DurableError
from remotemcp.routing.crypto import b64u_decode, canonical_node, verify_ed25519


SCHEMA="remotemcp.v31.raw-signed-node-heartbeat-pair.v1"
HEX40=re.compile(r"^[0-9a-f]{40}$")
HEX64=re.compile(r"^[0-9a-f]{64}$")
REQUIRED_HEADERS=(
    "X-RMCP-Device","X-RMCP-Route-Generation",
    "X-RMCP-Timestamp","X-RMCP-Nonce","X-RMCP-Signature",
)


def evaluate_raw_signed_heartbeat_pair(
    evidence: dict[str,Any],*,pinned_node_public_key_b64: str | None,
    now_ms: int,
)->dict[str,Any]:
    """Verify node proof only; release requires separately signed gateway proof."""
    reasons=[]
    def hold(reason):
        reasons.append(reason)

    if not isinstance(evidence,dict) or evidence.get("schema")!=SCHEMA:
        return {"node_signed_pair":"HOLD","release_gate":"HOLD",
                "blockers":["EVIDENCE_SCHEMA_INVALID"],"operator_authorized":False}
    expected=evidence.get("expected")
    requests=evidence.get("signed_heartbeat_requests")
    if not isinstance(expected,dict) or not isinstance(requests,list) or len(requests)!=2:
        return {"node_signed_pair":"HOLD","release_gate":"HOLD",
                "blockers":["RAW_SIGNED_PAIR_MISSING"],"operator_authorized":False}
    if not isinstance(pinned_node_public_key_b64,str):
        return {"node_signed_pair":"HOLD","release_gate":"HOLD",
                "blockers":["EXTERNAL_NODE_PUBLIC_KEY_REQUIRED"],"operator_authorized":False}
    try:
        raw_public_key=b64u_decode(pinned_node_public_key_b64)
        if len(raw_public_key)!=32: raise ValueError("wrong key length")
    except (DurableError,ValueError,TypeError):
        return {"node_signed_pair":"HOLD","release_gate":"HOLD",
                "blockers":["EXTERNAL_NODE_PUBLIC_KEY_INVALID"],"operator_authorized":False}
    if hashlib.sha256(raw_public_key).hexdigest()!=expected.get("device_key_fingerprint_sha256"):
        hold("NODE_PUBLIC_KEY_FINGERPRINT_MISMATCH")
    device=expected.get("device_id")
    generation=expected.get("route_generation")
    release=expected.get("node_release_commit")
    if not isinstance(device,str) or not re.fullmatch(r"dev_[0-9a-f]{32}",device):
        hold("PINNED_DEVICE_INVALID")
    if type(generation) is not int or generation<1:
        hold("PINNED_ROUTE_INVALID")
    if not isinstance(release,str) or not HEX40.fullmatch(release):
        hold("PINNED_RELEASE_INVALID")

    checked=[]
    for index,item in enumerate(requests):
        if not isinstance(item,dict):
            hold(f"RAW_{index}_INVALID")
            continue
        h=item.get("headers")
        if not isinstance(h,dict) or any(k not in h for k in REQUIRED_HEADERS):
            hold(f"RAW_{index}_SIGNED_HEADERS_MISSING")
            continue
        if item.get("method")!="POST" or item.get("path")!="/device/v1/heartbeat":
            hold(f"RAW_{index}_WRONG_SIGNED_ROUTE")
            continue
        try:
            body=base64.b64decode(item["body_b64"],validate=True)
            ts=int(h["X-RMCP-Timestamp"])
            gen=int(h["X-RMCP-Route-Generation"])
            nonce=str(h["X-RMCP-Nonce"])
            if len(b64u_decode(nonce))<16:
                raise ValueError("nonce entropy")
            if type(now_ms) is not int or ts>now_ms+5000 or now_ms-ts>90000:
                hold(f"RAW_{index}_SIGNATURE_TIMESTAMP_NOT_FRESH")
            if h["X-RMCP-Device"]!=device or gen!=generation:
                hold(f"RAW_{index}_SIGNED_ROUTE_IDENTITY_MISMATCH")
            verify_ed25519(
                pinned_node_public_key_b64,h["X-RMCP-Signature"],
                canonical_node(h["X-RMCP-Device"],gen,"POST",
                               "/device/v1/heartbeat",ts,nonce,body),
            )
            payload=json.loads(body.decode("utf-8"))
            if not isinstance(payload,dict):
                raise ValueError("heartbeat body must be object")
        except (KeyError,ValueError,TypeError,UnicodeError,DurableError) as exc:
            hold(f"RAW_{index}_SIGNATURE_OR_PAYLOAD_INVALID")
            continue
        att=payload.get("node_attestation")
        if not isinstance(att,dict):
            hold(f"RAW_{index}_NODE_ATTESTATION_MISSING")
            continue
        if att.get("release_commit")!=release:
            hold(f"RAW_{index}_NODE_RELEASE_MISMATCH")
        if type(payload.get("active_node_jobs")) is not int or payload["active_node_jobs"]!=0:
            hold(f"RAW_{index}_ACTIVE_NODE_JOBS_NOT_ZERO")
        if type(payload.get("active_node_jobs_unresolved")) is not int or payload["active_node_jobs_unresolved"]!=0:
            hold(f"RAW_{index}_UNRESOLVED_NODE_JOBS_NOT_ZERO")
        if payload.get("capacity_reconciliation_complete") is not True:
            hold(f"RAW_{index}_CAPACITY_UNRECONCILED")
        for name,wanted in (
            ("physical_process_safety_resolved",1),
            ("physical_process_blocker_count",0),
            ("physical_process_residual_job_count",0),
            ("physical_process_active_job_tree_count",0),
        ):
            if type(att.get(name)) is not int or att[name]!=wanted:
                hold(f"RAW_{index}_{name.upper()}_UNSAFE")
        checked.append({"timestamp_ms":ts,"nonce":nonce})

    if len(checked)==2:
        if checked[1]["timestamp_ms"]-checked[0]["timestamp_ms"]<30000:
            hold("SIGNED_PAIR_INTERVAL_LT_30S")
        if checked[0]["nonce"]==checked[1]["nonce"]:
            hold("SIGNATURE_NONCE_REUSED")

    proof_valid=not reasons and len(checked)==2
    # Gateway-only fields cannot be honestly proven from node-signed payload:
    # unexpired central command count, singleton/PID, gateway pin, ledger hash.
    if proof_valid:
        hold("GATEWAY_SIGNED_UNEXPIRED_COMMAND_COUNT_MISSING")
        hold("GATEWAY_SINGLETON_AND_PIN_PROOF_MISSING")
        hold("HISTORICAL_19_ROW_NONMUTATION_PROOF_MISSING")
        hold("OPERATOR_APPROVAL_NOT_GIVEN")
    return {
        "node_signed_pair":"PASS" if proof_valid else "HOLD",
        "release_gate":"HOLD",
        "verified_heartbeats":len(checked),
        "blockers":sorted(set(reasons)),
        "operator_authorized":False,
        "production_deployment_permitted":False,
    }
