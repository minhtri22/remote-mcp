from __future__ import annotations

import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}


class RoutedJobRepository:
    def __init__(self,db):
        self.db=db

    def get(self,proxy_job_id:str):
        row=self.db.query_one("SELECT * FROM routed_jobs WHERE proxy_job_id=?",(proxy_job_id,))
        if row is None:
            raise DurableError("NOT_FOUND","routed job not found",proxy_job_id=proxy_job_id)
        return row

    def by_operation(self,operation_id:str):
        return self.db.query_one("SELECT * FROM routed_jobs WHERE operation_id=?",(operation_id,))

    def create(self,con,operation_id:str,task_id:str,project_id:str,device_id:str):
        old=con.execute("SELECT * FROM routed_jobs WHERE operation_id=?",(operation_id,)).fetchone()
        if old:return old,False
        proxy="rjob_"+secrets.token_hex(16);t=now_ms()
        con.execute(
            "INSERT INTO routed_jobs(proxy_job_id,operation_id,task_id,project_id,device_id,last_known_state,last_seen_at_ms,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,?,?,'QUEUED',?,?,?)",
            (proxy,operation_id,task_id,project_id,device_id,t,t,t),
        )
        return con.execute("SELECT * FROM routed_jobs WHERE proxy_job_id=?",(proxy,)).fetchone(),True

    def update(self,proxy_job_id:str,*,node_job_id=None,state=None,terminal_result=None):
        t=now_ms()
        fields=["last_seen_at_ms=?","updated_at_ms=?"];vals=[t,t]
        if node_job_id is not None: fields.append("node_job_id=?");vals.append(node_job_id)
        if state is not None:
            fields.append("last_known_state=?");vals.append(state)
            if state in TERMINAL: fields.append("terminal_at_ms=?");vals.append(t)
        if terminal_result is not None:
            fields.append("terminal_result_json=?");vals.append(json.dumps(terminal_result,ensure_ascii=False,sort_keys=True,separators=(",",":")))
        vals.append(proxy_job_id)
        with self.db.transaction() as con:
            con.execute(f"UPDATE routed_jobs SET {','.join(fields)} WHERE proxy_job_id=?",tuple(vals))
        return self.get(proxy_job_id)

    def list_task_rows(self,task_id:str):
        return self.db.query_all(
            "SELECT * FROM routed_jobs WHERE task_id=? ORDER BY created_at_ms,proxy_job_id",
            (task_id,),
        )

    def list_task(self,task_id:str)->list[dict]:
        return [self.as_dict(r) for r in self.list_task_rows(task_id)]

    @staticmethod
    def as_dict(row,device_state=None):
        out={
            "proxy_job_id":row["proxy_job_id"],"job_id":row["proxy_job_id"],
            "task_id":row["task_id"],"project_id":row["project_id"],"device_id":row["device_id"],
            "node_job_id":row["node_job_id"],"state":row["last_known_state"],
            "last_seen_at_ms":int(row["last_seen_at_ms"]),"created_at_ms":int(row["created_at_ms"]),
        }
        if row["terminal_result_json"]:
            out["terminal_result"]=json.loads(row["terminal_result_json"])
        if device_state is not None: out["device_state"]=device_state
        return out