from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homelab_guardian import db
from homelab_guardian import web
from homelab_guardian.config import load_config
from homelab_guardian.diff import diff_scans

# Guardian as an MCP server: it exposes the latest scan's structured health data
# to any MCP client (an agent, Claude Desktop, ...) so the agent gets Guardian's
# "eyes" instead of re-deriving homelab state itself. This is the same read-only
# data the web view renders, surfaced over the Model Context Protocol.
#
# Read-only by default: every read tool reads the SQLite snapshot the scanner
# writes; nothing runs a scan or touches a host. The acknowledge/unacknowledge
# WRITE tools (the only mutation: muting a check, exactly what `guardian ack`
# does) are registered ONLY when config `mcp.allow_writes` is true — opt-in, so
# an agent attached with the default posture cannot change Guardian's state.
# Repair tools remain a separate, more strongly gated future phase.
#
# The data layer below is plain functions returning JSON-friendly dicts, so it is
# unit-testable without the `mcp` dependency. The thin FastMCP wiring in
# build_server() just decorates them; importing FastMCP is deferred so the rest
# of Guardian never depends on it.


def _latest_checks(database_path: str):
    """(scan_id, created_at, [HealthCheck]) for the newest scan, or None."""
    conn = db.connect(database_path)
    try:
        scan = db.load_latest_scan(conn)
        if scan is None:
            return None
        return scan[0], scan[1], web.checks_from_snapshot(scan[2])
    finally:
        conn.close()


def summary_payload(database_path: str) -> dict[str, Any]:
    """Overall homelab health plus a per-group roll-up — the 'is it OK?' answer."""
    latest = _latest_checks(database_path)
    if latest is None:
        return {"overall": "unknown", "message": "No scans recorded yet."}
    scan_id, created_at, checks = latest
    active = [c for c in checks if not c.acknowledged]
    counts = {s: sum(1 for c in active if c.status == s) for s in web.STATUS_ORDER}

    grouped: dict[str, list] = {}
    for check in active:
        grouped.setdefault(web.effective_group(check), []).append(check)
    groups = []
    for name in sorted(grouped, key=lambda n: (web.STATUS_ORDER.index(web._worst(grouped[n])), n.lower())):
        members = grouped[name]
        groups.append({
            "group": name,
            "status": web._worst(members),
            "counts": {s: sum(1 for c in members if c.status == s) for s in web.STATUS_ORDER if any(c.status == s for c in members)},
        })

    return {
        "overall": web.overall_of(checks),
        "scan_id": scan_id,
        "scanned_at": created_at,
        "counts": counts,
        "acknowledged": sum(1 for c in checks if c.acknowledged),
        "groups": groups,
    }


def problems_payload(database_path: str) -> list[dict[str, Any]]:
    """Every failing, non-acknowledged check, worst first, with the safest next step."""
    latest = _latest_checks(database_path)
    if latest is None:
        return []
    checks = latest[2]
    problems = [c for c in checks if c.status != "ok" and not c.acknowledged]
    problems.sort(key=lambda c: (web.STATUS_ORDER.index(c.status), c.name.lower()))
    return [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "group": web.effective_group(c),
            "summary": c.summary,
            "recommended_action": c.recommended_action,
        }
        for c in problems
    ]


