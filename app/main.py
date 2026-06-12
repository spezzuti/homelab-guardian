from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Any, Callable

from app import db
from app.collectors import backup_collector, docker_collector, homeassistant_collector, network_collector
from app.config import load_config
from app.diff import diff_scans
from app.doctor import run_doctor
from app.explain import explain
from app.models import HealthCheck
from app.notifications import telegram_notifier
from app.reports.markdown_report import write_report
from app.secrets import SecretStore, build_store

CollectorFn = Callable[..., list[HealthCheck]]

COLLECTORS: dict[str, CollectorFn] = {
    "docker": docker_collector.collect,
    "homeassistant": homeassistant_collector.collect,
    "network": network_collector.collect,
    "backups": backup_collector.collect,
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

    snapshot = {
        "app": config.get("app", {}).get("name", "Homelab Guardian"),
        "checks": [check.to_dict() for check in checks],
    }

    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")
    conn = db.connect(database_path)
    try:
        previous = db.load_latest_scan(conn)
        if previous is not None:
            diff = diff_scans(previous[2], checks, previous_scan_id=previous[0], previous_created_at=previous[1])
        else:
            diff = diff_scans(None, checks)
        scan_id = db.save_scan(conn, snapshot)
    finally:
        conn.close()

    narrative = explain(config.get("ai", {}), checks, diff, secrets=secrets)

    report_path = config.get("app", {}).get("report_path", "reports/latest.md")
    written = write_report(report_path, checks, scan_id=scan_id, diff=diff, narrative=narrative)
    print(f"Wrote report: {written}")
    print(f"Checks: {len(checks)}")

    telegram_notifier.notify(
        config.get("notifications", {}).get("telegram", {}), checks, diff, scan_id, secrets=secrets
    )
    return 0


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
    parser.add_argument("command", nargs="?", choices=["scan", "doctor"], default="scan", help="Command to run")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--doctor", action="store_true", help="Run preflight checks instead of a normal scan")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Repeat the scan every N seconds instead of running once. "
        "Useful for systemd services and long-running containers.",
    )
    args = parser.parse_args()

    if args.doctor or args.command == "doctor":
        return run_doctor(args.config)
    if args.interval > 0:
        return run_scan_loop(args.config, args.interval)
    return run_scan(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
