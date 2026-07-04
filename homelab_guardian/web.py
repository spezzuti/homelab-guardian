from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets as secrets_mod
import threading
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from homelab_guardian import db
from homelab_guardian.config import load_config
from homelab_guardian.configedit import (
    apply_collector_toggles,
    apply_setting_edits,
    editable_settings,
    parse_setting_edits,
    toggleable_collectors,
    write_config,
)
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

# --- brand art -----------------------------------------------------------
# Real branding is raster art, not generated vectors. Drop the files below
# into homelab_guardian/assets/ (see its README for specs) and the dashboard
# picks them up: `hero` becomes the character art blended into the header
# band, `logotype` replaces the CSS-lettered title. Absent files fall back
# to the text-only band — the feature degrades, never breaks.
_BRAND_FILES = {
    "hero.webp": "image/webp",
    "hero.png": "image/png",
    "logotype.webp": "image/webp",
    "logotype.png": "image/png",
    "favicon.png": "image/png",
}


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def brand_assets() -> dict[str, str]:
    """kind -> /brand/ URL for each present asset (webp preferred)."""
    found: dict[str, str] = {}
    for kind in ("hero", "logotype"):
        for ext in ("webp", "png"):
            if (_assets_dir() / f"{kind}.{ext}").is_file():
                found[kind] = f"/brand/{kind}.{ext}"
                break
    return found


def favicon_link() -> str:
    """The real-art favicon when present, else the built-in placeholder."""
    if (_assets_dir() / "favicon.png").is_file():
        return '<link rel="icon" href="/brand/favicon.png">'
    return FAVICON_LINK


# Favicon fallback: a shield-and-helm placeholder, used only when no real-art
# favicon has been installed in assets/.
FAVICON_LINK = (
    '<link rel="icon" href="data:image/svg+xml,'
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 132'%3E"
    "%3Cpath fill='%231b222c' d='M60 3 L114 20 V70 C114 100 92 120 60 129 C28 120 6 100 6 70 V20 Z'/%3E"
    "%3Cpath fill='%23aeb9c6' d='M60 22 Q37 23 35 47 L35 62 L46 71 L46 46 Q47 39 60 39 Q73 39 74 46 L74 71 L85 62 L85 47 Q83 23 60 22 Z'/%3E"
    "%3Cpath fill='%23515f73' d='M40 32 Q60 10 80 32 L74 37 Q60 22 46 37 Z'/%3E"
    "%3Cpath fill='%230b0e12' d='M46 46 Q47 40 60 40 Q73 40 74 46 L74 70 L60 78 L46 70 Z'/%3E"
    "%3Cpath fill='%23aeb9c6' d='M56 40 L64 40 L62 63 L60 66 L58 63 Z'/%3E"
    "%3Cpath fill='%238792a0' d='M46 70 L60 78 L74 70 L77 83 L68 90 L60 103 L52 90 L43 83 Z'/%3E"
    '%3C/svg%3E">'
)

# Brand ("interlock"): a slate accent for identity surfaces — wordmark, logo,
# focus rings. Deliberately NOT green/red/amber: status colors keep exclusive
# ownership of meaning, the brand never competes with them.
_DARK_VARS = """
    --bg: #14171c; --card: #1d222a; --text: #e8eaee; --muted: #9aa3b0;
    --critical: #ff5d64; --warning: #ffb454; --unknown: #8fa1bd; --ok: #4ecb71;
    --border: #2a313b; --brand: #93a5c4;
"""

PAGE_STYLE = f"""
:root {{
  color-scheme: light dark;
  --bg: #f5f6f8; --card: #ffffff; --text: #1c2330; --muted: #69707d;
  --critical: #d4373e; --warning: #c77d00; --unknown: #6c7a93; --ok: #2c8a4b;
  --border: #e3e6ea; --brand: #3d4c66;
  --mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{{_DARK_VARS}}}
}}
:root[data-theme="dark"] {{{_DARK_VARS}}}"""

