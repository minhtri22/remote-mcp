from __future__ import annotations

import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .models import ACTIVE_TASK_STATES,TERMINAL_TASK_STATES


class TaskRepository:
    def __init__(self,db):
        self.db=db

    def get(self,task_id:str):
        row=self.db.query_one("SELECT * FROM tasks WHERE task_id=?",(task_id,))
        if row is None:
            raise DurableError("TASK_NOT_FOUND","task not found",task_id=task_id)
        return row

    def create(self,project_id:str,title:str,base_ref:str,base_commit:str|None,branch_name:str|None,worktree_rel:str|None)->dict:
        task_id="tsk_"+secrets.token_hex(12)
        t=now_ms()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO tasks(task_id,project_id,title,state,base_ref,base_commit,branch_name,worktree_rel,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,'CREATED',?,?,?,?,?,?)",
                (task_id,project_id,title,base_ref,base_commit,branch_name,worktree_rel,t,t),
            )
            con.execute("UPDATE tasks SET state='READY',updated_at_ms=? WHERE task_id=?",(t,task_id))
            self._event(con,task_id,None,None,None,"TASK_CREATED",{"title":title})
            self._event(con,task_id,None,None,None,"TASK_READY",{})
        return self.status(task_id)

    def status(self,task_id:str)->dict:
        row=self.get(task_id)
        lease=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task_id,))
        return {
            "task_id":row["task_id"],"project_id":row["project_id"],"title":row["title"],
            "state":row["state"],"base_ref":row["base_ref"],"base_commit":row["base_commit"],
            "branch_name":row["branch_name"],"worktree_rel":row["worktree_rel"],
            "owner_agent_id":row["owner_agent_id"],"owner_session_id":row["owner_session_id"],
            "lease_epoch":row["lease_epoch"],"cleanup_pending":bool(row["cleanup_pending"]),
            "lease_expires_at_ms":lease["expires_at_ms"] if lease else None,
            "checkpoint":json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None,
        }

    @staticmethod
    def _event(con,task_id,operation_id,agent_id,session_id,event_type,payload):
        con.execute(
            "INSERT INTO task_events(task_id,operation_id,agent_id,session_id,event_type,payload_json,created_at_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (task_id,operation_id,agent_id,session_id,event_type,json.dumps(payload,sort_keys=True),now_ms()),
        )

    def checkpoint(self,operation_id:str,task_id:str,agent_id:str,session_id:str,lease_epoch:int,summary:str,metadata:dict)->dict:
        t=now_ms()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO task_checkpoints(task_id,operation_id,agent_id,session_id,lease_epoch,summary,metadata_json,created_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id,operation_id,agent_id,session_id,lease_epoch,summary,json.dumps(metadata,sort_keys=True),t),
            )
            payload={"summary":summary,"metadata":metadata,"lease_epoch":lease_epoch}
            con.execute(
                "UPDATE tasks SET checkpoint_json=?,updated_at_ms=? WHERE task_id=?",
                (json.dumps(payload,sort_keys=True),t,task_id),
            )
            self._event(con,task_id,operation_id,agent_id,session_id,"CHECKPOINT_CREATED",payload)
        return {"task_id":task_id,"summary":summary,"metadata":metadata,"lease_epoch":lease_epoch}

    def set_ready(self,task_id:str,reason:str)->dict:
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if row is None: raise DurableError("TASK_NOT_FOUND","task not found")
            if row["state"]!="BLOCKED":
                raise DurableError("TASK_STATE_CONFLICT","only BLOCKED task can become READY")
            con.execute("UPDATE tasks SET state='READY',updated_at_ms=? WHERE task_id=?",(t,task_id))
            self._event(con,task_id,None,None,None,"TASK_READY",{"reason":reason})
        return self.status(task_id)

    def nonterminal_jobs(self,task_id:str)->int:
        row=self.db.query_one(
            "SELECT COUNT(*) AS n FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
            "WHERE o.task_id=? AND j.state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
            (task_id,),
        )
        return int(row["n"]) if row else 0

    def jobs(self,task_id:str)->list[dict]:
        rows=self.db.query_all(
            "SELECT j.* FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE o.task_id=? ORDER BY j.created_at_ms",
            (task_id,),
        )
        return [
            {"job_id":r["job_id"],"state":r["state"],"created_at_ms":r["created_at_ms"],
             "started_at_ms":r["started_at_ms"],"finished_at_ms":r["finished_at_ms"],
             "exit_code":r["exit_code"]}
            for r in rows
        ]