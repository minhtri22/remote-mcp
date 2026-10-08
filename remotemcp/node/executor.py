from __future__ import annotations

import hashlib
from pathlib import Path

from remotemcp.durable.errors import DurableError


class NodeExecutor:
    def __init__(self,config,journal,projects,worktrees,cas,jobs):
        self.config=config; self.journal=journal; self.projects=projects
        self.worktrees=worktrees; self.cas=cas; self.jobs=jobs
        self.restart_requested=False

    def _task_root(self,task_id:str)->Path:
        return self.worktrees.execution_root(task_id).resolve()

    def _safe_task_path(self,task_id:str,path:str)->Path:
        root=self._task_root(task_id)
        p=(root/path).resolve()
        if p!=root and not p.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","task path escapes execution root")
        return p

    def _list_dir(self,payload):
        task_id=str(payload["task_id"]);path=str(payload.get("path","."))
        p=self._safe_task_path(task_id,path)
        if not p.is_dir():raise DurableError("NOT_FOUND","directory not found")
        return {"task_id":task_id,"path":path,"entries":[{"name":x.name,"is_dir":x.is_dir(),"size":x.stat().st_size if x.is_file() else None} for x in sorted(p.iterdir(),key=lambda x:x.name.lower())]}

    def _read_file(self,payload):
        task_id=str(payload["task_id"]);path=str(payload["path"])
        offset=max(0,int(payload.get("offset",0)));limit=max(1,min(int(payload.get("limit",120000)),120000))
        p=self._safe_task_path(task_id,path)
        if not p.is_file():raise DurableError("NOT_FOUND","file not found")
        raw=p.read_bytes()
        text=raw.decode("utf-8",errors="replace")
        data=text[offset:offset+limit]
        return {
            "task_id":task_id,"path":path,"offset":offset,
            "data":data,"content":data,
            "sha256":hashlib.sha256(raw).hexdigest(),
            "eof":offset+limit>=len(text),
        }

    def _search(self,payload):
        task_id=str(payload["task_id"]);pattern=str(payload["pattern"]);path=str(payload.get("path","."))
        base=self._safe_task_path(task_id,path)
        hits=[]
        files=[base] if base.is_file() else [x for x in base.rglob("*") if x.is_file()]
        for f in files:
            if len(hits)>=200:break
            try:
                text=f.read_text(encoding="utf-8")
            except Exception:
                continue
            for i,line in enumerate(text.splitlines(),1):
                if pattern in line:
                    hits.append({"path":f.relative_to(self._task_root(task_id)).as_posix(),"line":i,"text":line[:500]})
                    if len(hits)>=200:break
        return {"task_id":task_id,"pattern":pattern,"matches":hits,"truncated":len(hits)>=200}

    async def execute(self,envelope:dict)->dict:
        row,created=self.journal.receive(envelope)
        if not created and row["state"] in {"SUCCEEDED","FAILED","IN_DOUBT"}:
            return self.journal.response(row)
        self.journal.mark_executing(envelope["command_id"])
        t=envelope["command_type"];p=envelope["payload"]
        try:
            if t=="PROJECT_PROBE": result=self.projects.probe(str(p["path"]))
            elif t=="PROJECT_BIND": result=self.projects.bind(str(p["project_id"]),int(p["binding_generation"]),str(p["root_rel"]),str(p["project_kind"]))
            elif t=="TASK_BASE_RESOLVE": result=self.worktrees.resolve_base(p)
            elif t=="TASK_WORKTREE_ENSURE": result=self.worktrees.ensure(p)
            elif t=="TASK_WORKTREE_STATUS": result=self.worktrees.status(str(p["task_id"]))
            elif t=="TASK_LIST_DIR": result=self._list_dir(p)
            elif t=="TASK_READ_FILE": result=self._read_file(p)
            elif t=="TASK_SEARCH": result=self._search(p)
            elif t=="FILE_WRITE_CAS": result=self.cas.write(envelope["command_id"],p)
            elif t=="FILE_EDIT_CAS": result=self.cas.edit(envelope["command_id"],p)
            elif t=="JOB_SUBMIT": result=await self.jobs.submit(str(p["proxy_job_id"]),envelope["command_id"],p)
            elif t=="JOB_GET": result=self.jobs.get(str(p["proxy_job_id"]))
            elif t=="JOB_LOGS": result=self.jobs.logs(str(p["proxy_job_id"]),str(p.get("stream","stdout")),int(p.get("cursor",0)),int(p.get("max_bytes",65536)))
            elif t=="JOB_RESULT": result=self.jobs.result(str(p["proxy_job_id"]))
            elif t=="JOB_RECOVER_ROUTED_JOB": result=self.jobs.recover_routed_job(p)
            elif t=="JOB_ARTIFACT_STAT": result=self.jobs.artifact_stat(str(p["proxy_job_id"]),str(p["path"]))
            elif t=="JOB_ARTIFACT_READ": result=self.jobs.artifact_read(
                str(p["proxy_job_id"]),str(p["path"]),str(p["expected_sha256"]),
                int(p.get("offset",0)),int(p.get("limit",120000)),
            )
            elif t=="JOB_CANCEL": result=await self.jobs.cancel(str(p["proxy_job_id"]),envelope["command_id"])
            elif t=="TASK_WORKTREE_CLEANUP": result={"task_id":str(p["task_id"]),"deferred":True}
            elif t=="NODE_RESTART":
                self.restart_requested=True
                result={"device_id":self.jobs.device_id,"restart_requested":True}
            else: raise DurableError("INVALID_ARGUMENT","unsupported node command",command_type=t)
            return self.journal.response(self.journal.terminal(envelope["command_id"],"SUCCEEDED",result=result))
        except DurableError as exc:
            state="IN_DOUBT" if exc.code=="OPERATION_IN_DOUBT" else "FAILED"
            return self.journal.response(self.journal.terminal(envelope["command_id"],state,error_code=exc.code,error={"message":str(exc)}))
        except Exception as exc:
            return self.journal.response(self.journal.terminal(envelope["command_id"],"FAILED",error_code="NODE_EXECUTION_ERROR",error={"message":str(exc)}))
