from __future__ import annotations

import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.workspace_layout import classify_task_worktree_rel


class WorktreeManager:
    def __init__(self,workspace_root:Path):
        self.workspace_root=workspace_root.resolve()

    def _run(self,*argv:str,cwd:Path|None=None,check:bool=True)->subprocess.CompletedProcess:
        cp=subprocess.run(list(argv),cwd=str(cwd) if cwd else None,capture_output=True,text=True,shell=False)
        if check and cp.returncode!=0:
            raise DurableError("WORKTREE_CONFLICT",(cp.stderr or cp.stdout or "git failed").strip())
        return cp

    @staticmethod
    def _branch_candidate(base_ref:str)->str|None:
        ref=str(base_ref or "").strip()
        if not ref or any(ch in ref for ch in ("\x00","\r","\n")):
            return None
        if ref.startswith("refs/heads/"):
            return ref[len("refs/heads/"):]
        if ref.startswith("refs/remotes/origin/"):
            return ref[len("refs/remotes/origin/"):]
        if ref.startswith("origin/"):
            return ref[len("origin/"):]
        if ref.startswith("refs/"):
            return None
        if len(ref)==40 and all(ch in "0123456789abcdefABCDEF" for ch in ref):
            return None
        return ref

    def resolve_base(self,project_root:Path,base_ref:str)->str:
        """Resolve and locally materialize an immutable base commit.

        Local refs are preferred.  If a branch exists only on origin, fetch the
        exact remote branch into refs/remotes/origin/<branch> and pin the fetched
        commit.  Never return a nullable/ambiguous base.
        """
        ref=str(base_ref or "").strip()
        if not ref or len(ref)>1024 or any(ch in ref for ch in ("\x00","\r","\n")):
            raise DurableError("TASK_BASE_REF_UNRESOLVED","invalid task base_ref")

        local=self._run(
            "git","-C",str(project_root),"rev-parse","--verify",f"{ref}^{{commit}}",
            check=False,
        )
        if local.returncode==0 and local.stdout.strip():
            return local.stdout.strip()

        branch=self._branch_candidate(ref)
        if branch:
            remote_ref=f"refs/heads/{branch}"
            advertised=self._run(
                "git","-C",str(project_root),"ls-remote","--exit-code","origin",remote_ref,
                check=False,
            )
            if advertised.returncode==0 and advertised.stdout.strip():
                advertised_sha=advertised.stdout.split()[0].strip()
                fetch=self._run(
                    "git","-C",str(project_root),"fetch","--no-tags","origin",
                    f"{remote_ref}:refs/remotes/origin/{branch}",
                    check=False,
                )
                if fetch.returncode!=0:
                    raise DurableError(
                        "TASK_BASE_REF_UNRESOLVED",
                        (fetch.stderr or fetch.stdout or "failed to fetch remote task base").strip(),
                        base_ref=ref,
                    )
                fetched=self._run(
                    "git","-C",str(project_root),"rev-parse","--verify",
                    f"refs/remotes/origin/{branch}^{{commit}}",
                    check=False,
                )
                if fetched.returncode==0 and fetched.stdout.strip():
                    commit=fetched.stdout.strip()
                    if commit!=advertised_sha:
                        raise DurableError(
                            "TASK_BASE_REF_CHANGED_DURING_RESOLUTION",
                            "remote task base changed while being resolved",
                            base_ref=ref,
                            advertised_commit=advertised_sha,
                            fetched_commit=commit,
                        )
                    return commit

        detail=(local.stderr or local.stdout or "task base ref not found").strip()
        raise DurableError(
            "TASK_BASE_REF_UNRESOLVED",
            detail,
            base_ref=ref,
        )

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
        classify_task_worktree_rel(
            str(task["project_id"]),str(task["task_id"]),str(task["worktree_rel"])
        )
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
        classify_task_worktree_rel(
            str(task["project_id"]),str(task["task_id"]),str(task["worktree_rel"])
        )
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