# Roadmap

Guardian's bet: **the safety layer between an AI agent and root.** Plenty of
tools can tell you a service is down; Guardian gives any MCP-capable agent
eyes (structured health data) and hands (approval-gated, allowlisted,
audited repairs) without ever trusting it with a shell. Monitoring is the
substrate; the deterministic actuation contract is the product.

## Shipped (v0.1 → v0.3.x)

The founding arc is complete and runs in production on the maintainer's
homelab:

- **Monitor** — 12 read-only collectors (Docker, Home Assistant,
  DNS/TCP/HTTP/TLS, disks, mounts, systemd, firewall, SSH, exposed services,
  updates, backup health), snapshot diffing, flap damping, acknowledgments.
- **Report** — Markdown reports, read-only web dashboard with auth modes
  (basic / forward-auth / native OIDC), guided config edits, optional BYOM
  AI briefing, flap-damped Telegram notifications.
- **Attach an agent** — MCP server (stdio + bearer-gated HTTP), agent-webhook
  notification mode with HMAC signing and a deterministic Telegram fallback
  for criticals the agent fails to acknowledge.
- **Act** — approval-gated repair playbooks (restart unit/container, remount,
  reclaim family) with risk tiers, allowlists, argv-only execution, scoped
  sudoers, loop guards, backup interlocks, append-only audit, opt-in
  auto-repair for non-destructive reflexes, and reflex→specialist→human
  escalation.
- **Prove it** — `guardian drill`: scripted incident drills that score an
  attached agent's detect→diagnose→repair against ground truth, and ten
  adversarial safety probes that assert the gate holds regardless of how the
  agent behaves. Runs in CI with no model; the probes are themselves tested
  for their ability to fail.

## Now — the solid base

Trust artifacts before new features:

- [x] Published threat model (`docs/threat-model.md`) + `SECURITY.md`
- [x] bandit + pip-audit gates in CI (audit found zero true positives)
- [x] PEP 639 SPDX license metadata
- [ ] CLI migrated to argparse subparsers (before multi-host adds commands)
- [ ] Split-horizon DNS assertion (`dns_checks` gains `server:`/`expected:`)
- [ ] Guided edits v2 — thresholds and targets from `/settings`
  (comment-preserving nested edits)

## Next — brand, reach, and depth

- Brand identity + logo; dashboard reskin (CSS-only first — the stdlib,
  no-framework dashboard is deliberate and stays)
- Public landing page (GitHub Pages) + a recorded demo of the real incident
  loop: detect → agent narrates → "go ahead" → gated repair → verify
- Launch sequence: MCP directory listings → r/selfhosted → Show HN, in that
  order, only once the trust artifacts are live
- **Multi-host via agentless SSH** — a `hosts:` block, collectors run over
  `ssh host -- <argv>` with the same argv-only discipline, host-namespaced
  snapshots, MCP tools gain a `host` parameter. (Satellite agents are
  deliberately rejected for now: they multiply the trust surface.)
- Dashboard structural pass riding a `web/` package split: an activity
  timeline (repairs, approvals, agent actions) and a richer repair-approval
  view
- More demo-worthy playbooks: restic unlock, service rollback, cert renew
- More drills: a flapping unit, a mount that will not remount, an agent handed
  a check whose evidence has been tampered with
- MCP prompts/resources so any client is good at Guardian on first connect

## Later

- Prometheus `/metrics` exporter (coexist with Grafana stacks)
- ntfy + generic webhook notifiers
- Entry-points plugin API for third-party collectors (after multi-host
  settles the collector interface)
- Paid hosted features above the single host (alert relay, fleet view) —
  the self-hosted tool stays complete and free (AGPL)

## v1.0 criteria

Threat model published; subparser CLI; `web/` package split; split-horizon
DNS + guided-edits v2 shipped; 30 days of production soak with zero
priority-1 incidents.

## Non-goals

- Windows/macOS collector or repair parity (homelab servers are Linux; the
  core and network collectors stay cross-platform)
- A JS framework or build step for the dashboard
- A generic "run command" repair playbook — never
