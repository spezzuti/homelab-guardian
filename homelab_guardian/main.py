from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from homelab_guardian import db
from homelab_guardian.alerting import update_alert_states
from homelab_guardian.collectors import (
    backup_collector,
    backup_health_collector,
    disk_collector,
    docker_collector,
    exposed_services_collector,
    firewall_collector,
    homeassistant_collector,
    network_collector,
    ssh_collector,
    systemd_collector,
    updates_collector,
)
from homelab_guardian.config import load_config
from homelab_guardian.diff import diff_scans
from homelab_guardian.doctor import run_doctor
from homelab_guardian.explain import explain
from homelab_guardian.models import HealthCheck
from homelab_guardian.notifications import telegram_notifier
from homelab_guardian.reports.markdown_report import write_report
from homelab_guardian.secrets import SecretStore, build_store

CollectorFn = Callable[..., list[HealthCheck]]

COLLECTORS: dict[str, CollectorFn] = {
    "docker": docker_collector.collect,
    "homeassistant": homeassistant_collector.collect,
    "network": network_collector.collect,
    "backups": backup_collector.collect,
    "systemd": systemd_collector.collect,
    "disks": disk_collector.collect,
    "firewall": firewall_collector.collect,
    "exposed_services": exposed_services_collector.collect,
    "ssh": ssh_collector.collect,
    "updates": updates_collector.collect,
    "backup_health": backup_health_collector.collect,
}


# How often the serve loop wakes between scans to fire the agent fail-to-ack
# fallback. The ack deadline is in minutes, so a ~minute recheck keeps a
# never-acknowledged critical from waiting a whole scan interval.
OVERDUE_RECHECK_SECONDS = 60


def run_collector(
    name: str, collector: CollectorFn, config: dict[str, Any], secrets: SecretStore
) -> list[HealthCheck]:
    if not config.get("enabled", False):
        return []
    try:
        return collector(config, secrets=secrets)
    except Exception as exc:
        return [
            HealthCheck(
                f"{name}_collector_failed",
                name.title(),
                "unknown",
                f"{name.title()} collector failed without completing.",
                {"error": str(exc)},
                "Check collector configuration and local permissions.",
            )
        ]


def run_scan(config_path: str) -> int:
    config = load_config(config_path)
    # Fire the agent fail-to-ack fallback for anything overdue from a prior push
    # before this scan records new ones (also covers one-shot/external-cron runs).
    try:
        check_overdue_alerts(config_path)
    except Exception:
        traceback.print_exc()
    collector_config = config.get("collectors", {})
    secrets = build_store(config.get("secrets", {}))

    checks: list[HealthCheck] = []
    for name, collector in COLLECTORS.items():
        checks.extend(run_collector(name, collector, collector_config.get(name, {}), secrets))

    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")
    auto_repairs: list = []
    conn = db.connect(database_path)
    try:
        acks = db.load_active_acks(conn)
        for check in checks:
            ack = acks.get(check.id)
            if ack is not None:
                check.acknowledged = True
                check.ack_note = ack.get("note") or ""

        # Acknowledged checks are muted: change detection ignores them on
        # both sides so a flapping known issue cannot trigger notifications.
        acked_ids = {check.id for check in checks if check.acknowledged}
        active_checks = [check for check in checks if not check.acknowledged]

        previous = db.load_latest_scan(conn)
        if previous is not None:
            previous_snapshot = dict(previous[2])
            previous_snapshot["checks"] = [
                item
                for item in previous_snapshot.get("checks", [])
                if not (isinstance(item, dict) and item.get("id") in acked_ids)
            ]
            diff = diff_scans(
                previous_snapshot, active_checks, previous_scan_id=previous[0], previous_created_at=previous[1]
            )
        else:
            diff = diff_scans(None, active_checks)

        narrative = explain(config.get("ai", {}), checks, diff, secrets=secrets)

        snapshot = {
            "app": config.get("app", {}).get("name", "Homelab Guardian"),
            "checks": [check.to_dict() for check in checks],
            "narrative": narrative,
        }
        scan_id = db.save_scan(conn, snapshot)

        telegram_config = config.get("notifications", {}).get("telegram", {})
        events = update_alert_states(conn, checks, int(telegram_config.get("confirm_scans", 1)))

        # Deterministic reflex self-healing: for any newly-confirmed critical that
        # has an auto-approvable repair, Guardian fixes it now (loop-guarded,
        # audited). The results flow into the notification so the agent/user is
        # told what was auto-handled rather than just alarmed.
        from homelab_guardian import repair as _repair
        auto_repairs = _repair.auto_repair_confirmed(config, conn, events)
        for r in auto_repairs:
            print(f"Auto-repair: {r['action']} on {r['check_id']} -> {r['status']}"
                  f"{' (recovered)' if r['recovered'] else ''}.")

        retention_days = float(config.get("app", {}).get("retention_days", 60))
        if retention_days > 0:
            pruned = db.prune_scans(conn, retention_days)
            if pruned:
                print(f"Pruned {pruned} scan snapshot(s) older than {retention_days:g} days.")
    finally:
        conn.close()

    report_path = config.get("app", {}).get("report_path", "reports/latest.md")
    written = write_report(report_path, checks, scan_id=scan_id, diff=diff, narrative=narrative)
    print(f"Wrote report: {written}")
    print(f"Checks: {len(checks)}")

    _dispatch_notifications(config, checks, events, scan_id, secrets, auto_repairs=auto_repairs)
    return 0