PAGE_STYLE += """
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: radial-gradient(1100px 520px at 50% -160px,
    color-mix(in srgb, var(--brand) 9%, var(--bg)), var(--bg)) fixed;
}
main { max-width: 880px; margin: 0 auto; padding: 20px 16px 64px; }
a { color: inherit; }
::selection { background: color-mix(in srgb, var(--brand) 28%, transparent); }
/* --- the hero band: dark stone in both themes, status-reactive ---------- */
header.overall {
  position: relative; overflow: hidden;
  border-radius: 14px; padding: 22px 26px 20px; margin-bottom: 20px;
  background: linear-gradient(160deg, #1a212b 0%, #10141a 58%, #161d26 100%);
  border: 1px solid #29323f;
  border-bottom: 3px solid var(--accent, #3a4556);
  box-shadow: 0 18px 40px -30px rgba(0, 0, 0, 0.7);
}
header.overall::before { /* faint diagonal stone grain */
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(115deg,
    rgba(255, 255, 255, 0.022) 0 2px, transparent 2px 7px);
}
header.overall::after { /* status glow rising behind the emblem */
  content: ""; position: absolute; left: -40px; top: -60px; width: 260px; height: 260px;
  pointer-events: none; border-radius: 50%;
  background: radial-gradient(closest-side, var(--accent, #3a4556), transparent 70%);
  opacity: 0.28; filter: blur(8px);
}
.hero { position: relative; display: flex; align-items: center; gap: 22px; }
.hero-text { min-width: 0; }
header.overall h1 {
  margin: 0 0 4px; font-size: 1.45rem; font-weight: 700;
  font-family: Georgia, "Times New Roman", serif;
  text-transform: uppercase; letter-spacing: 0.16em; line-height: 1.15;
  background: linear-gradient(180deg, #dde4ee 0%, #a6b2c2 55%, #6f7c8e 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  filter: drop-shadow(0 2px 2px rgba(0, 0, 0, 0.55));
}
header.overall .status {
  font-size: 1.55rem; font-weight: 750; letter-spacing: 0.04em;
  color: var(--accent, #9aa8bb);
  filter: drop-shadow(0 0 14px color-mix(in srgb, var(--accent, #9aa8bb) 45%, transparent));
}
header.overall .meta { color: #96a2b4; font-size: 0.85rem; margin-top: 5px; }
header.overall .meta a { color: inherit; }
/* character art blended into the band; lit by the overall status */
header.overall.has-art { min-height: 196px; display: flex; align-items: center; }
header.overall.has-art .hero-text { max-width: 56%; }
.hero-art {
  position: absolute; right: 0; top: 0; height: 100%; max-width: 46%;
  object-fit: cover; object-position: right 14%; pointer-events: none;
  -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 30%);
  mask-image: linear-gradient(90deg, transparent 0, #000 30%);
}
header.overall.warn .hero-art { filter: drop-shadow(0 0 26px color-mix(in srgb, var(--warning) 40%, transparent)); }
header.overall.crit .hero-art { filter: drop-shadow(0 0 26px color-mix(in srgb, var(--critical) 50%, transparent)); }
@media (max-width: 640px) {
  .hero-art { opacity: 0.3; max-width: 75%; }
  header.overall.has-art .hero-text { max-width: none; }
}
/* carved logotype art replaces the CSS-lettered title when present */
h1.logotype { background: none; filter: drop-shadow(0 2px 3px rgba(0, 0, 0, 0.6)); }
h1.logotype img { display: block; max-height: 60px; max-width: 100%; }
/* --- top toolbar: brand + controls, out of the art's way ---------------- */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 2px 2px 12px;
}
.tb-brand {
  font-family: var(--mono); text-transform: uppercase;
  letter-spacing: 0.22em; font-size: 0.72rem; color: var(--muted);
}
.tb-actions { display: flex; gap: 8px; align-items: center; }
.tb-btn {
  cursor: pointer; text-decoration: none; white-space: nowrap;
  background: var(--card); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; font-size: 0.85rem; font-weight: 600;
  padding: 6px 11px; line-height: 1;
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.tb-btn:hover { border-color: var(--brand); transform: translateY(-1px); }
.counts { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-bottom: 20px; }
.pill {
  border-radius: 999px; padding: 5px 15px; font-size: 0.9rem; font-weight: 600;
  background: var(--card); border: 1px solid var(--border);
  border-top: 2px solid var(--accent, var(--border));
  box-shadow: 0 8px 18px -16px rgba(16, 20, 26, 0.5);
}
.pill b { font-size: 1.05rem; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 16px 20px; margin-bottom: 16px;
  box-shadow: 0 1px 2px rgba(16, 20, 26, 0.05), 0 14px 30px -26px rgba(16, 20, 26, 0.45);
}
.card h2 {
  margin: 0 0 10px; font-size: 0.8rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted);
  display: flex; align-items: center; gap: 12px;
}
.card h2::after {
  content: ""; flex: 1; height: 1px;
  background: linear-gradient(90deg, var(--border), transparent);
}
.check {
  border: 1px solid color-mix(in srgb, var(--accent, var(--border)) 30%, var(--border));
  border-left: 5px solid var(--accent, var(--border));
  background: color-mix(in srgb, var(--accent, var(--border)) 6%, var(--card));
  border-radius: 4px 10px 10px 4px;
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
/* the what-changed timeline: a cold rail with brand nodes */
ul.changes { margin: 0; padding-left: 4px; list-style: none; position: relative; }
ul.changes::before {
  content: ""; position: absolute; left: 3px; top: 10px; bottom: 8px; width: 2px;
  background: linear-gradient(180deg, var(--brand), transparent);
  opacity: 0.5; border-radius: 2px;
}
ul.changes li { margin: 9px 0; padding-left: 18px; position: relative; }
ul.changes li::before {
  content: ""; position: absolute; left: -1px; top: 0.5em; width: 10px; height: 10px;
  border-radius: 50%; background: var(--card); border: 2px solid var(--brand);
}
.briefing p { margin: 9px 0; font-family: Georgia, "Times New Roman", serif; font-size: 1.02rem; }
.card.briefing { border-left: 3px solid var(--brand); }
.card.acked summary { cursor: pointer; list-style: revert; }
.card.acked summary h2 { display: inline; }
.ackhint { color: var(--muted); font-size: 0.88rem; }
.acknote { color: var(--muted); font-style: italic; }
table.history { width: 100%; border-collapse: collapse; font-size: 0.92rem; font-variant-numeric: tabular-nums; }
table.history th, table.history td {
  text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border);
}
table.history th {
  color: var(--muted); font-weight: 700; font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.1em;
}
table.history tbody tr:hover td { background: color-mix(in srgb, var(--brand) 6%, transparent); }
table.history tr.current td { font-weight: 650; }
footer { color: var(--muted); font-size: 0.8rem; margin-top: 28px; text-align: center; }
.f-logo { display: block; height: 18px; width: auto; margin: 0 auto 8px; opacity: 0.45; }
code, pre, .cid { font-family: var(--mono); }
.pill b { font-family: var(--mono); }
button:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; border-radius: 4px; }
/* --- motion: quiet, semantic, and off for reduced-motion users ---------- */
@media (prefers-reduced-motion: no-preference) {
  header.overall, .counts, .card, details.tile { animation: rise 0.5s cubic-bezier(0.2, 0.7, 0.3, 1) both; }
  .counts { animation-delay: 0.05s; }
  .card:nth-of-type(1) { animation-delay: 0.08s; }
  .card:nth-of-type(2) { animation-delay: 0.12s; }
  .card:nth-of-type(3) { animation-delay: 0.16s; }
  .card:nth-of-type(n+4) { animation-delay: 0.2s; }
  details.tile { animation-delay: 0.2s; }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  header.overall::after { animation: breathe 9s ease-in-out infinite; }
  @keyframes breathe { 0%, 100% { opacity: 0.22; } 50% { opacity: 0.4; } }
  header.overall.crit .hero-art, header.overall.warn .hero-art { animation: ember 2.6s ease-in-out infinite; }
  @keyframes ember { 0%, 100% { filter: drop-shadow(0 0 18px color-mix(in srgb, var(--accent) 35%, transparent)); }
    50% { filter: drop-shadow(0 0 30px color-mix(in srgb, var(--accent) 60%, transparent)); } }
  details.tile, .card { transition: transform 0.16s ease, box-shadow 0.16s ease; }
  details.tile:hover { transform: translateY(-2px); box-shadow: 0 12px 28px -20px rgba(16, 20, 26, 0.55); }
}
h2.sectionhead { margin: 22px 2px 10px; font-size: 1.02rem; }
.settings-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 11px 4px; border-bottom: 1px solid var(--border);
}
.settings-row:last-child { border-bottom: none; }
.settings-row .label { font-weight: 600; }
.settings-row .cid { color: var(--muted); font-size: 0.82rem; }
.settings-row .cdesc { display: block; color: var(--muted); font-size: 0.82rem; margin-top: 3px; }
input.toggle { width: 18px; height: 18px; accent-color: var(--brand); }
.settings-row input.num {
  width: 96px; padding: 5px 8px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--bg); color: var(--text); font-size: 0.92rem;
  font-family: var(--mono);
}
.settings-row input.num:focus { outline: 2px solid var(--brand); border-color: var(--brand); }
h3.sgroup { margin: 16px 0 2px; font-size: 0.95rem; color: var(--muted); }
.savebar { margin-top: 18px; display: flex; gap: 14px; align-items: center; }
button.save {
  background: linear-gradient(180deg, color-mix(in srgb, var(--ok) 88%, #fff), var(--ok));
  color: #fff; border: none; border-radius: 8px;
  padding: 9px 20px; font-size: 0.85rem; font-weight: 700; cursor: pointer;
  text-transform: uppercase; letter-spacing: 0.07em;
  box-shadow: 0 8px 18px -12px color-mix(in srgb, var(--ok) 70%, transparent);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
button.save:hover { transform: translateY(-1px); box-shadow: 0 10px 22px -12px color-mix(in srgb, var(--ok) 80%, transparent); }
.rbadge {
  background: var(--critical); color: #fff; border-radius: 999px;
  font-size: 0.7rem; font-weight: 700; padding: 1px 6px; margin-left: 5px; vertical-align: top;
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
    return f'<a class="tb-btn" href="/repairs" title="Repairs">🔧 Repairs{badge}</a>'


def render_scan_page(
    scan: tuple[int, str, dict[str, Any]],
    diff: ScanDiff,
    history: list[tuple[int, str, dict[str, Any]]],
    refresh_seconds: int = 60,
    repairs_pending: int | None = None,
    brand: dict[str, str] | None = None,
) -> str:
    scan_id, created_at, snapshot = scan
    checks = checks_from_snapshot(snapshot)
    overall = overall_of(checks)
    icon, label = STATUS_META.get(overall, ("•", overall))
    counts = _counts(checks)
    narrative = snapshot.get("narrative") or ""
    app_name = html.escape(str(snapshot.get("app", "Homelab Guardian")))

    brand = brand or {}
    hero_art = f'<img class="hero-art" src="{brand["hero"]}" alt="">' if "hero" in brand else ""
    art_class = " has-art" if hero_art else ""
    if "logotype" in brand:
        title_html = f'<h1 class="logotype"><img src="{brand["logotype"]}" alt="{app_name}"></h1>'
        footer_mark = f'<img class="f-logo" src="{brand["logotype"]}" alt="">'
    else:
        title_html = f"<h1>{app_name}</h1>"
        footer_mark = ""

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
<title>{app_name} — {label}</title>{favicon_link()}<style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<nav class="topbar">
<span class="tb-brand">Homelab Guardian</span>
<span class="tb-actions">
{_repairs_link(repairs_pending)}
<a class="tb-btn" href="/settings" title="Settings">⚙ Settings</a>
<button class="theme-toggle tb-btn" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
</span>
</nav>
<header class="overall {_STATUS_CLASS.get(overall, 'unk')}{art_class}">
{hero_art}
<div class="hero">
<div class="hero-text">
{title_html}
<div class="status">{icon} {label.upper()}</div>
<div class="meta">Scan #{scan_id} · {_fmt_time(created_at)}{' · auto-refreshes every %ds' % refresh_seconds if refresh_seconds else ''}</div>
</div>
</div>
</header>
<div class="counts">{pills}</div>
{_render_briefing(narrative) if narrative else ''}
{_render_changes(diff)}
{_render_groups(checks)}
{_render_history(history, scan_id)}
<footer>{footer_mark}Homelab Guardian — read-only view · <a href="/">latest</a></footer>
</main>{TILE_SCRIPT}</body></html>"""


