# remote-mcp

RemoteMCP is a local MCP server for securely exposing a bounded workspace to authorized AI clients.

The current baseline provides OAuth-protected filesystem/search tools and allowlisted command execution. The v2 program evolves this into a durable multi-agent runtime with idempotent retry, persistent long-running jobs, task/lease coordination, Git worktree isolation, an append-only journal, and compact resume context.

## Current baseline

- Python
- MCP over Streamable HTTP
- OAuth 2.1-style authorization flow with PKCE
- workspace root containment
- redirect-host allowlist
- allowlisted command execution

Current tools:

- `list_dir`
- `read_file`
- `write_file`
- `edit_file`
- `search`
- `run_command`

## v2 direction

See [docs/REMOTE_MCP_V2_PLAN.md](docs/REMOTE_MCP_V2_PLAN.md).

Milestones:

1. **V2-0 Baseline freeze** — regression/security harness.
2. **V2-A Durability** — idempotent operations, SQLite WAL journal, durable jobs, retry/recovery.
3. **V2-B Multi-agent** — identities, tasks, leases, queues, Git worktree isolation.
4. **V2-C Context Broker** — manifests, hashes, cached summaries, bounded resume packages.
5. **V2-D MCP Tasks integration** — native `io.modelcontextprotocol/tasks` when supported, with `job_*` fallback.

## Long-running jobs

The v2 design does not keep one HTTP tool call alive for a multi-minute or multi-hour process.

Instead:

```text
job_submit
  -> durable job_id
  -> supervised process

job_get / job_wait / job_logs
  -> status/progress

job_result
  -> terminal result
```

This allows llama/Ollama/evaluation jobs to continue through browser disconnects or OAuth reconnects.

## Security

Do not commit passwords, OAuth state files, tokens, job environment secrets, or generated worktrees/logs.

This project is intended for a bounded local workspace exposed through an authenticated tunnel.

## Project operating protocol

For this repository:

- Every work/report turn ends with an explicit **Bước tiếp theo** naming the next valid step.
- After every major phase (V2-0, V2-A, V2-B, V2-C, V2-D), this README must be updated with the phase verdict, delivered capability, current limitations, and next authorized phase before work proceeds.