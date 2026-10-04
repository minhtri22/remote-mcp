from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import OperationState, now_ms
from remotemcp.durable.operations import request_hash
from remotemcp.node.client import node_platform
from remotemcp.node.config import NodeConfig
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity
from remotemcp.node.signing import sign_pair
from remotemcp.workspace_layout import enforce_agent_git_worktree_policy, task_worktree_rel

from .auth import SignedRequestVerifier
from .bindings import BindingRepository
from .commands import CommandRepository
from .config import RoutingConfig
from .devices import DeviceRepository
from .pairing import PairingService
from .routed_jobs import RoutedJobRepository,TERMINAL


class RoutingService:
    def __init__(self,config:RoutingConfig,durable,multiagent):
        self.config=config; self.durable=durable; self.multi=multiagent
        self.db=durable.db
        self.db.bootstrap(target_version=4)
        self.owner_account_id=multiagent.identity.owner_account_id
        with self.db.transaction() as con:
            self.multi.agents.ensure_owner(con)
        self.devices=DeviceRepository(self.db,self.owner_account_id,config.offline_after_seconds)
        self.pairing=PairingService(config,self.db,durable.operations,self.devices,self.owner_account_id)
        self.verifier=SignedRequestVerifier(config,self.db,self.devices)
        self.commands=CommandRepository(config,self.db,self.devices)
        self.bindings=BindingRepository(self.db,self.devices)
        self.routed_jobs=RoutedJobRepository(self.db)
        self.job_admissions=multiagent.job_admissions
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
                try:
                    self.devices.sweep_offline()
                except DurableError as exc:
                    # The sweep is the sole periodic OFFLINE-state writer.
                    # A transient SQLite writer collision must not kill the
                    # background task; effective request-time state remains
                    # fail-closed and the next cycle retries persistence.
                    if exc.code!="DB_BUSY":
                        raise
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

    @staticmethod
    def _dedicated_local_path(value:str,label:str)->Path:
        raw=str(value or "").strip()
        if not raw or any(ch in raw for ch in ("\x00","\r","\n")):
            raise DurableError("INVALID_ARGUMENT",f"{label} must be a non-empty absolute path")
        path=Path(raw).expanduser()
        if not path.is_absolute():
            raise DurableError("INVALID_ARGUMENT",f"{label} must be absolute")
        path=path.resolve()
        anchor_path=Path(path.anchor).resolve()
        if path==anchor_path:
            raise DurableError(
                "DEDICATED_NODE_SCOPE_INVALID",
                f"{label} cannot be a filesystem root",
                path=str(path),
            )
        return path

    def _local_pairing_result(
        self,
        *,
        pairing_id:str,
        device_name:str,
        node_root:Path,
        runtime_dir:Path,
        device_id:str,
        recovered:bool,
    )->dict:
        status=self.devices.status(device_id)
        return {
            "pairing_id":str(pairing_id),
            "device_id":status["device_id"],
            "device_name":status["device_name"],
            "device_state":status["state"],
            "route_generation":int(status["route_generation"]),
            "key_fingerprint_sha256":status["key_fingerprint_sha256"],
            "node_root":str(node_root),
            "runtime_dir":str(runtime_dir),
            "secure_local_pairing":True,
            "local_host_only":True,
            "pairing_secret_exposed":False,
            "node_process_started":False,
            "recovered_existing_pairing":bool(recovered),
        }

    def device_pair_local_dedicated_node(
        self,
        operation_id:str,
        pairing_id:str,
        device_name:str,
        node_root:str,
        runtime_dir:str,
        acknowledge_dedicated_node_scope:bool=False,
    )->dict:
        """Consume a one-time pairing entirely inside the gateway host process.

        The pairing code never crosses the MCP tool boundary and never enters
        shell/job arguments.  This action only materializes the paired local
        node identity; starting/supervising the node remains a separate action.
        """
        if not bool(acknowledge_dedicated_node_scope):
            raise DurableError(
                "DEDICATED_NODE_SCOPE_ACK_REQUIRED",
                "explicit dedicated-node scope acknowledgement is required",
            )
        name=str(device_name or "").strip()
        if not name or len(name)>128 or any(c in name for c in "\r\n"):
            raise DurableError("INVALID_ARGUMENT","invalid device_name")
        pair_id=str(pairing_id or "").strip()
        if not pair_id:
            raise DurableError("INVALID_ARGUMENT","pairing_id is required")
        root=self._dedicated_local_path(node_root,"node_root")
        runtime=self._dedicated_local_path(runtime_dir,"runtime_dir")
        if root==runtime:
            raise DurableError(
                "DEDICATED_NODE_SCOPE_INVALID",
                "node_root and runtime_dir must be distinct",
            )

        args={
            "pairing_id":pair_id,
            "device_name":name,
            "node_root":str(root),
            "runtime_dir":str(runtime),
            "acknowledge_dedicated_node_scope":True,
        }
        op,created=self._reserve(
            operation_id,
            "DEVICE_PAIR_LOCAL_DEDICATED_NODE",
            args,
        )
        if op["state"]==OperationState.SUCCEEDED.value:
            saved=self.durable.operations.replay_result(op) or {}
            return {**saved,"replayed":True}
        if op["state"]==OperationState.FAILED_FINAL.value:
            raise DurableError(
                op["error_code"] or "INVALID_ARGUMENT",
                "previous local pairing operation failed",
            )
        if created or op["state"] in {
            OperationState.RESERVED.value,
            OperationState.FAILED_RETRYABLE.value,
        }:
            self.durable.operations.mark_executing(operation_id)

        row=self.db.query_one(
            "SELECT * FROM device_pairings WHERE pairing_id=?",
            (pair_id,),
        )
        if row is None or row["owner_account_id"]!=self.owner_account_id:
            self.durable.operations.fail(
                operation_id,
                "DEVICE_PAIRING_INVALID",
                {"pairing_id":pair_id},
                False,
            )
            raise DurableError("DEVICE_PAIRING_INVALID","unknown pairing")
        if str(row["requested_name"])!=name:
            self.durable.operations.fail(
                operation_id,
                "DEVICE_PAIRING_INVALID",
                {"pairing_id":pair_id,"reason":"device_name mismatch"},
                False,
            )
            raise DurableError("DEVICE_PAIRING_INVALID","device_name mismatch")

        cfg=NodeConfig.create(self.config.public_origin,root,runtime)
        node_db=NodeDatabase(cfg.runtime_dir)
        node_db.bootstrap()

        # If the central pairing was already consumed, only recover when the
        # exact local private/public identity that consumed it still exists.
        if row["used_at_ms"] is not None:
            paired_id=row["paired_device_id"]
            if not paired_id:
                self.durable.operations.mark_in_doubt(
                    operation_id,{"pairing_id":pair_id,"reason":"used pairing lacks device id"}
                )
                raise DurableError(
                    "OPERATION_IN_DOUBT",
                    "pairing is used but paired device identity is unavailable",
                )
            if not (runtime/"device-ed25519.pem").exists():
                self.durable.operations.mark_in_doubt(
                    operation_id,
                    {"pairing_id":pair_id,"device_id":paired_id,"reason":"local key missing"},
                )
                raise DurableError(
                    "OPERATION_IN_DOUBT",
                    "pairing was consumed but the local node private key is missing",
                )
            ident=NodeIdentity(cfg.runtime_dir,node_db)
            central=self.devices.get(str(paired_id))
            if (
                str(central["public_key_b64"])!=ident.public_key_b64
                or str(central["key_fingerprint_sha256"])!=ident.fingerprint
            ):
                self.durable.operations.fail(
                    operation_id,
                    "NODE_IDENTITY_MISMATCH",
                    {"pairing_id":pair_id,"device_id":str(paired_id)},
                    False,
                )
                raise DurableError(
                    "NODE_IDENTITY_MISMATCH",
                    "consumed pairing belongs to a different local node identity",
                )
            response={
                "device_id":central["device_id"],
                "device_name":central["device_name"],
                "origin":self.config.public_origin,
                "route_generation":int(central["route_generation"]),
                "state":self.devices.effective_state(central),
                "public_key_b64":central["public_key_b64"],
                "key_fingerprint_sha256":central["key_fingerprint_sha256"],
                "paired_at_ms":int(central["paired_at_ms"]),
            }
            if ident.paired:
                if (
                    str(ident.device.get("device_id"))!=str(paired_id)
                    or str(ident.device.get("device_name"))!=name
                    or str(ident.device.get("origin")).rstrip("/")!=cfg.origin.rstrip("/")
                    or Path(str(ident.device.get("root"))).resolve()!=cfg.root
                ):
                    self.durable.operations.fail(
                        operation_id,
                        "NODE_IDENTITY_MISMATCH",
                        {"pairing_id":pair_id,"device_id":str(paired_id)},
                        False,
                    )
                    raise DurableError(
                        "NODE_IDENTITY_MISMATCH",
                        "existing local paired identity has a different scope",
                    )
            else:
                ident.persist_paired(
                    response,
                    origin=cfg.origin,
                    root=cfg.root,
                    device_name=name,
                )
            result=self._local_pairing_result(
                pairing_id=pair_id,
                device_name=name,
                node_root=root,
                runtime_dir=runtime,
                device_id=str(paired_id),
                recovered=True,
            )
            self.durable.operations.succeed(operation_id,result)
            return result

        if int(row["expires_at_ms"])<=now_ms():
            self.durable.operations.fail(
                operation_id,
                "DEVICE_PAIRING_EXPIRED",
                {"pairing_id":pair_id},
                False,
            )
            raise DurableError("DEVICE_PAIRING_EXPIRED","pairing expired")

        ident=NodeIdentity(cfg.runtime_dir,node_db)
        if ident.paired:
            self.durable.operations.fail(
                operation_id,
                "NODE_IDENTITY_MISMATCH",
                {"pairing_id":pair_id,"reason":"runtime already paired"},
                False,
            )
            raise DurableError(
                "NODE_IDENTITY_MISMATCH",
                "runtime_dir already contains a paired node identity",
            )

        timestamp,nonce,signature=sign_pair(ident,pair_id,name)
        response=self.pairing.consume_local_identity(
            pair_id,
            name,
            public_key_b64=ident.public_key_b64,
            timestamp_ms=timestamp,
            nonce=nonce,
            signature_b64=signature,
            platform=node_platform(),
            capabilities={
                "outbound_node":True,
                "managed_local_pairing":True,
            },
        )
        ident.persist_paired(
            response,
            origin=cfg.origin,
            root=cfg.root,
            device_name=name,
        )
        result=self._local_pairing_result(
            pairing_id=pair_id,
            device_name=name,
            node_root=root,
            runtime_dir=runtime,
            device_id=str(response["device_id"]),
            recovered=False,
        )
        self.durable.operations.succeed(operation_id,result)
        return result

    def device_list(self)->dict:
        return {"devices":[self.devices.status(r["device_id"]) for r in self.devices.list()]}

    def device_status(self,device_id:str)->dict:
        return self.devices.status(device_id)

    def device_capacity_status(self,device_id:str)->dict:
        """Return the signed node-heartbeat capacity truth.

        Central routed-job inventory is intentionally reported separately and
        must never be substituted for node-local active workload.
        """
        status=self.devices.status(device_id)
        reported=status.get("authoritative_active_node_jobs")
        fresh=bool(status.get("capacity_signal_fresh"))
        if status["state"]!="ONLINE":
            resolved=False
            reason="DEVICE_OFFLINE"
        elif reported is None:
            resolved=False
            reason="NODE_HEARTBEAT_CAPACITY_UNAVAILABLE"
        elif not fresh:
            resolved=False
            reason="NODE_HEARTBEAT_CAPACITY_STALE"
        else:
            resolved=True
            reason="AUTHORITATIVE_NODE_HEARTBEAT"
        active=int(reported) if resolved else None
        if not resolved:
            recommendation="RECHECK_NODE_CAPACITY"
        elif active==0:
            recommendation="NO_ACTIVE_NODE_JOBS_RECHECK_CPU_RAM_AND_OTHER_RESOURCE_GATES"
        else:
            recommendation="ACTIVE_NODE_WORKLOAD_PRESENT_RECHECK_CPU_RAM_BEFORE_ONE_SHOT"
        return self._with_routing(
            {
                "device_id":device_id,
                "device_name":status.get("device_name"),
                "device_state":status["state"],
                "capacity_resolved":resolved,
                "capacity_reason":reason,
                "authoritative_active_node_jobs":active,
                "reported_active_node_jobs":reported,
                "capacity_signal_source":status.get("capacity_signal_source"),
                "capacity_signal_fresh":fresh,
                "capacity_signal_age_ms":status.get(
                    "authoritative_active_node_jobs_age_ms"
                ),
                "registry_nonterminal_routed_jobs":int(
                    status.get("registry_nonterminal_routed_jobs",0)
                ),
                "registry_count_is_capacity_signal":False,
                "resource_gate_rule":"USE_AUTHORITATIVE_NODE_COUNT_ONLY",
                "recommendation":recommendation,
            },
            device_id,
        )

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
        capacity=self.device_capacity_status(device_id)
        if not capacity["capacity_resolved"]:
            raise DurableError(
                "DEVICE_CAPACITY_UNRESOLVED",
                "authoritative node workload is unavailable; restart fails closed",
                device_id=device_id,
                capacity_reason=capacity["capacity_reason"],
                registry_nonterminal_routed_jobs=capacity[
                    "registry_nonterminal_routed_jobs"
                ],
            )
        active_jobs=int(capacity["authoritative_active_node_jobs"])
        if active_jobs>0 and not bool(allow_active_jobs):
            raise DurableError(
                "DEVICE_BUSY",
                "device has authoritative active node jobs; restart requires explicit allow_active_jobs=true",
                device_id=device_id,
                authoritative_active_node_jobs=active_jobs,
                registry_nonterminal_routed_jobs=capacity[
                    "registry_nonterminal_routed_jobs"
                ],
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
                "authoritative_active_node_jobs_at_request":active_jobs,
                "registry_nonterminal_routed_jobs_at_request":capacity[
                    "registry_nonterminal_routed_jobs"
                ],
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
        worktree_rel=task_worktree_rel(project_id,task_id) if project["project_kind"]=="GIT" else None
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

    def _sync_routed_admission(self,row):
        admission=self.job_admissions.by_operation(row["operation_id"])
        if admission is None:
            return
        if admission["job_id"] is None:
            self.job_admissions.bind(row["operation_id"],row["proxy_job_id"])
        if row["last_known_state"] in TERMINAL and row["terminal_result_json"]:
            evidence=json.loads(row["terminal_result_json"])
            self.job_admissions.terminalize(
                row["operation_id"],row["last_known_state"],evidence,
            )

    async def _ensure_routed_terminal_evidence(self,binding,row):
        if row["last_known_state"] not in TERMINAL:
            raise DurableError(
                "PREDECESSOR_JOB_NOT_TERMINAL",
                "routed predecessor is still non-terminal",
                predecessor_job_id=row["proxy_job_id"],
                predecessor_state=row["last_known_state"],
            )
        if not row["terminal_result_json"]:
            if self.devices.status(binding["device_id"])["state"]!="ONLINE":
                raise DurableError(
                    "PREDECESSOR_STATE_UNRESOLVED",
                    "terminal routed predecessor lacks cached evidence while device is offline",
                    predecessor_job_id=row["proxy_job_id"],
                )
            try:
                result,_=await self._route_step(
                    binding["device_id"],"JOB_RESULT",
                    {"proxy_job_id":row["proxy_job_id"]},
                    project_id=binding["project_id"],task_id=row["task_id"],
                )
            except Exception as exc:
                raise DurableError(
                    "PREDECESSOR_STATE_UNRESOLVED",
                    "could not obtain authoritative routed predecessor terminal evidence",
                    predecessor_job_id=row["proxy_job_id"],
                ) from exc
            if not result.get("terminal") or result.get("state") not in TERMINAL:
                raise DurableError(
                    "PREDECESSOR_STATE_UNRESOLVED",
                    "routed predecessor terminal evidence is incomplete",
                    predecessor_job_id=row["proxy_job_id"],
                )
            row=self.routed_jobs.update(
                row["proxy_job_id"],
                node_job_id=result.get("node_job_id"),
                state=result.get("state"),
                terminal_result=result,
            )
        self._sync_routed_admission(row)
        return row

    async def _reconcile_routed_lane(
        self,binding,task_id:str,current_operation_id:str|None=None
    ):
        state=self.devices.status(binding["device_id"])["state"]
        rows=self.routed_jobs.list_task_rows(task_id)
        for row in rows:
            if current_operation_id and row["operation_id"]==current_operation_id:
                continue
            if row["last_known_state"] in TERMINAL:
                continue
            if state!="ONLINE":
                raise DurableError(
                    "PREDECESSOR_STATE_UNRESOLVED",
                    "cannot reconcile non-terminal routed predecessor while device is offline",
                    predecessor_job_id=row["proxy_job_id"],
                )
            try:
                remote,_=await self._route_step(
                    binding["device_id"],"JOB_GET",
                    {"proxy_job_id":row["proxy_job_id"]},
                    project_id=binding["project_id"],task_id=task_id,
                )
            except Exception as exc:
                raise DurableError(
                    "PREDECESSOR_STATE_UNRESOLVED",
                    "routed predecessor could not be resolved from the bound node",
                    predecessor_job_id=row["proxy_job_id"],
                ) from exc
            row=self.routed_jobs.update(
                row["proxy_job_id"],
                node_job_id=remote.get("node_job_id"),
                state=remote.get("state"),
            )
            if row["last_known_state"] not in TERMINAL:
                raise DurableError(
                    "PREDECESSOR_JOB_NOT_TERMINAL",
                    "task lane predecessor is still non-terminal",
                    predecessor_job_id=row["proxy_job_id"],
                    predecessor_state=row["last_known_state"],
                )
            await self._ensure_routed_terminal_evidence(binding,row)

        rows=[
            r for r in self.routed_jobs.list_task_rows(task_id)
            if not current_operation_id or r["operation_id"]!=current_operation_id
        ]
        if rows:
            latest=rows[-1]
            if latest["last_known_state"] not in TERMINAL:
                raise DurableError(
                    "PREDECESSOR_JOB_NOT_TERMINAL",
                    "latest task lane predecessor is non-terminal",
                    predecessor_job_id=latest["proxy_job_id"],
                    predecessor_state=latest["last_known_state"],
                )
            latest=await self._ensure_routed_terminal_evidence(binding,latest)
            return latest["proxy_job_id"]
        return None

    @staticmethod
    def _argv_sha256(argv:list[str])->str:
        return hashlib.sha256(
            json.dumps(argv,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _submit_intent_operation_id(operation_id:str)->str:
        return "rsi_"+hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:40]

    def _freeze_routed_submit_intent(
        self,operation_id:str,task_id:str,binding,lease,argv:list[str],cwd:str,
        evidence_paths:list[str],*,legacy_adopted:bool=False,
        original_lease_epoch:int|None=None,
    )->dict:
        intent={
            "original_operation_id":operation_id,
            "task_id":task_id,
            "project_id":binding["project_id"],
            "device_id":binding["device_id"],
            "binding_generation":int(binding["binding_generation"]),
            "original_agent_id":str(lease["agent_id"]),
            "original_lease_epoch":int(
                lease["lease_epoch"] if original_lease_epoch is None else original_lease_epoch
            ),
            "argv":list(argv),
            "argv_sha256":self._argv_sha256(list(argv)),
            "cwd":str(cwd),
            "evidence_paths":list(evidence_paths),
            "legacy_adopted":bool(legacy_adopted),
        }
        intent_id=self._submit_intent_operation_id(operation_id)
        op,created=self._reserve(
            intent_id,"TASK_JOB_SUBMIT_INTENT",intent,
            agent_id="",project_id=binding["project_id"],task_id=task_id,
        )
        if op["state"]==OperationState.SUCCEEDED.value:
            frozen=self.durable.operations.replay_result(op) or {}
            if frozen!=intent:
                raise DurableError(
                    "OPERATION_CONFLICT",
                    "frozen routed submit intent differs from the requested submission",
                    operation_id=operation_id,
                )
            return frozen
        if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
            self._operation_replay(op)
        if created:
            self.durable.operations.mark_executing(intent_id)
        # The intent operation is a deterministic metadata freeze; replay after
        # a crash between reserve/mark and succeed is safe with the same hash.
        self.durable.operations.succeed(intent_id,intent)
        return intent

    def _routed_submit_intent(self,operation_id:str)->dict|None:
        row=self.durable.operations.get(self._submit_intent_operation_id(operation_id))
        if row is None or row["kind"]!="TASK_JOB_SUBMIT_INTENT":
            return None
        if row["state"]!=OperationState.SUCCEEDED.value:
            return None
        result=self.durable.operations.replay_result(row)
        return result if isinstance(result,dict) else None

    def _submit_command_for_operation(self,operation_id:str):
        return self.db.query_one(
            "SELECT * FROM device_commands WHERE operation_id=? AND command_type='JOB_SUBMIT' "
            "ORDER BY operation_step,created_at_ms,command_id LIMIT 1",
            (operation_id,),
        )

    def task_job_submit_failure_status(self,task_id:str,operation_id:str)->dict:
        binding=self.bindings.require_task_binding(task_id)
        op=self.durable.operations.get(operation_id)
        if op is None or op["kind"]!="TASK_JOB_SUBMIT" or op["task_id"]!=task_id:
            raise DurableError(
                "NOT_FOUND","routed task-job submit operation not found",
                task_id=task_id,operation_id=operation_id,
            )
        intent=self._routed_submit_intent(operation_id)
        admission=self.job_admissions.by_operation(operation_id)
        proxy=self.routed_jobs.by_operation(operation_id)
        command=self._submit_command_for_operation(operation_id)
        device_state=self.devices.status(binding["device_id"])["state"]

        if proxy is not None:
            if proxy["last_known_state"] in TERMINAL and proxy["terminal_result_json"]:
                phase="TERMINAL"
                recommended="POSTRUN_EVIDENCE_OR_ADJUDICATION"
            elif proxy["node_job_id"] is not None:
                phase="EXECUTION_STARTED"
                recommended="OBSERVE_EXISTING_PROXY"
            elif command is not None:
                phase="POST_PROXY_PRE_NODE"
                recommended="USE_SAME_PROXY_RECOVERY_STATUS"
            else:
                phase="PROXY_CREATED"
                recommended="USE_SAME_PROXY_RECOVERY_STATUS"
        elif admission is not None:
            phase="ADMISSION_RESERVED"
            recommended="RECOVER_PREPROXY_SUBMIT"
        else:
            phase="BEFORE_ADMISSION"
            recommended="RECOVER_PREPROXY_SUBMIT"

        recoverable_errors={
            "PREDECESSOR_STATE_UNRESOLVED",
            "PREDECESSOR_JOB_NOT_TERMINAL",
            "TASK_JOB_ADMISSION_CONFLICT",
            "DEVICE_OFFLINE",
            "DB_BUSY",
        }
        identity_ok=bool(
            intent is not None
            and intent.get("task_id")==task_id
            and intent.get("project_id")==binding["project_id"]
            and intent.get("device_id")==binding["device_id"]
            and int(intent.get("binding_generation",-1))==int(binding["binding_generation"])
        )
        admission_ok=(
            admission is None
            or (
                admission["task_id"]==task_id
                and admission["execution_kind"]=="ROUTED"
                and admission["state"]=="ADMITTING"
                and admission["job_id"] is None
            )
        )
        preproxy_recoverable=bool(
            proxy is None
            and op["state"]==OperationState.FAILED_FINAL.value
            and op["error_code"] in recoverable_errors
            and identity_ok
            and admission_ok
        )
        if proxy is None and intent is None:
            recommended="ADOPT_LEGACY_SUBMIT_INTENT"
        elif proxy is None and not preproxy_recoverable:
            if op["state"] not in {
                OperationState.FAILED_FINAL.value,
                OperationState.SUCCEEDED.value,
            }:
                recommended="OBSERVE_OR_RECHECK_SUBMIT_OPERATION"
            elif op["error_code"] not in recoverable_errors:
                recommended="FAIL_CLOSED_NONRECOVERABLE_SUBMIT_ERROR"

        out={
            "task_id":task_id,
            "operation_id":operation_id,
            "operation_state":op["state"],
            "error_code":op["error_code"],
            "phase":phase,
            "device_state":device_state,
            "intent_available":intent is not None,
            "preproxy_recoverable":preproxy_recoverable,
            "recommended_action":recommended,
            "scientific_execution_started":bool(
                proxy is not None and (
                    proxy["node_job_id"] is not None
                    or proxy["last_known_state"] not in {"QUEUED"}
                )
            ),
            "replacement_scientific_job_forbidden":True,
        }
        if intent is not None:
            out["frozen_submit_intent"]={
                "argv_sha256":intent["argv_sha256"],
                "cwd":intent["cwd"],
                "evidence_paths":list(intent.get("evidence_paths") or []),
                "original_lease_epoch":int(intent["original_lease_epoch"]),
                "device_id":intent["device_id"],
                "binding_generation":int(intent["binding_generation"]),
                "legacy_adopted":bool(intent.get("legacy_adopted",False)),
            }
        if admission is not None:
            out["admission"]=self.job_admissions.as_dict(admission)
        if proxy is not None:
            out["proxy"]=self.routed_jobs.as_dict(proxy,device_state)
            if proxy["node_job_id"] is None:
                _,_,_,same_proxy=self._routed_submit_recovery_status(
                    task_id,proxy["proxy_job_id"]
                )
                out["same_proxy_recovery"]=same_proxy
        if command is not None:
            out["submit_command"]={
                "command_id":command["command_id"],
                "state":command["state"],
                "error_code":command["error_code"],
                "delivery_attempt":int(command["delivery_attempt"]),
            }
        return self._with_routing(
            out,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    def task_job_adopt_legacy_submit_intent(
        self,task_id:str,lease_token:str,lease_epoch:int,operation_id:str,
        argv:list[str],cwd:str=".",evidence_paths:list[str]|None=None,
    )->dict:
        evidence_paths=list(evidence_paths or [])
        binding=self.bindings.require_task_binding(task_id)
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        op=self.durable.operations.get(operation_id)
        if op is None or op["kind"]!="TASK_JOB_SUBMIT" or op["task_id"]!=task_id:
            raise DurableError("NOT_FOUND","legacy routed submit operation not found")
        if self.routed_jobs.by_operation(operation_id) is not None:
            raise DurableError(
                "PREEXECUTION_RECOVERY_NOT_ELIGIBLE",
                "legacy submit already has a routed proxy; use proxy-scoped recovery",
                operation_id=operation_id,
            )
        existing=self._routed_submit_intent(operation_id)
        if existing is not None:
            return {
                "operation_id":operation_id,
                "intent_adopted":False,
                "replayed":True,
                "argv_sha256":existing["argv_sha256"],
            }

        # Older releases did not persist submit arguments.  Recover them only
        # when caller-supplied values reproduce the exact historical request
        # hash.  Lease epoch is discovered by hash equality, never guessed into
        # execution semantics.
        matched_epoch=None
        matched_evidence_paths=None
        max_epoch=max(1,int(lease_epoch))
        for candidate_epoch in range(1,max_epoch+1):
            candidates=[
                {
                    "task_id":task_id,
                    "lease_epoch":candidate_epoch,
                    "argv":list(argv),
                    "cwd":str(cwd),
                    "evidence_paths":evidence_paths,
                },
                {
                    "task_id":task_id,
                    "lease_epoch":candidate_epoch,
                    "argv":list(argv),
                    "cwd":str(cwd),
                },
            ]
            for candidate in candidates:
                rh=request_hash(
                    kind="TASK_JOB_SUBMIT",
                    normalized_arguments=candidate,
                    principal_key=op["principal_key"] or "",
                    agent_id=op["agent_id"] or "",
                    project_id=op["project_id"] or "",
                    task_id=op["task_id"] or "",
                )
                if rh==op["request_hash"]:
                    matched_epoch=candidate_epoch
                    matched_evidence_paths=(
                        evidence_paths if "evidence_paths" in candidate else []
                    )
                    break
            if matched_epoch is not None:
                break
        if matched_epoch is None:
            raise DurableError(
                "LEGACY_SUBMIT_INTENT_MISMATCH",
                "supplied legacy argv/cwd do not match the exact historical submit request",
                operation_id=operation_id,
            )
        original_lease={
            "agent_id":op["agent_id"] or "",
            "lease_epoch":matched_epoch,
        }
        intent=self._freeze_routed_submit_intent(
            operation_id,task_id,binding,original_lease,list(argv),str(cwd),
            list(matched_evidence_paths or []),
            legacy_adopted=True,original_lease_epoch=matched_epoch,
        )
        return self._with_routing(
            {
                "operation_id":operation_id,
                "intent_adopted":True,
                "argv_sha256":intent["argv_sha256"],
                "original_lease_epoch":matched_epoch,
                "evidence_paths":list(intent.get("evidence_paths") or []),
            },
            binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_recover_preproxy_submit(
        self,task_id:str,lease_token:str,lease_epoch:int,operation_id:str,
        expected_original_argv_sha256:str,
    )->dict:
        binding=self.bindings.require_task_binding(task_id)
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        dev=self.devices.require_online(binding["device_id"])
        status=self.task_job_submit_failure_status(task_id,operation_id)
        intent=self._routed_submit_intent(operation_id)
        if intent is None:
            raise DurableError(
                "PREEXECUTION_RECOVERY_NOT_ELIGIBLE",
                "frozen submit intent is unavailable; adopt legacy intent first when applicable",
                operation_id=operation_id,
            )
        if str(expected_original_argv_sha256)!=str(intent["argv_sha256"]):
            raise DurableError(
                "COMMAND_CONFLICT",
                "original argv hash acknowledgement does not match frozen submit intent",
                operation_id=operation_id,
                expected_argv_sha256=intent["argv_sha256"],
            )
        if (
            intent["device_id"]!=binding["device_id"]
            or int(intent["binding_generation"])!=int(binding["binding_generation"])
        ):
            raise DurableError(
                "PROJECT_DEVICE_BINDING_CONFLICT",
                "task/device binding changed since the frozen submit intent",
                operation_id=operation_id,
            )

        recovery_operation_id=(
            "rpr_"+hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:40]
        )
        existing_recovery=self.durable.operations.get(recovery_operation_id)
        if existing_recovery is not None:
            replay=self._operation_replay(existing_recovery)
            if replay is not None:
                replay=self._with_routing(
                    replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                    binding_generation=int(binding["binding_generation"]),
                )
                return {**replay,"replayed":True}
            existing_proxy=self.routed_jobs.by_operation(operation_id)
            if existing_proxy is not None:
                cmd=self._submit_command_for_operation(operation_id)
                if cmd is None:
                    raise DurableError(
                        "PREEXECUTION_RECOVERY_UNRESOLVED",
                        "recovery proxy exists without its original submit command",
                        operation_id=operation_id,
                        proxy_job_id=existing_proxy["proxy_job_id"],
                    )
                try:
                    terminal=await self.commands.wait(cmd["command_id"])
                    result=self.commands.result_value(terminal)
                    row=self.routed_jobs.update(
                        existing_proxy["proxy_job_id"],
                        node_job_id=result.get("node_job_id"),
                        state=result.get("state","QUEUED"),
                    )
                    response={
                        **self.routed_jobs.as_dict(
                            row,self.devices.get(binding["device_id"])["state"]
                        ),
                        "recovery_kind":"PREPROXY_SAME_LOGICAL_SUBMIT",
                        "original_operation_id":operation_id,
                        "same_logical_submit_preserved":True,
                        "argv_sha256":intent["argv_sha256"],
                        "scientific_execution_was_started_before_recovery":False,
                    }
                    response=self._with_routing(
                        response,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                        binding_generation=int(binding["binding_generation"]),
                    )
                    self.durable.operations.succeed(recovery_operation_id,response)
                    return response
                except Exception as exc:
                    self._fail_final(recovery_operation_id,exc)
                    raise
        elif not status["preproxy_recoverable"]:
            raise DurableError(
                "PREEXECUTION_RECOVERY_NOT_ELIGIBLE",
                "submit is not eligible for pre-proxy recovery",
                operation_id=operation_id,
                phase=status["phase"],
                error_code=status.get("error_code"),
            )

        # Reconcile every earlier routed predecessor first.  If one is still
        # active or unresolved, this raises without creating a proxy or recovery
        # operation, so a later agent may retry the same logical recovery.
        predecessor=await self._reconcile_routed_lane(
            binding,task_id,current_operation_id=operation_id
        )
        admission=self.job_admissions.by_operation(operation_id)
        if admission is None:
            self.job_admissions.reserve(
                task_id,operation_id,"ROUTED",predecessor_job_id=predecessor
            )
        else:
            if (
                admission["task_id"]!=task_id
                or admission["execution_kind"]!="ROUTED"
                or admission["state"]!="ADMITTING"
                or admission["job_id"] is not None
            ):
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "existing admission cannot be resumed as a pre-proxy submit",
                    operation_id=operation_id,
                )

        if self.routed_jobs.by_operation(operation_id) is not None:
            raise DurableError(
                "TASK_JOB_LANE_STATE_MISMATCH",
                "a routed proxy appeared during pre-proxy recovery",
                operation_id=operation_id,
            )

        recovery_args={
            "task_id":task_id,
            "original_operation_id":operation_id,
            "original_argv_sha256":intent["argv_sha256"],
            "device_id":binding["device_id"],
            "binding_generation":int(binding["binding_generation"]),
        }
        recovery_op,created=self._reserve(
            recovery_operation_id,"TASK_JOB_RECOVER_PREPROXY",recovery_args,
            agent_id="",project_id=binding["project_id"],task_id=task_id,
        )
        if created:
            self.durable.operations.mark_executing(recovery_operation_id)

        try:
            with self.db.transaction() as con:
                proxy,_=self.routed_jobs.create(
                    con,operation_id,task_id,binding["project_id"],binding["device_id"]
                )
                payload={
                    "proxy_job_id":proxy["proxy_job_id"],
                    "task_id":task_id,
                    "argv":list(intent["argv"]),
                    "cwd":str(intent["cwd"]),
                    "evidence_paths":list(intent.get("evidence_paths") or []),
                }
                cmd,_=self.commands.create_in_tx(
                    con,binding["device_id"],"JOB_SUBMIT",payload,
                    route_generation=int(dev["route_generation"]),
                    project_id=binding["project_id"],task_id=task_id,
                    # Keep the command under the original logical submit
                    # operation so proxy-scoped recovery can find it later.
                    operation_id=operation_id,operation_step=0,
                    expires_at_ms=min(
                        int(lease["expires_at_ms"]),
                        now_ms()+self.config.mutation_ttl_seconds*1000,
                    ),
                )
            self.job_admissions.bind(operation_id,proxy["proxy_job_id"])
            terminal=await self.commands.wait(cmd["command_id"])
            result=self.commands.result_value(terminal)
            row=self.routed_jobs.update(
                proxy["proxy_job_id"],
                node_job_id=result.get("node_job_id"),
                state=result.get("state","QUEUED"),
            )
            response={
                **self.routed_jobs.as_dict(
                    row,self.devices.get(binding["device_id"])["state"]
                ),
                "recovery_kind":"PREPROXY_SAME_LOGICAL_SUBMIT",
                "original_operation_id":operation_id,
                "same_logical_submit_preserved":True,
                "argv_sha256":intent["argv_sha256"],
                "scientific_execution_was_started_before_recovery":False,
            }
            response=self._with_routing(
                response,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(recovery_operation_id,response)
            return response
        except Exception as exc:
            self._fail_final(recovery_operation_id,exc)
            raise

    async def task_job_submit_or_local(
        self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,
        argv:list[str],cwd:str=".",evidence_paths:list[str]|None=None
    )->dict:
        evidence_paths=list(evidence_paths or [])
        enforce_agent_git_worktree_policy(argv)
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            if evidence_paths:
                raise DurableError(
                    "INVALID_ARGUMENT",
                    "evidence_paths are currently supported only for routed task jobs",
                )
            return await self.multi.task_job_submit(
                operation_id,task_id,lease_token,lease_epoch,argv,cwd
            )
        dev=self.devices.require_online(binding["device_id"])
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        args={
            "task_id":task_id,"lease_epoch":int(lease_epoch),
            "argv":argv,"cwd":cwd,"evidence_paths":evidence_paths,
        }
        op,created=self._reserve(
            operation_id,"TASK_JOB_SUBMIT",args,agent_id=lease["agent_id"],
            project_id=binding["project_id"],task_id=task_id,
        )
        self._freeze_routed_submit_intent(
            operation_id,task_id,binding,lease,list(argv),str(cwd),evidence_paths
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

        existing_admission=self.job_admissions.by_operation(operation_id)
        existing_proxy=self.routed_jobs.by_operation(operation_id)
        if existing_admission is None and existing_proxy is None:
            try:
                predecessor=await self._reconcile_routed_lane(binding,task_id)
                self.job_admissions.reserve(
                    task_id,operation_id,"ROUTED",
                    predecessor_job_id=predecessor,
                )
            except DurableError as exc:
                self._fail_final(operation_id,exc)
                raise
        elif existing_admission is not None:
            if (
                existing_admission["task_id"]!=task_id or
                existing_admission["execution_kind"]!="ROUTED"
            ):
                exc=DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "operation replay targets a different routed task lane",
                )
                self._fail_final(operation_id,exc)
                raise exc

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
                "evidence_paths":evidence_paths,
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
        if self.job_admissions.by_operation(operation_id) is not None:
            self.job_admissions.bind(operation_id,proxy["proxy_job_id"])
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
                if self.routed_jobs.by_operation(operation_id) is None:
                    self.job_admissions.abort_if_unbound(operation_id,exc.code)
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

    def _routed_submit_recovery_status(self,task_id:str,proxy_job_id:str)->tuple[dict,dict,dict|None,dict]:
        binding,row=self._task_proxy(task_id,proxy_job_id)
        command=self.db.query_one(
            "SELECT * FROM device_commands WHERE operation_id=? AND command_type='JOB_SUBMIT' "
            "ORDER BY operation_step,created_at_ms,command_id LIMIT 1",
            (row["operation_id"],),
        )
        device_state=self.devices.status(binding["device_id"])["state"]
        out={
            **self.routed_jobs.as_dict(row,device_state),
            "recoverable":False,
            "recovery_kind":None,
            "recovery_reason":None,
            "same_proxy_identity_required":True,
            "replacement_job_forbidden":True,
        }
        if command is None:
            out["recovery_reason"]="ORIGINAL_SUBMIT_COMMAND_MISSING"
            return binding,row,None,out
        payload=json.loads(command["payload_json"])
        argv=payload.get("argv")
        argv_sha256=None
        if isinstance(argv,list):
            argv_sha256=hashlib.sha256(
                json.dumps(argv,ensure_ascii=False,separators=(",",":")).encode("utf-8")
            ).hexdigest()
        out["original_submit"]={
            "command_id":command["command_id"],
            "state":command["state"],
            "error_code":command["error_code"],
            "delivery_attempt":int(command["delivery_attempt"]),
            "cwd":payload.get("cwd","."),
            "argv_sha256":argv_sha256,
            "evidence_paths":list(payload.get("evidence_paths") or []),
        }
        admission=self.job_admissions.by_operation(row["operation_id"])
        if admission is not None:
            out["admission"]={
                "state":admission["state"],
                "execution_kind":admission["execution_kind"],
                "job_id":admission["job_id"],
            }
        if row["node_job_id"] is not None:
            out["recovery_reason"]="NODE_JOB_ALREADY_MAPPED"
        elif row["terminal_result_json"] or row["last_known_state"] in TERMINAL:
            out["recovery_reason"]="ROUTED_JOB_ALREADY_TERMINAL"
        elif row["last_known_state"]!="QUEUED":
            out["recovery_reason"]="ROUTED_JOB_NOT_PREEXECUTION_QUEUED"
        elif command["state"]!="FAILED":
            out["recovery_reason"]="ORIGINAL_SUBMIT_NOT_FAILED"
        elif command["error_code"]!="PATH_ESCAPE":
            out["recovery_reason"]="UNSUPPORTED_PREEXECUTION_FAILURE"
        elif command["result_json"] is not None:
            out["recovery_reason"]="ORIGINAL_SUBMIT_HAS_RESULT"
        elif not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):
            out["recovery_reason"]="ORIGINAL_ARGV_INVALID"
        elif payload.get("proxy_job_id")!=proxy_job_id or payload.get("task_id")!=task_id:
            out["recovery_reason"]="ORIGINAL_SUBMIT_IDENTITY_MISMATCH"
        elif admission is not None and (
            admission["task_id"]!=task_id or
            admission["execution_kind"]!="ROUTED" or
            admission["job_id"] not in (None,proxy_job_id) or
            admission["state"]=="TERMINAL"
        ):
            out["recovery_reason"]="JOB_ADMISSION_IDENTITY_MISMATCH"
        else:
            out["recoverable"]=True
            out["recovery_kind"]="PATH_ESCAPE_TO_MANAGED_TASK_ROOT"
            out["recovery_reason"]="ELIGIBLE"
            out["required_recovery_cwd"]="."
            out["requires_cwd_semantics_acknowledgement"]=True
        return binding,row,command,out

    def task_job_recovery_status(self,task_id:str,proxy_job_id:str)->dict:
        binding,_,_,out=self._routed_submit_recovery_status(task_id,proxy_job_id)
        return self._with_routing(
            out,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_recover_path_escape(
        self,task_id:str,lease_token:str,lease_epoch:int,proxy_job_id:str,
        expected_original_argv_sha256:str,
        acknowledge_cwd_semantics_preserved:bool=False,
    )->dict:
        binding,row,command,status=self._routed_submit_recovery_status(task_id,proxy_job_id)
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        self.devices.require_online(binding["device_id"])
        if not acknowledge_cwd_semantics_preserved:
            raise DurableError(
                "RECOVERY_ACK_REQUIRED",
                "PATH_ESCAPE recovery changes cwd to the managed task root; caller must confirm cwd does not change execution semantics",
                proxy_job_id=proxy_job_id,
            )
        if command is None:
            raise DurableError(
                "PREEXECUTION_RECOVERY_NOT_ELIGIBLE",
                "original routed submit command is unavailable",
                proxy_job_id=proxy_job_id,
                reason=status.get("recovery_reason"),
            )
        argv_sha256=status["original_submit"]["argv_sha256"]
        if str(expected_original_argv_sha256)!=str(argv_sha256):
            raise DurableError(
                "COMMAND_CONFLICT",
                "original argv hash acknowledgement does not match frozen submit payload",
                proxy_job_id=proxy_job_id,
                expected_argv_sha256=argv_sha256,
            )

        recovery_operation_id=f"routed-preexec-path-recovery:{proxy_job_id}"
        existing_recovery=self.durable.operations.get(recovery_operation_id)
        if existing_recovery is not None:
            replay=self._operation_replay(existing_recovery)
            if replay is not None:
                replay=self._with_routing(
                    replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                    binding_generation=int(binding["binding_generation"]),
                )
                return {**replay,"replayed":True}
        elif not status["recoverable"]:
            raise DurableError(
                "PREEXECUTION_RECOVERY_NOT_ELIGIBLE",
                "routed submit is not eligible for guarded PATH_ESCAPE recovery",
                proxy_job_id=proxy_job_id,
                reason=status.get("recovery_reason"),
            )

        args={
            "task_id":task_id,
            "proxy_job_id":proxy_job_id,
            "original_command_id":command["command_id"],
            "original_argv_sha256":argv_sha256,
            "recovery_cwd":".",
        }
        op,created=self._reserve(
            recovery_operation_id,"TASK_JOB_RECOVER_PATH_ESCAPE",args,
            agent_id="",project_id=binding["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if created:
            self.durable.operations.mark_executing(recovery_operation_id)

        try:
            # First prove whether the node already has this exact proxy.  A
            # successful probe repairs central mapping instead of resubmitting.
            try:
                remote,_=await self._route_step(
                    binding["device_id"],"JOB_GET",{"proxy_job_id":proxy_job_id},
                    project_id=binding["project_id"],task_id=task_id,
                )
            except DurableError as exc:
                if exc.code!="NOT_FOUND":
                    raise DurableError(
                        "PREEXECUTION_RECOVERY_UNRESOLVED",
                        "could not prove authoritative absence of the proxy on the bound node",
                        proxy_job_id=proxy_job_id,
                        cause=exc.code,
                    ) from exc
            else:
                row=self.routed_jobs.update(
                    proxy_job_id,node_job_id=remote.get("node_job_id"),state=remote.get("state"),
                )
                response={
                    **self.routed_jobs.as_dict(row,"ONLINE"),
                    "recovery_kind":"MAPPING_REPAIRED_FROM_AUTHORITATIVE_NODE",
                    "same_proxy_identity_preserved":True,
                    "argv_sha256":argv_sha256,
                    "cwd_changed":False,
                }
                response=self._with_routing(
                    response,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                    binding_generation=int(binding["binding_generation"]),
                )
                self.durable.operations.succeed(recovery_operation_id,response)
                return response

            original_payload=json.loads(command["payload_json"])
            payload={
                "proxy_job_id":proxy_job_id,
                "task_id":task_id,
                "argv":list(original_payload["argv"]),
                "cwd":".",
                "evidence_paths":list(original_payload.get("evidence_paths") or []),
            }
            remote,_=await self._route_step(
                binding["device_id"],"JOB_SUBMIT",payload,
                operation_id=recovery_operation_id,operation_step=0,
                project_id=binding["project_id"],task_id=task_id,
                expires_at_ms=min(
                    int(lease["expires_at_ms"]),
                    now_ms()+self.config.mutation_ttl_seconds*1000,
                ),
            )
            row=self.routed_jobs.update(
                proxy_job_id,node_job_id=remote.get("node_job_id"),state=remote.get("state"),
            )
            response={
                **self.routed_jobs.as_dict(row,"ONLINE"),
                "recovery_kind":"PATH_ESCAPE_TO_MANAGED_TASK_ROOT",
                "same_proxy_identity_preserved":True,
                "original_command_id":command["command_id"],
                "argv_sha256":argv_sha256,
                "original_cwd":original_payload.get("cwd","."),
                "recovery_cwd":".",
            }
            response=self._with_routing(
                response,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(recovery_operation_id,response)
            return response
        except Exception as exc:
            self._fail_final(recovery_operation_id,exc)
            raise

    def _original_routed_submit(self,row):
        command=self.db.query_one(
            "SELECT * FROM device_commands WHERE operation_id=? AND command_type='JOB_SUBMIT' "
            "ORDER BY operation_step,created_at_ms,command_id LIMIT 1",
            (row["operation_id"],),
        )
        if command is None:
            return None,None
        return command,json.loads(command["payload_json"])

    async def _refresh_exact_routed_job_if_online(self,binding,row):
        if row["last_known_state"] in TERMINAL:
            return row
        if self.devices.status(binding["device_id"])["state"]!="ONLINE":
            return row
        remote,_=await self._route_step(
            binding["device_id"],"JOB_GET",{"proxy_job_id":row["proxy_job_id"]},
            project_id=binding["project_id"],task_id=row["task_id"],
        )
        return self.routed_jobs.update(
            row["proxy_job_id"],
            node_job_id=remote.get("node_job_id"),
            state=remote.get("state"),
        )

    async def task_job_artifact_status(
        self,task_id:str,proxy_job_id:str,path:str
    )->dict:
        binding,row=self._task_proxy(task_id,proxy_job_id)
        state=self.devices.status(binding["device_id"])["state"]

        # Always reconcile the exact existing scientific job first when
        # possible.  Readback classification must never hide or overwrite the
        # authoritative execution state.
        try:
            row=await self._refresh_exact_routed_job_if_online(binding,row)
        except DurableError as exc:
            base={
                **self.routed_jobs.as_dict(row,state),
                "path":path,
                "declared_evidence_paths":[],
                "readable":False,
                "readback_state":"POSTRUN_EVIDENCE_JOB_STATE_UNRESOLVED",
                "message":str(exc),
                "scientific_rerun_required":False,
                "automatic_scientific_rerun_for_readback_forbidden":True,
            }
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )

        command,payload=self._original_routed_submit(row)
        declared=list((payload or {}).get("evidence_paths") or [])
        base={
            **self.routed_jobs.as_dict(row,state),
            "path":path,
            "declared_evidence_paths":declared,
            "readable":False,
            "readback_state":"UNKNOWN",
            "scientific_rerun_required":False,
            "automatic_scientific_rerun_for_readback_forbidden":True,
        }

        if row["last_known_state"] not in TERMINAL:
            base["readback_state"]="POSTRUN_EVIDENCE_JOB_NOT_TERMINAL"
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )

        if not row["terminal_result_json"]:
            if state!="ONLINE":
                base["readback_state"]="POSTRUN_EVIDENCE_TERMINAL_RESULT_UNRESOLVED"
                base["message"]="terminal job is cached but authoritative terminal result is unavailable while device is offline"
                return self._with_routing(
                    base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                    binding_generation=int(binding["binding_generation"]),
                )
            try:
                row=await self._ensure_routed_terminal_evidence(binding,row)
                base.update(self.routed_jobs.as_dict(row,state))
            except DurableError as exc:
                base["readback_state"]="POSTRUN_EVIDENCE_TERMINAL_RESULT_UNRESOLVED"
                base["message"]=str(exc)
                return self._with_routing(
                    base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                    binding_generation=int(binding["binding_generation"]),
                )

        if command is None:
            base["readback_state"]="ORIGINAL_SUBMIT_COMMAND_MISSING"
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
        if not declared:
            base["readback_state"]="POSTRUN_EVIDENCE_PATH_UNDECLARED"
            base["message"]="legacy job has no predeclared evidence manifest; do not rerun science to repair readback"
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
        if state!="ONLINE":
            base["readback_state"]="POSTRUN_EVIDENCE_DEVICE_OFFLINE"
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )

        try:
            result,_=await self._route_step(
                binding["device_id"],"JOB_ARTIFACT_STAT",
                {"proxy_job_id":proxy_job_id,"path":path},
                project_id=binding["project_id"],task_id=task_id,
            )
        except DurableError as exc:
            base["readback_state"]=exc.code
            base["message"]=str(exc)
            return self._with_routing(
                base,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
                binding_generation=int(binding["binding_generation"]),
            )
        result={
            **base,
            **result,
            "readable":True,
            "readback_state":"READY",
            "scientific_rerun_required":False,
            "automatic_scientific_rerun_for_readback_forbidden":True,
        }
        return self._with_routing(
            result,binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

    async def task_job_artifact_read(
        self,task_id:str,proxy_job_id:str,path:str,expected_sha256:str,
        offset:int=0,limit:int=120000,
    )->dict:
        binding,row=self._task_proxy(task_id,proxy_job_id)
        command,payload=self._original_routed_submit(row)
        if command is None:
            raise DurableError(
                "POSTRUN_EVIDENCE_PATH_UNDECLARED",
                "original routed submit command is unavailable",
                proxy_job_id=proxy_job_id,
            )
        if not list((payload or {}).get("evidence_paths") or []):
            raise DurableError(
                "POSTRUN_EVIDENCE_PATH_UNDECLARED",
                "legacy job has no predeclared evidence manifest; scientific execution must not be rerun",
                proxy_job_id=proxy_job_id,
            )
        row=await self._refresh_exact_routed_job_if_online(binding,row)
        if row["last_known_state"] not in TERMINAL:
            raise DurableError(
                "POSTRUN_EVIDENCE_JOB_NOT_TERMINAL",
                "post-run artifact read requires authoritative terminal job state",
                proxy_job_id=proxy_job_id,
                state=row["last_known_state"],
            )
        if not row["terminal_result_json"]:
            row=await self._ensure_routed_terminal_evidence(binding,row)
        self.devices.require_online(binding["device_id"])
        result,_=await self._route_step(
            binding["device_id"],"JOB_ARTIFACT_READ",
            {
                "proxy_job_id":proxy_job_id,
                "path":path,
                "expected_sha256":str(expected_sha256).lower(),
                "offset":int(offset),
                "limit":int(limit),
            },
            project_id=binding["project_id"],task_id=task_id,
        )
        result={
            **result,
            "readback_state":"READ_OK",
            "scientific_rerun_required":False,
            "automatic_scientific_rerun_for_readback_forbidden":True,
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
                row=self.routed_jobs.update(
                    proxy_job_id,node_job_id=result.get("node_job_id"),
                    state=result.get("state"),terminal_result=result,
                )
                self._sync_routed_admission(row)
        if row["terminal_result_json"]:
            self._sync_routed_admission(row)
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
        active_node_jobs=payload.get("active_node_jobs")
        unresolved_node_jobs=payload.get("active_node_jobs_unresolved")
        capacity_complete=payload.get("capacity_reconciliation_complete")
        candidate_nonterminal=payload.get("candidate_nonterminal_routed_jobs")
        observed_at_ms=now_ms()

        def _nonnegative_int(value,label):
            if isinstance(value,bool):
                raise DurableError("INVALID_ARGUMENT",f"{label} must be a non-negative integer")
            try:
                value=int(value)
            except Exception as exc:
                raise DurableError(
                    "INVALID_ARGUMENT",f"{label} must be a non-negative integer"
                ) from exc
            if value<0 or value>1000000:
                raise DurableError(
                    "INVALID_ARGUMENT",f"{label} is outside the accepted range"
                )
            return value

        if active_node_jobs is not None:
            active_node_jobs=_nonnegative_int(active_node_jobs,"active_node_jobs")
        if unresolved_node_jobs is not None:
            unresolved_node_jobs=_nonnegative_int(
                unresolved_node_jobs,"active_node_jobs_unresolved"
            )
        if candidate_nonterminal is not None:
            candidate_nonterminal=_nonnegative_int(
                candidate_nonterminal,"candidate_nonterminal_routed_jobs"
            )
        if capacity_complete is not None and not isinstance(capacity_complete,bool):
            raise DurableError(
                "INVALID_ARGUMENT",
                "capacity_reconciliation_complete must be boolean",
            )

        if (
            isinstance(capabilities,dict)
            or isinstance(platform,dict)
            or active_node_jobs is not None
            or unresolved_node_jobs is not None
            or capacity_complete is not None
            or candidate_nonterminal is not None
        ):
            with self.db.transaction() as con:
                if (
                    isinstance(capabilities,dict)
                    or active_node_jobs is not None
                    or unresolved_node_jobs is not None
                    or capacity_complete is not None
                    or candidate_nonterminal is not None
                ):
                    try:
                        merged=json.loads(device_row["capabilities_json"] or "{}")
                        if not isinstance(merged,dict):
                            merged={}
                    except Exception:
                        merged={}
                    if isinstance(capabilities,dict):
                        merged.update(capabilities)
                    if active_node_jobs is not None:
                        merged["_remotemcp_active_node_jobs"]=active_node_jobs
                        merged["_remotemcp_active_node_jobs_observed_at_ms"]=observed_at_ms
                    if unresolved_node_jobs is not None:
                        merged["_remotemcp_active_node_jobs_unresolved"]=unresolved_node_jobs
                    if capacity_complete is not None:
                        merged["_remotemcp_capacity_reconciliation_complete"]=capacity_complete
                    if candidate_nonterminal is not None:
                        merged["_remotemcp_candidate_nonterminal_routed_jobs"]=candidate_nonterminal
                    con.execute(
                        "UPDATE devices SET capabilities_json=? WHERE device_id=?",
                        (json.dumps(merged,sort_keys=True),device_row["device_id"]),
                    )
                if isinstance(platform,dict):
                    con.execute(
                        "UPDATE devices SET platform_json=? WHERE device_id=?",
                        (json.dumps(platform,sort_keys=True),device_row["device_id"]),
                    )
        return {
            "server_time_ms":observed_at_ms,
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
