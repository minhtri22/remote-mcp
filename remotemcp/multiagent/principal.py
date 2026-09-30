from __future__ import annotations

import json
import secrets
from pathlib import Path

from mcp.server.auth.middleware.auth_context import get_access_token

from remotemcp.durable.errors import DurableError
from remotemcp.durable.process import atomic_write_json


class OwnerIdentity:
    def __init__(self, runtime_dir: Path, db=None, auth_client_resolver=None):
        self.runtime_dir=runtime_dir.resolve()
        self.path=self.runtime_dir/"owner-account.json"
        self.db=db
        self._resolver=auth_client_resolver
        self.owner_account_id=self._load_or_create()

    def _db_owner_ids(self)->list[str]:
        if self.db is None:
            return []
        try:
            return [str(r["owner_account_id"]) for r in self.db.query_all("SELECT owner_account_id FROM owner_accounts")]
        except Exception:
            return []

    def _load_or_create(self)->str:
        self.runtime_dir.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text(encoding="utf-8"))
                value=str(data["owner_account_id"])
            except Exception as exc:
                raise DurableError("STARTUP_FATAL_OWNER_ACCOUNT","invalid owner-account.json") from exc
            if not value.startswith("own_"):
                raise DurableError("STARTUP_FATAL_OWNER_ACCOUNT","invalid owner_account_id")
            db_ids=self._db_owner_ids()
            if len(db_ids)>1 or (db_ids and value not in db_ids):
                raise DurableError("STARTUP_FATAL_OWNER_ACCOUNT","owner-account.json disagrees with persisted owner namespace")
            return value
        db_ids=self._db_owner_ids()
        if db_ids:
            raise DurableError(
                "STARTUP_FATAL_OWNER_ACCOUNT",
                "owner-account.json is missing while persisted owner state exists",
            )
        value="own_"+secrets.token_hex(16)
        atomic_write_json(self.path,{"version":1,"owner_account_id":value})
        return value

    def current_auth_client_id(self)->str:
        if self._resolver is not None:
            value=self._resolver()
            if value:
                return str(value)
        token=get_access_token()
        if token is None or not getattr(token,"client_id",None):
            raise DurableError("AUTH_REQUIRED","authenticated MCP access token required")
        return str(token.client_id)