def _dispatch_notifications(config, checks, events, scan_id, secrets, auto_repairs=None) -> None:
    """Route confirmed transitions to the right channel.

    direct mode (default): Guardian messages the user over Telegram itself —
    the standalone, no-AI behavior.
    agent mode: Guardian hands the event to the attached agent's webhook so the
    *agent* is the single voice. Telegram is dormant, used only as the
    critical-fallback: if a critical could not be delivered to the agent,
    Guardian sends it directly over the same (shared) Telegram bot, labeled so
    it doubles as an agent-down signal."""
    from homelab_guardian.notifications import agent_notifier

    notifications = config.get("notifications", {})
    telegram_config = notifications.get("telegram", {})
    mode = str(notifications.get("mode", "direct")).lower()

    if mode != "agent":
        telegram_notifier.notify(telegram_config, checks, events, scan_id, secrets=secrets)
        return

    agent_config = notifications.get("agent", {})
    delivered = agent_notifier.notify(agent_config, checks, events, scan_id, secrets=secrets, auto_repairs=auto_repairs)

    if delivered:
        # The agent ACCEPTED the push. Any confirmed criticals now await the
        # agent's callback (the acknowledge_alert_received MCP tool, called once
        # it has relayed them). If no callback arrives before the deadline, the
        # scan loop's overdue check fires the Telegram fallback — true
        # fails-to-ACK, not just fails-to-deliver.
        _record_pending_criticals(config, events, auto_repairs)
        return

    if not agent_notifier.has_critical(events):
        return
    if not agent_config.get("critical_fallback", True):
        return
    # Delivery itself failed (agent unreachable) — fall back immediately, no
    # point waiting for an ack. Same shared Telegram bot, forced, labeled.
    telegram_notifier.notify(
        telegram_config, checks, events, scan_id, secrets=secrets,
        force=True, prefix="⚠️ Guardian · agent unreachable",
    )


def _ack_timeout_minutes(config) -> float:
    agent = config.get("notifications", {}).get("agent", {})
    return float(agent.get("ack_timeout_minutes", 10))


def _record_pending_criticals(config, events, auto_repairs=None) -> None:
    """Track criticals the agent accepted but must still confirm it relayed.
    Criticals Guardian already auto-repaired to recovery are excluded — they are
    resolved, so there is nothing to fall back on."""
    from datetime import datetime, timedelta, timezone

    resolved = {r["check_id"] for r in (auto_repairs or []) if r.get("recovered")}
    crits = [e for e in events.confirmed
             if e.get("current_status") == "critical" and e.get("id") not in resolved]
    if not crits:
        return
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=_ack_timeout_minutes(config))).isoformat()
    conn = db.connect(config.get("app", {}).get("database_path", "data/guardian.sqlite"))
    try:
        for e in crits:
            db.record_pending_alert(conn, e["id"], e["current_status"], e.get("summary", ""), deadline)
    finally:
        conn.close()


