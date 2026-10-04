from __future__ import annotations

import secrets
import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.workspace_layout import project_workspace_rel, project_worktrees_rel


class ProjectRepository:
    def __init__(self,db,workspace_root:Path):
        self.db=db
        self.workspace_root=workspace_root.resolve()

    def canonical(self,path:str)->Path:
        p=(self.workspace_root/path).resolve()
        if not p.is_relative_to(self.workspace_root):
            raise DurableError("PATH_ESCAPE","project path outside workspace")
        if not p.is_dir():
            raise DurableError("NOT_FOUND","project path does not exist",path=path)
        return p

    def _kind(self,p:Path)->str:
        cp=subprocess.run(
            ["git","-C",str(p),"rev-parse","--show-toplevel"],
            capture_output=True,text=True,shell=False,
        )
        return "GIT" if cp.returncode==0 else "NON_GIT"

    def register(self,path:str,max_active_tasks:int)->dict:
        if not 1<=int(max_active_tasks)<=32:
            raise DurableError("INVALID_ARGUMENT","max_active_tasks must be 1..32")
        p=self.canonical(path)
        rel=p.relative_to(self.workspace_root).as_posix()
        kind=self._kind(p)
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM projects WHERE root_rel=?",(rel,)).fetchone()
            if row is None:
                project_id="prj_"+secrets.token_hex(12)
                con.execute(
                    "INSERT INTO projects(project_id,root_rel,project_kind,max_active_tasks,created_at_ms,updated_at_ms) "
                    "VALUES(?,?,?,?,?,?)",
                    (project_id,rel,kind,int(max_active_tasks),t,t),
                )
            else:
                project_id=row["project_id"]
                if row["project_kind"]!=kind:
                    raise DurableError("PROJECT_CONFLICT","project kind changed")
                con.execute(
                    "UPDATE projects SET max_active_tasks=?,updated_at_ms=? WHERE project_id=?",
                    (int(max_active_tasks),t,project_id),
                )
        return self.status(project_id)

    def get(self,project_id:str):
        row=self.db.query_one("SELECT * FROM projects WHERE project_id=?",(project_id,))
        if row is None: raise DurableError("PROJECT_NOT_FOUND","project not found")
        return row

    def status(self,project_id:str)->dict:
        row=self.get(project_id)
        active=self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=? AND state IN ('CLAIMED','RUNNING')",
            (project_id,),
        )
        return {
            "project_id":project_id,"root_rel":row["root_rel"],"project_kind":row["project_kind"],
            "max_active_tasks":row["max_active_tasks"],"active_tasks":int(active["n"]),
            "workspace_rel":project_workspace_rel(project_id),
            "worktrees_root_rel":project_worktrees_rel(project_id),
        }

    def managed_mode(self)->bool:
        row=self.db.query_one("SELECT 1 AS x FROM projects LIMIT 1")
        return row is not None

    def root_path(self,row)->Path:
        return (self.workspace_root/row["root_rel"]).resolve()

    def covering_project_for_path(self,path:Path):
        p=path.resolve()
        for row in self.db.query_all("SELECT * FROM projects"):
            root=self.root_path(row)
            if p==root or p.is_relative_to(root):
                return row
        for task in self.db.query_all("SELECT t.*,p.root_rel FROM tasks t JOIN projects p ON p.project_id=t.project_id WHERE t.worktree_rel IS NOT NULL"):
            wt=(self.workspace_root/task["worktree_rel"]).resolve()
            if p==wt or p.is_relative_to(wt):
                return task
        return None