from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from remotemcp.durable.errors import DurableError
from .cas import NodeCas
from .command_journal import NodeCommandJournal
from .db import NodeDatabase
from .executor import NodeExecutor
from .identity import NodeIdentity
from .jobs import NodeJobs
from .projects import NodeProjects
from .runtime_lock import RuntimeLock
from .process_safety import ProcessSafetyProbe
from .worktrees import NodeWorktrees


class NodeService:
    RECONNECT_INITIAL_SECONDS = 1.0
    RECONNECT_MAX_SECONDS = 15.0
    PHYSICAL_PROCESS_REFRESH_MS = 15_000
    TERMINAL_ROUTE_ERRORS = {
        "DEVICE_REVOKED",
        "DEVICE_ROUTE_GENERATION_MISMATCH",
        "DEVICE_SIGNATURE_INVALID",
        "DEVICE_SIGNATURE_STALE",
    }

    def __init__(self,config,client_factory=None):
        self.config=config
        self.lock=RuntimeLock(config.runtime_dir)
        self.db=NodeDatabase(config.runtime_dir);self.db.bootstrap()
        self.identity=NodeIdentity(config.runtime_dir,self.db)
        self.projects=NodeProjects(self.db,config.root)
        self.worktrees=NodeWorktrees(self.db,config.root,self.projects)
        self.journal=NodeCommandJournal(self.db)
        self.jobs=NodeJobs(config,self.db,self.identity,self.projects,self.worktrees)
        self.process_safety=ProcessSafetyProbe(self.jobs)
        self._process_safety_snapshot=None
        self._process_safety_task=None
        self.cas=NodeCas(self.db,self.worktrees,self.journal)
        self.executor=NodeExecutor(config,self.journal,self.projects,self.worktrees,self.cas,self.jobs)
        from .client import NodeClient
        self.client=(client_factory or NodeClient)(config,self.identity)
        self._stop=asyncio.Event()

    async def start(self):
        self.lock.acquire()
        self.cas.reconcile_all()
        await self.jobs.start()

    async def stop(self):
        self._stop.set()
        await self.jobs.stop()
        await self.client.close()
        self.lock.release()

    @classmethod
    def _retryable_connection_error(cls,exc:Exception)->bool:
        if isinstance(exc,httpx.TransportError):
            return True
        if isinstance(exc,DurableError):
            if exc.code in cls.TERMINAL_ROUTE_ERRORS:
                return False
            return exc.code in {"DEVICE_OFFLINE","INTERNAL_ERROR"}
        return False

    async def _wait_reconnect(self,seconds:float):
        try:
            await asyncio.wait_for(self._stop.wait(),timeout=max(0.0,float(seconds)))
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _pending_process_safety(reason:str="PROCESS_SAFETY_REFRESH_PENDING")->dict:
        return {
            "physical_process_safety_resolved":0,
            "physical_process_blocker_count":-1,
            "physical_process_residual_job_count":-1,
            "physical_process_declared_long_lived_count":-1,
            "physical_process_active_job_tree_count":-1,
            "physical_process_declared_pattern_count":0,
            "physical_process_observed_at_ms":time.time_ns()//1_000_000,
            "physical_process_snapshot_sha256":"pending",
            "physical_process_blocker_summary_json":"[]",
            "physical_process_error":reason,
        }

    async def _process_safety_for_heartbeat(self)->dict:
        task=self._process_safety_task
        if task is not None and task.done():
            try:
                result=task.result()
                if isinstance(result,dict):
                    self._process_safety_snapshot=result
            except Exception as exc:
                self._process_safety_snapshot=self._pending_process_safety(
                    type(exc).__name__
                )
            self._process_safety_task=None

        now_ms=time.time_ns()//1_000_000
        cached=self._process_safety_snapshot
        observed=None
        if isinstance(cached,dict):
            try:
                observed=int(cached.get("physical_process_observed_at_ms"))
            except (TypeError,ValueError):
                observed=None
        stale=(
            observed is None
            or max(0,now_ms-observed)>int(self.PHYSICAL_PROCESS_REFRESH_MS)
        )
        if stale and self._process_safety_task is None:
            self._process_safety_task=asyncio.create_task(
                asyncio.to_thread(self.process_safety.snapshot)
            )
        if isinstance(cached,dict):
            return cached
        return self._pending_process_safety()

    def _node_attestation(self,process_safety:dict|None=None)->dict:
        source_root=Path(__file__).resolve().parents[2]
        marker=source_root/".remotemcp-release.json"
        release_commit=None
        if marker.is_file():
            try:
                payload=json.loads(marker.read_text(encoding="utf-8-sig"))
                release_commit=payload.get("commit")
            except Exception:
                release_commit=None
        schema=self.db.get_meta("schema") or {}
        out={
            "source_dir":str(source_root),
            "release_commit":release_commit,
            "execution_root":str(self.config.root),
            "runtime_dir":str(self.config.runtime_dir),
            "schema_version":schema.get("version"),
            "schema_sha256":schema.get("sha256"),
            "python_prefix":str(Path(sys.prefix).resolve()),
            "infrastructure_root":os.environ.get("REMOTEMCP_INFRA_ROOT"),
            "log_dir":os.environ.get("REMOTEMCP_LOG_DIR"),
            "temp_dir":os.environ.get("REMOTEMCP_TEMP_DIR") or os.environ.get("TEMP"),
            "cache_dir":os.environ.get("REMOTEMCP_CACHE_DIR") or os.environ.get("XDG_CACHE_HOME"),
            "control_dir":os.environ.get("REMOTEMCP_CONTROL_DIR"),
            "zero_c_mode":os.environ.get("REMOTEMCP_ZERO_C")=="1",
        }
        if isinstance(process_safety,dict):
            out.update(process_safety)
        return out

    def _schedule_self_restart(self):
        source_root=Path(__file__).resolve().parents[2]
        runtime=str(self.config.runtime_dir)
        root=str(self.config.root)
        helper=(
            "import subprocess,sys,time;"
            "time.sleep(1.5);"
            "flags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|"
            "getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)) if sys.platform.startswith('win') else 0;"
            "subprocess.Popen([sys.argv[1],'-m','remotemcp.node','run','--runtime-dir',sys.argv[3],'--root',sys.argv[4]],"
            "cwd=sys.argv[2],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
            "creationflags=flags)"
        )
        flags=0
        if os.name=="nt":
            flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
        subprocess.Popen(
            [sys.executable,"-c",helper,sys.executable,str(source_root),runtime,root],
            cwd=str(source_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    async def run_forever(self):
        if not self.identity.paired:
            raise RuntimeError("node must be paired before run")
        await self.start()
        try:
            last_hb=0.0
            backoff=self.RECONNECT_INITIAL_SECONDS
            loop=asyncio.get_running_loop()
            while not self._stop.is_set():
                try:
                    now=loop.time()
                    if now-last_hb>=self.config.heartbeat_seconds:
                        capacity=self.jobs.capacity_snapshot()
                        hb_params=inspect.signature(self.client.heartbeat).parameters
                        if "active_job_summaries" in hb_params:
                            process_safety=await self._process_safety_for_heartbeat()
                            await self.client.heartbeat(
                                capacity["active_node_jobs"],
                                capacity["unresolved_node_jobs"],
                                capacity["capacity_reconciliation_complete"],
                                capacity["candidate_nonterminal_routed_jobs"],
                                capacity.get("active_job_summaries",[]),
                                self._node_attestation(process_safety),
                            )
                        elif "unresolved_node_jobs" in hb_params:
                            # V3.0-compatible custom clients receive reconciled
                            # counts but not V3.1 provenance/attestation.
                            await self.client.heartbeat(
                                capacity["active_node_jobs"],
                                capacity["unresolved_node_jobs"],
                                capacity["capacity_reconciliation_complete"],
                                capacity["candidate_nonterminal_routed_jobs"],
                            )
                        else:
                            # Compatibility for older/custom node clients.
                            # Their heartbeat is accepted for liveness only;
                            # the gateway will not treat it as resolved capacity.
                            await self.client.heartbeat(
                                capacity["active_node_jobs"]
                            )
                        last_hb=now
                    envelope=await self.client.poll()
                    if envelope is not None:
                        result=await self.executor.execute(envelope)
                        await self.client.result(envelope["command_id"],result)
                        if self.executor.restart_requested:
                            self._schedule_self_restart()
                            self._stop.set()
                            continue
                    backoff=self.RECONNECT_INITIAL_SECONDS
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not self._retryable_connection_error(exc):
                        raise
                    print(
                        f"RemoteMCP node connection interrupted: {type(exc).__name__}: {exc}; "
                        f"retrying in {backoff:g}s",
                        file=sys.stderr,
                        flush=True,
                    )
                    await self._wait_reconnect(backoff)
                    backoff=min(backoff*2,self.RECONNECT_MAX_SECONDS)
        finally:
            await self.stop()
