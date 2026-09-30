from __future__ import annotations
import json,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2bd_qual_common import gateway
from remotemcp.node.config import NodeConfig
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity
from remotemcp.node.signing import sign_pair
from remotemcp.durable.errors import DurableError

def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-pair-") as td:
        base=Path(td);g=gateway(base)
        p1=g.routing.pairing.begin("op","qual-pair")
        p2=g.routing.pairing.begin("op","qual-pair")
        assert p1["pairing_code"]==p2["pairing_code"]
        cfg=NodeConfig.create("http://127.0.0.1:9999",base/"node-root",base/"node-rt")
        db=NodeDatabase(cfg.runtime_dir);db.bootstrap();ident=NodeIdentity(cfg.runtime_dir,db)
        ts,n,s=sign_pair(ident,p1["pairing_id"],"qual-pair")
        payload={"pairing_id":p1["pairing_id"],"pairing_code":p1["pairing_code"],"device_name":"qual-pair","public_key_b64":ident.public_key_b64,"timestamp_ms":ts,"nonce":n,"signature_b64":s}
        out=g.routing.pair_http(payload)
        replay=g.routing.pairing.begin("op","qual-pair")
        assert "pairing_code" not in replay and replay["paired_device_id"]==out["device_id"]
        duplicate=None
        try:g.routing.pair_http(payload)
        except DurableError as e:duplicate=e.code
        assert duplicate=="DEVICE_PAIRING_INVALID"
        print(json.dumps({"verdict":"PASS","same_code_on_unused_replay":True,"clear_code_after_used":False,"duplicate_consume":duplicate},indent=2))
if __name__=="__main__":main()