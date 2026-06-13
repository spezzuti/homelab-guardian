from __future__ import annotations

import html
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from homelab_guardian import db
from homelab_guardian.diff import ScanDiff, diff_scans
from homelab_guardian.models import HealthCheck

# Read-only web view rendered from SQLite snapshots. Stdlib http.server only:
# no web framework dependency, no write endpoints, no JavaScript required.
# Binds to localhost by default; exposing it wider is an explicit choice.

STATUS_META = {
    "critical": ("🚨", "Critical"),
    "warning": ("⚠️", "Warning"),
    "unknown": ("❔", "Unknown"),
    "ok": ("✅", "OK"),
}
STATUS_ORDER = ["critical", "warning", "unknown", "ok"]

_DARK_VARS = """
    --bg: #14171c; --card: #1d222a; --text: #e8eaee; --muted: #9aa3b0;
    --critical: #ff5d64; --warning: #ffb454; --unknown: #8fa1bd; --ok: #4ecb71;
    --border: #2a313b;
"""

PAGE_STYLE = f"""
:root {{
  color-scheme: light dark;
  --bg: #f5f6f8; --card: #ffffff; --text: #1c2330; --muted: #69707d;
  --critical: #d4373e; --warning: #c77d00; --unknown: #6c7a93; --ok: #2c8a4b;
  --border: #e3e6ea;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{{_DARK_VARS}}}
}}
:root[data-theme="dark"] {{{_DARK_VARS}}}"""

PAGE_STYLE += """
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
a { color: inherit; }
header.overall {
  border-radius: 12px; padding: 20px 24px; margin-bottom: 20px;
  background: var(--card); border: 1px solid var(--border);
  border-left: 8px solid var(--accent, var(--unknown));
}
header.overall { position: relative; }
header.overall h1 { margin: 0 0 2px; font-size: 1.15rem; font-weight: 600; }
header.overall .status { font-size: 1.7rem; font-weight: 700; }
header.overall .meta { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }
.theme-toggle {
  position: absolute; top: 14px; right: 14px; cursor: pointer;
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; font-size: 1.05rem; padding: 4px 9px; line-height: 1;
}
.counts { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-bottom: 20px; }
.pill {
  border-radius: 999px; padding: 4px 14px; font-size: 0.9rem; font-weight: 600;
  background: var(--card); border: 1px solid var(--border);
}
.pill b { font-size: 1.05rem; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 16px 20px; margin-bottom: 16px;
}
.card h2 { margin: 0 0 10px; font-size: 1.02rem; }
.check {
  border-left: 5px solid var(--accent, var(--border));
  background: var(--bg); border-radius: 0 10px 10px 0;
  padding: 10px 14px; margin: 12px 0;
}
.check .name { font-weight: 650; }
.check .summary { margin: 2px 0; }
.check .action { color: var(--muted); font-size: 0.9rem; }
.check details { margin-top: 6px; }
.check summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; }
.check pre {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; overflow-x: auto; font-size: 0.8rem;
}
.crit { --accent: var(--critical); } .warn { --accent: var(--warning); }
.unk { --accent: var(--unknown); } .okc { --accent: var(--ok); }
.tilegrid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px; margin-top: 4px;
}
.tile {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px; min-width: 0;
}
.tile h3 {
  margin: 0 0 7px; font-size: 0.78rem; font-weight: 650;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;
}
.tile ul { list-style: none; margin: 0; padding: 0; }
.tile li {
  margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-size: 0.92rem;
}
details.morehistory summary { cursor: pointer; color: var(--muted); font-size: 0.88rem; padding: 6px 0; }
ul.changes { margin: 0; padding-left: 22px; }
ul.changes li { margin: 5px 0; }
.briefing p { margin: 8px 0; }
.card.acked summary { cursor: pointer; list-style: revert; }
.card.acked summary h2 { display: inline; }
.ackhint { color: var(--muted); font-size: 0.88rem; }
.acknote { color: var(--muted); font-style: italic; }
table.history { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
table.history th, table.history td {
  text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border);
}
table.history th { color: var(--muted); font-weight: 600; }
table.history tr.current td { font-weight: 650; }
footer { color: var(--muted); font-size: 0.8rem; margin-top: 28px; text-align: center; }
details.group {
  background: var(--card); border: 1px solid var(--border);
  border-left: 8px solid var(--accent, var(--border)); border-radius: 12px;
  margin-bottom: 14px;
}
details.group > summary {
  cursor: pointer; list-style: none; padding: 14px 20px;
  display: flex; align-items: center; gap: 10px; font-weight: 650; font-size: 1.02rem;
}
details.group > summary::-webkit-details-marker { display: none; }
details.group > summary::after {
  content: "▸"; color: var(--muted); font-weight: 400; margin-left: 4px;
}
details.group[open] > summary::after { content: "▾"; }
details.group > summary .gcount {
  color: var(--muted); font-weight: 500; font-size: 0.85rem; margin-left: auto;
}
details.group[open] > summary { border-bottom: 1px solid var(--border); }
.group .gbody { padding: 4px 20px 14px; }
.group ul.oklist { list-style: none; margin: 10px 0 0; padding: 0; columns: 2; column-gap: 18px; }
.group ul.oklist li {
  margin: 5px 0; font-size: 0.93rem; break-inside: avoid;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
@media (max-width: 520px) { .group ul.oklist { columns: 1; } }
"""

