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
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from pydantic import AnyHttpUrl

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from urllib.parse import urlparse

from oauth_provider import SCOPE, OwnerOAuthProvider
from remotemcp.durable.config import DurableConfig, safe_child_env
from remotemcp.durable.process import windows_process_options
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService
from remotemcp.routing.config import RoutingConfig
from remotemcp.routing.service import RoutingService
from remotemcp.routing.http import register_device_routes

import mcp.server.auth.routes as auth_routes


def _load_local_deployment_env(path: Path) -> None:
    allowed={
        "PUBLIC_URL","PORT","MCP_ROOT","MCP_STATE","MCP_RUNTIME_DIR",
        "ALLOWED_REDIRECT_HOSTS",
    }
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line=raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key,value=line.split("=",1)
        key=key.strip()
        if key not in allowed or key in os.environ:
            continue
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in {"'", '"'}:
            value=value[1:-1]
        os.environ[key]=value


_load_local_deployment_env(Path(__file__).resolve().parent/".env")

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

DURABLE_CONFIG = DurableConfig.from_env(ROOT)
durable_service = DurableService(DURABLE_CONFIG)
MULTIAGENT_CONFIG = MultiAgentConfig.from_durable(DURABLE_CONFIG)
multiagent_service = MultiAgentService(MULTIAGENT_CONFIG, durable_service)
ROUTING_CONFIG = RoutingConfig.from_env(DURABLE_CONFIG.runtime_dir, PUBLIC_URL)
routing_service = RoutingService(ROUTING_CONFIG, durable_service, multiagent_service)


@asynccontextmanager
async def durable_lifespan(_app):
    await durable_service.start()
    await multiagent_service.start()
    await routing_service.start()
    try:
        yield {}
    finally:
        await routing_service.stop()
        await multiagent_service.stop()
        await durable_service.stop()


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
    lifespan=durable_lifespan,
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


