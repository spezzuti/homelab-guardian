from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets as secrets_mod
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from homelab_guardian import db
from homelab_guardian.config import load_config
from homelab_guardian.configedit import apply_collector_toggles, toggleable_collectors, write_config
from homelab_guardian.diff import ScanDiff, diff_scans
from homelab_guardian.models import HealthCheck
from homelab_guardian.webauth import Authenticator, NoAuth, build_authenticator

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
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px; margin: 4px 0 16px; align-items: start;
}
details.tile {
  background: var(--card); border: 1px solid var(--border);
  border-left: 5px solid var(--accent, var(--ok));
  border-radius: 10px; min-width: 0;
}
details.tile > summary {
  cursor: pointer; list-style: none; padding: 9px 13px;
  font-weight: 650; font-size: 0.94rem; display: flex; align-items: center; gap: 7px;
}
details.tile > summary::-webkit-details-marker { display: none; }
details.tile > summary .tcount { color: var(--muted); font-weight: 500; font-size: 0.85rem; }
details.tile > summary::after { content: "▸"; margin-left: auto; color: var(--muted); font-weight: 400; }
details.tile[open] > summary::after { content: "▾"; }
details.tile[open] > summary { border-bottom: 1px solid var(--border); }
details.tile ul { list-style: none; margin: 0; padding: 8px 13px 11px; }
details.tile li {
  margin: 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-size: 0.9rem;
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
h2.sectionhead { margin: 22px 2px 10px; font-size: 1.02rem; }
a.settings-link {
  position: absolute; top: 14px; right: 56px; text-decoration: none;
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; font-size: 1.05rem; padding: 4px 9px; line-height: 1;
}
.settings-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 11px 4px; border-bottom: 1px solid var(--border);
}
.settings-row:last-child { border-bottom: none; }
.settings-row .label { font-weight: 600; }
.settings-row .cid { color: var(--muted); font-size: 0.82rem; }
.settings-row .cdesc { display: block; color: var(--muted); font-size: 0.82rem; margin-top: 3px; }
input.toggle { width: 18px; height: 18px; }
.savebar { margin-top: 18px; display: flex; gap: 14px; align-items: center; }
button.save {
  background: var(--ok); color: #fff; border: none; border-radius: 8px;
  padding: 8px 18px; font-size: 0.95rem; font-weight: 650; cursor: pointer;
}
a.repairs-link { right: 98px; }
.rbadge {
  background: var(--critical); color: #fff; border-radius: 999px;
  font-size: 0.7rem; font-weight: 700; padding: 1px 6px; margin-left: 3px; vertical-align: top;
}
.rcard {
  border: 1px solid var(--border); border-left: 5px solid var(--accent, var(--border));
  border-radius: 10px; padding: 12px 14px; margin-top: 12px;
}
.rcard .rhead { font-size: 1.02rem; }
.rcard .cid { color: var(--muted); font-size: 0.82rem; margin-top: 2px; }
.rcard .rplan { margin-top: 6px; }
.rcard .notice { margin-top: 6px; }
.rstatus {
  background: var(--accent, var(--unknown)); color: #fff; border-radius: 999px;
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase; padding: 2px 9px; margin-left: 6px;
}
.rform { margin-top: 10px; display: flex; gap: 10px; }
button.deny {
  background: var(--bg); color: var(--critical); border: 1px solid var(--critical);
  border-radius: 8px; padding: 8px 18px; font-size: 0.95rem; font-weight: 650; cursor: pointer;
}
.banner { border-radius: 10px; padding: 10px 14px; margin-bottom: 14px;
  background: var(--card); border: 1px solid var(--border); }
.banner.ok { border-left: 5px solid var(--ok); }
.banner.err { border-left: 5px solid var(--critical); }
.notice { color: var(--muted); }
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