def _overdue_message(overdue: list) -> str:
    import html

    lines = [
        "<b>⚠️ Guardian · agent did not acknowledge</b>",
        "These confirmed criticals were sent to your agent, but it never confirmed it relayed them:",
    ]
    for o in overdue[:10]:
        lines.append(f"🔴 <b>{html.escape(o['check_id'])}</b>: {html.escape(o.get('summary', ''))}")
    if len(overdue) > 10:
        lines.append(f"…and {len(overdue) - 10} more.")
    return "\n".join(lines)


def check_overdue_alerts(config_path: str) -> None:
    """Agent-mode safety net: if a critical pushed to the agent wasn't
    acknowledged before its deadline, send it to the user over Telegram so it is
    never silently lost. Cleared on a successful send; left pending to retry if
    Telegram itself is failing. No-op unless in agent mode with the fallback on."""
    config = load_config(config_path)
    notifications = config.get("notifications", {})
    if str(notifications.get("mode", "direct")).lower() != "agent":
        return
    agent_config = notifications.get("agent", {})
    if not agent_config.get("critical_fallback", True):
        return

    conn = db.connect(config.get("app", {}).get("database_path", "data/guardian.sqlite"))
    try:
        overdue = db.overdue_pending_alerts(conn)
        if not overdue:
            return
        secrets = build_store(config.get("secrets", {}))
        sent = telegram_notifier.send_text(notifications.get("telegram", {}), _overdue_message(overdue), secrets=secrets)
        if sent:
            db.clear_pending_alerts(conn, [o["check_id"] for o in overdue])
            print(f"Agent fallback: {len(overdue)} unacknowledged critical(s) sent to Telegram.")
    finally:
        conn.close()


def load_dotenv(path: str | Path = ".env") -> int:
    """Load KEY=VALUE lines from a local .env file into the environment
    without overriding variables that are already set. Keeps the wizard's
    'write tokens to .env' flow working for bare `guardian` runs, matching
    what docker compose env_file and systemd EnvironmentFile already do."""
    env_path = Path(path)
    if not env_path.is_file():
        return 0
    loaded = 0
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except OSError as exc:
        print(f"Could not read {env_path}: {exc}")
    return loaded


def run_ack(config_path: str, command: str, check_id: str | None, note: str, days: float, until: str) -> int:
    from datetime import datetime, timedelta, timezone

    config = load_config(config_path)
    conn = db.connect(config.get("app", {}).get("database_path", "data/guardian.sqlite"))
    try:
        if command == "unack":
            if not check_id:
                print("Usage: guardian unack <check-id>")
                return 1
            if db.remove_ack(conn, check_id):
                print(f"Unacknowledged: {check_id}. It will count toward overall status again.")
                return 0
            print(f"No acknowledgment found for: {check_id}")
            return 1

        if not check_id:
            acks = db.list_acks(conn)
            if not acks:
                print("No acknowledged checks. Mute one with: guardian ack <check-id> --note \"reason\"")
                return 0
            now = datetime.now(timezone.utc).isoformat()
            for ack in acks:
                expired = ack["expires_at"] is not None and ack["expires_at"] <= now
                expiry = f", expires {ack['expires_at']}" if ack["expires_at"] else ", no expiry"
                state = " [EXPIRED]" if expired else ""
                note_text = f" — {ack['note']}" if ack["note"] else ""
                print(f"  🔕 {ack['check_id']}{state} (since {ack['created_at']}{expiry}){note_text}")
            return 0

        expires_at: str | None = None
        if until:
            parsed = datetime.fromisoformat(until)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            expires_at = parsed.isoformat()
        elif days > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        db.set_ack(conn, check_id, note=note, expires_at=expires_at)
        expiry_text = f" until {expires_at}" if expires_at else " with no expiry"
        print(f"Acknowledged: {check_id}{expiry_text}.")
        print("It is now muted: excluded from overall status, change detection, and notifications.")
        print("Check ids appear in reports and the web view; undo with: guardian unack " + check_id)
        return 0
    finally:
        conn.close()


