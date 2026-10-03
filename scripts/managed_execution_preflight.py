from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_PAIRING_SECRET_TRANSPORTS = {
    "local-managed-pairing",
    "dedicated-managed-pairing-action",
}

FORBIDDEN_PAIRING_SECRET_TRANSPORTS = {
    "run-command",
    "routed-job",
    "job-argv",
    "shell-argument",
    "task-payload",
    "repository",
    "log",
    "checkpoint",
    "lineage",
}

NON_NODE_FAILURE_DOMAINS = {
    "connector-session",
    "oauth-session",
    "gateway-control-plane",
    "project-root-mismatch",
    "scientific-job-observability",
}


def _norm_scope(value: str | None) -> str:
    raw = (value or "").strip().replace("\\", "/")
    while "//" in raw:
        raw = raw.replace("//", "/")
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw.casefold()


def _same_scope(left: str | None, right: str | None) -> bool:
    return bool(left and right) and _norm_scope(left) == _norm_scope(right)


def _block(code: str, *reasons: str) -> dict[str, Any]:
    return {
        "allowed": False,
        "decision": code,
        "reasons": [r for r in reasons if r],
    }


def evaluate(facts: dict[str, Any]) -> dict[str, Any]:
    action = str(facts.get("action") or "").strip()

    if not action:
        return _block("BLOCKED_ON_PREFLIGHT_INPUT", "action is required")

    if bool(facts.get("identity_ambiguous")):
        return _block(
            "BLOCKED_ON_EXECUTION_IDENTITY_AMBIGUITY",
            "execution identity is ambiguous; fail closed instead of guessing",
        )

    secret_transport = str(facts.get("pairing_secret_transport") or "none").strip()
    if secret_transport in FORBIDDEN_PAIRING_SECRET_TRANSPORTS:
        return _block(
            "BLOCKED_ON_SECURE_PAIRING_CHANNEL",
            f"pairing secret transport is forbidden: {secret_transport}",
        )

    if action == "pair":
        if bool(facts.get("existing_approved_identity_for_role")):
            return _block(
                "BLOCKED_ON_EXISTING_IDENTITY_PRESERVATION",
                "an approved identity already exists for this execution role",
            )

        if not bool(facts.get("explicit_dedicated_node_gate")):
            return _block(
                "BLOCKED_ON_EXPLICIT_DEDICATED_NODE_GATE",
                "pairing requires an explicit dedicated-node gate",
            )

        required = (
            "approved_device_name",
            "requested_device_name",
            "approved_root",
            "requested_root",
            "approved_runtime_dir",
            "requested_runtime_dir",
        )
        missing = [key for key in required if not str(facts.get(key) or "").strip()]
        if missing:
            return _block(
                "BLOCKED_ON_DEDICATED_NODE_SCOPE",
                "missing exact approved/requested scope: " + ", ".join(missing),
            )

        if not _same_scope(
            str(facts.get("approved_device_name")),
            str(facts.get("requested_device_name")),
        ):
            return _block(
                "BLOCKED_ON_DEDICATED_NODE_SCOPE",
                "requested device name does not exactly match the approved device name",
            )

        if not _same_scope(
            str(facts.get("approved_root")),
            str(facts.get("requested_root")),
        ):
            return _block(
                "BLOCKED_ON_DEDICATED_NODE_SCOPE",
                "requested root does not exactly match the approved project root; parent broadening is forbidden",
            )

        if not _same_scope(
            str(facts.get("approved_runtime_dir")),
            str(facts.get("requested_runtime_dir")),
        ):
            return _block(
                "BLOCKED_ON_DEDICATED_NODE_SCOPE",
                "requested runtime directory does not exactly match the approved runtime directory",
            )

        if secret_transport not in ALLOWED_PAIRING_SECRET_TRANSPORTS:
            return _block(
                "BLOCKED_ON_SECURE_PAIRING_CHANNEL",
                "pairing requires a dedicated local/managed secret-consumption path",
            )

        return {
            "allowed": True,
            "decision": "ALLOW_EXPLICIT_DEDICATED_NODE_PAIRING",
            "reasons": ["all dedicated-node identity and secret-transport prerequisites are explicit"],
        }

    if action in {"project-register", "project-bind"}:
        if facts.get("project_path_within_existing_root") is False:
            return _block(
                "BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_OR_EXPLICIT_DEDICATED_NODE_GATE",
                "project is outside the existing node root; do not repair by pairing, root widening, or implicit migration",
            )
        if facts.get("project_path_within_existing_root") is None:
            return _block(
                "BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_PREFLIGHT",
                "project/root relationship must be established before registration or binding",
            )
        return {
            "allowed": True,
            "decision": "ALLOW_MANAGED_PROJECT_OPERATION",
            "reasons": ["project is within the established execution root"],
        }

    if action == "node-restart":
        failure_domain = str(facts.get("failure_domain") or "unknown").strip()
        if failure_domain in NON_NODE_FAILURE_DOMAINS:
            return _block(
                "BLOCKED_ON_NODE_RESTART_NOT_JUSTIFIED",
                f"{failure_domain} is not evidence of node-process failure",
            )
        if failure_domain != "node-process":
            return _block(
                "BLOCKED_ON_NODE_FAILURE_CLASSIFICATION",
                "node-process failure must be independently established before restart",
            )
        if not bool(facts.get("explicit_node_restart_authorization")):
            return _block(
                "BLOCKED_ON_NODE_RESTART_AUTHORIZATION",
                "node restart requires explicit authorization",
            )
        active_jobs = int(facts.get("active_routed_jobs") or 0)
        if active_jobs > 0 and not bool(facts.get("explicit_active_jobs_confirmation")):
            return _block(
                "BLOCKED_ON_ACTIVE_ROUTED_JOBS_CONFIRMATION",
                "active routed jobs require explicit confirmation before restart",
            )
        return {
            "allowed": True,
            "decision": "ALLOW_NODE_RESTART",
            "reasons": ["node-process failure and restart authorization are explicit"],
        }

    if action == "scientific-job-relaunch":
        if not bool(facts.get("explicit_relaunch_authorization")):
            return _block(
                "BLOCKED_ON_SCIENTIFIC_JOB_RELAUNCH_AUTHORIZATION",
                "loss of observability does not authorize a replacement scientific run",
            )
        return {
            "allowed": True,
            "decision": "ALLOW_EXPLICIT_SCIENTIFIC_JOB_RELAUNCH",
            "reasons": ["explicit relaunch authorization is present"],
        }

    return _block(
        "BLOCKED_ON_UNSUPPORTED_PREFLIGHT_ACTION",
        f"unsupported action: {action}",
    )


def _read_facts(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("preflight facts must be a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed RemoteMCP managed execution identity/pairing preflight."
    )
    parser.add_argument(
        "facts",
        help="JSON facts file, or '-' to read JSON from stdin.",
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    args = parser.parse_args()

    try:
        result = evaluate(_read_facts(args.facts))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = _block("BLOCKED_ON_PREFLIGHT_INPUT", str(exc))

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0 if result["allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
