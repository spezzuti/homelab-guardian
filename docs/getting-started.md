# Getting started: from monitoring to self-healing

This is the guided walkthrough for a new adopter. It takes you from `pip install`
to a read-only dashboard, then — only as far as you want to go — to an AI agent
reading Guardian's verified state, and finally to a human-approved repair and
optional self-healing.

Everything below is opt-in and off until you turn it on. Each step stands on its
own; stop at whichever rung you trust. Guardian's whole posture is **read-only by
default, any action human-gated, never a raw shell** — keep that in mind and the
rest follows.

## Prerequisites

- Python 3.10+.
- Linux for the host-hardening and systemd collectors; Guardian otherwise runs
  anywhere (macOS/Windows included) for the network, disk, Docker, and Home
  Assistant collectors.
- A virtualenv or `pipx` so the install stays isolated.

## Install

```bash
pip install homelab-guardian          # core
pip install 'homelab-guardian[mcp]'   # add the MCP server (step 3)
```

This gives you the `guardian` console command. (`python -m homelab_guardian.main`
is equivalent if you prefer.)

## Step 0 — generate a config

```bash
guardian init
```

The wizard asks a few questions and writes a working `config.yaml` (plus a
gitignored `.env` for any tokens you paste). If you say yes, it does a read-only
TCP-connect scan of your `/24` and recognizes common services (Home Assistant,
Proxmox, Pi-hole, Portainer, Plex, Jellyfin, Synology, Uptime Kuma, …),
filtering out smart-speaker false positives. On a Linux host it offers the
zero-config host-hardening checks too.

Prefer to write it by hand? Copy `config.example.yaml` to `config.yaml` — it
documents the entire config surface.

> Never commit `config.yaml`, `.env`, reports, or the database. They're
> gitignored for a reason: they hold tokens and machine-specific detail.

## Step 1 — monitor (read-only collectors)

Enable the collectors that fit your setup. A focused starting set:

```yaml
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
      - id: http_ha
        name: Home Assistant
        url: "http://homeassistant.local:8123"
        expected_status: [200, 301, 302, 401]
```

On a Linux host you can also turn on the zero-config host-hardening collectors —
`firewall`, `ssh`, `exposed_services`, `updates` — and `backup_health` (point it
at a restic repo or a systemd backup unit). None of them need root.

Then:

```bash
guardian doctor    # validates config, secrets, reachability, repair scope
guardian           # one scan -> reports/latest.md
```

`guardian doctor` is your friend: it tells you what's enabled-but-unconfigured,
whether secrets resolve, and (later) whether your repair allowlists and sudo
scope are sane. Run it whenever you change `config.yaml`.

Every check carries a `status` (`ok` / `warning` / `critical` / `unknown`), a
plain-English `summary`, an `evidence` dict, and a `recommended_action`. That
structured contract is what makes the later steps safe — the agent and the repair
layer act on verified checks, not guesses.

## Step 2 — see it (the dashboard)

```bash
guardian serve                 # http://localhost:8674 (localhost only)
guardian serve --interval 900  # appliance mode: scan every 15 min + serve
```

The page is read-only: overall status, *what changed* since the last scan, every
check with its evidence, and scan history with per-scan drill-down. No
JavaScript, no write endpoints. `/healthz` returns `ok` so another monitor can
watch Guardian itself.

It binds to localhost. Before exposing it (`--host 0.0.0.0`), add authentication
— `web.auth.mode` supports `basic`, `forward_auth`, and `oidc`. See
[auth.md](auth.md). There's also a `/settings` page (auth + CSRF gated) to toggle
collectors with comment-preserving writes to `config.yaml`.

For an always-on install, `deploy/homelab-guardian.service` is a ready-to-edit
systemd user service; in Docker Compose, run with `--interval` and
`restart: unless-stopped`.

## Step 3 — attach an agent over MCP

Hand Guardian's *verified* state to an AI agent so it reasons over real checks
instead of re-deriving the homelab itself. Read-only by default: the agent reads,
it does not change Guardian's state.

```bash
pip install 'homelab-guardian[mcp]'
```

**stdio (same host, the default).** The agent's client launches `guardian mcp` as
a subprocess. Point a client (Claude Desktop / Claude Code) at the console
command:

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

Now *"is my homelab healthy? what needs attention?"* is answered from Guardian's
checks. The read tools are `get_health_summary`, `list_problems`, `list_checks`,
`get_check`, `get_recent_changes`, `list_scan_history`, `list_acknowledgments`.

