from __future__ import annotations

import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .models import SessionState


class AgentRepository:
    def __init__(self,db,owner_account_id:str,ttl_seconds:int=120):
        self.db=db
        self.owner_account_id=owner_account_id
        self.ttl_ms=ttl_seconds*1000

    def ensure_owner(self,con)->None:
        con.execute(
            "INSERT OR IGNORE INTO owner_accounts(owner_account_id,created_at_ms) VALUES(?,?)",
            (self.owner_account_id,now_ms()),
        )

    def register(self,agent_name:str,client_instance_id:str,auth_client_id:str,capabilities:list[str]):
        if not client_instance_id:
            raise DurableError("INVALID_ARGUMENT","client_instance_id is required")
        t=now_ms()
        with self.db.transaction() as con:
            self.ensure_owner(con)
            row=con.execute(
                "SELECT * FROM agents WHERE owner_account_id=? AND client_instance_id=?",
                (self.owner_account_id,client_instance_id),
            ).fetchone()
            if row is None:
                agent_id="agt_"+secrets.token_hex(16)
                con.execute(
                    "INSERT INTO agents(agent_id,owner_account_id,client_instance_id,display_name,capabilities_json,created_at_ms,updated_at_ms) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (agent_id,self.owner_account_id,client_instance_id,agent_name,json.dumps(capabilities),t,t),
                )
            else:
                agent_id=row["agent_id"]
                con.execute(
                    "UPDATE agents SET display_name=?,capabilities_json=?,updated_at_ms=? WHERE agent_id=?",
                    (agent_name,json.dumps(capabilities),t,agent_id),
                )
            session_id="ses_"+secrets.token_hex(16)
            con.execute(
                "INSERT INTO agent_sessions(session_id,agent_id,auth_client_id,state,heartbeat_seq,created_at_ms,last_heartbeat_at_ms) "
                "VALUES(?,?,?,'ACTIVE',0,?,?)",
                (session_id,agent_id,auth_client_id,t,t),
            )
            return {
                "owner_account_id":self.owner_account_id,
                "agent_id":agent_id,
                "session_id":session_id,
                "auth_client_id":auth_client_id,
                "state":"ACTIVE",
            }

    def session(self,agent_id:str,session_id:str):
        row=self.db.query_one(
            "SELECT s.*,a.owner_account_id FROM agent_sessions s JOIN agents a ON a.agent_id=s.agent_id "
            "WHERE s.session_id=? AND s.agent_id=?",
            (session_id,agent_id),
        )
        if row is None:
            raise DurableError("SESSION_NOT_FOUND","session not found")
        if row["owner_account_id"]!=self.owner_account_id:
            raise DurableError("FORBIDDEN","session belongs to another owner")
        return row

    def validate_active(self,agent_id:str,session_id:str):
        row=self.session(agent_id,session_id)
        if row["state"]!=SessionState.ACTIVE.value:
            raise DurableError("SESSION_STALE","session is not active")
        return row

    def heartbeat(self,agent_id:str,session_id:str,seq:int)->dict:
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT s.*,a.owner_account_id FROM agent_sessions s JOIN agents a ON a.agent_id=s.agent_id "
                "WHERE s.session_id=? AND s.agent_id=?",
                (session_id,agent_id),
            ).fetchone()
            if row is None:
                raise DurableError("SESSION_NOT_FOUND","session not found")
            if row["owner_account_id"]!=self.owner_account_id:
                raise DurableError("FORBIDDEN","session belongs to another owner")
            if row["state"]!=SessionState.ACTIVE.value:
                raise DurableError("SESSION_STALE","session is not active")
            old=int(row["heartbeat_seq"])
            if seq<old:
                raise DurableError("HEARTBEAT_STALE","heartbeat_seq moved backwards")
            if seq==old:
                return {"agent_id":agent_id,"session_id":session_id,"heartbeat_seq":old,"renewed":False}
            con.execute(
                "UPDATE agent_sessions SET heartbeat_seq=?,last_heartbeat_at_ms=? WHERE session_id=?",
                (seq,t,session_id),
            )
            new_exp=t+self.ttl_ms
            con.execute(
                "UPDATE task_leases SET renewed_at_ms=?,expires_at_ms=? "
                "WHERE session_id=? AND expires_at_ms>?",
                (t,new_exp,session_id,t),
            )
            con.execute(
                "UPDATE path_leases SET renewed_at_ms=?,expires_at_ms=? "
                "WHERE session_id=? AND expires_at_ms>?",
                (t,new_exp,session_id,t),
            )
            return {"agent_id":agent_id,"session_id":session_id,"heartbeat_seq":seq,"renewed":True,"expires_at_ms":new_exp}

    def close(self,agent_id:str,session_id:str)->dict:
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT s.*,a.owner_account_id FROM agent_sessions s JOIN agents a ON a.agent_id=s.agent_id "
                "WHERE s.session_id=? AND s.agent_id=?",(session_id,agent_id)
            ).fetchone()
            if row is None:
                raise DurableError("SESSION_NOT_FOUND","session not found")
            if row["owner_account_id"]!=self.owner_account_id:
                raise DurableError("FORBIDDEN","wrong owner")
            con.execute(
                "UPDATE agent_sessions SET state='CLOSED',closed_at_ms=? WHERE session_id=?",
                (t,session_id),
            )
        return {"agent_id":agent_id,"session_id":session_id,"state":"CLOSED"}