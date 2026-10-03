from __future__ import annotations

import asyncio
import time

import pytest

from conftest import pair_node
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


def _paired(make_gateway,tmp_path):
    bundle=make_gateway()
    node,_=asyncio.run(pair_node(bundle,tmp_path/"node",tmp_path/"node-rt"))
    return bundle,node.identity.device["device_id"]


def test_status_list_and_require_online_do_not_invoke_persistence_sweep(
    make_gateway,tmp_path,monkeypatch
):
    bundle,did=_paired(make_gateway,tmp_path)

    def forbidden_sweep():
        raise AssertionError("request-time read path must not persist offline state")

    monkeypatch.setattr(bundle.routing.devices,"sweep_offline",forbidden_sweep)

    status=bundle.routing.device_status(did)
    listed=bundle.routing.device_list()
    row=bundle.routing.devices.require_online(did)

    assert status["state"]=="ONLINE"
    assert [d["device_id"] for d in listed["devices"]]==[did]
    assert row["device_id"]==did


def test_effective_offline_is_fail_closed_without_request_time_write(
    make_gateway,tmp_path
):
    bundle,did=_paired(make_gateway,tmp_path)

    stale=now_ms()-bundle.routing.devices.offline_ms-1000
    with bundle.durable.db.transaction() as con:
        con.execute(
            "UPDATE devices SET state='ONLINE',last_seen_at_ms=? WHERE device_id=?",
            (stale,did),
        )

    # Request-time status is freshness-correct but pure-read.
    assert bundle.routing.device_status(did)["state"]=="OFFLINE"
    assert bundle.routing.devices.get(did)["state"]=="ONLINE"

    with pytest.raises(DurableError) as exc:
        bundle.routing.devices.require_online(did)
    assert exc.value.code=="DEVICE_OFFLINE"

    # The request-time guard still did not mutate durable state.
    assert bundle.routing.devices.get(did)["state"]=="ONLINE"

    # The background-owned persistence primitive performs the state mutation.
    assert bundle.routing.devices.sweep_offline()==1
    assert bundle.routing.devices.get(did)["state"]=="OFFLINE"


def test_device_read_paths_do_not_wait_on_immediate_writer_lock(
    make_gateway,tmp_path
):
    bundle,did=_paired(make_gateway,tmp_path)

    writer=bundle.durable.db.connect()
    try:
        writer.execute("BEGIN IMMEDIATE")

        started=time.perf_counter()
        status=bundle.routing.device_status(did)
        listed=bundle.routing.device_list()
        elapsed=time.perf_counter()-started

        assert status["device_id"]==did
        assert [d["device_id"] for d in listed["devices"]]==[did]
        # A request-time BEGIN IMMEDIATE would wait for the database's 5 s
        # busy timeout here. Pure SELECT paths should complete well below that.
        assert elapsed < 2.0
    finally:
        writer.execute("ROLLBACK")
        writer.close()
