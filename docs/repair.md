# Approval-gated repair playbooks — safety design

**Status:** DESIGN — for review and sign-off **before** any repair code is
written. Nothing in this document is implemented yet.

This is the headline of Guardian's "safe actuator" arc: the step that turns
*"here's what's wrong and what I'd do about it"* into *"…want me to do it?"* —
without ever handing an LLM a shell.

## The principle

Guardian's whole identity is **deterministic, safety-grounded homelab ops**. A
repair feature could undermine that in one careless line. So the design is built
around a single rule:

> **Guardian executes a small set of named, parameterized, whitelisted actions —
> never raw shell, never an LLM-generated command — and only after a human has
> approved that specific action through a channel Guardian itself controls.**

Everything below exists to make that rule true and keep it true.

The loop this completes is the product thesis, already proven by hand on Marcus
(openipmi / systemd-networkd-wait-online): **detect → diagnose → propose →
approve → repair → verify.** Collectors detect; the `recommended_action` field
diagnoses; this layer proposes, gets approval, repairs, and verifies.

## What an LLM may and may not do

The most important boundary. The agent (Marcus, Claude, …) is the *conversation*
and the *proposer*. It is **never** the *authority*.

| Stage | Who | Enforced by |
|---|---|---|
| Detect a problem | Guardian collectors | deterministic scan |
| Propose a repair | agent **or** human | registry validation (action must exist + apply to a really-failing check) |
| **Approve** the repair | **human only** | Guardian, out-of-band, authenticated |
| Execute the repair | anyone holding an approved proposal | Guardian: refuses unless that proposal is `approved`, unexpired, unexecuted |
| Verify recovery | Guardian | re-runs the check |

Separating **propose** (agent may) from **approve** (human only, Guardian-enforced)
from **execute** (requires an approved proposal) is what keeps the LLM out of the
authorization path. The agent can propose all day; nothing runs until a human
approves through Guardian's own channel. A confused or prompt-injected agent
cannot self-authorize a repair, because approval does not live in the agent.

## Threat model

What we are defending against, and how.

- **A compromised or confused agent proposes something destructive.** → Allowlist
  (default-deny): only registered actions on explicitly-allowed targets can even
  be *proposed*. Human approval is required to *execute*. Blast radius is bounded
  per action.
- **Prompt injection via collector evidence** (e.g. a hostile container named
  `"; rm -rf /"`). → Actions are never assembled from free-form text. Parameters
  come from a validated registry and are checked against explicit allowlists
  (e.g. a unit name must be in `allowed_units`); they are passed as argv
  elements, never interpolated into a shell string. There is no shell.
- **The agent tries to skip approval and just execute.** → `execute_repair`
  refuses any proposal that is not `approved`. Approval is recorded by Guardian
  when a human confirms through the dashboard / CLI / Telegram — not when the
  agent says so.
- **Replay / stale approval.** → Proposals are single-use and time-bound. An
  approved proposal that is executed, expired, or superseded cannot run again.
- **Privilege escalation.** → Repairs use the least privilege necessary and a
  *scoped* mechanism (see Privilege), never Guardian's broad ambient rights.
- **A repair makes things worse / flaps.** → Loop guard (max attempts per
  window), per-action timeout, and mandatory post-repair verification; a repair
  that doesn't recover the check is reported as failed, not retried blindly.

## Default posture: off

Repairs are gated by their **own** master switch, stronger and separate from
`mcp.allow_writes` (which only governs ack-muting):

```yaml
repair:
  enabled: false            # master switch. Default off. Nothing can execute.
  require_approval: true     # human approval required (default). See auto-approve.
  audit_log: data/repairs.db # append-only record of every proposal/approval/run
  playbooks:
    restart_systemd_unit:
      enabled: false
      allowed_units: []      # explicit allowlist — empty means nothing is repairable
      auto_approve: false    # pre-authorize this action without per-instance approval
      max_attempts_per_hour: 3
```

With `repair.enabled: false` the repair MCP tools are not even registered and no
action can run — same fail-safe-by-construction approach as the read-only MCP
default and `allow_writes`.

## The playbook registry

A playbook is a **bounded, parameterized action**, defined in code (not config —
config only *enables and scopes* it). Each entry declares:

| Field | Meaning |
|---|---|
| `id` | unique name, e.g. `restart_systemd_unit` |
| `applies_to` | which checks it can repair (collector / check-id pattern) — a repair must map to a real, currently-failing check |
| `params` | typed parameters + their validation (e.g. `unit` ∈ `allowed_units`) |
| `run` | the bounded action: a Guardian function that builds an **argv list** (never a shell string) for one specific, narrow operation |
| `blast_radius` | one-line human description of the worst case |
| `reversible` | whether/how it can be undone |
| `verify` | which collector/check to re-run to confirm recovery |
| `privilege` | the exact privilege the action needs |
| `auto_approvable` | whether it is *eligible* for the auto-approve opt-in (most are not) |

The registry is the allowlist. If it isn't a registered playbook, Guardian
cannot do it. There is deliberately no generic "run command" playbook, ever.

## MCP / CLI surface

```
list_repair_actions(check_id?)   read  — what repairs apply to a (failing) check
propose_repair(check_id, action, params?)
                                 write — validate + create a proposal; returns a
                                         human-readable plan + proposal_id. NO execution.
execute_repair(proposal_id)      write — runs ONLY if the proposal is approved,
                                         unexpired, unexecuted; then auto-verifies.
get_repair_log(limit?)           read  — the audit trail
```

