# Changelog

## Unreleased

### Added

- **HTTP MCP transport** — `guardian mcp --http` serves Guardian over streamable
  HTTP for a *remote* agent (stdio stays the default for same-host). Gated by a
  bearer token (`mcp.http.token_env`); it refuses to start without one, so the
  network surface is never unauthenticated. For SSO, front it with a reverse
  proxy. Default bind `127.0.0.1:8675`.

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
