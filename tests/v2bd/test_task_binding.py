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


def test_guarded_claim_refuses_pending_routed_command_before_leasing(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-pending-claim";root.mkdir()
        init_git_repo(root/"repo","CLAIM")
        node,dev=await pair_node(g,root,tmp_path/"rt-pending-claim","pending-claim")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-pending-claim",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-pending-claim",project["project_id"],"guard"))
        task_id=task["task_id"]
        cmd,_=g.routing.commands.create(
            dev["device_id"],"TASK_WORKTREE_ENSURE",{"task_id":task_id},
            task_id=task_id,project_id=project["project_id"],
        )
        before=g.multi.tasks.status(task_id)
        agent=await g.multi.agent_register("a-pending-claim","test","pending",[])
        with pytest.raises(DurableError) as exc:
            await g.routing.task_claim_or_local(
                "claim-pending-claim",task_id,agent["agent_id"],agent["session_id"])
        assert exc.value.code=="TASK_COMMAND_UNRESOLVED"
        after=g.multi.tasks.status(task_id)
        assert after["state"]=="READY"
        assert after["lease_epoch"]==before["lease_epoch"]==0
        assert after["owner_agent_id"] is None
        snapshot=g.routing.task_cleanup_recovery_status_or_local(task_id)
        assert [c["command_id"] for c in snapshot["unresolved_commands"]]==[cmd["command_id"]]
        assert snapshot["worktree_deletion_authorized"] is False
        assert snapshot["lease_active"] is False
        assert snapshot["lease_record_present"] is False
    asyncio.run(run())


def test_guarded_cleanup_requires_clean_exact_head_and_is_idempotent(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-cleanup";root.mkdir()
        init_git_repo(root/"repo","CLEANUP")
        node,dev=await pair_node(g,root,tmp_path/"rt-cleanup","cleanup")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-cleanup",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-cleanup",project["project_id"],"cleanup"))
        task_id=task["task_id"]
        agent=await g.multi.agent_register("a-cleanup","test","cleanup",[])
        claim=await drive(g,node,g.routing.task_claim_or_local(
            "claim-cleanup",task_id,agent["agent_id"],agent["session_id"]))
        worktree=node.worktrees.execution_root(task_id)
        head=node.worktrees.status(task_id)["head_commit"]
        assert head==task["base_commit"]
        # Synthetic local state is a zero-science test fixture, not a production mutation.
        with g.durable.db.transaction() as con:
            con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
            con.execute(
                "UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,"
                "owner_session_id=NULL,cleanup_pending=1 WHERE task_id=?",(task_id,),
            )
        evidence=worktree/"KEEP_EVIDENCE.json"
        evidence.write_text('{"preserve":true}',encoding="utf-8")
        with pytest.raises(DurableError) as dirty:
            await drive(g,node,g.routing.task_cleanup_pending_resolve_or_local(
                "cleanup-dirty",task_id,claim["lease_epoch"],task["base_commit"],head))
        assert dirty.value.code=="TASK_CLEANUP_WORKTREE_UNVERIFIED"
        assert evidence.exists()
        assert g.multi.tasks.status(task_id)["cleanup_pending"] is True
        evidence.unlink()  # Only removes fixture in tmp_path, never a real scientific artifact.
        with pytest.raises(DurableError) as wrong:
            await drive(g,node,g.routing.task_cleanup_pending_resolve_or_local(
                "cleanup-wrong-head",task_id,claim["lease_epoch"],task["base_commit"],"f"*40))
        assert wrong.value.code=="TASK_CLEANUP_WORKTREE_UNVERIFIED"
        original_node_status=node.worktrees.status
        def forged_status(tid):
            result=original_node_status(tid)
            return {**result,"binding_generation":999999,
                    "encoded_root_rel":"FORGED_ROOT"}
        node.worktrees.status=forged_status
        try:
            with pytest.raises(DurableError) as wrong_binding:
                await drive(g,node,g.routing.task_cleanup_pending_resolve_or_local(
                    "cleanup-wrong-binding",task_id,claim["lease_epoch"],task["base_commit"],head))
            assert wrong_binding.value.code=="TASK_CLEANUP_WORKTREE_UNVERIFIED"
            assert g.multi.tasks.status(task_id)["cleanup_pending"] is True
        finally:
            node.worktrees.status=original_node_status
        result=await drive(g,node,g.routing.task_cleanup_pending_resolve_or_local(
            "cleanup-good",task_id,claim["lease_epoch"],task["base_commit"],head))
        assert result["cleanup_pending"] is False
        assert result["worktree_preserved"] is True
        assert worktree.exists()
        assert g.multi.tasks.status(task_id)["cleanup_pending"] is False
        replay=await g.routing.task_cleanup_pending_resolve_or_local(
            "cleanup-good",task_id,claim["lease_epoch"],task["base_commit"],head)
        assert replay["replayed"] is True
    asyncio.run(run())


