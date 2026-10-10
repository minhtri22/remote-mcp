"""Synthetic exact-set/partial-projection QA; never change a live ledger."""
from copy import deepcopy

import pytest
from tools.v31_frozen_command_readonly_comparator import compare_immutable_history

DIGEST="9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85"


def evidence():
    rows=[]
    for i in range(19):
        rows.append(dict(
            command_id=f"cmd_{i:032x}",
            command_type=["JOB_SUBMIT","JOB_GET","PROJECT_PROBE","TASK_BASE_RESOLVE"][i%4],
            route_generation=1,gateway_state="LEASED",
            delivery_attempt=1,task_id=f"tsk_{i:025x}",
            project_id=f"prj_{i:024x}",request_hash=f"{i:064x}",
            command_expires_at_ms=1000+i,
        ))
    frozen={
        "rows":rows,"lease_inventory_digest_sha256":DIGEST,
        "inventory_drift":False,"gateway_db_mutated":False,
        "node_db_mutated":False,
    }
    current={
        "records":[{k:v for k,v in row.items() if k not in (
            "request_hash","command_expires_at_ms",
        )} | {"expired":True} for row in rows],
        "pending_rows_truncated":False,
        "read_only":True,"mutation_performed":False,
    }
    protected={
        "rows":[{"command_id":x["command_id"]} for x in rows[:4]],
        "frozen_inventory_digest_sha256":DIGEST,
        "all_fully_reconciled":False,"replay_authorized":False,
        "database_changes":0,
    }
    return frozen,current,protected


def compare(f,c,p,pin=DIGEST):
    return compare_immutable_history(
        f,c,independently_pinned_digest=pin,protected_four=p,
    )


def test_matches_frozen_ids_and_shared_fields_but_never_licenses_upgrade():
    a,b,c=evidence()
    res=compare(a,b,c)
    assert res["verdict"]=="PARTIAL_STRUCTURAL_MATCH"
    assert res["checked_historical_rows"]==19
    assert res["checked_protected_rows"]==4
    assert res["byte_exact_immutability_proven"] is False
    assert res["historical_four_terminal_adjudicated"] is False
    assert res["release_gate"]=="HOLD"
    assert res["production_deployment_permitted"] is False


@pytest.mark.parametrize("damage",[
    "missing_row","new_row","duplicated_id","different_state",
    "different_delivery","different_task","different_project","different_type",
    "different_route","unexpired","truncated","wrong_source_hash","no_pinned_hash",
    "frozen_drift","reported_mutation","not_read_only","no_four","four_drift",
    "four_replayed","four_terminal_assumed","malformed_row",
])
def test_partial_or_mutated_evidence_never_clears_gate(damage):
    a,b,c=evidence()
    pin=DIGEST
    if damage=="missing_row": b["records"].pop()
    elif damage=="new_row": b["records"][0]["command_id"]="cmd_"+"f"*32
    elif damage=="duplicated_id":
        b["records"][0]["command_id"]=b["records"][1]["command_id"]
    elif damage=="different_state": b["records"][0]["gateway_state"]="SUCCEEDED"
    elif damage=="different_delivery": b["records"][0]["delivery_attempt"]=2
    elif damage=="different_task": b["records"][0]["task_id"]="tsk_other"
    elif damage=="different_project": b["records"][0]["project_id"]="prj_other"
    elif damage=="different_type": b["records"][0]["command_type"]="JOB_CANCEL"
    elif damage=="different_route": b["records"][0]["route_generation"]=2
    elif damage=="unexpired": b["records"][0]["expired"]=False
    elif damage=="truncated": b["pending_rows_truncated"]=True
    elif damage=="wrong_source_hash": a["lease_inventory_digest_sha256"]="b"*64
    elif damage=="no_pinned_hash": pin=""
    elif damage=="frozen_drift": a["inventory_drift"]=True
    elif damage=="reported_mutation": b["mutation_performed"]=True
    elif damage=="not_read_only": b["read_only"]=False
    elif damage=="no_four": c=None
    elif damage=="four_drift": c["rows"][0]["command_id"]="cmd_"+"f"*32
    elif damage=="four_replayed": c["replay_authorized"]=True
    elif damage=="four_terminal_assumed": c["all_fully_reconciled"]=True
    elif damage=="malformed_row": b["records"][0]=None
    r=compare(a,b,c,pin)
    assert r["verdict"]=="HOLD"
    assert r["production_deployment_permitted"] is False
