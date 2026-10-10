"""Read-only V3.1 managed command delivery reconciliation.

No gateway/node process changes; no retries, claims, leases, job submissions,
requeue, cleanup, or scientific lineage edits. Gateway and node SQL opened
read-only; only optional evidence file (exclusive create) is writable.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "IN_DOUBT"}
MAX_COMMANDS = 80


def ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"DATABASE_MISSING:{path}")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def classify(row: sqlite3.Row, receipt: sqlite3.Row | None, now: int) -> str:
    state = str(row["state"])
    attempts = int(row["delivery_attempt"])
    if state == "QUEUED":
        return "AWAITING_DELIVERY" if int(row["command_expires_at_ms"]) > now else "EXPIRED_NOT_DELIVERED" if attempts == 0 else "EXPIRED_REQUEUED"
    if state == "LEASED":
        if receipt is None:
            return "DELIVERED_LEASE_MISSING_NODE_RECEIPT" if attempts else "LEASED_WITHOUT_ATTEMPT"
        if str(receipt["state"]) in {"SUCCEEDED", "FAILED", "IN_DOUBT"}:
            return "NODE_TERMINAL_GATEWAY_NOT_ACKED"
        return "NODE_RECEIVED_NOT_TERMINAL"
    if state == "CANCELLED" and row["error_code"] == "DEVICE_COMMAND_EXPIRED":
        return "EXPIRED_HISTORICAL_DO_NOT_REPLAY"
    if receipt is None:
        return "GATEWAY_TERMINAL_WITHOUT_NODE_RECEIPT"
    if str(receipt["state"]) in {"SUCCEEDED", "FAILED", "IN_DOUBT"}:
        return "BOTH_TERMINAL"
    return "GATEWAY_TERMINAL_NODE_INCOMPLETE"


def audit(gateway_db: Path, node_db: Path, device_id: str, task_ids: list[str]) -> dict:
    if not task_ids or len(task_ids) > 10:
        raise ValueError("TASK_ID_COUNT_UNEXPECTED")
    if any(not t.startswith("tsk_") for t in task_ids):
        raise ValueError("TASK_ID_FORMAT_UNEXPECTED")
    now = time.time_ns() // 1_000_000
    with closing(ro(gateway_db)) as gw, closing(ro(node_db)) as nd:
        placeholders = ",".join("?" for _ in task_ids)
        commands = gw.execute(
            "SELECT command_id,task_id,operation_id,command_type,state,delivery_attempt,"
            "route_generation,request_hash,lease_expires_at_ms,command_expires_at_ms,"
            "created_at_ms,updated_at_ms,finished_at_ms,error_code "
            "FROM device_commands WHERE device_id=? AND task_id IN (" + placeholders + ") "
            "ORDER BY created_at_ms DESC,command_id DESC LIMIT ?",
            (device_id, *task_ids, MAX_COMMANDS),
        ).fetchall()
        last_command = gw.execute(
            "SELECT state,COUNT(*) n FROM device_commands WHERE device_id=? "
            "AND state IN ('QUEUED','LEASED') GROUP BY state", (device_id,)
        ).fetchall()
        recent = []
        for c in commands:
            receipt = nd.execute(
                "SELECT command_id,route_generation,request_hash,command_type,state,"
                "received_at_ms,started_at_ms,finished_at_ms,error_code "
                "FROM node_commands WHERE command_id=?", (c["command_id"],)
            ).fetchone()
            consistent = (
                receipt is None or (
                    int(receipt["route_generation"]) == int(c["route_generation"])
                    and receipt["request_hash"] == c["request_hash"]
                    and receipt["command_type"] == c["command_type"]
                )
            )
            recent.append({
                "command_id": c["command_id"], "task_id": c["task_id"],
                "operation_id": c["operation_id"],
                "command_type": c["command_type"],
                "gateway_state": c["state"],
                "delivery_attempt": c["delivery_attempt"],
                "lease_expires_at_ms": c["lease_expires_at_ms"],
                "command_expires_at_ms": c["command_expires_at_ms"],
                "expired_now": now >= int(c["command_expires_at_ms"]),
                "created_at_ms": c["created_at_ms"],
                "age_seconds": max(0, (now-int(c["created_at_ms"])) // 1000),
                "gateway_error_code": c["error_code"],
                "node_receipt_present": receipt is not None,
                "node_state": receipt["state"] if receipt is not None else None,
                "node_received_at_ms": receipt["received_at_ms"] if receipt is not None else None,
                "node_error_code": receipt["error_code"] if receipt is not None else None,
                "node_receipt_identity_consistent": consistent,
                "delivery_classification": classify(c, receipt, now),
            })
        live = sum(
            1 for c in recent if c["gateway_state"] in ("QUEUED", "LEASED")
            and not c["expired_now"]
        )
        return {
            "schema": "remotemcp.managed-command-reconciliation.v1",
            "observed_at_ms": now,
            "device_id": device_id, "task_ids": task_ids,
            "command_trace": recent, "trace_limit": MAX_COMMANDS,
            "device_nonterminal_command_inventory": {
                r["state"]: int(r["n"]) for r in last_command
            },
            "live_unexpired_commands_in_returned_trace": live,
            "snapshot_read_only": True,
            "original_commands_mutated": False,
            "node_jobs_relaunched": False,
            "science_jobs_authorized": False,
            "manual_investigation_required": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-db", type=Path, required=True)
    parser.add_argument("--node-db", type=Path, required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--output", type=Path)
    a = parser.parse_args()
    report = audit(a.gateway_db, a.node_db, a.device_id, a.task_id)
    data = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if a.output:
        # Evidence is never overwritten; no science directories are touched.
        with a.output.open("x", encoding="utf-8") as f:
            f.write(data)
    print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
