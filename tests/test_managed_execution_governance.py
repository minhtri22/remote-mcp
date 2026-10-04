from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_preflight():
    path = ROOT / "scripts" / "managed_execution_preflight.py"
    spec = importlib.util.spec_from_file_location("managed_execution_preflight", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_skill_contains_mandatory_identity_pairing_preflight():
    skill = (ROOT / "skills" / "remote-mcp" / "SKILL.md").read_text(encoding="utf-8")

    required = (
        "Mandatory managed-execution preflight",
        "A pairing ticket is capability material, not authorization",
        "BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_OR_EXPLICIT_DEDICATED_NODE_GATE",
        "Do not broaden an approved project root to its parent",
        "Pairing secrets must never be transported through",
        "Do not use a research/scientific task as a transport for infrastructure repair",
        "Loss of job observability is not authorization to resubmit",
        "Connector/OAuth/gateway errors are not evidence that an execution node must be restarted",
        "PLUGIN_SESSION_BINDING_FAILURE",
        "remote-mcp-v2-web-clean",
        "remote-mcp-v2-web",
        "fail closed",
        "task_job_recovery_status",
        "task_job_recover_path_escape",
    )
    for text in required:
        assert text in skill


def test_machine_readable_policy_matches_skill_contract():
    policy = json.loads(
        (ROOT / "skills" / "remote-mcp" / "managed_execution_policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert policy["schema"] == "remotemcp.managed-execution-governance.v1"
    assert policy["mode"] == "fail-closed"
    assert policy["identity"]["existing_approved_role_identity"] == "preserve"
    assert policy["identity"]["repair_by_repairing"] is False
    assert policy["pairing"]["ticket_is_authorization"] is False
    assert policy["project_root_mismatch"]["auto_create_node"] is False
    assert policy["project_root_mismatch"]["widen_existing_root"] is False
    assert policy["scientific_job"]["observability_loss_allows_relaunch"] is False
    recovery=policy["scientific_job"]["preexecution_routed_submit_recovery"]
    assert recovery["enabled"] is True
    assert recovery["supported_original_error_codes"] == ["PATH_ESCAPE"]
    assert recovery["require_same_proxy_job_id"] is True
    assert recovery["preserve_original_argv"] is True
    assert recovery["replacement_proxy_forbidden"] is True
    assert recovery["sql_or_node_db_repair_forbidden"] is True
    assert policy["plugin_session_binding"]["canonical_web_plugin_name"] == "remote-mcp-v2-web-clean"
    assert policy["plugin_session_binding"]["legacy_web_plugin_name"] == "remote-mcp-v2-web"
    assert policy["plugin_session_binding"]["failure_decision"] == "PLUGIN_SESSION_BINDING_FAILURE"


def test_pairing_preserves_existing_approved_identity():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "pair",
            "existing_approved_identity_for_role": True,
            "explicit_dedicated_node_gate": True,
            "pairing_secret_transport": "local-managed-pairing",
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_EXISTING_IDENTITY_PRESERVATION"


def test_pairing_ticket_does_not_replace_explicit_gate():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "pair",
            "existing_approved_identity_for_role": False,
            "explicit_dedicated_node_gate": False,
            "pairing_secret_transport": "local-managed-pairing",
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_EXPLICIT_DEDICATED_NODE_GATE"


def test_pairing_secret_cannot_travel_through_routed_job():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "pair",
            "pairing_secret_transport": "routed-job",
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_SECURE_PAIRING_CHANNEL"


def test_explicit_dedicated_pairing_requires_exact_scope_and_can_pass():
    p = _load_preflight()
    facts = {
        "action": "pair",
        "existing_approved_identity_for_role": False,
        "explicit_dedicated_node_gate": True,
        "approved_device_name": "project-exec-1",
        "requested_device_name": "project-exec-1",
        "approved_root": "X:/projects/example",
        "requested_root": "x:\\projects\\example\\",
        "approved_runtime_dir": "X:/runtime/example-node",
        "requested_runtime_dir": "x:\\runtime\\example-node",
        "pairing_secret_transport": "local-managed-pairing",
    }
    result = p.evaluate(facts)
    assert result["allowed"] is True
    assert result["decision"] == "ALLOW_EXPLICIT_DEDICATED_NODE_PAIRING"


def test_dedicated_pairing_blocks_parent_root_broadening():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "pair",
            "existing_approved_identity_for_role": False,
            "explicit_dedicated_node_gate": True,
            "approved_device_name": "project-exec-1",
            "requested_device_name": "project-exec-1",
            "approved_root": "X:/projects/example",
            "requested_root": "X:/projects",
            "approved_runtime_dir": "X:/runtime/example-node",
            "requested_runtime_dir": "X:/runtime/example-node",
            "pairing_secret_transport": "local-managed-pairing",
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_DEDICATED_NODE_SCOPE"


def test_project_root_mismatch_blocks_auto_pair_or_registration_repair():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "project-register",
            "project_path_within_existing_root": False,
            "pairing_secret_transport": "none",
        }
    )
    assert result["allowed"] is False
    assert (
        result["decision"]
        == "BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_OR_EXPLICIT_DEDICATED_NODE_GATE"
    )


def test_connector_or_gateway_failure_cannot_justify_node_restart():
    p = _load_preflight()
    for domain in ("connector-session", "oauth-session", "gateway-control-plane"):
        result = p.evaluate(
            {
                "action": "node-restart",
                "failure_domain": domain,
                "explicit_node_restart_authorization": True,
            }
        )
        assert result["allowed"] is False
        assert result["decision"] == "BLOCKED_ON_NODE_RESTART_NOT_JUSTIFIED"


def test_node_restart_requires_active_job_confirmation():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "node-restart",
            "failure_domain": "node-process",
            "explicit_node_restart_authorization": True,
            "active_routed_jobs": 2,
            "explicit_active_jobs_confirmation": False,
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_ACTIVE_ROUTED_JOBS_CONFIRMATION"


def test_scientific_job_observability_loss_does_not_authorize_relaunch():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "scientific-job-relaunch",
            "explicit_relaunch_authorization": False,
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "BLOCKED_ON_SCIENTIFIC_JOB_RELAUNCH_AUTHORIZATION"


def test_chatgpt_web_session_binding_fails_closed_when_plugin_or_link_missing():
    p = _load_preflight()
    base = {
        "action": "chatgpt-web-session-binding",
        "plugin_name": "remote-mcp-v2-web-clean",
        "canonical_plugin_available": True,
        "canonical_plugin_attached": True,
        "app_backed_dependency_present": True,
        "eligible_link_account": True,
    }

    for missing_key in (
        "canonical_plugin_available",
        "canonical_plugin_attached",
        "app_backed_dependency_present",
        "eligible_link_account",
    ):
        facts = dict(base)
        facts[missing_key] = False
        result = p.evaluate(facts)
        assert result["allowed"] is False
        assert result["decision"] == "PLUGIN_SESSION_BINDING_FAILURE"


def test_legacy_web_identity_is_not_accepted_as_canonical_session():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "chatgpt-web-session-binding",
            "plugin_name": "remote-mcp-v2-web",
            "canonical_plugin_available": True,
            "canonical_plugin_attached": True,
            "app_backed_dependency_present": True,
            "eligible_link_account": True,
        }
    )
    assert result["allowed"] is False
    assert result["decision"] == "PLUGIN_SESSION_BINDING_FAILURE"


def test_canonical_web_session_binding_can_pass():
    p = _load_preflight()
    result = p.evaluate(
        {
            "action": "chatgpt-web-session-binding",
            "plugin_name": "remote-mcp-v2-web-clean",
            "canonical_plugin_available": True,
            "canonical_plugin_attached": True,
            "app_backed_dependency_present": True,
            "eligible_link_account": True,
        }
    )
    assert result["allowed"] is True
    assert result["decision"] == "ALLOW_CANONICAL_WEB_SESSION_BINDING"