def render_empty_page() -> str:
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Homelab Guardian</title>"
        f"{favicon_link()}<style>{PAGE_STYLE}</style></head><body><main><div class=\"card\"><h2>No scans yet</h2>"
        f"<p>Run <code>guardian --config config.yaml</code> to produce the first scan, "
        f"or start the server with <code>--interval</code> to scan continuously.</p></div></main></body></html>"
    )


def _render_setting_inputs(settings: list[dict[str, Any]], editable: bool) -> str:
    """The Thresholds & timing card: whitelisted numeric inputs, grouped."""
    if not settings:
        return ""
    disabled = "" if editable else " disabled"
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in settings:
        groups.setdefault(s["group"], []).append(s)
    parts = [
        '<div class="card"><h2>Thresholds &amp; timing</h2>',
        '<p class="notice">Numeric limits for the enabled checks. Values outside the allowed range are rejected; comments in config.yaml are preserved.</p>',
    ]
    for group, entries in groups.items():
        parts.append(f'<h3 class="sgroup">{html.escape(group)}</h3>')
        for s in entries:
            ctx = f'<span class="cid">{html.escape(s["context"])}</span> · ' if s.get("context") else ""
            step = "1" if s["kind"] == "int" else "any"
            value = f"{s['value']:g}"
            parts.append(
                f'<label class="settings-row"><span>'
                f'<span class="label">{html.escape(s["label"])}</span> '
                f'<span class="cid">{html.escape(s["unit"])}</span>'
                f'<span class="cdesc">{ctx}allowed {s["min"]:g}–{s["max"]:g}</span></span>'
                f'<input class="num" type="number" name="setting:{html.escape(s["token"])}" '
                f'value="{value}" min="{s["min"]:g}" max="{s["max"]:g}" step="{step}"{disabled}>'
                f"</label>"
            )
    parts.append("</div>")
    return "".join(parts)


