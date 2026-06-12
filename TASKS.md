# Tasks

## Scaffold

- [x] Create documentation
- [x] Create Python package structure
- [x] Add YAML config loading
- [x] Add structured health check model
- [x] Add optional collectors
- [x] Add Markdown report writer
- [x] Add basic smoke-test path

## Sprint 1

- [x] Improve Docker collector metadata: image, status, health, restart count, ports, mounts, Compose labels
- [x] Make Docker unavailable state report `unknown` instead of crashing
- [x] Improve backup freshness checker with path existence, latest timestamp, age hours/days, required-path handling
- [x] Add DNS, TCP, and HTTP checks with clear failure evidence
- [x] Improve Markdown report ordering and overall status clarity
- [x] Expand `config.example.yaml` with realistic safe examples
- [x] Update README with collector behavior and safety notes

## Sprint 1.5 dogfood

- [x] Run against a private ignored `config.yaml`
- [x] Confirm generated report and SQLite files remain ignored
- [x] Confirm this machine does not expose `/var/run/docker.sock`

## Sprint 1.6 preflight and deployment clarity

- [x] Add CLI doctor/preflight command
- [x] Check Python version, config loading, report/data writability
- [x] Explain missing Docker socket when Docker collector is enabled
- [x] Check Home Assistant URL/token config when enabled
- [x] Check backup and network check configuration
- [x] Document deployment modes

## Sprint 1.7 Docker Compose packaging

- [x] Add Dockerfile for the Python app
- [x] Update Docker Compose service for one-shot Guardian runs
- [x] Mount private config, data, reports, and Docker socket paths
- [x] Add safer docker-socket-proxy Compose overlay
- [x] Document direct Python, direct socket, socket proxy, and missing-socket guidance

## Sprint 1.7 dogfood hardening

- [x] Harden Docker image name extraction for untagged images
- [x] Avoid unsafe Docker SDK list indexing
- [x] Wrap per-container metadata collection so one malformed container becomes a warning
- [x] Preserve partial Docker inventory results when some containers are readable
- [x] Add Docker inventory summary check with readable/error counts
- [x] Add Docker container exclusion patterns for Guardian runtime containers
- [x] Clarify backup-not-configured as incomplete configuration, not a detected failure

## Backup checker dogfood

- [ ] Test backup freshness using only a dummy local folder
- [ ] Confirm fresh dummy marker reports `ok`
- [ ] Confirm stale dummy marker reports warning without touching real backup paths
- [ ] Confirm dummy runtime folder, `config.yaml`, reports, and SQLite files are not committed

## v0.1 next tasks

- [ ] Real Docker host test with direct socket mode
- [ ] Real Docker host test with socket proxy mode
- [x] Home Assistant collector dogfood
- [ ] Report polish for containerized runs
- [x] Snapshot comparison improvements
- [x] Add previous-scan comparison output
- [x] Add robust SQLite snapshot read/write tests
- [x] Add unit tests for config loading
- [x] Add unit tests for each collector failure mode
- [x] Add report golden-file test
- [x] Add optional Telegram notification adapter later
- [ ] Add Docker image build verification on a Docker host
- [x] Add CI

## Sprint 4 — web report view (2026-06-12)

- [x] `guardian serve`: read-only stdlib web view rendered from SQLite
      snapshots (status banner, briefing, what-changed, check cards with
      evidence, scan history with per-scan drill-down, /healthz)
- [x] Appliance mode: `guardian serve --interval N` scans in the background
- [x] Store the AI narrative in scan snapshots
- [x] DB helpers: load_scan, load_scan_before, list_scans

## Sprint 3 — secrets, packaging, onboarding (2026-06-12)

- [x] Pluggable secrets providers: env (default) + Bitwarden Secrets Manager
      via bws CLI, env-first precedence, TTL cache, graceful degradation
- [x] Doctor preflight for the secrets provider (counts readable secrets)
- [x] pyproject.toml + `guardian` console command (pip install -e .)
- [x] `guardian init` setup wizard: interactive config generation
- [x] LAN auto-discovery (read-only TCP probes, reverse-DNS names,
      Google Cast false-positive fingerprinting)
- [x] .env autoloading for bare CLI runs (no python-dotenv dependency)
- [x] Emoji-safe stdout on legacy Windows codepages
- [ ] Rename `app` package to `homelab_guardian` before any PyPI release
- [ ] Pick a license before publishing
- [ ] Prebuilt Docker image (GHCR) via GitHub Actions

## Sprint 2 — diff, notify, schedule, explain (2026-06-12)

- [x] Snapshot diffing with what-changed report section
- [x] verify_tls option for self-signed homelab HTTPS services
- [x] Real homelab dogfood: 18 checks across Proxmox x2, TrueNAS, QNAP,
      iDRAC, Pi-hole, Portainer, Authentik, Home Assistant, modem, SSH hosts
- [x] Telegram notification adapter (send_on: always | changes | problems)
- [x] --interval loop mode plus example systemd user service
- [x] BYOM AI briefing layer over one OpenAI-compatible endpoint
- [ ] Per-config database_path guidance enforcement (currently docs-only)

## Future remote-collector support

- [ ] Design remote Docker collector transport without giving the app broad shell access
- [ ] Support remote host inventory snapshots over least-privilege APIs
- [ ] Support remote backup freshness checks for NAS paths not mounted locally
- [ ] Keep remote collectors optional and read-only by default
