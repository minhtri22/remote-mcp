"""Zero-science tests: opt-in node timing, failure taxonomy, and unchanged behavior."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import httpx
import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.node.cadence import NodeCadence, sqlite_error_class
from conftest import pair_node


def records(tmp_path):
    path = tmp_path / "cadence" / "node-poll-cadence-v1.jsonl"
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]


def test_opt_in_telemetry_is_allowlisted_monotonic_and_never_logs_secrets(tmp_path):
    off = NodeCadence(tmp_path, enabled=False)
    assert off.cycle({"device_id": "dev_x"}) is None
    off.emit(None)
    assert not (tmp_path / "cadence").exists()

    tracer = NodeCadence(tmp_path, enabled=True)
    row = tracer.cycle({"device_id": "dev_x", "route_generation": 1})
    row["payload"] = "SECRET_MUST_NOT_LEAK"
    row["lease_token"] = "SECRET_MUST_NOT_LEAK"
    with tracer.phase(row, "poll_http_duration_ms"):
        time.sleep(.002)
    row["poll_status"] = "EMPTY_204"
    tracer.emit(row)
    second = tracer.cycle({"device_id": "dev_x", "route_generation": 1})
    with tracer.phase(second, "poll_http_duration_ms"):
        pass
    tracer.emit(second)
    xs = records(tmp_path)
    assert len(xs) == 2
    assert xs[0]["schema"] == "remotemcp.node-poll-cadence.v1"
    assert xs[0]["poll_http_duration_ms"] >= 1
    assert xs[1]["poll_gap_monotonic_ms"] >= 0
    assert xs[1]["cycle_seq"] == 2
    assert "SECRET" not in (tmp_path / "cadence" / "node-poll-cadence-v1.jsonl").read_text()


@pytest.mark.parametrize(("message", "code"), [
    ("locking protocol", "SQLITE_LOCKING_PROTOCOL"),
    ("database is locked", "SQLITE_DATABASE_LOCKED"),
    ("disk I/O error", "SQLITE_IOERR"),
])
def test_sqlite_fail_closed_error_taxonomy(message, code):
    assert sqlite_error_class(sqlite3.OperationalError(message)) == code


def test_instrumentation_io_failure_does_not_affect_node_protocol(tmp_path):
    parent = tmp_path / "cadence"
    parent.write_text("not a directory")
    t = NodeCadence(tmp_path, enabled=True)
    t.emit(t.cycle({"device_id": "dev_fixture"}))
    assert t.last_error_class is not None


class ModernFixtureClient:
    def __init__(self, service, *, fail_once=False, envelope=None):
        self.service = service
        self.fail_once = fail_once
        self.envelope = envelope
        self.poll_calls = 0
        self.heartbeats = 0
        self.results = []
        self.closed = False

    async def heartbeat(self, active_node_jobs, unresolved_node_jobs=0,
                        capacity_reconciliation_complete=True,
                        candidate_nonterminal_routed_jobs=None,
                        active_job_summaries=None, node_attestation=None):
        self.heartbeats += 1
        assert node_attestation["supported_node_diagnostics"] == ["process_inspect_v1"]
        return {"ok": True}

    async def poll(self):
        self.poll_calls += 1
        if self.fail_once and self.poll_calls == 1:
            raise DurableError("DEVICE_OFFLINE", "injected proxy HTTP 502")
        self.service._stop.set()
        return self.envelope

    async def result(self, command_id, payload):
        self.results.append((command_id, payload))
        return {"ok": True}

    async def close(self):
        self.closed = True


def test_signed_node_empty_poll_phase_timing(make_gateway, tmp_path):
    async def run():
        gw = make_gateway()
        node, _ = await pair_node(gw, tmp_path/"root", tmp_path/"rt", "cadence-read")
        node._cadence = NodeCadence(tmp_path/"trace", enabled=True)
        fake = ModernFixtureClient(node)
        node.client = fake
        await asyncio.wait_for(node.run_forever(), timeout=4)
        assert fake.poll_calls == 1 and fake.heartbeats == 1 and fake.closed
        row = records(tmp_path/"trace")[0]
        assert row["heartbeat_due"] is True
        assert row["capacity_duration_ms"] >= 0
        assert row["process_safety_duration_ms"] >= 0
        assert row["heartbeat_duration_ms"] >= 0
        assert row["poll_http_duration_ms"] >= 0
        assert row["poll_status"] == "EMPTY_204"
        assert "result_ack_duration_ms" not in row
        assert not fake.results
    asyncio.run(run())


def test_received_command_and_ack_phases_preserve_once_only_semantics(make_gateway, tmp_path):
    async def run():
        gw = make_gateway()
        node, _ = await pair_node(gw, tmp_path/"root", tmp_path/"rt", "cadence-ack")
        node._cadence = NodeCadence(tmp_path/"trace", enabled=True)
        fake = ModernFixtureClient(node, envelope={"command_id": "cmd_fixture"})
        node.client = fake
        executions = []
        async def execute(envelope):
            executions.append(envelope["command_id"])
            return {"state": "SUCCEEDED"}
        node.executor.execute = execute
        await asyncio.wait_for(node.run_forever(), timeout=4)
        assert executions == ["cmd_fixture"]
        assert len(fake.results) == 1 and fake.results[0][0] == "cmd_fixture"
        row = records(tmp_path/"trace")[0]
        assert row["command_id"] == "cmd_fixture"
        assert row["poll_status"] == "COMMAND_200"
        assert row["execute_duration_ms"] >= 0
        assert row["result_ack_duration_ms"] >= 0
    asyncio.run(run())


def test_poll_502_logs_retry_phase_without_job_replay(make_gateway, tmp_path, monkeypatch):
    async def run():
        gw = make_gateway()
        node, _ = await pair_node(gw, tmp_path/"root", tmp_path/"rt", "cadence-fail-poll")
        node._cadence = NodeCadence(tmp_path/"trace", enabled=True)
        node.RECONNECT_INITIAL_SECONDS = .001
        node.RECONNECT_MAX_SECONDS = .002
        fake = ModernFixtureClient(node, fail_once=True)
        node.client = fake
        await asyncio.wait_for(node.run_forever(), timeout=4)
        rows = records(tmp_path/"trace")
        assert len(rows) == 2
        assert rows[0]["exception_phase"] == "poll_http"
        assert rows[0]["transport_error_code"] == "DEVICE_OFFLINE"
        assert rows[0]["reconnect_backoff_ms"] == 1.0
        assert rows[1]["poll_status"] == "EMPTY_204"
        assert fake.poll_calls == 2
        assert not fake.results
    asyncio.run(run())


def test_sqlite_lock_failure_never_becomes_false_zero_capacity(make_gateway, tmp_path):
    async def run():
        gw = make_gateway()
        node, _ = await pair_node(gw, tmp_path/"root", tmp_path/"rt", "cadence-lock")
        node._cadence = NodeCadence(tmp_path/"trace", enabled=True)
        fake = ModernFixtureClient(node)
        node.client = fake
        def broken_capacity():
            raise sqlite3.OperationalError("locking protocol")
        node.jobs.capacity_snapshot = broken_capacity
        with pytest.raises(sqlite3.OperationalError, match="locking protocol"):
            await node.run_forever()
        assert fake.poll_calls == 0 and not fake.results
        row = records(tmp_path/"trace")[0]
        assert row["exception_phase"] == "capacity"
        assert row["sqlite_error_code"] == "SQLITE_LOCKING_PROTOCOL"
        assert row["heartbeat_due"] is True
        assert "heartbeat_duration_ms" not in row
    asyncio.run(run())



class SixtyCycleFixtureClient(ModernFixtureClient):
    async def poll(self):
        self.poll_calls+=1
        if self.poll_calls>=60:
            self.service._stop.set()
        return None


def test_sixteen_times_five_zero_science_poll_cycles_per_os(make_gateway,tmp_path):
    """Exactly 60 empty polls in the real node loop, with tracing ON and OFF.

    No production endpoint, node command or job is created. The associated
    overhead panel isolates tracer cost below; loop wall time is not a
    substitute for a trace-overhead measurement.
    """
    import hashlib

    async def run_one(enabled: bool):
        gw=make_gateway()
        label="cadence60-on" if enabled else "cadence60-off"
        node,_=await pair_node(gw,tmp_path/(label+"-root"),
                               tmp_path/(label+"-rt"),label)
        trace_root=tmp_path/(label+"-trace")
        node._cadence=NodeCadence(trace_root,enabled=enabled)
        node.client=SixtyCycleFixtureClient(node)
        await asyncio.wait_for(node.run_forever(),timeout=20)
        fake=node.client
        assert fake.poll_calls==60
        assert fake.heartbeats>=1
        assert fake.results==[]
        assert fake.closed
        log=trace_root/"cadence"/"node-poll-cadence-v1.jsonl"
        if not enabled:
            assert not log.exists()
            return None
        xs=records(trace_root)
        assert len(xs)==60
        assert [x["cycle_seq"] for x in xs]==list(range(1,61))
        assert all(x["poll_status"]=="EMPTY_204" for x in xs)
        assert all(x["poll_http_duration_ms"]>=0 for x in xs)
        assert all("command_id" not in x for x in xs)
        assert len({x["node_process_epoch"] for x in xs})==1
        assert len({x["route_generation"] for x in xs})==1
        assert all("result_ack_duration_ms" not in x for x in xs)
        assert all("execute_duration_ms" not in x for x in xs)
        for x in xs:
            assert "capacity_duration_ms" in x
            assert "process_safety_duration_ms" in x
        blob=log.read_bytes()
        assert b"SECRET" not in blob
        print("V31_CADENCE_60_EMPTY_POLLS=PASS "
              "sha256="+hashlib.sha256(blob).hexdigest()+
              " cycles=60 status=EMPTY_204",flush=True)
        return xs

    assert asyncio.run(run_one(False)) is None
    assert len(asyncio.run(run_one(True)))==60


def test_60_cycle_opt_in_telemetry_overhead_p95_is_under_5ms(tmp_path):
    """Pair reproducible in-memory empty polls; measure only tracer overhead."""
    import math
    import statistics
    import hashlib

    samples={}
    for flag in (False,True):
        tracer=NodeCadence(tmp_path/("enabled" if flag else "disabled"),
                           enabled=flag)
        latencies=[]
        for i in range(60):
            started=time.perf_counter_ns()
            row=tracer.cycle({"device_id":"dev_fixture","route_generation":1})
            with tracer.phase(row,"poll_http_duration_ms"):
                envelope=None  # deterministic empty signed-poll fixture
            if row is not None:
                row["poll_status"]="EMPTY_204" if envelope is None else "COMMAND_200"
            tracer.emit(row)
            latencies.append((time.perf_counter_ns()-started)/1_000_000)
        samples[flag]=latencies
    def nearest_rank_p95(values):
        seq=sorted(values)
        return seq[math.ceil(.95*len(seq))-1]
    # A paired conservative comparison allows separate baseline loop overhead
    # without declaring the OS scheduler or network as telemetry overhead.
    on_p95=nearest_rank_p95(samples[True])
    off_p95=nearest_rank_p95(samples[False])
    incremental=max(0,on_p95-off_p95)
    print(
        "V31_CADENCE_OVERHEAD_60=PASS_OR_FAIL "
        f"off_p95_ms={off_p95:.4f} on_p95_ms={on_p95:.4f} "
        f"incremental_p95_ms={incremental:.4f} threshold_ms=5.0000",
        flush=True,
    )
    log=tmp_path/"enabled"/"cadence"/"node-poll-cadence-v1.jsonl"
    rows=records(tmp_path/"enabled")
    assert len(rows)==60
    assert all(x["poll_status"]=="EMPTY_204" for x in rows)
    assert not (tmp_path/"disabled"/"cadence").exists()
    assert incremental <= 5.0
