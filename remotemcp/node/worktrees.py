from __future__ import annotations

from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.workspace_layout import classify_task_worktree_rel, project_workspace_rel
from remotemcp.multiagent.worktrees import WorktreeManager


class NodeWorktrees:
    def __init__(self,db,root:Path,projects):
        self.db=db;self.root=root.resolve();self.projects=projects
        self.manager=WorktreeManager(self.root)

    def ensure(self,payload:dict)->dict:
        task_id=str(payload["task_id"]);project_id=str(payload["project_id"])
        generation=int(payload["binding_generation"])
        project=self.projects.get(project_id)
        if int(project["binding_generation"])!=generation:
            raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","task binding generation mismatch")
        t=now_ms()
        if project["project_kind"]=="NON_GIT":
            worktree_rel=None;branch=None
        else:
            branch=str(payload["branch_name"])
            worktree_rel=str(payload["worktree_rel"])
            layout=classify_task_worktree_rel(project_id,task_id,worktree_rel)
            project_root=self.projects.path(project_id)
            base_ref=str(payload.get("base_ref") or "HEAD")
            base_commit=self.manager.resolve_base(project_root,base_ref)
            task={
                "task_id":task_id,
                "project_id":project_id,
                "worktree_rel":worktree_rel,
                "branch_name":branch,
                "base_commit":base_commit,
            }
            wt=self.manager.provision(project_root,task)
            worktree_rel=wt.relative_to(self.root).as_posix()
        with self.db.transaction() as con:
            old=con.execute("SELECT * FROM node_tasks WHERE task_id=?",(task_id,)).fetchone()
            if old:
                if old["project_id"]!=project_id or int(old["binding_generation"])!=generation:
                    raise DurableError("WORKTREE_IDENTITY_MISMATCH","node task binding mismatch")
                if project["project_kind"]=="GIT" and (old["worktree_rel"]!=worktree_rel or old["branch_name"]!=branch):
                    raise DurableError("WORKTREE_IDENTITY_MISMATCH","node worktree identity mismatch")
            else:
                con.execute(
                    "INSERT INTO node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) "
                    "VALUES(?,?,?,?,?,'ACTIVE',?,?)",
                    (task_id,project_id,generation,worktree_rel,branch,t,t),
                )
        result={"task_id":task_id,"project_id":project_id,"worktree_rel":worktree_rel,"branch_name":branch,"state":"ACTIVE"}
        if project["project_kind"]=="GIT":
            result["workspace_rel"]=project_workspace_rel(project_id)
            result["worktree_layout"]=layout
        return result

    def task(self,task_id:str):
        row=self.db.query_one("SELECT * FROM node_tasks WHERE task_id=?",(task_id,))
        if row is None:raise DurableError("NOT_FOUND","node task missing")
        return row

    def execution_root(self,task_id:str)->Path:
        task=self.task(task_id);project=self.projects.get(task["project_id"])
        if project["project_kind"]=="GIT":
            if not task["worktree_rel"]:raise DurableError("WORKTREE_IDENTITY_MISMATCH","node task worktree missing")
            p=(self.root/task["worktree_rel"]).resolve()
        else:
            p=self.projects.path(task["project_id"])
        if p!=self.root and not p.is_relative_to(self.root):
            raise DurableError("PATH_ESCAPE","node task root escapes")
        return p

    def status(self,task_id:str)->dict:
        task=self.task(task_id);project=self.projects.get(task["project_id"])
        root=self.execution_root(task_id)
        clean=True
        if project["project_kind"]=="GIT":
            clean=self.manager.clean(root)
        result={"task_id":task_id,"project_id":task["project_id"],"worktree_rel":task["worktree_rel"],"branch_name":task["branch_name"],"clean":clean}
        if project["project_kind"]=="GIT":
            result["workspace_rel"]=project_workspace_rel(task["project_id"])
            result["worktree_layout"]=classify_task_worktree_rel(
                str(task["project_id"]),str(task["task_id"]),str(task["worktree_rel"])
            )
        return result
