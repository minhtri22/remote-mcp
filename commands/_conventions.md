# RemoteMCP Command Conventions

Every user-facing command in this directory must be a Markdown file whose name does not begin with `_`.

Files prefixed with `_` are repository conventions/meta documentation and are not packaged as user-facing commands.

## Required sections

Each command must contain:

1. YAML frontmatter with a non-empty `description`.
2. `## Preflight` — prerequisites and safety checks.
3. `## Plan` — intended action before execution.
4. `## Commands` — exact RemoteMCP tool behavior.
5. `## Verification` — evidence required before claiming success.
6. `## Summary` — concise result format.

## Safety rules

- Prefer read-only inspection before mutation.
- Never invent or replace a device identity to recover an existing device.
- Never guess a target device when more than one candidate exists.
- Never interrupt active routed jobs without explicit user approval.
- A dead/unresponsive gateway cannot repair itself through the same MCP endpoint; use the documented out-of-band gateway recovery path.
- Do not create duplicate project/task registrations as a recovery shortcut.
- Do not claim a command succeeded without live tool evidence.

## Naming

Use short lowercase nouns or verbs:

```text
/status
/devices
/restart <device>
```

Do not add camelCase aliases such as `/deviceList`.
