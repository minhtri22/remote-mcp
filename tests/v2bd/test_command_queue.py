from __future__ import annotations
import pytest
from conftest import pair_node
from remotemcp.durable.errors import DurableError

def test_operation_step_idempotency_and_redelivery(make_gateway,tmp_path):
    import asyncio
    b=make_gateway();node,dev=asyncio.run(pair_node(b,tmp_path/"node",tmp_path/"node-rt")); d=node.identity.device
    b.durable.operations.reserve("op","TEST_ROUTED_COMMAND_STEPS",{"purpose":"command-queue-idempotency"},principal_key=b.routing.owner_account_id)
    a,created=b.routing.commands.create(d["device_id"],"PROJECT_PROBE",{"path":"."},operation_id="op",operation_step=0)
    same,created2=b.routing.commands.create(d["device_id"],"PROJECT_PROBE",{"path":"."},operation_id="op",operation_step=0)
    assert a["command_id"]==same["command_id"] and created and not created2
    b2,_=b.routing.commands.create(d["device_id"],"PROJECT_BIND",{"project_id":"p"},operation_id="op",operation_step=1)
    assert b2["command_id"]!=a["command_id"]
    with pytest.raises(DurableError) as exc:b.routing.commands.create(d["device_id"],"PROJECT_PROBE",{"path":"other"},operation_id="op",operation_step=0)
    assert exc.value.code=="COMMAND_CONFLICT"
    p=b.routing.commands.poll(d["device_id"],d["route_generation"])
    assert p["state"]=="LEASED"
    with b.durable.db.transaction() as con:
        con.execute("update device_commands set lease_expires_at_ms=0 where command_id=?",(p["command_id"],))
    p2=b.routing.commands.poll(d["device_id"],d["route_generation"])
    assert p2["command_id"]==p["command_id"] and p2["delivery_attempt"]==2
