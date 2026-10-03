from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs" / "branch_serial_job_lifecycle_prelock.json"


def _spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_branch_serial_prelock_identity_and_scope():
    s = _spec()
    assert s["schema"] == "remotemcp.branch-serial-job-lifecycle-prelock.v1"
    assert s["gate"] == "REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_PREREGISTRATION"
    assert s["status"] == "FROZEN"
    assert s["scope"] == "specification_only"


def test_serialization_is_per_task_lane_not_project_agent_or_device():
    m = _spec()["execution_model"]
    assert m["serialization_key"] == "task_id"
    assert m["different_task_lanes_may_run_concurrently"] is True
    assert m["project_max_active_tasks_remains_concurrency_limit"] is True
    assert m["agent_id_is_serialization_key"] is False
    assert m["device_id_is_serialization_key"] is False
    assert m["project_id_is_serialization_key"] is False
    assert m["same_lane_policy"] == "SERIAL"


def test_terminal_contract_is_evidence_based_and_agent_cannot_end_job():
    t = _spec()["terminal_contract"]
    assert set(t["terminal_states"]) == {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}
    assert t["predecessor_success_required"] is False
    assert t["predecessor_terminal_required"] is True
    assert t["predecessor_terminal_evidence_required"] is True
    assert t["manual_agent_end_job_allowed"] is False
    assert _spec()["terminal_evidence"]["cache_only_terminal_without_authoritative_evidence_is_sufficient"] is False


def test_operation_replay_precedes_new_successor_admission():
    order = _spec()["admission_order"]
    replay = next(i for i, x in enumerate(order) if "operation_id replay" in x)
    reserve = next(i for i, x in enumerate(order) if "reserve exactly one new admission" in x)
    assert replay < reserve
    race = _spec()["race_contract"]
    assert race["operation_replay_must_not_self_block"] is True
    assert race["two_new_submits_same_task_must_not_both_admit"] is True


def test_unresolved_predecessor_fails_closed_without_replacement():
    r = _spec()["routed_reconciliation"]
    assert r["device_offline_or_unreachable"] == "fail_closed"
    assert r["unresolved_history_must_not_be_auto_lost"] is True
    assert r["no_replacement_job_on_unresolved_predecessor"] is True
    errors = _spec()["errors"]
    assert errors["predecessor_active"] == "PREDECESSOR_JOB_NOT_TERMINAL"
    assert errors["predecessor_unresolved"] == "PREDECESSOR_STATE_UNRESOLVED"


def test_legacy_rollout_preserves_history():
    l = _spec()["legacy_rollout"]
    assert l["auto_cancel_legacy_jobs"] is False
    assert l["delete_job_history"] is False
    assert l["rewrite_terminal_history"] is False
    assert l["unresolved_legacy_row_blocks_same_lane_new_submit"] is True


def test_status_read_path_stays_pure_read():
    a = _spec()["active_count_semantics"]
    assert a["device_status_read_path_remains_pure_read"] is True
    assert a["request_time_device_status_reconciliation_forbidden"] is True
    assert a["heartbeat_active_node_jobs_is_diagnostic_not_terminal_authority"] is True


def test_swarm_uses_parallel_lanes_not_same_lane_parallel_jobs():
    s = _spec()["swarm_compatibility"]
    assert s["supported"] is True
    assert s["parallelism_unit"] == "task_lane"
    assert s["serialization_unit"] == "task_lane"
    assert s["multiple_agents_may_own_distinct_task_lanes_concurrently"] is True
    assert s["same_project_parallel_lanes_subject_to_max_active_tasks"] is True
    assert s["same_lane_parallel_jobs_in_scope"] is False


def test_no_manual_end_api_and_next_gate_is_locked():
    api = _spec()["public_api"]
    assert api["existing_task_job_submit_signature_preserved"] is True
    assert api["new_manual_end_job_api"] is False
    assert _spec()["next_gate"] == (
        "REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_"
        "IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK"
    )
