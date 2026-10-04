from __future__ import annotations

import json
import secrets
import sqlite3

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


ACTIVE_ADMISSION_STATES={"ADMITTING","ACTIVE"}
TERMINAL_JOB_STATES={"SUCCEEDED","FAILED","CANCELLED","LOST"}


class TaskJobAdmissionRepository:
    def __init__(self,db):
        self.db=db

    def get(self,admission_id:str):
        row=self.db.query_one(
            "SELECT * FROM task_job_admissions WHERE admission_id=?",
            (admission_id,),
        )
        if row is None:
            raise DurableError("NOT_FOUND","task job admission not found")
        return row

    def by_operation(self,operation_id:str):
        return self.db.query_one(
            "SELECT * FROM task_job_admissions WHERE operation_id=?",
            (operation_id,),
        )

    def active(self,task_id:str,exclude_operation_id:str|None=None):
        sql=(
            "SELECT * FROM task_job_admissions "
            "WHERE task_id=? AND state IN ('ADMITTING','ACTIVE')"
        )
        params=[task_id]
        if exclude_operation_id:
            sql+=" AND operation_id<>?"
            params.append(exclude_operation_id)
        sql+=" ORDER BY sequence DESC LIMIT 1"
        return self.db.query_one(sql,tuple(params))

    def latest(self,task_id:str):
        return self.db.query_one(
            "SELECT * FROM task_job_admissions WHERE task_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (task_id,),
        )

    def reserve(
        self,
        task_id:str,
        operation_id:str,
        execution_kind:str,
        *,
        predecessor_job_id:str|None=None,
    ):
        if execution_kind not in {"LOCAL","ROUTED"}:
            raise DurableError("INVALID_ARGUMENT","invalid task job execution kind")
        t=now_ms()
        try:
            with self.db.transaction() as con:
                old=con.execute(
                    "SELECT * FROM task_job_admissions WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if old is not None:
                    if old["task_id"]!=task_id or old["execution_kind"]!=execution_kind:
                        raise DurableError(
                            "TASK_JOB_LANE_STATE_MISMATCH",
                            "operation is already bound to a different task job lane",
                        )
                    return old,False

                active=con.execute(
                    "SELECT * FROM task_job_admissions "
                    "WHERE task_id=? AND state IN ('ADMITTING','ACTIVE') "
                    "ORDER BY sequence DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if active is not None:
                    raise DurableError(
                        "TASK_JOB_ADMISSION_CONFLICT",
                        "task lane already has an admitted non-terminal job",
                        predecessor_operation_id=active["operation_id"],
                        predecessor_job_id=active["job_id"],
                    )

                latest=con.execute(
                    "SELECT * FROM task_job_admissions WHERE task_id=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                seq=(int(latest["sequence"])+1) if latest is not None else 1
                pred=predecessor_job_id
                if pred is None and latest is not None:
                    pred=latest["job_id"]

                admission_id="adm_"+secrets.token_hex(16)
                con.execute(
                    "INSERT INTO task_job_admissions("
                    "admission_id,task_id,sequence,operation_id,execution_kind,"
                    "predecessor_job_id,state,created_at_ms,updated_at_ms"
                    ") VALUES(?,?,?,?,?,?,'ADMITTING',?,?)",
                    (
                        admission_id,task_id,seq,operation_id,execution_kind,
                        pred,t,t,
                    ),
                )
                row=con.execute(
                    "SELECT * FROM task_job_admissions WHERE admission_id=?",
                    (admission_id,),
                ).fetchone()
                return row,True
        except sqlite3.IntegrityError as exc:
            raise DurableError(
                "TASK_JOB_ADMISSION_CONFLICT",
                "concurrent task job admission lost the lane race",
            ) from exc

    def bind(self,operation_id:str,job_id:str):
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "task job operation has no admission",
                )
            if row["job_id"] is not None and row["job_id"]!=job_id:
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "task job admission is already bound to a different job",
                )
            if row["state"] in {"TERMINAL","ABORTED"}:
                if row["job_id"]==job_id:
                    return row
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "terminal admission cannot be rebound",
                )
            con.execute(
                "UPDATE task_job_admissions SET job_id=?,state='ACTIVE',updated_at_ms=? "
                "WHERE operation_id=?",
                (job_id,t,operation_id),
            )
            return con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()

    def abort_if_unbound(self,operation_id:str,reason:str):
        t=now_ms()
        evidence=json.dumps({"reason":reason},sort_keys=True,separators=(",",":"))
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return None
            if row["state"]=="ADMITTING" and row["job_id"] is None:
                con.execute(
                    "UPDATE task_job_admissions SET state='ABORTED',"
                    "terminal_evidence_json=?,updated_at_ms=?,terminal_at_ms=? "
                    "WHERE operation_id=?",
                    (evidence,t,t,operation_id),
                )
            return con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()

    def terminalize(self,operation_id:str,terminal_state:str,evidence:dict):
        if terminal_state not in TERMINAL_JOB_STATES:
            raise DurableError("INVALID_ARGUMENT","invalid terminal job state")
        if not isinstance(evidence,dict) or not evidence:
            raise DurableError(
                "PREDECESSOR_STATE_UNRESOLVED",
                "terminal evidence is required before releasing the task lane",
            )
        data=json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "task job operation has no admission",
                )
            if row["state"]=="TERMINAL":
                if row["terminal_state"]!=terminal_state:
                    raise DurableError(
                        "TASK_JOB_LANE_STATE_MISMATCH",
                        "terminal admission state conflicts with authoritative job state",
                    )
                return row
            if row["state"]=="ABORTED":
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "aborted admission cannot be terminalized as a job",
                )
            con.execute(
                "UPDATE task_job_admissions SET state='TERMINAL',terminal_state=?,"
                "terminal_evidence_json=?,terminal_at_ms=?,updated_at_ms=? "
                "WHERE operation_id=?",
                (terminal_state,data,t,t,operation_id),
            )
            return con.execute(
                "SELECT * FROM task_job_admissions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()

    @staticmethod
    def as_dict(row):
        if row is None:
            return None
        out={
            "admission_id":row["admission_id"],
            "task_id":row["task_id"],
            "sequence":int(row["sequence"]),
            "operation_id":row["operation_id"],
            "execution_kind":row["execution_kind"],
            "predecessor_job_id":row["predecessor_job_id"],
            "job_id":row["job_id"],
            "state":row["state"],
            "terminal_state":row["terminal_state"],
        }
        if row["terminal_evidence_json"]:
            out["terminal_evidence"]=json.loads(row["terminal_evidence_json"])
        return out
