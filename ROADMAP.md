# Roadmap

## v0.1 — Daily Homelab Doctor

Goal: generate a local Markdown report from optional read-only collectors.

Delivered capabilities:

- Inspect Docker containers
- Detect stopped, unhealthy, or restarting containers
- Identify mounts, volumes, exposed ports, and compose projects
- Connect to Home Assistant via read-only API
- Report unavailable or unknown Home Assistant entities
- Run DNS, TCP, HTTP, and TLS/network checks
- Check backup folder freshness by local path
- Store snapshots locally in SQLite
- Compare current scan against the previous scan
- Generate a Markdown health report
- Optionally send Telegram notifications

## v0.2 — Product-shaped local Guardian

Goal: keep the read-only collector model, but make Guardian usable as a small
installed tool rather than only a scaffold.

Delivered capabilities:

- Packaged `guardian` CLI (`pip install -e .`)
- Setup wizard with optional read-only LAN discovery
- Read-only web dashboard (`guardian serve`) with scan history
- Acknowledgments for muting known issues without hiding them
- Flap-damped Telegram notifications
- Optional bring-your-own-model AI briefing layer
- Secrets provider abstraction with env and Bitwarden Secrets Manager support
- New collectors: systemd, disk space, TLS certificate expiry
- Snapshot retention pruning
- Multi-arch GHCR image workflow
- AGPL-3.0-or-later license

## v0.3 candidates — harden before widening scope

- Validate Docker socket-proxy mode on a real Docker host
- Finish backup freshness dogfood with dummy local folders before real backup paths
- Make doctor/preflight semantics explicit: practical writable preflight vs. optional check-only mode
- Add web dashboard deployment guidance for reverse proxy/auth
- Add static safety checks for mutating collector operations and secret leakage
- Add Python 3.10 CI coverage because the package supports `>=3.10`

## Later, after local mode is boring

- Rich diffing and trend analysis beyond previous-scan comparison
- Policy/rule engine
- Remote collector model over least-privilege APIs, not broad shell access
- Additional notification adapters
- Additional secrets providers
- Self-healing workflows only after an explicit safety design and operator approval model
- Paid hosted features
