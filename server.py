"""
Mini "Desktop Commander" bằng Python (MCP over Streamable HTTP) + OAuth 2.1.

Cài đặt:  pip install "mcp[cli]<2" uvicorn        # LƯU Ý: mcp 2.x đổi FastMCP -> MCPServer
Biến môi trường:
  PUBLIC_URL      URL công khai (https) của server, ví dụ https://mcp.tenmien.com
                  (thử local: http://localhost:8000)
  OWNER_PASSWORD  mật khẩu bạn nhập ở trang /login khi cấp quyền cho một client
  MCP_ROOT        thư mục workspace (mặc định ~/agent-workspace)
  ALLOWED_REDIRECT_HOSTS  host được phép nhận code, mặc định:
                  claude.ai,claude.com,chatgpt.com,localhost,127.0.0.1
Chạy:     python server.py      ->  connector URL: $PUBLIC_URL/mcp
"""
import asyncio
import os
import re
import shlex
from pathlib import Path

import uvicorn
from pydantic import AnyHttpUrl

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from urllib.parse import urlparse

from oauth_provider import SCOPE, OwnerOAuthProvider

import mcp.server.auth.routes as auth_routes

_original_build_metadata = auth_routes.build_metadata

def _build_metadata_chatgpt(*args, **kwargs):
    metadata = _original_build_metadata(*args, **kwargs)

    methods = list(metadata.token_endpoint_auth_methods_supported or [])
    if "none" not in methods:
        methods.append("none")

    metadata.token_endpoint_auth_methods_supported = methods
    return metadata

auth_routes.build_metadata = _build_metadata_chatgpt

ROOT = Path(os.environ.get("MCP_ROOT", "~/agent-workspace")).expanduser().resolve()
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
OWNER_PASSWORD = os.environ["OWNER_PASSWORD"]
if len(OWNER_PASSWORD) < 12:
    raise SystemExit("OWNER_PASSWORD phải dài tối thiểu 12 ký tự")
HOSTS = set(os.environ.get(
    "ALLOWED_REDIRECT_HOSTS", "claude.ai,claude.com,chatgpt.com,localhost,127.0.0.1").split(","))
ROOT.mkdir(parents=True, exist_ok=True)

# Chỉ cho phép các lệnh nằm trong allowlist (sửa theo nhu cầu)
ALLOWED_CMDS = {"ls", "cat", "grep", "rg", "git", "python", "pip", "node", "npm", "pytest"}
MAX_OUT = 20_000       # ký tự tối đa trả về cho agent
CMD_TIMEOUT = 60       # giây

provider = OwnerOAuthProvider(
    base_url=PUBLIC_URL,
    state_file=Path(os.environ.get("MCP_STATE", "~/.mcp-commander-state.json")).expanduser(),
    owner_password=OWNER_PASSWORD,
    allowed_redirect_hosts=HOSTS,
)

_host = urlparse(PUBLIC_URL).netloc
mcp = FastMCP(
    "local-commander",
    stateless_http=True,
    json_response=True,
    auth_server_provider=provider,
    auth=AuthSettings(
        # issuer_url=AnyHttpUrl(PUBLIC_URL),
        # resource_server_url=AnyHttpUrl(PUBLIC_URL + "/mcp"),
        issuer_url=PUBLIC_URL,
        resource_server_url=PUBLIC_URL + "/mcp",
        required_scopes=[SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
        validate_token_resource=True,
    ),
    # transport_security=TransportSecuritySettings(
    #     enable_dns_rebinding_protection=True,
    #     allowed_hosts=[_host, "localhost:*", "127.0.0.1:*"],
    #     allowed_origins=[PUBLIC_URL, "http://localhost:*", "http://127.0.0.1:*"],
    # ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            _host,
            f"{_host}:*",
            "localhost:*",
            "127.0.0.1:*",
        ],
        allowed_origins=[
            PUBLIC_URL,
            "https://chatgpt.com",
            "https://chat.openai.com",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    ),
)


@mcp.custom_route("/login", methods=["GET", "POST"])
async def login(request):
    return await provider.login(request)


def safe(rel: str) -> Path:
    """Chặn path traversal / symlink thoát khỏi ROOT."""
    p = (ROOT / rel).resolve()
    if not p.is_relative_to(ROOT):
        raise ValueError("Đường dẫn nằm ngoài workspace")
    return p


def clip(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n...[cắt bớt {len(s) - MAX_OUT} ký tự]"


@mcp.tool()
def list_dir(path: str = ".") -> str:
    """Liệt kê file/thư mục."""
    p = safe(path)
    return "\n".join(
        f"{'[D]' if c.is_dir() else '[F]'} {c.relative_to(ROOT)}" for c in sorted(p.iterdir())
    )


@mcp.tool()
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Đọc file text theo dòng (offset, limit)."""
    lines = safe(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return clip("\n".join(lines[offset: offset + limit]))


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Ghi đè (hoặc tạo) file."""
    p = safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Đã ghi {len(content)} ký tự vào {p.relative_to(ROOT)}"


@mcp.tool()
def edit_file(path: str, old: str, new: str) -> str:
    """Thay thế đúng 1 đoạn text duy nhất trong file (an toàn hơn ghi đè)."""
    p = safe(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        return f"Lỗi: 'old' xuất hiện {text.count(old)} lần, cần đúng 1 lần"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return "OK"


@mcp.tool()
def search(pattern: str, path: str = ".", max_hits: int = 100) -> str:
    """Tìm regex trong nội dung file."""
    rx, hits = re.compile(pattern), []
    for f in safe(path).rglob("*"):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= max_hits:
                        return "\n".join(hits)
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(hits) or "Không có kết quả"


@mcp.tool()
async def run_command(command: str) -> str:
    """Chạy lệnh (không qua shell) trong workspace, có allowlist + timeout."""
    argv = shlex.split(command)
    if not argv or argv[0] not in ALLOWED_CMDS:
        return f"Từ chối: '{argv[0] if argv else ''}' không nằm trong allowlist {sorted(ALLOWED_CMDS)}"
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={"PATH": os.environ["PATH"], "HOME": str(ROOT)},  # không lộ biến môi trường của bạn
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Timeout sau {CMD_TIMEOUT}s"
    return clip(f"[exit {proc.returncode}]\n" + out.decode(errors="replace"))



if __name__ == "__main__":
    # Chỉ bind localhost; tunnel/Tailscale Funnel đưa ra ngoài bằng HTTPS
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))