`propose_repair` is effectively a **dry run**: it returns exactly what would
happen (the resolved action, target, blast radius, reversibility, the verify
step) and changes nothing. The agent relays this to the user in plain language.

Approval is **not** an MCP tool the agent can call. It happens out-of-band:

- **Dashboard** — an authenticated *Approve / Deny* control on the proposal,
  reusing the Step 1.5 auth + CSRF surface (the same gate that already guards the
  settings write surface).
- **CLI** — `guardian repair approve <proposal_id>` / `guardian repair deny`.
- **Telegram (optional)** — Guardian sends the proposal with inline
  Approve/Deny buttons, reusing the notification path.

`execute_repair` checks Guardian's own approval record. The agent layer
(hermes/Marcus) can add a *second* gate — hermes already supports per-tool
human approval via Telegram buttons — so a careful deployment requires approval
*twice*: once in Guardian, once in the agent. Defense in depth, not redundancy.

## First playbook (and only the first)

Ship exactly one to start, the one the dogfood record already justifies:

**`restart_systemd_unit`** — restart a watched unit that is `failed` or stuck in
a restart loop.
- `applies_to`: `systemd_*` checks reporting a failed/looping unit.
- `params`: `unit` — **must** be in `repair.playbooks.restart_systemd_unit.allowed_units`
  (a user-blessed subset of the units the systemd collector already watches).
- `run`: `systemctl [--user] restart <unit>` as an argv list, with a timeout.
- `blast_radius`: that one service restarts (a brief interruption of just it).
- `reversible`: a restart is not "undoable", but it is the standard, low-risk
  recovery; the prior state was "failed", so the downside is bounded.
- `verify`: re-run the systemd collector for that unit; report `ok` or still-failed.
- `loop guard`: at most `max_attempts_per_hour`; after that, stop and escalate to
  the human ("I've restarted X three times this hour and it keeps failing — this
  needs you"). A repair that can't fix it must not become a restart loop of its own.

Candidates for *later* (each its own design + sign-off): `restart_container`
(Docker), `prune_path` (a specific, safe directory — higher risk, needs care),
`renew_cert`. None ship with the first cut.

## Privilege

Most useful repairs need elevated rights (restarting a *system* unit needs
root). The rule: **least privilege, scoped to the exact action — never
Guardian's broad ambient privilege.**

On Marcus today the service user has *passwordless sudo*, which is convenient but
far too broad to back a repair feature. The intended model instead:

- A **minimal sudoers allowlist** granting exactly the repair argv we ship and
  nothing else, e.g. `NOPASSWD: /usr/bin/systemctl restart marcus-backup.service`
  (one line per allowed unit), **or** a polkit rule scoped the same way.
- User-bus units (`systemctl --user`) need no elevation at all — prefer these
  where possible.
- Guardian's repair runner refuses to invoke anything not expressible through
  that scoped grant.

This keeps "Guardian can restart these three named services" from ever meaning
"Guardian can run anything as root."

## Audit & observability

Every proposal, approval, execution, and verification is recorded append-only
(proposed_by, approved_by, timestamps, resolved action + params, exit result,
verify result). Surfaced in:
- the dashboard (a repairs panel / history),
- `guardian repair log`,
- the `get_repair_log` MCP read tool,
and optionally announced over the notification channel. If a repair ran, you can
always see who proposed it, who approved it, what exactly executed, and whether
it worked.

## Failure handling

- **Timeout** per action; a hung action is killed and reported.
- **Partial / non-zero exit** → reported as failed; verification still runs and
  reports reality. Never reported as success on a bad exit.
- **Verify-failed** (ran cleanly but the check didn't recover) → surfaced
  explicitly: "the repair executed but the problem persists."
- **Loop guard** as above.
- A failed repair never silently retries.

## Phased rollout

- **3a — this doc + sign-off.** ← we are here.
- **3b — framework + the one playbook.** Registry, `propose`/`execute`/`verify`,
  audit store, gating (`repair.enabled`), and `restart_systemd_unit` with
  `require_approval: true` and `auto_approve: false`. Dogfood on Marcus against a
  real failed unit (the openipmi case), approving via CLI first.
- **3c — approval UX.** Dashboard Approve/Deny (reuse auth+CSRF); optional
  Telegram buttons; repairs panel in the dashboard.
- **3d — breadth.** More playbooks; opt-in `auto_approve` for vetted, idempotent,
  narrowly-scoped actions only.

## Open questions for sign-off

1. **Approval channel** to build first: dashboard button, CLI, Telegram inline,
   or rely on the agent's own hermes approval layer? (Recommendation: CLI for 3b
   to keep it simple, dashboard for 3c.)
2. **Privilege:** stand up a scoped sudoers allowlist for the specific repair
   argv (recommended), versus leaning on the existing passwordless sudo (not
   recommended)?
3. **Auto-approve:** keep `auto_approve` strictly off for the first release and
   require human approval for everything, or pre-authorize `restart_systemd_unit`
   for a named unit or two from the start?
4. **First playbook scope:** is `restart_systemd_unit` the right and only
   starting action, or do you want `restart_container` in the first cut too?
