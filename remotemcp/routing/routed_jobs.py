from __future__ import annotations

import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}


class RoutedJobRepository:
    def __init__(self,db,*,isolation=None):
        self.db=db
        # Default disabled. Signed isolation policy is injected only when
        # separately authorized in the gateway.
        self.isolation=isolation

    def _reject_protected(self,*,proxy_job_id=None,operation_id=None):
        policy=self.isolation
        if policy is None:
            return
        protected_operation=(
            operation_id is not None
            and any(alias==operation_id for alias, _ in policy.aliases)
        )
        if protected_operation or proxy_job_id in policy.proxys:
            raise DurableError(
                "HISTORICAL_COMMAND_PROTECTED",
                "historical routed job identity cannot be reused or updated",
            )

    def get(self,proxy_job_id:str):
        row=self.db.query_one("SELECT * FROM routed_jobs WHERE proxy_job_id=?",(proxy_job_id,))
        if row is None:
            raise DurableError("NOT_FOUND","routed job not found",proxy_job_id=proxy_job_id)
        return row

    def by_operation(self,operation_id:str):
        return self.db.query_one("SELECT * FROM routed_jobs WHERE operation_id=?",(operation_id,))

    def by_execution_key(self,execution_key:str):
        if not execution_key:
            return None
        return self.db.query_one(
            "SELECT * FROM routed_jobs WHERE execution_key=?",(execution_key,)
        )

    def create(
        self,con,operation_id:str,task_id:str,project_id:str,device_id:str,
        *,execution_key:str|None=None,argv_sha256:str|None=None,cwd:str|None=None,
    ):
        self._reject_protected(operation_id=operation_id)
        old=con.execute("SELECT * FROM routed_jobs WHERE operation_id=?",(operation_id,)).fetchone()
        if old:
            self._reject_protected(proxy_job_id=old["proxy_job_id"])
            return old,False
        proxy="rjob_"+secrets.token_hex(16);t=now_ms()
        if execution_key:
            existing=con.execute(
                "SELECT * FROM routed_jobs WHERE execution_key=?",(execution_key,)
            ).fetchone()
            if existing:
                self._reject_protected(proxy_job_id=existing["proxy_job_id"])
                return existing,False
        con.execute(
            "INSERT INTO routed_jobs("
            "proxy_job_id,operation_id,task_id,project_id,device_id,"
            "execution_key,argv_sha256,cwd,last_known_state,last_seen_at_ms,created_at_ms,updated_at_ms"
            ") VALUES(?,?,?,?,?,?,?,?, 'QUEUED',?,?,?)",
            (
                proxy,operation_id,task_id,project_id,device_id,
                execution_key,argv_sha256,cwd,t,t,t,
            ),
        )
        return con.execute("SELECT * FROM routed_jobs WHERE proxy_job_id=?",(proxy,)).fetchone(),True

    def update(self,proxy_job_id:str,*,node_job_id=None,state=None,terminal_result=None):
        self._reject_protected(proxy_job_id=proxy_job_id)
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
            "execution_key":row["execution_key"] if "execution_key" in row.keys() else None,
            "argv_sha256":row["argv_sha256"] if "argv_sha256" in row.keys() else None,
            "cwd":row["cwd"] if "cwd" in row.keys() else None,
            "last_seen_at_ms":int(row["last_seen_at_ms"]),"created_at_ms":int(row["created_at_ms"]),
        }
        if row["terminal_result_json"]:
            out["terminal_result"]=json.loads(row["terminal_result_json"])
        if device_state is not None: out["device_state"]=device_state
        return out