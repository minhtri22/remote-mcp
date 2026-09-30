from __future__ import annotations
import pytest
from conftest import pair_node
from remotemcp.durable.errors import DurableError

def test_pair_begin_replay_and_one_time_consume(make_gateway,tmp_path):
    b=make_gateway()
    first=b.routing.pairing.begin("pair-op","node-a")
    replay=b.routing.pairing.begin("pair-op","node-a")
    assert first["pairing_code"]==replay["pairing_code"]
    assert first["pairing_id"]==replay["pairing_id"]
    from remotemcp.node.config import NodeConfig
    from remotemcp.node.db import NodeDatabase
    from remotemcp.node.identity import NodeIdentity
    from remotemcp.node.signing import sign_pair
    cfg=NodeConfig.create("http://127.0.0.1:9999",tmp_path/"root",tmp_path/"node")
    db=NodeDatabase(cfg.runtime_dir);db.bootstrap(); ident=NodeIdentity(cfg.runtime_dir,db)
    ts,n,s=sign_pair(ident,first["pairing_id"],"node-a")
    payload={"pairing_id":first["pairing_id"],"pairing_code":first["pairing_code"],"device_name":"node-a","public_key_b64":ident.public_key_b64,"timestamp_ms":ts,"nonce":n,"signature_b64":s}
    out=b.routing.pair_http(payload)
    assert out["device_id"].startswith("dev_")
    used=b.routing.pairing.begin("pair-op","node-a")
    assert "pairing_code" not in used and used["paired_device_id"]==out["device_id"]
    with pytest.raises(DurableError) as exc:b.routing.pair_http(payload)
    assert exc.value.code=="DEVICE_PAIRING_INVALID"

def test_generated_pair_command_contains_pairing_id(make_gateway):
    b=make_gateway(); p=b.routing.pairing.begin("p","machine x")
    assert "--pairing-id "+p["pairing_id"] in p["node_pair_command"]
    assert "--code-file <PAIRING_CODE_FILE>" in p["node_pair_command"]

def test_pair_begin_crash_after_row_commit_replays_same_code(make_gateway,monkeypatch):
    b=make_gateway()
    original=b.durable.operations.succeed
    fired={"done":False}
    def crash_once(operation_id,result,result_hash=""):
        if operation_id=="pair-crash" and not fired["done"]:
            fired["done"]=True
            raise RuntimeError("injected-after-pair-row-commit")
        return original(operation_id,result,result_hash)
    monkeypatch.setattr(b.durable.operations,"succeed",crash_once)
    with pytest.raises(RuntimeError,match="injected-after-pair-row-commit"):
        b.routing.pairing.begin("pair-crash","node-crash")

    row=b.durable.db.query_one(
        "select * from device_pairings where requested_name='node-crash'"
    )
    assert row is not None
    expected=b.routing.pairing._response(row,include_code=True)["pairing_code"]
    op=b.durable.operations.get("pair-crash")
    assert op["state"]=="EXECUTING"

    monkeypatch.setattr(b.durable.operations,"succeed",original)
    replay=b.routing.pairing.begin("pair-crash","node-crash")
    assert replay["pairing_code"]==expected
    assert replay["pairing_id"]==row["pairing_id"]
    assert replay["replayed"] is True
    assert b.durable.operations.get("pair-crash")["state"]=="SUCCEEDED"
