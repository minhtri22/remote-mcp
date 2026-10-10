"""Operator-authorized PRIVATE heartbeat evidence verification; zero capture side effects.

A separate trusted operator may permit ONLY passively observed, already
authenticated heartbeat packets. This module never installs a hook, listens
on a network, reads runtime credentials, or modifies production.
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from remotemcp.routing.crypto import b64u_decode, canonical_json
from tools.v31_live_signed_heartbeat_verifier import evaluate_raw_signed_heartbeat_pair

AUTH_SCOPE = "remotemcp.v31.read-only-gateway-authenticated-heartbeat.v1"
CAPTURE_CHANNEL = "POST_AUTH_GATEWAY_OBSERVATION_ONLY"
EXPECTED_FIELDS = frozenset((
    "scope", "channel", "device_id", "route_generation",
    "node_release_commit", "gateway_release_commit",
    "node_public_key_sha256", "issued_at_ms", "expires_at_ms",
    "max_packets", "production_mutations_authorized",
    "production_deployment_authorized",
))
PIN_FIELDS = ("device_id", "route_generation", "node_release_commit",
              "gateway_release_commit", "node_public_key_sha256")
HEX40 = re.compile(r"^[a-f0-9]{40}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")


def inspect_authorized_capture(
    permit: dict[str, Any] | None,
    private_packets: dict[str, Any] | None,
    *,
    operator_pubkey_b64: str | None,
    node_pubkey_b64: str | None,
    external_pins: dict[str, Any] | None,
    now_ms: int,
) -> dict[str, Any]:
    """Fail closed; permits packet verification, NEVER release or packet collection."""
    failures: list[str] = []
    def result(*, packet_result=None):
        return {
            "capture_permit": "PASS" if not failures else "HOLD",
            "raw_pair_verified": (
                packet_result is not None and
                packet_result.get("node_signed_pair") == "PASS"
            ),
            "release_gate": "HOLD",
            "blockers": sorted(set(failures)),
            "authorized_to_install_capture": False,
            "authorized_to_change_production": False,
            "operator_upgrade_authorized": False,
            "production_deployment_permitted": False,
        }

    if not isinstance(permit, dict) or not isinstance(external_pins, dict):
        failures.append("PERMIT_OR_INDEPENDENT_PINS_MISSING")
        return result()
    payload = permit.get("payload")
    if not isinstance(payload, dict) or frozenset(payload) != EXPECTED_FIELDS:
        failures.append("PERMIT_SCHEMA_INVALID")
        return result()
    if any(payload.get(k) != external_pins.get(k) for k in PIN_FIELDS):
        failures.append("INDEPENDENT_PIN_MISMATCH")
    if not (isinstance(payload["device_id"], str) and
            re.fullmatch(r"dev_[0-9a-f]{32}", payload["device_id"])):
        failures.append("DEVICE_ID_INVALID")
    if type(payload["route_generation"]) is not int or payload["route_generation"] < 1:
        failures.append("ROUTE_GENERATION_INVALID")
    if not all(isinstance(payload[k], str) and HEX40.fullmatch(payload[k])
               for k in ("gateway_release_commit", "node_release_commit")):
        failures.append("RELEASE_SHA_INVALID")
    if not (isinstance(payload["node_public_key_sha256"], str) and
            HEX64.fullmatch(payload["node_public_key_sha256"])):
        failures.append("NODE_TRUST_PIN_INVALID")
    if (payload["scope"] != AUTH_SCOPE or
            payload["channel"] != CAPTURE_CHANNEL or
            payload["max_packets"] != 2 or
            type(payload["max_packets"]) is not int or
            payload["production_mutations_authorized"] is not False or
            payload["production_deployment_authorized"] is not False):
        failures.append("CAPTURE_SCOPE_BROADENED")
    start, end = payload["issued_at_ms"], payload["expires_at_ms"]
    if (type(now_ms) is not int or type(start) is not int or
            type(end) is not int or start > now_ms or end < now_ms or
            end - start > 600_000 or end <= start):
        failures.append("PERMIT_NOT_FRESH_OR_TOO_BROAD")
    try:
        key_raw = b64u_decode(operator_pubkey_b64)
        if len(key_raw) != 32:
            raise ValueError("operator public key length")
        Ed25519PublicKey.from_public_bytes(key_raw).verify(
            base64.b64decode(permit["signature_b64"], validate=True),
            canonical_json(payload).encode("utf-8"),
        )
    except Exception:
        failures.append("EXTERNAL_OPERATOR_SIGNATURE_NOT_VERIFIED")
    try:
        node_raw = b64u_decode(node_pubkey_b64)
        if len(node_raw) != 32:
            raise ValueError("node public key length")
        if hashlib.sha256(node_raw).hexdigest() != payload["node_public_key_sha256"]:
            failures.append("EXTERNAL_NODE_TRUST_ANCHOR_MISMATCH")
    except Exception:
        failures.append("EXTERNAL_NODE_TRUST_ANCHOR_INVALID")
    if failures:
        return result()
    if not isinstance(private_packets, dict):
        failures.append("RAW_SIGNED_PACKETS_NOT_CAPTURED")
        return result()

    expected = private_packets.get("expected")
    if not isinstance(expected, dict) or any(
        expected.get(k) != payload.get(k) for k in (
            "device_id", "route_generation", "node_release_commit",
        )
    ) or expected.get("device_key_fingerprint_sha256") != payload["node_public_key_sha256"]:
        failures.append("RAW_PACKETS_NOT_BOUND_TO_PERMIT")
        return result()

    raw = private_packets.get("signed_heartbeat_requests")
    if not isinstance(raw, list) or len(raw) != 2:
        failures.append("EXACTLY_TWO_RAW_PACKETS_REQUIRED")
        return result()
    try:
        for item in raw:
            if item.get("method") != "POST" or item.get("path") != "/device/v1/heartbeat":
                failures.append("UNAUTHORIZED_ENDPOINT")
                return result()
            data = base64.b64decode(item["body_b64"], validate=True)
            if len(data) > 65536:
                failures.append("CAPTURE_BODY_OVERSIZED")
                return result()
    except Exception:
        failures.append("CAPTURE_INPUT_INVALID")
        return result()
    check = evaluate_raw_signed_heartbeat_pair(
        private_packets, pinned_node_public_key_b64=node_pubkey_b64,
        now_ms=now_ms,
    )
    if check["node_signed_pair"] != "PASS":
        failures.append("RAW_NODE_PROTOCOL_SIGNATURE_INVALID")
    # Even a genuine permitted pair cannot certify gateway state, watchdog,
    # protected four-command immutability or an upgrade decision.
    return result(packet_result=check)
