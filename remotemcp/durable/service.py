from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from .config import DurableConfig
from .db import Database
from .errors import DurableError
from .events import EventRepository
from .jobs import JobRepository
from .models import JobState, OperationState, ProcessFingerprint, TERMINAL_JOB_STATES, now_ms
from .operations import OperationRepository
from .process import (
    read_json,
    resolve_executable,
    signal_owned_process,
    spawn_worker,
    verify_fingerprint,
)
from .reconcile import Reconciler
from remotemcp.workspace_layout import enforce_agent_git_worktree_policy


class DurableService:
    def __init__(self, config: DurableConfig):
        self.config = config
        self.db = Database(config.runtime_dir)
        self.db.bootstrap()
        self.operations = OperationRepository(self.db)
        self.jobs = JobRepository(self.db)
        self.events = EventRepository(self.db)
        self.reconciler = Reconciler(
            runtime_dir=config.runtime_dir,
            jobs=self.jobs,
            events=self.events,
            operations=self.operations,
            starting_grace_seconds=config.starting_grace_seconds,
        )
        self.reconciler.reconcile_all()
        self._supervisor_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._supervisor_task and not self._supervisor_task.done():
            return
        self._stop.clear()
        self._supervisor_task = asyncio.create_task(
            self._supervisor_loop(),
            name="remotemcp-v2a-supervisor",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None

    def _workspace_root(self, override=None) -> Path:
        root = Path(override).resolve() if override is not None else self.config.workspace_root.resolve()
        approved = tuple(
            Path(p).resolve()
            for p in (
                self.config.approved_workspace_roots
                or (self.config.workspace_root,)
            )
        )
        if not any(root==base or root.is_relative_to(base) for base in approved):
            raise DurableError(
                "PATH_ESCAPE",
                "workspace root is outside approved roots",
                workspace_root=str(root),
            )
        return root

    def _safe_cwd(self, cwd: str, workspace_root=None) -> Path:
        root = self._workspace_root(workspace_root)
        path = (root / cwd).resolve()
        if path!=root and not path.is_relative_to(root):
            raise DurableError("PATH_ESCAPE", "cwd is outside workspace", cwd=cwd)
        if not path.is_dir():
            raise DurableError("NOT_FOUND", "cwd does not exist", cwd=cwd)
        return path

    def _job_response(self, row, *, replayed: bool = False) -> dict:
        return {
            "job_id": row["job_id"],
            "operation_id": row["operation_id"],
            "state": row["state"],
            "replayed": replayed,
            "created_at_ms": row["created_at_ms"],
        }

    async def job_submit(
        self,
        operation_id: str,
        argv: list[str],
        cwd: str = ".",
        agent_id: str = "",
        project_id: str = "",
        task_id: str = "",
        *,
        env_overrides: dict[str, str] | None = None,
        workspace_root_override: Path | str | None = None,
    ) -> dict:
        await self.start()
        if not isinstance(argv, list) or not argv or not all(
            isinstance(x, str) and x for x in argv
        ):
            raise DurableError("INVALID_ARGUMENT", "argv must be a non-empty list[str]")

        workspace_root = self._workspace_root(workspace_root_override)
        cwd_path = self._safe_cwd(cwd, workspace_root)
        resolved = resolve_executable(argv[0], cwd_path, self.config.allowed_cmds)
        normalized_argv = [resolved, *argv[1:]]
        enforce_agent_git_worktree_policy(normalized_argv)
        env_overrides = dict(env_overrides or {})
        allowed_env = {"REMOTEMCP_DEVICE_ID", "REMOTEMCP_PROJECT_ID"}
        if any(k not in allowed_env for k in env_overrides):
            raise DurableError("INVALID_ARGUMENT", "unsupported durable env override")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in env_overrides.items()):
            raise DurableError("INVALID_ARGUMENT", "env overrides must be string pairs")
        normalized = {
            "argv": normalized_argv,
            "cwd": cwd,
            "workspace_root": str(workspace_root),
        }
        if env_overrides:
            normalized["env_overrides"] = env_overrides

        op, created = self.operations.reserve(
            operation_id,
            "JOB_SUBMIT",
            normalized,
            agent_id=agent_id,
            project_id=project_id,
            task_id=task_id,
        )
        state = op["state"]
        if not created:
            if state == OperationState.SUCCEEDED.value:
                result = self.operations.replay_result(op) or {}
                return {**result, "replayed": True}
            existing = self.jobs.by_operation(operation_id)
            if existing is not None and state == OperationState.EXECUTING.value:
                result = self._job_response(existing)
                self.operations.succeed(operation_id, result)
                return {**result, "replayed": True}
            if state in (
                OperationState.RESERVED.value,
                OperationState.EXECUTING.value,
                OperationState.IN_DOUBT.value,
            ):
                raise DurableError("OPERATION_IN_DOUBT", "operation requires reconciliation")
            if state == OperationState.FAILED_FINAL.value:
                raise DurableError(
                    op["error_code"] or "INVALID_ARGUMENT",
                    "previous operation failed finally",
                )

        self.operations.mark_executing(operation_id)
        command = {
            "argv": normalized_argv,
            "workspace_root": str(workspace_root),
            "env_overrides": env_overrides,
        }
        row, _ = self.jobs.create(operation_id, command, cwd)
        result = self._job_response(row)
        self.operations.succeed(operation_id, result)
        self.events.append(
            "JOB_SUBMITTED",
            {"state": row["state"]},
            job_id=row["job_id"],
            operation_id=operation_id,
        )
        return result

    def job_get(self, job_id: str) -> dict:
        row = self.jobs.get(job_id)
        return {
            "job_id": row["job_id"],
            "operation_id": row["operation_id"],
            "state": row["state"],
            "created_at_ms": row["created_at_ms"],
            "started_at_ms": row["started_at_ms"],
            "finished_at_ms": row["finished_at_ms"],
            "exit_code": row["exit_code"],
            "last_output_at_ms": row["last_output_at_ms"],
            "last_heartbeat_at_ms": row["last_heartbeat_at_ms"],
            "terminal_event_id": row["terminal_event_id"],
        }

    async def job_wait(
        self,
        job_id: str,
        subscriber_id: str,
        mode: str = "terminal",
        after_event_id: int = 0,
        timeout_seconds: int = 55,
    ) -> dict:
        await self.start()
        if mode not in {"terminal", "heartbeat", "progress"}:
            raise DurableError("INVALID_ARGUMENT", "invalid wait mode", mode=mode)
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds < 0 or timeout_seconds > 55:
            raise DurableError("INVALID_ARGUMENT", "timeout_seconds must be 0..55")

        row = self.jobs.get(job_id)
        if mode in {"heartbeat", "progress"}:
            return self._wait_response(row, changed=True)

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            row = self.jobs.get(job_id)
            event = self.events.terminal_event(job_id)
            ack = self.events.cursor(subscriber_id, job_id) if subscriber_id else 0
            threshold = max(int(after_event_id), ack)
            if event and int(event["event_id"]) > threshold:
                return self._wait_response(
                    row,
                    changed=True,
                    event_id=int(event["event_id"]),
                )
            if timeout_seconds == 0 or asyncio.get_running_loop().time() >= deadline:
                return self._wait_response(row, changed=False)
            await asyncio.sleep(0.25)

    def _wait_response(self, row, *, changed: bool, event_id: int = 0) -> dict:
        started = row["started_at_ms"] or row["created_at_ms"]
        return {
            "job_id": row["job_id"],
            "state": row["state"],
            "changed": changed,
            "event_id": event_id,
            "terminal": row["state"] in TERMINAL_JOB_STATES,
            "elapsed_ms": max(0, now_ms() - started),
            "last_output_at_ms": row["last_output_at_ms"],
        }

    def job_logs(
        self,
        job_id: str,
        stream: str = "stdout",
        cursor: int = 0,
        limit_bytes: int = 65536,
    ) -> dict:
        if stream not in {"stdout", "stderr"}:
            raise DurableError("INVALID_ARGUMENT", "stream must be stdout or stderr")
        cursor = int(cursor)
        limit_bytes = int(limit_bytes)
        if cursor < 0 or limit_bytes < 1 or limit_bytes > 262144:
            raise DurableError("INVALID_ARGUMENT", "invalid cursor/limit_bytes")
        row = self.jobs.get(job_id)
        rel = row["stdout_path"] if stream == "stdout" else row["stderr_path"]
        path = self.config.runtime_dir / rel
        data = b""
        eof = True
        if path.exists():
            with open(path, "rb") as f:
                f.seek(cursor)
                data = f.read(limit_bytes)
                eof = f.read(1) == b""
        return {
            "job_id": job_id,
            "stream": stream,
            "cursor": cursor,
            "next_cursor": cursor + len(data),
            "data": data.decode("utf-8", errors="replace"),
            "eof": eof,
        }

    def job_result(
        self,
        job_id: str,
        subscriber_id: str = "",
        ack_event_id: int = 0,
        operation_id: str = "",
    ) -> dict:
        row = self.jobs.get(job_id)
        terminal = row["state"] in TERMINAL_JOB_STATES
        result_data = None
        result_path = self.config.runtime_dir / row["result_path"]
        if result_path.exists():
            try:
                result_data = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result_data = None

        acked = self.events.cursor(subscriber_id, job_id) if subscriber_id else 0
        if ack_event_id:
            if not subscriber_id or not operation_id:
                raise DurableError(
                    "INVALID_ARGUMENT",
                    "subscriber_id and operation_id are required when ack_event_id > 0",
                )
            event = self.events.terminal_event(job_id)
            if event is None or int(event["event_id"]) != int(ack_event_id):
                raise DurableError(
                    "INVALID_ARGUMENT",
                    "ack_event_id is not this job's terminal event",
                )
            normalized = {
                "job_id": job_id,
                "subscriber_id": subscriber_id,
                "ack_event_id": int(ack_event_id),
            }
            op, created = self.operations.reserve(
                operation_id,
                "EVENT_ACK",
                normalized,
            )
            if not created:
                if op["state"] == OperationState.SUCCEEDED.value:
                    replay = self.operations.replay_result(op) or {}
                    acked = int(replay.get("acked_event_id", acked))
                elif op["state"] in (
                    OperationState.RESERVED.value,
                    OperationState.EXECUTING.value,
                    OperationState.IN_DOUBT.value,
                ):
                    raise DurableError(
                        "OPERATION_IN_DOUBT",
                        "ACK operation is not safely replayable yet",
                    )
                else:
                    raise DurableError(
                        op["error_code"] or "INVALID_ARGUMENT",
                        "previous ACK operation failed",
                    )
            else:
                self.operations.mark_executing(operation_id)
                acked = self.events.ack(
                    subscriber_id,
                    job_id,
                    int(ack_event_id),
                )
                self.operations.succeed(
                    operation_id,
                    {"acked_event_id": acked},
                )

        return {
            "job_id": job_id,
            "state": row["state"],
            "terminal": terminal,
            "exit_code": row["exit_code"],
            "result": result_data,
            "terminal_event_id": row["terminal_event_id"],
            "acked_event_id": acked,
        }

    async def job_cancel(
        self,
        operation_id: str,
        job_id: str,
        agent_id: str = "",
        project_id: str = "",
        task_id: str = "",
    ) -> dict:
        await self.start()
        op, created = self.operations.reserve(
            operation_id,
            "JOB_CANCEL",
            {"job_id": job_id},
            agent_id=agent_id,
            project_id=project_id,
            task_id=task_id,
        )
        if not created:
            if op["state"] == OperationState.SUCCEEDED.value:
                result = self.operations.replay_result(op) or {}
                return {**result, "replayed": True}
            if op["state"] in (
                OperationState.RESERVED.value,
                OperationState.EXECUTING.value,
                OperationState.IN_DOUBT.value,
            ):
                raise DurableError(
                    "OPERATION_IN_DOUBT",
                    "cancel operation is already active",
                )
            raise DurableError(
                op["error_code"] or "INVALID_ARGUMENT",
                "previous cancel failed",
            )

        self.operations.mark_executing(operation_id)
        row = self.jobs.get(job_id)
        ownership_verified = False

        if row["state"] in TERMINAL_JOB_STATES:
            pass
        elif row["state"] == JobState.QUEUED.value:
            row, _ = self.jobs.terminalize(
                job_id,
                JobState.CANCELLED.value,
                exit_code=None,
            )
            self.events.ensure_terminal(
                job_id,
                row["operation_id"],
                JobState.CANCELLED.value,
                {"exit_code": None},
            )
        else:
            row, _ = self.jobs.transition(
                job_id,
                (
                    JobState.STARTING.value,
                    JobState.RUNNING.value,
                    JobState.CANCELLING.value,
                ),
                JobState.CANCELLING.value,
                cancel_requested_at_ms=now_ms(),
            )
            fp = None
            if row["payload_fingerprint_json"]:
                try:
                    fp = ProcessFingerprint(
                        **json.loads(row["payload_fingerprint_json"])
                    )
                except (TypeError, json.JSONDecodeError):
                    fp = None
            if fp and verify_fingerprint(fp):
                signal_owned_process(fp)
                ownership_verified = True
            else:
                meta = read_json(
                    self.config.runtime_dir / "jobs" / job_id / "worker.json"
                )
                w = (meta or {}).get("worker_fingerprint")
                if w:
                    try:
                        wfp = ProcessFingerprint(**w)
                    except TypeError:
                        wfp = None
                    if (
                        wfp
                        and verify_fingerprint(wfp)
                        and (meta or {}).get("phase") == "WAITING_FOR_COMMIT"
                    ):
                        signal_owned_process(wfp)
                        ownership_verified = True
                        row, _ = self.jobs.terminalize(
                            job_id,
                            JobState.CANCELLED.value,
                            exit_code=None,
                        )
                        self.events.ensure_terminal(
                            job_id,
                            row["operation_id"],
                            JobState.CANCELLED.value,
                            {"exit_code": None},
                        )

        row = self.jobs.get(job_id)
        result = {
            "job_id": job_id,
            "operation_id": operation_id,
            "state": row["state"],
            "replayed": False,
            "ownership_verified": ownership_verified,
        }
        self.operations.succeed(operation_id, result)
        return result

    async def _supervisor_loop(self) -> None:
        try:
            while not self._stop.is_set():
                await self._tick()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.config.poll_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> None:
        self.reconciler.reconcile_jobs()
        active = self.jobs.count_states(
            (
                JobState.STARTING.value,
                JobState.RUNNING.value,
                JobState.CANCELLING.value,
            )
        )
        slots = max(0, self.config.max_parallel_jobs - active)
        if slots <= 0:
            return
        queued = self.jobs.list_states((JobState.QUEUED.value,))
        for row in queued[:slots]:
            await self._launch(row)

    async def _launch(self, row) -> None:
        updated, changed = self.jobs.transition(
            row["job_id"],
            (JobState.QUEUED.value,),
            JobState.STARTING.value,
            spawn_started_at_ms=now_ms(),
        )
        if not changed:
            return
        stderr_path = self.config.runtime_dir / updated["stderr_path"]
        try:
            spawn_worker(
                self.config.runtime_dir,
                updated["job_id"],
                updated["launch_nonce"],
                self.config.workspace_root,
                stderr_path,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            row, _ = self.jobs.terminalize(
                updated["job_id"],
                JobState.FAILED.value,
                exit_code=None,
                error_code="SPAWN_RESOURCE_EXHAUSTED_BEFORE_CHILD",
                error_json=json.dumps({"error": str(exc)}),
            )
            self.events.ensure_terminal(
                row["job_id"],
                row["operation_id"],
                JobState.FAILED.value,
                {"exit_code": None, "error": str(exc)},
            )
