from __future__ import annotations
import pytest
from conftest import pair_node
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms

def test_online_offline_recover_revoke(make_gateway,tmp_path):
    import asyncio
    b=make_gateway();node,dev=asyncio.run(pair_node(b,tmp_path/"node",tmp_path/"node-rt")); did=node.identity.device["device_id"]
    status=b.routing.device_status(did)
    assert status["state"]=="ONLINE"
    assert status["hostname"]=="node-a-host"
    assert status["platform"]["system"]=="Windows"
    with b.durable.db.transaction() as con:
        con.execute("update devices set last_seen_at_ms=? where device_id=?",(now_ms()-61000,did))
    b.routing.devices.sweep_offline()
    assert b.routing.device_status(did)["state"]=="OFFLINE"
    with b.durable.db.transaction() as con:
        b.routing.devices.touch_online_in_tx(con,did,now_ms())
    assert b.routing.device_status(did)["state"]=="ONLINE"
    old=b.routing.devices.get(did)["route_generation"]
    b.routing.devices.revoke(did,"test")
    row=b.routing.devices.get(did)
    assert row["state"]=="REVOKED" and row["route_generation"]==old+1
    with pytest.raises(DurableError) as exc:b.routing.devices.require_online(did)
    assert exc.value.code=="DEVICE_REVOKED"


def test_signed_heartbeat_is_authoritative_capacity_signal(make_gateway,tmp_path):
    import asyncio
    b=make_gateway()
    node,dev=asyncio.run(pair_node(
        b,tmp_path/"capacity-node",tmp_path/"capacity-node-rt","capacity-node"
    ))
    did=node.identity.device["device_id"]
    row=b.routing.devices.get(did)
    b.routing.heartbeat_http(
        row,
        {
            "node_time_ms":now_ms(),
            "capabilities":{"outbound_node":True},
            "platform":{"hostname":"node-a-host","system":"Windows"},
            "active_node_jobs":3,
        },
    )
    status=b.routing.device_status(did)
    assert status["authoritative_active_node_jobs"]==3
    assert status["capacity_signal_fresh"] is True
    assert status["capacity_signal_source"]=="SIGNED_NODE_HEARTBEAT"
    assert status["active_routed_jobs_is_capacity_signal"] is False

    capacity=b.routing.device_capacity_status(did)
    assert capacity["capacity_resolved"] is True
    assert capacity["authoritative_active_node_jobs"]==3
    assert capacity["registry_count_is_capacity_signal"] is False
    assert capacity["resource_gate_rule"]=="USE_AUTHORITATIVE_NODE_COUNT_ONLY"
    assert "RECHECK_CPU_RAM" in capacity["recommendation"]