def test_guarded_cleanup_refuses_unproven_job_and_requires_exact_epoch(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-unproven";root.mkdir()
        init_git_repo(root/"repo","UNPROVEN")
        node,dev=await pair_node(g,root,tmp_path/"rt-unproven","unproven")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-unproven",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-unproven",project["project_id"],"guard"))
        agent=await g.multi.agent_register("a-unproven","test","unproven",[])
        claim=await drive(g,node,g.routing.task_claim_or_local(
            "claim-unproven",task["task_id"],agent["agent_id"],agent["session_id"]))
        g.durable.operations.reserve(
            "orphaned-job","TASK_JOB_SUBMIT",{"fixture":"unproven-terminal"},
            principal_key=g.routing.owner_account_id,
            project_id=project["project_id"],task_id=task["task_id"],
        )
        with g.durable.db.transaction() as con:
            con.execute("DELETE FROM task_leases WHERE task_id=?",(task["task_id"],))
            con.execute(
                "UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,"
                "owner_session_id=NULL,cleanup_pending=1 WHERE task_id=?",
                (task["task_id"],),
            )
            g.routing.routed_jobs.create(
                con,"orphaned-job",task["task_id"],project["project_id"],dev["device_id"],
            )
        head=node.worktrees.status(task["task_id"])["head_commit"]
        with pytest.raises(DurableError) as bad_epoch:
            await g.routing.task_cleanup_pending_resolve_or_local(
                "cleanup-bad-epoch",task["task_id"],claim["lease_epoch"]+1,
                task["base_commit"],head,
            )
        assert bad_epoch.value.code=="TASK_CLEANUP_IDENTITY_MISMATCH"
        assert g.durable.db.query_one(
            "SELECT 1 FROM operations WHERE operation_id='cleanup-bad-epoch'"
        ) is None
        with pytest.raises(DurableError) as unknown:
            await g.routing.task_cleanup_pending_resolve_or_local(
                "cleanup-unproven",task["task_id"],claim["lease_epoch"],
                task["base_commit"],head,
            )
        assert unknown.value.code=="TASK_JOB_EVIDENCE_UNRESOLVED"
        assert g.durable.db.query_one(
            "SELECT 1 FROM operations WHERE operation_id='cleanup-unproven'"
        ) is None
        assert g.multi.tasks.status(task["task_id"])["cleanup_pending"] is True
    asyncio.run(run())