_STATUS_CLASS = {"critical": "crit", "warning": "warn", "unknown": "unk", "ok": "okc"}

_CATEGORY_PREFIXES = [
    ("http_", "Web services"),
    ("tcp_", "TCP services"),
    ("tls_", "Certificates"),
    ("dns_", "DNS"),
    ("ha_", "Home Assistant"),
    ("backup", "Backups"),
    ("docker", "Docker"),
    ("systemd_", "Services"),
    ("disk_", "Disks"),
    ("preflight_", "Preflight"),
    ("network_", "Network"),
]

THEME_SCRIPT = """
<script>
(function () {
  var saved = localStorage.getItem("guardian-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  window.toggleTheme = function () {
    var current = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    var next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("guardian-theme", next);
  };
})();
</script>"""


def _category(check_id: str) -> str:
    for prefix, label in _CATEGORY_PREFIXES:
        if check_id.startswith(prefix):
            return label
    return "Other"


def checks_from_snapshot(snapshot: dict[str, Any]) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    for item in snapshot.get("checks", []):
        if not isinstance(item, dict):
            continue
        checks.append(
            HealthCheck(
                id=str(item.get("id", "")),
                name=str(item.get("name", item.get("id", "unnamed"))),
                status=item.get("status", "unknown"),
                summary=str(item.get("summary", "")),
                evidence=item.get("evidence") or {},
                recommended_action=str(item.get("recommended_action", "")),
                group=str(item.get("group", "")),
                acknowledged=bool(item.get("acknowledged", False)),
                ack_note=str(item.get("ack_note", "")),
            )
        )
    return checks


def overall_of(checks: list[HealthCheck]) -> str:
    statuses = {check.status for check in checks if not check.acknowledged}
    for status in STATUS_ORDER[:-1]:
        if status in statuses:
            return status
    return "ok" if checks else "unknown"


def _fmt_time(created_at: str) -> str:
    try:
        return datetime.fromisoformat(created_at).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return html.escape(created_at)


def _counts(checks: list[HealthCheck]) -> dict[str, int]:
    return {status: sum(1 for c in checks if c.status == status and not c.acknowledged) for status in STATUS_ORDER}


def _render_acknowledged(acked: list[HealthCheck]) -> str:
    items: list[str] = []
    for check in sorted(acked, key=lambda c: c.name.lower()):
        icon, _ = STATUS_META.get(check.status, ("•", check.status))
        note = f' <span class="acknote">— {html.escape(check.ack_note)}</span>' if check.ack_note else ""
        items.append(
            f"<li>🔕 {icon} <b>{html.escape(check.name)}</b> (currently {check.status}): "
            f"{html.escape(check.summary)}{note}</li>"
        )
    return (
        f'<div class="card acked"><details><summary><h2>Acknowledged — muted known issues ({len(acked)})</h2></summary>'
        f'<p class="ackhint">Excluded from overall status, change detection, and notifications. '
        f"Unmute with <code>guardian unack &lt;check-id&gt;</code>.</p>"
        f'<ul class="changes">{"".join(items)}</ul></details></div>'
    )