def checks_payload(database_path: str, group: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    """All checks, optionally filtered by group and/or status."""
    latest = _latest_checks(database_path)
    if latest is None:
        return []
    out = []
    for c in latest[2]:
        g = web.effective_group(c)
        if group and g.lower() != group.lower():
            continue
        if status and c.status != status:
            continue
        out.append({"id": c.id, "name": c.name, "status": c.status, "group": g, "summary": c.summary, "acknowledged": c.acknowledged})
    out.sort(key=lambda r: (web.STATUS_ORDER.index(r["status"]), r["name"].lower()))
    return out


def check_payload(database_path: str, check_id: str) -> dict[str, Any] | None:
    """Full detail for one check, including its raw evidence dict."""
    latest = _latest_checks(database_path)
    if latest is None:
        return None
    for c in latest[2]:
        if c.id == check_id:
            return {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "group": web.effective_group(c),
                "summary": c.summary,
                "recommended_action": c.recommended_action,
                "evidence": c.evidence,
                "acknowledged": c.acknowledged,
                "ack_note": c.ack_note,
            }
    return None


def changes_payload(database_path: str) -> dict[str, Any]:
    """What changed since the previous scan: regressions, improvements, new/removed."""
    conn = db.connect(database_path)
    try:
        latest = db.load_latest_scan(conn)
        if latest is None:
            return {"has_previous": False, "has_changes": False, "message": "No scans recorded yet."}
        checks = web.checks_from_snapshot(latest[2])
        previous = db.load_scan_before(conn, latest[0])
        if previous is not None:
            diff = diff_scans(previous[2], checks, previous_scan_id=previous[0], previous_created_at=previous[1])
        else:
            diff = diff_scans(None, checks)
    finally:
        conn.close()
    return {
        "has_previous": diff.has_previous,
        "has_changes": diff.has_changes,
        "previous_scan_id": diff.previous_scan_id,
        "regressions": diff.regressions,
        "improvements": diff.improvements,
        "new_checks": diff.new_checks,
        "removed_checks": diff.removed_checks,
        "unchanged_count": diff.unchanged_count,
    }


def history_payload(database_path: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recent scans (newest first) with per-scan overall status and counts."""
    conn = db.connect(database_path)
    try:
        rows = db.list_scans(conn, limit=limit)
    finally:
        conn.close()
    out = []
    for scan_id, created_at, snapshot in rows:
        checks = web.checks_from_snapshot(snapshot)
        out.append({
            "scan_id": scan_id,
            "created_at": created_at,
            "overall": web.overall_of(checks),
            "counts": {s: sum(1 for c in checks if c.status == s and not c.acknowledged) for s in web.STATUS_ORDER},
        })
    return out


def list_acks_payload(database_path: str) -> list[dict[str, Any]]:
    """Currently muted (acknowledged) checks with their note and expiry. Read-only."""
    conn = db.connect(database_path)
    try:
        return db.list_acks(conn)
    finally:
        conn.close()


def acknowledge_check_payload(
    database_path: str, check_id: str, note: str = "", days: float | None = None
) -> dict[str, Any]:
    """Mute a check: it stops counting toward overall status and stops alerting.
    WRITE. `days` sets an auto-expiry; omit for an indefinite mute. Surfaces
    whether the id matched a check in the latest scan so a typo'd id is visible."""
    if not check_id:
        return {"acknowledged": False, "error": "A check_id is required."}
    from datetime import datetime, timedelta, timezone

    latest = _latest_checks(database_path)
    known = check_id in {c.id for c in latest[2]} if latest else False
    expires_at = None
    if days is not None and float(days) > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=float(days))).isoformat()

    conn = db.connect(database_path)
    try:
        db.set_ack(conn, check_id, note=note or "", expires_at=expires_at)
    finally:
        conn.close()
    return {
        "acknowledged": True,
        "check_id": check_id,
        "note": note or "",
        "expires_at": expires_at,
        "known_check": known,
    }


def unacknowledge_check_payload(database_path: str, check_id: str) -> dict[str, Any]:
    """Un-mute a previously acknowledged check so it counts and alerts again. WRITE."""
    if not check_id:
        return {"unacknowledged": False, "error": "A check_id is required."}
    conn = db.connect(database_path)
    try:
        removed = db.remove_ack(conn, check_id)
    finally:
        conn.close()
    return {
        "unacknowledged": removed,
        "check_id": check_id,
        "message": "Ack removed." if removed else "No active acknowledgment for that check id.",
    }


def pending_alerts_payload(database_path: str) -> list[dict[str, Any]]:
    """Criticals Guardian pushed to this agent that are awaiting the agent's
    confirmation it relayed them (call acknowledge_alert_received once relayed)."""
    conn = db.connect(database_path)
    try:
        return db.list_pending_alerts(conn)
    finally:
        conn.close()


def acknowledge_alerts_payload(database_path: str, check_ids: list[str]) -> dict[str, Any]:
    """Clear pending-alert tracking for the given criticals: the agent confirms it
    relayed them, so Guardian must NOT fire its Telegram fail-to-ack fallback."""
    ids = [c for c in (check_ids or []) if c]
    if not ids:
        return {"acknowledged_receipt": 0, "error": "At least one check_id is required."}
    conn = db.connect(database_path)
    try:
        cleared = db.clear_pending_alerts(conn, ids)
    finally:
        conn.close()
    return {"acknowledged_receipt": cleared, "check_ids": ids}


