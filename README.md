# Homelab Guardian

Homelab Guardian is a local-first, read-only homelab operations assistant.

It is not another dashboard. It generates plain-English health reports that explain:

- what is broken
- what changed
- what matters
- what the safest next step is

Guardian v0.1 is the **Daily Homelab Doctor**: a simple CLI that collects optional read-only signals, stores local snapshots, and writes a Markdown report.

## Core principles

- Local-first
- Read-only by default
- No destructive actions in the MVP
- No self-healing yet
- No AI shell execution
- No cloud dependency required
- Useful without AI
- Secrets stay local
- Every integration is optional
- Collectors degrade gracefully when unavailable or unconfigured

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python -m app.main --config config.yaml
```

The report is written to:

```text
reports/latest.md
```

For a safe first run without real services configured:

```bash
python -m app.main --config config.example.yaml
```

## Configuration

Start from `config.example.yaml`. Do not commit `config.yaml`, `.env`, API tokens, SSH keys, databases, generated reports, or machine-specific credentials.

Home Assistant access is read-only and uses an environment variable for the token:

```bash
export HOME_ASSISTANT_TOKEN="your-token-here"
```

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

Exited, unhealthy, restarting, or dead containers are surfaced as warnings or critical checks. If Docker is enabled but unavailable, the report shows `unknown` instead of crashing.

### Home Assistant collector

Disabled by default. When configured with a URL and token environment variable, it reads `/api/states` and reports unavailable or unknown entities. It does not modify Home Assistant.

### Network collector

Supports:

- DNS resolution checks
- TCP port checks
- HTTP status checks

Failures include clear evidence such as hostname, port, expected status, actual status, timeout, and error text.

### Backup freshness collector

Checks configured local paths without modifying them. It reports:

- whether the path exists
- latest modified file or folder timestamp
- backup age in hours and days
- warning if older than `max_age_days`
- critical if a required path is missing
- unknown if no backup paths are configured

## Report layout

The Markdown report includes:

- overall status
- summary counts
- critical issues first
- warnings second
- unknowns third
- OK checks last, collapsed to names when there are many
- recommended actions and JSON evidence for each non-collapsed check

## Safety notes

Docker socket access is powerful even for read-only inspection. Mount it only when you understand the risk.

Homelab Guardian does not modify services, containers, files outside its configured output/database paths, DNS records, Home Assistant entities, or backup contents.
