# Homelab Guardian

[![PyPI](https://img.shields.io/pypi/v/homelab-guardian.svg)](https://pypi.org/project/homelab-guardian/)
[![CI](https://github.com/spezzuti/homelab-guardian/actions/workflows/ci.yml/badge.svg)](https://github.com/spezzuti/homelab-guardian/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/homelab-guardian.svg)](https://pypi.org/project/homelab-guardian/)

**The safety layer between an AI agent and root.**

Giving an AI agent SSH access to your homelab works right up until it doesn't.
The usual answers are to trust the model's judgment, or to wrap dangerous
operations in a `confirm: true` argument that the model itself fills in. Neither
is a safety boundary. Both put a language model on the authority path to your
infrastructure.

Guardian is the boundary. It gives any [MCP](https://modelcontextprotocol.io)-capable
agent **eyes** — verified, structured health state it doesn't have to re-derive —
and **hands** — approval-gated, allowlisted, audited repairs — and never a shell.

## The actuation contract

Four rules hold everywhere Guardian can touch a system. They are the product;
the monitoring is the substrate that makes them possible.

1. **Never raw shell, never an AI-generated command.** Every action is a named,
   whitelisted, parameterized `argv` list. Targets come from validated check
   evidence or an admin allowlist. There is no generic "run this" playbook, and
   there never will be — it's an explicit [non-goal](ROADMAP.md).
2. **The model is never the authority.** An agent may *propose* a repair and may
   *execute* an approved one. Approval is human-only — `guardian repair approve`
   or the dashboard's `/repairs` page. Destructive actions can never
   auto-approve, by any path.
3. **Read-only until you say otherwise.** MCP write tools are *not registered*
   when `mcp.allow_writes` is false, which is the default. An agent attached to
   a default Guardian cannot mutate state — not "is asked not to," cannot.
4. **Everything is evidence, and everything is audited.** Every check emits
   `status / summary / evidence / recommended_action`. Every proposal, approval,
   execution, and verification lands in an append-only audit log, loop-guarded
   and interlocked against running backups.

The loop those rules produce:

```
detect  →  agent diagnoses and narrates  →  human approves  →
Guardian executes named argv  →  Guardian verifies recovery  →  audit
        ^                                                  |
        |__________________ still broken? escalate ________|
```

Guardian is the deterministic half. The agent narrates, reasons, and handles the
judgment-heavy fixes Guardian deliberately won't attempt.

**Security:** the safety story is documented up front, not in an appendix — see
the [threat model](docs/threat-model.md) for exactly what a *compromised* agent
can and cannot do through Guardian, and [SECURITY.md](SECURITY.md) for reporting.

## How you get there

Guardian started as a read-only "Daily Homelab Doctor" and grew into a safe
actuator. Every step is opt-in and off until you turn it on; each one degrades
gracefully. Start at 1 and go as far as you trust it.

1. **Monitor** — read-only collectors (Docker, Home Assistant, network and TLS,
   disks, mounts, systemd, plus host-hardening checks: firewall, SSH, exposed
   services, pending updates, backup health) feed the
   `status / summary / evidence / recommended_action` contract.
2. **Report** — local Markdown reports and a read-only web dashboard, each
   leading with *what changed* since the last scan; optional flap-damped
   Telegram notifications; an optional bring-your-own-model AI briefing.
3. **Attach an agent** — `guardian mcp` serves Guardian's *verified* state over
   MCP; agent-delivery mode makes the agent the single voice, with a
   deterministic Telegram fallback for criticals the agent never acknowledges.
4. **Self-heal, carefully** — `guardian repair` proposes a whitelisted fix,
   executes it **only after a human approves**, then verifies recovery. An
   opt-in auto-approve tier exists for vetted, non-destructive reflexes.

It is also useful with no agent at all. Plenty of tools can tell you a service
is down; Guardian tells you what changed, what it means, and what the safest
next step is — in plain English.

**Install:** `pip install homelab-guardian` (add `[mcp]` for the agent server,
`[docker]` for the Docker collector's SDK).
New here? See **[the getting-started walkthrough](docs/getting-started.md)**.

## Core principles

- **Never raw shell, never an AI-generated command** — named, parameterized argv
  only; the model is never the authority
- Any action is opt-in, whitelisted, reversible-minded, and **human-approved** —
  Guardian proposes; a person approves; Guardian verifies
- Local-first; read-only against homelab infrastructure by default
- No cloud dependency required, and useful without AI
- Secrets stay local
- Every integration is optional; collectors degrade gracefully when unavailable
  or unconfigured

Guardian does write its own local runtime state: Markdown reports, SQLite scan
snapshots, acknowledgments, alert state, and retention cleanup. Optional
integrations may send outbound requests for Telegram notifications or AI
briefings when explicitly enabled.

## Quick start

Guardian is on PyPI. Install it (a virtualenv or `pipx` keeps it isolated), then
let the wizard write a working config and run your first scan:

```bash
pip install homelab-guardian          # or: pipx install homelab-guardian

guardian init      # answer a few questions; optionally scans your LAN
guardian doctor    # preflight check — verifies config, secrets, reachability
guardian           # first scan -> reports/latest.md
guardian serve     # read-only web view at http://localhost:8674
```

That's the whole loop: install, generate a config, scan, look. Open
`reports/latest.md` in your editor or browse the dashboard.

To attach an AI agent over MCP, install the optional extra instead:

```bash
pip install 'homelab-guardian[mcp]'   # pulls the MCP SDK; enables `guardian mcp`
```

`guardian init` can probe your local network (read-only TCP connects, nothing
is sent to any device) and recognizes common homelab services — Home
Assistant, Proxmox, Pi-hole/AdGuard, Portainer, Plex, Jellyfin, Synology,
QNAP, Uptime Kuma, and more — then writes a working `config.yaml` for you.
Smart-speaker false positives (Google Cast devices) are fingerprinted and
filtered out automatically. On a Linux host it also offers the zero-config
host-hardening checks (firewall, SSH, exposed services, pending updates).

Ready to go further than monitoring? The
**[From monitoring to self-healing](#from-monitoring-to-self-healing)** walkthrough
below takes you from a first scan to an agent reading Guardian over MCP to a
human-approved repair.

## Manual first-run steps

Prefer to write the config yourself? Start from the example instead of the wizard:

```bash
curl -O https://raw.githubusercontent.com/spezzuti/homelab-guardian/main/config.example.yaml
cp config.example.yaml config.yaml
mkdir -p data reports
```

Edit `config.yaml` locally. Do not commit it. (`config.example.yaml` documents
the full config surface — every collector, secrets, MCP, repair, notifications.)

For Docker inventory, enable the Docker collector in `config.yaml` only on a Docker host or when using the socket proxy overlay:

```yaml
collectors:
  docker:
    enabled: true
    socket_url: unix://var/run/docker.sock
    exclude_containers:
      - "homelab-guardian*"
```

## Install from source (development)

To hack on Guardian, install the checkout in editable mode:

```bash
git clone https://github.com/spezzuti/homelab-guardian
cd homelab-guardian
python -m venv .venv
. .venv/bin/activate
pip install -e '.[mcp,docker,dev]'    # editable, with the MCP + Docker extras and pytest

guardian --config config.yaml          # same CLI as the installed package
guardian --config config.example.yaml  # safe example run, no private services
guardian doctor --config config.yaml   # preflight check
```

The console command and `python -m homelab_guardian.main` are equivalent;
`guardian doctor` and `guardian --doctor` are the same preflight.

## Prebuilt Docker image

Multi-arch images (amd64, arm64 — Raspberry Pi friendly) are published to
GitHub Container Registry on every main-branch push:

```bash
docker pull ghcr.io/spezzuti/homelab-guardian:latest
```

Notes for containerized runs:

- The systemd collector needs the host's systemd; run Guardian directly on
  the host if you want service monitoring.
- The Bitwarden secrets provider needs the `bws` CLI, which is not in the
  image; in containers, inject secrets as environment variables instead
  (env_file or `bws run -- docker compose ...`).

## Docker Compose run

Preferred install path on a Docker host:

```bash
cp config.example.yaml config.yaml
mkdir -p data reports
docker compose run --rm homelab-guardian
```

The default Compose file mounts:

- `./config.yaml:/app/config.yaml:ro`
- `./data:/app/data`
- `./reports:/app/reports`
- `/var/run/docker.sock:/var/run/docker.sock:ro`

Guardian writes:

- report: `./reports/latest.md`
- SQLite snapshots: `./data/guardian.sqlite`

Inspect the latest report:

```bash
sed -n '1,220p' reports/latest.md
```

Or open `reports/latest.md` in your editor.

## Docker socket warning

Mounting `/var/run/docker.sock` matters because the Docker collector must ask the Docker daemon for container metadata: status, health, restart count, ports, mounts, volumes, and Compose labels.

The Docker socket is powerful. Even when mounted `:ro`, the Docker API can expose sensitive host/container metadata, and socket access is often equivalent to broad control of Docker. Guardian only performs read-oriented SDK calls, but the socket itself should still be treated as privileged.

If `/var/run/docker.sock` is missing:

- You are probably not on a Docker host, or
- Guardian is running in a container without the socket mounted, or
- Docker Desktop / rootless Docker uses a different socket path.

Safest next step:

1. Run `guardian doctor --config config.yaml`.
2. Confirm the host actually runs Docker.
3. If running in Docker Compose, confirm the socket mount exists.
4. If you do not want Docker inventory on this machine, disable `collectors.docker.enabled`.

## Safer socket proxy mode

A safer alternative to direct socket mounting is the optional socket proxy Compose file:

```bash
docker compose -f docker-compose.socket-proxy.yml run --rm homelab-guardian
```

This starts `docker-socket-proxy` and sets:

```text
DOCKER_HOST=tcp://docker-socket-proxy:2375
```

The proxy exposes only selected read-oriented Docker API areas where possible and keeps write methods disabled. This reduces blast radius compared with mounting the raw socket directly into Guardian. It is still Docker daemon access, so use it intentionally.

## Configuration

Start from `config.example.yaml`. Do not commit `config.yaml`, `.env`, API tokens, SSH keys, databases, generated reports, or machine-specific credentials.

Home Assistant access is read-only and uses an environment variable for the token. The example Compose files load local `.env` values into the container and pass `HOMEASSISTANT_TOKEN` through to Guardian.

```bash
cp .env.example .env
# Edit .env locally. Never commit it.
```

## Deployment modes

### Run directly on a Docker host

Install Python and run Guardian on the same host that runs Docker. Enable the Docker collector only if `/var/run/docker.sock` exists and the user running Guardian can read Docker metadata.

### Run via Docker Compose with Docker socket mounted

Run Guardian as a one-shot container with local `config.yaml`, `data`, and `reports` bind mounts. This is the preferred MVP install path for Docker hosts.

### Run via Docker Compose with socket proxy

Use `docker-compose.socket-proxy.yml` to route Docker SDK calls through `docker-socket-proxy` instead of giving Guardian the raw socket.

### Run without Docker

Guardian is still useful without Docker. Leave the Docker collector disabled and use any combination of:

- DNS checks
- TCP checks
- HTTP checks
- local backup path checks
- Home Assistant API checks

### Future: remote collectors

Future versions may support remote collectors for Docker hosts, NAS systems, Home Assistant, and backup locations. The current MVP is local-only: paths and sockets are evaluated from the machine or container running Guardian.

## Current collectors

### Docker collector

Disabled by default because Docker socket access is sensitive. When enabled, it reads container metadata and reports:

- container name
- image
- status
- health status
- restart count
- exposed/published ports
- mounts, bind paths, and named volumes
- Docker Compose project/service labels

Exited, unhealthy, restarting, or dead containers are surfaced as warnings or critical checks. If Docker is enabled but unavailable, the report shows `unknown` with the likely cause and safest next step instead of crashing.

Guardian can exclude containers by name pattern. This is useful for ignoring Guardian's own one-shot runtime containers and its socket proxy:

```yaml
collectors:
  docker:
    exclude_containers:
      - "homelab-guardian*"
```

Docker Compose container names in this setup normally use hyphens, not underscores, so prefer `homelab-guardian*` for Guardian runtime exclusions. Excluded containers are skipped from normal container health checks. The Docker inventory summary still reports how many were excluded and which patterns were used.

### Home Assistant collector

Disabled by default. When configured with a URL and token environment variable, it performs a read-only `GET /api/states` request and reports unavailable or unknown entities. It does not call services and does not modify Home Assistant.

Safe setup for local dogfood:

1. In Home Assistant, create a long-lived access token from your user profile.
2. Copy `.env.example` to `.env` and put the token there:

   ```env
   HOMEASSISTANT_TOKEN=your-token-here
   ```

3. In the ignored local `config.yaml`, set the Home Assistant URL and enable the collector:

   ```yaml
   collectors:
     homeassistant:
       enabled: true
       url: "http://homeassistant.local:8123"
       token_env: "HOMEASSISTANT_TOKEN"
   ```

4. Run a report:

   ```bash
   guardian --config config.yaml
   ```

5. If running through Docker Compose, use the same ignored `.env` file and `config.yaml`:

   ```bash
   docker compose run --rm homelab-guardian
   ```

Never commit `.env`, `config.yaml`, reports, databases, tokens, or machine-specific credentials.

### Network collector

Supports:

- DNS resolution checks
- TCP port checks
- HTTP status checks
- TLS certificate expiry checks (works for self-signed certificates too)

Failures include clear evidence such as hostname, port, expected status, actual status, timeout, and error text.

### Backup freshness collector

Checks configured local paths without modifying them. It reports:

- whether the path exists
- latest modified file timestamp
- backup age in hours and days
- warning if the newest file is older than `max_age_days`
- critical if a required path is missing
- critical if a required directory exists but contains no files
- unknown if an optional directory exists but contains no files
- unknown if backup checks are enabled but no paths are configured yet

If `backups.enabled` is true and `paths: []`, Guardian reports `unknown` because the check is not ready to evaluate anything. That means configuration is incomplete, not that a backup failed. Add backup paths when ready, or set `backups.enabled: false` until backup monitoring is part of your rollout.

Backup paths are local to the machine or container running Guardian. If Guardian runs in Docker, mount backup locations read-only into the container first.

When the configured path is a file, Guardian uses that file's modified time. When the configured path is a directory, Guardian recursively scans files inside the directory and uses the newest file modified time. Directory modified times are ignored because they can change for reasons that do not prove a backup file is fresh.

### Safe backup freshness dogfood

Use a dummy local folder before pointing Guardian at real backup destinations. Do not test against production backup paths until the dummy procedure behaves as expected.

```bash
mkdir -p /tmp/homelab-guardian-backup-dogfood
printf 'dummy backup marker\n' > /tmp/homelab-guardian-backup-dogfood/backup-marker.txt
cp config.example.yaml config.yaml
```

In the ignored local `config.yaml`, set only the dummy path:

```yaml
collectors:
  backups:
    enabled: true
    paths:
      - id: dummy_backup_dogfood
        name: Dummy backup dogfood path
        path: /tmp/homelab-guardian-backup-dogfood
        max_age_days: 1
        required: true
```

Then run:

```bash
guardian --config config.yaml
```

Expected result: the dummy backup check reports `ok` while the marker file is fresh. To test stale behavior safely, change `max_age_days` to `0` or adjust only files inside `/tmp/homelab-guardian-backup-dogfood`. Never commit `config.yaml`, generated reports, database files, or the dummy runtime folder.

### Disk space and mounts collectors

The `disks` collector reads usage on configured mounts (or the drive Guardian
runs on when no `paths` are set) with warning/critical percent thresholds —
disk-full is the most common silent homelab failure. The `mounts` collector
verifies configured NAS/NFS/CIFS mountpoints are *actually* mounted: a dropped
share is silent, because the mountpoint directory still exists and a disk check
stays green. Read-only via `os.path.ismount`; it pairs with the `remount` repair.

### systemd collector

Sweeps the system (and optionally user) service manager for failed units and
units stuck in a restart loop — the `activating/auto-restart` state that never
reaches "failed" and hides exactly the breakage that matters. Specific units can
be watched individually with state and restart-count evidence. Run Guardian
directly on the host (a container cannot see the host's systemd).

### Host-hardening collectors

Read-only posture checks for the Linux host Guardian runs on. None need root and
all are off by default:

- **`firewall`** — is a host firewall active and defaulting to deny inbound?
- **`exposed_services`** — names services listening on a non-loopback address,
  flagging sensitive ones (SMB, VNC, databases, ...). Reads `ss` only.
- **`ssh`** — reads the effective sshd config and warns on password
  authentication or direct root login.
- **`updates`** — pending updates, pending *security* updates, and whether a
  reboot is required (apt-based hosts for now).
- **`backup_health`** — watches the backup *job*, not just a directory: restic
  snapshot age, or a systemd backup unit's last run. Distinct from the `backups`
  freshness collector above, which watches a path.

`guardian init` offers to enable the four zero-config ones when it detects it's
running on the host you want to watch.

## Web view

```bash
guardian serve                          # http://localhost:8674
guardian serve --interval 900           # appliance mode: scan + serve in one process
guardian serve --host 0.0.0.0           # expose on your LAN (explicit choice)
```

A read-only page rendered from local scan history: overall status, the AI
briefing when enabled, what changed, every check with its evidence, and
recent scan history with per-scan drill-down. Problem groups roll up first and
auto-open; healthy groups collapse — calm by default, deep on demand. No web
framework, no JavaScript on the health view; binds to localhost unless you say
otherwise. `/healthz` returns plain `ok` so other monitors can watch Guardian
itself.

Two opt-in, authenticated control surfaces exist when enabled: a `/settings`
page to toggle collectors (comment-preserving writes to `config.yaml`) and a
`/repairs` page to approve/deny repair proposals. Both sit behind auth + CSRF.
Add authentication before exposing the dashboard — `web.auth.mode` supports
`basic`, `forward_auth`, and `oidc`. See [docs/auth.md](docs/auth.md).

## Recurring scans

Guardian runs once by default. For continuous monitoring, pass `--interval`:

```bash
guardian --config config.yaml --interval 900
```

This repeats the scan every 900 seconds. A failed scan is logged and the loop
continues. Each scan is compared against the previous snapshot, so the report
and any notifications highlight what changed.

For a host install, `deploy/homelab-guardian.service` is a ready-to-edit
systemd user service. For Docker Compose, run the service with `--interval`
and `restart: unless-stopped` instead of one-shot `docker compose run`.

## Secrets providers

Every credential Guardian uses (Home Assistant token, Telegram bot token, AI
API key) is referenced by name in `config.yaml` and resolved through a secrets
provider. Tokens never live in the config file.

- `provider: env` (default) — names are environment variables, typically from
  a local `.env` file. No extra tooling required.
- `provider: bitwarden` — names are secret keys in
  [Bitwarden Secrets Manager](https://bitwarden.com/products/secrets-manager/),
  fetched through the `bws` CLI with a single machine-account access token
  (`BWS_ACCESS_TOKEN` in the environment). One token instead of a pile of
  `.env` entries, secrets stay centrally managed and rotatable, and
  environment variables still override when set. If the provider is
  unavailable, Guardian warns once and degrades to environment-only — a
  secrets backend outage never breaks a scan.

```yaml
secrets:
  provider: bitwarden
  bitwarden:
    access_token_env: "BWS_ACCESS_TOKEN"
    project_id: "" # optional: restrict to one project
```

`guardian doctor` verifies the provider end-to-end and reports how
many secrets are readable. The provider interface is intentionally small, so
additional backends (Vault, 1Password, Infisical) can be added without
touching collectors.

Alternative: `bws run -- guardian --config config.yaml` injects all
secrets as process environment variables without any Guardian configuration.

## AI briefing — bring your own model

Optional and disabled by default. When `ai.enabled` is true, Guardian sends
only the structured check results and the what-changed diff to a single
OpenAI-compatible chat completions endpoint and places the returned
plain-English briefing at the top of the report.

- Works with OpenRouter, a local Ollama/LM Studio endpoint, or any other
  OpenAI-compatible server — your model, your key, your choice.
- The model receives structured JSON only. It has no shell, no tools, and no
  access to your systems, and the prompt forbids suggesting state-changing
  commands.
- Guardian remains fully functional with this disabled; a failed model call
  never fails the scan.

```yaml
ai:
  enabled: true
  base_url: "http://localhost:11434/v1" # local Ollama example
  model: "qwen3:14b"
  api_key_env: "GUARDIAN_AI_API_KEY" # leave the env var unset for keyless local endpoints
```

Note: each config file should point at its own `database_path`. Scan diffing
compares against the previous snapshot in that database, so two configs
sharing one database will see each other's checks as added/removed noise.

## Attach an AI agent (MCP + agent-mode)

Guardian's collectors and the `status/summary/evidence/recommended_action`
contract are the moat; you can hand that view to any model.

- **`guardian mcp`** serves Guardian over the [Model Context Protocol](https://modelcontextprotocol.io)
  so an agent (Claude, a local agent, ...) reads your *verified* homelab state
  instead of re-deriving it. Read-only by default; optional gated write tools.
  See [docs/mcp.md](docs/mcp.md).
- **Agent-delivery mode** (`notifications.mode: agent`) makes Guardian feed each
  confirmed change to the agent's webhook so the **agent is the single voice**,
  with a deterministic Telegram fallback for criticals if the agent is
  unreachable.

The division of labor: Guardian is the deterministic source of truth and the
"reflex" actuator; the agent narrates, reasons, and handles the deep, judgment-
heavy fixes Guardian deliberately won't.

## Approval-gated repair

Optional, off by default (`repair.enabled`). Guardian can *propose* a fix for a
detected problem and, **after a human approves it**, *execute* it and verify
recovery — closing the detect → diagnose → propose → approve → repair → verify
loop. The whole point is to do this safely:

- **Never raw shell.** Only named, whitelisted, parameterized actions, built as
  argv lists. Targets come from validated check evidence or admin allowlists.
- **The agent is never the authority.** It can propose and execute, but approval
  is human-only (CLI `guardian repair approve`, or the dashboard `/repairs`
  page). Destructive actions can never auto-approve.
- Built-ins: restart a watched systemd unit or container; reclaim disk
  (`docker_prune` / `journal_vacuum` / `apt_clean` / `prune_dir`, with read-only
  previews and a backup interlock). Everything is audited and loop-guarded.

Design and threat model: [docs/repair.md](docs/repair.md) and
[docs/repair-reclaim.md](docs/repair-reclaim.md).

## From monitoring to self-healing

A four-step path from a read-only doctor to a careful self-healer. Each step is
opt-in and stands on its own — stop at any rung you're comfortable with. For the
full step-by-step version with explanations, see
[docs/getting-started.md](docs/getting-started.md).

### 1. Monitor — enable a few collectors

Start with the read-only collectors that fit your setup. Everything here only
*reads*; nothing is changed on any host.

```yaml
# config.yaml — a focused starting set
collectors:
  disks:
    enabled: true       # disk-full is the #1 silent homelab failure
    paths: []           # empty = the drive Guardian runs on
  systemd:
    enabled: true       # failed units AND restart loops
    units:
      - unit: my-backup.service
  network:
    enabled: true
    http_checks:
      - { id: http_ha, name: Home Assistant, url: "http://homeassistant.local:8123", expected_status: [200, 301, 302, 401] }
```

```bash
guardian doctor    # confirm the config is valid and targets are reachable
guardian           # scan -> reports/latest.md
```

### 2. See it — the dashboard

```bash
guardian serve                 # http://localhost:8674 (localhost only)
guardian serve --interval 900  # appliance mode: scan every 15 min + serve
```

The page is read-only and leads with overall status, *what changed*, and every
check with its evidence. Bind it to localhost (default) until you've added auth
(`web.auth.mode`, see [docs/auth.md](docs/auth.md)); use `--host 0.0.0.0` only as
an explicit choice once it's protected.

### 3. Attach an agent over MCP

Hand your *verified* state to an AI agent so it reasons over real checks instead
of re-deriving them. Read-only by default — the agent can read, not change.

```bash
pip install 'homelab-guardian[mcp]'
guardian mcp --config /path/to/config.yaml   # stdio; the agent launches this
```

Point a client at the stdio command (Claude Desktop / Claude Code example):

```json
{
  "mcpServers": {
    "homelab-guardian": {
      "command": "guardian",
      "args": ["mcp", "--config", "/path/to/config.yaml"]
    }
  }
}
```

Now ask the agent *"is my homelab healthy?"* and it answers from Guardian's
checks. For a remote agent, `guardian mcp --http` serves the same over a
bearer-token-gated HTTP transport. Depth, the full tool surface, and
agent-delivery notifications: [docs/mcp.md](docs/mcp.md).

### 4. Turn on a gated repair for one safe unit

Pick the *single* safest repair you have — restarting one watched systemd unit —
and gate it behind human approval. Guardian will only ever restart units you
explicitly allowlist, and only after you approve each proposal.

```yaml
repair:
  enabled: true                 # master switch
  require_approval: true        # human approves every proposal (default)
  playbooks:
    restart_systemd_unit:
      enabled: true
      allowed_units: [my-backup.service]   # explicit allowlist; empty = nothing
      auto_approve: false
      max_attempts_per_hour: 3
```

```bash
guardian doctor    # self-validates the repair config (allowlists, sudo scope)

# when my-backup.service is failing:
guardian repair list my-backup.service            # what repairs apply?
guardian repair propose my-backup.service restart_systemd_unit
guardian repair approve <proposal_id>             # the human-only gate
guardian repair execute <proposal_id>             # runs, then verifies recovery
```

`propose` is a dry run: it shows the exact argv, blast radius, and verify step
and changes nothing. The agent (over MCP) can propose and execute, but **never
approve** — approval lives only in Guardian's CLI/dashboard. System units need a
*scoped* sudoers grant for that exact argv; prefer `systemctl --user` units,
which need none. Full safety model: [docs/repair.md](docs/repair.md).

### 5 (optional). Self-healing — auto-approve a vetted action

Once you trust a *non-destructive* repair on a specific unit, you can let
Guardian act on its own. Setting `auto_approve: true` makes that one action a
deterministic reflex: on a **confirmed** (flap-damped) critical, Guardian
proposes, executes, and verifies with no human in the loop — still loop-guarded
and audited, and the agent is told what was auto-handled so it narrates the fix
instead of re-alarming.

```yaml
repair:
  enabled: true
  playbooks:
    restart_systemd_unit:
      enabled: true
      allowed_units: [my-backup.service]
      auto_approve: true        # self-heal this one allowlisted unit
      max_attempts_per_hour: 3
```

Destructive actions (anything that deletes — `docker_prune`, `prune_dir`)
**ignore `auto_approve` entirely** and always require a human, by construction.
Start narrow: one idempotent action, one unit, with the loop guard on.

## Telegram notifications

Optional and disabled by default. Configure under `notifications.telegram` in
`config.yaml` with a bot token and chat id provided through environment
variables (see `config.example.yaml`). `send_on: changes` is the recommended
mode: you only get a message when something actually changed since the last
scan. In `notifications.mode: agent`, Telegram is dormant and used only as the
critical-fallback when an attached agent is the primary voice.

## Flap damping

One wifi blip should not page you. With `notifications.telegram.confirm_scans: 2`,
a status change must hold for two consecutive scans before Guardian announces
it — in both directions, so recoveries are confirmed too. A check that flaps
up and down never triggers a message. The report and web view always show the
live state; damping only gates notifications.

## Acknowledging known issues

Chronic problems train you to ignore alerts. Acknowledge a check to mute it
without losing sight of it:

```bash
guardian ack ha_unavailable_entities --note "MQTT bridge down, part ordered" --days 14
guardian ack          # list current acknowledgments
guardian unack ha_unavailable_entities
```

An acknowledged check keeps its real status but is excluded from the overall
status, change detection, and notifications. It appears in a collapsed
"Acknowledged" section of the report and web view, with your note, so it
stays visible without drowning the signal. Acknowledgments can expire
automatically (`--days`, `--until`); check ids are shown in reports and in
the web view's evidence blocks.

## Report layout

The Markdown report includes:

- overall status
- summary counts
- what changed since the previous scan (regressions, improvements, new and removed checks)
- critical issues first
- warnings second
- unknowns third
- OK checks last, collapsed to names when there are many
- recommended actions and JSON evidence for each non-collapsed check

## Safety notes

Homelab Guardian's safety boundary has four parts:

- **Collectors are read-only.** Detection never modifies services, containers,
  DNS, Home Assistant entities, backup contents, systemd units, certificates,
  disks, or remote hosts.
- **Local runtime state is writable.** Guardian writes reports, SQLite scan
  snapshots, acknowledgments, alert state, and optional retention pruning under
  the configured report/database paths.
- **Outbound integrations are opt-in.** Telegram notifications and AI briefings
  send structured status data only when explicitly enabled.
- **Repair is opt-in and human-gated.** With `repair.enabled` (off by default),
  Guardian may *propose* a whitelisted, parameterized fix and execute it **only
  after a human approves the specific proposal** — never raw shell, never an
  AI-generated command, always followed by a verify and an audit record.
  Destructive actions can never auto-approve. See *Approval-gated repair* and
  [docs/repair.md](docs/repair.md).

Recommended actions in reports are diagnostic next steps for the operator;
nothing executes automatically without the approval flow above.
