# Project: Homelab Guardian

## Concept

Homelab Guardian is a local-first homelab operations assistant for Docker,
Home Assistant, DNS/network checks, backup freshness, systemd services, disks,
and TLS certificates. Its infrastructure collectors are read-only; its own
reports, snapshots, acknowledgments, alert state, and retention cleanup are
local runtime state.

The product goal is not to replace dashboards such as Uptime Kuma, Beszel, Grafana, or Home Assistant.

The goal is to produce plain-English health reports that tell a technical home user:

- what is broken
- what changed
- what matters
- what the safest next step is

## Target user

A technical home user running some combination of:

- Docker or Docker Compose
- Home Assistant
- Pi-hole or DNS services
- NAS or media storage
- Plex, Jellyfin, ErsatzTV, or a media stack
- Tailscale, VPN, or remote access
- backup scripts
- Telegram, Discord, Gotify, or similar notifications

## Value proposition

> Stop babysitting your homelab. Get a plain-English health report that tells you what broke, what changed, and what to check next.

## Current release shape

Guardian v0.3: Daily Homelab Doctor plus agent integration and approval-gated
repair.

The project is a packaged `guardian` CLI (PyPI) with a setup wizard and
`guardian doctor` preflight, Markdown report generation, SQLite scan history,
a web dashboard with optional auth (password or OIDC), acknowledgments,
flap-damped Telegram notifications, optional BYOM AI briefings, env/Bitwarden
secrets providers, an MCP server so agents read Guardian's verified state,
agent-delivery notifications with a deterministic critical-fallback, host
hardening collectors (firewall, SSH, exposed services, updates, backups), and
approval-gated repair playbooks (opt-in, disabled by default).

## Non-negotiable constraints

- Local-first
- Read-only against homelab infrastructure by default
- Destructive infrastructure actions only through opt-in, human-approved,
  allowlisted repair playbooks — never on auto-approve, disabled by default
- Self-healing is propose → human approve → execute → verify; never raw shell,
  never an AI-generated command
- No AI shell execution
- No cloud dependency required
- Useful even without AI
- Secrets stay local
- Docker socket access treated carefully
- Home Assistant token stored only through environment variables or local untracked config
- Every integration optional
- Every collector degrades gracefully
- Local app writes are limited to configured report/database paths and explicit outbound integrations
- Simple, boring, testable implementation
