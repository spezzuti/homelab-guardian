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

## First-run steps

```bash
git clone <repo-url>
cd homelab-guardian
cp config.example.yaml config.yaml
mkdir -p data reports
```

Edit `config.yaml` locally. Do not commit it.

For Docker inventory, enable the Docker collector in `config.yaml` only on a Docker host or when using the socket proxy overlay:

```yaml
collectors:
  docker:
    enabled: true
    socket_url: unix://var/run/docker.sock
    exclude_containers:
      - "homelab-guardian*"
```

## Direct Python run

Use this mode for development or for hosts where Python already has access to the paths and services you want to inspect.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.main --config config.yaml
```

Safe example run without private services:

```bash
python -m app.main --config config.example.yaml
```

Preflight check:

```bash
python -m app.main doctor --config config.yaml
```

Equivalent form:

```bash
python -m app.main --config config.yaml --doctor
```

## Docker Compose run

Preferred MVP install path on a Docker host:

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

1. Run `python -m app.main doctor --config config.yaml`.
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

Home Assistant access is read-only and uses an environment variable for the token:

```bash
export HOME_ASSISTANT_TOKEN="your-token-here"
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
python -m app.main --config config.yaml
```

Expected result: the dummy backup check reports `ok` while the marker file is fresh. To test stale behavior safely, change `max_age_days` to `0` or adjust only files inside `/tmp/homelab-guardian-backup-dogfood`. Never commit `config.yaml`, generated reports, database files, or the dummy runtime folder.

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

Homelab Guardian does not modify services, containers, files outside its configured output/database paths, DNS records, Home Assistant entities, or backup contents.