def test_guarded_claim_keeps_delivered_then_cancelled_command_unresolved(make_gateway,tmp_path):
    """A cancelled, formerly leased command may still have executed on the node."""
    async def run():
        g=make_gateway()
        root=tmp_path/"node-cancelled-delivered";root.mkdir()
        init_git_repo(root/"repo","CANCELLED")
        node,dev=await pair_node(g,root,tmp_path/"rt-cancelled-delivered","cancelled-delivered")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-cancelled-delivered",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-cancelled-delivered",project["project_id"],"guard"))
        task_id=task["task_id"]
        cmd,_=g.routing.commands.create(
            dev["device_id"],"TASK_WORKTREE_ENSURE",{"task_id":task_id},
            task_id=task_id,project_id=project["project_id"],
        )
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='CANCELLED',delivery_attempt=1,"
                "error_code='DEVICE_COMMAND_EXPIRED' WHERE command_id=?",
                (cmd["command_id"],),
            )
        pending=g.routing.task_cleanup_recovery_status_or_local(task_id)["unresolved_commands"]
        assert [x["command_id"] for x in pending]==[cmd["command_id"]]
        agent=await g.multi.agent_register("a-cancelled-delivered","test","cancelled",[])
        with pytest.raises(DurableError) as exc:
            await g.routing.task_claim_or_local(
                "claim-cancelled-delivered",task_id,agent["agent_id"],agent["session_id"])
        assert exc.value.code=="TASK_COMMAND_UNRESOLVED"
        assert g.durable.db.query_one(
            "SELECT 1 FROM operations WHERE operation_id='claim-cancelled-delivered'"
        ) is None
        assert g.multi.tasks.status(task_id)["state"]=="READY"
        # A queued command cancelled before its first delivery has no node-side effect.
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET delivery_attempt=0 WHERE command_id=?",
                (cmd["command_id"],),
            )
        assert g.routing.task_cleanup_recovery_status_or_local(task_id)["unresolved_commands"]==[]
        # This oracle exclusively tests cancellation/delivery admission.
        # Git worktree creation has separate platform-specific regression tests;
        # avoid coupling the cancellation gate to Windows Git path-length limits.
        assert g.routing._require_cleanup_quiescent(task_id,
            g.routing.bindings.task_binding(task_id)) is None
        assert g.multi.tasks.status(task_id)["state"]=="READY"
        assert g.durable.db.query_one(
            "SELECT 1 FROM operations WHERE operation_id='claim-cancelled-delivered'"
        ) is None
    asyncio.run(run())


def test_guarded_claim_rejects_terminal_cache_with_wrong_proxy_identity(make_gateway,tmp_path):
    """Independent negative oracle: terminal=True cannot substitute for exact job proof."""
    async def run():
        g=make_gateway()
        root=tmp_path/"node-forged-terminal";root.mkdir()
        init_git_repo(root/"repo","FORGED")
        node,dev=await pair_node(g,root,tmp_path/"rt-forged-terminal","forged-terminal")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-forged-terminal",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-forged-terminal",project["project_id"],"guard"))
        g.durable.operations.reserve(
            "forged-terminal-op","TASK_JOB_SUBMIT",{"fixture":"bad-proxy"},
            principal_key=g.routing.owner_account_id,
            project_id=project["project_id"],task_id=task["task_id"],
        )
        with g.durable.db.transaction() as con:
            proxy,_=g.routing.routed_jobs.create(
                con,"forged-terminal-op",task["task_id"],project["project_id"],dev["device_id"],
            )
        g.routing.routed_jobs.update(
            proxy["proxy_job_id"],node_job_id="job_frozen_fixture",
            state="SUCCEEDED",terminal_result={
                "terminal":True,"state":"SUCCEEDED",
                "proxy_job_id":"rjob_wrong","node_job_id":"job_frozen_fixture",
            },
        )
        snapshot=g.routing.task_cleanup_recovery_status_or_local(task["task_id"])
        assert snapshot["jobs"][0]["terminal_evidence_available"] is True
        assert snapshot["jobs"][0]["terminal_evidence_verified"] is False
        agent=await g.multi.agent_register("a-forged-terminal","test","forged",[])
        with pytest.raises(DurableError) as exc:
            await g.routing.task_claim_or_local(
                "claim-forged-terminal",task["task_id"],agent["agent_id"],agent["session_id"])
        assert exc.value.code=="TASK_JOB_EVIDENCE_UNRESOLVED"
        assert g.multi.tasks.status(task["task_id"])["state"]=="READY"
        assert g.durable.db.query_one(
            "SELECT 1 FROM operations WHERE operation_id='claim-forged-terminal'"
        ) is None
        g.routing.routed_jobs.update(
            proxy["proxy_job_id"],node_job_id="job_frozen_fixture",
            state="SUCCEEDED",terminal_result={
                "terminal":True,"state":"SUCCEEDED",
                "proxy_job_id":proxy["proxy_job_id"],"node_job_id":"job_frozen_fixture",
            },
        )
        assert g.routing.task_cleanup_recovery_status_or_local(
            task["task_id"])["jobs"][0]["terminal_evidence_verified"] is True
    asyncio.run(run())


