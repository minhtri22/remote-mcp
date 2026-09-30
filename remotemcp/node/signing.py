from __future__ import annotations

import secrets,time
from remotemcp.routing.crypto import b64u,canonical_node,canonical_pair


def nonce()->str:return b64u(secrets.token_bytes(18))

def now_ms()->int:return int(time.time()*1000)

def sign_pair(identity,pairing_id:str,device_name:str):
    ts=now_ms();n=nonce()
    msg=canonical_pair(pairing_id,device_name,identity.public_key_b64,ts,n)
    return ts,n,b64u(identity.private_key.sign(msg))

def signed_headers(identity,method:str,path:str,body:bytes)->dict[str,str]:
    if not identity.paired:
        raise RuntimeError("node is not paired")
    ts=now_ms();n=nonce();d=identity.device
    msg=canonical_node(d["device_id"],int(d["route_generation"]),method,path,ts,n,body)
    return {
        "X-RMCP-Device":d["device_id"],
        "X-RMCP-Route-Generation":str(d["route_generation"]),
        "X-RMCP-Timestamp":str(ts),
        "X-RMCP-Nonce":n,
        "X-RMCP-Signature":b64u(identity.private_key.sign(msg)),
    }
