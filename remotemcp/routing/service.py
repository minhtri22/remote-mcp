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
        self.db.bootstrap(target_version=5)
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
        retryable=bool(
            isinstance(exc,DurableError)
            and exc.code=="DEVICE_COMMAND_EXPIRED"
        )
        self.durable.operations.fail(
            operation_id,code,{"error":str(exc)},retryable
        )

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
        if "lifecycle_state" in binding.keys():
            result["binding_lifecycle_state"]=binding["lifecycle_state"]
            result["superseded_by_project_id"]=binding["superseded_by_project_id"]
            result["eligible_for_new_tasks"]=binding["lifecycle_state"]=="ACTIVE"
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

    @staticmethod
    def _public_node_root_rel(value:str)->str:
        raw=str(value or "")
        if raw.startswith("@root/"):
            parts=raw.split("/",2)
            if len(parts)==3 and parts[2]:
                return parts[2]
        return raw

    def _public_binding_fields(self,value:str)->dict:
        raw=str(value or "")
        return {
            "node_root_rel":self._public_node_root_rel(raw),
            "node_root_location":raw,
        }

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

    def _gateway_attestation(self)->dict:
        source_root=Path(__file__).resolve().parents[2]
        marker=source_root/".remotemcp-release.json"
        release_commit=None
        if marker.is_file():
            try:
                payload=json.loads(marker.read_text(encoding="utf-8-sig"))
                release_commit=payload.get("commit")
            except Exception:
                release_commit=None
        return {
            "source_dir":str(source_root),
            "release_commit":release_commit,
            "schema_target_version":5,
        }

    def device_list(self)->dict:
        return {
            "devices":[
                {**self.devices.status(r["device_id"]),"gateway_attestation":self._gateway_attestation()}
                for r in self.devices.list()
            ]
        }

    def device_status(self,device_id:str)->dict:
        # Expose the effective *gateway-enforced* release gate to every agent.
        # A node's own version claim is not evidence that old-node dispatch
        # has been disabled at the gateway.
        pin=str(getattr(self.commands,"required_release_sha","") or "")
        quarantine=bool(getattr(self.commands,"science_dispatch_hold",False))
        reason=self.commands._science_dispatch_blocker(device_id)
        return {
            **self.devices.status(device_id),
            "gateway_attestation":self._gateway_attestation(),
            "science_job_dispatch_gate":{
                "minimum_exact_release_sha":pin or None,
                "release_pin_enforced":bool(pin),
                "command_quarantine_active":quarantine,
                "new_job_admission_allowed":not bool(reason),
                "reason":reason or (
                    "PINNED_NODE_RELEASE_QUALIFIED" if pin else
                    "NO_RELEASE_PIN_CONFIGURED"
                ),
            },
        }

    async def device_process_inspect(self,device_id:str,pids:list[int]|None=None)->dict:
        """Priority read-only node OS diagnostic, independent of science tasks.

        Never queue against an old node without signed capability attestation.
        In particular, device ONLINE does not prove new-command compatibility.
        """
        values=[] if pids is None else pids
        if (not isinstance(values,list) or len(values)>16
            or any(type(v) is not int or v<=0 for v in values)):
            raise DurableError("INVALID_ARGUMENT","pids must be up to 16 positive integers")
        status=self.devices.status(device_id)
        if status["state"]!="ONLINE" or not status.get("capacity_signal_fresh"):
            raise DurableError("DEVICE_NOT_READY","fresh signed node heartbeat required")
        attestation=status.get("node_attestation") or {}
        supported=attestation.get("supported_node_diagnostics") or []
        if "process_inspect_v1" not in supported:
            raise DurableError(
                "NODE_DIAGNOSTIC_UPGRADE_REQUIRED",
                "node does not attest support for signed process inspection; no command was queued",
            )
        result,command=await self._route_step(
            device_id,"NODE_PROCESS_INSPECT",{"pids":values},
        )
        return {
            **result,
            "device_id":device_id,
            "command_id":command["command_id"],
            "route_generation":int(command["route_generation"]),
            "task_created":False,
            "scientific_job_created":False,
        }

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
        elif not bool(status.get("capacity_reconciliation_complete")):
            resolved=False
            reason="NODE_HEARTBEAT_CAPACITY_UNRECONCILED"
        elif status.get("authoritative_unresolved_node_jobs") not in (0,):
            resolved=False
            reason="NODE_HEARTBEAT_CAPACITY_UNRESOLVED_ROWS"
        elif not fresh:
            resolved=False
            reason="NODE_HEARTBEAT_CAPACITY_STALE"
        else:
            resolved=True
            reason="AUTHORITATIVE_NODE_HEARTBEAT_RECONCILED_DURABLE_STATE"
        active=int(reported) if resolved else None
        summaries=status.get("active_job_summaries",[]) or []
        active_job_identity_complete=bool(
            resolved
            and active is not None
            and len(summaries)==active
        )
        node_attestation=status.get("node_attestation",{}) or {}
        physical_process_observed_at_ms=None
        physical_process_age_ms=None
        try:
            physical_process_observed_at_ms=int(
                node_attestation.get("physical_process_observed_at_ms")
            )
            physical_process_age_ms=max(
                0,now_ms()-physical_process_observed_at_ms
            )
            physical_process_safety_resolved=bool(
                int(node_attestation.get("physical_process_safety_resolved",0))==1
                and fresh
                and physical_process_age_ms<=int(self.config.physical_process_max_age_seconds)*1000
            )
            physical_process_blocker_count=int(
                node_attestation.get("physical_process_blocker_count",-1)
            )
        except (TypeError,ValueError):
            physical_process_safety_resolved=False
            physical_process_blocker_count=-1
        physical_process_safe=bool(
            physical_process_safety_resolved
            and physical_process_blocker_count==0
        )
        gateway_attestation=self._gateway_attestation()
        node_release=node_attestation.get("release_commit")
        gateway_release=gateway_attestation.get("release_commit")
        release_attestation_match=bool(
            node_release
            and gateway_release
            and str(node_release)==str(gateway_release)
        )
        scientific_observability_ready=bool(
            resolved
            and active_job_identity_complete
            and release_attestation_match
            and node_attestation.get("execution_root")
            and node_attestation.get("runtime_dir")
        )
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
                "capacity_reconciliation_complete":bool(
                    status.get("capacity_reconciliation_complete")
                ),
                "authoritative_unresolved_node_jobs":status.get(
                    "authoritative_unresolved_node_jobs"
                ),
                "candidate_nonterminal_routed_jobs":status.get(
                    "candidate_nonterminal_routed_jobs"
                ),
                "active_job_summaries":summaries,
                "active_job_identity_complete":active_job_identity_complete,
                "node_attestation":node_attestation,
                "physical_process_safety_resolved":physical_process_safety_resolved,
                "physical_process_blocker_count":physical_process_blocker_count,
                "physical_process_safe":physical_process_safe,
                "physical_process_residual_job_count":node_attestation.get(
                    "physical_process_residual_job_count"
                ),
                "physical_process_declared_long_lived_count":node_attestation.get(
                    "physical_process_declared_long_lived_count"
                ),
                "physical_process_active_job_tree_count":node_attestation.get(
                    "physical_process_active_job_tree_count"
                ),
                "physical_process_snapshot_sha256":node_attestation.get(
                    "physical_process_snapshot_sha256"
                ),
                "physical_process_observed_at_ms":physical_process_observed_at_ms,
                "physical_process_age_ms":physical_process_age_ms,
                "gateway_attestation":gateway_attestation,
                "release_attestation_match":release_attestation_match,
                "scientific_observability_ready":scientific_observability_ready,
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

    def device_upgrade_readiness(self,device_id:str)->dict:
        """Fail-closed deployment gate including signed physical-process safety."""
        status=self.devices.status(device_id)
        capacity=self.device_capacity_status(device_id)
        t=now_ms()
        session_cutoff=t-int(self.multi.agents.ttl_ms)
        fresh_sessions=self.db.query_one(
            "SELECT COUNT(*) AS n FROM agent_sessions s "
            "JOIN agents a ON a.agent_id=s.agent_id "
            "WHERE a.owner_account_id=? AND s.state='ACTIVE' "
            "AND s.last_heartbeat_at_ms>=?",
            (self.owner_account_id,session_cutoff),
        )
        active_leases=self.db.query_one(
            "SELECT COUNT(*) AS n FROM task_leases WHERE expires_at_ms>?",
            (t,),
        )
        active_commands=self.db.query_one(
            "SELECT COUNT(*) AS n FROM device_commands "
            "WHERE device_id=? AND state IN ('QUEUED','LEASED') "
            "AND command_expires_at_ms>?",
            (device_id,t),
        )
        gateway_local_jobs=self.db.query_one(
            "SELECT COUNT(*) AS n FROM jobs "
            "WHERE state IN ('QUEUED','STARTING','RUNNING','CANCELLING')"
        )
        node_attestation=status.get("node_attestation",{}) or {}
        try:
            physical_resolved=int(
                node_attestation.get("physical_process_safety_resolved",0)
            )==1
        except (TypeError,ValueError):
            physical_resolved=False
        try:
            physical_blockers=int(
                node_attestation.get("physical_process_blocker_count",-1)
            )
        except (TypeError,ValueError):
            physical_blockers=-1
        physical_observed_at_ms=None
        physical_age_ms=None
        try:
            physical_observed_at_ms=int(
                node_attestation.get("physical_process_observed_at_ms")
            )
            physical_age_ms=max(0,t-physical_observed_at_ms)
        except (TypeError,ValueError):
            physical_observed_at_ms=None
            physical_age_ms=None
        physical_fresh=bool(
            physical_resolved
            and status.get("capacity_signal_fresh")
            and physical_age_ms is not None
            and physical_age_ms<=int(self.config.physical_process_max_age_seconds)*1000
        )
        blockers={
            "capacity_unresolved":not bool(capacity.get("capacity_resolved")),
            "active_node_jobs":int(capacity.get("authoritative_active_node_jobs") or 0),
            "fresh_agent_sessions":int(fresh_sessions["n"]) if fresh_sessions else 0,
            "unexpired_task_leases":int(active_leases["n"]) if active_leases else 0,
            "unexpired_device_commands":int(active_commands["n"]) if active_commands else 0,
            "gateway_local_nonterminal_jobs":(
                int(gateway_local_jobs["n"]) if gateway_local_jobs else 0
            ),
            "physical_process_safety_unresolved":not physical_fresh,
            "physical_process_blockers":physical_blockers,
        }
        ready=(
            not blockers["capacity_unresolved"]
            and blockers["active_node_jobs"]==0
            and blockers["fresh_agent_sessions"]==0
            and blockers["unexpired_task_leases"]==0
            and blockers["unexpired_device_commands"]==0
            and blockers["gateway_local_nonterminal_jobs"]==0
            and not blockers["physical_process_safety_unresolved"]
            and blockers["physical_process_blockers"]==0
        )
        return self._with_routing(
            {
                "device_id":device_id,
                "device_name":status.get("device_name"),
                "upgrade_allowed":ready,
                "decision":(
                    "UPGRADE_WINDOW_OPEN"
                    if ready else "WAIT_DO_NOT_RESTART_OR_DEPLOY"
                ),
                "blockers":blockers,
                "capacity_reconciliation_complete":bool(
                    status.get("capacity_reconciliation_complete")
                ),
                "authoritative_unresolved_node_jobs":status.get(
                    "authoritative_unresolved_node_jobs"
                ),
                "node_attestation":node_attestation,
                "physical_process_safety":{
                    "resolved":physical_fresh,
                    "blocker_count":physical_blockers,
                    "residual_job_process_count":node_attestation.get(
                        "physical_process_residual_job_count"
                    ),
                    "declared_long_lived_process_count":node_attestation.get(
                        "physical_process_declared_long_lived_count"
                    ),
                    "active_job_tree_process_count":node_attestation.get(
                        "physical_process_active_job_tree_count"
                    ),
                    "snapshot_sha256":node_attestation.get(
                        "physical_process_snapshot_sha256"
                    ),
                    "observed_at_ms":physical_observed_at_ms,
                    "age_ms":physical_age_ms,
                    "blocker_summary_json":node_attestation.get(
                        "physical_process_blocker_summary_json","[]"
                    ),
                    "error":node_attestation.get("physical_process_error",""),
                },
                "gateway_attestation":self._gateway_attestation(),
                "agent_session_ttl_ms":int(self.multi.agents.ttl_ms),
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
        node_attestation=status.get("node_attestation",{}) or {}
        try:
            physical_resolved=int(
                node_attestation.get("physical_process_safety_resolved",0)
            )==1
            physical_blockers=int(
                node_attestation.get("physical_process_blocker_count",-1)
            )
        except (TypeError,ValueError):
            physical_resolved=False
            physical_blockers=-1
        restart_physical_age_ms=None
        try:
            restart_physical_age_ms=max(
                0,now_ms()-int(
                    node_attestation.get("physical_process_observed_at_ms")
                )
            )
        except (TypeError,ValueError):
            restart_physical_age_ms=None
        if not (
            physical_resolved
            and status.get("capacity_signal_fresh")
            and restart_physical_age_ms is not None
            and restart_physical_age_ms<=int(self.config.physical_process_max_age_seconds)*1000
        ):
            raise DurableError(
                "DEVICE_PHYSICAL_PROCESS_SAFETY_UNRESOLVED",
                "physical process safety snapshot is unavailable or stale; restart fails closed",
                device_id=device_id,
            )
        if physical_blockers!=0:
            raise DurableError(
                "DEVICE_PHYSICAL_PROCESS_BUSY",
                "physical process safety blockers are present; restart fails closed",
                device_id=device_id,
                physical_process_blocker_count=physical_blockers,
                physical_process_snapshot_sha256=node_attestation.get(
                    "physical_process_snapshot_sha256"
                ),
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
                    "SELECT p.*,b.device_id,b.node_root_rel,b.binding_generation,"
                    "b.lifecycle_state,b.superseded_by_project_id FROM project_device_bindings b "
                    "JOIN projects p ON p.project_id=b.project_id WHERE b.device_id=? AND b.node_root_rel=?",
                    (device_id,node_root_rel),
                ).fetchone()
                if existing:
                    if existing["project_kind"]!=kind:
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","remote project kind changed")
                    lifecycle=existing["lifecycle_state"] if "lifecycle_state" in existing.keys() else "ACTIVE"
                    if lifecycle!="ACTIVE":
                        raise DurableError(
                            "PROJECT_BINDING_HISTORICAL",
                            "historical project binding cannot be reused for new work",
                            project_id=existing["project_id"],
                        )
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
            result={
                **self.multi.projects.status(project_id),
                "device_id":device_id,
                **self._public_binding_fields(node_root_rel),
                "binding_generation":generation,
            }
            result=self._with_routing(
                result,device_id,project_id=project_id,binding_generation=generation
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    async def project_materialize_git_on_device(
        self,
        operation_id:str,
        project_id:str,
        remote_url:str,
        remote_ref:str,
        expected_commit_sha:str,
    )->dict:
        project=self.multi.projects.get(project_id)
        binding=self.bindings.require_active_project_binding(project_id)
        device_id=binding["device_id"]
        self.devices.require_online(device_id)
        args={
            "project_id":project_id,
            "remote_url":str(remote_url),
            "remote_ref":str(remote_ref),
            "expected_commit_sha":str(expected_commit_sha).lower(),
        }
        op,created=self._reserve(
            operation_id,"PROJECT_MATERIALIZE_GIT_ON_DEVICE",args,project_id=project_id
        )
        replay=self._operation_replay(op)
        if replay is not None:
            replay=self._with_routing(
                replay,device_id,project_id=project_id,
                binding_generation=int(binding["binding_generation"]),
            )
            return {**replay,"replayed":True}
        if project["project_kind"]!="NON_GIT":
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_INVALID_STATE",
                "project must be NON_GIT before exact Git materialization",
                project_id=project_id,
            )
        task_count=self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?",(project_id,)
        )
        if task_count is not None and int(task_count["n"])!=0:
            raise DurableError(
                "PROJECT_GIT_MATERIALIZATION_HAS_TASKS",
                "NON_GIT project already has task state; semantics cannot change in place",
                project_id=project_id,
            )
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            result,_=await self._route_step(
                device_id,"PROJECT_MATERIALIZE_GIT",
                {
                    "project_id":project_id,
                    "binding_generation":int(binding["binding_generation"]),
                    "remote_url":str(remote_url),
                    "remote_ref":str(remote_ref),
                    "expected_commit_sha":str(expected_commit_sha).lower(),
                },
                operation_id=operation_id,operation_step=0,project_id=project_id,
            )
            if (
                result.get("project_kind")!="GIT"
                or str(result.get("head_commit") or "").lower()
                    !=str(expected_commit_sha).lower()
            ):
                raise DurableError(
                    "PROJECT_GIT_MATERIALIZATION_FAILED",
                    "node did not prove the requested exact Git identity",
                    project_id=project_id,
                )
            with self.db.transaction() as con:
                current_project=con.execute(
                    "SELECT * FROM projects WHERE project_id=?",(project_id,)
                ).fetchone()
                current_binding=con.execute(
                    "SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)
                ).fetchone()
                current_tasks=con.execute(
                    "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?",(project_id,)
                ).fetchone()
                if (
                    current_project is None
                    or current_project["project_kind"]!="NON_GIT"
                    or current_binding is None
                    or current_binding["device_id"]!=device_id
                    or int(current_binding["binding_generation"])!=int(binding["binding_generation"])
                    or int(current_tasks["n"])!=0
                ):
                    raise DurableError(
                        "PROJECT_DEVICE_BINDING_CONFLICT",
                        "project state changed during Git materialization",
                        project_id=project_id,
                    )
                con.execute(
                    "UPDATE projects SET project_kind='GIT',updated_at_ms=? WHERE project_id=?",
                    (now_ms(),project_id),
                )
            out={
                **self.multi.projects.status(project_id),
                **result,
                "device_id":device_id,
                **self._public_binding_fields(binding["node_root_rel"]),
                "binding_generation":int(binding["binding_generation"]),
                "project_kind":"GIT",
                "exact_commit_pinned":True,
            }
            out=self._with_routing(
                out,device_id,project_id=project_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,out)
            return out
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    async def project_bind_device(self,operation_id:str,project_id:str,device_id:str)->dict:
        project=self.multi.projects.get(project_id)
        current=self.bindings.project_binding(project_id)
        if current is not None:
            self.bindings.require_active_project_binding(project_id)
        if current and current["device_id"]==device_id:
            result={
                **self.multi.projects.status(project_id),
                "device_id":device_id,
                **self._public_binding_fields(current["node_root_rel"]),
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
            result={
                **self.multi.projects.status(project_id),
                "device_id":device_id,
                **self._public_binding_fields(binding["node_root_rel"]),
                "binding_generation":int(binding["binding_generation"]),
            }
            result=self._with_routing(
                result,device_id,project_id=project_id,
                binding_generation=int(binding["binding_generation"]),
            )
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc);raise

    def project_binding_deprecate(
        self,operation_id:str,project_id:str,superseded_by_project_id:str|None=None
    )->dict:
        args={
            "project_id":project_id,
            "superseded_by_project_id":superseded_by_project_id,
        }
        op,created=self._reserve(
            operation_id,"PROJECT_BINDING_DEPRECATE",args,project_id=project_id
        )
        replay=self._operation_replay(op)
        if replay is not None:
            return {**replay,"replayed":True}
        if created:
            self.durable.operations.mark_executing(operation_id)
        try:
            row=self.bindings.deprecate_project(project_id,superseded_by_project_id)
            result={
                "project_id":project_id,
                "binding_lifecycle_state":row["lifecycle_state"],
                "superseded_by_project_id":row["superseded_by_project_id"],
                "eligible_for_new_tasks":False,
                "device_id":row["device_id"],
                **self._public_binding_fields(row["node_root_rel"]),
                "binding_generation":int(row["binding_generation"]),
            }
            self.durable.operations.succeed(operation_id,result)
            return result
        except Exception as exc:
            self._fail_final(operation_id,exc)
            raise

    # ---- task lifecycle ----

    async def task_create_or_local(self,operation_id:str,project_id:str,title:str,base_ref:str="HEAD")->dict:
        binding=self.bindings.project_binding(project_id)
        if binding is not None:
            self.bindings.require_active_project_binding(project_id)
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
            base_commit=None
            if project["project_kind"]=="GIT":
                resolved,_=await self._route_step(
                    binding["device_id"],
                    "TASK_BASE_RESOLVE",
                    {
                        "project_id":project_id,
                        "binding_generation":int(binding["binding_generation"]),
                        "base_ref":base_ref,
                    },
                    operation_id=operation_id,
                    operation_step=0,
                    project_id=project_id,
                )
                base_commit=str(resolved.get("base_commit") or "").strip()
                if not base_commit:
                    raise DurableError(
                        "TASK_BASE_REF_UNRESOLVED",
                        "remote task base resolver returned no commit",
                        base_ref=base_ref,
                    )
            with self.db.transaction() as con:
                old=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
                if old is None:
                    con.execute(
                        "INSERT INTO tasks(task_id,project_id,title,state,base_ref,base_commit,branch_name,worktree_rel,created_at_ms,updated_at_ms) "
                        "VALUES(?,?,?,'READY',?,?,?,?,?,?)",
                        (task_id,project_id,title,base_ref,base_commit,branch,worktree_rel,t,t),
                    )
                    self.multi.tasks._event(
                        con,task_id,operation_id,None,None,"TASK_CREATED",
                        {"title":title,"base_ref":base_ref,"base_commit":base_commit},
                    )
                    self.multi.tasks._event(con,task_id,operation_id,None,None,"TASK_READY",{})
                    self.bindings.inherit_task(con,task_id,project_id)
                else:
                    if old["project_id"]!=project_id or old["title"]!=title or old["base_ref"]!=base_ref:
                        raise DurableError("OPERATION_CONFLICT","deterministic task identity conflicts")
                    old_commit=old["base_commit"]
                    if old_commit is None and base_commit is not None:
                        con.execute(
                            "UPDATE tasks SET base_commit=?,updated_at_ms=? WHERE task_id=? AND base_commit IS NULL",
                            (base_commit,now_ms(),task_id),
                        )
                    elif old_commit!=base_commit:
                        raise DurableError(
                            "TASK_BASE_COMMIT_MISMATCH",
                            "deterministic task identity resolved to a different base commit",
                            task_id=task_id,
                            stored_base_commit=old_commit,
                            resolved_base_commit=base_commit,
                        )
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

    def _task_unresolved_commands(self,task_id:str,device_id:str,exclude_operation_id:str|None=None)->list[dict]:
        """Gateway-only snapshot; never dispatch a new node command."""
        rows=self.db.query_all(
            "SELECT command_id,operation_id,command_type,state,delivery_attempt,"
            "command_expires_at_ms,created_at_ms FROM device_commands "
            "WHERE task_id=? AND device_id=? AND NOT ("
            "state IN ('SUCCEEDED','FAILED') "
            "OR (state='CANCELLED' AND delivery_attempt=0)) "
            "ORDER BY created_at_ms,command_id",
            (task_id,device_id),
        )
        return [
            {k:row[k] for k in (
                "command_id","operation_id","command_type","state",
                "delivery_attempt","command_expires_at_ms","created_at_ms",
            )}
            for row in rows if exclude_operation_id is None or row["operation_id"]!=exclude_operation_id
        ]

    def task_exact_command_receipts_or_local(
        self,task_id:str,command_ids:list[str],
    )->dict:
        """Read only exact immutable gateway receipts for an owned task.

        Does not poll, retry, lease, clear or mutate queued science commands.
        Limits are independent of the newest-200-commands task diagnostic.
        """
        if (
            not isinstance(command_ids,list) or not 1<=len(command_ids)<=16
            or any(not isinstance(x,str) or not x.startswith("cmd_")
                   or len(x)>80 for x in command_ids)
            or len(set(command_ids))!=len(command_ids)
        ):
            raise DurableError("INVALID_ARGUMENT","1..16 unique exact command IDs required")
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","task is not device-bound")
        task=self.multi.tasks.status(task_id)
        dev=self.devices.status(binding["device_id"])
        placeholders=",".join("?" for _ in command_ids)
        rows=self.db.query_all(
            "SELECT command_id,device_id,route_generation,project_id,task_id,"
            "operation_id,operation_step,command_type,request_hash,payload_json,"
            "state,delivery_attempt,lease_expires_at_ms,command_expires_at_ms,"
            "created_at_ms,updated_at_ms,finished_at_ms,error_code "
            "FROM device_commands WHERE command_id IN ("+placeholders+") "
            "AND device_id=? AND project_id=? AND task_id=?",
            (*command_ids,binding["device_id"],binding["project_id"],task_id),
        )
        found={r["command_id"]:r for r in rows}
        receipts=[]
        for command_id in command_ids:
            r=found.get(command_id)
            if r is None:
                receipts.append({"command_id":command_id,"state":"NOT_FOUND_OR_NOT_OWNED"})
                continue
            proxy=None
            try:
                val=json.loads(r["payload_json"])
                if isinstance(val,dict):
                    proxy=val.get("proxy_job_id")
            except (TypeError,ValueError,json.JSONDecodeError):
                pass
            receipts.append({
                "command_id":r["command_id"],
                "command_type":r["command_type"],
                "route_generation":int(r["route_generation"]),
                "operation_id":r["operation_id"],
                "operation_step":int(r["operation_step"]),
                "request_hash":r["request_hash"],
                "proxy_job_id_in_payload":proxy,
                "state":r["state"],
                "delivery_attempt":int(r["delivery_attempt"]),
                "lease_expires_at_ms":r["lease_expires_at_ms"],
                "command_expires_at_ms":int(r["command_expires_at_ms"]),
                "created_at_ms":int(r["created_at_ms"]),
                "updated_at_ms":int(r["updated_at_ms"]),
                "finished_at_ms":r["finished_at_ms"],
                "error_code":r["error_code"],
                "matching_task":r["task_id"]==task_id,
                "matching_project":r["project_id"]==binding["project_id"],
                "matching_device":r["device_id"]==binding["device_id"],
                "gateway_receipt_is_not_node_execution_proof":True,
            })
        return {
            "task_id":task_id,
            "project_id":task["project_id"],
            "device_id":binding["device_id"],
            "current_route_generation":dev["route_generation"],
            "receipts":receipts,
            "read_only":True,
            "command_polled":False,
            "command_requeued":False,
            "science_job_rerun_authorized":False,
            "node_upgrade_authorized":False,
        }

    def task_dispatch_diagnostic_or_local(self,task_id:str)->dict:
        """Read-only control-plane dispatch trace for one owned, device-bound task.

        Does not enqueue/poll/claim/refresh a command and does not infer node
        execution from device heartbeat. Snapshot is bounded to 200 commands.
        """
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","task is not device-bound")
        task=self.multi.tasks.status(task_id)
        device_id=binding["device_id"]
        device=self.devices.status(device_id)
        generation=int(device["route_generation"])
        stamp=now_ms()
        rows=self.db.query_all(
            "SELECT command_id,operation_id,operation_step,command_type,state,"
            "delivery_attempt,route_generation,command_expires_at_ms,"
            "lease_expires_at_ms,created_at_ms,updated_at_ms,finished_at_ms,error_code,request_hash "
            "FROM device_commands WHERE device_id=? AND task_id=? "
            "ORDER BY created_at_ms DESC,command_id DESC LIMIT 200",
            (device_id,task_id),
        )
        commands=[]
        for item in reversed(rows):
            expires=int(item["command_expires_at_ms"])
            lease_expires=item["lease_expires_at_ms"]
            state=item["state"]
            attempts=int(item["delivery_attempt"])
            commands.append({
                "command_id":item["command_id"],"operation_id":item["operation_id"],
                "operation_step":int(item["operation_step"]),"command_type":item["command_type"],
                "request_hash":item["request_hash"],
                "state":state,"delivery_attempt":attempts,
                "route_generation":int(item["route_generation"]),
                "created_at_ms":int(item["created_at_ms"]),
                "updated_at_ms":int(item["updated_at_ms"]),
                "command_expires_at_ms":expires,
                "command_expired":stamp>=expires,
                "lease_expires_at_ms":lease_expires,
                "delivery_lease_expired":lease_expires is not None and stamp>=int(lease_expires),
                "finished_at_ms":item["finished_at_ms"],
                "error_code":item["error_code"],
                "delivery_uncertain":(
                    attempts>0 and state not in ("SUCCEEDED","FAILED")
                ),
            })
        # Same eligibility and ordering as CommandRepository.poll; without its
        # state-changing expiry sweep or lease transitions.
        candidate=self.db.query_one(
            "SELECT command_id,task_id,command_type,created_at_ms FROM device_commands "
            "WHERE device_id=? AND route_generation=? AND state='QUEUED' "
            "AND command_expires_at_ms>? ORDER BY CASE command_type "
            "WHEN 'NODE_RESTART' THEN 0 WHEN 'JOB_CANCEL' THEN 0 "
            "WHEN 'TASK_BASE_RESOLVE' THEN 1 WHEN 'TASK_WORKTREE_ENSURE' THEN 1 "
            "WHEN 'PROJECT_PROBE' THEN 1 WHEN 'PROJECT_BIND' THEN 1 "
            "WHEN 'JOB_SUBMIT' THEN 2 ELSE 3 END,created_at_ms,command_id LIMIT 1",
            (device_id,generation,stamp),
        )
        summary=self.db.query_all(
            "SELECT state,COUNT(*) AS total,SUM(CASE WHEN delivery_attempt>0 THEN 1 ELSE 0 END) AS delivered "
            "FROM device_commands WHERE device_id=? AND route_generation=? GROUP BY state",
            (device_id,generation),
        )
        routed=self.routed_jobs.list_task_rows(task_id)
        return {
            "task_id":task_id,"project_id":task["project_id"],
            "device_id":device_id,"device_state":device["state"],
            "route_generation":generation,"task_binding_generation":int(binding["binding_generation"]),
            "task_state":task["state"],"cleanup_pending":bool(task["cleanup_pending"]),
            "snapshot_at_ms":stamp,
            "device_queue_counts":[{"state":r["state"],"count":int(r["total"]),
                "ever_delivered":int(r["delivered"] or 0)} for r in summary],
            "next_eligible_poll_command":None if candidate is None else {
                "command_id":candidate["command_id"],"task_id":candidate["task_id"],
                "command_type":candidate["command_type"],"created_at_ms":int(candidate["created_at_ms"]),
            },
            "task_command_trace":commands,"command_trace_truncated":len(rows)==200,
            "routed_jobs":[{
                "proxy_job_id":r["proxy_job_id"],"node_job_id":r["node_job_id"],
                "state":r["last_known_state"],
                "terminal_evidence_verified":self._terminal_job_evidence_verified(r),
            } for r in routed],
            "read_only":True,"node_command_dispatched":False,
            "node_process_liveness_verified":False,
            "authorization_to_rerun_jobs":False,
        }

    async def task_r5_command_attestation_or_local(
        self,operation_id:str,task_id:str,target_command_id:str,
    )->dict:
        """Freeze a read-only authenticated receipt. Never alter target command state."""
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","task is not device-bound")
        target=self.commands.get(target_command_id)
        if (target["task_id"]!=task_id
            or target["project_id"]!=binding["project_id"]
            or target["device_id"]!=binding["device_id"]):
            raise DurableError("COMMAND_PROOF_IDENTITY_MISMATCH","target not bound to this task")
        if (target["state"] not in ("QUEUED","LEASED","CANCELLED","IN_DOUBT")
            or int(target["delivery_attempt"])<=0):
            raise DurableError(
                "COMMAND_PROOF_INVALID_STATE",
                "only uncertain previously leased/delivered commands can be attested",
            )
        args={"task_id":task_id,"target_command_id":target_command_id,
              "expected_request_hash":target["request_hash"],
              "expected_route_generation":int(target["route_generation"])}
        op,created=self._reserve(
            operation_id,"TASK_R5_COMMAND_ATTEST",args,
            project_id=binding["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:
            return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            proof,_=await self._route_step(
                binding["device_id"],"NODE_COMMAND_ATTEST",{
                    "target_command_id":target_command_id,
                    "expected_request_hash":target["request_hash"],
                    "expected_route_generation":int(target["route_generation"]),
                    "expected_task_id":task_id,
                },operation_id=operation_id,operation_step=0,
                task_id=task_id,project_id=binding["project_id"],
            )
            if (proof.get("target_command_id")!=target_command_id
                or proof.get("request_hash")!=target["request_hash"]
                or proof.get("task_id")!=task_id
                or proof.get("operation_id")!=target["operation_id"]
                or proof.get("command_type")!=target["command_type"]
                or proof.get("route_generation")!=int(target["route_generation"])
                or proof.get("read_only") is not True
                or proof.get("target_reexecuted") is not False):
                raise DurableError("COMMAND_PROOF_IDENTITY_MISMATCH","authenticated proof mismatch")
            mapping=proof.get("exact_routed_job_mapping")
            if mapping is not None:
                original=json.loads(target["payload_json"])
                if (target["command_type"]!="JOB_SUBMIT"
                    or not isinstance(mapping,dict)
                    or mapping.get("proxy_job_id")!=original.get("proxy_job_id")
                    or mapping.get("task_id")!=task_id
                    or mapping.get("project_id")!=binding["project_id"]
                    or not isinstance(mapping.get("node_job_id"),str)
                    or not mapping["node_job_id"].startswith("job_")
                    or mapping.get("mapping_only") is not True
                    or mapping.get("job_execution_not_repeated") is not True):
                    raise DurableError("COMMAND_PROOF_IDENTITY_MISMATCH","frozen job mapping identity mismatch")
            result={"task_id":task_id,"target_command_id":target_command_id,
                "gateway_target_state":target["state"],"node_receipt":proof,
                "authenticated_node_route":True,"original_command_mutated":False,
                "cleanup_pending_resolution_authorized":False,
                "job_rerun_authorized":False}
            self.durable.operations.succeed(operation_id,result)
            return result
        except DurableError as exc:
            if exc.code!="DEVICE_COMMAND_PENDING":self._fail_final(operation_id,exc)
            raise

    async def task_r5_evidence_manifest_or_local(self,operation_id:str,task_id:str)->dict:
        """Read-only bounded node worktree inventory. Not a cleanup authorization."""
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","task is not device-bound")
        task=self.multi.tasks.status(task_id)
        if not task["worktree_rel"]:
            raise DurableError("INVALID_ARGUMENT","worktree manifest requires Git task")
        args={"task_id":task_id,"base_commit":task["base_commit"],
              "branch_name":task["branch_name"],"worktree_rel":task["worktree_rel"]}
        op,created=self._reserve(
            operation_id,"TASK_R5_EVIDENCE_MANIFEST",args,
            project_id=binding["project_id"],task_id=task_id,
        )
        replay=self._operation_replay(op)
        if replay is not None:return {**replay,"replayed":True}
        if created:self.durable.operations.mark_executing(operation_id)
        try:
            manifest,_=await self._route_step(
                binding["device_id"],"TASK_EVIDENCE_MANIFEST",{"task_id":task_id},
                operation_id=operation_id,operation_step=0,
                project_id=binding["project_id"],task_id=task_id,
            )
            identity={k:manifest.get(k) for k in (
                "task_id","project_id","binding_generation","branch_name",
                "worktree_rel","head_commit","tracked_dirty","untracked_files"
            )}
            exact=hashlib.sha256(
                json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
            ).hexdigest()
            if (manifest.get("task_id")!=task_id
                or manifest.get("project_id")!=binding["project_id"]
                or manifest.get("binding_generation")!=int(binding["binding_generation"])
                or manifest.get("branch_name")!=task["branch_name"]
                or manifest.get("worktree_rel")!=task["worktree_rel"]
                or manifest.get("head_commit")!=task["base_commit"]
                or manifest.get("manifest_sha256")!=exact
                or manifest.get("read_only") is not True
                or manifest.get("evidence_preserved") is not True
                or manifest.get("cleanup_authorized") is not False):
                raise DurableError("EVIDENCE_MANIFEST_MISMATCH","node manifest identity or digest mismatch")
            result={"task_id":task_id,"manifest":manifest,
                    "authenticated_node_route":True,"cleanup_authorized":False,
                    "scientific_namespace_pristine_verified":False}
            self.durable.operations.succeed(operation_id,result)
            return result
        except DurableError as exc:
            if exc.code!="DEVICE_COMMAND_PENDING":self._fail_final(operation_id,exc)
            raise

    def task_cleanup_recovery_status_or_local(self,task_id:str)->dict:
        """Read-only gateway reconciliation, safe when node command transport is jammed."""
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","guarded cleanup requires an existing routed task")
        task=self.multi.tasks.status(task_id)
        jobs=self.routed_jobs.list_task_rows(task_id)
        return {
            "task_id":task_id,"project_id":task["project_id"],
            "device_id":binding["device_id"],"binding_generation":int(binding["binding_generation"]),
            "state":task["state"],"cleanup_pending":bool(task["cleanup_pending"]),
            "lease_epoch":int(task["lease_epoch"]),
            "lease_active":(
                task["lease_expires_at_ms"] is not None
                and int(task["lease_expires_at_ms"])>now_ms()
            ),
            "lease_record_present":task["lease_expires_at_ms"] is not None,
            "base_commit":task["base_commit"],"branch_name":task["branch_name"],
            "worktree_rel":task["worktree_rel"],
            "unresolved_commands":self._task_unresolved_commands(task_id,binding["device_id"]),
            "jobs":[
                {"proxy_job_id":j["proxy_job_id"],"node_job_id":j["node_job_id"],
                 "state":j["last_known_state"],"terminal_evidence_available":bool(j["terminal_result_json"]),
                 "terminal_evidence_verified":self._terminal_job_evidence_verified(j)}
                for j in jobs
            ],
            "resolution_is_read_only":True,"worktree_deletion_authorized":False,
        }

    @staticmethod
    def _terminal_job_evidence_verified(row)->bool:
        """Only accept terminal proof bound to the exact existing proxy and node job."""
        if row["last_known_state"] not in ("SUCCEEDED","FAILED","CANCELLED"):
            return False
        if not row["node_job_id"] or not row["terminal_result_json"]:
            return False
        try:
            evidence=json.loads(row["terminal_result_json"])
        except (TypeError,ValueError):
            return False
        return (
            isinstance(evidence,dict)
            and evidence.get("terminal") is True
            and evidence.get("proxy_job_id")==row["proxy_job_id"]
            and evidence.get("node_job_id")==row["node_job_id"]
            and evidence.get("state")==row["last_known_state"]
        )

    def _require_cleanup_quiescent(self,task_id:str,binding:dict,*,exclude_operation_id:str|None=None)->None:
        outstanding=self._task_unresolved_commands(task_id,binding["device_id"],exclude_operation_id)
        if outstanding:
            raise DurableError(
                "TASK_COMMAND_UNRESOLVED",
                "task has unacknowledged routed commands; inspect gateway recovery status first",
                command_ids=[x["command_id"] for x in outstanding],
            )
        jobs=self.routed_jobs.list_task_rows(task_id)
        if any(not self._terminal_job_evidence_verified(j) for j in jobs):
            raise DurableError(
                "TASK_JOB_EVIDENCE_UNRESOLVED",
                "a routed job lacks independently retrievable terminal evidence",
            )

    async def task_cleanup_pending_resolve_or_local(
        self,operation_id:str,task_id:str,expected_lease_epoch:int,
        expected_base_commit:str,expected_head_commit:str,
    )->dict:
        """Non-destructive, CAS-style acknowledgment after clean node worktree proof."""
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","guarded cleanup requires a routed task")
        current=self.multi.tasks.status(task_id)
        args={"task_id":task_id,"epoch":int(expected_lease_epoch),
              "base_commit":expected_base_commit,"head_commit":expected_head_commit}
        # A completed operation can be replayed after cleanup_pending becomes 0.
        # For a fresh invalid request, validate first: no orphan RESERVED op.
        op=None
        prior=self.db.query_one("SELECT 1 FROM operations WHERE operation_id=?",(operation_id,))
        if prior is not None:
            op,created=self._reserve(
                operation_id,"TASK_CLEANUP_PENDING_RESOLVE",args,
                project_id=current["project_id"],task_id=task_id,
            )
            if op["state"]==OperationState.SUCCEEDED.value:
                return {**(self.durable.operations.replay_result(op) or {}),"replayed":True}
            if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
                self._operation_replay(op)
        if current["state"]!="RECOVERABLE" or not current["cleanup_pending"]:
            raise DurableError("TASK_CLEANUP_STATE_CONFLICT","task must be RECOVERABLE with cleanup_pending")
        if current["lease_expires_at_ms"] is not None or current["owner_agent_id"] is not None:
            raise DurableError("TASK_LEASE_CONFLICT","task has an owner or active lease")
        if int(current["lease_epoch"])!=int(expected_lease_epoch) or current["base_commit"]!=expected_base_commit:
            raise DurableError("TASK_CLEANUP_IDENTITY_MISMATCH","frozen task epoch or base mismatch")
        if not isinstance(expected_head_commit,str) or len(expected_head_commit)!=40 or any(
            c not in "0123456789abcdef" for c in expected_head_commit.lower()
        ):
            raise DurableError("INVALID_ARGUMENT","expected_head_commit must be exact SHA-1")
        self._require_cleanup_quiescent(task_id,binding,exclude_operation_id=operation_id)
        if op is None:
            op,created=self._reserve(
                operation_id,"TASK_CLEANUP_PENDING_RESOLVE",args,
                project_id=current["project_id"],task_id=task_id,
            )
            if op["state"]==OperationState.SUCCEEDED.value:
                return {**(self.durable.operations.replay_result(op) or {}),"replayed":True}
            if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
                self._operation_replay(op)
        if created:
            self.durable.operations.mark_executing(operation_id)
        try:
            node,_=await self._route_step(
                binding["device_id"],"TASK_WORKTREE_STATUS",{"task_id":task_id},
                operation_id=operation_id,operation_step=0,
                project_id=current["project_id"],task_id=task_id,
            )
            # Gateway projects.root_rel is a private @v2bd storage key, not a node path.
            # Compare the authoritative device binding's node_root_rel instead.
            project_binding=self.bindings.project_binding(current["project_id"])
            if (project_binding is None
                or project_binding["device_id"]!=binding["device_id"]
                or int(project_binding["binding_generation"])!=int(binding["binding_generation"])):
                raise DurableError("TASK_CLEANUP_BINDING_MISMATCH","task/project device bindings diverged")
            expected_node_root=str(project_binding["node_root_rel"]).replace("\\","/")
            root_matches=(
                node.get("encoded_root_rel")==expected_node_root
                if expected_node_root.startswith("@root/")
                else (node.get("root_namespace")=="CANONICAL"
                      and node.get("root_rel")==expected_node_root)
            )
            if (node.get("task_id")!=task_id
                or node.get("project_id")!=current["project_id"]
                or node.get("binding_generation")!=int(binding["binding_generation"])
                or not root_matches
                or node.get("branch_name")!=current["branch_name"]
                or node.get("worktree_rel")!=current["worktree_rel"]
                or node.get("head_commit")!=expected_head_commit
                or node.get("clean") is not True):
                raise DurableError(
                    "TASK_CLEANUP_WORKTREE_UNVERIFIED",
                    "worktree path/branch/HEAD/cleanliness does not match frozen expectations",
                )
            # Recheck mutable state under the same transaction that clears the flag.
            with self.db.transaction() as con:
                row=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
                lease=con.execute("SELECT 1 FROM task_leases WHERE task_id=?",(task_id,)).fetchone()
                if (row is None or row["state"]!="RECOVERABLE" or not row["cleanup_pending"]
                    or row["owner_agent_id"] is not None or lease is not None
                    or int(row["lease_epoch"])!=int(expected_lease_epoch)
                    or row["base_commit"]!=expected_base_commit
                    or row["branch_name"]!=current["branch_name"]
                    or row["worktree_rel"]!=current["worktree_rel"]):
                    raise DurableError("TASK_CLEANUP_RACE","task changed during cleanup reconciliation")
                # No concurrent command or unproven job may be silently ignored.
                self._require_cleanup_quiescent(
                    task_id,binding,exclude_operation_id=operation_id,
                )
                con.execute(
                    "UPDATE tasks SET cleanup_pending=0,updated_at_ms=? WHERE task_id=?",
                    (now_ms(),task_id),
                )
                self.multi.tasks._event(
                    con,task_id,operation_id,None,None,"TASK_CLEANUP_PENDING_RESOLVED",
                    {"lease_epoch":int(expected_lease_epoch),"base_commit":expected_base_commit,
                     "head_commit":expected_head_commit,"worktree_preserved":True},
                )
            result={"task_id":task_id,"cleanup_pending":False,
                    "worktree_preserved":True,"job_rerun_authorized":False,
                    "head_commit":expected_head_commit}
            self.durable.operations.succeed(operation_id,result)
            return result
        except DurableError as exc:
            if exc.code!="DEVICE_COMMAND_PENDING":
                self._fail_final(operation_id,exc)
            raise

    async def task_claim_or_local(self,operation_id:str,task_id:str,agent_id:str,session_id:str)->dict:
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            return await self.multi.task_claim(operation_id,task_id,agent_id,session_id)
        self.devices.require_online(binding["device_id"])
        self.multi.agents.validate_active(agent_id,session_id)
        # Refuse a fresh claim before recording an operation or minting a lease
        # if the same task has stranded commands/unproven job evidence.
        # Existing operation_id replays must still be able to reconcile.
        prior=self.db.query_one("SELECT 1 FROM operations WHERE operation_id=?",(operation_id,))
        if prior is None:
            self._require_cleanup_quiescent(task_id,binding)
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
        try:
            ensure_step=0
            if project["project_kind"]=="GIT" and not task["base_commit"]:
                resolved,_=await self._route_step(
                    binding["device_id"],
                    "TASK_BASE_RESOLVE",
                    {
                        "project_id":task["project_id"],
                        "binding_generation":int(binding["binding_generation"]),
                        "base_ref":task["base_ref"],
                    },
                    operation_id=operation_id,
                    operation_step=0,
                    project_id=task["project_id"],
                    task_id=task_id,
                    expires_at_ms=expiry,
                )
                resolved_commit=str(resolved.get("base_commit") or "").strip()
                if not resolved_commit:
                    raise DurableError(
                        "TASK_BASE_REF_UNRESOLVED",
                        "legacy routed task base resolver returned no commit",
                        task_id=task_id,
                        base_ref=task["base_ref"],
                    )
                with self.db.transaction() as con:
                    current=con.execute(
                        "SELECT base_commit FROM tasks WHERE task_id=?",
                        (task_id,),
                    ).fetchone()
                    if current is None:
                        raise DurableError("NOT_FOUND","task disappeared during base pin")
                    if current["base_commit"] is None:
                        con.execute(
                            "UPDATE tasks SET base_commit=?,updated_at_ms=? WHERE task_id=? AND base_commit IS NULL",
                            (resolved_commit,now_ms(),task_id),
                        )
                        self.multi.tasks._event(
                            con,task_id,operation_id,agent_id,session_id,
                            "TASK_BASE_PINNED",
                            {
                                "base_ref":task["base_ref"],
                                "base_commit":resolved_commit,
                                "legacy_repair":True,
                            },
                        )
                    elif current["base_commit"]!=resolved_commit:
                        raise DurableError(
                            "TASK_BASE_COMMIT_MISMATCH",
                            "task base changed while repairing legacy null base",
                            task_id=task_id,
                            stored_base_commit=current["base_commit"],
                            resolved_base_commit=resolved_commit,
                        )
                task=self.multi.tasks.get(task_id)
                ensure_step=1

            payload={
                "task_id":task_id,"project_id":task["project_id"],"binding_generation":int(binding["binding_generation"]),
                "branch_name":task["branch_name"],"worktree_rel":task["worktree_rel"],
                "base_ref":task["base_ref"],"base_commit":task["base_commit"],
            }
            await self._route_step(
                binding["device_id"],"TASK_WORKTREE_ENSURE",payload,
                operation_id=operation_id,operation_step=ensure_step,
                project_id=task["project_id"],task_id=task_id,
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
            if exc.code in {"DEVICE_COMMAND_PENDING","DEVICE_COMMAND_EXPIRED"}:
                if exc.code=="DEVICE_COMMAND_EXPIRED":
                    self._fail_final(operation_id,exc)
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

    async def _recover_routed_predecessor(self,binding,row):
        node_job_id=str(row["node_job_id"] or "").strip()
        if not node_job_id:
            raise DurableError(
                "PREDECESSOR_STATE_UNRESOLVED",
                "routed predecessor has no exact node_job_id for evidence recovery",
                predecessor_job_id=row["proxy_job_id"],
            )
        payload={
            "proxy_job_id":row["proxy_job_id"],
            "node_job_id":node_job_id,
            "task_id":row["task_id"],
            "project_id":row["project_id"],
            "argv_sha256":row["argv_sha256"] if "argv_sha256" in row.keys() else None,
            "cwd":row["cwd"] if "cwd" in row.keys() else None,
            "execution_key":row["execution_key"] if "execution_key" in row.keys() else None,
        }
        try:
            result,_=await self._route_step(
                binding["device_id"],"JOB_RECOVER_ROUTED_JOB",payload,
                project_id=binding["project_id"],task_id=row["task_id"],
            )
        except Exception as exc:
            if isinstance(exc,DurableError) and exc.code=="PREDECESSOR_JOB_NOT_TERMINAL":
                raise
            raise DurableError(
                "PREDECESSOR_STATE_UNRESOLVED",
                "exact routed predecessor recovery could not prove node evidence",
                predecessor_job_id=row["proxy_job_id"],
                node_job_id=node_job_id,
            ) from exc
        state=result.get("state")
        terminal=bool(result.get("terminal")) and state in TERMINAL
        row=self.routed_jobs.update(
            row["proxy_job_id"],
            node_job_id=result.get("node_job_id") or node_job_id,
            state=state,
            terminal_result=result if terminal else None,
        )
        if terminal:
            self._sync_routed_admission(row)
        return row,result

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
            except Exception:
                row,result=await self._recover_routed_predecessor(binding,row)
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
                row=self.routed_jobs.update(
                    row["proxy_job_id"],
                    node_job_id=remote.get("node_job_id"),
                    state=remote.get("state"),
                )
            except Exception:
                row,remote=await self._recover_routed_predecessor(binding,row)
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

    def _scientific_execution_key(
        self,task_id:str,scientific_gate:str,argv:list[str],cwd:str,evidence_paths:list[str]
    )->str:
        gate=str(scientific_gate or "").strip()
        if not gate or len(gate)>256:
            raise DurableError(
                "INVALID_ARGUMENT","scientific_gate must be a non-empty string <= 256 characters"
            )
        task=self.multi.tasks.get(task_id)
        base_commit=str(task["base_commit"] or "").strip()
        if not base_commit:
            raise DurableError(
                "SCIENTIFIC_BASE_COMMIT_REQUIRED",
                "exactly-once scientific execution requires an immutable task base_commit",
                task_id=task_id,
            )
        payload={
            "project_id":task["project_id"],
            "base_commit":base_commit,
            "scientific_gate":gate,
            "argv":list(argv),
            "cwd":str(cwd),
            "evidence_paths":list(evidence_paths),
        }
        digest=hashlib.sha256(
            json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
        ).hexdigest()
        return "sek_"+digest

    @staticmethod
    def _submit_intent_operation_id(operation_id:str)->str:
        return "rsi_"+hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:40]

    def _freeze_routed_submit_intent(
        self,operation_id:str,task_id:str,binding,lease,argv:list[str],cwd:str,
        evidence_paths:list[str],*,legacy_adopted:bool=False,
        original_lease_epoch:int|None=None,
        scientific_gate:str|None=None,execution_key:str|None=None,
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
            "scientific_gate":scientific_gate,
            "execution_key":execution_key,
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
        if (
            op is None
            or op["kind"] not in {"TASK_JOB_SUBMIT","TASK_JOB_SUBMIT_ONCE"}
            or op["task_id"]!=task_id
        ):
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
                "scientific_gate":intent.get("scientific_gate"),
                "execution_key":intent.get("execution_key"),
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
            raise DurableError(
                "NOT_FOUND",
                "legacy routed submit operation not found; exactly-once submits already freeze their intent",
            )
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
        if intent.get("execution_key"):
            existing_science=self.routed_jobs.by_execution_key(intent["execution_key"])
            if (
                existing_science is not None
                and existing_science["operation_id"]!=operation_id
            ):
                recovery_op,created=self._reserve(
                    recovery_operation_id,"TASK_JOB_RECOVER_PREPROXY",
                    {
                        "task_id":task_id,
                        "original_operation_id":operation_id,
                        "original_argv_sha256":intent["argv_sha256"],
                        "execution_key":intent["execution_key"],
                        "deduplicated_to_proxy_job_id":existing_science["proxy_job_id"],
                    },
                    agent_id="",project_id=binding["project_id"],task_id=task_id,
                )
                replay=self._operation_replay(recovery_op)
                if replay is not None:
                    return {**replay,"replayed":True}
                if created:
                    self.durable.operations.mark_executing(recovery_operation_id)
                admission=self.job_admissions.by_operation(operation_id)
                if admission is not None and admission["state"]=="ADMITTING":
                    self.job_admissions.abort_if_unbound(
                        operation_id,"SCIENTIFIC_EXECUTION_DEDUPLICATED"
                    )
                response={
                    **self.routed_jobs.as_dict(
                        existing_science,
                        self.devices.status(existing_science["device_id"])["state"],
                    ),
                    "recovery_kind":"EXACTLY_ONCE_EXISTING_EXECUTION_ADOPTED",
                    "original_operation_id":operation_id,
                    "same_logical_science_preserved":True,
                    "exactly_once":True,
                    "adopted_existing_execution":True,
                    "execution_key":intent["execution_key"],
                    "scientific_gate":intent.get("scientific_gate"),
                    "scientific_rerun_required":False,
                }
                self.durable.operations.succeed(recovery_operation_id,response)
                return response
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
                        "execution_key":intent.get("execution_key"),
                        "scientific_gate":intent.get("scientific_gate"),
                        "exactly_once":bool(intent.get("execution_key")),
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
                    con,operation_id,task_id,binding["project_id"],binding["device_id"],
                    execution_key=intent.get("execution_key"),
                    argv_sha256=intent.get("argv_sha256"),
                    cwd=str(intent["cwd"]),
                )
                payload={
                    "proxy_job_id":proxy["proxy_job_id"],
                    "task_id":task_id,
                    "argv":list(intent["argv"]),
                    "cwd":str(intent["cwd"]),
                    "evidence_paths":list(intent.get("evidence_paths") or []),
                    "execution_key":intent.get("execution_key"),
                    "scientific_gate":intent.get("scientific_gate"),
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
                "execution_key":intent.get("execution_key"),
                "scientific_gate":intent.get("scientific_gate"),
                "exactly_once":bool(intent.get("execution_key")),
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
        argv:list[str],cwd:str=".",evidence_paths:list[str]|None=None,
        scientific_gate:str|None=None,
    )->dict:
        evidence_paths=list(evidence_paths or [])
        enforce_agent_git_worktree_policy(argv)
        binding=self.bindings.task_binding(task_id)
        if binding is None:
            if evidence_paths or scientific_gate is not None:
                raise DurableError(
                    "DEVICE_CONTEXT_REQUIRED",
                    "evidence-bound or exactly-once scientific jobs require a routed task",
                )
            return await self.multi.task_job_submit(
                operation_id,task_id,lease_token,lease_epoch,argv,cwd
            )
        dev=self.devices.require_online(binding["device_id"])
        lease=self.multi.leases.validate(task_id,lease_token,lease_epoch)
        execution_key=None
        existing_execution=None
        if scientific_gate is not None:
            scientific_gate=str(scientific_gate).strip()
            execution_key=self._scientific_execution_key(
                task_id,scientific_gate,argv,cwd,evidence_paths
            )
            existing_execution=self.routed_jobs.by_execution_key(execution_key)
        args={
            "task_id":task_id,"lease_epoch":int(lease_epoch),
            "argv":argv,"cwd":cwd,"evidence_paths":evidence_paths,
        }
        if scientific_gate is not None:
            args["scientific_gate"]=str(scientific_gate)
            args["execution_key"]=execution_key
        op,created=self._reserve(
            operation_id,
            "TASK_JOB_SUBMIT_ONCE" if execution_key else "TASK_JOB_SUBMIT",
            args,agent_id=lease["agent_id"],
            project_id=binding["project_id"],task_id=task_id,
        )
        self._freeze_routed_submit_intent(
            operation_id,task_id,binding,lease,list(argv),str(cwd),evidence_paths,
            scientific_gate=scientific_gate,execution_key=execution_key,
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
        if existing_execution is not None:
            if created:
                self.durable.operations.mark_executing(operation_id)
            existing_binding=self.bindings.task_binding(existing_execution["task_id"])
            device_state=self.devices.status(existing_execution["device_id"])["state"]
            response={
                **self.routed_jobs.as_dict(existing_execution,device_state),
                "exactly_once":True,
                "adopted_existing_execution":True,
                "scientific_gate":str(scientific_gate),
                "existing_task_id":existing_execution["task_id"],
                "requested_task_id":task_id,
                "scientific_rerun_required":False,
            }
            response=self._with_routing(
                response,existing_execution["device_id"],
                project_id=existing_execution["project_id"],
                task_id=existing_execution["task_id"],
                binding_generation=(
                    int(existing_binding["binding_generation"])
                    if existing_binding is not None else None
                ),
            )
            self.durable.operations.succeed(operation_id,response)
            return response

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
        deduplicated_proxy=None
        with self.db.transaction() as con:
            proxy,proxy_created=self.routed_jobs.create(
                con,operation_id,task_id,binding["project_id"],binding["device_id"],
                execution_key=execution_key,
                argv_sha256=self._argv_sha256(argv),
                cwd=str(cwd),
            )
            if execution_key and not proxy_created and proxy["operation_id"]!=operation_id:
                deduplicated_proxy=proxy
                cmd=None
            else:
                payload={
                    "proxy_job_id":proxy["proxy_job_id"],
                    "task_id":task_id,"argv":argv,"cwd":cwd,
                    "evidence_paths":evidence_paths,
                    "execution_key":execution_key,
                    "scientific_gate":scientific_gate,
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
        if deduplicated_proxy is not None:
            self.job_admissions.abort_if_unbound(operation_id,"SCIENTIFIC_EXECUTION_DEDUPLICATED")
            existing_binding=self.bindings.task_binding(deduplicated_proxy["task_id"])
            response={
                **self.routed_jobs.as_dict(
                    deduplicated_proxy,
                    self.devices.status(deduplicated_proxy["device_id"])["state"],
                ),
                "exactly_once":True,
                "adopted_existing_execution":True,
                "scientific_gate":str(scientific_gate),
                "existing_task_id":deduplicated_proxy["task_id"],
                "requested_task_id":task_id,
                "scientific_rerun_required":False,
            }
            response=self._with_routing(
                response,deduplicated_proxy["device_id"],
                project_id=deduplicated_proxy["project_id"],
                task_id=deduplicated_proxy["task_id"],
                binding_generation=(
                    int(existing_binding["binding_generation"])
                    if existing_binding is not None else None
                ),
            )
            self.durable.operations.succeed(operation_id,response)
            return response
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
            if execution_key:
                response.update({
                    "exactly_once":True,
                    "adopted_existing_execution":False,
                    "scientific_gate":str(scientific_gate),
                    "scientific_rerun_required":False,
                })
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

    async def task_job_submit_once_or_local(
        self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,
        scientific_gate:str,argv:list[str],cwd:str=".",
        evidence_paths:list[str]|None=None,
    )->dict:
        return await self.task_job_submit_or_local(
            operation_id,task_id,lease_token,lease_epoch,argv,cwd,
            evidence_paths,scientific_gate=scientific_gate,
        )

    def task_job_inspect(self,task_id:str,proxy_job_id:str)->dict:
        """Observe a routed job without routing a command or acquiring a new lease."""
        binding,row=self._task_proxy(task_id,proxy_job_id)
        status=self.devices.status(binding["device_id"])
        task=self.multi.tasks.get(task_id)
        command,payload=self._original_routed_submit(row)
        payload=payload or {}
        summaries=status.get("active_job_summaries") or []
        matched=None
        for item in summaries:
            if not isinstance(item,dict):
                continue
            if (
                item.get("proxy_job_id")==proxy_job_id
                or (
                    row["node_job_id"]
                    and item.get("node_job_id")==row["node_job_id"]
                )
            ):
                matched=dict(item)
                break
        central_state=str(row["last_known_state"])
        terminal=central_state in TERMINAL
        heartbeat_fresh=bool(status.get("capacity_signal_fresh"))
        submitted_argv=payload.get("argv") if isinstance(payload.get("argv"),list) else None
        central_argv_sha256=(
            row["argv_sha256"]
            if "argv_sha256" in row.keys() and row["argv_sha256"]
            else (
                self._argv_sha256(submitted_argv)
                if submitted_argv is not None else None
            )
        )
        central_execution_key=(
            row["execution_key"] if "execution_key" in row.keys() else None
        )
        provenance_checks={
            "proxy_job_id_match":None,
            "node_job_id_match":None,
            "task_id_match":None,
            "project_id_match":None,
            "submitted_argv_sha256_match":None,
            "execution_key_match":None,
        }
        if matched is not None:
            provenance_checks={
                "proxy_job_id_match":matched.get("proxy_job_id")==proxy_job_id,
                "node_job_id_match":(
                    row["node_job_id"] is None
                    or matched.get("node_job_id")==row["node_job_id"]
                ),
                "task_id_match":matched.get("task_id")==task_id,
                "project_id_match":matched.get("project_id")==binding["project_id"],
                "submitted_argv_sha256_match":(
                    central_argv_sha256 is not None
                    and matched.get("submitted_argv_sha256")==central_argv_sha256
                ),
                "execution_key_match":(
                    matched.get("execution_key")==central_execution_key
                ),
            }
        provenance_match=bool(
            matched is not None
            and all(v is True for v in provenance_checks.values())
        )
        if terminal:
            authoritative_state=central_state
            provenance_resolved=True
            observation_source="CENTRAL_TERMINAL_CACHE"
        elif heartbeat_fresh and matched is not None and provenance_match:
            authoritative_state=str(matched.get("state") or central_state)
            provenance_resolved=True
            observation_source="SIGNED_NODE_HEARTBEAT_ACTIVE_JOB"
        elif heartbeat_fresh and matched is not None:
            authoritative_state="PROVENANCE_MISMATCH"
            provenance_resolved=False
            observation_source="SIGNED_NODE_HEARTBEAT_PROVENANCE_MISMATCH"
        elif (
            heartbeat_fresh
            and status.get("authoritative_active_node_jobs")==0
        ):
            authoritative_state="NOT_ACTIVE_ON_NODE"
            provenance_resolved=False
            observation_source="SIGNED_NODE_HEARTBEAT_NO_ACTIVE_MATCH"
        else:
            authoritative_state="UNRESOLVED"
            provenance_resolved=False
            observation_source="CENTRAL_REGISTRY_ONLY"
        return self._with_routing(
            {
                **self.routed_jobs.as_dict(row,status["state"]),
                "operation_id":row["operation_id"],
                "base_commit":task["base_commit"],
                "base_ref":task["base_ref"],
                "submitted_argv":submitted_argv,
                "argv_sha256":central_argv_sha256,
                "provenance_checks":provenance_checks,
                "provenance_match":provenance_match,
                "cwd":(
                    row["cwd"]
                    if "cwd" in row.keys() and row["cwd"] is not None
                    else payload.get("cwd")
                ),
                "execution_key":(
                    row["execution_key"] if "execution_key" in row.keys() else None
                ),
                "active_job_match":matched,
                "authoritative_execution_state":authoritative_state,
                "provenance_resolved":provenance_resolved,
                "observation_source":observation_source,
                "observation_refresh_command_created":False,
                "lease_required":False,
                "scientific_rerun_required":False,
                "automatic_scientific_rerun_for_readback_forbidden":True,
                "capacity_signal_fresh":heartbeat_fresh,
                "authoritative_active_node_jobs":status.get(
                    "authoritative_active_node_jobs"
                ),
                "node_attestation":status.get("node_attestation",{}),
                "gateway_attestation":self._gateway_attestation(),
            },
            binding["device_id"],project_id=binding["project_id"],task_id=task_id,
            binding_generation=int(binding["binding_generation"]),
        )

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
            try:
                remote,_=await self._route_step(
                    binding["device_id"],"JOB_GET",{"proxy_job_id":proxy_job_id},
                    project_id=binding["project_id"],task_id=task_id,
                )
            except DurableError as exc:
                if exc.code!="DEVICE_COMMAND_PENDING":
                    raise
                observed=self.task_job_inspect(task_id,proxy_job_id)
                observed["observation_refresh"]="PENDING"
                observed["readback_state"]="OBSERVATION_REFRESH_PENDING"
                observed["message"]=str(exc)
                return observed
            row=self.routed_jobs.update(
                proxy_job_id,node_job_id=remote.get("node_job_id"),
                state=remote.get("state"),
            )
            result=self.routed_jobs.as_dict(row,"ONLINE")
            result["observation_refresh"]="SUCCEEDED"
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
        try:
            result,_=await self._route_step(
                binding["device_id"],"JOB_LOGS",
                {
                    "proxy_job_id":proxy_job_id,"stream":stream,
                    "cursor":int(cursor),"max_bytes":int(max_bytes),
                },
                project_id=binding["project_id"],task_id=task_id,
            )
        except DurableError as exc:
            if exc.code!="DEVICE_COMMAND_PENDING":
                raise
            observed=self.task_job_inspect(task_id,proxy_job_id)
            return {
                **observed,
                "stream":stream,
                "cursor":int(cursor),
                "data":"",
                "content":"",
                "eof":False,
                "readback_state":"OBSERVATION_REFRESH_PENDING",
                "observation_refresh":"PENDING",
                "message":str(exc),
            }
        result={
            **result,
            "readback_state":"READ_OK",
            "observation_refresh":"SUCCEEDED",
        }
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
            try:
                result,_=await self._route_step(
                    binding["device_id"],"JOB_RESULT",{"proxy_job_id":proxy_job_id},
                    project_id=binding["project_id"],task_id=task_id,
                )
            except DurableError as exc:
                if exc.code!="DEVICE_COMMAND_PENDING":
                    raise
                observed=self.task_job_inspect(task_id,proxy_job_id)
                return {
                    **observed,
                    "terminal":False,
                    "result_readback_state":"OBSERVATION_REFRESH_PENDING",
                    "observation_refresh":"PENDING",
                    "message":str(exc),
                }
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
        active_job_summaries=payload.get("active_job_summaries")
        node_attestation=payload.get("node_attestation")
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
        if active_job_summaries is not None:
            if not isinstance(active_job_summaries,list) or len(active_job_summaries)>64:
                raise DurableError(
                    "INVALID_ARGUMENT",
                    "active_job_summaries must be a list of at most 64 items",
                )
            normalized_summaries=[]
            allowed={
                "proxy_job_id","node_job_id","task_id","project_id","state","pid",
                "command_sha256","submitted_argv_sha256","normalized_argv_sha256",
                "execution_key","scientific_gate","cwd","started_at_ms",
                "last_output_at_ms","last_heartbeat_at_ms",
            }
            for item in active_job_summaries:
                if not isinstance(item,dict):
                    raise DurableError("INVALID_ARGUMENT","active job summary must be an object")
                summary={k:item.get(k) for k in allowed if k in item}
                for key in (
                    "proxy_job_id","node_job_id","task_id","project_id","state",
                    "command_sha256","submitted_argv_sha256","normalized_argv_sha256",
                    "execution_key","scientific_gate","cwd"
                ):
                    value=summary.get(key)
                    if value is not None and (not isinstance(value,str) or len(value)>4096):
                        raise DurableError("INVALID_ARGUMENT",f"invalid active job summary field: {key}")
                for key in ("pid","started_at_ms","last_output_at_ms","last_heartbeat_at_ms"):
                    value=summary.get(key)
                    if value is not None:
                        try:
                            summary[key]=int(value)
                        except Exception as exc:
                            raise DurableError("INVALID_ARGUMENT",f"invalid active job summary field: {key}") from exc
                normalized_summaries.append(summary)
            active_job_summaries=normalized_summaries
        if (
            capacity_complete is True
            and active_node_jobs is not None
            and active_job_summaries is not None
            and len(active_job_summaries)!=active_node_jobs
        ):
            raise DurableError(
                "INVALID_ARGUMENT",
                "reconciled active_job_summaries must identify every active node job",
            )
        if node_attestation is not None:
            if not isinstance(node_attestation,dict):
                raise DurableError("INVALID_ARGUMENT","node_attestation must be an object")
            allowed_attestation={
                "source_dir","release_commit","execution_root","runtime_dir",
                "schema_version","schema_sha256","python_prefix",
                "infrastructure_root","log_dir","temp_dir","cache_dir",
                "control_dir","zero_c_mode",
                "physical_process_safety_resolved",
                "physical_process_blocker_count",
                "physical_process_residual_job_count",
                "physical_process_declared_long_lived_count",
                "physical_process_active_job_tree_count",
                "physical_process_declared_pattern_count",
                "physical_process_observed_at_ms",
                "physical_process_snapshot_sha256",
                "physical_process_blocker_summary_json",
                "physical_process_error",
            }
            node_attestation={
                k:node_attestation.get(k)
                for k in allowed_attestation
                if k in node_attestation
            }
            for key,value in node_attestation.items():
                if value is not None and not isinstance(value,(str,int)):
                    raise DurableError("INVALID_ARGUMENT",f"invalid node attestation field: {key}")
                if isinstance(value,str) and len(value)>16384:
                    raise DurableError("INVALID_ARGUMENT",f"node attestation field too large: {key}")

        if (
            isinstance(capabilities,dict)
            or isinstance(platform,dict)
            or active_node_jobs is not None
            or unresolved_node_jobs is not None
            or capacity_complete is not None
            or candidate_nonterminal is not None
            or active_job_summaries is not None
            or node_attestation is not None
        ):
            with self.db.transaction() as con:
                if (
                    isinstance(capabilities,dict)
                    or active_node_jobs is not None
                    or unresolved_node_jobs is not None
                    or capacity_complete is not None
                    or candidate_nonterminal is not None
                    or active_job_summaries is not None
                    or node_attestation is not None
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
                    if active_job_summaries is not None:
                        merged["_remotemcp_active_job_summaries"]=active_job_summaries
                        merged["_remotemcp_active_job_summaries_observed_at_ms"]=observed_at_ms
                    if node_attestation is not None:
                        merged["_remotemcp_node_attestation"]=node_attestation
                        merged["_remotemcp_node_attestation_observed_at_ms"]=observed_at_ms
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
