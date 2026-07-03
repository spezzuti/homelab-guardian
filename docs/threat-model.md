# Threat model

Guardian monitors a homelab and — only if you enable it — repairs it. This
document is the honest map of its attack surfaces, what enforces each
guarantee, and what an attacker gets at each opt-in level. The scenario it is
written around is the one that matters for an AI-attached actuator: **assume
the attached agent is fully compromised** (prompt-injected, confused, or
malicious) and ask what it can actually do.

Related: [SECURITY.md](../SECURITY.md) (reporting),
[docs/repair.md](repair.md) (repair safety design),
[docs/auth.md](auth.md) (dashboard auth mechanisms),
[docs/mcp.md](mcp.md) (agent integration).

## Philosophy

- **Fail closed.** Every mutating surface is off by default and gated
  independently. An unconfigured Guardian can observe and report, nothing else.
- **Deterministic authority.** Guardian decides what is alert-worthy and what
  may execute. The LLM proposes and narrates; it is never the authority.
- **No shell, ever.** Anything Guardian runs is a fixed argv list built from
  validated, allowlisted parameters. There is no code path that passes a
  string to a shell (`shell=True` is absent from the package — enforced by
  `tests/test_repair_safety.py`).

## Surfaces

### 1. Scan core (always on)

Collectors are read-only: they query APIs (Home Assistant, Docker socket),
read system state (`systemctl`, `ss`, `/proc`-adjacent tools), and probe the
network (DNS/TCP/TLS/HTTP). Subprocess use is argv-only with timeouts. The
scan writes to a local SQLite snapshot and a markdown report. Risk here is
confined to what the service user can already read.

### 2. Web dashboard (`guardian serve`)

- Binds `127.0.0.1` by default (`main.py`); binding wider is an explicit flag,
  and an unauthenticated all-interfaces bind prints a warning.
- Auth modes (`web.auth.mode`): `none` (default; the two write surfaces below
  are then read-only), `basic` (constant-time compares), `forward_auth`
  (identity headers trusted **only** from `trusted_proxies` source IPs —
  spoof-resistant by construction), `oidc` (auth-code + PKCE, state checked
  constant-time, back-channel token exchange, fail-closed if the client
  secret cannot be loaded: the server refuses to start rather than serve a
  broken login).
- Write surfaces: `/settings` (collector toggles; comment-preserving,
  validated, atomic config edits) and `/repairs` (approve/deny only — the web
  page **never executes** a repair). Both require auth to be enabled and a
  per-user HMAC CSRF token on every POST.
- `/healthz` is deliberately unauthenticated and returns only `ok`.

### 3. MCP — stdio and HTTP

- **stdio** (`guardian mcp`): process trust; whoever can spawn the process
  already has the service user's rights. No network surface.
- **HTTP** (`guardian mcp --http`): refuses to start without a bearer token
  (a tokenless network MCP surface cannot be misconfigured into existence),
  default-binds `127.0.0.1:8675`, compares tokens with
  `hmac.compare_digest`. The token is a single static secret: rotate it if
  exposed, and front the port with your SSO reverse proxy for anything
  beyond localhost.
- Tool gating is capability-tiered (see the compromised-agent table below):
  read tools always; ack writes only with `mcp.allow_writes`; repair tools
  only with `repair.enabled`. **There is no approve tool at any tier.**

### 4. Outbound notifications

- Telegram: outbound HTTPS only; the bot token comes from env/secrets
  provider, never config files.
- Agent webhook (`notifications.mode: agent`): payloads are HMAC-signed
  (`X-Hub-Signature-256`, GitHub-style) so the receiving gateway can verify
  origin. Delivery failure of a critical triggers the deterministic Telegram
  fallback; a critical accepted but never acknowledged by the agent triggers
  the fallback after `ack_timeout_minutes` — a down or silently-broken agent
  cannot swallow a critical.

### 5. Secrets

Env-first with an optional Bitwarden Secrets (`bws`) provider. Transient
provider failures are retried with backoff; auth-critical secrets are
fail-closed (OIDC refuses to start without its client secret). `guardian
doctor` preflights secret resolution so misconfiguration is loud, not silent.
Secrets never land in the SQLite snapshot, reports, or MCP payloads.

