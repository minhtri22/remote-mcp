from __future__ import annotations

import base64
import hashlib
import json
import os
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

    @staticmethod
    def _artifact_meta_key(proxy_job_id:str)->str:
        return f"job-artifacts:{proxy_job_id}"

    def _normalize_evidence_paths(self,task_id:str,paths)->list[dict]:
        if paths is None:
            return []
        if not isinstance(paths,list):
            raise DurableError("INVALID_ARGUMENT","evidence_paths must be a list")
        if len(paths)>64:
            raise DurableError("INVALID_ARGUMENT","evidence_paths may contain at most 64 exact files")
        root=self.worktrees.execution_root(task_id).resolve()
        out=[]
        seen=set()
        for raw in paths:
            if not isinstance(raw,str) or not raw.strip() or len(raw)>4096:
                raise DurableError("INVALID_ARGUMENT","each evidence path must be a non-empty string")
            requested=raw
            p=Path(raw)
            if p.is_absolute():
                resolved=p.resolve()
                scope="ABSOLUTE_DECLARED"
            else:
                resolved=(root/p).resolve()
                if resolved!=root and not resolved.is_relative_to(root):
                    raise DurableError("PATH_ESCAPE","declared evidence path escapes task root")
                scope="TASK_RELATIVE"
            key=os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "requested":requested,
                "resolved":str(resolved),
                "scope":scope,
            })
        return out

    def _artifact_manifest(self,proxy_job_id:str)->dict:
        return self.db.get_meta(self._artifact_meta_key(proxy_job_id)) or {
            "schema":"remotemcp.job-artifact-manifest.v1",
            "proxy_job_id":proxy_job_id,
            "paths":[],
        }

    def _artifact_target(self,proxy_job_id:str,path:str)->tuple[dict,Path]:
        row=self._row(proxy_job_id)
        manifest=self._artifact_manifest(proxy_job_id)
        if not manifest.get("paths"):
            raise DurableError(
                "POSTRUN_EVIDENCE_PATH_UNDECLARED",
                "job has no predeclared evidence paths; scientific execution must not be rerun to repair readback",
                proxy_job_id=proxy_job_id,
            )
        p=Path(path)
        if p.is_absolute():
            resolved=p.resolve()
        else:
            resolved=(self.worktrees.execution_root(row["task_id"]).resolve()/p).resolve()
        key=os.path.normcase(str(resolved))
        for item in manifest.get("paths",[]):
            if os.path.normcase(str(item.get("resolved","")))==key:
                return item,resolved
        raise DurableError(
            "POSTRUN_EVIDENCE_PATH_UNDECLARED",
            "requested artifact was not declared before job execution",
            proxy_job_id=proxy_job_id,
            path=path,
        )

    def _require_terminal_artifact_job(self,proxy_job_id:str):
        row=self._row(proxy_job_id)
        state=self.durable.job_get(row["node_job_id"])["state"]
        self._update(proxy_job_id,state)
        if state not in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
            raise DurableError(
                "POSTRUN_EVIDENCE_JOB_NOT_TERMINAL",
                "artifact readback is available only after authoritative terminal job state",
                proxy_job_id=proxy_job_id,
                state=state,
            )
        return row,state

    @staticmethod
    def _sha256_file(path:Path)->str:
        h=hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk=fh.read(1024*1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def artifact_stat(self,proxy_job_id:str,path:str)->dict:
        row,state=self._require_terminal_artifact_job(proxy_job_id)
        declaration,target=self._artifact_target(proxy_job_id,path)
        if not target.is_file():
            raise DurableError(
                "POSTRUN_EVIDENCE_NOT_FOUND",
                "declared post-run evidence file does not exist",
                proxy_job_id=proxy_job_id,
                path=path,
            )
        size=target.stat().st_size
        digest=self._sha256_file(target)
        return {
            "proxy_job_id":proxy_job_id,
            "node_job_id":row["node_job_id"],
            "task_id":row["task_id"],
            "project_id":row["project_id"],
            "job_state":state,
            "path":path,
            "resolved_path":str(target),
            "declaration_scope":declaration.get("scope"),
            "size_bytes":size,
            "sha256":digest,
            "terminal_evidence_preserved":True,
            "scientific_rerun_required":False,
        }

    def artifact_read(
        self,proxy_job_id:str,path:str,expected_sha256:str,offset:int=0,limit:int=120000
    )->dict:
        row,state=self._require_terminal_artifact_job(proxy_job_id)
        declaration,target=self._artifact_target(proxy_job_id,path)
        if not target.is_file():
            raise DurableError(
                "POSTRUN_EVIDENCE_NOT_FOUND",
                "declared post-run evidence file does not exist",
                proxy_job_id=proxy_job_id,
                path=path,
            )
        size=target.stat().st_size
        actual=self._sha256_file(target)
        expected=str(expected_sha256 or "").lower()
        if len(expected)!=64 or expected!=actual:
            raise DurableError(
                "POSTRUN_EVIDENCE_HASH_MISMATCH",
                "artifact bytes do not match the stat-pinned SHA-256",
                proxy_job_id=proxy_job_id,
                path=path,
                expected_sha256=expected,
                actual_sha256=actual,
            )
        offset=max(0,int(offset))
        limit=max(1,min(int(limit),120000))
        with target.open("rb") as fh:
            fh.seek(offset)
            chunk=fh.read(limit)
        try:
            content=chunk.decode("utf-8")
            encoding="utf-8"
        except UnicodeDecodeError:
            content=None
            encoding="base64"
        return {
            "proxy_job_id":proxy_job_id,
            "node_job_id":row["node_job_id"],
            "task_id":row["task_id"],
            "project_id":row["project_id"],
            "job_state":state,
            "path":path,
            "resolved_path":str(target),
            "declaration_scope":declaration.get("scope"),
            "sha256":actual,
            "size_bytes":size,
            "offset":offset,
            "returned_bytes":len(chunk),
            "eof":offset+len(chunk)>=size,
            "encoding":encoding,
            "content":content,
            "data_b64":base64.b64encode(chunk).decode("ascii"),
            "terminal_evidence_preserved":True,
            "scientific_rerun_required":False,
        }

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
        evidence_paths=payload.get("evidence_paths",[])
        if not isinstance(argv,list) or not argv:
            raise DurableError("INVALID_ARGUMENT","routed job argv must be non-empty list")
        _,rel,project_id=self._cwd(task_id,cwd)
        evidence_manifest={
            "schema":"remotemcp.job-artifact-manifest.v1",
            "proxy_job_id":proxy_job_id,
            "task_id":task_id,
            "project_id":project_id,
            "paths":self._normalize_evidence_paths(task_id,evidence_paths),
        }
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
            con.execute(
                "INSERT INTO node_meta(key,value_json) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (
                    self._artifact_meta_key(proxy_job_id),
                    json.dumps(evidence_manifest,ensure_ascii=False,sort_keys=True,separators=(",",":")),
                ),
            )
        return {
            "proxy_job_id":proxy_job_id,"node_job_id":node_job_id,"state":result["state"],
            "declared_evidence_paths":len(evidence_manifest["paths"]),
        }

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

    def capacity_snapshot(self)->dict:
        """Reconcile node-routed rows against durable job truth before counting.

        Any row that cannot be reconciled is reported separately so callers can
        fail closed rather than treating a stale routed-row count as capacity.
        """
        terminal={"SUCCEEDED","FAILED","CANCELLED","LOST"}
        candidates=self.db.query_all(
            "SELECT * FROM node_routed_jobs "
            "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')"
        )
        active=0
        unresolved=0
        reconciled_terminal=0
        for row in candidates:
            try:
                state=self.durable.job_get(row["node_job_id"])["state"]
            except DurableError:
                unresolved+=1
                continue
            self._update(row["proxy_job_id"],state)
            if state in terminal:
                reconciled_terminal+=1
            else:
                active+=1
        return {
            "active_node_jobs":active,
            "unresolved_node_jobs":unresolved,
            "candidate_nonterminal_routed_jobs":len(candidates),
            "reconciled_terminal_rows":reconciled_terminal,
            "capacity_reconciliation_complete":unresolved==0,
        }

    def reconcile_all(self):
        for row in self.db.query_all("SELECT * FROM node_routed_jobs"):
            try:
                state=self.durable.job_get(row["node_job_id"])["state"]
                self._update(row["proxy_job_id"],state)
            except DurableError:
                pass
