# Guardian as an MCP server

`guardian mcp` exposes Guardian over the [Model Context Protocol](https://modelcontextprotocol.io)
so **any** MCP-capable agent — Claude Desktop, Claude Code, a custom agent, a
local model — can read your homelab's *verified* health and, if you enable it,
**propose and execute human-approved repairs**. The agent never gets a shell;
it only ever calls Guardian's named tools.

Two postures, both opt-in:

- **Read-only (default).** The agent reads Guardian's checks, problems, and
  recent changes. Nothing it can call changes a host or Guardian's state.
- **Actuator (opt-in).** With `repair.enabled` (and the same allowlists the CLI
  uses), the agent can *propose* a whitelisted repair and *execute* one **after a
  human approves it out-of-band** — the agent can never approve its own repair.

## Quick start (any MCP client)

```bash
pip install 'homelab-guardian[mcp]'    # pulls the MCP SDK; core stays light
guardian init                          # writes a config.yaml (offers MCP setup)
guardian --config config.yaml          # one scan, so the server has data to read
```

Then point your MCP client at the `guardian mcp` stdio command. **Claude Desktop
/ Claude Code:**

```json
{
  "mcpServers": {
    "homelab-guardian": {
      "command": "guardian",
      "args": ["mcp", "--config", "/path/to/config.yaml"]
    }
  }
}
```

Now ask your agent *"is my homelab healthy? what needs attention?"* — it calls
`get_health_summary` / `list_problems` and answers from verified state instead of
guessing. `guardian init` prints this same snippet for you on the way out.

> stdio launches `guardian mcp` from the **client's** working directory, so two
> things must hold (both true after a normal `pip install`): the package is
> importable from any cwd (the install handles it — don't rely on running from a
> source checkout), and a relative `app.database_path` is resolved against the
> **config file's** directory, so the server reads the same snapshot the scanner
> writes no matter where the client started it.

## Tool surface

**Read tools — always registered:**

| Tool | Returns |
|---|---|
| `get_health_summary()` | overall status, per-status counts, per-group roll-up — the "is it OK?" answer |
| `list_problems()` | failing, non-acknowledged checks, worst first, each with its recommended next step |
| `list_checks(group?, status?)` | all checks, optionally filtered |
| `get_check(check_id)` | one check's full detail incl. the raw `evidence` dict |
| `get_recent_changes()` | regressions / improvements / new / removed since the previous scan |
| `list_scan_history(limit)` | recent scans with per-scan overall status and counts |
| `list_acknowledgments()` | checks currently muted, with note + expiry |
| `list_pending_alerts()` | criticals pushed to a proactive agent awaiting relay-confirmation |
| `acknowledge_alert_received(check_ids)` | a proactive agent confirms it relayed these criticals (see *Proactive push* below) |

Plus a resource: `guardian://health` (the summary as JSON).

**Acknowledgement write tools — only when `mcp.allow_writes: true`:**

| Tool | Effect |
|---|---|
| `acknowledge_check(check_id, note, days)` | mute a check; `days` auto-expires (0 = indefinite) |
| `unacknowledge_check(check_id)` | un-mute so it counts and alerts again |

**Repair tools — only when `repair.enabled: true`:**

| Tool | Effect |
|---|---|
| `list_repair_actions(check_id)` | which whitelisted repairs apply, each with a dry-run plan (exact argv, blast radius, reversibility). Changes nothing. |
| `propose_repair(check_id, action)` | stage a repair; returns a proposal id + the exact plan. **Does not execute.** |
| `execute_repair(proposal_id, confirmation?)` | run a proposal **a human has approved**, then verify recovery. Refused unless approved. |
| `get_repair_log()` | the append-only audit trail (who proposed, who approved, what ran, did it recover) |

Tool descriptions are written *for the agent* — they state when to call each
tool and the approval rules, which materially improves should-call behaviour on
recent models. When `allow_writes` is false and `repair.enabled` is false, those
tools aren't registered at all, so an attached agent simply cannot mutate
anything — the read-only safety story is structural, not a prompt request.

## The repair flow (agent-agnostic)

The actuator loop is the same for any agent, because **approval lives outside the
agent**:

1. A check is failing. The agent calls `list_repair_actions` → `propose_repair`.
2. The agent relays the plan to you in plain language and asks you to approve.
   **It cannot approve its own proposal.**
3. *You* approve out-of-band — `guardian repair approve <id>` (CLI) or the
   `/repairs` page on the dashboard.
4. The agent calls `execute_repair(<id>)`; Guardian runs the whitelisted argv
   (never a shell), then re-checks and reports whether it recovered. Every step
   is audited (`get_repair_log`).

Destructive actions never auto-approve and carry extra interlocks (e.g. a
backup-freshness check before deleting files). See [repair.md](repair.md).

Guardian also has its **own** reflex self-healing (`auto_approve` on a playbook)
that runs with no agent at all — the agent layer is for the cases a deterministic
reflex shouldn't or can't handle.

## Transport

- **stdio (default).** The client launches `guardian mcp` as a subprocess and
  talks over stdin/stdout — no network, no port, process-level trust. Use when
  the agent runs on the **same host** as Guardian.
- **Streamable HTTP (`guardian mcp --http`).** For a **remote** agent. Gated by a
  bearer token (`mcp.http.token_env`); clients send `Authorization: Bearer
  <token>`. It **refuses to start without a token**, so the surface is never
  unauthenticated. Default bind `127.0.0.1:8675` (`--host`/`--port` to change).
  For SSO/OIDC, front it with a reverse proxy.

```yaml
mcp:
  allow_writes: false               # set true to register the ack write tools
  http:
    token_env: GUARDIAN_MCP_TOKEN   # bearer token, from env or the secrets provider
```

## Two integration tiers

**1. Pull — works with any MCP agent (Claude Desktop, Claude Code, custom).**
The agent reads Guardian and drives repair **when you ask it to**. This is the
whole loop above and needs nothing beyond the MCP wiring. For *alerting* in this
tier, use Guardian's built-in channels — the dashboard and/or Telegram
(`notifications.mode: direct`) — and let the agent investigate on demand.

**2. Proactive push — for webhook-capable agents (advanced).** With
`notifications.mode: agent`, Guardian POSTs each confirmed change to an agent's
intake webhook so the **agent becomes the single proactive voice**. It signs the
payload (HMAC) and, for criticals, starts a timer: the agent calls
`acknowledge_alert_received([...])` once it has relayed the alert; if no callback
arrives within `notifications.agent.ack_timeout_minutes`, Guardian sends the
critical over Telegram itself — so a critical is never silently lost even if the
agent accepted it but failed to act.

This tier needs an agent that can *receive a webhook and run on its own*
(Claude Desktop and most chat clients can't — they're pull-only). The reference
implementation is **hermes-agent**, which subscribes its webhook intake to
Guardian and registers `guardian mcp` as a tool server so it can both narrate
alerts and drive the repair flow. The config is ordinary MCP — a
`mcp_servers.<name>` stdio block pointing at the same `guardian mcp` command —
plus a webhook subscription on the agent side; nothing Guardian does here is
hermes-specific.

## Notes

- The whole MCP layer reads the SQLite snapshot the scanner already writes — the
  same data the web view renders. The MCP server never runs a scan itself.
- The `mcp` SDK is an optional extra; `guardian mcp` prints an install hint if
  it's missing, and the data-layer functions are unit-tested without it.
