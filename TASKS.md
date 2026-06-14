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

- [x] Test backup freshness using only a dummy local folder (covered by
      `test_backup_collector.py`, which exercises every status on pytest
      `tmp_path` dummy folders — never real backup paths)
- [x] Confirm fresh dummy marker reports `ok` (`test_fresh_backup_is_ok`)
- [x] Confirm stale dummy marker reports warning without touching real backup
      paths (`test_stale_backup_is_warning`, uses `tmp_path` only)
- [x] Confirm dummy runtime folder, `config.yaml`, reports, and SQLite files
      are not committed (`.gitignore` covers config.yaml, reports/*.md,
      data/*.sqlite, .env; pytest tmp_path lives outside the repo)

## v0.1/v0.2 cleanup and carried-forward tasks

- [x] Real Docker host test with direct socket mode (completed in Sprint 6)
- [x] Real Docker host test with socket proxy mode (Leatherface Docker guest: proxy read endpoints worked, POST create blocked with 403, Guardian inventory matched `docker ps`, cleanup verified)
- [x] Home Assistant collector dogfood
- [ ] Report polish for containerized runs
- [x] Snapshot comparison improvements
- [x] Add previous-scan comparison output
- [x] Add robust SQLite snapshot read/write tests
- [x] Add unit tests for config loading
- [x] Add unit tests for each collector failure mode
- [x] Add report golden-file test
- [x] Add optional Telegram notification adapter later
- [x] Add Docker image build verification on a Docker host (completed in Sprint 6)
- [x] Add CI

## Sprint 8 — host-hardening collectors (from Marcus audit 2026-06-13)

Derived from a read-only audit of the production host. Each maps to the
existing collector contract: a module under `homelab_guardian/collectors/`
exposing `collect(config, secrets=None) -> list[HealthCheck]`, registered in
`COLLECTORS` in `main.py`, gated by `enabled`, read-only, returning the
`status/summary/evidence/recommended_action` shape. Real findings that would
have been caught automatically are noted per item.

Shipped 2026-06-13 — built, tested (142), committed, and **deployed live on
Marcus** (box `main`, new collectors enabled in the production config.yaml):

- [x] **firewall collector** (`firewall`): root-free — reads ufw/nftables/
      firewalld service state + the world-readable `/etc/default/ufw` policy.
      `warning` when no firewall or default-allow; `ok` on default-deny.
- [x] **exposed-services collector** (`exposed_services`): parses `ss -tulnH`,
      flags non-loopback binds on sensitive ports (SMB, VNC, DB ports, ...),
      with `allow_ports`/`sensitive_ports` overrides. *(Dogfood caught SMB +
      VNC still LISTENING on 0.0.0.0 on Marcus even after the firewall — a
      real, correct finding.)*
- [x] **ssh-hardening collector** (`ssh`): root-free — resolves effective
      sshd config from the files with sshd's own first-match-wins + drop-in
      ordering; warns on password auth / direct root login; notes fail2ban.
- [x] **updates collector** (`updates`): apt-check counts + security count +
      `/var/run/reboot-required`. apt-only for now; dnf/pacman → unknown.
- [x] **backup-health collector** (`backup_health`): `restic` mode
      (authoritative snapshot age) + creds-free `systemd` mode (a backup
      unit's last result/finish). *(Dogfood: restic mode reported the 1.3h-old
      Marcus snapshot `ok`; systemd mode correctly flagged that a oneshot's
      run-time is cleared by reboot — message now says so explicitly.)*
- [x] **Dashboard grouping**: `group` field on `HealthCheck`; problem groups
      render as full-width worst-of-children roll-up cards (auto-open, sorted
      first); healthy groups render as **collapsible 2-column tiles** (collapsed
      by default, open/closed state persisted in localStorage across the
      auto-refresh, largest-first). Live taxonomy: **Host** (disks+updates+
      systemd), **Infrastructure**, **Applications**, **Network**, Security,
      Backups — the infra/app split is via per-target `group:` in config.
- [x] `config.example.yaml` entries for all five collectors + `group:` docs;
      142 tests pass (5 new collector test files + grouping tests).
- [x] Dogfood against Marcus (isolated /tmp copy, temp DB): firewall ok, ssh
      ok, updates ok, backups ok (restic), Core services rolled up, exposed
      services warned. Grouping + collectors validated end-to-end.
- [x] **Deploy to the live Marcus service** + enable the new collectors in the
      production config.yaml. Done; dashboard live with the new taxonomy.
- [x] **Calm-by-default cleanup**: all collectors now default `enabled: False`
      in `config.py` DEFAULT_CONFIG, so an unconfigured host no longer emits
      `*_not_configured` "unknown" tiles.
- [x] **Acted on exposed-services findings (Marcus host)**: retired the VNC
      server (`vncserver@1`), Samba server (smbd/nmbd, was sharing /home), and
      rpcbind (NFSv4 backup mount verified fine without it). Security group is
      now green. *User action: repoint Guacamole from VNC to SSH.*
- [x] **Wizard** (`guardian init`): offers the five host-hardening collectors
      (fixed 2026-06-14, Linux-host gated). One prompt enables the four
      zero-config ones (firewall, exposed_services, ssh, updates); a follow-up
      optionally adds backup_health watching a named systemd backup unit.
      `build_config` gained `host_checks`/`backup_unit` params; 3 wizard tests.
- [x] **Push box `main` to GitHub** (done 2026-06-14: GitHub origin was stale
      at b10a4ae; pushed up through 32f6808, fast-forwarding the interim
      b4e1fab/8fcaffb/0fe0033 commits. GitHub now current).
- [x] **First-scan-after-boot is spurious** (fixed 2026-06-14): added a
      `network-ready` preflight (`homelab_guardian/network_ready.py`) that the
      scan loop awaits **before the first scan only** — later scans run ungated
      so a real outage still surfaces. Probe hosts auto-derive from the network
      collector's dns/tcp/tls/http checks (loopback skipped, de-duped) or an
      explicit `app.network_ready.hosts`; retries with backoff until a probe
      resolves or `timeout_seconds` (default 120) elapses, then scans anyway.
      Best-effort: never blocks the loop on error. 7 tests; config knob
      documented in `config.example.yaml`.

## Sprint 10 — dashboard auth foundation (2026-06-13)

The prerequisite for guided config-edits-from-the-dashboard (and, later, an HTTP
MCP transport — same auth layer). Mechanisms, not per-provider code. See
`docs/auth.md`.

- [x] **`basic`** — built-in HTTP Basic username/password (constant-time
      compare; `password_env` preferred over plaintext). Zero deps.
- [x] **`forward_auth`** — trust identity headers from an upstream proxy, but
      ONLY from a source IP in `trusted_proxies` (spoof-proof). One mode covers
      Authelia, Authentik, oauth2-proxy, Cloudflare Access, Traefik/Caddy/nginx.
- [x] **`oidc`** — native OpenID Connect (Authentik/Keycloak/Zitadel/…),
      auth-code flow + PKCE, in-memory cookie sessions. No crypto dep: the
      id_token is trusted via the direct back-channel TLS token exchange, with
      aud/iss/exp/nonce validated. `requests` only (already a dep).
- [x] `web.auth` config (mode + per-mode keys), `/healthz` left open, gate wired
      into the stdlib handler. 170 tests incl. end-to-end server-thread gating.
- [x] **Guided config edits (v1)**: a `/settings` page (gear link in the
      dashboard header) with collector enable/disable toggles. Auth-gated +
      CSRF-protected; editing requires auth to be ON (read-only, with a notice,
      when mode=none — you can't rewrite the host config over an unauthenticated
      network surface). Writes are comment-preserving surgical edits to
      config.yaml (the source of truth), validated + atomic with a .bak. The
      running scan picks up changes automatically. `configedit.py` + 12 tests.
- [ ] Guided edits v2: thresholds, add/remove targets, app settings — likely
      wants a comment-preserving round-trip lib (ruamel) for nested edits.
- [x] OIDC end-to-end dogfood (done 2026-06-14): Guardian client registered in
      the user's Authentik, live config set to `web.auth.mode: oidc`
      (guardian.example.com via Cloudflare tunnel → Marcus:8674), browser
      login round-trip confirmed by the user, client_secret moved to the
      hermes-keys Bitwarden vault.

## Sprint 9 — MCP server (2026-06-13)

Expose Guardian over the Model Context Protocol so any agent (Marcus, Claude)
gets its structured "eyes" instead of re-deriving homelab state. Design +
connection details in `docs/mcp.md`.

- [x] **Read-only MCP server** (`guardian mcp`, FastMCP over stdio): tools
      `get_health_summary`, `list_problems`, `list_checks`, `get_check`,
      `get_recent_changes`, `list_scan_history` + a `guardian://health`
      resource. Thin layer over the existing `db`/`web`/`diff` helpers — reads
      the same snapshot the web view renders; runs no scans, mutates nothing.
- [x] Optional `[mcp]` extra (`pip install 'homelab-guardian[mcp]'`) so core
      stays dependency-light; data layer unit-tested without `mcp` (7 tests).
- [x] Dogfood: install the extra on Marcus, point Marcus's MCP config at
      `guardian mcp` (done 2026-06-14, Door A step 1). `pip install -e '.[mcp]'`
      into Guardian's venv (importable from any cwd + `guardian` console cmd);
      `guardian` stdio entry added to `~/.hermes/config.yaml` mcp_servers;
      MCP db path now resolves against the config dir so the foreign-cwd launch
      reads the right snapshot. Verified: `hermes mcp test guardian` →
      `✓ Connected (763ms)`, 6 tools discovered.
      Remaining: the *behavioural* half — make Marcus actually prefer Guardian
      for health questions / stop double-alerting (a hermes agent-policy change,
      not transport). Capability is now in place.
- [x] **Acknowledgement tools (gated write phase)** (done 2026-06-14, Step 2):
      `acknowledge_check` / `unacknowledge_check` MCP tools, gated by
      `mcp.allow_writes` (default false → tools not even registered). Same
      reversible mute as `guardian ack`. Plus a read-only `list_acknowledgments`.
      Data layer unit-tested without the mcp dep (218 tests); gating + write
      round-trip dogfooded on Marcus (7 tools off / 9 on; ack→DB→unack verified).
- [x] **Fail-to-ACK fallback (Step 2b)** (done 2026-06-14): agent-mode
      critical-fallback upgraded from fails-to-deliver to true fails-to-ack.
      `pending_alerts` table; `acknowledge_alert_received` / `list_pending_alerts`
      MCP tools (always on); criticals the agent accepts get a deadline
      (`agent.ack_timeout_minutes`, default 10) and go to Telegram if not
      confirmed relayed. Serve loop rechecks every 60s. 227 tests; both paths
      dogfooded live on Marcus (webhook prompt now tells Marcus to call back).
- [ ] **Approval-gated repair playbooks** (whitelisted, never raw shell).
      - [x] 3a: safety design doc drafted (`docs/repair.md`, 2026-06-14) —
            AWAITING SIGN-OFF before code. Propose(agent)/approve(human-only)/
            execute/verify; `repair.enabled` default off; first playbook
            `restart_systemd_unit`; 4 open questions for the user.
      - [x] 3b (done 2026-06-14, commit 64b5cd0): `repair.py` framework +
            `restart_systemd_unit`; `repair_proposals` audit table; CLI
            `guardian repair {list|propose|approve|deny|execute|log}` (approve =
            human/CLI only); MCP propose/execute/list/log gated by
            `repair.enabled` (no approve tool). 239 tests. Dogfooded live on
            Marcus with a dummy unit + scoped sudoers: detect→propose→refuse-
            unapproved→approve→real `sudo systemctl restart`→verify recovered→
            audit; agent (MCP) proved unable to self-approve. Artifacts cleaned
            up; live config untouched (repair stays disabled).
      - [x] restart_container playbook (done 2026-06-14, commit d9c6ba0):
            same propose/approve/execute/verify shape; container name from the
            failing docker_container_* check's evidence, allowlisted, `docker
            restart <name>` (or sudo). 243 tests. Dogfooded live on Marcus with a
            throwaway alpine container: detect(exited)→propose→refuse-unapproved
            →approve→real `docker restart`→verify(running)→audit; cleaned up.
      - [x] 3c (done 2026-06-14, commit 42ee1cb): `/repairs` dashboard page,
            Approve/Deny per proposal (auth + CSRF, reusing the settings write
            surface); 🔧 header link with pending-count badge when repairs
            enabled. Approval channel ONLY — never executes (execution stays
            MCP/CLI). 251 tests (8 real-HTTP); box-smoked on an isolated serve.
      - [x] 3d-a/3d-b (done 2026-06-14, commits 5de9c7c/9373591): reclaim design
            + framework (preview/preconditions/risk-tiers) + cache/log playbooks
            (docker_prune/journal_vacuum/apt_clean). 259 tests; preview dogfooded
            read-only on the box. Reclaim stays disabled on live.
      - [x] 3d-c (done 2026-06-14, commit aecff7d): prune_dir (find -delete over
            allowed_paths + age filter) + backup interlock (not-ok Backups →
            hard refuse; no Backups → warn unless require_fresh_backup) + warn
            hook + optional require_typed_confirmation (CLI --confirm / MCP).
            266 tests; dogfooded live on a throwaway dir (real find -delete,
            backup warning, typed-confirm all proven). Reclaim family complete.
      - [x] ARMED on Marcus (go-live 2026-06-14): mcp.allow_writes + repair
            (restart_systemd_unit[hermes-dashboard], auto_approve off); live
            end-to-end dogfood on hermes-dashboard. Running as intended.
      - [ ] expand repair coverage on Marcus (more watched units/allowlist +
            scoped sudoers) as the system is fleshed out
- [ ] **HTTP transport + auth** once the dashboard auth foundation lands.

## Sprint 7 — reliability quartet (2026-06-13)

- [x] Disk space collector (thresholds, defaults to the system drive)
- [x] TLS certificate expiry checks incl. self-signed (dependency-free DER
      validity parse), validated live against LE and Proxmox certs
- [x] Snapshot retention pruning (app.retention_days, default 60)
- [x] Flap damping: per-check alert state machine, confirm_scans gating,
      symmetric recovery confirmation, ack-aware

## Sprint 6 — Docker validation, GHCR, systemd collector (2026-06-13)

- [x] Real Docker host test with direct socket mode (Marcus, Docker 29.1.3:
      healthy/exited/unhealthy containers detected correctly)
- [x] Containerized one-shot compose run validated on a real Docker host
- [x] Docker image build verification on a Docker host
- [x] GHCR multi-arch image workflow (amd64 + arm64)
- [x] systemd collector: failed + restart-loop sweeps (system/user bus),
      watched units, old-systemd fallback — found 2 real failed services
      on first production scan
- [x] Socket proxy mode test on a real Docker host (Leatherface Docker guest: 6 containers matched through proxy, write probe blocked, no test artifacts left behind)
- [ ] Consider shipping bws CLI in the Docker image for the bitwarden
      secrets provider in containers

## Sprint 5 — UI polish + acknowledgments (2026-06-13)

- [x] Web UI: centered count pills, dark/light theme toggle with
      localStorage persistence, healthy checks grouped by category in columns
- [x] Ack system: guardian ack/unack CLI, SQLite acks table, optional expiry
- [x] Acked checks muted across overall status, diff, Telegram, AI prompt,
      report, and web view (collapsed section with notes)

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
- [x] Rename `app` package to `homelab_guardian` before any PyPI release
- [x] Pick a license before publishing (AGPL-3.0-or-later)
- [x] Prebuilt Docker image (GHCR) via GitHub Actions

## Sprint 2 — diff, notify, schedule, explain (2026-06-12)

- [x] Snapshot diffing with what-changed report section
- [x] verify_tls option for self-signed homelab HTTPS services
- [x] Real homelab dogfood: 18 checks across Proxmox x2, TrueNAS, QNAP,
      iDRAC, Pi-hole, Portainer, Authentik, Home Assistant, modem, SSH hosts
- [x] Telegram notification adapter (send_on: always | changes | problems)
- [x] --interval loop mode plus example systemd user service
- [x] BYOM AI briefing layer over one OpenAI-compatible endpoint
- [ ] Per-config database_path guidance enforcement (currently docs-only)

## Future collector enhancements

- [ ] **DNS answer-assertion (split-horizon validation)**: extend the network
      collector's `dns_checks` to support `server:` (query a specific resolver,
      e.g. Pi-hole at 192.168.50.30) and `expected:` (assert the returned A
      record, e.g. port.example.com -> 192.168.50.20). Today the DNS check is
      resolvability-only via the system resolver. This is the one capability
      Marcus's retired lobby_network_watchdog had that Guardian lacks; deferred
      by choice (a broken split-horizon would still surface via the dependent
      http_checks failing). Broadly useful for any split-DNS homelab, not
      Marcus-specific. Needs dnspython or a small UDP DNS query (no system dep).

## Future remote-collector support

- [ ] Design remote Docker collector transport without giving the app broad shell access
- [ ] Support remote host inventory snapshots over least-privilege APIs
- [ ] Support remote backup freshness checks for NAS paths not mounted locally
- [ ] Keep remote collectors optional and read-only by default
