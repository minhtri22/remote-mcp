from __future__ import annotations

import asyncio,sys

from remotemcp.node.db import NodeDatabase
from remotemcp.node.projects import NodeProjects
from remotemcp.node.worktrees import NodeWorktrees
from remotemcp.node.jobs import NodeJobs
from remotemcp.durable.models import now_ms


def test_node_job_replay_recovers_same_local_job_after_mapping_loss(tmp_path):
    async def run():
        root=tmp_path/"root";proj=root/"p";proj.mkdir(parents=True);rt=tmp_path/"rt"
        db=NodeDatabase(rt);db.bootstrap();projects=NodeProjects(db,root);projects.bind("prj",1,"p","NON_GIT")
        with db.transaction() as con:
            t=now_ms();con.execute("insert into node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) values('tsk','prj',1,NULL,NULL,'ACTIVE',?,?)",(t,t))
        wt=NodeWorktrees(db,root,projects);jobs=NodeJobs(db,root,rt,"dev_x",wt)
        payload={"proxy_job_id":"rjob_x","task_id":"tsk","project_id":"prj","argv":[sys.executable,"-c","print('ok')"],"cwd":"."}
        first=await jobs.submit("cmd_x",payload)
        with db.transaction() as con:con.execute("delete from node_routed_jobs where proxy_job_id='rjob_x'")
        second=await jobs.submit("cmd_x",payload)
        assert first["node_job_id"]==second["node_job_id"]
        await jobs.durable.start()
        try:
            for _ in range(200):
                st=jobs.durable.job_get(first["node_job_id"])
                if st["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:break
                await asyncio.sleep(.02)
            assert jobs.durable.job_get(first["node_job_id"])["state"]=="SUCCEEDED"
        finally:await jobs.durable.stop()
    asyncio.run(run())