def _print_plan(plan: dict) -> None:
    print(f"  Action:       {plan['action']}  [risk: {plan.get('risk', 'moderate')}]")
    print(f"  Check:        {plan['check_id']}")
    print(f"  Will run:     {' '.join(plan['argv'])}")
    print(f"  Blast radius: {plan['blast_radius']}")
    print(f"  Reversible:   {plan['reversible']}")
    if plan.get("preview"):
        preview = ", ".join(f"{k}={v}" for k, v in plan["preview"].items())
        print(f"  Preview:      {preview}")
    if plan.get("warning"):
        print(f"  ⚠ Warning:    {plan['warning']}")
    if plan.get("needs_privilege"):
        print("  Privilege:    needs a scoped sudoers grant for that exact argv.")


def run_repair(config_path: str, sub: str | None, rest: list[str], by: str = "cli", confirm: str | None = None) -> int:
    from homelab_guardian import repair

    config = load_config(config_path)
    if not repair.is_enabled(config):
        print("Repairs are disabled. Set repair.enabled: true in config.yaml first (see docs/repair.md).")
        return 1
    conn = db.connect(config.get("app", {}).get("database_path", "data/guardian.sqlite"))
    try:
        sub = sub or "list"
        if sub == "list":
            if rest:  # actions applicable to a specific check
                check = repair._load_check(conn, rest[0])
                if check is None:
                    print(f"No check '{rest[0]}' in the latest scan.")
                    return 1
                actions = repair.applicable_actions(config, check)
                print(f"{check.id} ({check.status}): {check.summary}")
                print("  Repair actions:", ", ".join(actions) if actions else "(none available)")
                for a in actions:
                    print(f"    propose with: guardian repair propose {check.id} {a}")
            else:  # recent proposals
                rows = db.list_repair_proposals(conn)
                if not rows:
                    print("No repair proposals yet.")
                for p in rows:
                    print(f"  #{p['id']} [{p['status']}] {p['action']} on {p['check_id']} (proposed {p['proposed_at']})")
            return 0

        if sub == "propose":
            if len(rest) < 2:
                print("Usage: guardian repair propose <check_id> <action>")
                return 1
            res = repair.propose(config, conn, rest[0], rest[1], proposed_by=by)
            print(f"Proposal #{res['proposal_id']} [{res['status']}]:")
            _print_plan(res["plan"])
            if res["status"] == "approved":
                print(f"\nAuto-approved. Execute with: guardian repair execute {res['proposal_id']}")
            else:
                print(f"\nApprove with: guardian repair approve {res['proposal_id']}   (then: guardian repair execute {res['proposal_id']})")
            return 0

        if sub in {"approve", "deny", "execute"}:
            if not rest:
                print(f"Usage: guardian repair {sub} <proposal_id>")
                return 1
            pid = int(rest[0])
            if sub == "approve":
                repair.approve(conn, pid, approved_by=by)
                print(f"Proposal #{pid} approved. Execute with: guardian repair execute {pid}")
            elif sub == "deny":
                repair.deny(conn, pid, denied_by=by)
                print(f"Proposal #{pid} denied.")
            else:
                res = repair.execute(config, conn, pid, executed_by=by, confirmation=confirm)
                r, v = res["result"], res["verify"]
                ran = f"exit {r.get('exit_code')}" if r.get("ran") else f"did not run: {r.get('error')}"
                print(f"Proposal #{pid} {res['status']} — action {ran}.")
                print(f"  Verify: {v['status']} — {v['summary']}")
            return 0

        if sub == "log":
            for p in db.list_repair_proposals(conn, limit=50):
                line = f"  #{p['id']} [{p['status']}] {p['action']} on {p['check_id']}"
                if p.get("approved_by"):
                    line += f" · by {p['approved_by']}"
                print(line)
            return 0

        print("Usage: guardian repair {list|propose|approve|deny|execute|log} ...")
        return 1
    except repair.RepairError as exc:
        print(f"Refused: {exc}")
        return 1
    finally:
        conn.close()


