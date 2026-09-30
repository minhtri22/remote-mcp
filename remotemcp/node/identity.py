from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from remotemcp.durable.errors import DurableError
from remotemcp.durable.process import atomic_write_bytes,atomic_write_json
from remotemcp.routing.crypto import b64u,public_key_fingerprint


class NodeIdentity:
    def __init__(self,runtime_dir:Path,db):
        self.runtime_dir=runtime_dir.resolve();self.db=db
        self.key_path=self.runtime_dir/"device-ed25519.pem"
        self.device_path=self.runtime_dir/"device.json"
        self.private_key=self._load_or_create_key()
        self.public_raw=self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw
        )
        self.public_key_b64=b64u(self.public_raw)
        self.fingerprint=public_key_fingerprint(self.public_raw)
        self.device=self._load_device()
        self._verify_consistency()

    def _load_or_create_key(self):
        self.runtime_dir.mkdir(parents=True,exist_ok=True)
        if self.key_path.exists():
            try:
                key=serialization.load_pem_private_key(self.key_path.read_bytes(),password=None)
            except Exception as exc:
                raise DurableError("NODE_IDENTITY_MISMATCH","invalid node private key") from exc
            if not isinstance(key,Ed25519PrivateKey):
                raise DurableError("NODE_IDENTITY_MISMATCH","node key is not Ed25519")
            return key
        if self.device_path.exists():
            raise DurableError("NODE_IDENTITY_MISMATCH","device.json exists but private key is missing")
        key=Ed25519PrivateKey.generate()
        pem=key.private_bytes(
            serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()
        )
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
        fd=os.open(self.key_path,flags,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(pem);f.flush()
            try:os.fsync(f.fileno())
            except OSError:pass
        return key

    def _load_device(self):
        if not self.device_path.exists():return None
        try:return json.loads(self.device_path.read_text(encoding="utf-8"))
        except Exception as exc:raise DurableError("NODE_IDENTITY_MISMATCH","invalid device.json") from exc

    def _verify_consistency(self):
        meta=self.db.get_meta("identity")
        for source in (self.device,meta):
            if source:
                if source.get("public_key_b64")!=self.public_key_b64 or source.get("key_fingerprint_sha256")!=self.fingerprint:
                    raise DurableError("NODE_IDENTITY_MISMATCH","node identity fingerprint mismatch")
        if self.device and meta:
            for key in ("device_id","origin","root","route_generation"):
                if str(self.device.get(key))!=str(meta.get(key)):
                    raise DurableError("NODE_IDENTITY_MISMATCH",f"node identity mismatch for {key}")

    @property
    def paired(self)->bool:return self.device is not None

    def persist_paired(self,response:dict,*,origin:str,root:Path,device_name:str):
        if self.paired:
            if self.device.get("device_id")!=response.get("device_id"):
                raise DurableError("NODE_IDENTITY_MISMATCH","cannot replace paired device identity")
            return self.device
        data={
            "schema_version":1,"device_id":response["device_id"],"device_name":device_name,
            "origin":origin.rstrip("/"),"root":str(root.resolve()),
            "public_key_b64":self.public_key_b64,"key_fingerprint_sha256":self.fingerprint,
            "route_generation":int(response["route_generation"]),"paired_at_ms":int(response["paired_at_ms"]),
        }
        atomic_write_json(self.device_path,data)
        self.db.set_meta("identity",data)
        self.device=data
        return data
