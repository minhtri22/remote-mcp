"""Fail-closed, opt-in gateway protection for four *historical* commands.

Not a reconciliation tool. Never updates or repairs the old command, proxy or
node job. Enabled only when an operator provides a separate SHA-pinned,
Ed25519-signed PRIVATE manifest via three matching environment variables.
The private manifest is NOT source controlled and must not contain secrets.

No production activation or installation is authorized by this code commit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from .crypto import canonical_json

SCHEMA = "remotemcp.v31.expired-four-private-isolation.v1"
FROZEN_INVENTORY_SHA256 = "9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85"
CATEGORIES = {"EXPIRED_DELIVERED_NO_RECEIPT": 3, "EXPIRED_NODE_RECEIVED_NONTERMINAL": 1}
PROTECTED_TYPES = frozenset({"JOB_SUBMIT", "JOB_GET", "PROJECT_PROBE", "TASK_BASE_RESOLVE"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CMDID = re.compile(r"^cmd_[0-9a-f]{32}$")


def _hold(message: str) -> None:
    raise DurableError("HISTORICAL_ISOLATION_INVALID_HOLD", message)


def _bytes_b64(s: str, count: int) -> bytes:
    try:
        decoded = base64.b64decode(s, validate=True)
    except (ValueError, TypeError) as exc:
        raise DurableError("HISTORICAL_ISOLATION_INVALID_HOLD", "invalid base64 identity") from exc
    if len(decoded) != count:
        _hold("invalid signature or signing public key length")
    return decoded


class ExpiredFourIsolation:
    """Immutable, offline-only decision layer. The gateway DB remains authoritative."""

    def __init__(self, private_manifest: dict, *, expected_digest: str = FROZEN_INVENTORY_SHA256):
        if private_manifest.get("schema") != SCHEMA:
            _hold("unknown private isolation schema")
        inventory = private_manifest.get("inventory_rows")
        targets = private_manifest.get("protected_commands")
        if not isinstance(inventory, list) or len(inventory) != 19:
            _hold("frozen 19-command inventory required")
        if not isinstance(targets, list) or len(targets) != 4:
            _hold("exactly four protected bindings required")
        digest = hashlib.sha256(json.dumps(
            inventory, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if (digest != expected_digest or
                private_manifest.get("frozen_inventory_sha256") != expected_digest):
            _hold("private inventory hash differs from frozen evidence")
        indexed = {r.get("command_id"): r for r in inventory if isinstance(r, dict)}
        if len(indexed) != 19:
            _hold("inventory duplicate or malformed identifiers")
        selected = {cid: r for cid, r in indexed.items() if r.get("classification") in CATEGORIES}
        counts = Counter(r.get("classification") for r in selected.values())
        if counts != CATEGORIES or len(selected) != 4:
            _hold("frozen four-command classification/count drift")
        if {r.get("command_type") for r in selected.values()} != PROTECTED_TYPES:
            _hold("unexpected command types")
        ids: dict[str, dict] = {}
        aliases: set[tuple[str, int]] = set()
        proxys: set[str] = set()
        device = private_manifest.get("device_id")
        if not isinstance(device, str) or not device.startswith("dev_") or len(device) != 36:
            _hold("invalid device id")
        if any(r.get("command_id") != cid for cid, r in indexed.items()):
            _hold("mismatched source identifiers")
        for t in targets:
            if not isinstance(t, dict):
                _hold("invalid protected command binding")
            cid = t.get("command_id")
            if not isinstance(cid, str) or not CMDID.fullmatch(cid) or cid not in selected or cid in ids:
                _hold("protected ID not in frozen class or duplicated")
            r = selected[cid]
            if any(t.get(k) != r.get(k) for k in (
                "command_id", "command_type", "request_hash", "route_generation",
                "command_expires_at_ms", "lease_expires_at_ms",
            )):
                _hold("protected binding inconsistent with frozen inventory")
            if not isinstance(t.get("request_hash"), str) or not HEX64.fullmatch(t["request_hash"]):
                _hold("invalid request hash")
            if not isinstance(t.get("route_generation"), int) or t["route_generation"] < 1:
                _hold("invalid route generation")
            if not isinstance(t.get("command_expires_at_ms"), int) or not isinstance(t.get("lease_expires_at_ms"), int):
                _hold("expiry evidence missing")
            if t["command_expires_at_ms"] > now_ms() or t["lease_expires_at_ms"] > now_ms():
                _hold("a protected command/lease is not yet expired")
            alias=t.get("operation_id")
            step=t.get("operation_step")
            if alias is None:
                if step not in (None, 0):
                    _hold("operation step without operation id")
            elif not isinstance(alias, str) or not alias or not isinstance(step, int) or step < 0:
                _hold("invalid operation alias")
            else:
                if (alias, step) in aliases:
                    _hold("duplicate protected operation binding")
                aliases.add((alias, step))
            proxy=t.get("proxy_job_id")
            if t["command_type"]=="JOB_SUBMIT":
                if not isinstance(proxy,str) or not proxy.startswith("rjob_"):
                    _hold("missing job submit proxy identity")
                proxys.add(proxy)
            elif proxy is not None:
                _hold("non-submit command cannot bind job proxy")
            ids[cid]=t
        if set(ids)!=set(selected):
            _hold("protected identifiers do not exactly match frozen four")
        self.device_id=device
        self.ids=tuple(sorted(ids))
        self.bindings=ids
        self.aliases=frozenset(aliases)
        self.proxys=frozenset(proxys)

    @classmethod
    def load_signed(cls, file_path: Path, expected_sha256: str, public_key_b64: str,\n                    *, expected_inventory_digest: str = FROZEN_INVENTORY_SHA256):
        if not HEX64.fullmatch(expected_sha256):
            _hold("missing exact manifest SHA-256 pin")
        try:
            raw=Path(file_path).read_bytes()
        except OSError as exc:
            raise DurableError("HISTORICAL_ISOLATION_INVALID_HOLD", "missing private manifest") from exc
        if hashlib.sha256(raw).hexdigest()!=expected_sha256:
            _hold("private manifest bytes differ from pinned SHA")
        try:
            manifest=json.loads(raw.decode("utf-8"))
            if not isinstance(manifest, dict):
                _hold("manifest must be an object")
            signature=_bytes_b64(manifest["signature_b64"], 64)
            key=Ed25519PublicKey.from_public_bytes(_bytes_b64(public_key_b64, 32))
            unsigned={k:v for k,v in manifest.items() if k!="signature_b64"}
            key.verify(signature,canonical_json(unsigned).encode("utf-8"))
        except (KeyError, ValueError, TypeError, UnicodeError) as exc:
            raise DurableError("HISTORICAL_ISOLATION_INVALID_HOLD", "malformed signed manifest") from exc
        except Exception as exc:
            if isinstance(exc, DurableError):
                raise
            raise DurableError("HISTORICAL_ISOLATION_INVALID_HOLD", "signature verification failed") from exc
        return cls(manifest, expected_digest=expected_inventory_digest)

    def assert_bound(self, con, device_id: str) -> None:
        """Read-only identity check performed before ANY applicable SQL mutation."""
        if device_id!=self.device_id:
            return
        for cid in self.ids:
            row=con.execute(
                "SELECT command_id,device_id,route_generation,command_type,"
                "request_hash,operation_id,operation_step,command_expires_at_ms,"
                "lease_expires_at_ms,payload_json FROM device_commands WHERE command_id=?",
                (cid,),
            ).fetchone()
            t=self.bindings[cid]
            if row is None:
                _hold("protected command disappeared")
            if (row["device_id"]!=self.device_id or
                    any(row[k]!=t[k] for k in (
                        "command_id", "route_generation", "command_type",
                        "request_hash", "operation_id", "operation_step",
                        "command_expires_at_ms", "lease_expires_at_ms"))):
                _hold("protected gateway identity drift")
            if t["command_type"]=="JOB_SUBMIT":
                try:
                    proxy=json.loads(row["payload_json"]).get("proxy_job_id")
                except (ValueError, TypeError, AttributeError):
                    _hold("protected job payload unreadable")
                if proxy!=t["proxy_job_id"]:
                    _hold("protected job proxy alias drift")

    def before_create(self, con, device_id: str, generation: int,
                      command_type: str, payload: dict, request_hash: str,
                      operation_id: str | None, operation_step: int) -> None:
        if device_id!=self.device_id:
            return
        self.assert_bound(con,device_id)
        if operation_id is not None:
            if (operation_id, int(operation_step)) in self.aliases:
                raise DurableError("HISTORICAL_COMMAND_PROTECTED", "protected operation cannot be revived or replaced")
            if any(alias==operation_id for alias, _ in self.aliases):
                raise DurableError("AMBIGUOUS_REPLACEMENT_HOLD", "protected operation cannot be continued with a different step")
        if command_type in {"JOB_SUBMIT","JOB_RECOVER_ROUTED_JOB"}:
            if isinstance(payload,dict) and payload.get("proxy_job_id") in self.proxys:
                raise DurableError("HISTORICAL_COMMAND_PROTECTED","protected job proxy cannot be recovered or resubmitted")
        # Unscoped identical replay has no independent operation identity.
        if operation_id is None and command_type in PROTECTED_TYPES and any(
            t["command_type"]==command_type
            and t["route_generation"]==generation
            and t["request_hash"]==request_hash
            for t in self.bindings.values()
        ):
            raise DurableError("AMBIGUOUS_REPLACEMENT_HOLD","unscoped identical historical command")
        # An *independent* operation_id does not become protected just because
        # a read-only payload matches; normal admission rules still apply.

    def exclude_clause(self, *, column: str = "command_id") -> tuple[str, tuple[str, ...]]:
        if column != "command_id":
            raise ValueError("UNSUPPORTED_COLUMN")
        return " AND command_id NOT IN ("+",".join("?" for _ in self.ids)+")", self.ids
