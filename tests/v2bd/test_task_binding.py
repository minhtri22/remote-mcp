from __future__ import annotations

import asyncio,inspect

import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.workspace_layout import project_workspace_rel

from conftest import pair_node,drive,init_git_repo


def test_task_inherits_immutable_device_and_claim_routes_only_there(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt","node")
        p=await drive(g,node,g.routing.project_register_on_device("p",dev["device_id"],"repo",4))
        t=await g.routing.task_create_or_local("t",p["project_id"],"task")
        expected_prefix=f"{project_workspace_rel(p['project_id'])}/worktrees/"
        assert t["worktree_rel"].startswith(expected_prefix)
        b=g.routing.bindings.task_binding(t["task_id"])
        assert b["device_id"]==dev["device_id"]
        assert int(b["binding_generation"])==p["binding_generation"]
        agent=await g.multi.agent_register("a","agent","install",[])
        c=await drive(g,node,g.routing.task_claim_or_local("c",t["task_id"],agent["agent_id"],agent["session_id"]))
        assert c["device_id"]==dev["device_id"]
        assert g.multi.tasks.status(t["task_id"])["state"]=="RUNNING"
        # Routing authority came only from the immutable task binding above.
    asyncio.run(run())


def test_node_rejects_agent_selected_worktree_outside_project_workspace(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-layout";root.mkdir();init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt-layout","node-layout")
        p=await drive(
            g,node,g.routing.project_register_on_device(
                "p-layout",dev["device_id"],"repo",4
            )
        )
        payload={
            "task_id":"tsk_rogue",
            "project_id":p["project_id"],
            "binding_generation":p["binding_generation"],
            "branch_name":"remotemcp/task/tsk_rogue",
            "worktree_rel":"CLDP-SIX-ROGUE-SIBLING",
            "base_ref":"HEAD",
            "base_commit":None,
        }
        with pytest.raises(DurableError) as exc:
            node.worktrees.ensure(payload)
        assert exc.value.code=="WORKTREE_LAYOUT_VIOLATION"
        assert not (root/"CLDP-SIX-ROGUE-SIBLING").exists()
    asyncio.run(run())


def test_routed_submit_blocks_direct_git_worktree_mutation_at_gateway(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-worktree-guard"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(
            g,root,tmp_path/"rt-worktree-guard","node-worktree-guard"
        )
        p=await drive(
            g,node,g.routing.project_register_on_device(
                "p-worktree-guard",dev["device_id"],"repo",4
            )
        )
        t=await g.routing.task_create_or_local(
            "t-worktree-guard",p["project_id"],"task"
        )
        agent=await g.multi.agent_register(
            "a-worktree-guard","agent","install",[]
        )
        claim=await drive(
            g,node,g.routing.task_claim_or_local(
                "c-worktree-guard",t["task_id"],
                agent["agent_id"],agent["session_id"]
            )
        )
        with pytest.raises(DurableError) as exc:
            await g.routing.task_job_submit_or_local(
                "j-worktree-guard",
                t["task_id"],
                claim["lease_token"],
                claim["lease_epoch"],
                ["git","worktree","add","../rogue-worktree"],
                ".",
            )
        assert exc.value.code=="WORKTREE_MUTATION_FORBIDDEN"
        assert g.routing.routed_jobs.by_operation("j-worktree-guard") is None
        assert not (root/"rogue-worktree").exists()
    asyncio.run(run())
