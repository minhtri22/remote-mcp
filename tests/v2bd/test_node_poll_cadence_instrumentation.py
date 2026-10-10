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
