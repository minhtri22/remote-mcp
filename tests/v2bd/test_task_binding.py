from __future__ import annotations

import asyncio,inspect

from conftest import pair_node,drive,init_git_repo


def test_task_inherits_immutable_device_and_claim_routes_only_there(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")
        p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",4))
        t=await g.routing.task_create_or_local("t",p["project_id"],"task")
        b=g.routing.bindings.task_binding(t["task_id"])
        assert b["device_id"]==dev["device_id"]
        assert int(b["binding_generation"])==p["binding_generation"]
        agent=await g.multi.agent_register("a","agent","install",[])
        c=await drive(g,node,g.routing.task_claim_or_local("c",t["task_id"],agent["agent_id"],agent["session_id"]))
        assert c["device_id"]==dev["device_id"]
        assert g.multi.tasks.status(t["task_id"])["state"]=="RUNNING"
        # Routing authority came only from the immutable task binding above.
    asyncio.run(run())
