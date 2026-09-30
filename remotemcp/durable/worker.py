from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import safe_child_env
from .db import Database
from .jobs import JobRepository
from .models import JobState, now_ms
from .process import (
    atomic_write_json,
    command_sha256,
    fingerprint_process,
    read_json,
    signal_owned_process,
    worker_argv,
)


PROTOCOL_VERSION = 1


def _wait_for_commit(path: Path, job_id: str, nonce: str, timeout: float = 300.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = read_json(path)
        if data and data.get("job_id") == job_id and data.get("launch_nonce") == nonce:
            return True
        time.sleep(0.1)
    return False


def _write_result_and_terminal(
    job_dir: Path,
    *,
    job_id: str,
    nonce: str,
    state: str,
    exit_code: int | None,
    payload_fp: dict | None,
) -> None:
    result = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": job_id,
        "state": state,
        "exit_code": exit_code,
        "finished_at_ms": now_ms(),
    }
    result_bytes = atomic_write_json(job_dir / "result.json", result)
    terminal = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": job_id,
        "launch_nonce": nonce,
        "terminal_state": state,
        "exit_code": exit_code,
        "finished_at_ms": result["finished_at_ms"],
        "payload_fingerprint": payload_fp,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
    }
    atomic_write_json(job_dir / "terminal.json", terminal)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--launch-nonce", required=True)
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).resolve()
    db = Database(runtime_dir)
    db.bootstrap()
    jobs = JobRepository(db)

    row = jobs.get(args.job_id)
    if row["launch_nonce"] != args.launch_nonce:
        return 20

    job_dir = runtime_dir / "jobs" / args.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    expected_worker_argv = worker_argv(runtime_dir, args.job_id, args.launch_nonce)
    worker_hash = command_sha256(expected_worker_argv)
    worker_fp = fingerprint_process(os.getpid(), worker_hash)
    if worker_fp is None:
        return 21

    worker_meta = {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": args.job_id,
        "launch_nonce": args.launch_nonce,
        "phase": "WAITING_FOR_COMMIT",
        "worker_fingerprint": worker_fp.to_dict(),
        "payload_fingerprint": None,
        "updated_at_ms": now_ms(),
    }
    atomic_write_json(job_dir / "worker.json", worker_meta)

    if not _wait_for_commit(job_dir / "launch.commit", args.job_id, args.launch_nonce):
        return 22

    row = jobs.get(args.job_id)
    if row["state"] == JobState.CANCELLING.value:
        _write_result_and_terminal(
            job_dir,
            job_id=args.job_id,
            nonce=args.launch_nonce,
            state=JobState.CANCELLED.value,
            exit_code=None,
            payload_fp=None,
        )
        return 0

    command = json.loads(row["command_json"])
    argv = list(command["argv"])
    workspace_root = Path(command["workspace_root"]).resolve()
    cwd = (workspace_root / row["cwd_rel"]).resolve()
    if not cwd.is_relative_to(workspace_root):
        return 23

    worker_meta.update(
        {
            "phase": "SPAWNING_PAYLOAD",
            "updated_at_ms": now_ms(),
        }
    )
    atomic_write_json(job_dir / "worker.json", worker_meta)

    # Re-check cancellation after publishing a phase that prevents the server
    # from treating this worker as safely pre-payload.
    if jobs.get(args.job_id)["state"] == JobState.CANCELLING.value:
        _write_result_and_terminal(
            job_dir,
            job_id=args.job_id,
            nonce=args.launch_nonce,
            state=JobState.CANCELLED.value,
            exit_code=None,
            payload_fp=None,
        )
        return 0

    env = safe_child_env(workspace_root)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    stdout_path = runtime_dir / row["stdout_path"]
    stderr_path = runtime_dir / row["stderr_path"]
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    with open(stdout_path, "ab", buffering=0) as out, open(stderr_path, "ab", buffering=0) as err:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            shell=False,
            creationflags=creationflags,
            close_fds=True,
        )

    payload_hash = command_sha256(argv)
    payload_fp = fingerprint_process(proc.pid, payload_hash)
    if payload_fp is None:
        try:
            proc.terminate()
        except Exception:
            pass
        _write_result_and_terminal(
            job_dir,
            job_id=args.job_id,
            nonce=args.launch_nonce,
            state=JobState.FAILED.value,
            exit_code=None,
            payload_fp=None,
        )
        return 24

    jobs.update_fields(
        args.job_id,
        payload_fingerprint_json=json.dumps(payload_fp.to_dict(), sort_keys=True),
        started_at_ms=now_ms(),
        last_heartbeat_at_ms=now_ms(),
    )

    worker_meta.update(
        {
            "phase": "PAYLOAD_RUNNING",
            "payload_fingerprint": payload_fp.to_dict(),
            "updated_at_ms": now_ms(),
        }
    )
    atomic_write_json(job_dir / "worker.json", worker_meta)

    previous_sizes = (-1, -1)
    while proc.poll() is None:
        state = jobs.get(args.job_id)["state"]
        if state == JobState.CANCELLING.value:
            try:
                signal_owned_process(payload_fp)
            except Exception:
                pass

        try:
            sizes = (stdout_path.stat().st_size, stderr_path.stat().st_size)
        except OSError:
            sizes = previous_sizes
        fields = {"last_heartbeat_at_ms": now_ms()}
        if sizes != previous_sizes:
            fields["last_output_at_ms"] = now_ms()
            previous_sizes = sizes
        jobs.update_fields(args.job_id, **fields)
        time.sleep(0.5)

    exit_code = proc.returncode
    final_state = (
        JobState.CANCELLED.value
        if jobs.get(args.job_id)["state"] == JobState.CANCELLING.value
        else JobState.SUCCEEDED.value if exit_code == 0 else JobState.FAILED.value
    )

    _write_result_and_terminal(
        job_dir,
        job_id=args.job_id,
        nonce=args.launch_nonce,
        state=final_state,
        exit_code=exit_code,
        payload_fp=payload_fp.to_dict(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
