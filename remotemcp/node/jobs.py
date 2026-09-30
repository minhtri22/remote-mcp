from __future__ import annotations

from pathlib import Path

from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.durable.service import DurableService


class NodeJobs:
    def __init__(self,*args):
        # Runtime form:
        #   NodeJobs(config, db, identity, projects, worktrees)
        # Direct qualification form:
        #   NodeJobs(db, root, runtime_dir, device_id, worktrees)
        if len(args)!=5:
            raise TypeError("NodeJobs expects 5 arguments")
        if hasattr(args[0],"runtime_dir") and hasattr(args[0],"root"):
            config,db,identity,projects,worktrees=args
            self.config=config
            self.db=db
            self.device_id=identity.device["device_id"] if identity.device else ""
            self.worktrees=worktrees
        else:
            db,root,runtime_dir,device_id,worktrees=args
            class _Cfg: pass
            config=_Cfg()
            config.root=Path(root).resolve()
            config.runtime_dir=Path(runtime_dir).resolve()
            self.config=config
            self.db=db
            self.device_id=str(device_id)
            self.worktrees=worktrees
        self.durable=DurableService(
            DurableConfig(
                workspace_root=self.config.root,
                runtime_dir=self.config.runtime_dir/"durable",
                allowed_cmds=DEFAULT_DURABLE_ALLOWED_CMDS,
                max_parallel_jobs=4,
                poll_ms=20,
                starting_grace_seconds=2,
            )
        )

    async def start(self):
        await self.durable.start()
        self.reconcile_all()

    async def stop(self):
        await self.durable.stop()

    def _cwd(self,task_id:str,cwd:str)->tuple[Path,str,str]:
        task=self.worktrees.task(task_id)
        project_id=task["project_id"]
        root=self.worktrees.execution_root(task_id).resolve()
        target=(root/cwd).resolve()
        if target!=root and not target.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","node job cwd escapes task root")
        rel=target.relative_to(self.config.root).as_posix() if target!=self.config.root else "."
        return target,rel,project_id

    async def submit(self,*args)->dict:
        if len(args)==2:
            command_id,payload=args
            proxy_job_id=str(payload["proxy_job_id"])
        elif len(args)==3:
            proxy_job_id,command_id,payload=args
        else:
            raise TypeError("submit expects (command_id,payload) or (proxy_job_id,command_id,payload)")
        task_id=str(payload["task_id"])
        argv=payload.get("argv")
        cwd=str(payload.get("cwd","."))
        if not isinstance(argv,list) or not argv:
            raise DurableError("INVALID_ARGUMENT","routed job argv must be non-empty list")
        _,rel,project_id=self._cwd(task_id,cwd)
        supplied_project=payload.get("project_id")
        if supplied_project is not None and str(supplied_project)!=project_id:
            raise DurableError("COMMAND_CONFLICT","routed job project mismatch")
        operation_id=f"v2bd-node-job:{self.device_id}:{command_id}"
        result=await self.durable.job_submit(
            operation_id,argv,rel,
            project_id=project_id,task_id=task_id,
            env_overrides={
                "REMOTEMCP_DEVICE_ID":self.device_id,
                "REMOTEMCP_PROJECT_ID":project_id,
            },
        )
        node_job_id=result["job_id"]
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM node_routed_jobs WHERE proxy_job_id=?",(proxy_job_id,)).fetchone()
            if row:
                if row["node_job_id"]!=node_job_id or row["task_id"]!=task_id or row["project_id"]!=project_id:
                    raise DurableError("COMMAND_CONFLICT","proxy routed-job mapping mismatch")
            else:
                con.execute(
                    "INSERT INTO node_routed_jobs(proxy_job_id,node_job_id,task_id,project_id,state,created_at_ms,updated_at_ms) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (proxy_job_id,node_job_id,task_id,project_id,result["state"],t,t),
                )
        return {"proxy_job_id":proxy_job_id,"node_job_id":node_job_id,"state":result["state"]}

    def _row(self,proxy_job_id:str):
        row=self.db.query_one("SELECT * FROM node_routed_jobs WHERE proxy_job_id=?",(proxy_job_id,))
        if row is None:raise DurableError("NOT_FOUND","node routed job not found")
        return row

    def get(self,proxy_job_id:str)->dict:
        row=self._row(proxy_job_id)
        state=self.durable.job_get(row["node_job_id"])
        self._update(row["proxy_job_id"],state["state"])
        return {
            "proxy_job_id":row["proxy_job_id"],"node_job_id":row["node_job_id"],
            "task_id":row["task_id"],"project_id":row["project_id"],
            **state,
        }

    def logs(self,proxy_job_id:str,stream:str,cursor:int,max_bytes:int)->dict:
        row=self._row(proxy_job_id)
        out=self.durable.job_logs(row["node_job_id"],stream,cursor,max_bytes)
        out["proxy_job_id"]=proxy_job_id
        out["node_job_id"]=row["node_job_id"]
        return out

    def result(self,proxy_job_id:str)->dict:
        row=self._row(proxy_job_id)
        out=self.durable.job_result(row["node_job_id"])
        self._update(proxy_job_id,out["state"])
        out["proxy_job_id"]=proxy_job_id
        out["node_job_id"]=row["node_job_id"]
        return out

    async def cancel(self,proxy_job_id:str,command_id:str)->dict:
        row=self._row(proxy_job_id)
        op=f"v2bd-node-cancel:{self.device_id}:{command_id}"
        out=await self.durable.job_cancel(
            op,row["node_job_id"],project_id=row["project_id"],task_id=row["task_id"]
        )
        self._update(proxy_job_id,out["state"])
        return {
            "proxy_job_id":proxy_job_id,"node_job_id":row["node_job_id"],
            "state":out["state"],"ownership_verified":out["ownership_verified"],
        }

    def _update(self,proxy_job_id:str,state:str):
        t=now_ms(); terminal=state in {"SUCCEEDED","FAILED","CANCELLED","LOST"}
        with self.db.transaction() as con:
            con.execute(
                "UPDATE node_routed_jobs SET state=?,updated_at_ms=?,terminal_at_ms=CASE WHEN ? THEN COALESCE(terminal_at_ms,?) ELSE terminal_at_ms END WHERE proxy_job_id=?",
                (state,t,1 if terminal else 0,t,proxy_job_id),
            )

    def reconcile_all(self):
        for row in self.db.query_all("SELECT * FROM node_routed_jobs"):
            try:
                state=self.durable.job_get(row["node_job_id"])["state"]
                self._update(row["proxy_job_id"],state)
            except DurableError:
                pass
