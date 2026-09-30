from __future__ import annotations

import asyncio
import subprocess

from conftest import init_git_repo


def test_two_tasks_same_git_project_get_distinct_worktrees(make_bundle):
    async def run():
        b=make_bundle()
        repo=init_git_repo(b.workspace/"repo")
        a=await b.multi.agent_register("ra","a","ia",[])
        z=await b.multi.agent_register("rz","z","iz",[])
        p=await b.multi.project_register("p","repo",4)
        t1=await b.multi.task_create("t1",p["project_id"],"one")
        t2=await b.multi.task_create("t2",p["project_id"],"two")
        c1=await b.multi.task_claim("c1",t1["task_id"],a["agent_id"],a["session_id"])
        c2=await b.multi.task_claim("c2",t2["task_id"],z["agent_id"],z["session_id"])
        s1=b.multi.task_status(t1["task_id"]); s2=b.multi.task_status(t2["task_id"])
        assert s1["worktree_rel"]!=s2["worktree_rel"]
        assert s1["branch_name"]!=s2["branch_name"]
        w1=b.multi.execution_root(t1["task_id"]); w2=b.multi.execution_root(t2["task_id"])
        assert w1.exists() and w2.exists() and w1!=w2
        assert subprocess.run(["git","-C",str(w1),"branch","--show-current"],capture_output=True,text=True).stdout.strip()==s1["branch_name"]
        assert subprocess.run(["git","-C",str(w2),"branch","--show-current"],capture_output=True,text=True).stdout.strip()==s2["branch_name"]
    asyncio.run(run())


def test_dirty_worktree_is_preserved(make_bundle):
    async def run():
        b=make_bundle()
        init_git_repo(b.workspace/"repo")
        a=await b.multi.agent_register("ra","a","ia",[])
        p=await b.multi.project_register("p","repo",4)
        t=await b.multi.task_create("t",p["project_id"],"one")
        c=await b.multi.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        wt=b.multi.execution_root(t["task_id"])
        (wt/"dirty.txt").write_text("dirty",encoding="utf-8")
        row=b.multi.tasks.get(t["task_id"]); project=b.multi.projects.get(p["project_id"])
        assert b.multi.worktrees.remove_if_safe(b.multi.projects.root_path(project),row,False) is False
        assert wt.exists()
    asyncio.run(run())