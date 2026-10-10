"""Zero-science independent proof: operator permit cannot deploy or create capture."""
import base64
import hashlib
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from remotemcp.routing.crypto import b64u,b64u_decode,canonical_json
from tools.v31_operator_capture_authorization import (
    AUTH_SCOPE,CAPTURE_CHANNEL,inspect_authorized_capture,
)
from test_v31_live_signed_heartbeat_verifier import sample,NOW


def fixture():
    _,node_pub,packets=sample()
    operator=Ed25519PrivateKey.generate()
    operator_pub=b64u(operator.public_key().public_bytes_raw())
    payload={
        "scope":AUTH_SCOPE,"channel":CAPTURE_CHANNEL,
        "device_id":packets["expected"]["device_id"],
        "route_generation":packets["expected"]["route_generation"],
        "node_release_commit":packets["expected"]["node_release_commit"],
        "gateway_release_commit":"e"*40,
        "node_public_key_sha256":hashlib.sha256(b64u_decode(node_pub)).hexdigest(),
        "issued_at_ms":NOW-15000,"expires_at_ms":NOW+120000,
        "max_packets":2,
        "production_mutations_authorized":False,
        "production_deployment_authorized":False,
    }
    permit={"payload":payload}
    signed(operator,permit)
    pins={k:payload[k] for k in (
        "device_id","route_generation","node_release_commit",
        "gateway_release_commit","node_public_key_sha256",
    )}
    return operator,operator_pub,node_pub,permit,packets,pins


def signed(operator,permit):
    permit["signature_b64"]=base64.b64encode(
        operator.sign(canonical_json(permit["payload"]).encode())
    ).decode()


def check(operator_pub,node_pub,permit,packets,pins):
    return inspect_authorized_capture(
        permit,packets,operator_pubkey_b64=operator_pub,
        node_pubkey_b64=node_pub,external_pins=pins,now_ms=NOW,
    )


def test_genuine_operator_permit_and_raw_node_packets_never_approve_production():
    _,operator_pub,node_pub,permit,packets,pins=fixture()
    result=check(operator_pub,node_pub,permit,packets,pins)
    assert result["capture_permit"]=="PASS"
    assert result["raw_pair_verified"] is True
    assert result["release_gate"]=="HOLD"
    assert result["authorized_to_install_capture"] is False
    assert result["authorized_to_change_production"] is False
    assert result["operator_upgrade_authorized"] is False
    assert result["production_deployment_permitted"] is False


@pytest.mark.parametrize("problem",[
    "no_permit","no_raw","no_external_operator","bad_operator_signature",
    "self_signed_operator","expired_permit","long_permit","wrong_scope",
    "wrong_channel","allow_production_mutation","allow_deploy","three_packets",
    "wrong_gateway_pin","wrong_device_pin","wrong_node_public_key","tampered_raw_body",
    "unauthorized_poll_endpoint","extra_permit_field",
])
def test_unauthorized_or_incomplete_capture_is_fail_closed(problem):
    operator,operator_pub,node_pub,permit,packets,pins=fixture()
    permit=deepcopy(permit)
    packets=deepcopy(packets)
    if problem=="no_permit": permit=None
    elif problem=="no_raw": packets=None
    elif problem=="no_external_operator": operator_pub=None
    elif problem=="bad_operator_signature": permit["signature_b64"]="AA=="
    elif problem=="self_signed_operator":
        other=Ed25519PrivateKey.generate()
        operator_pub=b64u(other.public_key().public_bytes_raw())
        signed(other,permit)
    elif problem=="expired_permit": permit["payload"]["expires_at_ms"]=NOW-1
    elif problem=="long_permit": permit["payload"]["expires_at_ms"]=NOW+1_000_000
    elif problem=="wrong_scope": permit["payload"]["scope"]="CAPTURE_ALL"
    elif problem=="wrong_channel": permit["payload"]["channel"]="MITM_UNAPPROVED"
    elif problem=="allow_production_mutation":
        permit["payload"]["production_mutations_authorized"]=True
    elif problem=="allow_deploy":
        permit["payload"]["production_deployment_authorized"]=True
    elif problem=="three_packets": permit["payload"]["max_packets"]=3
    elif problem=="wrong_gateway_pin":
        permit["payload"]["gateway_release_commit"]="f"*40
    elif problem=="wrong_device_pin":
        permit["payload"]["device_id"]="dev_"+"f"*32
    elif problem=="wrong_node_public_key":
        node_pub=b64u(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    elif problem=="tampered_raw_body":
        packets["signed_heartbeat_requests"][1]["body_b64"]=base64.b64encode(
            b'{"active_node_jobs":1}'
        ).decode()
    elif problem=="unauthorized_poll_endpoint":
        packets["signed_heartbeat_requests"][1]["path"]="/device/v1/poll"
    elif problem=="extra_permit_field":
        permit["payload"]["full_request_logging"]=True
    if problem in (
        "expired_permit","long_permit","wrong_scope","wrong_channel",
        "allow_production_mutation","allow_deploy","three_packets",
        "wrong_gateway_pin","wrong_device_pin","extra_permit_field",
    ):
        signed(operator,permit)
    r=check(operator_pub,node_pub,permit,packets,pins)
    assert (r["capture_permit"]!="PASS" or r["raw_pair_verified"] is False)
    assert r["release_gate"]=="HOLD"
    assert r["authorized_to_install_capture"] is False
    assert r["authorized_to_change_production"] is False
    assert r["production_deployment_permitted"] is False
