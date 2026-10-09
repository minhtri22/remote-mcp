from __future__ import annotations

import json

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.routing.crypto import canonical_json
from .models import TERMINAL_NODE_COMMAND_STATES


class NodeCommandJournal:
    def __init__(self,db):
        self.db=db

    def get(self,command_id:str):
        row=self.db.query_one("SELECT * FROM node_commands WHERE command_id=?",(command_id,))
        if row is None:
            raise DurableError("NOT_FOUND","node command not found",command_id=command_id)
        return row

    def attest(self,command_id:str,expected_request_hash:str,expected_route_generation:int,
               expected_task_id:str)->dict:
        """Read-only exact node-journal receipt; never replays the target command."""
        import hashlib
        row=self.get(command_id)
        envelope=json.loads(row["payload_json"] or "{}")
        if (row["request_hash"]!=expected_request_hash
            or int(row["route_generation"])!=int(expected_route_generation)
            or envelope.get("command_id")!=command_id
            or envelope.get("task_id")!=expected_task_id):
            raise DurableError(
                "COMMAND_PROOF_IDENTITY_MISMATCH",
                "node journal command identity does not match frozen gateway evidence",
            )
        state=row["state"]
        terminal=state in TERMINAL_NODE_COMMAND_STATES
        return {
            "target_command_id":command_id,
            "request_hash":row["request_hash"],
            "route_generation":int(row["route_generation"]),
            "task_id":expected_task_id,
            "operation_id":row["operation_id"],
            "command_type":row["command_type"],
            "state":state,"terminal":terminal,
            "received_at_ms":row["received_at_ms"],
            "started_at_ms":row["started_at_ms"],
            "finished_at_ms":row["finished_at_ms"],
            "result_sha256":(
                hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
                if row["result_json"] is not None else None
            ),
            "error_code":row["error_code"],
            "error_sha256":(
                hashlib.sha256(row["error_json"].encode("utf-8")).hexdigest()
                if row["error_json"] is not None else None
            ),
            "read_only":True,
            "target_reexecuted":False,
        }

    def receive(self,envelope:dict):
        command_id=str(envelope["command_id"])
        rh=str(envelope["request_hash"])
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone()
            if row is not None:
                if row["request_hash"]!=rh:
                    raise DurableError("COMMAND_CONFLICT","command_id reused with different request_hash")
                return row,False
            con.execute(
                "INSERT INTO node_commands(command_id,route_generation,operation_id,request_hash,command_type,payload_json,state,received_at_ms) "
                "VALUES(?,?,?,?,?,?,'RECEIVED',?)",
                (
                    command_id,int(envelope["route_generation"]),envelope.get("operation_id"),rh,
                    str(envelope["command_type"]),canonical_json(envelope),now_ms(),
                ),
            )
            return con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone(),True

    def mark_executing(self,command_id:str):
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone()
            if row is None:raise DurableError("NOT_FOUND","node command missing")
            if row["state"]=="RECEIVED":
                con.execute("UPDATE node_commands SET state='EXECUTING',started_at_ms=? WHERE command_id=?",(t,command_id))
            return con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone()

    def terminal(self,command_id:str,state:str,*,result=None,error_code=None,error=None):
        if state not in {"SUCCEEDED","FAILED","IN_DOUBT"}:
            raise DurableError("INVALID_ARGUMENT","invalid node command terminal state")
        result_json=canonical_json(result) if result is not None else None
        error_json=canonical_json(error) if error is not None else None
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone()
            if row is None:raise DurableError("NOT_FOUND","node command missing")
            if row["state"] in TERMINAL_NODE_COMMAND_STATES:
                same=row["state"]==state and row["result_json"]==result_json and row["error_code"]==error_code and row["error_json"]==error_json
                if not same:raise DurableError("COMMAND_CONFLICT","conflicting node terminal result")
                return row
            con.execute(
                "UPDATE node_commands SET state=?,result_json=?,error_code=?,error_json=?,finished_at_ms=? WHERE command_id=?",
                (state,result_json,error_code,error_json,t,command_id),
            )
            return con.execute("SELECT * FROM node_commands WHERE command_id=?",(command_id,)).fetchone()

    @staticmethod
    def response(row)->dict:
        return {
            "request_hash":row["request_hash"],"state":row["state"],
            "result":json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code":row["error_code"],
            "error":json.loads(row["error_json"]) if row["error_json"] else None,
        }
