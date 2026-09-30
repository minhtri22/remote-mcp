from __future__ import annotations

import asyncio

from conftest import pair_harness,pump_until


def test_two_node_routing_reads_correct_marker_and_no_cross_route(make_gateway,tmp_path):
    async def run():
        g=make_gateway(); a=await pair_harness(g,tmp_path,"node-a","PHYSICAL_MACHINE_1_ONLY"); b=await pair_harness(g,tmp_path,"node-b","PHYSICAL_MACHINE_2_ONLY")
        from conftest import init_git_repo
        init_git_repo(b.root/"pilot-machine-2","PHYSICAL_MACHINE_2_ONLY")
        stop=asyncio.Event(); pa=asyncio.create_task(pump_until(stop,g,a)); pb=asyncio.create_task(pump_until(stop,g,b))
        try:
            p1=await g.routing.project_register_on_device("p1",a.device["device_id"],"pilot",4)
            p2=await g.routing.project_register_on_device("p2",b.device["device_id"],"pilot-machine-2",4)
            t1=await g.routing.task_create_or_local("t1",p1["project_id"],"one")
            t2=await g.routing.task_create_or_local("t2",p2["project_id"],"two")
            ag=await g.multi.agent_register("areg","agent","install",[])
            c1=await g.routing.task_claim_or_local("c1",t1["task_id"],ag["agent_id"],ag["session_id"])
            c2=await g.routing.task_claim_or_local("c2",t2["task_id"],ag["agent_id"],ag["session_id"])
            r1=await g.routing.task_read_file(t1["task_id"],"DEVICE_MARKER.txt")
            r2=await g.routing.task_read_file(t2["task_id"],"DEVICE_MARKER.txt")
            assert "PHYSICAL_MACHINE_1_ONLY" in r1["data"]
            assert "PHYSICAL_MACHINE_2_ONLY" in r2["data"]
            assert p1["routing"]["device_id"]==a.device["device_id"]
            assert p2["routing"]["device_id"]==b.device["device_id"]
            assert p1["routing"]["hostname"]=="node-a-host"
            assert p2["routing"]["hostname"]=="node-b-host"
            assert t1["routing"]["device_id"]==a.device["device_id"]
            assert t2["routing"]["device_id"]==b.device["device_id"]
            assert g.routing.project_status_or_local(p1["project_id"])["routing"]["hostname"]=="node-a-host"
            assert g.routing.project_status_or_local(p2["project_id"])["routing"]["hostname"]=="node-b-host"
            assert g.routing.task_status_or_local(t1["task_id"])["routing"]["device_id"]==a.device["device_id"]
            assert g.routing.task_status_or_local(t2["task_id"])["routing"]["device_id"]==b.device["device_id"]
            assert r1["routing"]["device_id"]==a.device["device_id"]
            assert r2["routing"]["device_id"]==b.device["device_id"]
            assert r1["routing"]["hostname"]=="node-a-host"
            assert r2["routing"]["hostname"]=="node-b-host"
            assert a.db.query_one("select * from node_tasks where task_id=?",(t2["task_id"],)) is None
            assert b.db.query_one("select * from node_tasks where task_id=?",(t1["task_id"],)) is None
        finally:
            stop.set(); await asyncio.gather(pa,pb)
    asyncio.run(run())