def test_dispatch_diagnostic_is_gateway_only_and_reports_expired_leases(make_gateway,tmp_path):
    """No node dispatch, queued job resubmit, or mutation of live queue states."""
    async def run():
        g=make_gateway()
        root=tmp_path/"node-diagnostic";root.mkdir()
        init_git_repo(root/"repo","DIAG")
        node,dev=await pair_node(g,root,tmp_path/"rt-diagnostic","diag")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-diagnostic",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-diagnostic",project["project_id"],"read-only"))
        tid=task["task_id"]
        command,_=g.routing.commands.create(
            dev["device_id"],"JOB_SUBMIT",{"proxy_job_id":"rjob_fixture_only"},
            task_id=tid,project_id=project["project_id"],
        )
        count_before=g.durable.db.query_one(
            "SELECT COUNT(*) AS n FROM device_commands WHERE device_id=?",
            (dev["device_id"],),
        )["n"]
        report=g.routing.task_dispatch_diagnostic_or_local(tid)
        assert report["read_only"] is True
        assert report["node_command_dispatched"] is False
        assert report["node_process_liveness_verified"] is False
        assert report["authorization_to_rerun_jobs"] is False
        assert report["next_eligible_poll_command"]["command_id"]==command["command_id"]
        assert report["task_command_trace"][-1]["state"]=="QUEUED"
        assert report["task_command_trace"][-1]["delivery_attempt"]==0
        assert g.durable.db.query_one(
            "SELECT COUNT(*) AS n FROM device_commands WHERE device_id=?",
            (dev["device_id"],),
        )["n"]==count_before
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='LEASED',delivery_attempt=1,"
                "lease_expires_at_ms=1 WHERE command_id=?",(command["command_id"],),
            )
        report=g.routing.task_dispatch_diagnostic_or_local(tid)
        row=report["task_command_trace"][-1]
        assert row["state"]=="LEASED"
        assert row["delivery_uncertain"] is True
        assert row["delivery_lease_expired"] is True
        assert report["next_eligible_poll_command"] is None
        assert g.routing.commands.get(command["command_id"])["state"]=="LEASED"
    asyncio.run(run())


def test_dispatch_diagnostic_rejects_unbound_task(make_gateway,tmp_path):
    g=make_gateway()
    with pytest.raises(DurableError) as exc:
        g.routing.task_dispatch_diagnostic_or_local("tsk_invalid_not_bound")
    assert exc.value.code=="DEVICE_CONTEXT_REQUIRED"


def test_r5_signed_route_attests_cancelled_original_without_reexecution(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-r5-proof";root.mkdir()
        init_git_repo(root/"repo","R5")
        node,dev=await pair_node(g,root,tmp_path/"rt-r5-proof","r5")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-r5-proof",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-r5-proof",project["project_id"],"proof"))
        task_id=task["task_id"]
        command,_=g.routing.commands.create(
            dev["device_id"],"TASK_LIST_DIR",{"task_id":task_id,"path":"."},
            project_id=project["project_id"],task_id=task_id,
        )
        envelope=g.routing.commands.envelope(command)
        node.journal.receive(envelope)
        node.journal.mark_executing(command["command_id"])
        node.journal.terminal(command["command_id"],"SUCCEEDED",result={"fixture":True})
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='CANCELLED',delivery_attempt=1,"
                "error_code='DEVICE_COMMAND_EXPIRED' WHERE command_id=?",
                (command["command_id"],),
            )
        result=await drive(g,node,g.routing.task_r5_command_attestation_or_local(
            "r5-prove",task_id,command["command_id"]))
        proof=result["node_receipt"]
        assert proof["terminal"] is True
        assert proof["state"]=="SUCCEEDED"
        assert proof["target_reexecuted"] is False
        assert proof["request_hash"]==command["request_hash"]
        assert result["original_command_mutated"] is False
        assert result["cleanup_pending_resolution_authorized"] is False
        assert g.routing.commands.get(command["command_id"])["state"]=="CANCELLED"
        replay=await g.routing.task_r5_command_attestation_or_local(
            "r5-prove",task_id,command["command_id"])
        assert replay["replayed"] is True
        with pytest.raises(DurableError) as mismatched:
            node.journal.attest(command["command_id"],"f"*64,
                command["route_generation"],task_id)
        assert mismatched.value.code=="COMMAND_PROOF_IDENTITY_MISMATCH"
    asyncio.run(run())


