# Architecture

## Shape

Homelab Guardian is a small Python CLI application.

```text
app/
  main.py                  CLI entry point
  config.py                YAML config loading and defaults
  db.py                    SQLite connection and snapshot schema helpers
  models.py                Shared structured health check types
  collectors/              Optional read-only collectors
  reports/                 Markdown report generation
```

## Data flow

1. Load YAML config.
2. Initialize the local SQLite database path from config.
3. Run enabled collectors.
4. Convert every result into a structured health check.
5. Treat collector failures as `unknown` checks instead of crashing.
6. Store a scan snapshot locally.
7. Generate `reports/latest.md`.

## Health check contract

Every check returns:

- `id`
- `name`
- `status`: `ok`, `warning`, `critical`, or `unknown`
- `summary`
- `evidence`
- `recommended_action`

## Collector design

Collectors are optional. Missing configuration, missing dependencies, unavailable sockets, failed API calls, DNS failures, and permission errors should create warning or unknown checks rather than aborting the scan.

## Storage

SQLite is used for local scan snapshots. The first implementation stores raw JSON snapshots so the schema stays boring while the checks evolve.

## Deployment modes

### Direct Python mode

Guardian runs directly on the host with Python. Local paths in `config.yaml` are host paths. Docker inspection works only when the configured Docker socket or endpoint is reachable from that host user.

### Containerized local collector mode

Guardian runs as a one-shot Docker Compose service on the Docker host. This is the preferred MVP install path:

```text
repo checkout
  config.yaml      private, mounted read-only into /app/config.yaml
  data/            mounted writable into /app/data for SQLite snapshots
  reports/         mounted writable into /app/reports for latest.md
```

The container runs the same CLI entry point:

```text
python -m app.main --config /app/config.yaml
```

### Direct socket mode

`docker-compose.yml` mounts `/var/run/docker.sock:/var/run/docker.sock:ro` into the Guardian container. The collector uses the Docker SDK against `unix://var/run/docker.sock`. Guardian makes read-oriented calls only, but direct socket access is still privileged and should be treated carefully.

### Socket proxy mode

`docker-compose.socket-proxy.yml` defines Guardian plus `docker-socket-proxy` and sets `DOCKER_HOST=tcp://docker-socket-proxy:2375` for Guardian. The Docker collector prefers `DOCKER_HOST` over `socket_url`, so the same private `config.yaml` can be used while this Compose file routes Docker API access through the proxy. The proxy enables only selected read-oriented API areas where possible and keeps write methods disabled.

### Future remote collector mode

Future versions may collect from remote Docker hosts, NAS systems, Home Assistant instances, or backup locations through explicit least-privilege APIs. Remote collectors should remain optional, read-only by default, and should not give Guardian broad shell access.

## Security posture

- No secrets committed
- No destructive actions
- No shell execution by AI or collectors
- Docker socket is optional and should be mounted intentionally
- Home Assistant token comes from an environment variable or untracked local config
