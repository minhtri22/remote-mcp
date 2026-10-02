# Public Repo Current-Main Sanitization and Command Layout Static QA

Status: **COMPLETE / PASS**

Gate:

`PUBLIC_REPO_CURRENT_MAIN_SANITIZATION_AND_COMMAND_LAYOUT_STATIC_QA`

Date: 2026-10-02

This is an engineering/operational QA record. It is not a scientific result and does not belong in scientific lineage.

## Scope

This gate closes two source-only changes on the public repository's current `main` branch:

1. public-source sanitization of deployment-specific metadata;
2. normalization of ChatGPT operator commands into first-class `commands/*.md` sources, including canonical `/devices`.

No production runtime, execution node, gateway process, installed plugin, project/task binding, or active routed job was modified by this gate.

## Command source layout

Canonical repository layout:

```text
commands/
├── _conventions.md
├── status.md
├── devices.md
└── restart.md
```

User-facing commands:

```text
/status
/devices
/restart <device>
```

There is no `/deviceList` alias.

The plugin builder reads command files from the repository `commands/` directory and packages the exact source text. Command Markdown is no longer embedded in the builder implementation.

Static assertions confirmed:

- all three user-facing commands contain Preflight / Plan / Commands / Verification / Summary sections;
- `/devices` is read-only and uses `device_list`;
- `commands/_conventions.md` is repository metadata and is not packaged as a user-facing command;
- package tests require `commands/devices.md`;
- package tests compare packaged command content byte-for-text with the repository source;
- README, User Guide and ChatGPT Plugin Setup document `/devices`.

## Public repository sanitization

Current-main source was normalized so deployment-specific values are not required in public tracked files.

Private non-secret deployment metadata is represented through a local gitignored `.env` file. The public repository contains only `.env.example` with generic placeholders.

Examples of local-only metadata:

```text
PUBLIC_URL
PORT
MCP_ROOT
MCP_STATE
MCP_RUNTIME_DIR
ALLOWED_REDIRECT_HOSTS
REMOTEMCP_PILOT_HOST_A
REMOTEMCP_PILOT_HOST_B
REMOTEMCP_QUAL_WORKSPACE
REMOTEMCP_QUAL_LLAMA_CLI
```

`OWNER_PASSWORD` is intentionally excluded from the `.env` contract. Windows gateway configuration persists it separately using DPAPI.

Current-main hygiene checks cover:

- private deployment hostname;
- physical Windows hostnames;
- concrete device/agent/session/owner identifiers;
- user-specific profile paths;
- committed `.env` files;
- private-key PEM material.

Known deployment-specific literals audited during this gate returned no hits on the current default branch after sanitization.

## Regression guard

`tests/test_public_repo_hygiene.py` protects the public tree against reintroducing known private deployment metadata and secret material.

The private deployment hostname is represented in the test only by its SHA-256 digest, so the hygiene test itself does not publish the hostname in plaintext.

## Runtime non-interference

This gate intentionally performed no:

- production deployment;
- gateway restart;
- execution-node restart;
- plugin refresh/reinstall;
- device pair/revoke;
- project/task registration or rebind;
- routed job mutation;
- force-push or Git history rewrite.

Therefore active agents remain on their existing runtime/plugin state.

## History boundary

Sanitizing current `main` does not erase literals that may exist in older public Git objects.

History rewrite is explicitly deferred while other agents may depend on existing commit identities, clones, worktrees, or branches.

The next history operation is allowed only after all active agents are stopped or an explicit maintenance window is opened:

`PUBLIC_REPO_GIT_HISTORY_PRIVATE_METADATA_PURGE_AND_FRESH_CLONE_VERIFICATION`

That future gate must treat history rewrite and force-push as disruptive operations and must include fresh-clone verification before closure.
