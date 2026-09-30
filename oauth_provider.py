"""
OAuth 2.1 Authorization Server cho MCP server cá nhân (một chủ sở hữu).

- Dynamic Client Registration (RFC 7591): client tự đăng ký, nhưng chỉ chấp nhận
  redirect_uri thuộc host trong allowlist.
- /authorize -> trang /login: CHỦ SỞ HỮU phải nhập mật khẩu và bấm Cho phép.
- PKCE (S256) do SDK kiểm tra ở /token.
- Access token 1 giờ, refresh token 30 ngày (xoay vòng), authorization code dùng 1 lần.
- Token lưu dưới dạng hash SHA-256 trong file JSON quyền 0600.
"""
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPE = "mcp"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300
PENDING_TTL = 600
MAX_CLIENTS = 50
MAX_FAILS, FAIL_WINDOW = 5, 900  # 5 lần sai / 15 phút -> khóa


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class OwnerOAuthProvider:
    def __init__(self, base_url: str, state_file: Path, owner_password: str,
                 allowed_redirect_hosts: set[str]):
        self.base_url = base_url.rstrip("/")
        self.file = state_file
        self.pw = owner_password
        self.hosts = allowed_redirect_hosts
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.access: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}
        self.codes: dict[str, AuthorizationCode] = {}      # chỉ trong RAM
        self.pending: dict[str, tuple] = {}                # chỉ trong RAM
        self.fails: list[float] = []
        self._load()

    # ---------- lưu trữ ----------
    def _load(self):
        if not self.file.exists():
            return
        d = json.loads(self.file.read_text())
        self.clients = {k: OAuthClientInformationFull.model_validate(v)
                        for k, v in d.get("clients", {}).items()}
        self.access, self.refresh = d.get("access", {}), d.get("refresh", {})

    def _save(self):
        data = {"clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
                "access": self.access, "refresh": self.refresh}
        tmp = self.file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.file)

    # ---------- client registration ----------
    async def get_client(self, client_id: str):
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if len(self.clients) >= MAX_CLIENTS:
            raise RegistrationError(error="invalid_client_metadata",
                                    error_description="Đã đạt giới hạn số client")
        for uri in client_info.redirect_uris or []:
            u = urlparse(str(uri))
            local = u.hostname in ("localhost", "127.0.0.1")
            if not ((u.scheme == "https" and u.hostname in self.hosts) or
                    (local and u.scheme == "http" and u.hostname in self.hosts)):
                raise RegistrationError(error="invalid_redirect_uri",
                                        error_description=f"redirect_uri không được phép: {u.hostname}")
        self.clients[client_info.client_id] = client_info
        self._save()

    # ---------- authorize + login ----------
    async def authorize(self, client, params: AuthorizationParams) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v[2] > now}
        req = secrets.token_urlsafe(24)
        self.pending[req] = (client, params, now + PENDING_TTL)
        return f"{self.base_url}/login?req={req}"

    def _page(self, req: str, client, error: str = "", status: int = 200) -> HTMLResponse:
        name = html.escape(client.client_name or client.client_id)
        hosts = ", ".join(html.escape(urlparse(str(u)).hostname or "") for u in client.redirect_uris)
        err = f'<p style="color:#b00">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Cho phép truy cập</title>
<body style="font-family:system-ui;max-width:420px;margin:12vh auto;padding:0 16px">
<h2>Cho phép truy cập máy của bạn?</h2>
<p>Ứng dụng <b>{name}</b> (chuyển hướng về: {hosts}) muốn dùng các công cụ file và lệnh trên máy này.</p>
{err}
<form method=post>
<input type=hidden name=req value="{html.escape(req)}">
<input type=password name=password placeholder="Mật khẩu chủ sở hữu" autofocus style="width:100%;padding:8px;margin:8px 0">
<button name=action value=allow style="padding:8px 16px">Cho phép</button>
<button name=action value=deny style="padding:8px 16px">Từ chối</button>
</form>"""
        return HTMLResponse(body, status_code=status, headers={
            "Cache-Control": "no-store", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'"})

    async def login(self, request: Request) -> Response:
        if request.method == "GET":
            req = request.query_params.get("req", "")
            entry = self.pending.get(req)
            if not entry or entry[2] < time.time():
                return HTMLResponse("Yêu cầu không hợp lệ hoặc đã hết hạn.", status_code=400)
            return self._page(req, entry[0])

        form = await request.form()
        req, action = str(form.get("req", "")), str(form.get("action", ""))
        entry = self.pending.get(req)
        if not entry or entry[2] < time.time():
            return HTMLResponse("Yêu cầu không hợp lệ hoặc đã hết hạn.", status_code=400)
        client, params, _ = entry

        if action == "deny":
            self.pending.pop(req, None)
            return RedirectResponse(construct_redirect_uri(
                str(params.redirect_uri), error="access_denied", state=params.state), status_code=302)

        now = time.time()
        self.fails = [t for t in self.fails if t > now - FAIL_WINDOW]
        if len(self.fails) >= MAX_FAILS:
            return HTMLResponse("Quá nhiều lần thử sai. Thử lại sau 15 phút.", status_code=429)
        if not hmac.compare_digest(str(form.get("password", "")).encode(), self.pw.encode()):
            self.fails.append(now)
            return self._page(req, client, "Sai mật khẩu.", status=401)

        self.pending.pop(req, None)  # dùng 1 lần
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [SCOPE], expires_at=now + CODE_TTL,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource)
        return RedirectResponse(construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state), status_code=302)

    # ---------- token ----------
    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access = {k: v for k, v in self.access.items() if v["expires_at"] > now}
        self.refresh = {k: v for k, v in self.refresh.items() if v["expires_at"] > now}
        self.access[_h(access)] = dict(client_id=client_id, scopes=scopes,
                                       expires_at=now + ACCESS_TTL, resource=resource)
        self.refresh[_h(refresh)] = dict(client_id=client_id, scopes=scopes,
                                         expires_at=now + REFRESH_TTL, resource=resource)
        self._save()
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          scope=" ".join(scopes), refresh_token=refresh)

    async def load_authorization_code(self, client, authorization_code: str):
        c = self.codes.get(authorization_code)
        if c and (c.client_id != client.client_id or c.expires_at < time.time()):
            self.codes.pop(authorization_code, None)
            return None
        return c

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode):
        self.codes.pop(authorization_code.code, None)  # dùng 1 lần
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str):
        e = self.refresh.get(_h(refresh_token))
        if not e or e["client_id"] != client.client_id or e["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, **e)

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]):
        # Newer MCP SDK RefreshToken models may not expose resource.
        # Preserve it from our stored token record while rotating the old token.
        stored = self.refresh.pop(_h(refresh_token.token), None)  # xoay vòng
        resource = getattr(refresh_token, "resource", None)
        if resource is None and stored is not None:
            resource = stored.get("resource")
        return self._issue(client.client_id, scopes or refresh_token.scopes, resource)

    async def load_access_token(self, token: str):
        e = self.access.get(_h(token))
        if not e or e["expires_at"] < time.time():
            return None
        return AccessToken(token=token, **e)

    async def revoke_token(self, token) -> None:
        cid = token.client_id  # thu hồi toàn bộ token của client đó
        self.access = {k: v for k, v in self.access.items() if v["client_id"] != cid}
        self.refresh = {k: v for k, v in self.refresh.items() if v["client_id"] != cid}
        self._save()