# Remember each health tile's open/closed state across the page's auto-refresh,
# so collapsing a tile sticks instead of springing back open every cycle.
TILE_SCRIPT = """
<script>
(function () {
  function key(d) { return "guardian-tile:" + (d.getAttribute("data-tile") || ""); }
  function init() {
    document.querySelectorAll("details.tile").forEach(function (d) {
      var saved = localStorage.getItem(key(d));
      if (saved === "open") d.open = true;
      else if (saved === "closed") d.open = false;
      d.addEventListener("toggle", function () {
        localStorage.setItem(key(d), d.open ? "open" : "closed");
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
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

    # Fully-healthy groups: collapsible tiles in a 2-column grid. Each tile
    # rolls its checks up under a green header and opens by default so the
    # checks stay visible, but can be collapsed to tidy the view.
    if healthy_groups:
        total_ok = sum(len(m) for _, m in healthy_groups)
        # Largest tiles first so paired rows are closer in height; collapsed by
        # default (even + calm), with per-tile open state remembered client-side
        # so an auto-refresh never re-expands what you collapsed.
        healthy_groups.sort(key=lambda kv: (-len(kv[1]), kv[0].lower()))
        tiles = []
        for name, members in healthy_groups:
            items = "".join(
                f'<li title="{html.escape(c.summary)}">✅ {html.escape(c.name)}</li>'
                for c in sorted(members, key=lambda c: c.name.lower())
            )
            tiles.append(
                f'<details class="tile okc" data-tile="{html.escape(name)}"><summary>✅ {html.escape(name)}'
                f'<span class="tcount">({len(members)})</span></summary>'
                f'<ul>{items}</ul></details>'
            )
        sections.append(
            f'<h2 class="sectionhead">Healthy ({total_ok})</h2>'
            f'<div class="tilegrid">{"".join(tiles)}</div>'
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


def _repairs_link(repairs_pending: int | None) -> str:
    """The 🔧 header link, shown only when repairs are enabled. A badge shows the
    count of proposals awaiting approval."""
    if repairs_pending is None:
        return ""
    badge = f'<span class="rbadge">{repairs_pending}</span>' if repairs_pending else ""
    return f'<a class="settings-link repairs-link" href="/repairs" title="Repairs">🔧{badge}</a>'


def render_scan_page(
    scan: tuple[int, str, dict[str, Any]],
    diff: ScanDiff,
    history: list[tuple[int, str, dict[str, Any]]],
    refresh_seconds: int = 60,
    repairs_pending: int | None = None,
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
<a class="settings-link" href="/settings" title="Settings">⚙</a>
{_repairs_link(repairs_pending)}
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
</main>{TILE_SCRIPT}</body></html>"""


def render_empty_page() -> str:
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Homelab Guardian</title>"
        f"<style>{PAGE_STYLE}</style></head><body><main><div class=\"card\"><h2>No scans yet</h2>"
        f"<p>Run <code>guardian --config config.yaml</code> to produce the first scan, "
        f"or start the server with <code>--interval</code> to scan continuously.</p></div></main></body></html>"
    )


