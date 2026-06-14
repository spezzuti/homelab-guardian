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
    collector_config = config.get("collectors", {})
    secrets = build_store(config.get("secrets", {}))

    checks: list[HealthCheck] = []
    for name, collector in COLLECTORS.items():
        checks.extend(run_collector(name, collector, collector_config.get(name, {}), secrets))

    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")
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

    telegram_notifier.notify(telegram_config, checks, events, scan_id, secrets=secrets)
    return 0


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


def run_scan_loop(config_path: str, interval_seconds: int) -> int:
    """Run scans forever, every interval_seconds. A failed scan is reported
    and the loop continues — a transient collector error must not stop a
    long-running Guardian."""
    print(f"Running a scan every {interval_seconds} seconds. Press Ctrl+C to stop.")
    while True:
        try:
            run_scan(config_path)
        except KeyboardInterrupt:
            raise
        except Exception:
            print("Scan failed; will retry on the next interval.")
            traceback.print_exc()
        try:
            time.sleep(interval_seconds)
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
        choices=["scan", "doctor", "init", "serve", "ack", "unack", "mcp"],
        default="scan",
        help="Command to run",
    )
    parser.add_argument("check_id", nargs="?", help="ack/unack: the check id to mute or unmute")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--note", default="", help="ack: why this check is muted")
    parser.add_argument("--days", type=float, default=0, help="ack: auto-expire after this many days")
    parser.add_argument("--until", default="", help="ack: auto-expire at this ISO date/time")
    parser.add_argument("--doctor", action="store_true", help="Run preflight checks instead of a normal scan")
    parser.add_argument("--force", action="store_true", help="init: overwrite an existing config file")
    parser.add_argument(
        "--no-discover", action="store_true", help="init: skip the local network service discovery step"
    )
    parser.add_argument("--host", default="127.0.0.1", help="serve: address to bind (default localhost only)")
    parser.add_argument("--port", type=int, default=8674, help="serve: port for the web view")
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
    if args.doctor or args.command == "doctor":
        return run_doctor(args.config)
    if args.command == "serve":
        from homelab_guardian.web import serve

        return serve(
            load_config(args.config),
            host=args.host,
            port=args.port,
            scan_interval=args.interval,
            scan_loop=(lambda: run_scan_loop(args.config, args.interval)) if args.interval > 0 else None,
        )
    if args.command == "mcp":
        from homelab_guardian.mcp_server import run_stdio

        return run_stdio(args.config)
    if args.interval > 0:
        return run_scan_loop(args.config, args.interval)
    return run_scan(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
