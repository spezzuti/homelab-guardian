# Changelog

## Unreleased

### Added

- Split-horizon DNS validation: `dns_checks` entries take an optional
  `server:` (query that resolver directly — dependency-free A-record client,
  UDP with TCP retry on truncation) and `expected:` (assert the returned
  addresses). A resolver that answers with the wrong record is a `warning`
  with the split-horizon guidance; an unreachable resolver stays `critical`.

### Security

- Published the project threat model (`docs/threat-model.md`) and a security
  policy (`SECURITY.md`), linked from the top of the README: attack surfaces,
  the compromised-agent scenario by configuration tier, enforced invariants,
  and the honest residual-risk list.
- CI now gates every push on `bandit` (static security scan; config in
  `pyproject.toml`) and `pip-audit` (known CVEs in the runtime dependency
  tree). The audit that introduced the gate found zero true positives; the
  handful of false positives carry justified inline `# nosec` annotations.

### Changed

- CLI migrated to argparse subparsers: `guardian scan|doctor|init|serve|mcp|ack|unack|repair`
  each own their arguments and per-command `--help`. All documented
  invocations keep working, including `--config` before or after the
  subcommand and the legacy `guardian --doctor` / `guardian --interval N`
  spellings.
- Packaging: license metadata migrated to a PEP 639 SPDX expression
  (`license = "AGPL-3.0-or-later"`), removing the deprecated TOML-table form
  and license classifier that setuptools warns will break builds in 2027.
- `ROADMAP.md` rewritten to match reality (the shipped monitor → agent →
  repair arc) and the road ahead; stale done items pruned from `TASKS.md`.

## v0.3.5 — 2026-07-02

### Security

- **`guardian init` now actually writes the dashboard-auth, MCP, mounts, and
  repair answers.** The wizard collected all four and then silently dropped them
  from the generated `config.yaml` — worst case, a user answered yes to
  password-protecting the dashboard, pasted a password into `.env`, and the
  dashboard served **unauthenticated** while they believed it was protected (the
  MCP-remote and repair steps were similarly no-ops). The wiring is fixed, and a
  new end-to-end wizard test drives the prompts and asserts the *written* config
  contains every section the user said yes to. If you ran `guardian init` on
  v0.3.3/v0.3.4 and enabled any of these, re-run it (or add the sections by
  hand) — check your `config.yaml` for a `web.auth` block in particular.
