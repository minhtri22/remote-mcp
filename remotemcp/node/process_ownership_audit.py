"""Node-local, read-only reverse PID -> command/proxy/job observability.

No schema bootstrap, DB writes, job execution, signal, service restart or lock
acquisition. This is an on-host forensic snapshot, NOT replay authorization.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from remotemcp.durable.models import ProcessFingerprint
from remotemcp.durable.process import fingerprint_process

NONTERMINAL = {"QUEUED", "STARTING", "RUNNING", "CANCELLING"}
MAX_DB_JOBS = 50_000
MAX_NODE_ROWS = 50_000
MAX_PID_QUERIES = 64


def _open_readonly(path: Path) -> sqlite3.Connection:
    """SQLite URI mode=ro does not create a missing DB or upgrade its schema."""
    if not path.is_file():
        raise FileNotFoundError(f"READ_ONLY_DB_MISSING: {path.name}")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=4.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=3000")
    return con


def _fingerprint(value: object) -> ProcessFingerprint | None:
    if not value:
        return None
    try:
        d = json.loads(value) if isinstance(value, str) else value
        if not isinstance(d, dict):
            return None
        p = ProcessFingerprint(**d)
        if int(p.pid) <= 0 or not p.start_token or not p.executable_canonical or not p.command_sha256:
            return None
        return p
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _process_status(expected: ProcessFingerprint | None) -> tuple[str, dict]:
    if expected is None:
        return "FINGERPRINT_MISSING_OR_INVALID", {}
    try:
        actual = fingerprint_process(expected.pid, expected.command_sha256)
    except (OSError, PermissionError, ValueError):
        return "OS_PROCESS_PROBE_FAILED", {}
    if actual is None:
        # Access denied is also possible, not proof of termination.
        return "NOT_OBSERVABLE_OR_EXITED", {"pid": expected.pid}
    detail = {
        "pid": actual.pid,
        "start_token_matches": actual.start_token == expected.start_token,
        "executable_matches": (
            actual.executable_canonical.casefold() == expected.executable_canonical.casefold()
            if actual.executable_canonical and expected.executable_canonical else False
        ),
    }
    if not detail["start_token_matches"]:
        return "PID_REUSED_OR_START_CHANGED", detail
    if not detail["executable_matches"]:
        return "EXECUTABLE_MISMATCH", detail
    return "EXACT_LIVE_PROCESS", detail


def _snapshot_con(con: sqlite3.Connection, query: str, params=()) -> list[dict]:
    return [dict(row) for row in con.execute(query, params).fetchall()]


def audit_process_ownership(runtime_dir: Path, requested_pids: Iterable[int] = ()) -> dict:
    """Read existing journals with exact PID/start/executable checks.

    A standalone Python process not launched through RemoteMCP is UNMAPPED,
    never assigned to a job by command-line similarity or parent PID alone.
    """
    runtime_dir = Path(runtime_dir).resolve()
    pids = sorted({int(pid) for pid in requested_pids})
    if len(pids) > MAX_PID_QUERIES or any(pid <= 0 for pid in pids):
        raise ValueError("INVALID_PID_QUERY")
    node_path = runtime_dir / "node.db"
    durable_path = runtime_dir / "durable" / "runtime.db"
    node = _open_readonly(node_path)
    try:
        durable = _open_readonly(durable_path)
        try:
            links = _snapshot_con(
                node,
                "SELECT proxy_job_id,node_job_id,task_id,project_id,state "
                "FROM node_routed_jobs ORDER BY proxy_job_id LIMIT ?",
                (MAX_NODE_ROWS + 1,),
            )
            commands = _snapshot_con(
                node,
                "SELECT command_id,route_generation,request_hash,command_type,"
                "payload_json,state FROM node_commands WHERE command_type='JOB_SUBMIT' "
                "ORDER BY received_at_ms,command_id LIMIT ?",
                (MAX_NODE_ROWS + 1,),
            )
            all_jobs = _snapshot_con(
                durable,
                "SELECT job_id,operation_id,state,worker_fingerprint_json,"
                "payload_fingerprint_json,created_at_ms,started_at_ms,finished_at_ms "
                "FROM jobs ORDER BY created_at_ms,job_id LIMIT ?",
                (MAX_DB_JOBS + 1,),
            )
        finally:
            durable.close()
    finally:
        node.close()

    if (len(all_jobs) > MAX_DB_JOBS or len(links) > MAX_NODE_ROWS
        or len(commands) > MAX_NODE_ROWS):
        return {
            "schema": "remotemcp.node-process-ownership-audit.v1",
            "status": "HOLD_TRUNCATED_JOURNAL",
            "read_only": True,
            "cutover_authorized": False, "science_rerun_authorized": False,
        }

    links_by_job = {r["node_job_id"]: r for r in links}
    commands_by_id = {c["command_id"]: c for c in commands}
    inspected = []
    indexed_pids: dict[int, list[dict]] = {}
    # Include all nonterminal jobs as well as terminal jobs which still have
    # a fingerprint for an explicitly requested PID.
    for job in all_jobs:
        worker = _fingerprint(job["worker_fingerprint_json"])
        payload = _fingerprint(job["payload_fingerprint_json"])
        if job["state"] not in NONTERMINAL and not any(
            fp is not None and fp.pid in pids for fp in (worker, payload)
        ):
            continue
        link = links_by_job.get(job["job_id"])
        op = str(job["operation_id"] or "")
        command_id = op.rsplit(":", 1)[-1] if op.startswith("v2bd-node-job:") else None
        command = commands_by_id.get(command_id)
        claimed_proxy = None
        if command:
            try:
                p = json.loads(command["payload_json"])
                claimed_proxy = p.get("proxy_job_id") if isinstance(p, dict) else None
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        trace = {
            "node_job_id": job["job_id"],
            "proxy_job_id": link["proxy_job_id"] if link else None,
            "task_id": link["task_id"] if link else None,
            "project_id": link["project_id"] if link else None,
            "state": job["state"],
            "node_proxy_row_state": link["state"] if link else None,
            "command_id": command_id,
            "node_command_state": command["state"] if command else None,
            "node_command_route_generation": command["route_generation"] if command else None,
            "node_command_request_hash": command["request_hash"] if command else None,
            "journal_proxy_matches": (
                bool(link and command and claimed_proxy == link["proxy_job_id"])
                if command else None
            ),
            # Existing fingerprint records hash submitted command; OS probe
            # proves PID/start/executable only, not full live command-line argv.
            "live_os_argv_hash_verified": False,
            "processes": [],
        }
        for role, fp in (("worker", worker), ("payload", payload)):
            status, evidence = _process_status(fp)
            item = {
                "role": role,
                "pid": fp.pid if fp else None,
                "start_token": fp.start_token if fp else None,
                "command_sha256": fp.command_sha256 if fp else None,
                "verification": status,
                **evidence,
            }
            trace["processes"].append(item)
            if fp:
                indexed_pids.setdefault(fp.pid, []).append({
                    "role": role,
                    "node_job_id": job["job_id"],
                    "proxy_job_id": trace["proxy_job_id"],
                    "verification": status,
                })
        if (any(x["verification"] == "EXACT_LIVE_PROCESS" for x in trace["processes"])
            and job["state"] not in NONTERMINAL):
            trace["integrity_warning"] = "TERMINAL_JOB_WITH_LIVE_PROCESS"
        integrity_flags = []
        if link is None and command_id is not None:
            integrity_flags.append("DURABLE_JOB_WITHOUT_PROXY_LINK")
        if command_id and command is None:
            integrity_flags.append("DURABLE_COMMAND_JOURNAL_MISSING")
        if link is not None and command is not None and not trace["journal_proxy_matches"]:
            integrity_flags.append("COMMAND_PROXY_IDENTITY_MISMATCH")
        if link is not None and link["state"] != job["state"]:
            integrity_flags.append("PROXY_DURABLE_JOB_STATE_DIVERGENCE")
        if "integrity_warning" in trace:
            integrity_flags.append(trace["integrity_warning"])
        trace["integrity_warnings"] = sorted(set(integrity_flags))
        inspected.append(trace)
    pid_results = []
    for pid in pids:
        matches = indexed_pids.get(pid, [])
        if matches:
            exact = [m for m in matches if m["verification"] == "EXACT_LIVE_PROCESS"]
            distinct_jobs = {m["node_job_id"] for m in exact}
            if len(distinct_jobs) > 1:
                state = "AMBIGUOUS_MULTIPLE_LIVE_JOB_OWNERS"
            elif len(distinct_jobs) == 1:
                state = "EXACT_LIVE_DURABLE_JOB_MATCH"
            else:
                state = "RECORDED_PID_WITHOUT_VERIFIED_LIVE_OWNERSHIP"
            pid_results.append({
                "pid": pid,
                "state": state,
                "matches": matches,
            })
        else:
            try:
                current = fingerprint_process(pid, "")
            except (OSError, PermissionError, ValueError):
                current = None
            pid_results.append({
                "pid": pid,
                "state": "UNMAPPED_OS_PROCESS_VISIBLE" if current else "UNMAPPED_OR_NOT_OBSERVABLE",
                "os_start_token": current.start_token if current else None,
                "os_executable": current.executable_canonical if current else None,
                "matches": [],
            })
    # An interrupted JOB_SUBMIT may have reached the node command journal
    # before the proxy/job mapping was committed. Do not infer unexecuted.
    linked_proxy_ids = {link["proxy_job_id"] for link in links}
    orphan_receipts = []
    for cmd in commands:
        try:
            payload_data = json.loads(cmd["payload_json"])
            proxy_id = payload_data.get("proxy_job_id") if isinstance(payload_data, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            proxy_id = None
        if not proxy_id or proxy_id not in linked_proxy_ids:
            orphan_receipts.append({
                "command_id": cmd["command_id"],
                "route_generation": cmd["route_generation"],
                "request_hash": cmd["request_hash"],
                "node_command_state": cmd["state"],
                "proxy_job_id": proxy_id,
                "warning": "COMMAND_RECEIVED_WITHOUT_PROXY_JOB_MAPPING",
            })
    warnings = sorted({
        flag for t in inspected for flag in t["integrity_warnings"]
    } | ({"COMMAND_RECEIPT_UNMAPPED"} if orphan_receipts else set())
      | ({"PID_OWNER_AMBIGUOUS"} if any(
          x["state"] == "AMBIGUOUS_MULTIPLE_LIVE_JOB_OWNERS" for x in pid_results
      ) else set()))
    return {
        "schema": "remotemcp.node-process-ownership-audit.v1",
        "status": "OBSERVED_WITH_INTEGRITY_WARNINGS" if warnings else "READ_ONLY_OBSERVATION",
        "runtime_dir": str(runtime_dir),
        "read_only": True,
        "process_mappings": inspected,
        "pid_queries": pid_results,
        "integrity_warnings": warnings,
        "unmapped_command_receipts": orphan_receipts,
        "tracked_nonterminal_jobs": sum(j["state"] in NONTERMINAL for j in all_jobs),
        "cutover_authorized": False,
        "science_rerun_authorized": False,
        "process_kill_authorized": False,
        "mutation_performed": False,
    }
