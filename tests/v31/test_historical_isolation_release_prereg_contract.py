"""Static, offline prereg lock validation, NOT production execution/acceptance."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/v31/REMOTEMCP_V31_HISTORICAL_EXPIRED_COMMAND_ISOLATION_AND_NODE_INSTRUMENTATION_RELEASE_QUALIFICATION_PREREGISTRATION.md"
SPEC = ROOT / "specs/v31/historical_expired_isolation_node_release_prereg_v1.json"


def load():
    return DOC.read_text(encoding="utf-8"), json.loads(SPEC.read_text(encoding="utf-8"))


def test_frozen_evidence_identity_and_cardinality():
    doc, spec = load()
    assert spec["phase"] == "PREREGISTRATION_ONLY"
    rows = spec["frozen_leased_inventory"]
    assert (rows["terminal_node_gateway_stale"], rows["expired_delivered_no_node_receipt"],
            rows["expired_node_nonterminal"]) == (15, 3, 1)
    assert rows["rows"] == sum(rows[x] for x in (
        "terminal_node_gateway_stale", "expired_delivered_no_node_receipt",
        "expired_node_nonterminal"
    ))
    assert spec["frozen_four_forensics"]["rows"] == 4
    assert (spec["frozen_four_forensics"]["node_nonterminal"],
            spec["frozen_four_forensics"]["no_proven_execution"]) == (1, 3)
    assert spec["frozen_four_forensics"]["all_terminal_reconciled"] is False
    for digest in (rows["sha256"], spec["frozen_four_forensics"]["result_sha256"]):
        assert re.fullmatch("[0-9a-f]{64}", digest)
        assert digest in doc
    assert spec["protected_command_identity_source"] == "PRIVATE_FROZEN_SNAPSHOT_ONLY"


def test_all_isolation_entry_points_and_no_mutation_contract():
    doc, spec = load()
    assert set(spec["protected_types"]) == {
        "JOB_SUBMIT", "JOB_GET", "PROJECT_PROBE", "TASK_BASE_RESOLVE"
    }
    guards = spec["guards"]
    assert set(guards["source_identity_verification"]) == {
        "device_id", "route_generation", "command_id", "command_type",
        "request_hash", "operation_id", "operation_step"
    }
    assert set(guards["protected_actions"]) == {
        "CREATE_REUSE", "REVIVE_EXPIRED", "REQUEUE", "LEASE",
        "DELIVER", "RECOVER_ROUTED_JOB", "HISTORICAL_REPLAY"
    }
    assert guards["historical_command_write_authorized"] is False
    assert guards["science_job_write_authorized"] is False
    assert guards["powershell_global_allowlist_authorized"] is False
    assert guards["offline_or_stale_capacity"] == "FAIL_CLOSED"
    assert guards["invalid_snapshot"] == "HOLD"
    assert guards["operation_alias_unresolved"] == "HOLD"
    assert guards["expired_does_not_imply_replay"] is True
    assert "create_in_tx" in doc
    assert "CANCELLED" in doc and "DEVICE_COMMAND_EXPIRED" in doc
    assert "HISTORICAL_COMMAND_PROTECTED" in doc
    assert "OPERATION_LINK_UNRESOLVED_HOLD" in doc
    assert "NO_PROVEN_EXECUTION_UNRESOLVED" in doc


def test_prelocked_release_gates_and_anti_replay():
    doc, spec = load()
    assert spec["synthetic_isolation_test_cases"] == [
        f"ISO-{i:02d}" for i in range(1, 13)
    ]
    qa = spec["release_qualification"]
    assert sorted(qa["qa_operating_systems"]) == ["ubuntu-latest", "windows-latest"]
    assert qa["baseline_pr73_required_checks"] == 14
    assert qa["baseline_pr72_required_checks"] == 2
    assert qa["consecutive_empty_poll_cycles_per_os"] == 60
    assert qa["telemetry_p95_overhead_milliseconds_max"] == 5
    assert qa["preflight_snapshot_min_gap_seconds"] >= 30
    assert qa["no_unexpired_commands"] is True
    assert qa["signed_fresh_capacity_required"] is True
    assert qa["physical_nonterminal_node_jobs"] == 0
    assert qa["physical_unresolved_node_jobs"] == 0
    assert qa["private_approval_required"] is True
    assert qa["release_pin_requalification_required"] is True
    assert qa["production_activation_from_prereg"] is False
    live = spec["live_qualification"]
    assert live == {
        "sequential_reads": 6,
        "each_client_elapsed_milliseconds_strictly_less_than": 55000,
        "receipt_match_required": True,
        "stop_at_first_failure": True,
    }
    status = spec["terminal_status"]
    assert all(status[k] is False for k in (
        "static_isolation_implemented", "node_upgraded", "live_six_pass",
        "historical_four_reconciled", "science_agents_resume_authorized"
    ))
    assert "STOP" in doc
    assert "NO EXECUTION LOCK" in doc


def test_public_prereg_discloses_no_private_identifiers_or_machine_paths():
    doc, spec = load()
    full_text = doc + "\n" + json.dumps(spec, sort_keys=True)
    for pattern in [
        r"\bdev_[0-9a-f]{32}\b",
        r"\bagt_[0-9a-f]{32}\b",
        r"\bses_[0-9a-f]{32}\b",
        r"\bC:\\Users\\[A-Za-z0-9._-]+",
        r"\bD:\\(?:WORK|2\.RemoteMCP)(?:\\|\b)",
        r"\bcmd_[0-9a-f]{32}\b",
        r"\brjob_[0-9a-f]{32}\b",
    ]:
        assert not re.search(pattern, full_text, flags=re.I)