def render_settings_page(
    collectors: list[dict[str, Any]],
    *,
    editable: bool,
    csrf_token: str,
    identity: Any = None,
    saved: bool = False,
    error: str | None = None,
    auth_mode: str = "none",
) -> str:
    rows = []
    for c in collectors:
        checked = " checked" if c["enabled"] else ""
        disabled = "" if editable else " disabled"
        desc = c.get("description") or ""
        desc_html = f'<span class="cdesc">{html.escape(desc)}</span>' if desc else ""
        rows.append(
            f'<label class="settings-row"><span>'
            f'<span class="label">{html.escape(c["label"])}</span> '
            f'<span class="cid">{html.escape(c["name"])}</span>'
            f"{desc_html}</span>"
            f'<input class="toggle" type="checkbox" name="collector:{html.escape(c["name"])}"{checked}{disabled}>'
            f"</label>"
        )
    rows_html = "".join(rows)

    if saved:
        banner = '<div class="banner ok">Saved. Changes take effect on the next scan.</div>'
    elif error:
        banner = f'<div class="banner err">Could not save: {html.escape(error)}</div>'
    else:
        banner = ""

    if editable:
        who = f"Signed in as <b>{html.escape(identity.user)}</b>." if identity else ""
        logout = ' · <a href="/auth/logout">Sign out</a>' if auth_mode == "oidc" else ""
        body = (
            f'<form method="post" action="/settings">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf_token)}">'
            f'<div class="card"><h2>Collectors</h2>'
            f'<p class="notice">Turn checks on or off. Saved to config.yaml; the running scan picks up changes automatically.</p>'
            f"{rows_html}"
            f'<div class="savebar"><button class="save" type="submit">Save</button>'
            f'<span class="notice">{who}{logout}</span></div></div></form>'
        )
    else:
        body = (
            '<div class="banner err">Authentication is off — settings are read-only here.</div>'
            '<div class="card"><h2>Collectors</h2>'
            '<p class="notice">Editing the host config over the network needs a login. '
            "Set <code>web.auth.mode</code> in config.yaml (basic / forward_auth / oidc), then reload. "
            "Current configuration:</p>"
            f"{rows_html}</div>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Homelab Guardian — Settings</title><style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<header class="overall okc">
<button class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
<h1>Settings</h1>
<div class="meta"><a href="/">← Back to dashboard</a></div>
</header>
{banner}
{body}
<footer>Homelab Guardian — settings</footer>
</main></body></html>"""


_REPAIR_STATUS_CLASS = {
    "proposed": "warn", "approved": "okc", "denied": "unk", "executed": "okc", "failed": "crit",
}


def _render_proposal(p: dict[str, Any], *, editable: bool, csrf_token: str) -> str:
    plan = p.get("plan_json") if isinstance(p.get("plan_json"), dict) else {}
    argv = " ".join(p.get("argv") or plan.get("argv") or [])
    status = str(p.get("status", "proposed"))
    status_cls = _REPAIR_STATUS_CLASS.get(status, "unk")
    badge = f'<span class="rstatus">{html.escape(status)}</span>'
    parts = [
        f'<div class="rhead"><b>#{p.get("id")}</b> {html.escape(str(p.get("action", "")))} {badge}</div>',
        f'<div class="cid">on {html.escape(str(p.get("check_id", "")))}</div>',
    ]
    if argv:
        parts.append(f'<div class="rplan">Will run: <code>{html.escape(argv)}</code></div>')
    if plan.get("blast_radius"):
        parts.append(f'<div class="notice">{html.escape(str(plan["blast_radius"]))}</div>')
    meta = f'proposed by {html.escape(str(p.get("proposed_by") or "—"))} · {html.escape(str(p.get("proposed_at") or ""))}'
    if p.get("approved_by"):
        verb = "denied" if status == "denied" else "approved"
        meta += f' · {verb} by {html.escape(str(p["approved_by"]))}'
    parts.append(f'<div class="cid">{meta}</div>')
    verify = p.get("verify_json") if isinstance(p.get("verify_json"), dict) else None
    if verify and p.get("executed_at"):
        parts.append(f'<div class="notice">Verify: <b>{html.escape(str(verify.get("status")))}</b> — {html.escape(str(verify.get("summary", "")))}</div>')

    if status == "proposed":
        if editable:
            parts.append(
                '<form method="post" action="/repairs" class="rform">'
                f'<input type="hidden" name="csrf" value="{html.escape(csrf_token)}">'
                f'<input type="hidden" name="proposal_id" value="{p.get("id")}">'
                '<button class="save" name="decision" value="approve" type="submit">Approve</button>'
                '<button class="deny" name="decision" value="deny" type="submit">Deny</button>'
                "</form>"
            )
        else:
            parts.append('<div class="notice">Enable <code>web.auth</code> to approve or deny here.</div>')
    elif status == "approved":
        parts.append(f'<div class="notice">Approved — run <code>guardian repair execute {p.get("id")}</code> (or the agent will).</div>')
    return f'<div class="rcard {status_cls}">' + "".join(parts) + "</div>"


def render_repairs_page(
    proposals: list[dict[str, Any]],
    *,
    editable: bool,
    csrf_token: str,
    repair_enabled: bool = True,
    notice: str | None = None,
    error: str | None = None,
) -> str:
    if not repair_enabled:
        body = ('<div class="card"><h2>Repairs</h2><p class="notice">Repairs are disabled. '
                "Set <code>repair.enabled: true</code> in config.yaml to use approval-gated repair "
                "playbooks (see docs/repair.md).</p></div>")
        banner = ""
    else:
        cards = "".join(_render_proposal(p, editable=editable, csrf_token=csrf_token) for p in proposals)
        notice_html = "" if editable else '<div class="banner err">Authentication is off — approvals are read-only here.</div>'
        empty = "" if proposals else '<p class="notice">No repair proposals yet. An agent or <code>guardian repair propose</code> creates them; approve here or with <code>guardian repair approve &lt;id&gt;</code>.</p>'
        body = f'{notice_html}<div class="card"><h2>Repair proposals</h2>{empty}{cards}</div>'
        if notice:
            banner = f'<div class="banner ok">{html.escape(notice)}</div>'
        elif error:
            banner = f'<div class="banner err">{html.escape(error)}</div>'
        else:
            banner = ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Homelab Guardian — Repairs</title><style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<header class="overall okc">
<button class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
<h1>Repairs</h1>
<div class="meta"><a href="/">← Back to dashboard</a></div>
</header>
{banner}
{body}
<footer>Homelab Guardian — repairs · approval-gated, never raw shell</footer>
</main></body></html>"""


class GuardianRequestHandler(BaseHTTPRequestHandler):
    database_path: str = "data/guardian.sqlite"
    config_path: str = "config.yaml"
    refresh_seconds: int = 60
    history_limit: int = 30
    auth: Authenticator = NoAuth()
    auth_mode: str = "none"
    csrf_secret: bytes = b"guardian-dev-csrf-secret"

    # quiet default request logging; the scan loop output matters more
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send(
        self,
        body: str,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str, extra_headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Health check stays open so uptime monitors don't need credentials.
        if path == "/healthz":
            self._send("ok", content_type="text/plain; charset=utf-8")
            return
        # Auth-owned routes (e.g. OIDC login/callback) run before the gate.
        if self.auth.owns(path):
            self.auth.handle(self, path)
            return
        if self.auth.identify(self) is None:
            self.auth.challenge(self)
            return
        if path == "/":
            self._render_scan(None)
            return
        if path == "/settings":
            self._render_settings()
            return
        if path == "/repairs":
            self._render_repairs()
            return
        if path.startswith("/scan/"):
            try:
                self._render_scan(int(path.removeprefix("/scan/")))
            except ValueError:
                self._send("not found", status=404, content_type="text/plain; charset=utf-8")
            return
        self._send("not found", status=404, content_type="text/plain; charset=utf-8")

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return ""
        try:
            return self.rfile.read(length).decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Drain the request body up front, before any branching. If we send an
        # early error (401/403/404) while the client's POST body still sits
        # unread in the socket, closing the connection makes the OS reset it
        # (RST) — on Windows the client then sees WinError 10053 instead of our
        # status. Read once here; downstream handlers reuse it.
        self._post_body = self._read_body()
        if self.auth.identify(self) is None:
            self.auth.challenge(self)
            return
        if path == "/settings":
            self._save_settings()
            return
        if path == "/repairs":
            self._save_repair_decision()
            return
        self._send("not found", status=404, content_type="text/plain; charset=utf-8")

    # --- settings / guided config edits --------------------------------

    def _editable(self) -> bool:
        # Editing the host config over the network requires auth to be enabled.
        return not isinstance(self.auth, NoAuth)

    def _csrf_token(self, user: str) -> str:
        return hmac.new(self.csrf_secret, user.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def _check_csrf(self, user: str, token: str) -> bool:
        return bool(token) and hmac.compare_digest(self._csrf_token(user), token)

    def _render_settings(self) -> None:
        config = load_config(self.config_path)
        identity = self.auth.identify(self)
        query = parse_qs(urlparse(self.path).query)
        csrf = self._csrf_token(identity.user) if identity else ""
        self._send(render_settings_page(
            toggleable_collectors(config),
            editable=self._editable(),
            csrf_token=csrf,
            identity=identity,
            saved=query.get("saved") == ["1"],
            error=(query.get("error") or [None])[0],
            auth_mode=self.auth_mode,
        ))

    def _save_settings(self) -> None:
        if not self._editable():
            self._send(
                "Editing requires authentication — enable web.auth in config.yaml.",
                status=403,
                content_type="text/plain; charset=utf-8",
            )
            return
        identity = self.auth.identify(self)
        form = parse_qs(self._post_body)
        if not self._check_csrf(identity.user, (form.get("csrf") or [""])[0]):
            self._send(
                "Invalid form token — reload the settings page and try again.",
                status=403,
                content_type="text/plain; charset=utf-8",
            )
            return
        config = load_config(self.config_path)
        desired = {}
        for col in toggleable_collectors(config):
            now = f"collector:{col['name']}" in form
            if now != col["enabled"]:
                desired[col["name"]] = now
        if desired:
            try:
                with open(self.config_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
                write_config(self.config_path, apply_collector_toggles(text, desired))
            except Exception as exc:
                self._redirect("/settings?error=" + quote(str(exc)[:200]))
                return
        self._redirect("/settings?saved=1")

    # --- repairs: approval-gated repair proposals ----------------------

    def _render_repairs(self) -> None:
        config = load_config(self.config_path)
        identity = self.auth.identify(self)
        query = parse_qs(urlparse(self.path).query)
        enabled = bool(config.get("repair", {}).get("enabled", False))
        proposals: list[dict[str, Any]] = []
        if enabled:
            conn = db.connect(self.database_path)
            try:
                proposals = db.list_repair_proposals(conn, limit=50)
            finally:
                conn.close()
        self._send(render_repairs_page(
            proposals,
            editable=self._editable(),
            csrf_token=self._csrf_token(identity.user) if identity else "",
            repair_enabled=enabled,
            notice=(query.get("ok") or [None])[0],
            error=(query.get("error") or [None])[0],
        ))

    def _save_repair_decision(self) -> None:
        if not self._editable():
            self._send("Approving repairs requires authentication — enable web.auth in config.yaml.",
                       status=403, content_type="text/plain; charset=utf-8")
            return
        config = load_config(self.config_path)
        if not config.get("repair", {}).get("enabled", False):
            self._send("Repairs are disabled.", status=403, content_type="text/plain; charset=utf-8")
            return
        identity = self.auth.identify(self)
        form = parse_qs(self._post_body)
        if not self._check_csrf(identity.user, (form.get("csrf") or [""])[0]):
            self._send("Invalid form token — reload the repairs page and try again.",
                       status=403, content_type="text/plain; charset=utf-8")
            return
        from homelab_guardian import repair

        decision = (form.get("decision") or [""])[0]
        try:
            proposal_id = int((form.get("proposal_id") or ["0"])[0])
        except ValueError:
            self._redirect("/repairs?error=" + quote("Invalid proposal id."))
            return
        conn = db.connect(self.database_path)
        try:
            if decision == "approve":
                repair.approve(conn, proposal_id, approved_by=identity.user)
                self._redirect("/repairs?ok=" + quote(f"Proposal #{proposal_id} approved."))
            elif decision == "deny":
                repair.deny(conn, proposal_id, denied_by=identity.user)
                self._redirect("/repairs?ok=" + quote(f"Proposal #{proposal_id} denied."))
            else:
                self._redirect("/repairs?error=" + quote("Unknown decision."))
        except repair.RepairError as exc:
            self._redirect("/repairs?error=" + quote(str(exc)[:200]))
        finally:
            conn.close()

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
            repairs_pending = None
            if load_config(self.config_path).get("repair", {}).get("enabled", False):
                repairs_pending = sum(1 for p in db.list_repair_proposals(conn, limit=200) if p["status"] == "proposed")
        finally:
            conn.close()
        # only the live view auto-refreshes; historical scans are static
        refresh = self.refresh_seconds if scan_id is None else 0
        self._send(render_scan_page(scan, diff, history, refresh_seconds=refresh, repairs_pending=repairs_pending))


def serve(
    config: dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 8674,
    scan_interval: int = 0,
    scan_loop: Any = None,
    config_path: str = "config.yaml",
) -> int:
    database_path = config.get("app", {}).get("database_path", "data/guardian.sqlite")

    from homelab_guardian.secrets import build_store

    secrets = build_store(config.get("secrets", {}))
    authenticator = build_authenticator(config, secrets)
    auth_mode = str((config.get("web") or {}).get("auth", {}).get("mode", "none")).lower()

    handler = type(
        "BoundGuardianHandler",
        (GuardianRequestHandler,),
        {
            "database_path": database_path,
            "config_path": config_path,
            "auth": authenticator,
            "auth_mode": auth_mode,
            "csrf_secret": secrets_mod.token_bytes(32),
        },
    )

    if scan_interval > 0 and scan_loop is not None:
        worker = threading.Thread(target=scan_loop, daemon=True, name="guardian-scan-loop")
        worker.start()
        print(f"Background scans every {scan_interval} seconds.")

    server = ThreadingHTTPServer((host, port), handler)
    shown_host = "localhost" if host in {"127.0.0.1", "::1"} else host
    print(f"Guardian web view on http://{shown_host}:{port} (read-only). Press Ctrl+C to stop.")
    print(f"Auth: {auth_mode}.")
    if host == "0.0.0.0" and auth_mode == "none":
        print("Listening on all interfaces with no auth — anyone on your network can view reports.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
    return 0