def _render_check(check: HealthCheck) -> str:
    evidence = html.escape(json.dumps(check.evidence, indent=2, sort_keys=True, default=str))
    icon, _ = STATUS_META.get(check.status, ("•", check.status))
    return (
        f'<div class="check {_STATUS_CLASS.get(check.status, "unk")}">'
        f'<div class="name">{icon} {html.escape(check.name)}</div>'
        f'<div class="summary">{html.escape(check.summary)}</div>'
        f'<div class="action">Safest next step: {html.escape(check.recommended_action)}</div>'
        f"<details><summary>Evidence</summary><pre>{evidence}</pre></details>"
        f"</div>"
    )


def _render_changes(diff: ScanDiff) -> str:
    if not diff.has_previous:
        return '<div class="card"><h2>What changed</h2><p>First recorded scan — nothing to compare against yet.</p></div>'
    if not diff.has_changes:
        return (
            f'<div class="card"><h2>What changed</h2>'
            f"<p>No changes since scan #{diff.previous_scan_id}. "
            f"All {diff.unchanged_count} checks have the same status as before.</p></div>"
        )
    items: list[str] = []
    for change in diff.regressions:
        items.append(
            f"<li>📉 <b>{html.escape(change['name'])}</b>: {change['previous_status']} → "
            f"<b>{change['current_status']}</b> — {html.escape(change['summary'])}</li>"
        )
    for change in diff.improvements:
        items.append(
            f"<li>📈 <b>{html.escape(change['name'])}</b>: {change['previous_status']} → "
            f"<b>{change['current_status']}</b></li>"
        )
    for check in diff.new_checks:
        icon, _ = STATUS_META.get(check["status"], ("•", ""))
        items.append(f"<li>🆕 {icon} <b>{html.escape(check['name'])}</b>: new check, currently {check['status']}</li>")
    for check in diff.removed_checks:
        items.append(f"<li>➖ <b>{html.escape(check['name'])}</b>: no longer checked</li>")
    unchanged = (
        f"<p>{diff.unchanged_count} other checks unchanged (vs scan #{diff.previous_scan_id}).</p>"
        if diff.unchanged_count
        else ""
    )
    return f'<div class="card"><h2>What changed</h2><ul class="changes">{"".join(items)}</ul>{unchanged}</div>'


def _render_briefing(narrative: str) -> str:
    paragraphs = "".join(f"<p>{html.escape(p.strip())}</p>" for p in narrative.split("\n\n") if p.strip())
    return f'<div class="card briefing"><h2>Briefing</h2>{paragraphs}</div>'


def effective_group(check: HealthCheck) -> str:
    """The heading a check rolls up under: its explicit group, or a sensible
    one derived from its id so older snapshots still group cleanly."""
    return check.group or _category(check.id)


_SEVERITY = {status: index for index, status in enumerate(STATUS_ORDER)}


def _worst(checks: list[HealthCheck]) -> str:
    statuses = {c.status for c in checks}
    for status in STATUS_ORDER[:-1]:
        if status in statuses:
            return status
    return "ok"


