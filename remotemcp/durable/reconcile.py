from __future__ import annotations

import json
from pathlib import Path

from .events import EventRepository
from .jobs import JobRepository
from .models import JobState, ProcessFingerprint, TERMINAL_JOB_STATES, now_ms
from .operations import OperationRepository
from .process import (
    atomic_write_json,
    command_sha256,
    read_json,
    signal_owned_process,
    verify_fingerprint,
    worker_argv,
)


class Reconciler:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        jobs: JobRepository,
        events: EventRepository,
        operations: OperationRepository,
        starting_grace_seconds: int = 30,
    ):
        self.runtime_dir = runtime_dir
        self.jobs = jobs
        self.events = events
        self.operations = operations
        self.starting_grace_seconds = starting_grace_seconds

    def _job_dir(self, job_id: str) -> Path:
        return self.runtime_dir / "jobs" / job_id

    @staticmethod
    def _fp(data: dict | None) -> ProcessFingerprint | None:
        if not data:
            return None
        try:
            return ProcessFingerprint(**data)
        except TypeError:
            return None

    def _terminalize_from_marker(self, row, marker: dict) -> bool:
        if marker.get("job_id") != row["job_id"]:
            return False
        if marker.get("launch_nonce") != row["launch_nonce"]:
            return False
        state = marker.get("terminal_state")
        if state not in {
            JobState.SUCCEEDED.value,
            JobState.FAILED.value,
            JobState.CANCELLED.value,
        }:
            return False
        exit_code = marker.get("exit_code")
        new_row, _ = self.jobs.terminalize(
            row["job_id"],
            state,
            exit_code=exit_code,
        )
        event_id = self.events.ensure_terminal(
            row["job_id"],
            row["operation_id"],
            state,
            {"exit_code": exit_code},
        )
        if new_row["terminal_event_id"] != event_id:
            self.jobs.update_fields(row["job_id"], terminal_event_id=event_id)
        return True

    def _mark_lost(self, row, reason: str) -> None:
        self.jobs.terminalize(
            row["job_id"],
            JobState.LOST.value,
            exit_code=None,
            error_code="PROCESS_OWNERSHIP_MISMATCH",
            error_json=json.dumps({"reason": reason}, sort_keys=True),
        )
        self.events.ensure_terminal(
            row["job_id"],
            row["operation_id"],
            JobState.LOST.value,
            {"reason": reason, "exit_code": None},
        )

    def _republish_launch_commit(self, row) -> None:
        job_dir = self._job_dir(row["job_id"])
        atomic_write_json(
            job_dir / "launch.commit",
            {
                "protocol_version": 1,
                "job_id": row["job_id"],
                "launch_nonce": row["launch_nonce"],
                "committed_at_ms": row["started_at_ms"] or now_ms(),
            },
        )

    def reconcile_operations(self) -> None:
        rows = self.operations.db.query_all(
            "SELECT * FROM operations WHERE state IN ('RESERVED','EXECUTING')"
        )
        for row in rows:
            if row["state"] == "RESERVED":
                self.operations.fail(
                    row["operation_id"],
                    "TRANSPORT_TIMEOUT",
                    {"reason": "reserved operation found at startup without execution"},
                    retryable=True,
                )
                continue

            job = self.jobs.by_operation(row["operation_id"])
            if job is not None:
                result = {
                    "job_id": job["job_id"],
                    "operation_id": row["operation_id"],
                    "state": job["state"],
                    "replayed": False,
                    "created_at_ms": job["created_at_ms"],
                }
                self.operations.succeed(row["operation_id"], result)
            else:
                self.operations.mark_in_doubt(
                    row["operation_id"],
                    {"reason": "executing operation has no reconcilable durable evidence"},
                )

    def reconcile_jobs(self) -> None:
        rows = self.jobs.db.query_all("SELECT * FROM jobs ORDER BY created_at_ms,job_id")
        now = now_ms()
        for row in rows:
            state = row["state"]
            job_dir = self._job_dir(row["job_id"])
            marker = read_json(job_dir / "terminal.json")

            if state in TERMINAL_JOB_STATES:
                self.events.ensure_terminal(
                    row["job_id"],
                    row["operation_id"],
                    state,
                    {"exit_code": row["exit_code"]},
                )
                continue

            if marker and self._terminalize_from_marker(row, marker):
                continue

            if state == JobState.QUEUED.value:
                continue

            worker_meta = read_json(job_dir / "worker.json")
            worker_fp = self._fp((worker_meta or {}).get("worker_fingerprint"))
            valid_worker = bool(worker_fp and verify_fingerprint(worker_fp))

            if state == JobState.STARTING.value:
                if valid_worker:
                    expected_hash = command_sha256(
                        worker_argv(self.runtime_dir, row["job_id"], row["launch_nonce"])
                    )
                    if worker_fp.command_sha256 != expected_hash:
                        self._mark_lost(row, "worker command fingerprint mismatch")
                        continue
                    updated, changed = self.jobs.transition(
                        row["job_id"],
                        (JobState.STARTING.value,),
                        JobState.RUNNING.value,
                        worker_fingerprint_json=json.dumps(worker_fp.to_dict(), sort_keys=True),
                        started_at_ms=row["started_at_ms"] or now_ms(),
                    )
                    if changed:
                        self._republish_launch_commit(updated)
                    continue

                started = row["spawn_started_at_ms"] or row["created_at_ms"]
                if now - started > self.starting_grace_seconds * 1000:
                    self._mark_lost(row, "STARTING worker handshake missing or unverifiable")
                continue

            if state in (JobState.RUNNING.value, JobState.CANCELLING.value):
                if valid_worker:
                    if (
                        (worker_meta or {}).get("phase") == "WAITING_FOR_COMMIT"
                        and not (job_dir / "launch.commit").exists()
                    ):
                        self._republish_launch_commit(row)
                    if state == JobState.CANCELLING.value:
                        payload_fp = self._fp(
                            json.loads(row["payload_fingerprint_json"])
                            if row["payload_fingerprint_json"]
                            else None
                        )
                        if payload_fp and verify_fingerprint(payload_fp):
                            try:
                                signal_owned_process(payload_fp)
                            except Exception:
                                pass
                    continue
                # A worker can exit milliseconds after the payload exits while it is
                # still atomically publishing terminal.json. Avoid misclassifying that
                # narrow publication window as LOST; stale identity is still classified
                # conservatively once the last heartbeat is older than 2 seconds.
                last_seen = (
                    row["last_heartbeat_at_ms"]
                    or row["started_at_ms"]
                    or row["updated_at_ms"]
                    or row["created_at_ms"]
                )
                if now - int(last_seen) <= 2000:
                    continue
                self._mark_lost(row, f"{state} worker identity is no longer valid")

    def reconcile_all(self) -> None:
        self.reconcile_operations()
        self.reconcile_jobs()
