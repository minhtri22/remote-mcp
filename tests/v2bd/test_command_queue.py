from __future__ import annotations
import pytest
from conftest import pair_node
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms

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



def test_expired_command_same_operation_requeues_same_command_id(make_gateway,tmp_path):
    import asyncio
    b=make_gateway()
    node,dev=asyncio.run(pair_node(b,tmp_path/"node-expire",tmp_path/"node-expire-rt"))
    d=node.identity.device
    b.durable.operations.reserve(
        "expire-op","TEST_ROUTED_COMMAND_REQUEUE",{"purpose":"expiry-requeue"},
        principal_key=b.routing.owner_account_id,
    )
    first,created=b.routing.commands.create(
        d["device_id"],"PROJECT_PROBE",{"path":"."},
        operation_id="expire-op",operation_step=0,
        expires_at_ms=now_ms()-1,
    )
    assert created
    assert b.routing.commands.poll(d["device_id"],d["route_generation"]) is None
    cancelled=b.routing.commands.get(first["command_id"])
    assert cancelled["state"]=="CANCELLED"
    assert cancelled["error_code"]=="DEVICE_COMMAND_EXPIRED"

    revived,created2=b.routing.commands.create(
        d["device_id"],"PROJECT_PROBE",{"path":"."},
        operation_id="expire-op",operation_step=0,
        expires_at_ms=now_ms()+120000,
    )
    assert created2 is True
    assert revived["command_id"]==first["command_id"]
    assert revived["state"]=="QUEUED"
    assert revived["error_code"] is None
    leased=b.routing.commands.poll(d["device_id"],d["route_generation"])
    assert leased["command_id"]==first["command_id"]
    assert leased["state"]=="LEASED"


def test_control_plane_commands_have_poll_priority(make_gateway,tmp_path):
    import asyncio
    b=make_gateway()
    node,dev=asyncio.run(pair_node(b,tmp_path/"node-priority",tmp_path/"node-priority-rt"))
    d=node.identity.device
    low,_=b.routing.commands.create(
        d["device_id"],"JOB_LOGS",{"proxy_job_id":"rjob_x","stream":"stdout"},
    )
    high,_=b.routing.commands.create(
        d["device_id"],"TASK_WORKTREE_ENSURE",{"task_id":"tsk_x"},
    )
    picked=b.routing.commands.poll(d["device_id"],d["route_generation"])
    assert picked["command_id"]==high["command_id"]
    assert picked["command_id"]!=low["command_id"]


def test_command_ttl_exceeds_gateway_wait(make_gateway):
    b=make_gateway()
    assert b.routing.config.mutation_ttl_seconds>b.routing.config.gateway_wait_seconds
