from __future__ import annotations

import argparse
from typing import Any, Callable

from app import db
from app.collectors import backup_collector, docker_collector, homeassistant_collector, network_collector
from app.config import load_config
from app.models import HealthCheck
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
        return [HealthCheck(f"{name}_disabled", name.title(), "unknown", f"{name.title()} collector is disabled.", {}, f"Enable collectors.{name}.enabled if this integration should be checked.")]
    try:
        return collector(config)
    except Exception as exc:
        return [HealthCheck(f"{name}_collector_failed", name.title(), "unknown", f"{name.title()} collector failed without completing.", {"error": str(exc)}, "Check collector configuration and local permissions.")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Homelab Guardian health report.")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config file")
    args = parser.parse_args()

    config = load_config(args.config)
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
        scan_id = db.save_scan(conn, snapshot)
    finally:
        conn.close()

    report_path = config.get("app", {}).get("report_path", "reports/latest.md")
    written = write_report(report_path, checks, scan_id=scan_id)
    print(f"Wrote report: {written}")
    print(f"Checks: {len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
