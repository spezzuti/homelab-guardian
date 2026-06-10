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

Start from `config.example.yaml`. Do not commit `config.yaml`, `.env`, API tokens, SSH keys, or machine-specific credentials.

Home Assistant access is read-only and uses an environment variable for the token:

```bash
export HOME_ASSISTANT_TOKEN="your-token-here"
```

## Current collectors

- Docker collector: optional, read-only Docker socket inspection when enabled
- Home Assistant collector: optional, read-only API status/entity inspection when configured
- Network collector: optional DNS/TCP checks from local config
- Backup collector: optional freshness checks for configured local paths

## Safety notes

Docker socket access is powerful even for read-only inspection. Mount it only when you understand the risk.

Homelab Guardian does not modify services, containers, files outside its configured output/database paths, DNS records, Home Assistant entities, or backup contents.
