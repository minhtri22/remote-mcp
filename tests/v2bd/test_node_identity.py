from __future__ import annotations
import json,pytest
from remotemcp.durable.errors import DurableError
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity

def test_identity_key_is_stable_and_missing_key_after_device_is_fatal(tmp_path):
    rt=tmp_path/"rt";db=NodeDatabase(rt);db.bootstrap()
    a=NodeIdentity(rt,db);fp=a.fingerprint
    b=NodeIdentity(rt,db);assert b.fingerprint==fp
    a.device_path.write_text(json.dumps({"device_id":"dev_x","origin":"https://x","root":"x","route_generation":1,"public_key_b64":a.public_key_b64,"key_fingerprint_sha256":a.fingerprint}),encoding="utf-8")
    a.key_path.unlink()
    with pytest.raises(DurableError) as exc:NodeIdentity(rt,db)
    assert exc.value.code=="NODE_IDENTITY_MISMATCH"
