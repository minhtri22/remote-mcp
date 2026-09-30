from __future__ import annotations
import asyncio,json,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from remotemcp.durable.models import now_ms
from remotemcp.node.config import NodeConfig
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity
from remotemcp.node.projects import NodeProjects
from remotemcp.node.worktrees import NodeWorktrees
from remotemcp.node.jobs import NodeJobs

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-job-") as td:
        base=Path(td);root=base/"root";proj=root/"p";proj.mkdir(parents=True);rt=base/"rt"
        cfg=NodeConfig.create("http://127.0.0.1:9999",root,rt)
        db=NodeDatabase(rt);db.bootstrap()
        ident=NodeIdentity(rt,db)
        ident.persist_paired(
            {
                "device_id":"dev_restart",
                "route_generation":1,
                "paired_at_ms":now_ms(),
            },
            origin=cfg.origin,root=cfg.root,device_name="restart-node",
        )
        projects=NodeProjects(db,root);projects.bind("prj",1,"p","NON_GIT")
        with db.transaction() as con:
            t=now_ms()
            con.execute(
                "insert into node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) "
                "values('tsk','prj',1,NULL,NULL,'ACTIVE',?,?)",(t,t)
            )
        wt=NodeWorktrees(db,root,projects)
        payload={
            "proxy_job_id":"rjob_restart",
            "task_id":"tsk",
            "project_id":"prj",
            "argv":["python","-c","import time; print('before',flush=True); time.sleep(1.0); print('after',flush=True)"],
            "cwd":".",
        }

        j1=NodeJobs(cfg,db,ident,projects,wt)
        first=await j1.submit("rjob_restart","cmd_submit",payload)
        for _ in range(100):
            if j1.durable.job_get(first["node_job_id"])["state"]=="RUNNING": break
            await asyncio.sleep(.02)
        await j1.durable.stop()

        # New node-agent process view over the same durable subruntime.
        db2=NodeDatabase(rt);db2.bootstrap()
        ident2=NodeIdentity(rt,db2)
        projects2=NodeProjects(db2,root)
        wt2=NodeWorktrees(db2,root,projects2)
        j2=NodeJobs(cfg,db2,ident2,projects2,wt2)
        replay=await j2.submit("rjob_restart","cmd_submit",payload)
        assert replay["node_job_id"]==first["node_job_id"]

        await j2.durable.start()
        try:
            for _ in range(250):
                state=j2.durable.job_get(first["node_job_id"])["state"]
                if state in {"SUCCEEDED","FAILED","CANCELLED","LOST"}: break
                await asyncio.sleep(.02)
            state=j2.durable.job_get(first["node_job_id"])["state"]
            assert state=="SUCCEEDED",state
            logs=j2.logs("rjob_restart","stdout",0,65536)
            assert "before" in logs["data"] and "after" in logs["data"]
        finally:
            await j2.durable.stop()

        print(json.dumps({
            "verdict":"PASS",
            "proxy_job_id":"rjob_restart",
            "node_job_id":first["node_job_id"],
            "same_job_after_restart":True,
            "terminal":"SUCCEEDED"
        },indent=2))

if __name__=="__main__":
    asyncio.run(main())
