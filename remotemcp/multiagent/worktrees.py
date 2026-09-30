from __future__ import annotations

import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError


class WorktreeManager:
    def __init__(self,workspace_root:Path):
        self.workspace_root=workspace_root.resolve()

    def _run(self,*argv:str,cwd:Path|None=None,check:bool=True)->subprocess.CompletedProcess:
        cp=subprocess.run(list(argv),cwd=str(cwd) if cwd else None,capture_output=True,text=True,shell=False)
        if check and cp.returncode!=0:
            raise DurableError("WORKTREE_CONFLICT",(cp.stderr or cp.stdout or "git failed").strip())
        return cp

    def resolve_base(self,project_root:Path,base_ref:str)->str:
        cp=self._run("git","-C",str(project_root),"rev-parse",f"{base_ref}^{{commit}}")
        return cp.stdout.strip()

    def list_worktrees(self,project_root:Path)->list[dict]:
        cp=self._run("git","-C",str(project_root),"worktree","list","--porcelain")
        out=[]; cur={}
        for line in cp.stdout.splitlines()+[""]:
            if not line:
                if cur: out.append(cur);cur={}
                continue
            if " " in line:
                k,v=line.split(" ",1);cur[k]=v
            else: cur[line]=True
        return out

    def provision(self,project_root:Path,task)->Path:
        wt=(self.workspace_root/task["worktree_rel"]).resolve()
        if not wt.is_relative_to(self.workspace_root):
            raise DurableError("PATH_ESCAPE","worktree outside workspace")
        branch=task["branch_name"]; base=task["base_commit"]
        registered=self.list_worktrees(project_root)
        for entry in registered:
            ep=Path(entry.get("worktree","")).resolve()
            br=str(entry.get("branch","")).removeprefix("refs/heads/")
            if ep==wt:
                if br!=branch:
                    raise DurableError("WORKTREE_IDENTITY_MISMATCH","expected worktree has wrong branch")
                return wt
            if br==branch and ep!=wt:
                raise DurableError("WORKTREE_CONFLICT","task branch is registered at another worktree")
        if wt.exists():
            raise DurableError("WORKTREE_CONFLICT","worktree path exists but is not registered")

        cp=self._run("git","-C",str(project_root),"show-ref","--verify",f"refs/heads/{branch}",check=False)
        wt.parent.mkdir(parents=True,exist_ok=True)
        if cp.returncode==0:
            self._run("git","-C",str(project_root),"worktree","add",str(wt),branch)
        else:
            self._run("git","-C",str(project_root),"worktree","add","-b",branch,str(wt),base)
        return self.validate(project_root,task)

    def validate(self,project_root:Path,task)->Path:
        wt=(self.workspace_root/task["worktree_rel"]).resolve()
        branch=task["branch_name"]
        for entry in self.list_worktrees(project_root):
            if Path(entry.get("worktree","")).resolve()==wt:
                br=str(entry.get("branch","")).removeprefix("refs/heads/")
                if br!=branch:
                    raise DurableError("WORKTREE_IDENTITY_MISMATCH","wrong branch")
                return wt
        raise DurableError("WORKTREE_IDENTITY_MISMATCH","expected worktree not registered")

    def clean(self,wt:Path)->bool:
        cp=self._run("git","-C",str(wt),"status","--porcelain")
        return cp.stdout.strip()==""

    def remove_if_safe(self,project_root:Path,task,has_jobs:bool)->bool:
        wt=self.validate(project_root,task)
        if has_jobs or not self.clean(wt):
            return False
        self._run("git","-C",str(project_root),"worktree","remove",str(wt))
        return True