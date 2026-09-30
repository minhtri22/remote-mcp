from __future__ import annotations

import hashlib
import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .models import ACTIVE_TASK_STATES


class LeaseRepository:
    def __init__(self,db,tokens,ttl_seconds:int=120):
        self.db=db
        self.tokens=tokens
        self.ttl_ms=ttl_seconds*1000

    def _lease_row(self,con,task_id:str):
        return con.execute("SELECT * FROM task_leases WHERE task_id=?",(task_id,)).fetchone()

    def validate(self,task_id:str,token:str,epoch:int):
        t=now_ms()
        row=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task_id,))
        if row is None: raise DurableError("LEASE_REQUIRED","task lease required")
        if int(row["lease_epoch"])!=int(epoch) or row["expires_at_ms"]<=t:
            raise DurableError("LEASE_STALE","task lease stale")
        if not self.tokens.verify(token,row["lease_token_hash"]):
            raise DurableError("LEASE_STALE","task lease token invalid")
        return row

    def claim_phase1(self,task_id:str,agent_id:str,session_id:str)->dict:
        t=now_ms(); exp=t+self.ttl_ms
        with self.db.transaction() as con:
            task=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if task is None: raise DurableError("TASK_NOT_FOUND","task not found")
            if task["state"] not in ("READY","RECOVERABLE"):
                raise DurableError("TASK_STATE_CONFLICT","task not claimable",state=task["state"])
            project=con.execute("SELECT * FROM projects WHERE project_id=?",(task["project_id"],)).fetchone()
            active=con.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE project_id=? AND state IN ('CLAIMED','RUNNING')",
                (task["project_id"],),
            ).fetchone()["n"]
            if int(active)>=int(project["max_active_tasks"]):
                raise DurableError("PROJECT_CONCURRENCY_LIMIT","project active task limit reached")
            epoch=int(task["lease_epoch"])+1
            nonce=self.tokens.new_nonce()
            token=self.tokens.derive(task_id,epoch,agent_id,session_id,nonce)
            token_hash=self.tokens.digest(token)
            con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
            con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
            con.execute(
                "INSERT INTO task_leases(task_id,agent_id,session_id,lease_epoch,lease_token_nonce,lease_token_hash,acquired_at_ms,renewed_at_ms,expires_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id,agent_id,session_id,epoch,nonce,token_hash,t,t,exp),
            )
            con.execute(
                "UPDATE tasks SET state='CLAIMED',owner_agent_id=?,owner_session_id=?,lease_epoch=?,claimed_at_ms=?,updated_at_ms=? WHERE task_id=?",
                (agent_id,session_id,epoch,t,t,task_id),
            )
            con.execute(
                "INSERT INTO task_events(task_id,agent_id,session_id,event_type,payload_json,created_at_ms) VALUES(?,?,?,'TASK_CLAIMED',?,?)",
                (task_id,agent_id,session_id,'{"lease_epoch":%d}'%epoch,t),
            )
        return {"task_id":task_id,"agent_id":agent_id,"session_id":session_id,"lease_epoch":epoch,"lease_token":token,"expires_at_ms":exp}

    def current_token(self,task_id:str,agent_id:str,session_id:str,epoch:int)->str:
        row=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task_id,))
        if row is None or int(row["lease_epoch"])!=int(epoch) or row["agent_id"]!=agent_id or row["session_id"]!=session_id:
            raise DurableError("LEASE_STALE","claim replay lease no longer current")
        return self.tokens.derive(task_id,epoch,agent_id,session_id,row["lease_token_nonce"])

    def finalize_running(self,task_id:str,agent_id:str,session_id:str,epoch:int)->None:
        t=now_ms()
        with self.db.transaction() as con:
            lease=self._lease_row(con,task_id)
            task=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if lease is None or task is None or int(lease["lease_epoch"])!=int(epoch):
                raise DurableError("LEASE_STALE","claim lease vanished")
            if lease["agent_id"]!=agent_id or lease["session_id"]!=session_id or lease["expires_at_ms"]<=t:
                raise DurableError("LEASE_STALE","claim lease no longer current")
            if task["state"]!="CLAIMED":
                raise DurableError("TASK_STATE_CONFLICT","task no longer CLAIMED")
            con.execute("UPDATE tasks SET state='RUNNING',updated_at_ms=? WHERE task_id=?",(t,task_id))
            con.execute(
                "INSERT INTO task_events(task_id,agent_id,session_id,event_type,payload_json,created_at_ms) VALUES(?,?,?,'TASK_RUNNING',?,?)",
                (task_id,agent_id,session_id,'{"lease_epoch":%d}'%epoch,t),
            )

    def release(self,task_id:str,token:str,epoch:int,target_state:str,event_type:str,reason:str="")->None:
        lease=self.validate(task_id,token,epoch)
        t=now_ms()
        with self.db.transaction() as con:
            task=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if task is None: raise DurableError("TASK_NOT_FOUND","task not found")
            con.execute("DELETE FROM path_leases WHERE task_id=?",(task_id,))
            con.execute("DELETE FROM task_leases WHERE task_id=?",(task_id,))
            con.execute(
                "UPDATE tasks SET state=?,owner_agent_id=NULL,owner_session_id=NULL,updated_at_ms=? WHERE task_id=?",
                (target_state,t,task_id),
            )
            con.execute(
                "INSERT INTO task_events(task_id,agent_id,session_id,event_type,payload_json,created_at_ms) VALUES(?,?,?,?,?,?)",
                (task_id,lease["agent_id"],lease["session_id"],event_type,json.dumps({"reason":reason},sort_keys=True),t),
            )

    @staticmethod
    def _parts(path_rel:str)->tuple[str,...]:
        p=path_rel.replace("\\","/").strip("/")
        return tuple(x for x in p.split("/") if x and x!=".")

    @classmethod
    def conflicts(cls,a_path:str,a_scope:str,b_path:str,b_scope:str)->bool:
        a=cls._parts(a_path); b=cls._parts(b_path)
        if a_scope=="FILE" and b_scope=="FILE":
            return a==b
        if a_scope=="TREE":
            return a==b or (len(a)<=len(b) and b[:len(a)]==a) or (b_scope=="TREE" and len(b)<len(a) and a[:len(b)]==b)
        if b_scope=="TREE":
            return a==b or (len(b)<=len(a) and a[:len(b)]==b)
        return False

    def acquire_path(self,task_id:str,token:str,epoch:int,path_rel:str,scope:str)->dict:
        lease=self.validate(task_id,token,epoch)
        if scope not in ("FILE","TREE"):
            raise DurableError("INVALID_ARGUMENT","scope must be FILE or TREE")
        norm="/".join(self._parts(path_rel)) or "."
        t=now_ms()
        with self.db.transaction() as con:
            task=con.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            for row in con.execute(
                "SELECT * FROM path_leases WHERE project_id=? AND expires_at_ms>?",
                (task["project_id"],t),
            ).fetchall():
                if self.conflicts(norm,scope,row["path_rel"],row["scope"]):
                    raise DurableError("LEASE_CONFLICT","overlapping path lease",path=norm)
            lease_id="pls_"+secrets.token_hex(12)
            con.execute(
                "INSERT INTO path_leases(path_lease_id,project_id,task_id,agent_id,session_id,lease_epoch,path_rel,scope,mode,acquired_at_ms,renewed_at_ms,expires_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?, 'WRITE_EXCLUSIVE',?,?,?)",
                (lease_id,task["project_id"],task_id,lease["agent_id"],lease["session_id"],epoch,norm,scope,t,t,lease["expires_at_ms"]),
            )
        return {"path_lease_id":lease_id,"task_id":task_id,"path":norm,"scope":scope,"expires_at_ms":lease["expires_at_ms"]}

    def release_path(self,task_id:str,token:str,epoch:int,path_lease_id:str)->dict:
        self.validate(task_id,token,epoch)
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM path_leases WHERE path_lease_id=? AND task_id=?",(path_lease_id,task_id)).fetchone()
            if row is None: raise DurableError("NOT_FOUND","path lease not found")
            con.execute("DELETE FROM path_leases WHERE path_lease_id=?",(path_lease_id,))
        return {"path_lease_id":path_lease_id,"released":True}

    def has_covering(self,task_id:str,path_rel:str,require_root_tree:bool=False)->bool:
        t=now_ms()
        rows=self.db.query_all("SELECT * FROM path_leases WHERE task_id=? AND expires_at_ms>?",(task_id,t))
        norm="/".join(self._parts(path_rel)) or "."
        for row in rows:
            if require_root_tree:
                if row["scope"]=="TREE" and row["path_rel"]==".":
                    return True
            elif row["scope"]=="TREE" and self.conflicts(row["path_rel"],"TREE",norm,"FILE"):
                return True
            elif row["scope"]=="FILE" and row["path_rel"]==norm:
                return True
        return False