- **An agent ack now *defers* the Telegram critical-fallback instead of
  cancelling it.** Previously `acknowledge_alert_received` deleted the pending
  alert, so a confused or compromised agent could claim "relayed" and disarm the
  fail-to-ack safety net on its own unverifiable say-so. An ack now buys one
  deferral window (`notifications.agent.ack_defer_minutes`, default 60; repeat
  acks don't extend it): if the check is still critical and no human has
  acknowledged it when the deferred deadline passes, the fallback fires anyway.
  Tracking clears only on the real "user is covered" signals — the check
  recovers, a human acks it (`guardian ack` / dashboard), or the fallback fires —
  and the fallback message now distinguishes "agent never acknowledged" from
  "agent relayed, but it's still critical".
- **Typed destructive confirmation is now a human-held token.** The
  `require_typed_confirmation` gate compared against the proposal id — which
  `propose_repair` had just returned to the agent, making the gate a no-op on
  the agent path. Approving a destructive proposal now mints a random token,
  stored in a column excluded from every agent-readable payload and shown only
  on the human approval surfaces (CLI approve output, dashboard notice), so the
  gate is a genuine second human touch.
- **Concurrent `execute()` calls can no longer run one approved repair twice.**
  The approved→running transition is now an atomic conditional UPDATE (the same
  single-use pattern approval already used); the losing caller is refused.
- **OIDC login is bound to the browser that began it (login-CSRF fix).** The
  `state` was tracked only server-side, so an attacker-initiated login's valid
  state+code could be replayed in a victim's browser to sign them into the
  attacker's account. `/auth/login` now sets state as a short-lived HttpOnly
  cookie that must match (constant-time) at the callback.
- **OIDC error pages escape exception text** — the one spot that interpolated
  server-derived strings into HTML unescaped.

### Fixed

- **Collector correctness sweep:** disk `percent_used` now matches `df` (the
  ~5% root reserve no longer delays the critical threshold); systemd
  backup-health timestamps are requested as real UTC (`--timestamp=utc`), fixing
  ages skewed by the server's local offset; `sshd_config` `Match` blocks are no
  longer read as global settings (false warnings / masked real ones); a
  container that exited with an application error (1–127) is **critical**, while
  signal exits (`docker stop`) stay warnings.
- **One bad target can no longer blind a whole collector:** malformed per-item
  values (port/timeout/expected_status) degrade only that check instead of
  collapsing every network target to `unknown`; a hung NFS/CIFS mount is probed
  under a thread timeout, so a stale share reads as critical instead of wedging
  the scan; non-dict Home Assistant states are guarded; a required backup past
  its critical age (3× the freshness window) escalates to critical.
- **`serve --interval` readers no longer 500 during a background scan** —
  SQLite `busy_timeout` plus WAL (best-effort; some network filesystems reject
  it) let the web threads read while the scan thread writes.

### Changed

- **The Docker SDK is an optional extra:** `pip install
  'homelab-guardian[docker]'`. The core install is dependency-light again; the
  collector degrades to a clear install-hint check without it, and the container
  image still ships the SDK.

### Internal

- CI runs **ruff + mypy** on a clean baseline; collector statuses are typed as
  `HealthStatus` literals so the check contract is enforced at type-check time.
- `run_scan` decomposed into staged, unit-testable helpers running on a single
  DB connection per cycle.
- ARCHITECTURE/PROJECT docs corrected to state the real v0.3 safety boundary
  (approval-gated repair exists; it is allowlisted argv, human-approved, never
  raw shell).

## v0.3.4 — 2026-06-24

### Fixed

- **An unreachable network target is now `critical`, not `warning`.** Previously
  every reachability failure in the `network` collector — TCP connection refused,
  HTTP no-response, DNS that won't resolve, no TLS handshake — was classified as
  `warning`; only an *expired certificate* was ever `critical`. In agent mode the
  deterministic Telegram critical-fallback is gated on a confirmed critical, so a
  host going **completely dark** produced only warnings and never tripped the
  safety net — it rode entirely on the attached agent relaying one webhook. Found
  the hard way: a main server went down, every dependent check went `ok → warning`,
  the single agent push was silently dropped, and no fallback could fire. Now a
  target Guardian cannot reach AT ALL is `critical` by default (a *degraded but
  reachable* target — unexpected HTTP status, cert merely expiring soon — stays a
  warning). Opt a noisy/optional target back down with
  `critical_on_unreachable: false` on the check.

### Documentation

- **MCP guide rewritten to be agent-agnostic.** `docs/mcp.md` now leads with the
  any-client quick start (Claude Desktop / Claude Code), documents the full
  current tool surface including the gated repair tools (`list_repair_actions` /
  `propose_repair` / `execute_repair` / `get_repair_log`) and the agent-agnostic
  approve-out-of-band repair flow, and tiers integration as **pull** (works with
  any MCP agent) vs **proactive push** (advanced, webhook-capable agents). A cold
  `pip install 'homelab-guardian[mcp]'` was verified end-to-end against a real MCP
  stdio client (15 tools registered, tools callable).

### Fixed

- **Docker `socket_url` default** — the shipped default was the malformed
  `unix://var/run/docker.sock` (two slashes). It connected anyway (the code
  prepends the missing slash for the existence check and the Docker SDK
  tolerates the rest), but it's now the canonical `unix:///var/run/docker.sock`
  in the example config, `DEFAULT_CONFIG`, and the collector/doctor fallbacks.

## v0.3.3 — 2026-06-15

### Added

- **Auto-repair escalation** — when scan-loop self-healing tries but can't
  recover a critical (the fix didn't take, or the loop guard is spent), Guardian
  tags it `escalate` and surfaces an `escalations` list to the agent. These stay
  alert-worthy, making the reflex → specialist → human hand-off explicit. Each
  escalation carries a read-only `diagnostic` (recent journal lines for a systemd
  unit, container logs for Docker) so the agent gets the *why*, not just the
  *what*.
- **`guardian init` onboards the v0.3 features** — optional wizard steps for
  attaching an agent over MCP (prints the client config / generates a token),
  password-protecting the dashboard, watching NAS mounts, and one conservative
  self-healing repair.

### Security / Changed

- **Dashboard fails closed on an unloadable auth secret.** If `web.auth.mode` is
  `oidc`/`basic` and the configured secret can't be resolved (e.g. the secrets
  vault was unreachable at startup), `guardian serve` now refuses to start
  instead of silently serving a login that can't complete. The bitwarden store
  retries transient failures with backoff before falling back to env-only, and
  `guardian doctor` preflights that auth secrets actually resolve.

### Fixed

- CI: the dashboard-auth end-to-end tests no longer depend on a `config.yaml`
  being present in the working directory (passed locally, failed in CI).

## v0.3.2 — 2026-06-14

### Added

- **`mounts` collector + `remount` repair** — verifies configured NAS/NFS/CIFS
  mountpoints are actually mounted (a dropped share is silent: the mountpoint
  dir still exists, so a disk check stays green). The `remount` playbook
  re-mounts a dropped, allowlisted mount via `sudo -n mount <path>` and verifies
  it came back.

## v0.3.1 — 2026-06-14

First release published to PyPI. (0.3.0 was tagged but never published.)

### Added

- **HTTP MCP transport** — `guardian mcp --http` serves Guardian over streamable
  HTTP for a *remote* agent (stdio stays the default for same-host). Gated by a
  bearer token (`mcp.http.token_env`); it refuses to start without one, so the
  network surface is never unauthenticated. For SSO, front it with a reverse
  proxy. Default bind `127.0.0.1:8675`.
- **Scan-loop auto-repair (self-healing)** — with a playbook's `auto_approve:
  true`, Guardian auto-proposes, executes, and verifies its repair on a
  *confirmed* (flap-damped) critical, no human in the loop — the deterministic
  reflex tier acting on its own. Loop-guarded and audited; destructive actions
  still can never auto-approve. The agent notification carries what was
  auto-handled (`auto_repaired`) so the agent narrates the fix instead of
  re-alarming.

## v0.3.0 — 2026-06-14

The "safe actuator" arc: Guardian grew from a read-only doctor into something an
AI agent can attach to and, with human approval, act through — without ever
giving the model a shell. Dogfooded live on a real homelab throughout.

### Added

- **MCP server** (`guardian mcp`) — exposes Guardian's structured health view to
  any agent over the Model Context Protocol (Claude, a local agent, ...), so the
  agent reasons over verified state instead of re-deriving it. Read tools are
  always available; optional acknowledgement *write* tools sit behind
  `mcp.allow_writes` (off by default — the tools aren't even registered).
- **Agent-delivery notifications** (`notifications.mode: agent`) — Guardian hands
  each confirmed change to an attached agent's webhook so the **agent is the
  single voice**, with a critical-fallback to Telegram over the same shared bot
  if the agent is unreachable or doesn't confirm it relayed a critical in time.
- **Approval-gated repair** (`guardian repair`, `repair.enabled`) — Guardian can
  *propose* and, after **human approval**, *execute* whitelisted, bounded repairs
  (restart a watched systemd unit or container; reclaim disk via `docker_prune` /
  `journal_vacuum` / `apt_clean` / `prune_dir`), then **verify** recovery. Never
  raw shell (argv only); targets come from validated check evidence or admin
  allowlists; every step is audited and loop-guarded. Destructive actions can
  never auto-approve, carry read-only previews ("would free ≈X"), and a cross-
  collector **backup interlock** refuses to delete user files while backups are
  not ok-and-fresh. Approve via CLI (`guardian repair approve`) or the dashboard
  `/repairs` page; an attached agent can propose/execute but **never approve**.
- **Dashboard authentication** — `web.auth.mode`: `basic` / `forward_auth` /
  `oidc` (mechanisms, not per-provider code). `/healthz` stays open.
- **Guided config edits** — a `/settings` page (auth + CSRF gated) to toggle
  collectors, with comment-preserving writes to `config.yaml`.
- **Host-hardening collectors** — `firewall`, `exposed_services`, `ssh`,
  `updates`, and `backup_health` (restic snapshot age or a systemd backup unit).
- **Group-primary dashboard** — problem groups roll up first and auto-open;
  healthy groups collapse. Calm by default, deep on demand.

### Security

- The repair feature is the safety showcase: **propose → approve → execute →
  verify**, with approval enforced by Guardian out-of-band so the LLM is never
  the authority. An independent security review confirmed the core guarantees
  and drove hardening — execute-time re-validation (closes a propose→execute
  TOCTOU), a backup-*freshness* interlock, loop-guard crash survival, and input
  bounds. `guardian doctor` self-validates the repair config (unwatched allowed
  units, risky prune paths, passwordless-ALL sudo); a no-shell invariant test
  locks the argv-only guarantee.

### Changed / Fixed

- **Calm by default** — enabled-but-unconfigured collectors stay quiet; the
  "you turned this on but didn't configure it" guidance lives in `guardian
  doctor`, not as dashboard noise.
- **Network-ready first scan** — after a reboot, the first scan waits for the
  network so it no longer flips every network/TLS check to a false warning.
- **Cross-platform fix** — POST handlers drain the request body on early errors
  (a Windows-only connection-reset bug); CI now covers Windows + Python 3.10–3.12.

## v0.2.0 — 2026-06-13

The "from scaffold to product" release. Everything below was dogfooded
against a real homelab before landing.

### Added

- **Snapshot diffing** — every report leads with what changed since the last
  scan: regressions, improvements, new and removed checks.
- **Web dashboard** (`guardian serve`) — read-only, stdlib-only, dark/light
  theme, categorized check tiles, collapsible scan history, `/healthz`.
  Appliance mode (`--interval`) scans in the background of the same process.
- **Setup wizard** (`guardian init`) — interactive config generation with
  optional LAN auto-discovery (read-only TCP probes, reverse-DNS naming,
  Google Cast false-positive filtering).
- **Telegram notifications** — flap-damped via `confirm_scans`: a status must
  hold N consecutive scans before it is announced; recoveries confirmed
  symmetrically.
- **Acknowledgment system** (`guardian ack`/`unack`) — mute chronic known
  issues with notes and optional expiry; muted checks stay visible but never
  page you.
- **AI briefing (BYOM)** — optional plain-English summary from any
  OpenAI-compatible endpoint (OpenRouter, local Ollama, ...). The model sees
  structured check data only; no shell, no tools.
- **Secrets providers** — env (default) or Bitwarden Secrets Manager via the
  bws CLI: one machine-account token instead of a pile of .env entries.
- **New collectors** — systemd (failed units AND restart loops, watched
  units), disk space (threshold alerts), TLS certificate expiry (self-signed
  supported via dependency-free DER parse).
- **Scheduling** — `--interval` loop mode plus a ready-to-edit systemd user
  service in `deploy/`.
- **Packaging** — `pip install` -> `guardian` CLI; multi-arch (amd64/arm64)
  Docker images on GHCR; `.env` autoloading; snapshot retention pruning.
- **Tests and CI** — 108 tests; GitHub Actions for tests and image publishing.

### Changed

- Package renamed from `app` to `homelab_guardian`.
- License set to AGPL-3.0-or-later.

## v0.1.0 — 2026-06-10

Initial scaffold: Daily Homelab Doctor. Optional read-only collectors
(Docker, Home Assistant, network, backup freshness), structured health-check
contract, SQLite snapshots, Markdown report, doctor preflight, Docker Compose
packaging with socket-proxy overlay.
