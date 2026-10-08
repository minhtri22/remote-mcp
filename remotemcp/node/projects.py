from __future__ import annotations

import secrets
import shutil
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


    @staticmethod
    def _git_run(argv:list[str],*,cwd:Path|None=None,check:bool=True):
        cp=subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            shell=False,
        )
        if check and cp.returncode!=0:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_FAILED",
                (cp.stderr or cp.stdout or "git command failed").strip(),
            )
        return cp

    @staticmethod
    def _exact_sha(value:str)->str:
        sha=str(value or "").strip().lower()
        if len(sha)!=40 or any(ch not in "0123456789abcdef" for ch in sha):
            raise DurableError(
                "INVALID_ARGUMENT",
                "expected_commit_sha must be an exact 40-character Git SHA",
            )
        return sha

    @staticmethod
    def _safe_git_text(value:str,label:str)->str:
        out=str(value or "").strip()
        if not out or len(out)>4096 or any(ch in out for ch in ("\\x00","\\r","\\n")):
            raise DurableError("INVALID_ARGUMENT",f"{label} is invalid")
        if label=="remote_url" and "://" in out:
            authority=out.split("://",1)[1].split("/",1)[0]
            if "@" in authority:
                raise DurableError(
                    "INVALID_ARGUMENT",
                    "remote_url must not embed credentials",
                )
        return out

    def _git_identity(self,p:Path)->tuple[str|None,str|None]:
        hp=self._git_run(["git","-C",str(p),"rev-parse","HEAD"],check=False)
        rp=self._git_run(["git","-C",str(p),"remote","get-url","origin"],check=False)
        head=hp.stdout.strip().lower() if hp.returncode==0 and hp.stdout.strip() else None
        remote=rp.stdout.strip() if rp.returncode==0 and rp.stdout.strip() else None
        return head,remote

    def materialize_git(
        self,
        project_id:str,
        binding_generation:int,
        remote_url:str,
        remote_ref:str,
        expected_commit_sha:str,
    )->dict:
        remote_url=self._safe_git_text(remote_url,"remote_url")
        remote_ref=self._safe_git_text(remote_ref,"remote_ref")
        expected=self._exact_sha(expected_commit_sha)
        row=self.get(project_id)
        if int(row["binding_generation"])!=int(binding_generation):
            raise DurableError(
                "PROJECT_DEVICE_BINDING_CONFLICT",
                "binding generation changed before Git materialization",
            )
        p=self.safe(row["root_rel"])
        if p==self.root:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_FORBIDDEN",
                "node root itself cannot be converted from bootstrap to a Git project",
            )

        actual=self.probe(row["root_rel"])
        if row["project_kind"]=="GIT":
            head,remote=self._git_identity(p)
            if actual["project_kind"]=="GIT" and head==expected and remote==remote_url:
                return {
                    "project_id":project_id,
                    "binding_generation":int(binding_generation),
                    "root_rel":actual["root_rel"],
                    "project_kind":"GIT",
                    "head_commit":head,
                    "remote_url":remote,
                    "materialized":False,
                    "replayed":True,
                }
            raise DurableError(
                "PROJECT_DEVICE_BINDING_CONFLICT",
                "project is already Git but does not match the requested exact source",
            )
        if row["project_kind"]!="NON_GIT" or actual["project_kind"]!="NON_GIT":
            raise DurableError(
                "PROJECT_DEVICE_BINDING_CONFLICT",
                "only a bound NON_GIT bootstrap project can be materialized",
            )

        task_count=self.db.query_one(
            "SELECT COUNT(*) AS n FROM node_tasks WHERE project_id=?",
            (project_id,),
        )
        if task_count is not None and int(task_count["n"])!=0:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_HAS_TASKS",
                "NON_GIT project already has task state; semantics cannot change in place",
            )

        entries=list(p.iterdir())
        unexpected=[x.name for x in entries if x.name!=".remotemcp-bootstrap"]
        if unexpected:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_ROOT_NOT_PRISTINE",
                "bootstrap project root contains unexpected content",
                unexpected_entries=sorted(unexpected)[:32],
            )

        token=secrets.token_hex(8)
        stage=p.parent/f".{p.name}.remotemcp-git-stage-{token}"
        backup=p.parent/f".{p.name}.remotemcp-bootstrap-backup-{token}"
        if stage.exists() or backup.exists():
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_FAILED",
                "staging path collision",
            )

        cutover=False
        try:
            stage.mkdir()
            self._git_run(["git","init",str(stage)])
            self._git_run(["git","-C",str(stage),"remote","add","origin",remote_url])
            self._git_run(["git","-C",str(stage),"fetch","--no-tags","origin",remote_ref])
            fetched=self._git_run(
                ["git","-C",str(stage),"rev-parse","--verify","FETCH_HEAD^{commit}"]
            ).stdout.strip().lower()
            if fetched!=expected:
                raise DurableError(
                    "PROJECT_GIT_MATERIALIZATION_COMMIT_MISMATCH",
                    "remote_ref did not resolve to expected_commit_sha",
                    expected_commit_sha=expected,
                    observed_commit_sha=fetched,
                )
            self._git_run(["git","-C",str(stage),"checkout","--detach",expected])
            staged_head,staged_remote=self._git_identity(stage)
            if staged_head!=expected or staged_remote!=remote_url:
                raise DurableError(
                    "PROJECT_GIT_MATERIALIZATION_FAILED",
                    "staged checkout failed exact identity verification",
                )

            p.rename(backup)
            try:
                stage.rename(p)
                cutover=True
                final=self.probe(row["root_rel"])
                head,remote=self._git_identity(p)
                if final["project_kind"]!="GIT" or head!=expected or remote!=remote_url:
                    raise DurableError(
                        "PROJECT_GIT_MATERIALIZATION_FAILED",
                        "post-cutover Git identity verification failed",
                    )
                with self.db.transaction() as con:
                    current=con.execute(
                        "SELECT * FROM node_projects WHERE project_id=?",
                        (project_id,),
                    ).fetchone()
                    if (
                        current is None
                        or int(current["binding_generation"])!=int(binding_generation)
                        or current["root_rel"]!=row["root_rel"]
                        or current["project_kind"]!="NON_GIT"
                    ):
                        raise DurableError(
                            "PROJECT_DEVICE_BINDING_CONFLICT",
                            "node project binding changed during Git materialization",
                        )
                    con.execute(
                        "UPDATE node_projects SET project_kind='GIT',updated_at_ms=? WHERE project_id=?",
                        (now_ms(),project_id),
                    )
                shutil.rmtree(backup,ignore_errors=False)
                return {
                    "project_id":project_id,
                    "binding_generation":int(binding_generation),
                    "root_rel":final["root_rel"],
                    "project_kind":"GIT",
                    "head_commit":head,
                    "remote_url":remote,
                    "remote_ref":remote_ref,
                    "materialized":True,
                    "bootstrap_consumed":True,
                }
            except Exception:
                if cutover and p.exists():
                    p.rename(stage)
                if backup.exists() and not p.exists():
                    backup.rename(p)
                raise
        finally:
            if stage.exists():
                shutil.rmtree(stage,ignore_errors=True)
            if backup.exists() and p.exists():
                shutil.rmtree(backup,ignore_errors=True)

    def get(self,project_id:str):
        row=self.db.query_one("SELECT * FROM node_projects WHERE project_id=?",(project_id,))
        if row is None:raise DurableError("NOT_FOUND","node project binding missing")
        return row

    def path(self,project_id:str)->Path:
        row=self.get(project_id)
        return self.safe(row["root_rel"])
