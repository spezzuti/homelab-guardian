# Project: Homelab Guardian

## Concept

Homelab Guardian is a local-first, read-only homelab operations assistant for Docker, Home Assistant, DNS/network checks, and backup freshness.

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

## MVP name

Guardian v0.1: Daily Homelab Doctor

## Non-negotiable constraints

- Local-first
- Read-only by default
- No destructive actions in the MVP
- No self-healing yet
- No AI shell execution
- No cloud dependency required
- Useful even without AI
- Secrets stay local
- Docker socket access treated carefully
- Home Assistant token stored only through environment variables or local untracked config
- Every integration optional
- Every collector degrades gracefully
- Simple, boring, testable first version