register_device_routes(mcp, routing_service)


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
    multiagent_service.guard.guard_file_mutation(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Đã ghi {len(content)} ký tự vào {p.relative_to(ROOT)}"


@mcp.tool()
def edit_file(path: str, old: str, new: str) -> str:
    """Thay thế đúng 1 đoạn text duy nhất trong file (an toàn hơn ghi đè)."""
    p = safe(path)
    multiagent_service.guard.guard_file_mutation(p)
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
    multiagent_service.guard.guard_run_command()
    argv = shlex.split(command)
    if not argv or argv[0] not in ALLOWED_CMDS:
        return f"Từ chối: '{argv[0] if argv else ''}' không nằm trong allowlist {sorted(ALLOWED_CMDS)}"
    creationflags, startupinfo = windows_process_options()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=safe_child_env(ROOT),  # allowlist tối thiểu; không kế thừa toàn bộ server env
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Timeout sau {CMD_TIMEOUT}s"
    return clip(f"[exit {proc.returncode}]\n" + out.decode(errors="replace"))


@mcp.tool()
async def job_submit(
    operation_id: str,
    argv: list[str],
    cwd: str = ".",
    agent_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> dict:
    """Submit a durable, idempotent long-running job."""
    multiagent_service.guard.guard_job_submit()
    return await durable_service.job_submit(
        operation_id,
        argv,
        cwd,
        agent_id,
        project_id,
        task_id,
    )


@mcp.tool()
def job_get(job_id: str) -> dict:
    """Return durable job status."""
    routing_service.guard_local_job_id(job_id)
    return durable_service.job_get(job_id)


@mcp.tool()
async def job_wait(
    job_id: str,
    subscriber_id: str,
    mode: str = "terminal",
    after_event_id: int = 0,
    timeout_seconds: int = 55,
) -> dict:
    """Bounded wait for terminal/heartbeat/progress state."""
    routing_service.guard_local_job_id(job_id)
    return await durable_service.job_wait(
        job_id,
        subscriber_id,
        mode,
        after_event_id,
        timeout_seconds,
    )


@mcp.tool()
def job_logs(
    job_id: str,
    stream: str = "stdout",
    cursor: int = 0,
    limit_bytes: int = 65536,
) -> dict:
    """Read bounded raw-log bytes by cursor."""
    routing_service.guard_local_job_id(job_id)
    return durable_service.job_logs(job_id, stream, cursor, limit_bytes)


@mcp.tool()
def job_result(
    job_id: str,
    subscriber_id: str = "",
    ack_event_id: int = 0,
    operation_id: str = "",
) -> dict:
    """Return normalized result and optionally ACK terminal event idempotently."""
    routing_service.guard_local_job_id(job_id)
    return durable_service.job_result(
        job_id,
        subscriber_id,
        ack_event_id,
        operation_id,
    )


@mcp.tool()
async def job_cancel(
    operation_id: str,
    job_id: str,
    agent_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> dict:
    """Request idempotent, ownership-verified cancellation."""
    routing_service.guard_local_job_id(job_id)
    multiagent_service.guard.guard_job_cancel(job_id, durable_service.db)
    return await durable_service.job_cancel(
        operation_id,
        job_id,
        agent_id,
        project_id,
        task_id,
    )


@mcp.tool()
async def agent_register(
    operation_id: str,
    agent_name: str,
    client_instance_id: str,
    capabilities: list[str] = [],
) -> dict:
    return await multiagent_service.agent_register(
        operation_id, agent_name, client_instance_id, capabilities
    )


@mcp.tool()
def agent_heartbeat(
    agent_id: str,
    session_id: str,
    heartbeat_seq: int,
) -> dict:
    return multiagent_service.agent_heartbeat(
        agent_id, session_id, heartbeat_seq
    )


@mcp.tool()
async def session_close(
    operation_id: str,
    agent_id: str,
    session_id: str,
) -> dict:
    return await multiagent_service.session_close(
        operation_id, agent_id, session_id
    )


@mcp.tool()
async def project_register(
    operation_id: str,
    path: str,
    max_active_tasks: int = 4,
) -> dict:
    return await routing_service.project_register_or_local(
        operation_id, path, max_active_tasks
    )


@mcp.tool()
def project_status(project_id: str) -> dict:
    return routing_service.project_status_or_local(project_id)


@mcp.tool()
async def task_create(
    operation_id: str,
    project_id: str,
    title: str,
    base_ref: str = "HEAD",
) -> dict:
    return await routing_service.task_create_or_local(
        operation_id, project_id, title, base_ref
    )


@mcp.tool()
async def task_claim(
    operation_id: str,
    task_id: str,
    agent_id: str,
    session_id: str,
) -> dict:
    return await routing_service.task_claim_or_local(
        operation_id, task_id, agent_id, session_id
    )


@mcp.tool()
def task_status(task_id: str) -> dict:
    return routing_service.task_status_or_local(task_id)


@mcp.tool()
async def task_checkpoint(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    summary: str,
    metadata: dict = {},
) -> dict:
    return await multiagent_service.task_checkpoint(
        operation_id, task_id, lease_token, lease_epoch, summary, metadata
    )


@mcp.tool()
async def task_block(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    reason: str,
) -> dict:
    return await multiagent_service.task_block(
        operation_id, task_id, lease_token, lease_epoch, reason
    )


@mcp.tool()
async def task_set_ready(
    operation_id: str,
    task_id: str,
    reason: str = "",
) -> dict:
    return await multiagent_service.task_set_ready(
        operation_id, task_id, reason
    )


@mcp.tool()
async def task_release(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
) -> dict:
    return await multiagent_service.task_release(
        operation_id, task_id, lease_token, lease_epoch
    )


@mcp.tool()
async def task_complete(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    outcome_summary: str,
) -> dict:
    return await routing_service.task_complete_or_local(
        operation_id, task_id, lease_token, lease_epoch, outcome_summary
    )


@mcp.tool()
async def path_lease_acquire(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    path: str,
    scope: str = "FILE",
) -> dict:
    return await multiagent_service.path_lease_acquire(
        operation_id, task_id, lease_token, lease_epoch, path, scope
    )


@mcp.tool()
async def path_lease_release(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    path_lease_id: str,
) -> dict:
    return await multiagent_service.path_lease_release(
        operation_id, task_id, lease_token, lease_epoch, path_lease_id
    )


@mcp.tool()
async def file_write_cas(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    path: str,
    expected_sha256: str,
    content: str,
) -> dict:
    return await routing_service.file_write_cas_or_local(
        operation_id, task_id, lease_token, lease_epoch,
        path, expected_sha256, content
    )


@mcp.tool()
async def file_edit_cas(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    path: str,
    expected_sha256: str,
    old: str,
    new: str,
    expected_occurrences: int = 1,
) -> dict:
    return await routing_service.file_edit_cas_or_local(
        operation_id, task_id, lease_token, lease_epoch,
        path, expected_sha256, old, new, expected_occurrences
    )


@mcp.tool()
async def task_job_submit(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    argv: list[str],
    cwd: str = ".",
) -> dict:
    return await routing_service.task_job_submit_or_local(
        operation_id, task_id, lease_token, lease_epoch, argv, cwd
    )


@mcp.tool()
async def task_job_submit_with_evidence(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    argv: list[str],
    evidence_paths: list[str],
    cwd: str = ".",
) -> dict:
    """Submit a routed job with exact evidence files declared before execution."""
    return await routing_service.task_job_submit_or_local(
        operation_id, task_id, lease_token, lease_epoch, argv, cwd, evidence_paths
    )


@mcp.tool()
def task_jobs(task_id: str) -> dict:
    return routing_service.task_jobs_or_local(task_id)


@mcp.tool()
async def task_job_cancel(
    operation_id: str,
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    job_id: str,
) -> dict:
    return await routing_service.task_job_cancel_or_local(
        operation_id, task_id, lease_token, lease_epoch, job_id
    )


@mcp.tool()
async def device_pair_begin(
    operation_id: str,
    device_name: str,
) -> dict:
    return await routing_service.device_pair_begin(operation_id, device_name)


@mcp.tool()
def device_list() -> dict:
    return routing_service.device_list()


@mcp.tool()
def device_status(device_id: str) -> dict:
    return routing_service.device_status(device_id)


@mcp.tool()
async def device_revoke(
    operation_id: str,
    device_id: str,
    reason: str = "",
) -> dict:
    return await routing_service.device_revoke(operation_id, device_id, reason)


@mcp.tool()
async def device_restart(
    operation_id: str,
    device_id: str,
    reason: str = "user-requested",
    allow_active_jobs: bool = False,
) -> dict:
    return await routing_service.device_restart(
        operation_id, device_id, reason, allow_active_jobs
    )


@mcp.tool()
async def project_register_on_device(
    operation_id: str,
    device_id: str,
    path: str,
    max_active_tasks: int = 4,
) -> dict:
    return await routing_service.project_register_on_device(
        operation_id, device_id, path, max_active_tasks
    )


@mcp.tool()
async def project_bind_device(
    operation_id: str,
    project_id: str,
    device_id: str,
) -> dict:
    return await routing_service.project_bind_device(
        operation_id, project_id, device_id
    )


@mcp.tool()
async def task_list_dir(
    task_id: str,
    path: str = ".",
) -> dict:
    return await routing_service.task_list_dir(task_id, path)


@mcp.tool()
async def task_read_file(
    task_id: str,
    path: str,
    offset: int = 0,
    limit: int = 120000,
) -> dict:
    return await routing_service.task_read_file(task_id, path, offset, limit)


@mcp.tool()
async def task_search(
    task_id: str,
    pattern: str,
    path: str = ".",
) -> dict:
    return await routing_service.task_search(task_id, pattern, path)


@mcp.tool()
def task_job_recovery_status(
    task_id: str,
    proxy_job_id: str,
) -> dict:
    """Classify same-proxy pre-node recovery without launching replacement science."""
    return routing_service.task_job_recovery_status(task_id, proxy_job_id)


@mcp.tool()
async def task_job_recover_path_escape(
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    proxy_job_id: str,
    expected_original_argv_sha256: str,
    acknowledge_cwd_semantics_preserved: bool = False,
) -> dict:
    """Recover a pre-execution PATH_ESCAPE submit on the same proxy."""
    return await routing_service.task_job_recover_path_escape(
        task_id,
        lease_token,
        lease_epoch,
        proxy_job_id,
        expected_original_argv_sha256,
        acknowledge_cwd_semantics_preserved,
    )


@mcp.tool()
async def task_job_recover_expired_submit(
    task_id: str,
    lease_token: str,
    lease_epoch: int,
    proxy_job_id: str,
    expected_original_argv_sha256: str,
) -> dict:
    """Retry the same routed submit only when its original command expired before first delivery."""
    return await routing_service.task_job_recover_expired_submit(
        task_id,
        lease_token,
        lease_epoch,
        proxy_job_id,
        expected_original_argv_sha256,
    )


@mcp.tool()
async def task_job_artifact_status(
    task_id: str,
    proxy_job_id: str,
    path: str,
) -> dict:
    """Check declared post-run scientific evidence without rerunning science."""
    return await routing_service.task_job_artifact_status(
        task_id, proxy_job_id, path
    )


@mcp.tool()
async def task_job_artifact_read(
    task_id: str,
    proxy_job_id: str,
    path: str,
    expected_sha256: str,
    offset: int = 0,
    limit: int = 120000,
) -> dict:
    """Read a predeclared terminal evidence file pinned by SHA-256."""
    return await routing_service.task_job_artifact_read(
        task_id, proxy_job_id, path, expected_sha256, offset, limit
    )


@mcp.tool()
async def task_job_get(
    task_id: str,
    proxy_job_id: str,
    refresh: bool = True,
) -> dict:
    return await routing_service.task_job_get(task_id, proxy_job_id, refresh)


@mcp.tool()
async def task_job_logs(
    task_id: str,
    proxy_job_id: str,
    stream: str = "stdout",
    cursor: int = 0,
    max_bytes: int = 65536,
) -> dict:
    return await routing_service.task_job_logs(
        task_id, proxy_job_id, stream, cursor, max_bytes
    )


@mcp.tool()
async def task_job_result(
    task_id: str,
    proxy_job_id: str,
) -> dict:
    return await routing_service.task_job_result(task_id, proxy_job_id)


if __name__ == "__main__":
    # Chỉ bind localhost; tunnel/Tailscale Funnel đưa ra ngoài bằng HTTPS
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))