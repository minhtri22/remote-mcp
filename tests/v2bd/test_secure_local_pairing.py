from __future__ import annotations

import inspect
import json

import pytest

from remotemcp.durable.errors import DurableError


def test_secure_local_dedicated_node_pairing_consumes_without_secret_argument(make_gateway,tmp_path):
    g=make_gateway()
    p=g.routing.pairing.begin("pair-begin-local","machine-local")
    node_root=tmp_path/"node-root"
    runtime=tmp_path/"node-runtime"

    sig=inspect.signature(g.routing.device_pair_local_dedicated_node)
    assert "pairing_code" not in sig.parameters

    out=g.routing.device_pair_local_dedicated_node(
        "pair-local-op",
        p["pairing_id"],
        "machine-local",
        str(node_root.resolve()),
        str(runtime.resolve()),
        True,
    )
    assert out["device_id"].startswith("dev_")
    assert out["secure_local_pairing"] is True
    assert out["pairing_secret_exposed"] is False
    assert out["node_process_started"] is False
    assert (runtime/"device.json").exists()
    assert (runtime/"device-ed25519.pem").exists()

    payload=json.dumps(out,sort_keys=True)
    assert p["pairing_code"] not in payload
    op=g.durable.operations.get("pair-local-op")
    assert p["pairing_code"] not in (op["result_json"] or "")
    assert p["pairing_code"] not in (op["error_json"] or "")

    used=g.routing.pairing.begin("pair-begin-local","machine-local")
    assert "pairing_code" not in used
    assert used["paired_device_id"]==out["device_id"]


def test_secure_local_pairing_requires_explicit_scope_ack(make_gateway,tmp_path):
    g=make_gateway()
    p=g.routing.pairing.begin("pair-begin-ack","machine-ack")
    with pytest.raises(DurableError) as exc:
        g.routing.device_pair_local_dedicated_node(
            "pair-local-ack",
            p["pairing_id"],
            "machine-ack",
            str((tmp_path/"root").resolve()),
            str((tmp_path/"runtime").resolve()),
            False,
        )
    assert exc.value.code=="DEDICATED_NODE_SCOPE_ACK_REQUIRED"


def test_secure_local_pairing_replay_preserves_identity(make_gateway,tmp_path):
    g=make_gateway()
    p=g.routing.pairing.begin("pair-begin-replay","machine-replay")
    root=(tmp_path/"root").resolve()
    runtime=(tmp_path/"runtime").resolve()
    first=g.routing.device_pair_local_dedicated_node(
        "pair-local-replay",
        p["pairing_id"],
        "machine-replay",
        str(root),
        str(runtime),
        True,
    )
    replay=g.routing.device_pair_local_dedicated_node(
        "pair-local-replay",
        p["pairing_id"],
        "machine-replay",
        str(root),
        str(runtime),
        True,
    )
    assert replay["device_id"]==first["device_id"]
    assert replay["key_fingerprint_sha256"]==first["key_fingerprint_sha256"]
    assert replay["replayed"] is True


def test_secure_local_pairing_refuses_identity_replacement(make_gateway,tmp_path):
    g=make_gateway()
    first=g.routing.pairing.begin("pair-begin-one","machine-one")
    runtime=(tmp_path/"runtime").resolve()
    root=(tmp_path/"root").resolve()
    g.routing.device_pair_local_dedicated_node(
        "pair-local-one",
        first["pairing_id"],
        "machine-one",
        str(root),
        str(runtime),
        True,
    )

    second=g.routing.pairing.begin("pair-begin-two","machine-two")
    with pytest.raises(DurableError) as exc:
        g.routing.device_pair_local_dedicated_node(
            "pair-local-two",
            second["pairing_id"],
            "machine-two",
            str(root),
            str(runtime),
            True,
        )
    assert exc.value.code=="NODE_IDENTITY_MISMATCH"
