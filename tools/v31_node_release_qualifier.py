"""Offline-only V3.1 signed preflight adjudicator. Never issues node commands.

This verifies a private pair of raw, operator-trusted signed snapshots. Test
fixtures can exercise the verifier but never establish live authorization.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from remotemcp.routing.crypto import canonical_json


REQUIRED = (
    "device_id", "route_generation", "device_key_fingerprint_sha256",
    "gateway_release_commit", "node_release_commit",
    "process_epoch", "node_online", "capacity_signal_fresh",
    "physical_process_safety_resolved", "physical_process_singleton",
    "active_node_jobs", "unresolved_node_jobs", "unexpired_commands",
    "frozen_inventory_sha256", "protected_command_replay_count",
    "observed_at_ms",
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def evaluate_private_signed_pair(evidence: dict, *, now_ms: int,
                                 pinned_trust_anchor_b64: str | None = None) -> dict:
    """Fail closed; PASS means *eligible for operator review* only."""
    blockers: list[str] = []

    if evidence.get("schema") != "remotemcp.v31.private-node-release-preflight.v1":
        return {"verdict": "HOLD", "blockers": ["SCHEMA_INVALID"],
                "operator_authorized": False}
    expected = evidence.get("expected")
    snapshots = evidence.get("snapshots")
    # The trust anchor must be supplied independently of the evidence.
    # An attacker must not self-authorize by embedding their own public key.
    key_b64 = pinned_trust_anchor_b64
    if not isinstance(expected, dict) or not isinstance(snapshots, list) or len(snapshots) != 2:
        return {"verdict": "HOLD", "blockers": ["EVIDENCE_INCOMPLETE"],
                "operator_authorized": False}
    if not isinstance(key_b64, str):
        return {"verdict": "HOLD", "blockers": ["EXTERNAL_TRUST_ANCHOR_REQUIRED"],
                "operator_authorized": False}
    if evidence.get("trusted_node_public_key_b64") not in (None,key_b64):
        return {"verdict": "HOLD", "blockers": ["TRUST_ANCHOR_MISMATCH"],
                "operator_authorized": False}
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except (ValueError, TypeError):
        return {"verdict": "HOLD", "blockers": ["TRUST_ANCHOR_INVALID"],
                "operator_authorized": False}
    if not all(HEX40.fullmatch(str(expected.get(k,""))) for k in (
        "gateway_release_commit","node_release_commit"
    )):
        blockers.append("RELEASE_SOURCE_PIN_INVALID")
    if not all(HEX64.fullmatch(str(expected.get(k,""))) for k in (
        "device_key_fingerprint_sha256","frozen_inventory_sha256"
    )):
        blockers.append("IDENTITY_PIN_INVALID")
    signed: list[dict] = []
    for index, item in enumerate(snapshots):
        if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
            blockers.append(f"SNAPSHOT_{index}_PAYLOAD_INVALID")
            continue
        payload = item["payload"]
        if any(k not in payload for k in REQUIRED):
            blockers.append(f"SNAPSHOT_{index}_FIELDS_MISSING")
            continue
        try:
            sig = base64.b64decode(item["signature_b64"], validate=True)
            key.verify(sig, canonical_json(payload).encode("utf-8"))
        except Exception:
            blockers.append(f"SNAPSHOT_{index}_SIGNATURE_INVALID")
            continue
        signed.append(payload)
        for k in ("device_id","route_generation","device_key_fingerprint_sha256",
                  "gateway_release_commit","node_release_commit","frozen_inventory_sha256"):
            if payload.get(k) != expected.get(k):
                blockers.append(f"SNAPSHOT_{index}_{k.upper()}_MISMATCH")
        if type(payload["observed_at_ms"]) is not int:
            blockers.append(f"SNAPSHOT_{index}_TIMESTAMP_INVALID")
        elif payload["observed_at_ms"] > now_ms + 5_000 or now_ms - payload["observed_at_ms"] > 90_000:
            blockers.append(f"SNAPSHOT_{index}_NOT_FRESH")
        for k in ("node_online","capacity_signal_fresh","physical_process_safety_resolved"):
            if payload[k] is not True:
                blockers.append(f"SNAPSHOT_{index}_{k.upper()}_NOT_TRUE")
        for k,v in (
            ("physical_process_singleton",1),
            ("active_node_jobs",0),
            ("unresolved_node_jobs",0),
            ("unexpired_commands",0),
            ("protected_command_replay_count",0),
        ):
            if type(payload[k]) is not int or payload[k]!=v:
                blockers.append(f"SNAPSHOT_{index}_{k.upper()}_UNSAFE")
        if not isinstance(payload["process_epoch"],str) or not payload["process_epoch"]:
            blockers.append(f"SNAPSHOT_{index}_PROCESS_EPOCH_INVALID")
    if len(signed)==2:
        left,right=signed
        if right["observed_at_ms"]-left["observed_at_ms"] < 30_000:
            blockers.append("SIGNED_SNAPSHOT_INTERVAL_LT_30S")
        if left["process_epoch"] != right["process_epoch"]:
            blockers.append("PROCESS_EPOCH_CHANGED")
        if left["device_id"] != right["device_id"]:
            blockers.append("DEVICE_CHANGED")
    return {
        "verdict": "REVIEW_ELIGIBLE" if not blockers else "HOLD",
        "blockers": sorted(set(blockers)),
        "signed_snapshots_verified": len(signed),
        "operator_authorized": False,
        "production_deployment_permitted": False,
        "live_six_pass": False,
    }


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: python -m tools.v31_node_release_qualifier "
              "PRIVATE_EVIDENCE.json TRUST_ANCHOR_B64_FILE INDEPENDENT_ANCHOR_FILE_SHA256",
              file=sys.stderr)
        return 2
    anchor_raw=Path(sys.argv[2]).read_bytes()
    claimed_digest=sys.argv[3]
    if not HEX64.fullmatch(claimed_digest) or hashlib.sha256(anchor_raw).hexdigest()!=claimed_digest:
        print(json.dumps({"verdict":"HOLD","blockers":["TRUST_ANCHOR_FILE_HASH_MISMATCH"],
                          "operator_authorized":False}))
        return 1
    raw=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result=evaluate_private_signed_pair(
        raw,now_ms=__import__("time").time_ns()//1_000_000,
        pinned_trust_anchor_b64=anchor_raw.decode("ascii").strip(),
    )
    print(json.dumps(result,sort_keys=True))
    return 0 if result["verdict"]=="REVIEW_ELIGIBLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
