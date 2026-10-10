"""Compare 19 frozen command bindings against later read-only OOB evidence.

A comparison of partial ledger projections is NOT a byte-identical database
proof, a signed-isolation activation, or command terminal adjudication.
"""
from __future__ import annotations

import re
from typing import Any

HEX64=re.compile(r"^[a-f0-9]{64}$")
COMMON_IMMUTABLE_FIELDS=(
    "command_id","command_type","route_generation",
    "gateway_state","delivery_attempt","task_id","project_id",
)


def compare_immutable_history(
    frozen:dict[str,Any], current:dict[str,Any], *,
    independently_pinned_digest:str,
    protected_four:dict[str,Any]|None=None,
)->dict[str,Any]:
    reasons=[]
    def hold(s): reasons.append(s)
    if not isinstance(frozen,dict) or not isinstance(current,dict):
        return {"verdict":"HOLD","blockers":["EVIDENCE_NOT_OBJECT"],
                "production_deployment_permitted":False}
    old=frozen.get("rows")
    new=current.get("records")
    if not isinstance(old,list) or not isinstance(new,list):
        return {"verdict":"HOLD","blockers":["LEDGER_ROWS_MISSING"],
                "production_deployment_permitted":False}
    if (len(old)!=19 or len(new)!=19 or
            not all(isinstance(x,dict) for x in old+new)):
        hold("HISTORICAL_19_ROW_CENSUS_NOT_EXACT")
    old_ids=[x.get("command_id") for x in old if isinstance(x,dict)]
    new_ids=[x.get("command_id") for x in new if isinstance(x,dict)]
    if (len(set(old_ids))!=19 or len(set(new_ids))!=19 or
            set(old_ids)!=set(new_ids)):
        hold("HISTORICAL_COMMAND_ID_SET_DRIFT")
    sha=frozen.get("lease_inventory_digest_sha256")
    if (not isinstance(independently_pinned_digest,str) or
            not HEX64.fullmatch(independently_pinned_digest) or
            not isinstance(sha,str) or
            sha!=independently_pinned_digest):
        hold("FROZEN_INVENTORY_HASH_PIN_MISMATCH")
    if frozen.get("inventory_drift") is not False:
        hold("FROZEN_BASELINE_NOT_PRISTINE")
    if (frozen.get("gateway_db_mutated") is not False or
            frozen.get("node_db_mutated") is not False or
            current.get("read_only") is not True or
            current.get("mutation_performed") is not False):
        hold("READ_ONLY_PROVENANCE_NOT_REPORTED")
    if current.get("pending_rows_truncated") is not False:
        hold("RECENT_CENSUS_TRUNCATED")
    if any(x.get("expired") is not True for x in new):
        hold("RECENT_HISTORICAL_LEASE_STILL_UNEXPIRED")
    keyed={x.get("command_id"):x for x in new}
    for left in old:
        right=keyed.get(left.get("command_id"))
        if not isinstance(right,dict):
            continue
        if any(f not in left or f not in right or
               left[f]!=right[f] for f in COMMON_IMMUTABLE_FIELDS):
            hold("EXACT_SHARED_FIELD_DRIFT")
            break
    if protected_four is None:
        hold("FOUR_COMMAND_FORENSICS_MISSING")
    else:
        four=protected_four.get("rows")
        if (not isinstance(four,list) or len(four)!=4 or
                not all(isinstance(x,dict) and x.get("command_id") in set(old_ids)
                        for x in four) or
                len({x["command_id"] for x in four})!=4):
            hold("FOUR_PROTECTED_BINDINGS_INVALID")
        if protected_four.get("frozen_inventory_digest_sha256")!=sha:
            hold("FOUR_COMMAND_FROZEN_PIN_MISMATCH")
        if (protected_four.get("all_fully_reconciled") is True or
                protected_four.get("replay_authorized") is True or
                protected_four.get("database_changes")!=0):
            hold("PROTECTED_FOUR_OUTCOME_OR_MUTATION_INVALID")
    # Current OOB projection lacks request_hash and expiry timestamps.
    # An equal ID/type/attempt/state is useful but not byte-identical proof.
    if not reasons:
        return {
            "verdict":"PARTIAL_STRUCTURAL_MATCH",
            "checked_historical_rows":19,
            "checked_protected_rows":4,
            "byte_exact_immutability_proven":False,
            "historical_four_terminal_adjudicated":False,
            "release_gate":"HOLD",
            "blockers":["FULL_IMMUTABLE_ROW_FIELDS_OR_SIGNED_HASH_PROOF_MISSING",
                        "PRODUCTION_SIGNED_ISOLATION_NOT_ATTESTED"],
            "production_deployment_permitted":False,
        }
    return {"verdict":"HOLD","blockers":sorted(set(reasons)),
            "byte_exact_immutability_proven":False,
            "historical_four_terminal_adjudicated":False,
            "release_gate":"HOLD",
            "production_deployment_permitted":False}
