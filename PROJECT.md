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

Guardian v0.2: Daily Homelab Doctor plus dashboard and alerts.

The project is now a packaged `guardian` CLI with optional setup wizard,
Markdown report generation, SQLite scan history, read-only web view,
acknowledgments, flap-damped Telegram notifications, optional BYOM AI
briefings, env/Bitwarden secrets providers, and GHCR image publishing.

## Non-negotiable constraints

- Local-first
- Read-only against homelab infrastructure by default
- No destructive infrastructure actions
- No self-healing yet
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
