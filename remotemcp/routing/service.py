from __future__ import annotations

import asyncio
import hashlib
import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import OperationState, now_ms

from .auth import SignedRequestVerifier
from .bindings import BindingRepository
from .commands import CommandRepository
from .config import RoutingConfig
from .devices import DeviceRepository
from .pairing import PairingService
from .routed_jobs import RoutedJobRepository


class RoutingService:
    def __init__(self,config:RoutingConfig,durable,multiagent):
        self.config=config; self.durable=durable; self.multi=multiagent
        self.db=durable.db
        self.db.bootstrap(target_version=3)
        self.owner_account_id=multiagent.identity.owner_account_id
        with self.db.transaction() as con:
            self.multi.agents.ensure_owner(con)
        self.devices=DeviceRepository(self.db,self.owner_account_id,config.offline_after_seconds)
        self.pairing=PairingService(config,self.db,durable.operations,self.devices,self.owner_account_id)
        self.verifier=SignedRequestVerifier(config,self.db,self.devices)
        self.commands=CommandRepository(config,self.db,self.devices)
        self.bindings=BindingRepository(self.db,self.devices)
        self.routed_jobs=RoutedJobRepository(self.db)
        self._stop=asyncio.Event();self._task=None

    async def start(self):
        if self._task and not self._task.done():return
        self._stop.clear()
        self._task=asyncio.create_task(self._loop(),name="remotemcp-v2bd-routing")

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
            self._task=None

    async def _loop(self):
        try:
            while not self._stop.is_set():
                self.devices.sweep_offline()
                try:await asyncio.wait_for(self._stop.wait(),timeout=5.0)
                except asyncio.TimeoutError:pass
        except asyncio.CancelledError:
            raise

    def _reserve(self,operation_id,kind,args,*,agent_id="",project_id="",task_id=""):
        return self.durable.operations.reserve(
            operation_id,kind,args,principal_key=self.owner_account_id,
            agent_id=agent_id,project_id=project_id,task_id=task_id,
        )

    def _operation_replay(self,op):
        if op["state"]==OperationState.SUCCEEDED.value:
            return self.durable.operations.replay_result(op)
        if op["state"]==OperationState.FAILED_FINAL.value:
            raise DurableError(op["error_code"] or "INVALID_ARGUMENT","previous operation failed")
        if op["state"]==OperationState.IN_DOUBT.value:
            raise DurableError("OPERATION_IN_DOUBT","operation is in doubt")
        return None

    def _fail_final(self,operation_id,exc):
        if isinstance(exc,DurableError) and exc.code=="DEVICE_COMMAND_PENDING":
            return
        code=getattr(exc,"code","INVALID_ARGUMENT")
        self.durable.operations.fail(operation_id,code,{"error":str(exc)},False)

    def guard_local_job_id(self,job_id:str)->None:
        if str(job_id).startswith("rjob_"):
            raise DurableError(
                "ROUTED_JOB_USE_TASK_TOOLS",
                "routed jobs must be observed/cancelled through task-scoped routed-job tools",
                job_id=job_id,
            )

    def guard_gateway_job_id(self,job_id:str)->None:
        self.guard_local_job_id(job_id)

    def node_poll(self,device_id:str):
        row=self.devices.require_online(device_id)
        cmd=self.commands.poll(device_id,int(row["route_generation"]))
        return self.commands.envelope(cmd) if cmd is not None else None

    def _routing_meta(
        self,
        device_id:str,
        *,
        project_id:str|None=None,
        task_id:str|None=None,
        binding_generation:int|None=None,
    )->dict:
        status=self.devices.status(device_id)
        meta={
            "device_id":status["device_id"],
            "device_name":status["device_name"],
            "hostname":status.get("hostname"),
            "device_state":status["state"],
        }
        if project_id is not None:
            meta["project_id"]=project_id
        if task_id is not None:
            meta["task_id"]=task_id
        if binding_generation is not None:
            meta["binding_generation"]=int(binding_generation)
        return meta

    def _with_routing(
        self,
        result:dict,
        device_id:str,
        *,
        project_id:str|None=None,
        task_id:str|None=None,
        binding_generation:int|None=None,
    )->dict:
        return {
            **result,
            "routing":self._routing_meta(
                device_id,
                project_id=project_id,
                task_id=task_id,
                binding_generation=binding_generation,
            ),
        }

    def project_status_or_local(self,project_id:str)->dict:
        result=self.multi.project_status(project_id)
        binding=self.bindings.project_binding(project_id)
        if binding is None:
            return result
        return self._with_routing(
            result,
            binding["device_id"],
            project_id=project_id,
            binding_generation=int(binding["binding_generation"]),
        )

    def task_status_or_local(self,task_id:str)->dict:
        result=self.multi.task_status(task_id)
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return result
        return self._with_routing(
            result,
            binding["device_id"],
            project_id=binding["project_id"],
            task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    def _remote_storage_key(self,device_id:str,node_root_rel:str)->str:
        digest=hashlib.sha256(node_root_rel.encode("utf-8")).hexdigest()[:24]
        return f"@v2bd/{device_id}/{digest}"

    async def _route_step(
        self,device_id:str,command_type:str,payload:dict,*,operation_id:str|None=None,
        operation_step:int=0,project_id:str|None=None,task_id:str|None=None,
        expires_at_ms:int|None=None,
    ):
        row,_=self.commands.create(
            device_id,command_type,payload,project_id=project_id,task_id=task_id,
            operation_id=operation_id,operation_step=operation_step,expires_at_ms=expires_at_ms,
        )
        terminal=await self.commands.wait(row["command_id"])
        return self.commands.result_value(terminal),terminal

    # ---- device ownership/tools ----

    async def device_pair_begin(self,operation_id:str,device_name:str)->dict:
        return self.pairing.begin(operation_id,device_name)

    def device_list(self)->dict:
        return {"devices":[self.devices.status(r["device_id"]) for r in self.devices.list()]}

    def device_status(self,device_id:str)->dict:
        return self.devices.status(device_id)

    async def device_revoke(self,operation_id:str,device_id:str,reason:str="")->dict:
        op,created=self._reserve(operation_id,"DEVICE_REVOKE",{"device_id":device_id,"reason":reason})
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(replay,device_id)
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        row=self.devices.revoke(device_id,reason)
        result=self._with_routing(self.devices.status(row["device_id"]),device_id)
        self.durable.operations.succeed(operation_id,result)
        return result

    async def device_restart(
        self,
        operation_id:str,
        device_id:str,
        reason:str="user-requested",
        allow_active_jobs:bool=False,
    )->dict:
        status=self.devices.status(device_id)
        if status["state"]!="ONLINE":
            raise DurableError("DEVICE_OFFLINE","device is offline")
        active_jobs=int(status.get("active_routed_jobs",0))
        if active_jobs>0 and not bool(allow_active_jobs):
            raise DurableError(
                "DEVICE_BUSY",
                "device has active routed jobs; restart requires explicit allow_active_jobs=true",
                device_id=device_id,
                active_routed_jobs=active_jobs,
            )
        args={
            "device_id":device_id,
            "reason":reason,
            "allow_active_jobs":bool(allow_active_jobs),
        }
        op,created=self._reserve(operation_id,"DEVICE_RESTART",args)
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(replay,device_id)
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            result,_=await self._route_step(
                device_id,
                "NODE_RESTART",
                {"reason":reason},
                operation_id=operation_id,
                operation_step=0,
            )
            result={
                **result,
                "active_routed_jobs_at_request":active_jobs,
                "same_identity_expected":True,
            }
            result=self._with_routing(result,device_id)
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    # ---- project routing ----

    async def project_register_or_local(self,operation_id:str,path:str,max_active_tasks:int=4)->dict:
        if self.devices.list():
            raise DurableError("DEVICE_CONTEXT_REQUIRED","paired execution devices require project_register_on_device")
        return await self.multi.project_register(operation_id,path,max_active_tasks)

    async def project_register_on_device(self,operation_id:str,device_id:str,path:str,max_active_tasks:int=4)->dict:
        if not 1<=int(max_active_tasks)<=32:
            raise DurableError("INVALID_ARGUMENT","max_active_tasks must be 1..32")
        self.devices.require_online(device_id)
        args={"device_id":device_id,"path":path,"max_active_tasks":int(max_active_tasks)}
        op,created=self._reserve(operation_id,"PROJECT_REGISTER_ON_DEVICE",args)
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,device_id,
                project_id=replay.get("project_id"),
                binding_generation=replay.get("binding_generation"),
            )
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            probe,_=await self._route_step(
                device_id,"PROJECT_PROBE",{"path":path},
                operation_id=operation_id,operation_step=0,
            )
            node_root_rel=str(probe["root_rel"]); kind=str(probe["project_kind"])
            if kind not in {"GIT","NON_GIT"}:
                raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","invalid node project kind")
            storage_key=self._remote_storage_key(device_id,node_root_rel)
            t=now_ms()
            deterministic="prj_"+hashlib.sha256(f"{self.owner_account_id}|{operation_id}|{device_id}|{node_root_rel}".encode()).hexdigest()[:24]
            with self.db.transaction() as con:
                existing=con.execute(
                    "SELECT p.*,b.device_id,b.node_root_rel,b.binding_generation FROM project_device_bindings b "
                    "JOIN projects p ON p.project_id=b.project_id WHERE b.device_id=? AND b.node_root_rel=?",
                    (device_id,node_root_rel),
                ).fetchone()
                if existing:
                    if existing["project_kind"]!=kind:
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","remote project kind changed")
                    project_id=existing["project_id"]
                    con.execute("UPDATE projects SET max_active_tasks=?,updated_at_ms=? WHERE project_id=?",(int(max_active_tasks),t,project_id))
                    generation=int(existing["binding_generation"])
                else:
                    conflict=con.execute("SELECT project_id FROM projects WHERE root_rel=?",(storage_key,)).fetchone()
                    if conflict and conflict["project_id"]!=deterministic:
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","remote storage key collision")
                    project_id=deterministic
                    con.execute(
                        "INSERT OR IGNORE INTO projects(project_id,root_rel,project_kind,max_active_tasks,created_at_ms,updated_at_ms) "
                        "VALUES(?,?,?,?,?,?)",
                        (project_id,storage_key,kind,int(max_active_tasks),t,t),
                    )
                    con.execute(
                        "INSERT OR IGNORE INTO project_device_bindings(project_id,device_id,node_root_rel,binding_generation,bound_at_ms,updated_at_ms) "
                        "VALUES(?,?,?,1,?,?)",
                        (project_id,device_id,node_root_rel,t,t),
                    )
                    binding=con.execute("SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)).fetchone()
                    if binding is None or binding["device_id"]!=device_id or binding["node_root_rel"]!=node_root_rel:
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","central project binding mismatch")
                    generation=int(binding["binding_generation"])
            bind_payload={
                "project_id":project_id,"binding_generation":generation,
                "root_rel":node_root_rel,"project_kind":kind,
            }
            await self._route_step(
                device_id,"PROJECT_BIND",bind_payload,
                operation_id=operation_id,operation_step=1,project_id=project_id,
            )
            result={**self.multi.projects.status(project_id),"device_id":device_id,"node_root_rel":node_root_rel,"binding_generation":generation}
            result=self._with_routing(
                result,device_id,project_id=project_id,binding_generation=generation
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    async def project_bind_device(self,operation_id:str,project_id:str,device_id:str)->dict:
        project=self.multi.projects.get(project_id)
        current=self.bindings.project_binding(project_id)
        if current and current["device_id"]==device_id:
            result={
                **self.multi.projects.status(project_id),
                "device_id":device_id,
                "node_root_rel":current["node_root_rel"],
                "binding_generation":int(current["binding_generation"]),
            }
            return {
                **self._with_routing(
                    result,device_id,project_id=project_id,
                    binding_generation=int(current["binding_generation"]),
                ),
                "replayed":True,
            }
        if str(project["root_rel"]).startswith("@v2bd/"):
            raise DurableError("PROJECT_DEVICE_MIGRATION_FORBIDDEN","remote-created project cannot be rebound by path inference")
        self.devices.require_online(device_id)
        args={"project_id":project_id,"device_id":device_id}
        op,created=self._reserve(operation_id,"PROJECT_BIND_DEVICE",args,project_id=project_id)
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,device_id,project_id=project_id,
                binding_generation=replay.get("binding_generation"),
            )
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            probe,_=await self._route_step(
                device_id,"PROJECT_PROBE",{"path":project["root_rel"]},
                operation_id=operation_id,operation_step=0,project_id=project_id,
            )
            if probe["project_kind"]!=project["project_kind"]:
                raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","project kind differs on target device")
            binding=self.bindings.bind_project(project_id,device_id,str(probe["root_rel"]))
            await self._route_step(
                device_id,"PROJECT_BIND",
                {"project_id":project_id,"binding_generation":int(binding["binding_generation"]),"root_rel":binding["node_root_rel"],"project_kind":project["project_kind"]},
                operation_id=operation_id,operation_step=1,project_id=project_id,
            )
            result={**self.multi.projects.status(project_id),"device_id":device_id,"node_root_rel":binding["node_root_rel"],"binding_generation":int(binding["binding_generation"])}
            result=self._with_routing(
                result,device_id,project_id=project_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc);raise

    # ---- task lifecycle ----

    async def task_create_or_local(self,operation_id:str,project_id:str,title:str,base_ref:str="HEAD")->dict:
        binding=self.bindings.project_binding(project_id)
        if binding is None:
            return await self.multi.task_create(operation_id,project_id,title,base_ref)
        project=self.multi.projects.get(project_id)
        args={"project_id":project_id,"title":title,"base_ref":base_ref}
        op,created=self._reserve(operation_id,"TASK_CREATE",args,project_id=project_id)
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,binding["device_id"],project_id=project_id,
                task_id=replay.get("task_id"),
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        task_id="tsk_"+hashlib.sha256(f"{self.owner_account_id}|{operation_id}|{project_id}".encode()).hexdigest()[:24]
        branch=f"remotemcp/task/{task_id}" if project["project_kind"]=="GIT" else None
        worktree_rel=f".remote-worktrees/{project_id}/{task_id}" if project["project_kind"]=="GIT" else None
        t=now_ms()
        try:
            with self.db.transaction() as con:
                old=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
                if old is None:
                    con.execute(
                        "INSERT INTO tasks(task_id,project_id,title,state,base_ref,base_commit,branch_name,worktree_rel,created_at_ms,updated_at_ms) "
                        "VALUES(?,?,?,'READY',?,NULL,?,?,?,?)",
                        (task_id,project_id,title,base_ref,branch,worktree_rel,t,t),
                    )
                    self.multi.tasks._event(con,task_id,operation_id,None,None,"TASK_CREATED",{"title":title})
                    self.multi.tasks._event(con,task_id,operation_id,None,None,"TASK_READY",{})
                    self.bindings.inherit_task(con,task_id,project_id)
                else:
                    if old["project_id"]!=project_id or old["title"]!=title or old["base_ref"]!=base_ref:
                        raise DurableError("OPERATION_CONFLICT","deterministic task identity conflicts")
            result=self.multi.tasks.status(task_id)
            result["device_id"]=binding["device_id"]
            result=self._with_routing(
                result,binding["device_id"],project_id=project_id,task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc);raise

    async def task_claim_or_local(self,operation_id:str,task_id:str,agent_id:str,session_id:str)->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return await self.multi.task_claim(operation_id,task_id,agent_id,session_id)
        self.devices.require_online(binding["device_id"])
        self.multi.agents.validate_active(agent_id,session_id)
        args={"task_id":task_id,"agent_id":agent_id,"session_id":session_id}
        op,created=self._reserve(operation_id,"TASK_CLAIM",args,agent_id=agent_id,task_id=task_id)
        if op["state"]==OperationState.SUCCEEDED.value:
            result=self.durable.operations.replay_result(op) or {}
            result=self._with_routing(
                result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            token=self.multi.leases.current_token(task_id,agent_id,session_id,int(result["lease_epoch"]))
            return {**result,"lease_token":token,"replayed":True}
        if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
            self._operation_replay(op)
        if created:
            self.durable.operations.mark_executing(operation_id)
            lease=self.multi.leases.claim_phase1(task_id,agent_id,session_id)
        else:
            lease=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task_id,))
            if lease is None or lease["agent_id"]!=agent_id or lease["session_id"]!=session_id:
                raise DurableError("OPERATION_IN_DOUBT","routed claim lost its lease evidence")
            lease=dict(lease)
            lease["lease_token"]=self.multi.leases.current_token(task_id,agent_id,session_id,int(lease["lease_epoch"]))
        task=self.multi.tasks.get(task_id);project=self.multi.projects.get(task["project_id"])
        if task["state"]=="RUNNING":
            result={"task_id":task_id,"agent_id":agent_id,"session_id":session_id,"lease_epoch":int(lease["lease_epoch"]),"expires_at_ms":int(lease["expires_at_ms"]),"device_id":binding["device_id"]}
            result=self._with_routing(
                result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return {**result,"lease_token":lease["lease_token"]}
        expiry=min(int(lease["expires_at_ms"]),now_ms()+self.config.mutation_ttl_seconds*1000)
        payload={
            "task_id":task_id,"project_id":task["project_id"],"binding_generation":int(binding["binding_generation"]),
            "branch_name":task["branch_name"],"worktree_rel":task["worktree_rel"],
            "base_ref":task["base_ref"],"base_commit":task["base_commit"],
        }
        try:
            await self._route_step(
                binding["device_id"],"TASK_WORKTREE_ENSURE",payload,
                operation_id=operation_id,operation_step=0,project_id=task["project_id"],task_id=task_id,
                expires_at_ms=expiry,
            )
            self.multi.leases.finalize_running(task_id,agent_id,session_id,int(lease["lease_epoch"]))
            result={"task_id":task_id,"agent_id":agent_id,"session_id":session_id,"lease_epoch":int(lease["lease_epoch"]),"expires_at_ms":int(lease["expires_at_ms"]),"device_id":binding["device_id"]}
            result=self._with_routing(
                result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return {**result,"lease_token":lease["lease_token"]}
        except DurableError as exc:
            if exc.code=="DEVICE_COMMAND_PENDING":
                raise
            with self.db.transaction() as con:
                con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
                con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
                con.execute(
                    "UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,owner_session_id=NULL,cleanup_pending=1,updated_at_ms=? WHERE task_id=?",
                    (now_ms(),task_id),
                )
            self._fail_final(operation_id,exc);raise

    async def task_complete_or_local(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,outcome_summary:str)->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return await self.multi.task_complete(operation_id,task_id,lease_token,lease_epoch,outcome_summary)
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        task=self.multi.tasks.get(task_id);project=self.multi.projects.get(task["project_id"])
        active=self.db.query_one(
            "SELECT COUNT(*) AS n FROM routed_jobs WHERE task_id=? AND last_known_state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
            (task_id,),
        )["n"]
        if active:
            raise DurableError("ACTIVE_JOB_CONFLICT","task has non-terminal routed jobs")
        args={"task_id":task_id,"epoch":int(lease_epoch),"outcome_summary":outcome_summary}
        op,created=self._reserve(
            operation_id,"TASK_COMPLETE",args,
            agent_id=lease["agent_id"],project_id=task["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,binding["device_id"],project_id=task["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if created:
            self.durable.operations.mark_executing(operation_id)
        try:
            if project["project_kind"]=="GIT":
                status,_=await self._route_step(
                    binding["device_id"],"TASK_WORKTREE_STATUS",{"task_id":task_id},
                    operation_id=operation_id,operation_step=0,
                    project_id=task["project_id"],task_id=task_id,
                )
                if not status.get("clean"):
                    raise DurableError("WORKTREE_DIRTY","remote task worktree must be clean/committed")
            t=now_ms()
            with self.db.transaction() as con:
                current=con.execute("SELECT * FROM task_leases WHERE task_id=?",(task_id,)).fetchone()
                if current is None or int(current["lease_epoch"])!=int(lease_epoch):
                    raise DurableError("LEASE_STALE","lease changed before completion")
                con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
                con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
                con.execute(
                    "UPDATE tasks SET state='COMPLETED',owner_agent_id=NULL,owner_session_id=NULL,finished_at_ms=?,updated_at_ms=? WHERE task_id=?",
                    (t,t,task_id),
                )
            result=self.multi.tasks.status(task_id)
            result["outcome_summary"]=outcome_summary
            result["device_id"]=binding["device_id"]
            result=self._with_routing(
                result,binding["device_id"],project_id=task["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    # ---- routed read/CAS ----

    async def _task_read(self,task_id:str,command_type:str,payload:dict):
        binding=self.bindings.require_task_binding(task_id)
        self.devices.require_online(binding["device_id"])
        payload={"task_id":task_id,**payload}
        result,_=await self._route_step(
            binding["device_id"],command_type,payload,
            project_id=binding["project_id"],task_id=task_id,
        )
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_list_dir(self,task_id:str,path:str=".")->dict:
        return await self._task_read(task_id,"TASK_LIST_DIR",{"path":path})

    async def task_read_file(self,task_id:str,path:str,offset:int=0,limit:int=120000)->dict:
        return await self._task_read(
            task_id,"TASK_READ_FILE",
            {"path":path,"offset":int(offset),"limit":int(limit)},
        )

    async def task_search(self,task_id:str,pattern:str,path:str=".")->dict:
        return await self._task_read(
            task_id,"TASK_SEARCH",{"path":path,"pattern":pattern}
        )

    async def _cas_or_local(
        self,kind:str,operation_id:str,task_id:str,lease_token:str,
        lease_epoch:int,payload:dict,
    ):
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            if kind=="FILE_WRITE_CAS":
                return await self.multi.file_write_cas(
                    operation_id,task_id,lease_token,lease_epoch,
                    payload["path"],payload["expected_sha256"],payload["content"],
                )
            return await self.multi.file_edit_cas(
                operation_id,task_id,lease_token,lease_epoch,
                payload["path"],payload["expected_sha256"],payload["old"],payload["new"],
                payload["expected_occurrences"],
            )
        self.devices.require_online(binding["device_id"])
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        normalized={k:v for k,v in payload.items() if k!="content"}
        if "content" in payload:
            normalized["content_sha256"]=hashlib.sha256(
                str(payload["content"]).encode()
            ).hexdigest()
        normalized.update({"task_id":task_id,"lease_epoch":int(lease_epoch)})
        op,created=self._reserve(
            operation_id,kind,normalized,agent_id=lease["agent_id"],
            project_id=binding["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if created:
            self.durable.operations.mark_executing(operation_id)
        expiry=min(
            int(lease["expires_at_ms"]),
            now_ms()+self.config.mutation_ttl_seconds*1000,
        )
        node_payload={"task_id":task_id,**payload}
        try:
            result,_=await self._route_step(
                binding["device_id"],kind,node_payload,
                operation_id=operation_id,operation_step=0,
                project_id=binding["project_id"],task_id=task_id,
                expires_at_ms=expiry,
            )
            result=self._with_routing(
                result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    async def file_write_cas_or_local(
        self,operation_id,task_id,lease_token,lease_epoch,path,expected_sha256,content
    ):
        return await self._cas_or_local(
            "FILE_WRITE_CAS",operation_id,task_id,lease_token,lease_epoch,
            {"path":path,"expected_sha256":expected_sha256,"content":content},
        )

    async def file_edit_cas_or_local(
        self,operation_id,task_id,lease_token,lease_epoch,path,expected_sha256,
        old,new,expected_occurrences=1
    ):
        return await self._cas_or_local(
            "FILE_EDIT_CAS",operation_id,task_id,lease_token,lease_epoch,
            {
                "path":path,"expected_sha256":expected_sha256,
                "old":old,"new":new,"expected_occurrences":int(expected_occurrences),
            },
        )

    # ---- routed jobs ----

    def _task_proxy(self,task_id:str,proxy_job_id:str):
        binding=self.bindings.require_task_binding(task_id)
        row=self.routed_jobs.get(proxy_job_id)
        if row["task_id"]!=task_id or row["device_id"]!=binding["device_id"]:
            raise DurableError("FORBIDDEN","routed job does not belong to task/device")
        return binding,row

    async def task_job_submit_or_local(
        self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,
        argv:list[str],cwd:str="."
    )->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return await self.multi.task_job_submit(
                operation_id,task_id,lease_token,lease_epoch,argv,cwd
            )
        dev=self.devices.require_online(binding["device_id"])
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        args={
            "task_id":task_id,"lease_epoch":int(lease_epoch),
            "argv":argv,"cwd":cwd,
        }
        op,created=self._reserve(
            operation_id,"TASK_JOB_SUBMIT",args,agent_id=lease["agent_id"],
            project_id=binding["project_id"],task_id=task_id,
        )
        if op["state"]==OperationState.SUCCEEDED.value:
            replay=self.durable.operations.replay_result(op) or {}
            replay=self._with_routing(
                replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
            self._operation_replay(op)
        self.devices.require_online(binding["device_id"])
        if created:
            self.durable.operations.mark_executing(operation_id)
        with self.db.transaction() as con:
            proxy,_=self.routed_jobs.create(
                con,operation_id,task_id,binding["project_id"],binding["device_id"]
            )
            payload={
                "proxy_job_id":proxy["proxy_job_id"],
                "task_id":task_id,"argv":argv,"cwd":cwd,
            }
            cmd,_=self.commands.create_in_tx(
                con,binding["device_id"],"JOB_SUBMIT",payload,
                route_generation=int(dev["route_generation"]),
                project_id=binding["project_id"],task_id=task_id,
                operation_id=operation_id,operation_step=0,
                expires_at_ms=min(
                    int(lease["expires_at_ms"]),
                    now_ms()+self.config.mutation_ttl_seconds*1000,
                ),
            )
        try:
            terminal=await self.commands.wait(cmd["command_id"])
            result=self.commands.result_value(terminal)
            row=self.routed_jobs.update(
                proxy["proxy_job_id"],
                node_job_id=result.get("node_job_id"),
                state=result.get("state","QUEUED"),
            )
            response=self.routed_jobs.as_dict(
                row,self.devices.get(binding["device_id"])["state"]
            )
            response=self._with_routing(
                response,binding["device_id"],
                project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,response)
            return response
        except DurableError as exc:
            if exc.code!="DEVICE_COMMAND_PENDING":
                self._fail_final(operation_id,exc)
            raise

    def task_jobs_or_local(self,task_id:str)->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return self.multi.task_jobs(task_id)
        state=self.devices.status(binding["device_id"])["state"]
        result={
            "task_id":task_id,
            "jobs":[{**j,"device_state":state} for j in self.routed_jobs.list_task(task_id)],
        }
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_get(
        self,task_id:str,proxy_job_id:str,refresh:bool=True
    )->dict:
        binding,row=self._task_proxy(task_id,proxy_job_id)
        state=self.devices.status(binding["device_id"])["state"]
        if not refresh or state!="ONLINE":
            result=self.routed_jobs.as_dict(row,state)
        else:
            remote,_=await self._route_step(
                binding["device_id"],"JOB_GET",{"proxy_job_id":proxy_job_id},
                project_id=binding["project_id"],task_id=task_id,
            )
            row=self.routed_jobs.update(
                proxy_job_id,node_job_id=remote.get("node_job_id"),
                state=remote.get("state"),
            )
            result=self.routed_jobs.as_dict(row,"ONLINE")
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_logs(
        self,task_id:str,proxy_job_id:str,stream:str="stdout",
        cursor:int=0,max_bytes:int=65536
    )->dict:
        binding,_=self._task_proxy(task_id,proxy_job_id)
        self.devices.require_online(binding["device_id"])
        result,_=await self._route_step(
            binding["device_id"],"JOB_LOGS",
            {
                "proxy_job_id":proxy_job_id,"stream":stream,
                "cursor":int(cursor),"max_bytes":int(max_bytes),
            },
            project_id=binding["project_id"],task_id=task_id,
        )
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_result(self,task_id:str,proxy_job_id:str)->dict:
        binding,row=self._task_proxy(task_id,proxy_job_id)
        if row["terminal_result_json"]:
            result=json.loads(row["terminal_result_json"])
        else:
            self.devices.require_online(binding["device_id"])
            result,_=await self._route_step(
                binding["device_id"],"JOB_RESULT",{"proxy_job_id":proxy_job_id},
                project_id=binding["project_id"],task_id=task_id,
            )
            if result.get("state"):
                self.routed_jobs.update(
                    proxy_job_id,node_job_id=result.get("node_job_id"),
                    state=result.get("state"),
                )
            if result.get("terminal"):
                self.routed_jobs.update(
                    proxy_job_id,node_job_id=result.get("node_job_id"),
                    state=result.get("state"),terminal_result=result,
                )
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_cancel_or_local(
        self,operation_id:str,task_id:str,lease_token:str,
        lease_epoch:int,job_id:str
    )->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return await self.multi.task_job_cancel(
                operation_id,task_id,lease_token,lease_epoch,job_id
            )
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        self._task_proxy(task_id,job_id)
        self.devices.require_online(binding["device_id"])
        args={
            "task_id":task_id,"proxy_job_id":job_id,
            "lease_epoch":int(lease_epoch),
        }
        op,created=self._reserve(
            operation_id,"TASK_JOB_CANCEL",args,
            agent_id=lease["agent_id"],project_id=binding["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if created:
            self.durable.operations.mark_executing(operation_id)
        try:
            result,_=await self._route_step(
                binding["device_id"],"JOB_CANCEL",{"proxy_job_id":job_id},
                operation_id=operation_id,operation_step=0,
                project_id=binding["project_id"],task_id=task_id,
                expires_at_ms=min(
                    int(lease["expires_at_ms"]),
                    now_ms()+self.config.mutation_ttl_seconds*1000,
                ),
            )
            row=self.routed_jobs.update(
                job_id,node_job_id=result.get("node_job_id"),state=result.get("state")
            )
            response=self.routed_jobs.as_dict(row,"ONLINE")
            response=self._with_routing(
                response,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,response)
            return response
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    # ---- HTTP transport handlers ----

    def verify_signed(self,method,path,headers,body):
        return self.verifier.verify(method,path,headers,body)

    def pair_http(self,payload:dict)->dict:
        return self.pairing.consume(payload)

    def heartbeat_http(self,device_row,payload:dict)->dict:
        capabilities=payload.get("capabilities")
        platform=payload.get("platform")
        if isinstance(capabilities,dict) or isinstance(platform,dict):
            with self.db.transaction() as con:
                if isinstance(capabilities,dict):
                    con.execute(
                        "UPDATE devices SET capabilities_json=? WHERE device_id=?",
                        (json.dumps(capabilities,sort_keys=True),device_row["device_id"]),
                    )
                if isinstance(platform,dict):
                    con.execute(
                        "UPDATE devices SET platform_json=? WHERE device_id=?",
                        (json.dumps(platform,sort_keys=True),device_row["device_id"]),
                    )
        return {
            "server_time_ms":now_ms(),
            "device_id":device_row["device_id"],
            "state":"ONLINE",
            "route_generation":int(device_row["route_generation"]),
            "next_heartbeat_seconds":self.config.heartbeat_seconds,
        }

    async def poll_http(self,device_row):
        deadline=asyncio.get_running_loop().time()+self.config.poll_long_wait_seconds
        while True:
            row=self.commands.poll(
                device_row["device_id"],int(device_row["route_generation"])
            )
            if row is not None:
                return self.commands.envelope(row)
            if asyncio.get_running_loop().time()>=deadline:
                return None
            await asyncio.sleep(0.1)

    def result_http(self,device_row,command_id:str,payload:dict)->dict:
        row=self.commands.commit_result(
            device_row["device_id"],int(device_row["route_generation"]),
            command_id,payload,
        )
        self._apply_job_result_side_effect(row)
        return {
            "command_id":command_id,
            "accepted":True,
            "central_state":row["state"],
        }

    def _apply_job_result_side_effect(self,command_row):
        if command_row["state"]!="SUCCEEDED" or not command_row["result_json"]:
            return
        if command_row["command_type"] not in {
            "JOB_SUBMIT","JOB_GET","JOB_RESULT","JOB_CANCEL"
        }:
            return
        payload=json.loads(command_row["payload_json"])
        result=json.loads(command_row["result_json"])
        proxy_id=payload.get("proxy_job_id")
        if not proxy_id:
            return
        try:
            self.routed_jobs.update(
                proxy_id,node_job_id=result.get("node_job_id"),
                state=result.get("state"),
                terminal_result=(
                    result if command_row["command_type"]=="JOB_RESULT"
                    and result.get("terminal") else None
                ),
            )
        except DurableError:
            return