def render_settings_page(
    collectors: list[dict[str, Any]],
    *,
    editable: bool,
    csrf_token: str,
    identity: Any = None,
    saved: bool = False,
    error: str | None = None,
    auth_mode: str = "none",
    settings: list[dict[str, Any]] | None = None,
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

    settings_html = _render_setting_inputs(settings or [], editable)
    if editable:
        who = f"Signed in as <b>{html.escape(identity.user)}</b>." if identity else ""
        logout = ' · <a href="/auth/logout">Sign out</a>' if auth_mode == "oidc" else ""
        body = (
            f'<form method="post" action="/settings">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf_token)}">'
            f'<div class="card"><h2>Collectors</h2>'
            f'<p class="notice">Turn checks on or off. Saved to config.yaml; the running scan picks up changes automatically.</p>'
            f"{rows_html}</div>"
            f"{settings_html}"
            f'<div class="card"><div class="savebar"><button class="save" type="submit">Save</button>'
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
            f"{settings_html}"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Homelab Guardian — Settings</title>{favicon_link()}<style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<nav class="topbar">
<span class="tb-brand">Homelab Guardian</span>
<span class="tb-actions">
<a class="tb-btn" href="/">← Dashboard</a>
<button class="theme-toggle tb-btn" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
</span>
</nav>
<header class="overall okc">
<h1>Settings</h1>
<div class="meta">Collectors and thresholds — saved straight into config.yaml.</div>
</header>
{banner}
{body}
<footer>Homelab Guardian — settings</footer>
</main></body></html>"""


_REPAIR_STATUS_CLASS = {
    "proposed": "warn", "approved": "okc", "denied": "unk", "executed": "okc", "failed": "crit",
}


def _render_proposal(p: dict[str, Any], *, editable: bool, csrf_token: str) -> str:
    plan_raw = p.get("plan_json")
    plan: dict[str, Any] = plan_raw if isinstance(plan_raw, dict) else {}
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
    if plan.get("risk"):
        parts.append(f'<div class="cid">risk: {html.escape(str(plan["risk"]))}</div>')
    if plan.get("blast_radius"):
        parts.append(f'<div class="notice">{html.escape(str(plan["blast_radius"]))}</div>')
    if plan.get("preview"):
        prev = ", ".join(f"{k}={v}" for k, v in plan["preview"].items())
        parts.append(f'<div class="rplan">Preview: <code>{html.escape(prev[:300])}</code></div>')
    if plan.get("warning"):
        parts.append(f'<div class="banner err">⚠ {html.escape(str(plan["warning"]))}</div>')
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
<title>Homelab Guardian — Repairs</title>{favicon_link()}<style>{PAGE_STYLE}</style>{THEME_SCRIPT}</head>
<body><main>
<nav class="topbar">
<span class="tb-brand">Homelab Guardian</span>
<span class="tb-actions">
<a class="tb-btn" href="/">← Dashboard</a>
<button class="theme-toggle tb-btn" onclick="toggleTheme()" title="Toggle light/dark theme">🌓</button>
</span>
</nav>
<header class="overall okc">
<h1>Repairs</h1>
<div class="meta">Approve or deny proposed repairs — execution stays with the CLI and MCP.</div>
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
        # Brand art is public and cacheable. The name must be in the fixed
        # allowlist — there is no directory access, so no traversal surface.
        if path.startswith("/brand/"):
            self._serve_brand(path.removeprefix("/brand/"))
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

    def _serve_brand(self, name: str) -> None:
        content_type = _BRAND_FILES.get(name)
        asset = _assets_dir() / name
        if content_type is None or not asset.is_file():
            self._send("not found", status=404, content_type="text/plain; charset=utf-8")
            return
        payload = asset.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

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
            error=next(iter(query.get("error") or []), None),
            auth_mode=self.auth_mode,
            settings=editable_settings(config),
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
        if identity is None:  # unreachable behind _editable(), but fail closed
            self._send("Authentication required.", status=403, content_type="text/plain; charset=utf-8")
            return
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
        # Whitelisted numeric edits: only tokens the registry derives from the
        # CURRENT config are accepted — a client cannot post arbitrary paths.
        posted = {key[len("setting:"):]: values[0] for key, values in form.items()
                  if key.startswith("setting:") and values}
        try:
            edits = parse_setting_edits(config, posted)
        except ValueError as exc:
            self._redirect("/settings?error=" + quote(str(exc)[:200]))
            return
        if desired or edits:
            try:
                with open(self.config_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
                if desired:
                    text = apply_collector_toggles(text, desired)
                if edits:
                    text = apply_setting_edits(text, edits)
                write_config(self.config_path, text)
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
            notice=next(iter(query.get("ok") or []), None),
            error=next(iter(query.get("error") or []), None),
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
        if identity is None:  # unreachable behind _editable(), but fail closed
            self._send("Authentication required.", status=403, content_type="text/plain; charset=utf-8")
            return
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
                res = repair.approve(conn, proposal_id, approved_by=identity.user)
                message = f"Proposal #{proposal_id} approved."
                if res.get("confirm_token"):
                    message += (f" Destructive action — executing requires the confirmation token "
                                f"{res['confirm_token']} (shown only here).")
                self._redirect("/repairs?ok=" + quote(message))
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
        self._send(render_scan_page(scan, diff, history, refresh_seconds=refresh,
                                    repairs_pending=repairs_pending, brand=brand_assets()))


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
    try:
        authenticator = build_authenticator(config, secrets)
    except ValueError as exc:
        # Fail closed rather than serve a dashboard whose auth can't work (e.g. an
        # OIDC secret that didn't load because the vault was unreachable). Exit
        # non-zero so the supervisor restarts and retries once it's reachable.
        print(f"Refusing to start the dashboard — authentication is misconfigured: {exc}")
        return 1
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
    if host == "0.0.0.0" and auth_mode == "none":  # nosec B104
        print("Listening on all interfaces with no auth — anyone on your network can view reports.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
    return 0
