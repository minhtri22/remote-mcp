# V2-0 Baseline Freeze

Status: PASS
Scope: freeze current behavior without changing runtime semantics.

## Frozen runtime surface

Current MCP tools:

- `list_dir(path=".")`
- `read_file(path, offset=0, limit=500)`
- `write_file(path, content)`
- `edit_file(path, old, new)`
- `search(pattern, path=".", max_hits=100)`
- `run_command(command)`

Current runtime constants:

- `MAX_OUT = 20_000`
- `CMD_TIMEOUT = 60`
- command allowlist:
  - `ls`
  - `cat`
  - `grep`
  - `rg`
  - `git`
  - `python`
  - `pip`
  - `node`
  - `npm`
  - `pytest`

## Frozen workspace containment

`safe(rel)` resolves `ROOT / rel` and rejects any resolved path that is not relative to `ROOT`.

This includes parent traversal and, when the operating system permits symlink creation, symlink escapes.

## Frozen file semantics

- `read_file` is UTF-8 with replacement and line slicing.
- `write_file` overwrites/creates UTF-8 text and creates parent directories.
- `edit_file` mutates only when `old` occurs exactly once.
- `search` recursively scans files, skips paths containing `.git`, and stops at `max_hits`.
- `clip` limits returned output to `MAX_OUT`.

## Frozen command semantics

`run_command`:

- parses with `shlex.split`;
- rejects a command whose executable is outside `ALLOWED_CMDS`;
- runs with `cwd=ROOT`;
- captures stderr into stdout;
- kills the child when `CMD_TIMEOUT` expires;
- returns clipped combined output;
- currently replaces the child environment with exactly:
  - `PATH`
  - `HOME=ROOT`

The minimal subprocess environment is a known V2-0 defect on Windows. Python networking/asyncio can fail without `SystemRoot/WINDIR`, and Git network operations have also failed from this environment. V2-0 records this behavior; it does not repair it.

## Frozen OAuth/security baseline

- scope: `mcp`
- access token TTL: 3600 s
- refresh token TTL: 30 days
- authorization code TTL: 300 s
- pending authorization TTL: 600 s
- max clients: 50
- failed owner-password limit: 5 per 900 s
- dynamic client registration validates redirect hosts
- access/refresh tokens are persisted only by SHA-256 hash
- authorization codes and pending login requests are RAM-only
- refresh tokens rotate
- revoke removes access + refresh tokens for the client
- server requires owner password length >= 12
- transport binds to localhost; HTTPS exposure is expected through a tunnel

## QA layers

1. Static/runtime contract tests in `tests/test_baseline_contract.py`.
2. OAuth provider unit tests in `tests/test_oauth_provider_unit.py`.
3. Existing manual full OAuth flow in `test_oauth_flow.py`.
4. Optional public endpoint smoke via `scripts/v2_0_public_smoke.py`.

## V2-0 rule

No behavior change is authorized in this phase. Any defect discovered here is documented and deferred to V2-A or a later explicitly opened repair step.

## Final V2-0 verdict

Verdict: **PASS**

Closed: 2026-09-30.

Final evidence:

- regression/provider suite: **17 passed**;
- isolated full OAuth flow: **PASS**;
- public OAuth metadata + protected-resource metadata + unauthenticated `/mcp` Bearer challenge: **PASS**;
- `git diff --check`: **PASS**.

The existing full OAuth test fixture was corrected to request the protected MCP resource explicitly (`resource=<base>/mcp`) because the frozen server has `validate_token_resource=True`. This is a test-harness correction and does not change runtime semantics.

Observed defects intentionally left unchanged in V2-0:

1. child-process environment is too narrow for reliable Windows networking/asyncio;
2. `run_command` is synchronous/request-bound and kills work after 60 seconds;
3. mutation replay has no idempotency key;
4. no durable job/process reconciliation exists;
5. no multi-agent coordination or context-resume layer exists.

V2-0 is formally closed. The only authorized next implementation step is:

`REMOTE_MCP_V2A_DURABLE_JOB_AND_OPERATION_PRELOCK`.