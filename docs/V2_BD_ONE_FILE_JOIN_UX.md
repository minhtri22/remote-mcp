# V2-BD one-file Windows join UX

Status: implemented as an additive operator-UX layer. It does not change V2-BD routing, device identity, replay, binding, or no-failover semantics.

## Goal

A user adding a second Windows execution machine should not have to manually:

- install `httpx` or `cryptography`;
- copy/type a `pairing_id`;
- create a pairing-code file;
- choose runtime paths;
- start the node with a separate command;
- configure auto-start.

The owner/gateway can create either a short-lived, single-use artifact `RemoteMCP-Join.ps1` or, preferably, a short-lived one-line HTTPS join command. The one-line path avoids dependence on chat/file-download transport entirely.

## Gateway behavior

`device_pair_begin(operation_id, device_name)` keeps the existing manual fields for compatibility and additionally returns:

- `join_script_filename = RemoteMCP-Join.ps1`;
- `join_script_powershell` containing an instance-bound one-file bootstrap;
- `join_url`, an opaque-ticket HTTPS endpoint that serves the same bootstrap only while the pairing is unused and unexpired;
- `join_command`, a one-line PowerShell command (`irm <join_url> | iex`).

The script embeds the same single-use pairing bundle already authorized by `device_pair_begin`. No new enrollment authority or bypass is introduced.

The script is instance-bound through the gateway's configured `PUBLIC_URL`. The RemoteMCP source does not require `remote.threadon.xyz`; that domain is only one private deployment.

## Join-machine behavior

The generated PowerShell script:

1. finds Python 3.11+ or installs Python 3.12 through `winget` when available;
2. creates an isolated node virtual environment under `%LOCALAPPDATA%\RemoteMCP`;
3. installs the pinned node dependencies `httpx==0.28.1` and `cryptography==46.0.6`;
4. uses a local RemoteMCP source tree when the script is run from one, otherwise downloads the configured public source archive;
5. creates the user's RemoteMCP workspace and node runtime directories;
6. writes the embedded `pairing_id|pairing_code` only to a temporary local file;
7. calls `python -m remotemcp.node pair` **without a manual `--pairing-id` argument**;
8. removes the temporary pairing file;
9. runs `remotemcp.node doctor`;
10. installs a per-user hidden startup launcher;
11. starts the outbound-only node immediately;
12. prints the resulting node status.

The node Ed25519 private key is generated on the joining machine and never leaves that machine.

## Security properties preserved

- pairing remains single-use and short-lived;
- pairing still requires Ed25519 proof-of-possession;
- node private key remains local;
- no inbound node port is opened;
- no Cloudflare tunnel is created for the node;
- no research project is automatically bound;
- no automatic device migration/failover is introduced;
- the manual pairing flow remains available for diagnostics/recovery.

## Open-source deployment rule

The one-file join UX MUST remain self-hosted and instance-relative.

RemoteMCP does not require an OpenAI account, ChatGPT cookie, RemoteMCP-operated cloud service, or a centrally operated RemoteMCP domain for node enrollment.

The gateway URL is taken from that installation's `PUBLIC_URL`.

## Current packaging boundary

The script can bootstrap from the public source archive. A future packaged `RemoteMCPNode.exe` can replace the Python/source bootstrap without changing the pairing/routing protocol.
