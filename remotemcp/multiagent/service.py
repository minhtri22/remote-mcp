from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.durable.operations import OperationState
from .agents import AgentRepository
from .cas import CasService
from .config import MultiAgentConfig
from .guard import LegacyGuard
from .jobs import TaskJobService
from .leases import LeaseRepository
from .principal import OwnerIdentity
from .projects import ProjectRepository
from .reconcile import MultiAgentReconciler
from .tasks import TaskRepository
from .tokens import LeaseTokenManager
from .worktrees import WorktreeManager


class MultiAgentService:
    def __init__(self,config:MultiAgentConfig,durable,auth_client_resolver=None):
        self.config=config; self.durable=durable; self.db=durable.db
        # V2-A DurableService keeps bootstrap() semantics at schema v1.
        # Entering the V2-B control plane explicitly upgrades the same DB to v2.
        self.db.bootstrap(target_version=2)
        self.identity=OwnerIdentity(config.runtime_dir,self.db,auth_client_resolver)
        self.tokens=LeaseTokenManager(config.runtime_dir,self.db)
        self.agents=AgentRepository(self.db,self.identity.owner_account_id,config.task_lease_ttl_seconds)
        self.projects=ProjectRepository(self.db,config.workspace_root)
        self.tasks=TaskRepository(self.db)
        self.leases=LeaseRepository(self.db,self.tokens,config.task_lease_ttl_seconds)
        self.worktrees=WorktreeManager(config.workspace_root)
        self.guard=LegacyGuard(self.projects)
        self.cas=CasService(self.db,durable.operations,self.leases,config.workspace_root,self.execution_root)
        self.jobs=TaskJobService(self.db,durable,self.leases,self.projects,self.tasks,self.worktrees,self.execution_root)
        self.reconciler=MultiAgentReconciler(self.db,self.tasks,self.projects,self.worktrees,self.cas,config.task_lease_ttl_seconds)
        self.reconciler.reconcile_all()
        self._task=None; self._stop=asyncio.Event()

    async def start(self):
        if self._task and not self._task.done(): return
        self._stop.clear(); self._task=asyncio.create_task(self._loop(),name="remotemcp-v2b-reconciler")

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task=None

    async def _loop(self):
        try:
            while not self._stop.is_set():
                self.reconciler.reconcile_all()
                try: await asyncio.wait_for(self._stop.wait(),timeout=self.config.poll_ms/1000)
                except asyncio.TimeoutError: pass
        except asyncio.CancelledError: raise

    def _auth_client(self)->str:
        return self.identity.current_auth_client_id()

    def _op(self,operation_id,kind,args,agent_id="",project_id="",task_id=""):
        return self.durable.operations.reserve(
            operation_id,kind,args,principal_key=self.identity.owner_account_id,
            agent_id=agent_id,project_id=project_id,task_id=task_id
        )

    def _op_replay(self,row):
        if row["state"]==OperationState.SUCCEEDED.value:
            return self.durable.operations.replay_result(row)
        if row["state"] in (OperationState.RESERVED.value,OperationState.EXECUTING.value,OperationState.IN_DOUBT.value):
            raise DurableError("OPERATION_IN_DOUBT","operation is not safely replayable")
        raise DurableError(row["error_code"] or "INVALID_ARGUMENT","previous operation failed")

    async def agent_register(self,operation_id:str,agent_name:str,client_instance_id:str,capabilities:list[str]=[])->dict:
        auth=self._auth_client()
        op,created=self._op(operation_id,"AGENT_REGISTER",{"agent_name":agent_name,"client_instance_id":client_instance_id,"capabilities":capabilities})
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.agents.register(agent_name,client_instance_id,auth,capabilities)
        self.durable.operations.succeed(operation_id,result); return result

    def agent_heartbeat(self,agent_id:str,session_id:str,heartbeat_seq:int)->dict:
        return self.agents.heartbeat(agent_id,session_id,int(heartbeat_seq))

    async def session_close(self,operation_id:str,agent_id:str,session_id:str)->dict:
        op,created=self._op(operation_id,"SESSION_CLOSE",{"agent_id":agent_id,"session_id":session_id},agent_id=agent_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.agents.close(agent_id,session_id)
        # Lease expiry semantics: closing makes current leases recoverable immediately.
        with self.db.transaction() as con:
            con.execute("UPDATE task_leases SET expires_at_ms=? WHERE session_id=?",(now_ms()-1,session_id))
            con.execute("UPDATE path_leases SET expires_at_ms=? WHERE session_id=?",(now_ms()-1,session_id))
        self.reconciler.expire_sessions_and_leases()
        self.durable.operations.succeed(operation_id,result); return result

    async def project_register(self,operation_id:str,path:str,max_active_tasks:int=4)->dict:
        op,created=self._op(operation_id,"PROJECT_REGISTER",{"path":path,"max_active_tasks":max_active_tasks})
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.projects.register(path,max_active_tasks)
        self.durable.operations.succeed(operation_id,result); return result

    def project_status(self,project_id:str)->dict:
        return self.projects.status(project_id)

    async def task_create(self,operation_id:str,project_id:str,title:str,base_ref:str="HEAD")->dict:
        project=self.projects.get(project_id)
        base_commit=None; branch=None; worktree_rel=None
        if project["project_kind"]=="GIT":
            base_commit=self.worktrees.resolve_base(self.projects.root_path(project),base_ref)
        op,created=self._op(operation_id,"TASK_CREATE",{"project_id":project_id,"title":title,"base_ref":base_ref,"base_commit":base_commit},project_id=project_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        task_id="tsk_pending"
        # TaskRepository allocates ID, then deterministic branch/path are filled atomically.
        result=self.tasks.create(project_id,title,base_ref,base_commit,None,None)
        if project["project_kind"]=="GIT":
            task_id=result["task_id"]
            branch=f"remotemcp/task/{task_id}"
            worktree_rel=f".remote-worktrees/{project_id}/{task_id}"
            with self.db.transaction() as con:
                con.execute("UPDATE tasks SET branch_name=?,worktree_rel=? WHERE task_id=?",(branch,worktree_rel,task_id))
            result=self.tasks.status(task_id)
        self.durable.operations.succeed(operation_id,result); return result

    def task_status(self,task_id:str)->dict:
        return self.tasks.status(task_id)

    async def task_claim(self,operation_id:str,task_id:str,agent_id:str,session_id:str)->dict:
        self.agents.validate_active(agent_id,session_id)
        op,created=self._op(operation_id,"TASK_CLAIM",{"task_id":task_id,"agent_id":agent_id,"session_id":session_id},agent_id=agent_id,task_id=task_id)
        if not created:
            result=self._op_replay(op) or {}
            epoch=int(result["lease_epoch"])
            token=self.leases.current_token(task_id,agent_id,session_id,epoch)
            return {**result,"lease_token":token,"replayed":True}

        self.durable.operations.mark_executing(operation_id)
        lease=self.leases.claim_phase1(task_id,agent_id,session_id)
        task=self.tasks.get(task_id); project=self.projects.get(task["project_id"])
        try:
            if project["project_kind"]=="GIT":
                self.worktrees.provision(self.projects.root_path(project),task)
            self.leases.finalize_running(task_id,agent_id,session_id,lease["lease_epoch"])
        except Exception as exc:
            with self.db.transaction() as con:
                con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
                con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
                con.execute("UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,owner_session_id=NULL,cleanup_pending=1,updated_at_ms=? WHERE task_id=?",(now_ms(),task_id))
            self.durable.operations.fail(operation_id,getattr(exc,"code","WORKTREE_CONFLICT"),{"error":str(exc)},False)
            raise
        result={"task_id":task_id,"agent_id":agent_id,"session_id":session_id,"lease_epoch":lease["lease_epoch"],"expires_at_ms":lease["expires_at_ms"]}
        self.durable.operations.succeed(operation_id,result)
        return {**result,"lease_token":lease["lease_token"]}

    async def task_checkpoint(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,summary:str,metadata:dict={})->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        op,created=self._op(operation_id,"TASK_CHECKPOINT",{"task_id":task_id,"summary":summary,"metadata":metadata,"lease_epoch":lease_epoch},agent_id=lease["agent_id"],task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.tasks.checkpoint(operation_id,task_id,lease["agent_id"],lease["session_id"],lease_epoch,summary,metadata)
        self.durable.operations.succeed(operation_id,result); return result

    async def task_block(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,reason:str)->dict:
        self.leases.validate(task_id,lease_token,lease_epoch)
        op,created=self._op(operation_id,"TASK_BLOCK",{"task_id":task_id,"reason":reason,"epoch":lease_epoch},task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        self.leases.release(task_id,lease_token,lease_epoch,"BLOCKED","TASK_BLOCKED",reason)
        result=self.tasks.status(task_id); self.durable.operations.succeed(operation_id,result); return result

    async def task_set_ready(self,operation_id:str,task_id:str,reason:str="")->dict:
        op,created=self._op(operation_id,"TASK_SET_READY",{"task_id":task_id,"reason":reason},task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.tasks.set_ready(task_id,reason); self.durable.operations.succeed(operation_id,result); return result

    async def task_release(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int)->dict:
        task=self.tasks.get(task_id); target="READY" if task["state"]=="CLAIMED" and not task["worktree_rel"] else "RECOVERABLE"
        op,created=self._op(operation_id,"TASK_RELEASE",{"task_id":task_id,"epoch":lease_epoch},task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        self.leases.release(task_id,lease_token,lease_epoch,target,"TASK_RELEASED","")
        result=self.tasks.status(task_id); self.durable.operations.succeed(operation_id,result); return result

    async def task_complete(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,outcome_summary:str)->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        task=self.tasks.get(task_id); project=self.projects.get(task["project_id"])
        if self.tasks.nonterminal_jobs(task_id):
            raise DurableError("ACTIVE_JOB_CONFLICT","task has non-terminal jobs")
        if project["project_kind"]=="GIT":
            wt=self.worktrees.validate(self.projects.root_path(project),task)
            if not self.worktrees.clean(wt):
                raise DurableError("WORKTREE_DIRTY","task worktree must be clean/committed")
        op,created=self._op(operation_id,"TASK_COMPLETE",{"task_id":task_id,"epoch":lease_epoch,"outcome_summary":outcome_summary},agent_id=lease["agent_id"],project_id=task["project_id"],task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        with self.db.transaction() as con:
            current=con.execute("SELECT * FROM task_leases WHERE task_id=?",(task_id,)).fetchone()
            if current is None or int(current["lease_epoch"])!=int(lease_epoch):
                raise DurableError("LEASE_STALE","lease changed before completion")
            running=con.execute(
                "SELECT COUNT(*) AS n FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                "WHERE o.task_id=? AND j.state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",(task_id,)
            ).fetchone()["n"]
            if running: raise DurableError("ACTIVE_JOB_CONFLICT","task has non-terminal jobs")
            con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
            con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
            con.execute("UPDATE tasks SET state='COMPLETED',owner_agent_id=NULL,owner_session_id=NULL,finished_at_ms=?,updated_at_ms=? WHERE task_id=?",(now_ms(),now_ms(),task_id))
        result=self.tasks.status(task_id); result["outcome_summary"]=outcome_summary
        self.durable.operations.succeed(operation_id,result); return result

    async def path_lease_acquire(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path:str,scope:str="FILE")->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        op,created=self._op(operation_id,"PATH_LEASE_ACQUIRE",{"task_id":task_id,"path":path,"scope":scope,"epoch":lease_epoch},agent_id=lease["agent_id"],task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.leases.acquire_path(task_id,lease_token,lease_epoch,path,scope); self.durable.operations.succeed(operation_id,result); return result

    async def path_lease_release(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path_lease_id:str)->dict:
        op,created=self._op(operation_id,"PATH_LEASE_RELEASE",{"task_id":task_id,"path_lease_id":path_lease_id,"epoch":lease_epoch},task_id=task_id)
        if not created: return {**(self._op_replay(op) or {}),"replayed":True}
        self.durable.operations.mark_executing(operation_id)
        result=self.leases.release_path(task_id,lease_token,lease_epoch,path_lease_id); self.durable.operations.succeed(operation_id,result); return result

    async def file_write_cas(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path:str,expected_sha256:str,content:str)->dict:
        return self.cas.write(operation_id,task_id,lease_token,lease_epoch,path,expected_sha256,content)

    async def file_edit_cas(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path:str,expected_sha256:str,old:str,new:str,expected_occurrences:int=1)->dict:
        return self.cas.edit(operation_id,task_id,lease_token,lease_epoch,path,expected_sha256,old,new,expected_occurrences)

    async def task_job_submit(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,argv:list[str],cwd:str=".")->dict:
        return await self.jobs.submit(operation_id,task_id,lease_token,lease_epoch,argv,cwd)

    def task_jobs(self,task_id:str)->dict:
        return self.jobs.list(task_id)

    async def task_job_cancel(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,job_id:str)->dict:
        return await self.jobs.cancel(operation_id,task_id,lease_token,lease_epoch,job_id)

    def execution_root(self,task_id:str)->Path:
        task=self.tasks.get(task_id); project=self.projects.get(task["project_id"])
        if project["project_kind"]=="GIT":
            if not task["worktree_rel"]: raise DurableError("WORKTREE_IDENTITY_MISMATCH","task has no worktree")
            return (self.config.workspace_root/task["worktree_rel"]).resolve()
        return self.projects.root_path(project)