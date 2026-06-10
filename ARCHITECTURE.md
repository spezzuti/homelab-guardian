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

## Security posture

- No secrets committed
- No destructive actions
- No shell execution by AI or collectors
- Docker socket is optional and should be mounted intentionally
- Home Assistant token comes from an environment variable or untracked local config
