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

Plus one resource: `guardian://health` (the summary as JSON).

Tool descriptions are written for the agent's benefit — they state *when* to call
each tool, which materially improves should-call behaviour on recent models.

## Transport

- **stdio (v1, default).** The client launches `guardian mcp` as a subprocess and
  talks over stdin/stdout. No network, no port, no auth — process-level trust.
  Ideal when the agent runs on the same host as Guardian (e.g. Marcus).
- **Streamable HTTP (future).** For a remote agent, FastMCP can serve over HTTP
  (`mcp.run(transport="streamable-http")`). That exposes a network surface and
  therefore needs auth first — the same auth prerequisite as the web dashboard.
  Not enabled in v1.

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

## Roadmap

1. **v1 (done):** read-only stdio server, the six tools + health resource.
2. **Acknowledgement tools (gated):** `acknowledge_check` / `unacknowledge_check`
   — the first *write* surface. Mutates the acks table, so it ships behind an
   explicit opt-in (a config flag and/or MCP `always_ask` confirmation), never on
   by default.
3. **Approval-gated repair playbooks:** whitelisted, never raw shell — the
   detect→diagnose→approve→repair→verify loop, exposed as tools an agent proposes
   and a human confirms.
4. **HTTP transport + auth:** once the dashboard auth foundation lands, reuse it
   to serve MCP to remote agents.
