from __future__ import annotations

import asyncio
import hashlib
import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .crypto import canonical_json
from .models import ALLOWED_COMMANDS,CommandState,TERMINAL_COMMAND_STATES


class CommandRepository:
    def __init__(self,config,db,devices):
        self.config=config; self.db=db; self.devices=devices

    @staticmethod
    def request_hash(device_id:str,generation:int,command_type:str,project_id:str|None,task_id:str|None,payload:dict)->str:
        data={
            "device_id":device_id,"route_generation":int(generation),
            "command_type":command_type,"project_id":project_id,"task_id":task_id,
            "payload":payload,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def get(self,command_id:str):
        row=self.db.query_one("SELECT * FROM device_commands WHERE command_id=?",(command_id,))
        if row is None:
            raise DurableError("NOT_FOUND","device command not found",command_id=command_id)
        return row

    def create_in_tx(
        self,con,device_id:str,command_type:str,payload:dict,*,project_id:str|None=None,
        task_id:str|None=None,operation_id:str|None=None,operation_step:int=0,
        expires_at_ms:int|None=None,route_generation:int|None=None,
    ):
        if command_type not in ALLOWED_COMMANDS:
            raise DurableError("INVALID_ARGUMENT","unsupported routed command",command_type=command_type)
        if route_generation is None:
            dev=con.execute("SELECT * FROM devices WHERE device_id=?",(device_id,)).fetchone()
            if dev is None:raise DurableError("DEVICE_NOT_FOUND","device not found")
            if dev["state"]!="ONLINE":raise DurableError("DEVICE_OFFLINE","device is not online")
            route_generation=int(dev["route_generation"])
        rh=self.request_hash(device_id,int(route_generation),command_type,project_id,task_id,payload)
        if expires_at_ms is None:
            expires_at_ms=now_ms()+self.config.mutation_ttl_seconds*1000
        t=now_ms()
        if operation_id:
            old=con.execute(
                "SELECT * FROM device_commands WHERE operation_id=? AND operation_step=?",
                (operation_id,int(operation_step)),
            ).fetchone()
            if old:
                if old["request_hash"]!=rh:
                    raise DurableError("COMMAND_CONFLICT","operation step already has different routed command")
                if (
                    old["state"]=="CANCELLED"
                    and old["error_code"]=="DEVICE_COMMAND_EXPIRED"
                ):
                    con.execute(
                        "UPDATE device_commands SET state='QUEUED',result_json=NULL,error_code=NULL,error_json=NULL,"
                        "lease_expires_at_ms=NULL,finished_at_ms=NULL,command_expires_at_ms=?,updated_at_ms=? "
                        "WHERE command_id=? AND state='CANCELLED' AND error_code='DEVICE_COMMAND_EXPIRED'",
                        (int(expires_at_ms),t,old["command_id"]),
                    )
                    revived=con.execute(
                        "SELECT * FROM device_commands WHERE command_id=?",
                        (old["command_id"],),
                    ).fetchone()
                    self.devices._event(
                        con,device_id,"COMMAND_REQUEUED",
                        {"command_type":command_type,"reason":"DEVICE_COMMAND_EXPIRED"},
                        old["command_id"],
                    )
                    return revived,True
                return old,False
        command_id="cmd_"+secrets.token_hex(16)
        con.execute(
            "INSERT INTO device_commands(command_id,device_id,route_generation,operation_id,operation_step,"
            "project_id,task_id,command_type,request_hash,payload_json,state,command_expires_at_ms,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'QUEUED',?,?,?)",
            (
                command_id,device_id,int(route_generation),operation_id,int(operation_step),
                project_id,task_id,command_type,rh,canonical_json(payload),
                int(expires_at_ms),t,t,
            ),
        )
        self.devices._event(con,device_id,"COMMAND_QUEUED",{"command_type":command_type},command_id)
        return con.execute("SELECT * FROM device_commands WHERE command_id=?",(command_id,)).fetchone(),True

    def create(
        self,device_id:str,command_type:str,payload:dict,*,project_id:str|None=None,
        task_id:str|None=None,operation_id:str|None=None,operation_step:int=0,
        expires_at_ms:int|None=None,
    ):
        dev=self.devices.require_online(device_id)
        with self.db.transaction() as con:
            return self.create_in_tx(
                con,device_id,command_type,payload,project_id=project_id,task_id=task_id,
                operation_id=operation_id,operation_step=operation_step,expires_at_ms=expires_at_ms,
                route_generation=int(dev["route_generation"]),
            )

    def poll(self,device_id:str,route_generation:int):
        dev=self.devices.require_online(device_id)
        if int(dev["route_generation"])!=int(route_generation):
            raise DurableError("DEVICE_ROUTE_GENERATION_MISMATCH","route generation mismatch")
        t=now_ms()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE device_commands SET state='CANCELLED',error_code='DEVICE_COMMAND_EXPIRED',finished_at_ms=?,updated_at_ms=? "
                "WHERE device_id=? AND state='QUEUED' AND command_expires_at_ms<=?",
                (t,t,device_id,t),
            )
            con.execute(
                "UPDATE device_commands SET state='QUEUED',lease_expires_at_ms=NULL,updated_at_ms=? "
                "WHERE device_id=? AND state='LEASED' AND lease_expires_at_ms<=? AND command_expires_at_ms>? AND route_generation=?",
                (t,device_id,t,t,int(route_generation)),
            )
            row=con.execute(
                "SELECT * FROM device_commands WHERE device_id=? AND route_generation=? AND state='QUEUED' "
                "AND command_expires_at_ms>? "
                "ORDER BY CASE command_type "
                "WHEN 'NODE_RESTART' THEN 0 "
                "WHEN 'JOB_CANCEL' THEN 0 "
                "WHEN 'TASK_BASE_RESOLVE' THEN 1 "
                "WHEN 'TASK_WORKTREE_ENSURE' THEN 1 "
                "WHEN 'PROJECT_PROBE' THEN 1 "
                "WHEN 'PROJECT_BIND' THEN 1 "
                "WHEN 'JOB_SUBMIT' THEN 2 "
                "ELSE 3 END, created_at_ms,command_id LIMIT 1",
                (device_id,int(route_generation),t),
            ).fetchone()
            if row is None:
                return None
            lease=t+self.config.command_lease_seconds*1000
            con.execute(
                "UPDATE device_commands SET state='LEASED',delivery_attempt=delivery_attempt+1,lease_expires_at_ms=?,updated_at_ms=? "
                "WHERE command_id=? AND state='QUEUED'",
                (lease,t,row["command_id"]),
            )
            return con.execute("SELECT * FROM device_commands WHERE command_id=?",(row["command_id"],)).fetchone()

    @staticmethod
    def envelope(row)->dict:
        return {
            "command_id":row["command_id"],"device_id":row["device_id"],
            "route_generation":int(row["route_generation"]),"operation_id":row["operation_id"],
            "project_id":row["project_id"],"task_id":row["task_id"],
            "command_type":row["command_type"],"request_hash":row["request_hash"],
            "payload":json.loads(row["payload_json"]),"issued_at_ms":int(row["created_at_ms"]),
            "command_expires_at_ms":int(row["command_expires_at_ms"]),
        }

    def commit_result(self,device_id:str,route_generation:int,command_id:str,payload:dict):
        row=self.get(command_id)
        if row["device_id"]!=device_id:
            raise DurableError("FORBIDDEN","command belongs to another device")
        if int(row["route_generation"])!=int(route_generation):
            raise DurableError("DEVICE_ROUTE_GENERATION_MISMATCH","command generation mismatch")
        if str(payload.get("request_hash",""))!=row["request_hash"]:
            raise DurableError("COMMAND_CONFLICT","command request hash mismatch")
        state=str(payload.get("state",""))
        if state not in {"SUCCEEDED","FAILED","IN_DOUBT"}:
            raise DurableError("INVALID_ARGUMENT","invalid command result state")
        result=payload.get("result")
        error_code=payload.get("error_code")
        error=payload.get("error")
        result_json=canonical_json(result) if result is not None else None
        error_json=canonical_json(error) if error is not None else None
        if row["state"] in TERMINAL_COMMAND_STATES:
            same=(
                row["state"]==state and row["result_json"]==result_json and
                row["error_code"]==error_code and row["error_json"]==error_json
            )
            if not same:
                raise DurableError("COMMAND_CONFLICT","conflicting terminal command result")
            return row
        t=now_ms()
        with self.db.transaction() as con:
            current=con.execute("SELECT * FROM device_commands WHERE command_id=?",(command_id,)).fetchone()
            if current["state"] in TERMINAL_COMMAND_STATES:
                return current
            con.execute(
                "UPDATE device_commands SET state=?,result_json=?,error_code=?,error_json=?,finished_at_ms=?,updated_at_ms=? WHERE command_id=?",
                (state,result_json,error_code,error_json,t,t,command_id),
            )
            self.devices._event(con,device_id,"COMMAND_TERMINAL",{"state":state},command_id)
            return con.execute("SELECT * FROM device_commands WHERE command_id=?",(command_id,)).fetchone()

    async def wait(self,command_id:str,timeout_seconds:int|None=None):
        timeout=self.config.gateway_wait_seconds if timeout_seconds is None else max(0,min(int(timeout_seconds),self.config.gateway_wait_seconds))
        deadline=asyncio.get_running_loop().time()+timeout
        while True:
            row=self.get(command_id)
            if row["state"] in TERMINAL_COMMAND_STATES:
                return row
            if timeout==0 or asyncio.get_running_loop().time()>=deadline:
                raise DurableError("DEVICE_COMMAND_PENDING","routed command is still pending",command_id=command_id)
            await asyncio.sleep(0.05)

    def result_value(self,row):
        if row["state"]=="SUCCEEDED":
            return json.loads(row["result_json"]) if row["result_json"] else {}
        if row["state"]=="IN_DOUBT":
            raise DurableError("OPERATION_IN_DOUBT","remote command is in doubt",command_id=row["command_id"])
        if row["state"]=="CANCELLED":
            raise DurableError(row["error_code"] or "DEVICE_COMMAND_EXPIRED","remote command cancelled")
        raise DurableError(row["error_code"] or "INVALID_ARGUMENT","remote command failed",command_id=row["command_id"])