def test_r5_evidence_manifest_hashes_untracked_without_git_mutation(make_gateway,tmp_path):
    import hashlib
    async def run():
        g=make_gateway()
        root=tmp_path/"node-r5-manifest";root.mkdir()
        init_git_repo(root/"repo","R5MANIFEST")
        node,dev=await pair_node(g,root,tmp_path/"rt-r5-manifest","manifest")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-r5-manifest",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-r5-manifest",project["project_id"],"manifest"))
        agent=await g.multi.agent_register("a-r5-manifest","test","manifest",[])
        await drive(g,node,g.routing.task_claim_or_local(
            "claim-r5-manifest",task["task_id"],agent["agent_id"],agent["session_id"]))
        worktree=node.worktrees.execution_root(task["task_id"])
        content=b'{"evidence":"frozen"}\n'
        evidence=worktree/"STAGEG_EVIDENCE.json"
        evidence.write_bytes(content)
        # Ignore one real evidence file: it MUST still be hashed and retained.
        ignore_rule=worktree/".gitignore"
        ignore_rule.write_bytes(b"*.secret\n")
        ignored=worktree/"IGNORED.secret"
        ignored_content=b"ignored-binary-science-evidence"
        ignored.write_bytes(ignored_content)
        pre=evidence.read_bytes()
        manifest=node.worktrees.evidence_manifest(task["task_id"])
        assert manifest["cleanup_authorized"] is False
        assert manifest["read_only"] is True
        assert manifest["clean"] is False
        assert manifest["tracked_dirty"] is False
        assert manifest["untracked_files"]==[
            {"path":".gitignore","bytes":len(b"*.secret\n"),
             "sha256":hashlib.sha256(b"*.secret\n").hexdigest()},
            {"path":"IGNORED.secret","bytes":len(ignored_content),
             "sha256":hashlib.sha256(ignored_content).hexdigest()},
            {"path":"STAGEG_EVIDENCE.json","bytes":len(pre),
             "sha256":hashlib.sha256(pre).hexdigest()},
        ]
        frozen=await drive(g,node,g.routing.task_r5_evidence_manifest_or_local(
            "r5-manifest",task["task_id"]))
        assert frozen["manifest"]["manifest_sha256"]==manifest["manifest_sha256"]
        assert frozen["cleanup_authorized"] is False
        assert evidence.read_bytes()==pre
        assert (worktree/"STAGEG_EVIDENCE.json").exists()
    asyncio.run(run())


def test_r5_exact_proxy_node_mapping_from_terminal_submit_journal(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"node-r5-mapping";root.mkdir()
        init_git_repo(root/"repo","MAPPING")
        node,dev=await pair_node(g,root,tmp_path/"rt-r5-mapping","mapping")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-r5-mapping",dev["device_id"],"repo",2))
        task=await drive(g,node,g.routing.task_create_or_local(
            "t-r5-mapping",project["project_id"],"proof"))
        tid=task["task_id"]
        target,_=g.routing.commands.create(
            dev["device_id"],"JOB_SUBMIT",
            {"task_id":tid,"proxy_job_id":"rjob_exact_fixture","argv":["noop"],"cwd":"."},
            project_id=project["project_id"],task_id=tid,
        )
        envelope=g.routing.commands.envelope(target)
        node.journal.receive(envelope)
        node.journal.terminal(target["command_id"],"SUCCEEDED",result={
            "proxy_job_id":"rjob_exact_fixture","node_job_id":"job_exact_fixture",
            "state":"QUEUED",
        })
        proof=node.journal.attest(target["command_id"],target["request_hash"],
            target["route_generation"],tid)
        assert proof["exact_routed_job_mapping"]=={
            "proxy_job_id":"rjob_exact_fixture","node_job_id":"job_exact_fixture",
            "submitted_state":"QUEUED","task_id":tid,
            "project_id":project["project_id"],"mapping_only":True,
            "job_execution_not_repeated":True,
        }
        assert node.journal.get(target["command_id"])["state"]=="SUCCEEDED"
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='CANCELLED',delivery_attempt=1,"
                "error_code='DEVICE_COMMAND_EXPIRED' WHERE command_id=?",
                (target["command_id"],),
            )
        frozen=await drive(g,node,g.routing.task_r5_command_attestation_or_local(
            "r5-attest-exact-mapping",tid,target["command_id"]))
        assert frozen["node_receipt"]["exact_routed_job_mapping"]["node_job_id"]=="job_exact_fixture"
        assert frozen["job_rerun_authorized"] is False
        assert g.routing.commands.get(target["command_id"])["state"]=="CANCELLED"
        # Wrong proxy in terminal JSON must never substitute for an exact map.
        fake=dict(envelope,command_id="cmd_bad_mapping",request_hash="hash_bad_mapping")
        node.journal.receive(fake)
        node.journal.terminal("cmd_bad_mapping","SUCCEEDED",result={
            "proxy_job_id":"rjob_wrong","node_job_id":"job_wrong","state":"QUEUED",
        })
        assert node.journal.attest("cmd_bad_mapping","hash_bad_mapping",
            target["route_generation"],tid)["exact_routed_job_mapping"] is None
    asyncio.run(run())


