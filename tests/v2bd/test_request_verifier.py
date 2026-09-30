from __future__ import annotations
import json,pytest
from conftest import pair_node
from remotemcp.durable.errors import DurableError
from remotemcp.node.signing import signed_headers

def test_valid_signature_and_nonce_replay(make_gateway,tmp_path):
    import asyncio
    b=make_gateway();node,dev=asyncio.run(pair_node(b,tmp_path/"node1",tmp_path/"node1-rt"))
    body=json.dumps({"node_time_ms":1},sort_keys=True,separators=(",",":")).encode()
    h=signed_headers(node.identity,"POST","/device/v1/heartbeat",body)
    row=b.routing.verify_signed("POST","/device/v1/heartbeat",h,body)
    assert row["device_id"]==node.identity.device["device_id"]
    with pytest.raises(DurableError) as exc:b.routing.verify_signed("POST","/device/v1/heartbeat",h,body)
    assert exc.value.code=="DEVICE_REPLAY"

def test_stale_generation_and_bad_body_signature(make_gateway,tmp_path):
    import asyncio
    b=make_gateway();node,dev=asyncio.run(pair_node(b,tmp_path/"node2",tmp_path/"node2-rt"))
    body=b"{}"; h=signed_headers(node.identity,"POST","/device/v1/heartbeat",body)
    bad=dict(h);bad["X-RMCP-Route-Generation"]="999"
    with pytest.raises(DurableError) as exc:b.routing.verify_signed("POST","/device/v1/heartbeat",bad,body)
    assert exc.value.code=="DEVICE_ROUTE_GENERATION_MISMATCH"
    with pytest.raises(DurableError) as exc:b.routing.verify_signed("POST","/device/v1/heartbeat",h,b'{"x":1}')
    assert exc.value.code=="DEVICE_SIGNATURE_INVALID"
