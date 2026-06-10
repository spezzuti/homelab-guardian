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

## Preflight / doctor

Use the doctor command to see whether the current machine is a good place to run Guardian:

```bash
python -m app.main doctor --config config.yaml
```

Equivalent form:

```bash
python -m app.main --config config.yaml --doctor
```

The doctor checks:

- Python version
- config file loading
- reports directory writability
- data directory writability
- Docker socket availability when Docker collection is enabled
- Home Assistant URL and token environment variable when enabled
- backup path configuration when enabled
- network check configuration when enabled

## Configuration

Start from `config.example.yaml`. Do not commit `config.yaml`, `.env`, API tokens, SSH keys, databases, generated reports, or machine-specific credentials.

Home Assistant access is read-only and uses an environment variable for the token:

```bash
export HOME_ASSISTANT_TOKEN="your-token-here"
```

## Deployment modes

### Run directly on a Docker host

Install Python and run Guardian on the same host that runs Docker. Enable the Docker collector only if `/var/run/docker.sock` exists and the user running Guardian can read Docker metadata.

```yaml
collectors:
  docker:
    enabled: true
    socket_url: unix://var/run/docker.sock
```

### Run via Docker Compose with Docker socket mounted

Guardian can run in a container, but Docker inspection only works if the socket is intentionally mounted:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

The Docker socket is sensitive. Even read-only mounting can expose powerful Docker API access. Mount it only on machines where you understand and accept that risk.

### Run without Docker

Guardian is still useful without Docker. Leave the Docker collector disabled and use any combination of:

- DNS checks
- TCP checks
- HTTP checks
- local backup path checks
- Home Assistant API checks

This mode is useful on a small monitoring VM, a NAS shell, or any host that can see the services you care about.

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

Backup paths are local to the machine or container running Guardian. If Guardian runs in Docker, mount backup locations read-only into the container first.

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