def _render_groups(checks: list[HealthCheck]) -> str:
    """Group-primary view: one collapsible card per group, showing the
    worst-of-children status. Groups with a problem sort first and open by
    default; all-healthy groups stay collapsed and quiet."""
    acked = [c for c in checks if c.acknowledged]
    active = [c for c in checks if not c.acknowledged]

    grouped: dict[str, list[HealthCheck]] = {}
    for check in active:
        grouped.setdefault(effective_group(check), []).append(check)

    problem_groups = [(n, m) for n, m in grouped.items() if _worst(m) != "ok"]
    healthy_groups = [(n, m) for n, m in grouped.items() if _worst(m) == "ok"]

    sections: list[str] = []

    # Groups with something wrong: full-width roll-up cards, worst first, open.
    problem_groups.sort(key=lambda kv: (_SEVERITY[_worst(kv[1])], kv[0].lower()))
    for name, members in problem_groups:
        worst = _worst(members)
        counts: dict[str, int] = {}
        for check in members:
            counts[check.status] = counts.get(check.status, 0) + 1
        summary_counts = " · ".join(
            f"{counts[s]} {STATUS_META[s][1].lower()}" for s in STATUS_ORDER if counts.get(s)
        )
        icon, _ = STATUS_META.get(worst, ("•", worst))
        problems = sorted(
            (c for c in members if c.status != "ok"),
            key=lambda c: (_SEVERITY[c.status], c.name.lower()),
        )
        healthy = sorted((c for c in members if c.status == "ok"), key=lambda c: c.name.lower())
        body = "".join(_render_check(check) for check in problems)
        if healthy:
            items = "".join(
                f'<li title="{html.escape(c.summary)}">✅ {html.escape(c.name)}</li>' for c in healthy
            )
            body += f'<ul class="oklist">{items}</ul>'
        sections.append(
            f'<details class="group {_STATUS_CLASS.get(worst, "unk")}" open>'
            f'<summary>{icon} {html.escape(name)}'
            f'<span class="gcount">{summary_counts}</span></summary>'
            f'<div class="gbody">{body}</div></details>'
        )

    # Fully-healthy groups: compact multi-column tile grid (calm + dense).
    if healthy_groups:
        total_ok = sum(len(m) for _, m in healthy_groups)
        healthy_groups.sort(key=lambda kv: kv[0].lower())
        tiles = []
        for name, members in healthy_groups:
            items = "".join(
                f'<li title="{html.escape(c.summary)}">✅ {html.escape(c.name)}</li>'
                for c in sorted(members, key=lambda c: c.name.lower())
            )
            tiles.append(f'<div class="tile"><h3>{html.escape(name)} ({len(members)})</h3><ul>{items}</ul></div>')
        sections.append(
            f'<div class="card"><h2>Healthy ({total_ok})</h2>'
            f'<div class="tilegrid">{"".join(tiles)}</div></div>'
        )

    if acked:
        sections.append(_render_acknowledged(acked))
    return "".join(sections)


HISTORY_VISIBLE_ROWS = 5


def _history_row(scan: tuple[int, str, dict[str, Any]], current_id: int | None) -> str:
    scan_id, created_at, snapshot = scan
    checks = checks_from_snapshot(snapshot)
    overall = overall_of(checks)
    icon, label = STATUS_META.get(overall, ("•", overall))
    counts = _counts(checks)
    current = ' class="current"' if scan_id == current_id else ""
    return (
        f"<tr{current}><td><a href=\"/scan/{scan_id}\">#{scan_id}</a></td>"
        f"<td>{_fmt_time(created_at)}</td><td>{icon} {label}</td>"
        f"<td>{counts['critical']} / {counts['warning']} / {counts['unknown']} / {counts['ok']}</td></tr>"
    )


def _render_history(history: list[tuple[int, str, dict[str, Any]]], current_id: int | None) -> str:
    if not history:
        return ""
    header = "<tr><th>Scan</th><th>Time</th><th>Status</th><th>crit / warn / unk / ok</th></tr>"
    recent = "".join(_history_row(scan, current_id) for scan in history[:HISTORY_VISIBLE_ROWS])
    body = f'<table class="history">{header}{recent}</table>'
    older = history[HISTORY_VISIBLE_ROWS:]
    if older:
        older_rows = "".join(_history_row(scan, current_id) for scan in older)
        body += (
            f'<details class="morehistory"><summary>Show {len(older)} older scans</summary>'
            f'<table class="history">{older_rows}</table></details>'
        )
    return f'<div class="card"><h2>Scan history</h2>{body}</div>'


