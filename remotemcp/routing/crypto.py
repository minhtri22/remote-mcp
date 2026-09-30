from __future__ import annotations

import base64
import hashlib
import hmac
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from remotemcp.durable.errors import DurableError


def b64u(data:bytes)->str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(value:str)->bytes:
    try:
        raw=value.encode("ascii")
        return base64.urlsafe_b64decode(raw+b"="*((4-len(raw)%4)%4))
    except Exception as exc:
        raise DurableError("INVALID_ARGUMENT","invalid base64url") from exc


def sha256_hex(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value)->str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def canonical_pair(pairing_id:str,device_name:str,public_key_b64:str,timestamp_ms:int,nonce:str)->bytes:
    return f"RMCPPAIR1\n{pairing_id}\n{device_name}\n{public_key_b64}\n{int(timestamp_ms)}\n{nonce}".encode("utf-8")


def canonical_node(device_id:str,route_generation:int,method:str,path:str,timestamp_ms:int,nonce:str,body:bytes)->bytes:
    return (
        f"RMCPNODE1\n{device_id}\n{int(route_generation)}\n{method.upper()}\n{path}\n"
        f"{int(timestamp_ms)}\n{nonce}\n{sha256_hex(body)}"
    ).encode("utf-8")


def public_key_fingerprint(public_key_raw:bytes)->str:
    return sha256_hex(public_key_raw)


def verify_ed25519(public_key_b64:str,signature_b64:str,message:bytes)->bytes:
    raw=b64u_decode(public_key_b64)
    if len(raw)!=32:
        raise DurableError("DEVICE_SIGNATURE_INVALID","Ed25519 public key must be 32 bytes")
    sig=b64u_decode(signature_b64)
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(sig,message)
    except Exception as exc:
        raise DurableError("DEVICE_SIGNATURE_INVALID","Ed25519 signature verification failed") from exc
    return raw


def compare_pair_code(code:str,expected_hash:str)->bool:
    return hmac.compare_digest(hashlib.sha256(code.encode("utf-8")).hexdigest(),expected_hash)
