"""V31 four-ambiguous-lease, immutable cross-journal forensics (NO repair).

Requires a frozen 19-lease inventory with an exact SHA-256 digest, then opens
gateway runtime, node runtime, and durable runtime SQLite in mode=ro.
No process launch/replay/reconcile, no writes to database, no secret payloads
or command argv in output; output JSON is exclusive-create when requested.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

FROZEN_DIGEST = "9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85"
EXPECTED_TYPES = frozenset({"JOB_SUBMIT", "JOB_GET", "PROJECT_PROBE", "TASK_BASE_RESOLVE"})
TARGET_COUNTS = {"EXPIRED_DELIVERED_NO_RECEIPT": 3, "EXPIRED_NODE_RECEIVED_NONTERMINAL": 1}
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "LOST"})


def ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError("SOURCE_DATABASE_NOT_PRESENT")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_frozen(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if raw.get("schema") != "remotemcp.v31.leased-command-evidence.v1":
        raise RuntimeError("FROZEN_SCHEMA_INVALID")
    device = raw.get("device_id")
    if (not isinstance(device, str) or not device.startswith("dev_")
            or len(device) != 36 or raw.get("actual_leased") != 19):
        raise RuntimeError("FROZEN_INVENTORY_IDENTITY_INVALID")
    if raw.get("lease_inventory_digest_sha256") != FROZEN_DIGEST:
        raise RuntimeError("FROZEN_DIGEST_FIELD_MISMATCH")
    rows = raw.get("rows")
    if not isinstance(rows, list) or len(rows) != 19:
        raise RuntimeError("FROZEN_ROW_COUNT_INVALID")
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != FROZEN_DIGEST:
        raise RuntimeError("FROZEN_ROWS_DIGEST_INVALID")
    index = {r.get("command_id"): r for r in rows}
    targets = {
        cid: row for cid, row in index.items()
        if row.get("classification") in TARGET_COUNTS
    }
    counts = {kind: sum(r.get("classification") == kind for r in targets.values())
              for kind in TARGET_COUNTS}
    if (len(index) != 19 or len(targets) != 4 or counts != TARGET_COUNTS
            or set(r.get("command_type") for r in targets.values()) != EXPECTED_TYPES
            or any(not isinstance(cid, str) or not cid.startswith("cmd_") for cid in targets)):
        raise RuntimeError("FROZEN_FOUR_COMMANDS_INVALID")
    return {"device_id": device, "targets": targets}


def _one(db, sql, args):
    row = db.execute(sql, args).fetchone()
    return dict(row) if row is not None else None


def forensic_snapshot(frozen_path: Path, gateway_db: Path, node_db: Path,
                      durable_db: Path) -> dict:
    frozen = load_frozen(frozen_path)
    results = []
    with closing(ro(gateway_db)) as gw, closing(ro(node_db)) as nd, closing(ro(durable_db)) as dd:
        for cid, orig in frozen["targets"].items():
            expected_type = orig["command_type"]
            g = _one(gw,
                "SELECT command_id,device_id,task_id,project_id,operation_id,"
                "state,command_type,request_hash,route_generation,"
                "delivery_attempt,command_expires_at_ms,lease_expires_at_ms,payload_json "
                "FROM device_commands WHERE command_id=?", (cid,))
            n = _one(nd,
                "SELECT command_id,command_type,request_hash,route_generation,"
                "state,received_at_ms,started_at_ms,finished_at_ms,error_code "
                "FROM node_commands WHERE command_id=?", (cid,))
            exact = (
                g is not None
                and g["device_id"] == frozen["device_id"] and g["command_type"] == expected_type
                and g["request_hash"] == orig["request_hash"]
                and g["route_generation"] == orig["route_generation"]
            )
            receipt_exact = (
                n is None or (
                    exact and n["command_type"] == expected_type
                    and n["request_hash"] == orig["request_hash"]
                    and n["route_generation"] == orig["route_generation"]
                )
            )
            job = None
            source_proxy = None
            gateway_proxy_state = None
            node_proxy_state = None
            durable_operation_state = None
            worker_evidence_present = False
            payload_evidence_present = False
            events = 0
            terminal_events = 0
            # Only the frozen JOB_SUBMIT can be correlated to durable execution.
            # Decode argv only in memory if present, NEVER log or hash contents.
            if expected_type == "JOB_SUBMIT" and exact:
                try:
                    payload = json.loads(g["payload_json"])
                    source_proxy = payload.get("proxy_job_id")
                except (ValueError, TypeError, AttributeError):
                    source_proxy = None
                if isinstance(source_proxy, str) and source_proxy.startswith("rjob_"):
                    p = _one(gw,
                        "SELECT proxy_job_id,task_id,project_id,node_job_id,"
                        "last_known_state FROM routed_jobs WHERE proxy_job_id=?",
                        (source_proxy,))
                    if p and p["task_id"] == g["task_id"] and p["project_id"] == g["project_id"]:
                        gateway_proxy_state = p["last_known_state"]
                    elif p:
                        exact = False
                    np = _one(nd,
                        "SELECT proxy_job_id,task_id,project_id,node_job_id,state "
                        "FROM node_routed_jobs WHERE proxy_job_id=?", (source_proxy,))
                    if np and np["task_id"] == g["task_id"] and np["project_id"] == g["project_id"]:
                        node_proxy_state = np["state"]
                    elif np:
                        exact = False
                    # Node durable operation id is derived by NodeJobs.submit,
                    # not by parsing or re-executing a shell command.
                    opid = f"v2bd-node-job:{frozen['device_id']}:{cid}"
                    d = _one(dd,
                        "SELECT o.operation_id,o.state AS operation_state,"
                        "j.job_id,j.state AS job_state,j.finished_at_ms,"
                        "j.worker_fingerprint_json,j.payload_fingerprint_json "
                        "FROM operations o LEFT JOIN jobs j ON j.operation_id=o.operation_id "
                        "WHERE o.operation_id=?", (opid,))
                    if d:
                        durable_operation_state = d["operation_state"]
                        job = {
                            "job_id": d["job_id"],
                            "job_state": d["job_state"],
                            "finished_at_ms": d["finished_at_ms"],
                        }
                        worker_evidence_present = bool(d["worker_fingerprint_json"])
                        payload_evidence_present = bool(d["payload_fingerprint_json"])
                        if d["job_id"]:
                            counts = _one(dd,
                                "SELECT COUNT(*) n,COALESCE(SUM(terminal),0) term "
                                "FROM events WHERE job_id=?", (d["job_id"],))
                            events = int(counts["n"])
                            terminal_events = int(counts["term"])
                        if np and np["node_job_id"] != d["job_id"]:
                            exact = False
                        if p and p.get("node_job_id") and p["node_job_id"] != d["job_id"]:
                            exact = False

            if not exact or not receipt_exact:
                verdict = "IDENTITY_CONFLICT_HOLD"
            elif expected_type == "JOB_SUBMIT" and job is not None:
                verdict = "DURABLE_JOB_PRESENT_RECONCILIATION_REQUIRED"
            elif n is not None and n["state"] in {"RECEIVED", "EXECUTING"}:
                verdict = "NODE_NONTERMINAL_STALE_UNRESOLVED"
            else:
                verdict = "NO_PROVEN_EXECUTION_UNRESOLVED"
            results.append({
                "command_id": cid,
                "command_type": expected_type,
                "task_id": orig.get("task_id"),
                "gateway_state": g["state"] if g else None,
                "gateway_changed_since_snapshot": bool(g and (
                    g["state"] != orig["gateway_state"]
                    or g["delivery_attempt"] != orig["delivery_attempt"])),
                "gateway_identity_consistent": exact,
                "node_receipt_present": n is not None,
                "node_state": n["state"] if n else None,
                "node_receipt_identity_consistent": receipt_exact,
                "node_received_at_ms": n["received_at_ms"] if n else None,
                "node_finished_at_ms": n["finished_at_ms"] if n else None,
                "node_error_code": n["error_code"] if n else None,
                "proxy_id": source_proxy if isinstance(source_proxy, str) and source_proxy.startswith("rjob_") else None,
                "gateway_proxy_state": gateway_proxy_state,
                "node_proxy_state": node_proxy_state,
                "durable_operation_state": durable_operation_state,
                "durable_job": job,
                "worker_fingerprint_evidence_present": worker_evidence_present,
                "payload_fingerprint_evidence_present": payload_evidence_present,
                "durable_event_count": events,
                "durable_terminal_event_count": terminal_events,
                "verdict": verdict,
                "mutation_authorized": False,
                "replay_authorized": False,
            })
    serialized = json.dumps(results, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "schema": "remotemcp.v31.four-command-forensics.v1",
        "observed_at_ms": time.time_ns() // 1_000_000,
        "frozen_inventory_digest_sha256": FROZEN_DIGEST,
        "result_digest_sha256": hashlib.sha256(serialized).hexdigest(),
        "device_id": frozen["device_id"],
        "rows": results,
        "all_four_accounted_for": len(results) == 4,
        "all_fully_reconciled": False,
        "read_only": True,
        "database_changes": 0,
        "replay_authorized": False,
        "manual_independent_adjudication_required": True,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--frozen-inventory", required=True, type=Path)
    p.add_argument("--gateway-db", required=True, type=Path)
    p.add_argument("--node-db", required=True, type=Path)
    p.add_argument("--durable-db", required=True, type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    report = forensic_snapshot(
        a.frozen_inventory, a.gateway_db, a.node_db, a.durable_db)
    content = json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if a.output:
        with a.output.open("x", encoding="utf-8", newline="\n") as f:
            f.write(content)
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
