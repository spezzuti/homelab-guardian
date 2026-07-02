# Architecture

## Shape

Homelab Guardian is a small Python CLI application with read-only
infrastructure collectors, local persistence, optional outbound notifications,
and a read-only web view.

```text
homelab_guardian/
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
8. Optionally update local alert/acknowledgment state and send enabled
   outbound notifications.

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

## Safety boundary

Guardian separates three kinds of activity:

- **Infrastructure reads:** collectors inspect Docker metadata, Home Assistant
  states, DNS/TCP/HTTP/TLS endpoints, backup path metadata, systemd state, and
  disk usage. They should not modify the systems they inspect.
- **Local app writes:** Guardian writes reports, SQLite snapshots,
  acknowledgments, alert state, and retention pruning under configured local
  output/database paths.
- **Optional outbound sends:** Telegram notifications and AI briefings use
  explicit configuration and secrets; disabling them leaves scan/report
  behavior intact.
- **Approval-gated repair (opt-in, disabled by default):** when
  `repair.enabled` is true, Guardian can run *named, parameterized, allowlisted*
  actions (restart an allowlisted systemd unit or container, remount, reclaim
  disk) — never raw shell and never an LLM-generated command. Every action is
  derived from Guardian's own validated check evidence, re-validated against the
  allowlist at execution time, loop-guarded, and audited. An agent may *propose*
  and *execute*, but only a human can *approve*; destructive actions (e.g.
  `docker_prune`, `prune_dir`) can never run on an auto-approval. A narrow
  reflex tier may auto-run only playbooks explicitly marked `auto_approve`, which
  excludes every destructive action. See `docs/repair.md` for the full design.

No action outside that approval-gated, allowlisted set is ever taken: there is
no generic "run a command" capability, and the AI layer stays read-only.

## Storage

SQLite is used for local scan snapshots, acknowledgments, and alert state. Scan
snapshots are stored as raw JSON so the schema stays boring while checks evolve.

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
python -m homelab_guardian.main --config /app/config.yaml
```

### Direct socket mode

`docker-compose.yml` mounts `/var/run/docker.sock:/var/run/docker.sock:ro` into the Guardian container. The collector uses the Docker SDK against `unix://var/run/docker.sock`. Guardian makes read-oriented calls only, but direct socket access is still privileged and should be treated carefully.

### Socket proxy mode

`docker-compose.socket-proxy.yml` defines Guardian plus `docker-socket-proxy` and sets `DOCKER_HOST=tcp://docker-socket-proxy:2375` for Guardian. The Docker collector prefers `DOCKER_HOST` over `socket_url`, so the same private `config.yaml` can be used while this Compose file routes Docker API access through the proxy. The proxy enables only selected read-oriented API areas where possible and keeps write methods disabled.

### Future remote collector mode

Future versions may collect from remote Docker hosts, NAS systems, Home Assistant instances, or backup locations through explicit least-privilege APIs. Remote collectors should remain optional, read-only by default, and should not give Guardian broad shell access.

## Security posture

- No secrets committed
- Collectors are read-only; they never modify the systems they inspect
- Destructive infrastructure actions are possible only through the opt-in,
  human-approved, allowlisted repair playbooks (disabled by default) — never
  from raw shell and never from an LLM-generated command
- No shell execution by the AI layer; repair runs only fixed, allowlisted argv
- Docker socket is optional and should be mounted intentionally
- Home Assistant token comes from an environment variable or untracked local config
- Web view binds to localhost by default; LAN exposure should sit behind an
  intentional access-control boundary
