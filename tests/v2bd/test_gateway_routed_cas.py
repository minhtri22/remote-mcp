from __future__ import annotations

import asyncio

from conftest import pair_node,drive,init_git_repo


def test_device_bound_cas_mutates_node_worktree_not_gateway(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")
        p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",4))
        a=await g.multi.agent_register("a","agent","install",[])
        t=await g.routing.task_create_or_local("t",p["project_id"],"task")
        c=await drive(g,node,g.routing.task_claim_or_local("c",t["task_id"],a["agent_id"],a["session_id"]))
        r=await drive(g,node,g.routing.task_read_file(t["task_id"],"DEVICE_MARKER.txt"))
        out=await drive(g,node,g.routing.file_write_cas_or_local(
            "w",t["task_id"],c["lease_token"],c["lease_epoch"],
            "DEVICE_MARKER.txt",r["sha256"],"REMOTE_ONLY\n"
        ))
        assert out["after_hash"]!=r["sha256"]
        assert "REMOTE_ONLY" in (node.worktrees.execution_root(t["task_id"])/"DEVICE_MARKER.txt").read_text()
        assert not (g.workspace/"DEVICE_MARKER.txt").exists()
    asyncio.run(run())
