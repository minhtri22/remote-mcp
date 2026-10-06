from __future__ import annotations

import asyncio

import httpx
import time

from conftest import pair_node


class FlakyClient:
    def __init__(self,service):
        self.service=service
        self.heartbeat_calls=0
        self.poll_calls=0
        self.closed=False

    async def heartbeat(self,active):
        self.heartbeat_calls += 1
        if self.heartbeat_calls == 1:
            raise httpx.ConnectError("injected gateway restart")
        return {"ok":True}

    async def poll(self):
        self.poll_calls += 1
        self.service._stop.set()
        return None

    async def result(self,command_id,payload):
        return {"ok":True}

    async def close(self):
        self.closed=True


def test_node_survives_transient_gateway_disconnect(make_gateway,tmp_path,monkeypatch):
    async def run():
        b=make_gateway()
        node,_=await pair_node(b,tmp_path/"root",tmp_path/"rt","reconnect-node")
        monkeypatch.setattr(node,"RECONNECT_INITIAL_SECONDS",0.01)
        monkeypatch.setattr(node,"RECONNECT_MAX_SECONDS",0.02)
        flaky=FlakyClient(node)
        node.client=flaky

        def forbidden_probe():
            raise AssertionError(
                "legacy heartbeat compatibility path must not enumerate OS processes"
            )

        monkeypatch.setattr(node.process_safety,"snapshot",forbidden_probe)
        await asyncio.wait_for(node.run_forever(),timeout=2)
        assert flaky.heartbeat_calls >= 2
        assert flaky.poll_calls == 1
        assert flaky.closed is True
    asyncio.run(run())


class ModernFlakyClient:
    def __init__(self,service):
        self.service=service
        self.heartbeat_calls=0
        self.poll_calls=0
        self.closed=False
        self.attestations=[]

    async def heartbeat(
        self,
        active_node_jobs,
        unresolved_node_jobs=0,
        capacity_reconciliation_complete=True,
        candidate_nonterminal_routed_jobs=None,
        active_job_summaries=None,
        node_attestation=None,
    ):
        self.heartbeat_calls += 1
        self.attestations.append(dict(node_attestation or {}))
        if self.heartbeat_calls == 1:
            raise httpx.ConnectError("injected gateway restart")
        return {"ok":True}

    async def poll(self):
        self.poll_calls += 1
        self.service._stop.set()
        return None

    async def result(self,command_id,payload):
        return {"ok":True}

    async def close(self):
        self.closed=True


def test_modern_reconnect_does_not_wait_for_slow_process_scanner(make_gateway,tmp_path,monkeypatch):
    async def run():
        b=make_gateway()
        node,_=await pair_node(b,tmp_path/"modern-root",tmp_path/"modern-rt","modern-reconnect")
        monkeypatch.setattr(node,"RECONNECT_INITIAL_SECONDS",0.01)
        monkeypatch.setattr(node,"RECONNECT_MAX_SECONDS",0.02)
        modern=ModernFlakyClient(node)
        node.client=modern
        calls={"n":0}

        def slow_probe():
            calls["n"] += 1
            time.sleep(.5)
            return {
                "physical_process_safety_resolved":1,
                "physical_process_blocker_count":0,
                "physical_process_residual_job_count":0,
                "physical_process_declared_long_lived_count":0,
                "physical_process_active_job_tree_count":0,
                "physical_process_declared_pattern_count":0,
                "physical_process_observed_at_ms":time.time_ns()//1_000_000,
                "physical_process_snapshot_sha256":"a"*64,
                "physical_process_blocker_summary_json":"[]",
                "physical_process_error":"",
            }

        monkeypatch.setattr(node.process_safety,"snapshot",slow_probe)
        started=asyncio.get_running_loop().time()
        await asyncio.wait_for(node.run_forever(),timeout=.3)
        elapsed=asyncio.get_running_loop().time()-started
        assert elapsed < .3
        assert modern.heartbeat_calls >= 2
        assert modern.poll_calls == 1
        assert modern.closed is True
        assert calls["n"] == 1
        assert modern.attestations[0]["physical_process_safety_resolved"] == 0
        assert modern.attestations[0]["physical_process_blocker_count"] == -1
    asyncio.run(run())


def test_process_scanner_failure_is_cached_unresolved(make_gateway,tmp_path,monkeypatch):
    async def run():
        b=make_gateway()
        node,_=await pair_node(b,tmp_path/"failure-root",tmp_path/"failure-rt","scanner-failure")

        def broken_probe():
            raise RuntimeError("scanner boom")

        monkeypatch.setattr(node.process_safety,"snapshot",broken_probe)
        first=await node._process_safety_for_heartbeat()
        assert first["physical_process_safety_resolved"] == 0
        assert first["physical_process_blocker_count"] == -1

        for _ in range(100):
            await asyncio.sleep(.001)
            if node._process_safety_task is not None and node._process_safety_task.done():
                break

        second=await node._process_safety_for_heartbeat()
        assert second["physical_process_safety_resolved"] == 0
        assert second["physical_process_blocker_count"] == -1
        assert second["physical_process_error"] == "RuntimeError"
    asyncio.run(run())
