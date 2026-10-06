from __future__ import annotations

import asyncio

import httpx

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
