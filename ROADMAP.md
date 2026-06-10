# Roadmap

## v0.1 — Daily Homelab Doctor

Goal: generate a local Markdown report from optional read-only collectors.

Planned capabilities:

- Inspect Docker containers
- Detect stopped, unhealthy, or restarting containers
- Identify mounts, volumes, exposed ports, and compose projects
- Connect to Home Assistant via read-only API
- Report unavailable or unknown Home Assistant entities
- Run basic DNS and TCP/network checks
- Check backup folder freshness by path
- Store snapshots locally in SQLite
- Compare current scan against the previous scan
- Generate a Markdown health report
- Optionally send the report later through Telegram or other notification services

## Later, not v0.1

- Web UI
- Scheduling daemon
- Notification adapters
- Rich diffing and trend analysis
- Policy/rule engine
- Multi-host agent model
- AI-assisted explanation layer
- Self-healing workflows
- Paid hosted features
