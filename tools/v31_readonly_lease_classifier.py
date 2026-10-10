"""V3.1 19-lease exact, read-only classifier.

This tool does NOT change gateway/node SQLite, replay routed commands, claim
a task, reconnect an agent, cancel a job, or restart a process. Its only
optional output file is an exclusive-create JSON snapshot in an operator-approved
evidence directory. Preregistered at 2026-10-10; production execution gated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = "remotemcp.v31.leased-command-evidence.v1"
TERMINAL_NODE = frozenset({"SUCCEEDED", "FAILED", "IN_DOUBT"})
DEVICE_ID = "dev_dd73ebfa742f468f2d212bade88c175b"
EXPECTED_LEASE_COUNT = 19


def _read_only_database(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError("DATABASE_NOT_PRESENT")
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=8.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def classify_command(command: dict[str, Any], receipt: dict[str, Any] | None, now_ms: int) -> str:
    """Classify an immutable LEASED gateway row, never infer replay admission."""
    if command.get("state") != "LEASED":
        return "UNRESOLVED_HOLD"
    cmd_id = command.get("command_id")
    req_hash = command.get("request_hash")
    command_type = command.get("command_type")
    generation = _int_or_none(command.get("route_generation"))
    if not isinstance(cmd_id, str) or not cmd_id.startswith("cmd_"):
        return "UNRESOLVED_HOLD"
    if not isinstance(req_hash, str) or len(req_hash) != 64:
        return "UNRESOLVED_HOLD"
    if not isinstance(command_type, str) or generation is None:
        return "UNRESOLVED_HOLD"
    if receipt is not None:
        if (receipt.get("command_id") != cmd_id
                or receipt.get("request_hash") != req_hash
                or receipt.get("command_type") != command_type
                or _int_or_none(receipt.get("route_generation")) != generation):
            return "IDENTITY_MISMATCH_HOLD"

    command_expiry = _int_or_none(command.get("command_expires_at_ms"))
    lease_expiry = _int_or_none(command.get("lease_expires_at_ms"))
    if command_expiry is None or lease_expiry is None or command_expiry < 0 or lease_expiry < 0:
        return "UNRESOLVED_HOLD"
    if now_ms < command_expiry or now_ms < lease_expiry:
        return "UNEXPIRED_LEASE_HOLD"
    attempts = _int_or_none(command.get("delivery_attempt"))
    if attempts is None or attempts < 0:
        return "UNRESOLVED_HOLD"
    if receipt is not None:
        node_state = receipt.get("state")
        if node_state in TERMINAL_NODE:
            return "EXPIRED_NODE_TERMINAL_GATEWAY_STALE"
        if node_state in {"RECEIVED", "EXECUTING", "QUEUED", "RUNNING"}:
            return "EXPIRED_NODE_RECEIVED_NONTERMINAL"
        return "UNRESOLVED_HOLD"
    if attempts > 0:
        return "EXPIRED_DELIVERED_NO_RECEIPT"
    return "EXPIRED_NEVER_DELIVERED"


def _rows(db: sqlite3.Connection, device_id: str) -> list[dict[str, Any]]:
    # Deliberately excludes command payload_json, result_json, token and secrets.
    rs = db.execute(
        "SELECT command_id,device_id,project_id,task_id,command_type,"
        "state,delivery_attempt,route_generation,request_hash,"
        "created_at_ms,command_expires_at_ms,lease_expires_at_ms "
        "FROM device_commands WHERE device_id=? AND state='LEASED' "
        "ORDER BY created_at_ms,command_id",
        (device_id,),
    ).fetchall()
    return [dict(r) for r in rs]


def snapshot(gateway_db: Path, node_db: Path, device_id: str = DEVICE_ID,
             expected: int = EXPECTED_LEASE_COUNT, now_ms: int | None = None) -> dict[str, Any]:
    if not device_id.startswith("dev_"):
        raise ValueError("INVALID_DEVICE_ID")
    if expected < 0:
        raise ValueError("INVALID_EXPECTED_COUNT")
    observed_at = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
    with closing(_read_only_database(gateway_db)) as gw, closing(_read_only_database(node_db)) as nd:
        commands = _rows(gw, device_id)
        out: list[dict[str, Any]] = []
        for command in commands:
            r = nd.execute(
                "SELECT command_id,request_hash,route_generation,"
                "command_type,state,received_at_ms,started_at_ms,finished_at_ms,error_code "
                "FROM node_commands WHERE command_id=?",
                (command["command_id"],),
            ).fetchone()
            receipt = dict(r) if r is not None else None
            klass = classify_command(command, receipt, observed_at)
            out.append({
                "command_id": command["command_id"],
                "task_id": command["task_id"],
                "project_id": command["project_id"],
                "command_type": command["command_type"],
                "route_generation": command["route_generation"],
                "request_hash": command["request_hash"],
                "gateway_state": command["state"],
                "delivery_attempt": command["delivery_attempt"],
                "created_at_ms": command["created_at_ms"],
                "command_expires_at_ms": command["command_expires_at_ms"],
                "lease_expires_at_ms": command["lease_expires_at_ms"],
                "node_receipt_present": receipt is not None,
                "node_state": receipt["state"] if receipt else None,
                "node_received_at_ms": receipt["received_at_ms"] if receipt else None,
                "node_finished_at_ms": receipt["finished_at_ms"] if receipt else None,
                "node_error_code": receipt["error_code"] if receipt else None,
                "fingerprint_consistent": (
                    klass != "IDENTITY_MISMATCH_HOLD" if receipt is not None else None
                ),
                "classification": klass,
                "auto_replay_authorized": False,
                "mutation_authorized": False,
            })
    counts: dict[str, int] = {}
    for row in out:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    hold = any(row["classification"] in {
        "UNEXPIRED_LEASE_HOLD", "IDENTITY_MISMATCH_HOLD", "UNRESOLVED_HOLD"
    } for row in out)
    canonical = json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "schema": SCHEMA,
        "device_id": device_id,
        "observed_at_ms": observed_at,
        "expected_historical_leased": expected,
        "actual_leased": len(out),
        "inventory_drift": len(out) != expected,
        "hard_safety_hold": hold,
        "lease_inventory_digest_sha256": hashlib.sha256(canonical).hexdigest(),
        "classification_counts": counts,
        "rows": out,
        "read_only": True,
        "gateway_db_mutated": False,
        "node_db_mutated": False,
        "historical_command_replay_authorized": False,
        "science_task_mutation_authorized": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gateway-db", type=Path, required=True)
    p.add_argument("--node-db", type=Path, required=True)
    p.add_argument("--device-id", default=DEVICE_ID)
    p.add_argument("--historical-count", type=int, default=EXPECTED_LEASE_COUNT)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = snapshot(a.gateway_db, a.node_db, a.device_id, a.historical_count)
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if a.output:
        # Creation allowed only for NEW audit evidence; never overwrite.
        with a.output.open("x", encoding="utf-8", newline="\n") as f:
            f.write(serialized)
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
