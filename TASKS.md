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

## v0.1 next tasks

- [ ] Real Docker host test with direct socket mode
- [ ] Real Docker host test with socket proxy mode
- [ ] Home Assistant collector dogfood
- [ ] Report polish for containerized runs
- [ ] Snapshot comparison improvements
- [ ] Add previous-scan comparison output
- [ ] Add robust SQLite snapshot read/write tests
- [ ] Add unit tests for config loading
- [ ] Add unit tests for each collector failure mode
- [ ] Add report golden-file test
- [ ] Add optional Telegram notification adapter later
- [ ] Add Docker image build verification on a Docker host
- [ ] Add CI

## Future remote-collector support

- [ ] Design remote Docker collector transport without giving the app broad shell access
- [ ] Support remote host inventory snapshots over least-privilege APIs
- [ ] Support remote backup freshness checks for NAS paths not mounted locally
- [ ] Keep remote collectors optional and read-only by default
