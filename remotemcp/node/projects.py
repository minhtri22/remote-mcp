from __future__ import annotations

import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


class NodeProjects:
    def __init__(self,db,root:Path):
        self.db=db;self.root=root.resolve()

    def safe(self,path:str)->Path:
        p=(self.root/path).resolve()
        if p!=self.root and not p.is_relative_to(self.root):
            raise DurableError("PATH_ESCAPE","node project path escapes MCP_NODE_ROOT")
        if not p.is_dir():
            raise DurableError("NOT_FOUND","node project path does not exist",path=path)
        return p

    def probe(self,path:str)->dict:
        p=self.safe(path)
        rel=p.relative_to(self.root).as_posix() if p!=self.root else "."
        cp=subprocess.run(["git","-C",str(p),"rev-parse","--show-toplevel"],capture_output=True,text=True,shell=False)
        kind="GIT" if cp.returncode==0 else "NON_GIT"
        head=None
        if kind=="GIT":
            hp=subprocess.run(["git","-C",str(p),"rev-parse","HEAD"],capture_output=True,text=True,shell=False)
            if hp.returncode==0:head=hp.stdout.strip()
        return {"root_rel":rel,"project_kind":kind,"head_commit":head}

    def bind(self,project_id:str,binding_generation:int,root_rel:str,project_kind:str)->dict:
        p=self.safe(root_rel)
        actual=self.probe(root_rel)
        if actual["project_kind"]!=project_kind:
            raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","project kind changed")
        t=now_ms()
        with self.db.transaction() as con:
            old=con.execute("SELECT * FROM node_projects WHERE project_id=?",(project_id,)).fetchone()
            if old:
                if old["root_rel"]!=actual["root_rel"] or old["project_kind"]!=project_kind or int(old["binding_generation"])!=int(binding_generation):
                    raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","node project binding conflict")
            else:
                con.execute(
                    "INSERT INTO node_projects(project_id,binding_generation,root_rel,project_kind,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?)",
                    (project_id,int(binding_generation),actual["root_rel"],project_kind,t,t),
                )
        return {"project_id":project_id,"binding_generation":int(binding_generation),"root_rel":actual["root_rel"],"project_kind":project_kind}

    def get(self,project_id:str):
        row=self.db.query_one("SELECT * FROM node_projects WHERE project_id=?",(project_id,))
        if row is None:raise DurableError("NOT_FOUND","node project binding missing")
        return row

    def path(self,project_id:str)->Path:
        row=self.get(project_id)
        return self.safe(row["root_rel"])
