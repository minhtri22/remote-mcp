import asyncio
import json
from pathlib import Path

import pytest

from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

import oauth_provider


def make_client(client_id="client-1", redirect="http://localhost:9999/cb"):
    return OAuthClientInformationFull(
        client_id=client_id,
        client_name="V2-0 Test Client",
        redirect_uris=[redirect],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


def make_provider(tmp_path):
    return oauth_provider.OwnerOAuthProvider(
        base_url="http://localhost:8765",
        state_file=tmp_path / "oauth-state.json",
        owner_password="correct-horse-battery",
        allowed_redirect_hosts={"localhost", "127.0.0.1", "chatgpt.com"},
    )


def test_frozen_oauth_constants():
    assert oauth_provider.SCOPE == "mcp"
    assert oauth_provider.ACCESS_TTL == 3600
    assert oauth_provider.REFRESH_TTL == 30 * 86400
    assert oauth_provider.CODE_TTL == 300
    assert oauth_provider.PENDING_TTL == 600
    assert oauth_provider.MAX_CLIENTS == 50
    assert oauth_provider.MAX_FAILS == 5
    assert oauth_provider.FAIL_WINDOW == 900


def test_registration_accepts_allowed_localhost_and_persists(tmp_path):
    p = make_provider(tmp_path)
    client = make_client()

    asyncio.run(p.register_client(client))

    assert p.clients[client.client_id].client_name == "V2-0 Test Client"
    raw = json.loads((tmp_path / "oauth-state.json").read_text(encoding="utf-8"))
    assert client.client_id in raw["clients"]


def test_registration_rejects_disallowed_redirect_host(tmp_path):
    p = make_provider(tmp_path)
    client = make_client(redirect="https://evil.example/cb")

    with pytest.raises(RegistrationError):
        asyncio.run(p.register_client(client))


def test_issue_persists_only_hashed_tokens_and_reload_validates_access(tmp_path):
    p = make_provider(tmp_path)

    token = p._issue("client-1", ["mcp"], "http://localhost:8765/mcp")
    state_text = (tmp_path / "oauth-state.json").read_text(encoding="utf-8")

    assert token.access_token not in state_text
    assert token.refresh_token not in state_text
    assert oauth_provider._h(token.access_token) in state_text
    assert oauth_provider._h(token.refresh_token) in state_text

    reloaded = make_provider(tmp_path)
    access = asyncio.run(reloaded.load_access_token(token.access_token))

    assert access is not None
    assert access.client_id == "client-1"
    assert access.scopes == ["mcp"]
    assert str(access.resource) == "http://localhost:8765/mcp"


def test_refresh_rotation_invalidates_old_refresh_token(tmp_path):
    p = make_provider(tmp_path)
    first = p._issue("client-1", ["mcp"], "http://localhost:8765/mcp")

    loaded = asyncio.run(p.load_refresh_token(make_client(), first.refresh_token))
    assert loaded is not None

    second = asyncio.run(p.exchange_refresh_token(make_client(), loaded, ["mcp"]))
    assert second.refresh_token != first.refresh_token

    old = asyncio.run(p.load_refresh_token(make_client(), first.refresh_token))
    new = asyncio.run(p.load_refresh_token(make_client(), second.refresh_token))
    assert old is None
    assert new is not None


def test_revoke_access_token_revokes_all_tokens_for_client(tmp_path):
    p = make_provider(tmp_path)
    first = p._issue("client-1", ["mcp"], "http://localhost:8765/mcp")
    access = asyncio.run(p.load_access_token(first.access_token))
    assert access is not None

    asyncio.run(p.revoke_token(access))

    assert asyncio.run(p.load_access_token(first.access_token)) is None
    assert asyncio.run(p.load_refresh_token(make_client(), first.refresh_token)) is None


def test_codes_and_pending_are_not_persisted(tmp_path):
    p = make_provider(tmp_path)
    p.codes["secret-code"] = object()
    p.pending["pending-id"] = (object(), object(), 9999999999)
    p._save()

    state = json.loads((tmp_path / "oauth-state.json").read_text(encoding="utf-8"))
    assert "codes" not in state
    assert "pending" not in state
    assert "secret-code" not in json.dumps(state)
    assert "pending-id" not in json.dumps(state)
