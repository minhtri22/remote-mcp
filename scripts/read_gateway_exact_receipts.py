"""Read-only offline gateway receipt proof for an existing device/task/command.

For execution by an authorized gateway operator on the gateway host only.
No database bootstrap/migrations, no polling, no job retry, no lease update.
Never emits executable argv, payload, API key, private identity or secrets.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

SCHEMA = "remotemcp.gateway-exact-receipt.v1"
MAX_COMMANDS = 16


def inspect_gateway(
    db_path: Path, *, device_id: str, task_id: str, command_ids: list[str],
) -> dict:
    if (
        not db_path.is_file()
        or not device_id.startswith("dev_")
        or not task_id.startswith("tsk_")
        or not 1 <= len(command_ids) <= MAX_COMMANDS
        or len(command_ids) != len(set(command_ids))
        or any(not isinstance(x,str) or not x.startswith("cmd_") for x in command_ids)
    ):
        raise ValueError("INVALID_EVIDENCE_IDENTITY_OR_DATABASE")
    conn = sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro", uri=True, timeout=4.0)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        receipts=[]
        for command_id in command_ids:
            row=conn.execute(
                "SELECT command_id,device_id,task_id,project_id,route_generation,"
                "operation_id,operation_step,command_type,request_hash,payload_json,"
                "state,delivery_attempt,lease_expires_at_ms,command_expires_at_ms,"
                "created_at_ms,updated_at_ms,finished_at_ms,error_code "
                "FROM device_commands WHERE command_id=? AND device_id=? AND task_id=?",
                (command_id,device_id,task_id),
            ).fetchone()
            if row is None:
                receipts.append({
                    "command_id":command_id,"status":"NOT_FOUND_OR_NOT_OWNED",
                    "node_reexecution_authorized":False,
                })
                continue
            payload={}
            try:
                payload=json.loads(row["payload_json"] or "{}")
            except (json.JSONDecodeError,ValueError,TypeError):
                pass
            proxy=payload.get("proxy_job_id") if isinstance(payload,dict) else None
            linked=conn.execute(
                "SELECT proxy_job_id,node_job_id,last_known_state,operation_id "
                "FROM routed_jobs WHERE operation_id=? AND task_id=? AND device_id=?",
                (row["operation_id"],task_id,device_id),
            ).fetchone() if row["operation_id"] else None
            receipts.append({
                "command_id":command_id,
                "status":"GATEWAY_ROW_FOUND",
                "device_id":row["device_id"],
                "task_id":row["task_id"],
                "project_id":row["project_id"],
                "route_generation":row["route_generation"],
                "operation_id":row["operation_id"],
                "operation_step":row["operation_step"],
                "command_type":row["command_type"],
                "request_hash":row["request_hash"],
                "proxy_job_id_in_payload":proxy,
                "state":row["state"],
                "delivery_attempt":row["delivery_attempt"],
                "lease_expires_at_ms":row["lease_expires_at_ms"],
                "command_expires_at_ms":row["command_expires_at_ms"],
                "created_at_ms":row["created_at_ms"],
                "updated_at_ms":row["updated_at_ms"],
                "finished_at_ms":row["finished_at_ms"],
                "error_code":row["error_code"],
                "routed_proxy_job_id":linked["proxy_job_id"] if linked else None,
                "routed_node_job_id":linked["node_job_id"] if linked else None,
                "routed_state":linked["last_known_state"] if linked else None,
                "node_receipt_still_requires_independent_comparison":True,
            })
        return {
            "schema":SCHEMA,
            "status":"READ_ONLY_GATEWAY_RECEIPTS",
            "device_id":device_id,"task_id":task_id,
            "receipts":receipts,
            "read_only":True,"gateway_database_unchanged_by_script":True,
            "node_command_dispatch_performed":False,
            "scientific_job_replay_authorized":False,
            "node_upgrade_authorized":False,
        }
    finally:
        conn.close()


def main() -> int:
    p=argparse.ArgumentParser(description="Read-only exact command gateway proof")
    p.add_argument("--db-path",type=Path,required=True)
    p.add_argument("--device-id",required=True)
    p.add_argument("--task-id",required=True)
    p.add_argument("--command-id",action="append",required=True)
    args=p.parse_args()
    evidence=inspect_gateway(
        args.db_path,device_id=args.device_id,task_id=args.task_id,
        command_ids=args.command_id,
    )
    print(json.dumps(evidence,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if all(r["status"]=="GATEWAY_ROW_FOUND" for r in evidence["receipts"]) else 2


if __name__=="__main__":
    raise SystemExit(main())
