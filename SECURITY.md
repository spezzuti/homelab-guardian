# Security Policy

Homelab Guardian is a monitoring tool that can optionally act on your systems
(approval-gated repairs, opt-in). We treat every mutating surface as
security-critical and document exactly what an attacker — including a
compromised AI agent — can and cannot do in the
[threat model](docs/threat-model.md).

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's
**Security → Report a vulnerability** on this repository (private vulnerability
reporting is enabled). Do not open a public issue for anything you believe is
exploitable.

You can expect an acknowledgement within a few days. Guardian is maintained by
one person; honest triage beats theater — you'll get a real answer about
whether it's a bug, how bad it is, and when a fix lands.

## Supported versions

Only the **latest release** on PyPI receives security fixes. Guardian is
pre-1.0; there are no maintenance branches.

## Security posture in one paragraph

Guardian is local-first and fail-closed. All collectors are read-only. Every
write surface is off by default and independently gated: dashboard edits
require auth + CSRF, MCP write tools require `mcp.allow_writes`, repairs
require their own `repair.enabled` plus per-playbook allowlists. Repairs
execute only named, parameterized, allowlisted actions as argv (never a shell,
never an LLM-generated command), only after a human approves that specific
proposal through a channel Guardian controls — an agent has no approve tool,
so it cannot self-authorize. Destructive actions can never auto-approve.
Privilege comes from scoped sudoers grants for exact commands, not broad sudo.
Every proposal, approval, execution, and verification is recorded append-only.

Details, enforcement points, and the honest list of residual risks:
[docs/threat-model.md](docs/threat-model.md).

## Development-practice disclosure

Guardian is developed with heavy AI assistance, reviewed and dogfooded by the
maintainer on a production homelab. We consider that worth stating plainly in
a security policy: the trust argument rests on the enforced invariants, test
suite (361 tests, 3-OS CI matrix, bandit + pip-audit gates), and the published
threat model — not on how the code was typed.