### 6. Repair execution (the sharp surface)

The full design is [docs/repair.md](repair.md). Enforcement summary:

- Registry allowlist: only named playbooks exist; no generic "run command."
- Target allowlists per playbook (`allowed_units`, `allowed_containers`,
  `allowed_paths`) — re-validated **at execute time**, not just at propose
  (a config change between propose and execute cannot widen the blast
  radius; TOCTOU-checked, and a proposal whose argv no longer matches its
  re-resolved plan is refused).
- Propose → **human approve** (dashboard/CLI — never MCP) → execute →
  verify. Proposals are single-use and time-bound.
- Privilege: scoped sudoers grants for the exact argv (one line per allowed
  unit), or the user bus where no elevation is needed. `guardian doctor`
  warns when it detects a passwordless-ALL sudo configuration instead.
- Risk tiers: `destructive` playbooks can **never** auto-approve (enforced in
  `execute`, not just config), carry preview ("would free ≈4.2 GB / 1,203
  files"), preconditions (user-data deletion requires a verified fresh
  backup — fail-closed without one), optional typed confirmation held by the
  human, and filesystem binding (a reclaim that would free a different
  filesystem than the failing one is refused).
- Loop guard (`max_attempts_per_hour`, crash-surviving via a `running` row
  written before action), per-action timeout, and mandatory post-repair
  verification — a repair that didn't recover the check is reported
  **failed**, never success.
- Append-only audit of every proposal/approval/execution/verification with
  who/what/when/result.

## The compromised-agent scenario

What a fully hostile attached agent gets, by configuration tier:

| Configuration | Attacker capability | Bounded by |
|---|---|---|
| Defaults (read-only MCP) | Read health summaries, check evidence, scan history | Data exposure only; no mutation paths exist |
| + `mcp.allow_writes` | Acknowledge (mute) checks, incl. a real critical | Ack expiry; acks are listed on the dashboard and via `list_acknowledgments`; audit trail. **Accepted residual risk** — enabling ack-writes means trusting the agent with alert hygiene |
| + `repair.enabled` | Propose any allowlisted repair; execute *approved* ones | No approve tool exists; human approval out-of-band; allowlists + argv-only + scoped sudo bound each action; destructive never auto-approves |
| + `auto_approve` on a playbook | Trigger that reflex by inducing its failure condition | Only non-destructive playbooks are eligible; loop guard; verification; escalation surfaces the pattern to the human |

Prompt injection **via collector evidence** (a hostile container named
`; rm -rf /`) is handled structurally: parameters are validated against
allowlists and passed as single argv elements — a malicious name fails the
allowlist check, and even an allowlisted-but-weird name cannot break out of
its argv slot (regression-tested in `tests/test_repair_safety.py`).

## Residual risks, stated plainly

1. **Ack-muting by a write-enabled agent** (above) — the price of delegated
   alert hygiene. Mitigations listed; not eliminated.
2. **The MCP HTTP bearer token is one static secret.** Rotation is manual.
   Front it with SSO for remote use.
3. **Dashboard `mode: none` on a LAN** exposes read-only health data to
   anyone on that network. Guardian warns but allows it — homelab reality.
4. **Local trust:** the SQLite DB, config, and reports are only as protected
   as the host account that owns them. Guardian assumes the box itself is
   yours.
5. **Supply chain:** two runtime dependencies (PyYAML, requests), audited by
   `pip-audit` in CI; optional extras (`mcp`, `docker`) widen the tree when
   you opt in.
6. **Solo maintainer, AI-assisted development** — disclosed in
   [SECURITY.md](../SECURITY.md); the guarantees above rest on enforced
   invariants and tests, not authorship.

## Verification tooling

- `bandit` (config in `pyproject.toml`) and `pip-audit` run on every CI push;
  the audit that introduced this gate found **zero true positives** (the
  skips and inline `# nosec` annotations each carry their justification).
- 361 tests across ubuntu + windows × Python 3.10–3.12, including the
  no-shell invariant, auth gating end-to-end over real HTTP, CSRF rejection,
  repair refuse-unapproved, and destructive-never-auto enforcement.
- `guardian doctor` preflights the live posture: unresolvable auth secrets
  are CRITICAL, over-broad sudo and unwatched allowlisted units are warned.
