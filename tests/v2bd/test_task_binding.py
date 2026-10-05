from __future__ import annotations

import asyncio,inspect,subprocess

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
        t=await drive(g,node,g.routing.task_create_or_local("t",p["project_id"],"task"))
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
        t=await drive(
            g,node,g.routing.task_create_or_local(
                "t-worktree-guard",p["project_id"],"task"
            )
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



def test_routed_task_create_pins_remote_only_branch(make_gateway,tmp_path):
    async def run():
        origin=tmp_path/"origin.git"
        seed=init_git_repo(tmp_path/"seed","SEED")
        subprocess.run(["git","-C",str(seed),"branch","-M","main"],check=True,capture_output=True,text=True)
        subprocess.run(["git","init","--bare",str(origin)],check=True,capture_output=True,text=True)
        subprocess.run(["git","-C",str(seed),"remote","add","origin",str(origin)],check=True,capture_output=True,text=True)
        subprocess.run(["git","-C",str(seed),"push","-u","origin","main"],check=True,capture_output=True,text=True)
        subprocess.run(["git","--git-dir",str(origin),"symbolic-ref","HEAD","refs/heads/main"],check=True,capture_output=True,text=True)

        subprocess.run(["git","-C",str(seed),"checkout","-b","research/remote-only"],check=True,capture_output=True,text=True)
        (seed/"REMOTE_ONLY.txt").write_text("remote-only\n",encoding="utf-8")
        subprocess.run(["git","-C",str(seed),"add","REMOTE_ONLY.txt"],check=True,capture_output=True,text=True)
        subprocess.run(["git","-C",str(seed),"commit","-m","remote only base"],check=True,capture_output=True,text=True)
        expected=subprocess.run(
            ["git","-C",str(seed),"rev-parse","HEAD"],
            check=True,capture_output=True,text=True,
        ).stdout.strip()
        subprocess.run(
            ["git","-C",str(seed),"push","origin","HEAD:refs/heads/research/remote-only"],
            check=True,capture_output=True,text=True,
        )

        root=tmp_path/"node-remote-only"
        root.mkdir()
        subprocess.run(["git","clone",str(origin),str(root/"repo")],check=True,capture_output=True,text=True)
        subprocess.run(
            ["git","-C",str(root/"repo"),"update-ref","-d","refs/remotes/origin/research/remote-only"],
            check=True,capture_output=True,text=True,
        )
        missing=subprocess.run(
            ["git","-C",str(root/"repo"),"rev-parse","--verify","research/remote-only^{commit}"],
            capture_output=True,text=True,
        )
        assert missing.returncode!=0

        g=make_gateway()
        node,dev=await pair_node(g,root,tmp_path/"rt-remote-only","node-remote-only")
        p=await drive(
            g,node,g.routing.project_register_on_device(
                "p-remote-only",dev["device_id"],"repo",4
            )
        )
        task=await drive(
            g,node,g.routing.task_create_or_local(
                "t-remote-only",p["project_id"],"task remote only","research/remote-only"
            )
        )
        assert task["base_commit"]==expected
        assert task["base_ref"]=="research/remote-only"

        agent=await g.multi.agent_register(
            "a-remote-only","agent","install-remote-only",[]
        )
        await drive(
            g,node,g.routing.task_claim_or_local(
                "c-remote-only",task["task_id"],agent["agent_id"],agent["session_id"]
            )
        )
        head=subprocess.run(
            ["git","-C",str(node.worktrees.execution_root(task["task_id"])),"rev-parse","HEAD"],
            check=True,capture_output=True,text=True,
        ).stdout.strip()
        assert head==expected
    asyncio.run(run())


def test_routed_task_create_invalid_base_fails_before_ready_task(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-invalid-base"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt-invalid-base","node-invalid-base")
        p=await drive(
            g,node,g.routing.project_register_on_device(
                "p-invalid-base",dev["device_id"],"repo",4
            )
        )
        before=g.durable.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?",
            (p["project_id"],),
        )["n"]
        with pytest.raises(DurableError) as exc:
            await drive(
                g,node,g.routing.task_create_or_local(
                    "t-invalid-base",p["project_id"],"bad task","refs/heads/does-not-exist"
                )
            )
        assert exc.value.code=="TASK_BASE_REF_UNRESOLVED"
        after=g.durable.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?",
            (p["project_id"],),
        )["n"]
        assert after==before
    asyncio.run(run())



def test_routed_claim_repairs_legacy_null_base_commit(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-legacy-base"
        root.mkdir()
        init_git_repo(root/"repo","NODE")
        node,dev=await pair_node(g,root,tmp_path/"rt-legacy-base","node-legacy-base")
        p=await drive(
            g,node,g.routing.project_register_on_device(
                "p-legacy-base",dev["device_id"],"repo",4
            )
        )
        task=await drive(
            g,node,g.routing.task_create_or_local(
                "t-legacy-base",p["project_id"],"legacy task","HEAD"
            )
        )
        expected=task["base_commit"]
        assert expected
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE tasks SET base_commit=NULL WHERE task_id=?",
                (task["task_id"],),
            )

        agent=await g.multi.agent_register(
            "a-legacy-base","agent","install-legacy-base",[]
        )
        await drive(
            g,node,g.routing.task_claim_or_local(
                "c-legacy-base",task["task_id"],agent["agent_id"],agent["session_id"]
            )
        )
        repaired=g.routing.task_status_or_local(task["task_id"])
        assert repaired["base_commit"]==expected
        head=subprocess.run(
            ["git","-C",str(node.worktrees.execution_root(task["task_id"])),"rev-parse","HEAD"],
            check=True,capture_output=True,text=True,
        ).stdout.strip()
        assert head==expected
    asyncio.run(run())
