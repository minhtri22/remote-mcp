from __future__ import annotations

import asyncio
from conftest import init_git_repo
from remotemcp.durable.models import now_ms


def test_claimed_git_task_reconciles_to_running_without_duplicate_worktree(make_bundle):
    async def run():
        b=make_bundle()
        init_git_repo(b.workspace/"repo")
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p","repo",4)
        t=await b.multi.task_create("tc",p["project_id"],"one")
        lease=b.multi.leases.claim_phase1(t["task_id"],a["agent_id"],a["session_id"])
        assert b.multi.task_status(t["task_id"])["state"]=="CLAIMED"
        b.multi.reconciler.reconcile_claimed()
        assert b.multi.task_status(t["task_id"])["state"]=="RUNNING"
        rows=b.multi.worktrees.list_worktrees(b.multi.projects.root_path(b.multi.projects.get(p["project_id"])))
        matches=[r for r in rows if str(r.get("branch","")).endswith(b.multi.task_status(t["task_id"])["branch_name"])]
        assert len(matches)==1
        b.multi.reconciler.reconcile_claimed()
        rows2=b.multi.worktrees.list_worktrees(b.multi.projects.root_path(b.multi.projects.get(p["project_id"])))
        assert len([r for r in rows2 if str(r.get("branch","")).endswith(b.multi.task_status(t["task_id"])["branch_name"])])==1
    asyncio.run(run())


def test_restart_expiry_marks_recoverable_without_touching_jobs_table(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p",".",4)
        t=await b.multi.task_create("tc",p["project_id"],"one")
        c=await b.multi.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        before=b.durable.db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"]
        with b.durable.db.transaction() as con:
            con.execute("UPDATE task_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
        b.multi.reconciler.reconcile_all()
        after=b.durable.db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"]
        assert before==after
        assert b.multi.task_status(t["task_id"])["state"]=="RECOVERABLE"
    asyncio.run(run())