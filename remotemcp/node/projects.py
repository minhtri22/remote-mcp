from __future__ import annotations

import secrets
import shutil
import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


class NodeProjects:
    ROOT_PREFIX="@root/"

    def __init__(self,db,root:Path,legacy_roots=(),historical_root:Path|None=None):
        self.db=db
        self.root=root.resolve()
        self.legacy_roots=tuple(Path(p).resolve() for p in (legacy_roots or ()))
        self._roots={"CANONICAL":self.root}
        for i,p in enumerate(self.legacy_roots,1):
            self._roots[f"LEGACY{i}"]=p
        hist=Path(historical_root).resolve() if historical_root is not None else self.root
        self.historical_namespace="CANONICAL"
        for name,base in self._roots.items():
            if base==hist:
                self.historical_namespace=name
                break

    @staticmethod
    def _root_meta_key(project_id:str)->str:
        return f"project-root-namespace:{project_id}"

    def _decode(self,value:str,default_namespace:str)->tuple[str,str]:
        raw=str(value or "").replace("\\","/").strip()
        if raw.startswith(self.ROOT_PREFIX):
            tail=raw[len(self.ROOT_PREFIX):]
            parts=tail.split("/",1)
            if len(parts)!=2 or not parts[0] or not parts[1]:
                raise DurableError("INVALID_ARGUMENT","invalid encoded node root path",path=value)
            namespace,rel=parts
        else:
            namespace,rel=default_namespace,raw
        if namespace not in self._roots:
            raise DurableError(
                "PROJECT_ROOT_NAMESPACE_UNAVAILABLE",
                "project root namespace is not configured on this node",
                root_namespace=namespace,
            )
        return namespace,rel or "."

    def _encode(self,namespace:str,rel:str)->str:
        rel=str(rel or ".").replace("\\","/")
        return f"{self.ROOT_PREFIX}{namespace}/{rel}"

    def _namespace_for_project(self,project_id:str)->str:
        meta=self.db.get_meta(self._root_meta_key(project_id)) or {}
        namespace=str(meta.get("root_namespace") or "")
        if namespace:
            if namespace not in self._roots:
                raise DurableError(
                    "PROJECT_ROOT_NAMESPACE_UNAVAILABLE",
                    "stored project root namespace is not configured",
                    project_id=project_id,
                    root_namespace=namespace,
                )
            return namespace
        # Rows created before dual-root support belonged to the historical
        # paired root, not to a newly supplied canonical root.
        return self.historical_namespace

    def base(self,project_id:str)->Path:
        return self._roots[self._namespace_for_project(project_id)]

    def safe(self,path:str,default_namespace:str="CANONICAL")->Path:
        namespace,rel=self._decode(path,default_namespace)
        base=self._roots[namespace]
        p=(base/rel).resolve()
        if p!=base and not p.is_relative_to(base):
            raise DurableError("PATH_ESCAPE","node project path escapes approved root")
        if not p.is_dir():
            raise DurableError("NOT_FOUND","node project path does not exist",path=path)
        return p

    def _probe(self,path:str,default_namespace:str)->dict:
        namespace,rel_in=self._decode(path,default_namespace)
        base=self._roots[namespace]
        p=self.safe(path,default_namespace)
        rel=p.relative_to(base).as_posix() if p!=base else "."
        cp=subprocess.run(["git","-C",str(p),"rev-parse","--show-toplevel"],capture_output=True,text=True,shell=False)
        kind="GIT" if cp.returncode==0 else "NON_GIT"
        head=None
        if kind=="GIT":
            hp=subprocess.run(["git","-C",str(p),"rev-parse","HEAD"],capture_output=True,text=True,shell=False)
            if hp.returncode==0:head=hp.stdout.strip()
        return {
            "root_rel":self._encode(namespace,rel),
            "root_namespace":namespace,
            "root_base":str(base),
            "project_kind":kind,
            "head_commit":head,
        }

    def probe(self,path:str)->dict:
        # New registrations always target the canonical root. Legacy roots are
        # compatibility-only and are never selected implicitly for new work.
        return self._probe(path,"CANONICAL")

    def bind(self,project_id:str,binding_generation:int,root_rel:str,project_kind:str)->dict:
        old=self.db.query_one("SELECT * FROM node_projects WHERE project_id=?",(project_id,))
        default_namespace=self._namespace_for_project(project_id) if old else "CANONICAL"
        actual=self._probe(root_rel,default_namespace)
        if actual["project_kind"]!=project_kind:
            raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","project kind changed")
        if old:
            stored_namespace=self._namespace_for_project(project_id)
            if stored_namespace!=actual["root_namespace"]:
                raise DurableError(
                    "PROJECT_DEVICE_BINDING_CONFLICT",
                    "project root namespace changed",
                    project_id=project_id,
                    stored_root_namespace=stored_namespace,
                    requested_root_namespace=actual["root_namespace"],
                )
        t=now_ms()
        with self.db.transaction() as con:
            old=con.execute("SELECT * FROM node_projects WHERE project_id=?",(project_id,)).fetchone()
            if old:
                old_ns=stored_namespace
                _,old_rel=self._decode(old["root_rel"],old_ns)
                _,new_rel=self._decode(actual["root_rel"],actual["root_namespace"])
                if old_rel!=new_rel or old["project_kind"]!=project_kind or int(old["binding_generation"])!=int(binding_generation):
                    raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","node project binding conflict")
            else:
                con.execute(
                    "INSERT INTO node_projects(project_id,binding_generation,root_rel,project_kind,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?)",
                    (project_id,int(binding_generation),actual["root_rel"],project_kind,t,t),
                )
        self.db.set_meta(
            self._root_meta_key(project_id),
            {
                "root_namespace":actual["root_namespace"],
                "root_base":actual["root_base"],
                "bound_at_ms":t,
            },
        )
        return {
            "project_id":project_id,
            "binding_generation":int(binding_generation),
            "root_rel":actual["root_rel"],
            "root_namespace":actual["root_namespace"],
            "root_base":actual["root_base"],
            "project_kind":project_kind,
        }


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
        namespace=self._namespace_for_project(project_id)
        p=self.path(project_id)
        base=self._roots[namespace]
        if p==base:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_FORBIDDEN",
                "node root itself cannot be converted from bootstrap to a Git project",
            )

        actual=self._probe(row["root_rel"],namespace)
        if row["project_kind"]=="GIT":
            head,remote=self._git_identity(p)
            if actual["project_kind"]=="GIT" and head==expected and remote==remote_url:
                return {
                    "project_id":project_id,
                    "binding_generation":int(binding_generation),
                    "root_rel":row["root_rel"],
                    "root_namespace":namespace,
                    "root_base":str(base),
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
                final=self._probe(row["root_rel"],namespace)
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
                    "root_rel":row["root_rel"],
                    "root_namespace":namespace,
                    "root_base":str(base),
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
        namespace=self._namespace_for_project(project_id)
        return self.safe(row["root_rel"],namespace)

    def root_info(self,project_id:str)->dict:
        row=self.get(project_id)
        namespace=self._namespace_for_project(project_id)
        base=self._roots[namespace]
        _,rel=self._decode(row["root_rel"],namespace)
        return {
            "root_namespace":namespace,
            "root_base":str(base),
            "root_rel":rel,
            "encoded_root_rel":self._encode(namespace,rel),
            "legacy_compatibility":namespace!="CANONICAL",
        }