def build_server(database_path: str, allow_writes: bool = False, config: dict[str, Any] | None = None):
    """Construct the FastMCP server. Imports `mcp` lazily so core Guardian never
    depends on it; raises ModuleNotFoundError if the optional extra is missing.

    Read tools are always registered. The acknowledge/unacknowledge WRITE tools
    are registered only when `allow_writes` is true (config `mcp.allow_writes`),
    keeping the default posture read-only. The repair tools are registered only
    when `repair.enabled` is true (a separate, stronger gate) — and even then the
    agent only gets propose/execute/list, never approve (human-only, see
    docs/repair.md)."""
    from mcp.server.fastmcp import FastMCP

    config = config or {}
    server = FastMCP("homelab-guardian")

    @server.tool()
    def get_health_summary() -> dict:
        """Current overall homelab health: the overall status (ok / warning / critical / unknown),
        a count of checks in each status, and a per-group roll-up (Host, Infrastructure,
        Applications, Network, Security, Backups). Call this first to answer "is everything OK?"."""
        return summary_payload(database_path)

    @server.tool()
    def list_problems() -> list:
        """Every health check that is currently failing (status is not "ok") and is not
        acknowledged, worst-severity first. Each item has the status, group, a plain-English
        summary, and the safest recommended next step. Use this to see what actually needs attention."""
        return problems_payload(database_path)

    @server.tool()
    def list_checks(group: str = "", status: str = "") -> list:
        """List all health checks, optionally filtered. `group` filters by group name
        (e.g. "Security", "Infrastructure"); `status` filters by ok/warning/critical/unknown.
        Returns id, name, status, group, summary, and whether it is acknowledged."""
        return checks_payload(database_path, group or None, status or None)

    @server.tool()
    def get_check(check_id: str) -> dict:
        """Full detail for one check by its id, including the raw evidence dictionary the
        collector recorded (e.g. ports, ages, versions). Use after list_problems to investigate."""
        result = check_payload(database_path, check_id)
        return result if result is not None else {"error": f"No check found with id '{check_id}'."}

    @server.tool()
    def get_recent_changes() -> dict:
        """What changed since the previous scan: regressions (got worse), improvements
        (recovered), and any newly-added or removed checks. Use to understand recent movement."""
        return changes_payload(database_path)

    @server.tool()
    def list_scan_history(limit: int = 20) -> list:
        """Recent scan history, newest first, with each scan's overall status and status counts."""
        return history_payload(database_path, limit)

    @server.tool()
    def list_acknowledgments() -> list:
        """List checks that are currently muted (acknowledged): their id, the note
        explaining why, when it was set, and any expiry. Use to see what is being
        deliberately silenced before acknowledging or un-acknowledging anything."""
        return list_acks_payload(database_path)

    @server.tool()
    def list_pending_alerts() -> list:
        """Criticals Guardian pushed to you that you have NOT yet confirmed relaying.
        Each one will be sent to the user over Telegram (the fail-to-ack fallback) if
        you don't call acknowledge_alert_received before its deadline."""
        return pending_alerts_payload(database_path)

    @server.tool()
    def acknowledge_alert_received(check_ids: list[str]) -> dict:
        """Confirm you have RELAYED these criticals to the user, so Guardian does not
        also send them over Telegram. Call this right after you message the user about
        a critical Guardian pushed to you. Pass the check ids from the event payload.
        Not gated by mcp.allow_writes: it only clears Guardian's fallback bookkeeping
        (it does not change any health state), and the fail-to-ack safety net depends
        on it working whenever an agent is attached."""
        return acknowledge_alerts_payload(database_path, check_ids)

    @server.resource("guardian://health")
    def health_resource() -> str:
        """The latest health summary as JSON."""
        return json.dumps(summary_payload(database_path), indent=2, default=str)

    if allow_writes:
        @server.tool()
        def acknowledge_check(check_id: str, note: str = "", days: float = 0) -> dict:
            """Mute (acknowledge) a health check so it stops counting toward overall
            status and stops alerting — for a known, accepted, or being-worked-on issue.
            Set `note` to record WHY (always do this). Set `days` to auto-expire the mute
            after that many days; 0 means no expiry. WRITE: this changes Guardian's state,
            so only do it when the user explicitly asks to silence or accept a check."""
            return acknowledge_check_payload(database_path, check_id, note, days or None)

        @server.tool()
        def unacknowledge_check(check_id: str) -> dict:
            """Un-mute a previously acknowledged check so it counts toward overall status
            and alerts again. WRITE: only when the user wants a silenced check active again."""
            return unacknowledge_check_payload(database_path, check_id)

    if config.get("repair", {}).get("enabled", False):
        from homelab_guardian import repair as _repair

        @server.tool()
        def list_repair_actions(check_id: str) -> dict:
            """Repair actions available for a failing check, each with a dry-run plan
            (exactly what would run, its blast radius, reversibility). Changes nothing.
            Use to see what could fix a problem before proposing it."""
            conn = db.connect(database_path)
            try:
                check = _repair._load_check(conn, check_id)
                if check is None:
                    return {"error": f"No check '{check_id}' in the latest scan."}
                actions = _repair.applicable_actions(config, check)
                latest = _repair._all_latest_checks(conn)
                plans = []
                for a in actions:
                    try:
                        plans.append(_repair.build_plan(config, check, a, latest_checks=latest))
                    except _repair.RepairError as exc:
                        plans.append({"action": a, "blocked": str(exc)})
                return {"check_id": check_id, "status": check.status, "actions": actions, "plans": plans}
            finally:
                conn.close()

        @server.tool()
        def propose_repair(check_id: str, action: str) -> dict:
            """Propose a repair for a failing check. Returns a proposal id and the exact
            plan but DOES NOT execute it. Relay the plan to the user in plain language and
            ask them to approve — approval happens out-of-band (a human runs
            `guardian repair approve <id>`); you cannot approve it yourself. After they
            approve, call execute_repair."""
            conn = db.connect(database_path)
            try:
                return _repair.propose(config, conn, check_id, action, proposed_by="agent")
            except _repair.RepairError as exc:
                return {"error": str(exc)}
            finally:
                conn.close()

        @server.tool()
        def execute_repair(proposal_id: int) -> dict:
            """Execute a repair proposal that a human has APPROVED, then verify recovery.
            Fails unless the proposal is approved (it is refused otherwise). Only call this
            after the user has approved the specific proposal."""
            conn = db.connect(database_path)
            try:
                return _repair.execute(config, conn, proposal_id, executed_by="agent")
            except _repair.RepairError as exc:
                return {"error": str(exc)}
            finally:
                conn.close()

        @server.tool()
        def get_repair_log(limit: int = 20) -> list:
            """Recent repair proposals and their outcomes (proposed / approved / denied /
            executed / failed), newest first — the audit trail."""
            conn = db.connect(database_path)
            try:
                return db.list_repair_proposals(conn, limit=limit)
            finally:
                conn.close()

    return server


def resolve_database_path(config: dict[str, Any], config_path: str) -> str:
    """Resolve the snapshot DB path the MCP server should read. An MCP client
    launches this process from *its* working directory (e.g. the agent's home),
    not Guardian's — so a relative `database_path` is resolved against the
    config file's directory, ensuring we read the same snapshot the scanner
    (run from the Guardian dir) writes."""
    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")
    db_path = Path(database_path)
    if not db_path.is_absolute():
        db_path = Path(config_path).resolve().parent / db_path
    return str(db_path)


def run_stdio(config_path: str) -> int:
    """CLI entry: serve Guardian over MCP on stdio (a client launches this process)."""
    config = load_config(config_path)
    database_path = resolve_database_path(config, config_path)
    allow_writes = bool(config.get("mcp", {}).get("allow_writes", False))
    try:
        server = build_server(database_path, allow_writes=allow_writes, config=config)
    except ModuleNotFoundError:
        print(
            "The MCP server needs the optional 'mcp' dependency.\n"
            "Install it with:  pip install 'homelab-guardian[mcp]'",
        )
        return 1
    server.run()  # stdio transport (default); blocks until the client disconnects
    return 0
