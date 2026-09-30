from __future__ import annotations

from pathlib import Path

from remotemcp.durable.errors import DurableError


class TaskJobService:
    def __init__(self,db,durable,leases,projects,tasks,worktrees,execution_root_resolver):
        self.db=db; self.durable=durable; self.leases=leases
        self.projects=projects; self.tasks=tasks; self.worktrees=worktrees
        self._root=execution_root_resolver

    async def submit(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,argv:list[str],cwd:str=".")->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        task=self.tasks.get(task_id)
        project=self.projects.get(task["project_id"])
        root=self._root(task_id)
        if project["project_kind"]=="GIT":
            self.worktrees.validate(self.projects.root_path(project),task)
        else:
            if not self.leases.has_covering(task_id,".",require_root_tree=True):
                raise DurableError("LEASE_REQUIRED","NON_GIT task job requires project-root TREE lease")
        target=(root/cwd).resolve()
        if target!=root and not target.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","job cwd escapes task root")
        rel_to_workspace=target.relative_to(self.durable.config.workspace_root).as_posix()
        return await self.durable.job_submit(
            operation_id,argv,rel_to_workspace,
            agent_id=lease["agent_id"],project_id=task["project_id"],task_id=task_id,
        )

    def list(self,task_id:str)->dict:
        self.tasks.get(task_id)
        return {"task_id":task_id,"jobs":self.tasks.jobs(task_id)}

    async def cancel(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,job_id:str)->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        row=self.db.query_one(
            "SELECT o.task_id,o.project_id FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
            (job_id,),
        )
        if row is None: raise DurableError("NOT_FOUND","job not found")
        if row["task_id"]!=task_id:
            raise DurableError("FORBIDDEN","job does not belong to task")
        return await self.durable.job_cancel(
            operation_id,job_id,agent_id=lease["agent_id"],project_id=row["project_id"],task_id=task_id
        )