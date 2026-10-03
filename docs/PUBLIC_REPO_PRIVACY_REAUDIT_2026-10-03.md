# Public Repository Privacy Re-Audit — 2026-10-03

Status: **CURRENT MAIN PASS**

Scope: tracked public source and documentation on the current `main` branch.

This audit is intentionally about the **current tree**, not historical commits.

## Public-source policy

Deployment-specific values must not be committed.

Public examples use placeholders only, including:

```text
https://mcp.example.com
<GATEWAY_PORT>
<WORKSPACE_ROOT>
<PRIVATE_STATE_FILE>
<PRIVATE_RUNTIME_DIR>
<MACHINE_A_HOST>
<MACHINE_B_HOST>
<CHATGPT_APP_ID>
<PRIVATE_PLUGIN_ID>
<CONNECTED_ACCOUNT_NAME>
```

## Re-audited sensitive categories

The current public tree was checked for:

- the private deployment domain and its parent domain;
- real Windows hostnames;
- user-profile paths;
- private work-drive paths;
- concrete device, agent, session, and owner IDs;
- concrete ChatGPT app IDs;
- concrete private plugin IDs;
- connected-account display names;
- personal email addresses;
- committed local `.env` files;
- private-key PEM material.

No indexed current-main hit was found for the known private deployment values in those categories.

## Regression guards

`tests/test_public_repo_hygiene.py` now guards against reintroducing:

- the known private deployment hostname;
- real `DESKTOP-...` hostnames;
- concrete `dev_...`, `agt_...`, `ses_...`, and `own_...` IDs;
- `C:\Users\<name>` paths;
- private `D:\...` work roots used by the deployment;
- concrete ChatGPT app IDs;
- concrete private plugin IDs;
- connected-account display-name patterns;
- non-example personal email addresses;
- committed `.env` variants;
- private-key material.

`.env.example` is placeholder-only.

## Configuration boundary

Private values belong in local ignored configuration or runtime state, never in public source.

Examples of private/local-only data:

- real `PUBLIC_URL`;
- real gateway port if deployment-specific;
- workspace/state/runtime filesystem roots;
- redirect hosts specific to one deployment;
- ChatGPT app/plugin IDs;
- OAuth state and tokens;
- owner password;
- pairing credentials;
- node private keys;
- machine names and device IDs.

## History boundary

This PASS does **not** assert that old Git history is free of values that existed before current-main sanitization.

Historical purge remains a separate maintenance operation because rewriting public history can disrupt active clones, worktrees, and agents.

Do not perform history rewrite or force-push while active workloads depend on the current commit graph.
