# Guardian as an MCP server

**Status:** read-only v1 implemented (`guardian mcp`). Acknowledgement / repair
tools are a deliberately separate, gated future phase (see Roadmap below).

## Why

Guardian's moat is its collectors and the structured health-check contract
(`status` / `summary` / `evidence` / `recommended_action`). Exposing that over the
[Model Context Protocol](https://modelcontextprotocol.io) lets **any** agent read
Guardian's view of the homelab instead of re-deriving it. The motivating case:
Marcus (the home agent) consumes Guardian over MCP and reasons about real,
verified state — rather than Guardian and Marcus both alerting the user
separately about the same thing.

## Design

- **Read-only.** Every tool reads the latest SQLite snapshot the scanner already
  writes — the same data the web view renders. Nothing in the MCP server runs a
  scan, changes an ack, or touches a host. This keeps the safety story intact: a
  read-only MCP server is safe to attach to an agent.
- **Thin over existing helpers.** `mcp_server.py` reuses `db`, `web`
  (`checks_from_snapshot`, `effective_group`, `overall_of`), and `diff`. The
  data layer is plain functions returning JSON-friendly dicts (`summary_payload`,
  `problems_payload`, …), unit-tested without the `mcp` dependency. FastMCP just
  decorates them.
- **Optional dependency.** `pip install 'homelab-guardian[mcp]'` pulls the `mcp`
  SDK; the core stays dependency-light. `guardian mcp` prints an install hint if
  the extra is missing.

## Tool surface (read-only)

| Tool | Returns |
|---|---|
| `get_health_summary()` | overall status, per-status counts, per-group roll-up — the "is it OK?" answer |
| `list_problems()` | failing, non-acknowledged checks, worst first, each with its recommended next step |
| `list_checks(group?, status?)` | all checks, optionally filtered |
| `get_check(check_id)` | one check's full detail incl. the raw `evidence` dict |
| `get_recent_changes()` | regressions / improvements / new / removed since the previous scan |
| `list_scan_history(limit)` | recent scans with per-scan overall status and counts |
| `list_acknowledgments()` | checks currently muted (acknowledged), with note + expiry |
| `list_pending_alerts()` | criticals pushed to this agent awaiting its relay-confirmation |
| `acknowledge_alert_received(check_ids)` | the agent confirms it relayed these criticals (clears the fail-to-ack fallback) — see below |

Plus one resource: `guardian://health` (the summary as JSON).

`acknowledge_alert_received` is always available (not behind `allow_writes`): it
only clears Guardian's fallback bookkeeping, not health state, and the
fail-to-ack safety net depends on it working whenever an agent is attached. In
agent-delivery mode (`notifications.mode: agent`), Guardian pushes a confirmed
critical to the agent's webhook and starts a timer; the agent should call
`acknowledge_alert_received([...])` once it has relayed the critical to the user.
If no callback arrives within `notifications.agent.ack_timeout_minutes`, Guardian
sends the critical over Telegram itself — so a critical is never silently lost
even if the agent accepted it but failed to act.

### Write tools (opt-in, `mcp.allow_writes: true`)

Off by default. When enabled, two WRITE tools are registered so an agent can
manage acknowledgements on the user's behalf — the same mute the `guardian ack`
CLI performs (reversible; muted checks are excluded from overall status and
alerts). With `allow_writes` false they are not registered at all, so an attached
agent cannot mutate Guardian's state.

| Tool | Effect |
|---|---|
| `acknowledge_check(check_id, note, days)` | mute a check; `days` auto-expires the mute (0 = indefinite) |
| `unacknowledge_check(check_id)` | un-mute a check so it counts and alerts again |

Tool descriptions are written for the agent's benefit — they state *when* to call
each tool, which materially improves should-call behaviour on recent models.

## Transport

- **stdio (v1, default).** The client launches `guardian mcp` as a subprocess and
  talks over stdin/stdout. No network, no port, no auth — process-level trust.
  Ideal when the agent runs on the same host as Guardian (e.g. Marcus).
- **Streamable HTTP (`guardian mcp --http`).** For a *remote* agent. Serves over
  streamable HTTP, gated by a **bearer token** (`mcp.http.token_env`) — clients
  send `Authorization: Bearer <token>`. It **refuses to start without a token**,
  so the network surface is never unauthenticated. Default bind `127.0.0.1:8675`
  (override with `--host`/`--port`). For SSO/OIDC, front it with a reverse proxy
  (same mechanisms-not-providers stance as the dashboard).

```yaml
mcp:
  http:
    token_env: GUARDIAN_MCP_TOKEN   # the bearer token, from env or the secrets provider
```

## Connecting a client

**Claude Desktop / Claude Code** — add an MCP server entry pointing at the
console command (run it from the Guardian checkout / venv):

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

**Claude API (local stdio server)** — use the Anthropic SDK's MCP helpers
(`pip install 'anthropic[mcp]'`):

```python
from anthropic import AsyncAnthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

client = AsyncAnthropic()
params = StdioServerParameters(command="guardian", args=["mcp", "--config", "config.yaml"])
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        runner = client.beta.messages.tool_runner(
            model="claude-opus-4-8", max_tokens=16000,
            messages=[{"role": "user", "content": "Is my homelab healthy? What needs attention?"}],
            tools=[async_mcp_tool(t, session) for t in tools],
        )
        async for message in runner:
            print(message)
```

**Marcus (hermes-agent)** — point its MCP config at the same stdio command. This
is the configuration that lets Marcus consume Guardian instead of double-alerting.
hermes-agent uses a `mcp_servers.<name>` block (stdio `command`/`args`); the live
wiring on Marcus is:

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  guardian:
    command: /home/marcus/homelab-guardian/.venv/bin/guardian
    args: [mcp, --config, /home/marcus/homelab-guardian/config.yaml]
    enabled: true
    timeout: 30
    tools:
      resources: true
```

Two prerequisites the stdio launch depends on, both because the client starts
`guardian mcp` from **its own** working directory, not Guardian's:

1. The package must be importable from any cwd — `pip install -e '.[mcp]'` into
   Guardian's venv (this also creates the `guardian` console command). Running
   straight from the source tree only works when cwd is the checkout.
2. A relative `app.database_path` is resolved against the **config file's**
   directory (see `resolve_database_path`), so the server reads the same
   snapshot the scanner writes — regardless of where the client launched it.

Verify with hermes's own tooling: `hermes mcp test guardian` should report
`✓ Connected` and `✓ Tools discovered: 6`.

## Roadmap

1. **v1 (done):** read-only stdio server, the six tools + health resource.
   **Wired live on Marcus (2026-06-14):** `hermes mcp test guardian` →
   `✓ Connected`, 6 tools. Marcus can now read Guardian's view over MCP.
   *Remaining behavioural step: have Marcus actually prefer Guardian for
   homelab-health questions / stop its own redundant alerting — an agent-policy
   change on the hermes side, separate from this transport wiring.*
2. **Acknowledgement tools (done — gated):** `acknowledge_check` /
   `unacknowledge_check` — the first *write* surface. Mutates the acks table, so
   it ships behind the explicit opt-in `mcp.allow_writes` (default false): the
   tools are not registered at all unless enabled. The same mute as `guardian
   ack`, reversible.
3. **Approval-gated repair playbooks:** whitelisted, never raw shell — the
   detect→diagnose→approve→repair→verify loop, exposed as tools an agent proposes
   and a human confirms.
4. **HTTP transport + auth (done):** `guardian mcp --http` serves streamable HTTP
   gated by a bearer token, refusing to start unauthenticated — so a remote agent
   can consume Guardian, not just a same-host one.
