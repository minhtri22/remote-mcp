from __future__ import annotations
import json,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2bd_qual_common import gateway,pair_node
from remotemcp.durable.errors import DurableError
from remotemcp.node.signing import signed_headers,nonce
from remotemcp.routing.crypto import b64u,canonical_node
from remotemcp.durable.models import now_ms

def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-auth-") as td:
        base=Path(td);g=gateway(base);root=base/"node";root.mkdir();node,dev=pair_node(g,root,base/"rt","auth-node")
        body=b"{}"; path="/device/v1/heartbeat"
        h=signed_headers(node.identity,"POST",path,body)
        g.routing.verify_signed("POST",path,h,body)
        replay=None
        try:g.routing.verify_signed("POST",path,h,body)
        except DurableError as e:replay=e.code
        assert replay=="DEVICE_REPLAY"

        stale_ts=now_ms()-61000;n=nonce()
        msg=canonical_node(dev["device_id"],1,"POST",path,stale_ts,n,body)
        hs={"X-RMCP-Device":dev["device_id"],"X-RMCP-Route-Generation":"1","X-RMCP-Timestamp":str(stale_ts),"X-RMCP-Nonce":n,"X-RMCP-Signature":b64u(node.identity.private_key.sign(msg))}
        stale=None
        try:g.routing.verify_signed("POST",path,hs,body)
        except DurableError as e:stale=e.code
        assert stale=="DEVICE_SIGNATURE_STALE"

        fresh=signed_headers(node.identity,"POST",path,body); fresh["X-RMCP-Route-Generation"]="999"
        generation=None
        try:g.routing.verify_signed("POST",path,fresh,body)
        except DurableError as e:generation=e.code
        assert generation=="DEVICE_ROUTE_GENERATION_MISMATCH"
        print(json.dumps({"verdict":"PASS","nonce_replay":replay,"stale_timestamp":stale,"stale_generation":generation},indent=2))
if __name__=="__main__":main()