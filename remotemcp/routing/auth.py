from __future__ import annotations

import sqlite3

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .crypto import b64u_decode,canonical_node,verify_ed25519


class SignedRequestVerifier:
    def __init__(self,config,db,devices):
        self.config=config; self.db=db; self.devices=devices

    def verify(self,method:str,path:str,headers,body:bytes):
        if len(body)>self.config.max_signed_body_bytes:
            raise DurableError("INVALID_ARGUMENT","signed request body too large")
        try:
            device_id=str(headers["X-RMCP-Device"])
            generation=int(headers["X-RMCP-Route-Generation"])
            timestamp=int(headers["X-RMCP-Timestamp"])
            nonce=str(headers["X-RMCP-Nonce"])
            signature=str(headers["X-RMCP-Signature"])
        except Exception as exc:
            raise DurableError("DEVICE_SIGNATURE_INVALID","missing or invalid signed request headers") from exc
        row=self.devices.get(device_id)
        if row["state"]=="REVOKED":
            raise DurableError("DEVICE_REVOKED","device is revoked")
        if int(row["route_generation"])!=generation:
            raise DurableError("DEVICE_ROUTE_GENERATION_MISMATCH","route generation mismatch")
        t=now_ms()
        if abs(t-timestamp)>self.config.signed_timestamp_window_seconds*1000:
            raise DurableError("DEVICE_SIGNATURE_STALE","signed request timestamp stale")
        raw_nonce=b64u_decode(nonce)
        if len(raw_nonce)<16:
            raise DurableError("DEVICE_SIGNATURE_INVALID","nonce entropy too low")
        verify_ed25519(row["public_key_b64"],signature,canonical_node(device_id,generation,method,path,timestamp,nonce,body))
        with self.db.transaction() as con:
            try:
                con.execute(
                    "INSERT INTO device_request_nonces(device_id,nonce,seen_at_ms,expires_at_ms) VALUES(?,?,?,?)",
                    (device_id,nonce,t,t+self.config.nonce_retention_seconds*1000),
                )
            except sqlite3.IntegrityError as exc:
                raise DurableError("DEVICE_REPLAY","signed request nonce already used") from exc
            fresh=self.devices.touch_online_in_tx(con,device_id,t)
            con.execute("DELETE FROM device_request_nonces WHERE expires_at_ms<?",(t,))
        return fresh
