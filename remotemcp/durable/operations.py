from __future__ import annotations

import hashlib
import json

from .db import Database
from .errors import DurableError
from .models import OperationState, now_ms


def canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def request_hash(
    *,
    kind: str,
    normalized_arguments,
    principal_key: str = "",
    agent_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> str:
    payload = {
        "kind": kind,
        "normalized_arguments": normalized_arguments,
        "principal_key": principal_key,
        "agent_id": agent_id,
        "project_id": project_id,
        "task_id": task_id,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class OperationRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, operation_id: str):
        return self.db.query_one(
            "SELECT * FROM operations WHERE operation_id=?",
            (operation_id,),
        )

    def reserve(
        self,
        operation_id: str,
        kind: str,
        normalized_arguments,
        *,
        principal_key: str = "",
        agent_id: str = "",
        project_id: str = "",
        task_id: str = "",
    ):
        if not operation_id:
            raise DurableError("INVALID_ARGUMENT", "operation_id is required")
        rh = request_hash(
            kind=kind,
            normalized_arguments=normalized_arguments,
            principal_key=principal_key,
            agent_id=agent_id,
            project_id=project_id,
            task_id=task_id,
        )
        t = now_ms()
        with self.db.transaction() as con:
            row = con.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO operations("
                    "operation_id,kind,request_hash,state,principal_key,agent_id,project_id,task_id,"
                    "attempt_count,created_at_ms,updated_at_ms"
                    ") VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                    (
                        operation_id,
                        kind,
                        rh,
                        OperationState.RESERVED.value,
                        principal_key or None,
                        agent_id or None,
                        project_id or None,
                        task_id or None,
                        t,
                        t,
                    ),
                )
                row = con.execute(
                    "SELECT * FROM operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                return row, True

            if row["request_hash"] != rh or row["kind"] != kind:
                raise DurableError(
                    "OPERATION_CONFLICT",
                    "operation_id was already used with a different request",
                    operation_id=operation_id,
                )
            return row, False

    def mark_executing(self, operation_id: str):
        t = now_ms()
        with self.db.transaction() as con:
            row = con.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise DurableError("NOT_FOUND", "operation not found")
            if row["state"] not in (
                OperationState.RESERVED.value,
                OperationState.FAILED_RETRYABLE.value,
            ):
                return row
            con.execute(
                "UPDATE operations SET state=?, attempt_count=attempt_count+1,"
                "started_at_ms=COALESCE(started_at_ms,?), updated_at_ms=? "
                "WHERE operation_id=?",
                (OperationState.EXECUTING.value, t, t, operation_id),
            )
            return con.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()

    def succeed(self, operation_id: str, result: dict, result_hash: str = ""):
        t = now_ms()
        data = canonical_json(result)
        if not result_hash:
            result_hash = hashlib.sha256(data.encode("utf-8")).hexdigest()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE operations SET state=?, result_json=?, result_hash=?,"
                "error_code=NULL,error_json=NULL,finished_at_ms=?,updated_at_ms=? "
                "WHERE operation_id=?",
                (
                    OperationState.SUCCEEDED.value,
                    data,
                    result_hash,
                    t,
                    t,
                    operation_id,
                ),
            )

    def fail(self, operation_id: str, code: str, details: dict, retryable: bool):
        t = now_ms()
        state = (
            OperationState.FAILED_RETRYABLE.value
            if retryable
            else OperationState.FAILED_FINAL.value
        )
        with self.db.transaction() as con:
            con.execute(
                "UPDATE operations SET state=?,error_code=?,error_json=?,"
                "finished_at_ms=?,updated_at_ms=? WHERE operation_id=?",
                (
                    state,
                    code,
                    canonical_json(details),
                    t,
                    t,
                    operation_id,
                ),
            )

    def mark_in_doubt(self, operation_id: str, details: dict | None = None):
        t = now_ms()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE operations SET state=?,error_code='OPERATION_IN_DOUBT',"
                "error_json=?,updated_at_ms=? WHERE operation_id=?",
                (
                    OperationState.IN_DOUBT.value,
                    canonical_json(details or {}),
                    t,
                    operation_id,
                ),
            )

    @staticmethod
    def replay_result(row):
        if row and row["result_json"]:
            return json.loads(row["result_json"])
        return None
