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

## v0.1 next tasks

- [ ] Add previous-scan comparison output
- [ ] Add robust SQLite snapshot read/write tests
- [ ] Add unit tests for config loading
- [ ] Add unit tests for each collector failure mode
- [ ] Add report golden-file test
- [ ] Add optional Telegram notification adapter
- [ ] Add Docker image build verification
- [ ] Add CI

## Future remote-collector support

- [ ] Design remote Docker collector transport without giving the app broad shell access
- [ ] Support remote host inventory snapshots over least-privilege APIs
- [ ] Support remote backup freshness checks for NAS paths not mounted locally
- [ ] Keep remote collectors optional and read-only by default
