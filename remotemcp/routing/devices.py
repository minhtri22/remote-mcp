from __future__ import annotations

import json
import secrets

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


class DeviceRepository:
    def __init__(self,db,owner_account_id:str,offline_after_seconds:int=60):
        self.db=db
        self.owner_account_id=owner_account_id
        self.offline_ms=int(offline_after_seconds)*1000

    def get(self,device_id:str):
        row=self.db.query_one(
            "SELECT * FROM devices WHERE device_id=? AND owner_account_id=?",
            (device_id,self.owner_account_id),
        )
        if row is None:
            raise DurableError("DEVICE_NOT_FOUND","device not found",device_id=device_id)
        return row

    def list(self)->list:
        return self.db.query_all(
            "SELECT * FROM devices WHERE owner_account_id=? ORDER BY paired_at_ms,device_id",
            (self.owner_account_id,),
        )

    def effective_state(self,row,at_ms:int|None=None)->str:
        """Return routing state without mutating SQLite.

        OFFLINE freshness is computed from last_seen_at_ms so request-time reads
        fail closed even before the background persistence sweep runs.
        """
        state=str(row["state"])
        if state!="ONLINE":
            return state
        t=now_ms() if at_ms is None else int(at_ms)
        if int(row["last_seen_at_ms"]) < t-self.offline_ms:
            return "OFFLINE"
        return "ONLINE"

    @staticmethod
    def _event(con,device_id,event_type,payload=None,command_id=None):
        con.execute(
            "INSERT INTO device_events(device_id,event_type,command_id,payload_json,created_at_ms) VALUES(?,?,?,?,?)",
            (device_id,event_type,command_id,json.dumps(payload or {},sort_keys=True),now_ms()),
        )

    def create_paired(self,con,device_name:str,public_key_b64:str,fingerprint:str,capabilities=None,platform=None):
        t=now_ms()
        device_id="dev_"+secrets.token_hex(16)
        con.execute(
            "INSERT INTO devices(device_id,owner_account_id,device_name,public_key_b64,key_fingerprint_sha256,"
            "state,route_generation,capabilities_json,platform_json,paired_at_ms,last_seen_at_ms) "
            "VALUES(?,?,?,?,?,'ONLINE',1,?,?,?,?)",
            (
                device_id,self.owner_account_id,device_name,public_key_b64,fingerprint,
                json.dumps(capabilities or {},sort_keys=True),
                json.dumps(platform or {},sort_keys=True),t,t,
            ),
        )
        self._event(con,device_id,"DEVICE_PAIRED",{"device_name":device_name})
        return con.execute("SELECT * FROM devices WHERE device_id=?",(device_id,)).fetchone()

    def touch_online_in_tx(self,con,device_id:str,t:int):
        row=con.execute("SELECT * FROM devices WHERE device_id=?",(device_id,)).fetchone()
        if row is None or row["owner_account_id"]!=self.owner_account_id:
            raise DurableError("DEVICE_NOT_FOUND","device not found")
        if row["state"]=="REVOKED":
            raise DurableError("DEVICE_REVOKED","device is revoked")
        old=row["state"]
        con.execute(
            "UPDATE devices SET state='ONLINE',last_seen_at_ms=? WHERE device_id=?",
            (t,device_id),
        )
        if old=="OFFLINE":
            self._event(con,device_id,"DEVICE_ONLINE",{})
        return con.execute("SELECT * FROM devices WHERE device_id=?",(device_id,)).fetchone()

    def sweep_offline(self)->int:
        """Persist effective OFFLINE state.

        This is intentionally a writer and is owned by the routing background
        loop. Request-time status/online checks must not call it.
        """
        t=now_ms(); cutoff=t-self.offline_ms
        changed=0
        with self.db.transaction() as con:
            rows=con.execute(
                "SELECT device_id FROM devices WHERE owner_account_id=? AND state='ONLINE' AND last_seen_at_ms<?",
                (self.owner_account_id,cutoff),
            ).fetchall()
            for row in rows:
                con.execute("UPDATE devices SET state='OFFLINE' WHERE device_id=?",(row["device_id"],))
                self._event(con,row["device_id"],"DEVICE_OFFLINE",{})
                changed+=1
        return changed

    def require_online(self,device_id:str):
        row=self.get(device_id)
        state=self.effective_state(row)
        if state=="REVOKED":
            raise DurableError("DEVICE_REVOKED","device is revoked")
        if state!="ONLINE":
            raise DurableError("DEVICE_OFFLINE","device is offline")
        return row

    def revoke(self,device_id:str,reason:str=""):
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM devices WHERE device_id=? AND owner_account_id=?",
                (device_id,self.owner_account_id),
            ).fetchone()
            if row is None:
                raise DurableError("DEVICE_NOT_FOUND","device not found")
            if row["state"]!="REVOKED":
                newgen=int(row["route_generation"])+1
                con.execute(
                    "UPDATE devices SET state='REVOKED',route_generation=?,revoked_at_ms=? WHERE device_id=?",
                    (newgen,t,device_id),
                )
                con.execute(
                    "UPDATE device_commands SET state='CANCELLED',error_code='DEVICE_REVOKED',finished_at_ms=?,updated_at_ms=? "
                    "WHERE device_id=? AND state='QUEUED'",
                    (t,t,device_id),
                )
                con.execute(
                    "UPDATE device_commands SET state='IN_DOUBT',error_code='DEVICE_REVOKED',finished_at_ms=?,updated_at_ms=? "
                    "WHERE device_id=? AND state='LEASED'",
                    (t,t,device_id),
                )
                self._event(con,device_id,"DEVICE_REVOKED",{"reason":reason,"route_generation":newgen})
            return con.execute("SELECT * FROM devices WHERE device_id=?",(device_id,)).fetchone()

    def status(self,device_id:str)->dict:
        row=self.get(device_id)
        state=self.effective_state(row)
        bound=self.db.query_one("SELECT COUNT(*) AS n FROM project_device_bindings WHERE device_id=?",(device_id,))
        active=self.db.query_one(
            "SELECT COUNT(*) AS n FROM device_commands WHERE device_id=? AND state IN ('QUEUED','LEASED')",
            (device_id,),
        )
        jobs=self.db.query_one(
            "SELECT COUNT(*) AS n FROM routed_jobs WHERE device_id=? AND last_known_state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
            (device_id,),
        )
        try:
            platform=json.loads(row["platform_json"] or "{}")
        except Exception:
            platform={}
        try:
            capabilities=json.loads(row["capabilities_json"] or "{}")
        except Exception:
            capabilities={}
        return {
            "device_id":row["device_id"],"device_name":row["device_name"],"state":state,
            "route_generation":int(row["route_generation"]),"last_seen_at_ms":int(row["last_seen_at_ms"]),
            "key_fingerprint_sha256":row["key_fingerprint_sha256"],
            "hostname":platform.get("hostname"),
            "platform":platform,
            "capabilities":capabilities,
            "bound_projects":int(bound["n"]),"active_commands":int(active["n"]),
            "active_routed_jobs":int(jobs["n"]),
        }
