# Changelog

## v0.2.0 — 2026-06-13

The "from scaffold to product" release. Everything below was dogfooded
against a real homelab before landing.

### Added

- **Snapshot diffing** — every report leads with what changed since the last
  scan: regressions, improvements, new and removed checks.
- **Web dashboard** (`guardian serve`) — read-only, stdlib-only, dark/light
  theme, categorized check tiles, collapsible scan history, `/healthz`.
  Appliance mode (`--interval`) scans in the background of the same process.
- **Setup wizard** (`guardian init`) — interactive config generation with
  optional LAN auto-discovery (read-only TCP probes, reverse-DNS naming,
  Google Cast false-positive filtering).
- **Telegram notifications** — flap-damped via `confirm_scans`: a status must
  hold N consecutive scans before it is announced; recoveries confirmed
  symmetrically.
- **Acknowledgment system** (`guardian ack`/`unack`) — mute chronic known
  issues with notes and optional expiry; muted checks stay visible but never
  page you.
- **AI briefing (BYOM)** — optional plain-English summary from any
  OpenAI-compatible endpoint (OpenRouter, local Ollama, ...). The model sees
  structured check data only; no shell, no tools.
- **Secrets providers** — env (default) or Bitwarden Secrets Manager via the
  bws CLI: one machine-account token instead of a pile of .env entries.
- **New collectors** — systemd (failed units AND restart loops, watched
  units), disk space (threshold alerts), TLS certificate expiry (self-signed
  supported via dependency-free DER parse).
- **Scheduling** — `--interval` loop mode plus a ready-to-edit systemd user
  service in `deploy/`.
- **Packaging** — `pip install` -> `guardian` CLI; multi-arch (amd64/arm64)
  Docker images on GHCR; `.env` autoloading; snapshot retention pruning.
- **Tests and CI** — 108 tests; GitHub Actions for tests and image publishing.

### Changed

- Package renamed from `app` to `homelab_guardian`.
- License set to AGPL-3.0-or-later.

## v0.1.0 — 2026-06-10

Initial scaffold: Daily Homelab Doctor. Optional read-only collectors
(Docker, Home Assistant, network, backup freshness), structured health-check
contract, SQLite snapshots, Markdown report, doctor preflight, Docker Compose
packaging with socket-proxy overlay.
