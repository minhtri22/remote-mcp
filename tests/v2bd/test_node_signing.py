from __future__ import annotations
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from remotemcp.node.signing import nonce
from remotemcp.routing.crypto import b64u,b64u_decode,canonical_node,verify_ed25519

def test_canonical_sign_verify():
    key=Ed25519PrivateKey.generate(); raw=key.public_key().public_bytes_raw(); pub=b64u(raw)
    n=nonce(); msg=canonical_node("dev_a",1,"POST","/x",123,n,b"{}")
    sig=b64u(key.sign(msg))
    assert verify_ed25519(pub,sig,msg)==raw
    assert len(b64u_decode(n))>=16