def test_exact_gateway_command_receipts_are_scoped_and_no_dispatch(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        root=tmp_path/"gateway-exact-commands";root.mkdir()
        init_git_repo(root/"repo","EXACT")
        node,dev=await pair_node(g,root,tmp_path/"rt-exact","exact")
        project=await drive(g,node,g.routing.project_register_on_device(
            "p-exact",dev["device_id"],"repo",2))
        t1=await drive(g,node,g.routing.task_create_or_local(
            "t-exact-1",project["project_id"],"scope-a"))
        t2=await drive(g,node,g.routing.task_create_or_local(
            "t-exact-2",project["project_id"],"scope-b"))
        row1,_=g.routing.commands.create(
            dev["device_id"],"JOB_SUBMIT",
            {"proxy_job_id":"rjob_existing_a","task_id":t1["task_id"]},
            task_id=t1["task_id"],project_id=project["project_id"],
        )
        row2,_=g.routing.commands.create(
            dev["device_id"],"JOB_SUBMIT",
            {"proxy_job_id":"rjob_existing_b","task_id":t2["task_id"]},
            task_id=t2["task_id"],project_id=project["project_id"],
        )
        with g.durable.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='LEASED',delivery_attempt=1 "
                "WHERE command_id=?",(row1["command_id"],),
            )
        prior=g.routing.commands.get(row1["command_id"])
        a=g.routing.task_exact_command_receipts_or_local(
            t1["task_id"],[row1["command_id"],row2["command_id"]],
        )
        assert a["read_only"] is True
        assert a["command_polled"] is False
        assert a["science_job_rerun_authorized"] is False
        assert a["node_upgrade_authorized"] is False
        assert a["receipts"][0]["command_id"]==row1["command_id"]
        assert a["receipts"][0]["request_hash"]==row1["request_hash"]
        assert a["receipts"][0]["delivery_attempt"]==1
        assert a["receipts"][0]["state"]=="LEASED"
        assert a["receipts"][0]["proxy_job_id_in_payload"]=="rjob_existing_a"
        assert a["receipts"][1]["state"]=="NOT_FOUND_OR_NOT_OWNED"
        assert g.routing.commands.get(row1["command_id"])==prior
        trace=g.routing.task_dispatch_diagnostic_or_local(t1["task_id"])
        assert any(
            x["command_id"]==row1["command_id"]
            and x["request_hash"]==row1["request_hash"]
            for x in trace["task_command_trace"]
        )
        with pytest.raises(DurableError) as e:
            g.routing.task_exact_command_receipts_or_local(
                t1["task_id"],[row1["command_id"],row1["command_id"]],
            )
        assert e.value.code=="INVALID_ARGUMENT"
        with pytest.raises(DurableError) as e:
            g.routing.task_exact_command_receipts_or_local(
                "tsk_unbound", [row1["command_id"]],
            )
        assert e.value.code=="DEVICE_CONTEXT_REQUIRED"
    asyncio.run(run())
