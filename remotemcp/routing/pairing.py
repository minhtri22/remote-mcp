from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import OperationState, now_ms
from remotemcp.durable.process import atomic_write_bytes
from .crypto import b64u,canonical_pair,compare_pair_code,public_key_fingerprint,verify_ed25519
from .join_script import render_windows_join_script


class PairingService:
    def __init__(self,config,db,operations,devices,owner_account_id:str):
        self.config=config; self.db=db; self.operations=operations; self.devices=devices
        self.owner_account_id=owner_account_id
        self.key=self._load_or_create_key()

    def _load_or_create_key(self)->bytes:
        path=self.config.pairing_key_path
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            data=path.read_bytes()
            if len(data)!=32:
                raise DurableError("STARTUP_FATAL_PAIRING_KEY_MISSING","device-pairing.key must be exactly 32 bytes")
            return data
        row=self.db.query_one(
            "SELECT COUNT(*) AS n FROM device_pairings WHERE used_at_ms IS NULL AND expires_at_ms>?",
            (now_ms(),),
        )
        if row and int(row["n"]):
            raise DurableError("STARTUP_FATAL_PAIRING_KEY_MISSING","active pairing rows exist but pairing key is missing")
        data=secrets.token_bytes(32)
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
        fd=os.open(path,flags,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        return data

    def _code(self,pairing_id:str,name:str,nonce:str)->str:
        msg=f"{pairing_id}|{self.owner_account_id}|{name}|{nonce}".encode()
        mac=hmac.new(self.key,msg,hashlib.sha256).digest()
        return f"pc1_{nonce}_{b64u(mac)}"

    def _join_ticket(self,row)->str:
        msg=(
            f"RMCPJOIN1|{row['pairing_id']}|{self.owner_account_id}|"
            f"{row['requested_name']}|{row['code_nonce']}"
        ).encode("utf-8")
        return b64u(hmac.new(self.key,msg,hashlib.sha256).digest())

    def join_script(self,pairing_id:str,ticket:str)->str:
        row=self.db.query_one("SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,))
        if row is None or row["owner_account_id"]!=self.owner_account_id:
            raise DurableError("DEVICE_PAIRING_INVALID","unknown pairing")
        if row["used_at_ms"] is not None:
            raise DurableError("DEVICE_PAIRING_INVALID","pairing already used")
        if int(row["expires_at_ms"])<=now_ms():
            raise DurableError("DEVICE_PAIRING_EXPIRED","pairing expired")
        if not hmac.compare_digest(str(ticket),self._join_ticket(row)):
            raise DurableError("DEVICE_PAIRING_INVALID","invalid join ticket")
        code=self._code(row["pairing_id"],row["requested_name"],row["code_nonce"])
        bundle=f"{row['pairing_id']}|{code}"
        return render_windows_join_script(
            public_origin=self.config.public_origin,
            device_name=row["requested_name"],
            pairing_bundle=bundle,
        )

    def _response(self,row,*,include_code:bool)->dict:
        out={
            "pairing_id":row["pairing_id"],"expires_at_ms":int(row["expires_at_ms"]),
            "device_name":row["requested_name"],
        }
        if include_code:
            code=self._code(row["pairing_id"],row["requested_name"],row["code_nonce"])
            bundle=f"{row['pairing_id']}|{code}"
            out["pairing_code"]=code
            out["pairing_code_file_content"]=bundle
            out["node_pair_command"]=(
                f"python -m remotemcp.node pair --url {self.config.public_origin} "
                f"--pairing-id {row['pairing_id']} --code-file <PAIRING_CODE_FILE> "
                "--name <DEVICE_NAME> --root <MCP_NODE_ROOT> --runtime-dir <MCP_NODE_RUNTIME_DIR>"
            )
            out["join_script_filename"]="RemoteMCP-Join.ps1"
            out["join_script_powershell"]=render_windows_join_script(
                public_origin=self.config.public_origin,
                device_name=row["requested_name"],
                pairing_bundle=bundle,
            )
            ticket=self._join_ticket(row)
            join_url=(
                f"{self.config.public_origin}/device/v1/join/"
                f"{row['pairing_id']}/{ticket}"
            )
            out["join_url"]=join_url
            out["join_command"]=(
                "powershell -NoProfile -ExecutionPolicy Bypass -Command "
                f"\"irm '{join_url}' | iex\""
            )
        else:
            out["paired_device_id"]=row["paired_device_id"]
            out["status"]="USED"
        return out

    def begin(self,operation_id:str,device_name:str)->dict:
        name=(device_name or "").strip()
        if not name or len(name)>128 or any(c in name for c in "\r\n"):
            raise DurableError("INVALID_ARGUMENT","invalid device_name")
        op,created=self.operations.reserve(
            operation_id,"DEVICE_PAIR_BEGIN",{"device_name":name},
            principal_key=self.owner_account_id,
        )
        pairing_id="pair_"+hashlib.sha256(
            f"{self.owner_account_id}|{operation_id}".encode("utf-8")
        ).hexdigest()[:32]

        if not created and op["state"]==OperationState.SUCCEEDED.value:
            saved=self.operations.replay_result(op) or {}
            row=self.db.query_one(
                "SELECT * FROM device_pairings WHERE pairing_id=?",
                (saved.get("pairing_id") or pairing_id,),
            )
            if row is None:
                raise DurableError("OPERATION_IN_DOUBT","pairing row missing")
            include=row["used_at_ms"] is None and int(row["expires_at_ms"])>now_ms()
            return {**self._response(row,include_code=include),"replayed":True}

        if op["state"] in (OperationState.FAILED_FINAL.value,OperationState.IN_DOUBT.value):
            raise DurableError(
                op["error_code"] or "OPERATION_IN_DOUBT",
                "pairing begin operation is not retryable",
            )

        self.operations.mark_executing(operation_id)
        row=self.db.query_one(
            "SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,)
        )
        if row is None:
            nonce=b64u(secrets.token_bytes(18))
            code=self._code(pairing_id,name,nonce)
            t=now_ms(); exp=t+self.config.pairing_ttl_seconds*1000
            with self.db.transaction() as con:
                current=con.execute(
                    "SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,)
                ).fetchone()
                if current is None:
                    con.execute(
                        "INSERT INTO device_pairings(pairing_id,owner_account_id,requested_name,code_nonce,code_hash_sha256,created_at_ms,expires_at_ms) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            pairing_id,self.owner_account_id,name,nonce,
                            hashlib.sha256(code.encode()).hexdigest(),t,exp,
                        ),
                    )
            row=self.db.query_one(
                "SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,)
            )

        if row is None or row["owner_account_id"]!=self.owner_account_id or row["requested_name"]!=name:
            raise DurableError("OPERATION_CONFLICT","pairing row conflicts with operation")
        if row["used_at_ms"] is None and int(row["expires_at_ms"])<=now_ms():
            self.operations.fail(
                operation_id,"DEVICE_PAIRING_EXPIRED",
                {"pairing_id":pairing_id},False,
            )
            raise DurableError("DEVICE_PAIRING_EXPIRED","pairing expired")

        persisted={
            "pairing_id":pairing_id,
            "expires_at_ms":int(row["expires_at_ms"]),
            "device_name":name,
        }
        self.operations.succeed(operation_id,persisted)
        include=row["used_at_ms"] is None and int(row["expires_at_ms"])>now_ms()
        return {**self._response(row,include_code=include),"replayed":not created}

    def consume_local_identity(
        self,
        pairing_id:str,
        device_name:str,
        *,
        public_key_b64:str,
        timestamp_ms:int,
        nonce:str,
        signature_b64:str,
        platform:dict|None=None,
        capabilities:dict|None=None,
    )->dict:
        """Consume an active pairing without exposing its secret to callers.

        This is intentionally for a privileged local managed-pairing action.
        The one-time pairing code is derived inside the gateway process and is
        never accepted as a tool argument, shell argument, task payload, log,
        checkpoint, or repository artifact.
        """
        row=self.db.query_one(
            "SELECT * FROM device_pairings WHERE pairing_id=?",
            (str(pairing_id),),
        )
        if row is None or row["owner_account_id"]!=self.owner_account_id:
            raise DurableError("DEVICE_PAIRING_INVALID","unknown pairing")
        if str(device_name)!=str(row["requested_name"]):
            raise DurableError("DEVICE_PAIRING_INVALID","device_name mismatch")
        if row["used_at_ms"] is not None:
            raise DurableError("DEVICE_PAIRING_INVALID","pairing already used")
        if int(row["expires_at_ms"])<=now_ms():
            raise DurableError("DEVICE_PAIRING_EXPIRED","pairing expired")
        code=self._code(
            str(row["pairing_id"]),
            str(row["requested_name"]),
            str(row["code_nonce"]),
        )
        return self.consume({
            "pairing_id":str(row["pairing_id"]),
            "pairing_code":code,
            "device_name":str(device_name),
            "public_key_b64":str(public_key_b64),
            "timestamp_ms":int(timestamp_ms),
            "nonce":str(nonce),
            "signature_b64":str(signature_b64),
            "platform":dict(platform or {}),
            "capabilities":dict(capabilities or {}),
        })

    def consume(self,payload:dict)->dict:
        try:
            pairing_id=str(payload["pairing_id"]); code=str(payload["pairing_code"])
            name=str(payload["device_name"]); pub=str(payload["public_key_b64"])
            timestamp=int(payload["timestamp_ms"]); nonce=str(payload["nonce"]); sig=str(payload["signature_b64"])
        except Exception as exc:
            raise DurableError("DEVICE_PAIRING_INVALID","malformed pairing request") from exc
        row=self.db.query_one("SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,))
        if row is None or row["owner_account_id"]!=self.owner_account_id:
            raise DurableError("DEVICE_PAIRING_INVALID","unknown pairing")
        if row["used_at_ms"] is not None:
            raise DurableError("DEVICE_PAIRING_INVALID","pairing already used")
        t=now_ms()
        if int(row["expires_at_ms"])<=t:
            raise DurableError("DEVICE_PAIRING_EXPIRED","pairing expired")
        if not compare_pair_code(code,row["code_hash_sha256"]):
            raise DurableError("DEVICE_PAIRING_INVALID","pairing code mismatch")
        if name!=row["requested_name"]:
            raise DurableError("DEVICE_PAIRING_INVALID","device_name mismatch")
        if abs(t-timestamp)>self.config.signed_timestamp_window_seconds*1000:
            raise DurableError("DEVICE_SIGNATURE_STALE","pair timestamp outside allowed window")
        raw_nonce=__import__("remotemcp.routing.crypto",fromlist=["b64u_decode"]).b64u_decode(nonce)
        if len(raw_nonce)<16:
            raise DurableError("DEVICE_PAIRING_INVALID","pair nonce entropy too low")
        raw=verify_ed25519(pub,sig,canonical_pair(pairing_id,name,pub,timestamp,nonce))
        fp=public_key_fingerprint(raw)
        with self.db.transaction() as con:
            current=con.execute("SELECT * FROM device_pairings WHERE pairing_id=?",(pairing_id,)).fetchone()
            if current is None or current["used_at_ms"] is not None or int(current["expires_at_ms"])<=now_ms():
                raise DurableError("DEVICE_PAIRING_INVALID","pairing already consumed or expired")
            platform=payload.get("platform") if isinstance(payload.get("platform"),dict) else {}
            capabilities=payload.get("capabilities") if isinstance(payload.get("capabilities"),dict) else {}
            dev=self.devices.create_paired(
                con,name,pub,fp,capabilities=capabilities,platform=platform
            )
            con.execute(
                "UPDATE device_pairings SET used_at_ms=?,paired_device_id=? WHERE pairing_id=?",
                (now_ms(),dev["device_id"],pairing_id),
            )
        return {
            "device_id":dev["device_id"],"device_name":dev["device_name"],
            "origin":self.config.public_origin,"route_generation":int(dev["route_generation"]),
            "state":dev["state"],"public_key_b64":dev["public_key_b64"],
            "key_fingerprint_sha256":dev["key_fingerprint_sha256"],
            "paired_at_ms":int(dev["paired_at_ms"]),"server_time_ms":now_ms(),
        }