def run_scan_loop(config_path: str, interval_seconds: int) -> int:
    """Run scans forever, every interval_seconds. A failed scan is reported
    and the loop continues — a transient collector error must not stop a
    long-running Guardian."""
    print(f"Running a scan every {interval_seconds} seconds. Press Ctrl+C to stop.")
    first = True
    while True:
        try:
            if first:
                # Gate ONLY the first scan on network readiness, so a reboot's
                # scan-before-DNS doesn't flip every network check to a false
                # warning. Later scans run ungated — a real outage must surface.
                from homelab_guardian.network_ready import wait_for_network_ready
                from homelab_guardian.config import load_config

                try:
                    wait_for_network_ready(load_config(config_path))
                except Exception:
                    pass  # readiness is best-effort; never block the scan loop
                first = False
            run_scan(config_path)
        except KeyboardInterrupt:
            raise
        except Exception:
            print("Scan failed; will retry on the next interval.")
            traceback.print_exc()
        # Sleep until the next scan, but wake periodically to fire the agent
        # fail-to-ack fallback for any critical the agent didn't confirm in time
        # (the ack deadline is usually minutes, far shorter than the scan interval).
        try:
            slept = 0.0
            while slept < interval_seconds:
                nap = min(OVERDUE_RECHECK_SECONDS, interval_seconds - slept)
                time.sleep(nap)
                slept += nap
                try:
                    check_overdue_alerts(config_path)
                except Exception:
                    traceback.print_exc()
        except KeyboardInterrupt:
            print("Stopped.")
            return 0


def main() -> int:
    # Windows consoles often default to a legacy codepage that cannot encode
    # the status emoji; degrade characters instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Generate a Homelab Guardian health report.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["scan", "doctor", "init", "serve", "ack", "unack", "mcp", "repair"],
        default="scan",
        help="Command to run",
    )
    parser.add_argument("check_id", nargs="?", help="ack/unack: the check id; repair: the subcommand")
    parser.add_argument("rest", nargs="*", help="repair: subcommand arguments")
    parser.add_argument("--by", default="cli", help="repair: identity recorded for approve/deny/execute")
    parser.add_argument("--confirm", default=None, help="repair: typed-confirmation token for destructive execute")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--note", default="", help="ack: why this check is muted")
    parser.add_argument("--days", type=float, default=0, help="ack: auto-expire after this many days")
    parser.add_argument("--until", default="", help="ack: auto-expire at this ISO date/time")
    parser.add_argument("--doctor", action="store_true", help="Run preflight checks instead of a normal scan")
    parser.add_argument("--force", action="store_true", help="init: overwrite an existing config file")
    parser.add_argument(
        "--no-discover", action="store_true", help="init: skip the local network service discovery step"
    )
    parser.add_argument("--host", default="127.0.0.1", help="serve / mcp --http: address to bind (default localhost only)")
    parser.add_argument("--port", type=int, default=0, help="serve / mcp --http: port (default 8674 for serve, 8675 for mcp --http)")
    parser.add_argument("--http", action="store_true", help="mcp: serve over streamable HTTP (bearer-token auth) instead of stdio")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Repeat the scan every N seconds instead of running once. With "
        "serve, runs scans in the background of the web view.",
    )
    args = parser.parse_args()

    if args.command == "init":
        from homelab_guardian.wizard import run_init

        return run_init(args.config, force=args.force, discover_network=False if args.no_discover else None)

    load_dotenv()
    if args.command in {"ack", "unack"}:
        return run_ack(args.config, args.command, args.check_id, args.note, args.days, args.until)
    if args.command == "repair":
        return run_repair(args.config, args.check_id, args.rest, by=args.by, confirm=args.confirm)
    if args.doctor or args.command == "doctor":
        return run_doctor(args.config)
    if args.command == "serve":
        from homelab_guardian.web import serve

        return serve(
            load_config(args.config),
            host=args.host,
            port=args.port or 8674,
            scan_interval=args.interval,
            scan_loop=(lambda: run_scan_loop(args.config, args.interval)) if args.interval > 0 else None,
            config_path=args.config,
        )
    if args.command == "mcp":
        if args.http:
            from homelab_guardian.mcp_server import run_http

            return run_http(args.config, host=args.host, port=args.port or 8675)
        from homelab_guardian.mcp_server import run_stdio

        return run_stdio(args.config)
    if args.interval > 0:
        return run_scan_loop(args.config, args.interval)
    return run_scan(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
