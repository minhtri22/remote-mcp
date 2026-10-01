from __future__ import annotations

import asyncio

from conftest import pair_node, pump_once


def test_device_restart_routes_to_same_identity(make_gateway,tmp_path):
    async def run():
        b=make_gateway()
        node,_=await pair_node(b,tmp_path/"root",tmp_path/"rt","restart-node")
        did=node.identity.device["device_id"]

        task=asyncio.create_task(
            b.routing.device_restart("restart-op",did,"qualification",False)
        )
        deadline=asyncio.get_running_loop().time()+3
        while not task.done():
            await pump_once(b,node)
            if asyncio.get_running_loop().time()>=deadline:
                task.cancel()
                raise AssertionError("restart route timeout")
            await asyncio.sleep(.005)

        out=await task
        assert out["device_id"]==did
        assert out["restart_requested"] is True
        assert out["same_identity_expected"] is True
        assert out["active_routed_jobs_at_request"]==0
        assert node.executor.restart_requested is True

        node.executor.restart_requested=False
        replay=await b.routing.device_restart("restart-op",did,"qualification",False)
        assert replay["replayed"] is True
        assert replay["device_id"]==did
        assert node.executor.restart_requested is False

    asyncio.run(run())
