from __future__ import annotations

import argparse
from typing import Any, Callable

from app import db
from app.collectors import backup_collector, docker_collector, homeassistant_collector, network_collector
from app.config import load_config
from app.diff import diff_scans
from app.doctor import run_doctor
from app.models import HealthCheck
from app.notifications import telegram_notifier
from app.reports.markdown_report import write_report

CollectorFn = Callable[[dict[str, Any]], list[HealthCheck]]

COLLECTORS: dict[str, CollectorFn] = {
    "docker": docker_collector.collect,
    "homeassistant": homeassistant_collector.collect,
    "network": network_collector.collect,
    "backups": backup_collector.collect,
}


def run_collector(name: str, collector: CollectorFn, config: dict[str, Any]) -> list[HealthCheck]:
    if not config.get("enabled", False):
        return []
    try:
        return collector(config)
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

    checks: list[HealthCheck] = []
    for name, collector in COLLECTORS.items():
        checks.extend(run_collector(name, collector, collector_config.get(name, {})))

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

    report_path = config.get("app", {}).get("report_path", "reports/latest.md")
    written = write_report(report_path, checks, scan_id=scan_id, diff=diff)
    print(f"Wrote report: {written}")
    print(f"Checks: {len(checks)}")

    telegram_notifier.notify(
        config.get("notifications", {}).get("telegram", {}), checks, diff, scan_id
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Homelab Guardian health report.")
    parser.add_argument("command", nargs="?", choices=["scan", "doctor"], default="scan", help="Command to run")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    parser.add_argument("--doctor", action="store_true", help="Run preflight checks instead of a normal scan")
    args = parser.parse_args()

    if args.doctor or args.command == "doctor":
        return run_doctor(args.config)
    return run_scan(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
