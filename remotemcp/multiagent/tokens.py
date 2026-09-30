from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from remotemcp.durable.errors import DurableError


class LeaseTokenManager:
    def __init__(self,runtime_dir:Path,db):
        self.runtime_dir=runtime_dir.resolve()
        self.path=self.runtime_dir/"lease-token.key"
        self.db=db
        self.key=self._load_or_create()

    def _active_leases(self)->int:
        try:
            row=self.db.query_one("SELECT COUNT(*) AS n FROM task_leases")
            return int(row["n"]) if row else 0
        except Exception:
            return 0

    def _load_or_create(self)->bytes:
        self.runtime_dir.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            data=self.path.read_bytes()
            if len(data)!=32:
                raise DurableError("STARTUP_FATAL_LEASE_KEY_MISSING","lease-token.key must be exactly 32 bytes")
            return data
        if self._active_leases():
            raise DurableError("STARTUP_FATAL_LEASE_KEY_MISSING","active leases exist but lease-token.key is missing")
        data=secrets.token_bytes(32)
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
        fd=os.open(self.path,flags,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(data)
            f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        return data

    @staticmethod
    def new_nonce()->str:
        return "ln_"+secrets.token_urlsafe(18)

    def derive(self,task_id:str,lease_epoch:int,agent_id:str,session_id:str,nonce:str)->str:
        msg=f"{task_id}|{lease_epoch}|{agent_id}|{session_id}|{nonce}".encode()
        mac=hmac.new(self.key,msg,hashlib.sha256).digest()
        enc=base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
        return f"lt1_{nonce}_{enc}"

    @staticmethod
    def digest(token:str)->str:
        return hashlib.sha256(token.encode()).hexdigest()

    def verify(self,token:str,expected_hash:str)->bool:
        return hmac.compare_digest(self.digest(token),expected_hash)