def render_scan_page(
    scan: tuple[int, str, dict[str, Any]],
    diff: ScanDiff,
    history: list[tuple[int, str, dict[str, Any]]],
    refresh_seconds: int = 60,
) -> str:
    scan_id, created_at, snapshot = scan
    checks = checks_from_snapshot(snapshot)
    overall = overall_of(checks)
    icon, label = STATUS_META.get(overall, ("•", overall))
    counts = _counts(checks)
    narrative = snapshot.get("narrative") or ""
    app_name = html.escape(str(snapshot.get("app", "Homelab Guardian")))

    pills = "".join(
        f'<span class="pill {_STATUS_CLASS[s]}">{STATUS_META[s][0]} <b>{counts[s]}</b> {STATUS_META[s][1].lower()}</span>'
        for s in STATUS_ORDER
    )
    acked_count = sum(1 for c in checks if c.acknowledged)
    if acked_count:
        pills += f'<span class="pill">🔕 <b>{acked_count}</b> acknowledged</span>'
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>{app_name} — {label}</title><style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<header class="overall {_STATUS_CLASS.get(overall, 'unk')}">
<button class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
<h1>{app_name}</h1>
<div class="status">{icon} {label.upper()}</div>
<div class="meta">Scan #{scan_id} · {_fmt_time(created_at)}{' · auto-refreshes every %ds' % refresh_seconds if refresh_seconds else ''}</div>
</header>
<div class="counts">{pills}</div>
{_render_briefing(narrative) if narrative else ''}
{_render_changes(diff)}
{_render_groups(checks)}
{_render_history(history, scan_id)}
<footer>Homelab Guardian — read-only view · <a href="/">latest</a></footer>
</main></body></html>"""


def render_empty_page() -> str:
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Homelab Guardian</title>"
        f"<style>{PAGE_STYLE}</style></head><body><main><div class=\"card\"><h2>No scans yet</h2>"
        f"<p>Run <code>guardian --config config.yaml</code> to produce the first scan, "
        f"or start the server with <code>--interval</code> to scan continuously.</p></div></main></body></html>"
    )


class GuardianRequestHandler(BaseHTTPRequestHandler):
    database_path: str = "data/guardian.sqlite"
    refresh_seconds: int = 60
    history_limit: int = 30

    # quiet default request logging; the scan loop output matters more
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send(self, body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send("ok", content_type="text/plain; charset=utf-8")
            return
        if path == "/":
            self._render_scan(None)
            return
        if path.startswith("/scan/"):
            try:
                self._render_scan(int(path.removeprefix("/scan/")))
            except ValueError:
                self._send("not found", status=404, content_type="text/plain; charset=utf-8")
            return
        self._send("not found", status=404, content_type="text/plain; charset=utf-8")

    def _render_scan(self, scan_id: int | None) -> None:
        conn = db.connect(self.database_path)
        try:
            scan = db.load_latest_scan(conn) if scan_id is None else db.load_scan(conn, scan_id)
            if scan is None:
                if scan_id is None:
                    self._send(render_empty_page())
                else:
                    self._send("scan not found", status=404, content_type="text/plain; charset=utf-8")
                return
            previous = db.load_scan_before(conn, scan[0])
            checks = checks_from_snapshot(scan[2])
            if previous is not None:
                diff = diff_scans(previous[2], checks, previous_scan_id=previous[0], previous_created_at=previous[1])
            else:
                diff = diff_scans(None, checks)
            history = db.list_scans(conn, limit=self.history_limit)
        finally:
            conn.close()
        # only the live view auto-refreshes; historical scans are static
        refresh = self.refresh_seconds if scan_id is None else 0
        self._send(render_scan_page(scan, diff, history, refresh_seconds=refresh))


def serve(
    config: dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 8674,
    scan_interval: int = 0,
    scan_loop: Any = None,
) -> int:
    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")

    handler = type(
        "BoundGuardianHandler",
        (GuardianRequestHandler,),
        {"database_path": database_path},
    )

    if scan_interval > 0 and scan_loop is not None:
        worker = threading.Thread(target=scan_loop, daemon=True, name="guardian-scan-loop")
        worker.start()
        print(f"Background scans every {scan_interval} seconds.")

    server = ThreadingHTTPServer((host, port), handler)
    shown_host = "localhost" if host in {"127.0.0.1", "::1"} else host
    print(f"Guardian web view on http://{shown_host}:{port} (read-only). Press Ctrl+C to stop.")
    if host == "0.0.0.0":
        print("Listening on all interfaces — anyone on your network can view reports.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
    return 0