**Remote agent (HTTP).** `guardian mcp --http` serves the same over streamable
HTTP, gated by a bearer token (`mcp.http.token_env`) — it refuses to start
without one, so the network surface is never unauthenticated. Front it with a
reverse proxy for SSO.

**Optional write tools.** `mcp.allow_writes: true` adds `acknowledge_check` /
`unacknowledge_check` so the agent can mute a check on your behalf (the same
reversible mute as `guardian ack`). Off by default — the tools aren't even
registered.

**Agent as the single voice.** `notifications.mode: agent` makes Guardian POST
each confirmed change to the agent's webhook so the agent reports and offers to
fix — with a deterministic Telegram fallback if a *critical* can't be delivered
or the agent never confirms it relayed it. The division of labor: Guardian is the
deterministic source of truth and reflex actuator; the agent narrates and handles
the judgment-heavy fixes Guardian deliberately won't.

Full tool surface, transports, and client examples: [mcp.md](mcp.md).

## Step 4 — a human-approved repair, for one safe unit

This is the step that turns *"here's what's wrong and what I'd do"* into
*"…want me to do it?"* — without ever handing the agent a shell.

Pick the single safest repair you have: restarting one watched systemd unit.
Guardian will only restart units you explicitly allowlist, and only after you
approve each proposal.

```yaml
repair:
  enabled: true                 # master switch (off by default)
  require_approval: true        # human approves every proposal (the default)
  playbooks:
    restart_systemd_unit:
      enabled: true
      allowed_units: [my-backup.service]   # explicit allowlist; empty = nothing
      auto_approve: false
      max_attempts_per_hour: 3             # loop guard, then it escalates to you
      timeout: 60
```

```bash
guardian doctor    # self-validates the repair config (allowlists, sudo scope)

# when my-backup.service is actually failing:
guardian repair list my-backup.service              # what repairs apply?
guardian repair propose my-backup.service restart_systemd_unit
guardian repair approve <proposal_id>               # the human-only gate
guardian repair execute <proposal_id>               # runs, then verifies recovery
guardian repair log                                 # the audit trail
```

What makes this safe:

- **`propose` is a dry run.** It shows the exact argv, blast radius,
  reversibility, and the verify step — and changes nothing.
- **Approval is human-only and lives in Guardian.** An agent (over MCP) can
  propose and execute, but **never approve**. A prompt-injected or confused agent
  cannot self-authorize. Approve via the CLI above or the dashboard `/repairs`
  page.
- **Never raw shell.** Only named, parameterized argv built from a registered
  playbook; the target (`unit`) must be in your allowlist and is read from the
  failing check's evidence, never from free-form text.
- **Execute re-validates.** At execute time Guardian rebuilds the plan and
  refuses if the target left the allowlist, the check recovered, or the argv
  drifted from what was approved.
- **Privilege is scoped.** A *system* unit needs a minimal sudoers grant for that
  exact argv (one line per allowed unit) — never passwordless-ALL. `systemctl
  --user` units need no elevation; prefer those.

Other shipped playbooks follow the same shape: `restart_container` (a watched
Docker container) and the disk-reclaim family (`docker_prune`, `journal_vacuum`,
`apt_clean`, `prune_dir`) — the latter carry read-only "would free ≈X" previews
and a cross-collector **backup interlock** that refuses to delete user files when
backups aren't ok-and-fresh. Full safety model and threat model:
[repair.md](repair.md) and [repair-reclaim.md](repair-reclaim.md).

## Step 5 (optional) — self-healing

Once you trust a *non-destructive* repair on a specific unit, let Guardian act on
its own. `auto_approve: true` makes that one action a deterministic reflex: on a
**confirmed** (flap-damped) critical, Guardian proposes, executes, and verifies
with no human in the loop — still loop-guarded and audited. The agent
notification carries what was auto-handled (`auto_repaired`) so the agent narrates
the fix instead of re-alarming.

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

**Destructive actions ignore `auto_approve` entirely.** Anything that deletes
(`docker_prune`, `prune_dir`) can never be auto-approved, regardless of config —
`execute` refuses a destructive proposal that wasn't human-approved. Start
narrow: one idempotent action, one unit, loop guard on. Expand only after you've
watched it behave.

## Where to go next

- [mcp.md](mcp.md) — the MCP server in depth: every tool, transports, wiring a
  real agent.
- [repair.md](repair.md) — the approval-gated repair safety design and threat
  model.
- [repair-reclaim.md](repair-reclaim.md) — destructive disk-reclaim repairs:
  previews, cross-collector preconditions, risk tiers.
- [auth.md](auth.md) — dashboard authentication (`basic` / `forward_auth` /
  `oidc`).
- `config.example.yaml` — the fully annotated config surface.
