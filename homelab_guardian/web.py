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
    "logotype-cut.png": "image/png",
    "favicon.png": "image/png",
    # vendored fonts: the dashboard never calls out to a font CDN
    "fonts/oswald-500.woff2": "font/woff2",
    "fonts/oswald-600.woff2": "font/woff2",
    "fonts/plexsans-400.woff2": "font/woff2",
    "fonts/plexsans-500.woff2": "font/woff2",
    "fonts/plexsans-600.woff2": "font/woff2",
    "fonts/plexmono-400.woff2": "font/woff2",
    "fonts/plexmono-600.woff2": "font/woff2",
}


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def brand_assets() -> dict[str, str]:
    """kind -> /brand/ URL for each present asset (webp preferred)."""
    found: dict[str, str] = {}
    for kind in ("hero", "logotype", "logotype-cut"):
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

# Design system: "Stronghold refined" (option 2A of the user's Claude Design
# project). Single committed dark theme — the design's world is a night watch;
# there is no light mode by choice. Fonts are vendored under /brand/fonts.

_FONT_FACES = "".join(
    f"@font-face {{ font-family:'{fam}'; font-weight:{w}; font-style:normal; "
    f"font-display:swap; src:url('/brand/fonts/{slug}.woff2') format('woff2'); }}"
    for fam, w, slug in [
        ("Oswald", 500, "oswald-500"), ("Oswald", 600, "oswald-600"),
        ("IBM Plex Sans", 400, "plexsans-400"), ("IBM Plex Sans", 500, "plexsans-500"),
        ("IBM Plex Sans", 600, "plexsans-600"),
        ("IBM Plex Mono", 400, "plexmono-400"), ("IBM Plex Mono", 600, "plexmono-600"),
    ]
)

PAGE_STYLE = _FONT_FACES + """
:root {
  color-scheme: dark;
  --bg: #0b0e14; --shell: #151b26; --hero: #171c26; --panel: #1a2130;
  --inner: #141a28; --code: #10141c;
  --bd: #2b3547; --bd-in: #262f42; --row: #232b3a;
  --t1: #e2e7f0; --t2: #d6dce8; --t3: #c7cfdd; --mut: #8f9db3; --mut2: #7d8aa0;
  --dim: #5d6a80; --dim2: #6b788f;
  --acc: #63a4d8; --link: #8ec3ea;
  --ok: #4cb572; --ok-t: #7fd6a0; --crit: #d9584a; --crit-t: #f0897d;
  --warn: #d9a13b; --warn-t: #e8bf6e; --unk: #7d8aa0; --unk-t: #a9b6c9;
  --oswald: 'Oswald', system-ui, sans-serif;
  --sans: 'IBM Plex Sans', system-ui, sans-serif;
  --mono: 'IBM Plex Mono', ui-monospace, Consolas, monospace;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--t3); font: 14px/1.5 var(--sans); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
::selection { background: rgba(99, 164, 216, 0.3); }
main { max-width: 1280px; margin: 0 auto; padding: 28px 20px 64px; }
@keyframes hgPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@keyframes hgGlow { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }

/* --- the stronghold shell -------------------------------------------- */
.shell-card { background: var(--shell); border: 1px solid var(--bd); border-radius: 14px; overflow: hidden; }
.hero { position: relative; height: 290px; background: var(--hero); }
.hero-art {
  position: absolute; right: 0; top: 0; height: 100%; width: 560px;
  object-fit: cover; object-position: 60% 12%;
}
.hero-wash {
  position: absolute; inset: 0;
  background: linear-gradient(90deg, var(--shell) 42%, rgba(21, 27, 38, 0.55) 68%, rgba(21, 27, 38, 0) 100%);
}
.hero-glow {
  position: absolute; left: 2px; top: -11px; width: 520px; height: 160px;
  background: radial-gradient(ellipse 50% 46% at 50% 50%, var(--glow, rgba(76, 181, 114, 0.16)) 0%, transparent 70%);
  animation: hgGlow 5.5s ease-in-out infinite; pointer-events: none;
}
.hero-body { position: absolute; left: 32px; top: 30px; right: 480px; }
.hero-logo { width: 460px; max-width: 100%; display: block; position: relative; }
.hero-logo-text { font: 600 34px var(--oswald); letter-spacing: 4px; color: var(--t2); text-transform: uppercase; }
.hero-row { display: flex; align-items: center; gap: 14px; margin-top: 20px; flex-wrap: wrap; }
.ochip {
  border: 1px solid var(--oc, var(--ok-t)); color: var(--oc, var(--ok-t));
  background: var(--ocbg, rgba(76, 181, 114, 0.14));
  font: 600 13px var(--mono); letter-spacing: 2px; padding: 7px 16px; border-radius: 6px;
}
.omsg { color: var(--mut); font-size: 13.5px; }
.hero-meta {
  display: flex; align-items: center; gap: 7px; margin-top: 12px;
  color: var(--dim); font: 12px var(--mono);
}
.pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); animation: hgPulse 2.4s infinite; }

/* folder tabs riding the hero's bottom edge */
.tabs { position: absolute; left: 32px; bottom: 0; display: flex; gap: 4px; }
.tab {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font: 500 14px var(--oswald); letter-spacing: 2px; text-transform: uppercase;
  padding: 11px 22px; border-radius: 8px 8px 0 0; text-decoration: none;
  color: var(--mut2); background: rgba(11, 14, 20, 0.55);
  border: 1px solid var(--row); border-bottom: none;
}
.tab:hover { color: var(--t2); text-decoration: none; }
.tab.active { color: var(--t2); background: var(--shell); border-color: var(--bd); position: relative; z-index: 1; }
.rbadge {
  background: var(--crit); color: #fff; font: 600 10px var(--mono);
  padding: 1px 6px; border-radius: 8px;
}
.deck { padding: 28px 32px; display: flex; flex-direction: column; gap: 22px; border-top: 1px solid var(--bd); }

/* --- panels ------------------------------------------------------------ */
.panel { background: var(--panel); border: 1px solid var(--bd); border-radius: 12px; padding: 18px 24px; }
.ptitle {
  font: 500 13px var(--oswald); letter-spacing: 2px; color: var(--mut2);
  text-transform: uppercase; margin: 0 0 14px;
}
.ptitle-lg { font: 500 15px var(--oswald); letter-spacing: 2px; color: var(--t2); text-transform: uppercase; margin: 0; }
.pnote { color: var(--dim); font-size: 12px; margin: -6px 0 10px; }

/* network strip */
.net { display: flex; align-items: flex-start; gap: 0; overflow-x: auto; padding-bottom: 4px; }
.net-node { text-align: center; flex: none; }
.net-wan {
  width: 44px; height: 44px; border-radius: 50%; border: 2px solid #3a465c; background: var(--shell);
  margin: 0 auto; display: flex; align-items: center; justify-content: center;
  color: var(--mut2); font: 600 9px var(--mono);
}
.net-box {
  width: 44px; height: 44px; border-radius: 10px; border: 2px solid var(--nb, var(--ok));
  background: var(--shell); margin: 0 auto;
}
.host-grid { display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 14px; flex: 1; min-width: 0; }
.host-card { background: var(--inner); border: 1px solid var(--bd-in); border-radius: 12px; padding: 12px 16px; min-width: 0; }
.host-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.host-name { color: var(--t1); font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.host-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gd, var(--ok)); flex: none; }
.host-sub { color: var(--dim2); font-size: 11.5px; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 900px) { .host-grid { grid-template-columns: 1fr 1fr; } }
.net-name { color: var(--mut); font-size: 12px; margin-top: 7px; max-width: 86px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.net-link { width: 40px; height: 2px; background: var(--bd); margin-top: 22px; flex: none; }

/* counts + changed */
.duo { display: grid; grid-template-columns: 1fr 340px; gap: 22px; align-items: start; }
.counts { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.count { border-radius: 12px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; background: var(--cbg, var(--inner)); }
.count b { font: 500 26px var(--oswald); color: var(--ctx, var(--t2)); }
.count span { font: 10.5px var(--mono); letter-spacing: 1px; color: var(--ctx, var(--mut)); opacity: 0.8; }
.changes { display: flex; flex-direction: column; gap: 9px; }
.chg { display: flex; align-items: baseline; gap: 10px; font-size: 13.5px; }
.chg .arrow { font-family: var(--mono); width: 12px; flex: none; }
.chg .meta { color: var(--dim); font-size: 11.5px; font-family: var(--mono); }

/* group cards */
.groups { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; align-items: start; }
.gtally { font: 11px var(--mono); }
.grow { display: flex; align-items: flex-start; gap: 12px; padding: 9px 0; border-top: 1px solid var(--row); }
.gdot { width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; flex-shrink: 0; background: var(--gd, var(--ok)); }
.grow .body { flex: 1; min-width: 0; }
.grow .top { display: flex; justify-content: space-between; gap: 10px; }
.grow .name { color: var(--t3); font-size: 13.5px; font-weight: 500; }
.grow .chip {
  font: 9.5px var(--mono); letter-spacing: 1px; padding: 3px 8px; border-radius: 4px;
  height: fit-content; flex: none; background: var(--chbg); color: var(--chtx);
}
.grow .summary { color: var(--mut2); font-size: 12.5px; margin-top: 2px; }
.grow .action { color: var(--acc); font-size: 12px; margin-top: 3px; }
.grow .acknote { color: var(--dim); font-size: 11.5px; margin-top: 3px; font-style: italic; }
.grow details { margin-top: 4px; }
.grow summary { cursor: pointer; color: var(--dim); font-size: 11.5px; }
.grow pre {
  background: var(--code); border: 1px solid var(--bd); color: var(--link);
  border-radius: 6px; padding: 8px 10px; overflow-x: auto; font: 12px var(--mono); margin: 6px 0 0;
}

/* briefing + history */
.briefing p { margin: 8px 0; color: var(--t3); font-size: 13.5px; }
.histrip { display: flex; flex-wrap: wrap; gap: 8px 14px; font: 12px var(--mono); color: var(--dim); }
.histrip a { color: var(--mut); }
.histrip .cur { color: var(--t2); font-weight: 600; }

/* settings */
.setgrid { display: grid; grid-template-columns: 1fr 400px; gap: 22px; align-items: start; }
.setrow { display: flex; align-items: center; gap: 14px; padding: 11px 0; border-top: 1px solid var(--row); }
.setrow .sname { width: 160px; color: var(--t2); font-size: 13.5px; font-weight: 500; flex-shrink: 0; }
.setrow .sdesc { flex: 1; color: var(--mut2); font-size: 12.5px; }
.switch { position: relative; width: 40px; height: 22px; flex-shrink: 0; cursor: pointer; }
.switch input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; }
.switch .track { position: absolute; inset: 0; border-radius: 11px; background: var(--bd); transition: background 0.2s; }
.switch .knob {
  position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%;
  background: #e8edf6; transition: left 0.2s;
}
.switch input:checked ~ .track { background: #2e7d4f; }
.switch input:checked ~ .knob { left: 20px; }
.switch input:disabled ~ .track { opacity: 0.55; }
.setrow input.num {
  width: 96px; padding: 5px 8px; border: 1px solid var(--bd); border-radius: 6px;
  background: var(--code); color: var(--t2); font: 12.5px var(--mono); flex-shrink: 0;
}
.setrow input.num:focus { outline: 1px solid var(--acc); border-color: var(--acc); }
.postpill { font: 10px var(--mono); letter-spacing: 1px; padding: 4px 10px; border-radius: 4px; flex: none; }
.warncard {
  background: rgba(217, 161, 59, 0.07); border: 1px solid rgba(217, 161, 59, 0.25);
  border-radius: 12px; padding: 16px 20px; color: #c9a869; font-size: 12.5px;
}
.savebar { display: flex; align-items: center; gap: 14px; }
.btn-go {
  cursor: pointer; background: #2e7d4f; color: #eaf6ee; border: none;
  font: 500 15px var(--oswald); letter-spacing: 2px; text-transform: uppercase;
  text-align: center; padding: 12px 26px; border-radius: 8px;
}
.btn-go:hover { background: #35935c; }
.btn-no {
  cursor: pointer; background: transparent; border: 1px solid #3a465c; color: var(--mut);
  font: 500 15px var(--oswald); letter-spacing: 2px; text-transform: uppercase;
  text-align: center; padding: 11px 26px; border-radius: 8px;
}
.btn-no:hover { border-color: var(--crit); color: var(--crit-t); }
.notice { color: var(--dim); font-size: 12px; }

/* repairs */
.prop { background: var(--panel); border: 1px solid var(--bd); border-radius: 12px; padding: 24px; display: grid; grid-template-columns: 1fr 300px; gap: 24px; }
.prop-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; color: var(--t2); font-size: 17px; font-weight: 600; }
.prop-title code { font-family: var(--mono); color: var(--acc); font-size: 15px; }
.pstat { font: 10px var(--mono); letter-spacing: 1px; padding: 4px 10px; border-radius: 4px; background: var(--chbg); color: var(--chtx); }
.prop-meta { color: var(--mut2); font-size: 12.5px; margin-top: 6px; }
.prop-meta code { font-family: var(--mono); }
.prop-grid { display: grid; grid-template-columns: 120px 1fr; gap: 9px 16px; margin-top: 18px; font-size: 13px; }
.prop-grid .k { color: var(--dim); }
.prop-grid .v { color: var(--t3); }
.prop-grid code {
  background: var(--code); border: 1px solid var(--bd); color: var(--link);
  padding: 3px 8px; border-radius: 4px; font: 12.5px var(--mono);
}
.prop-side { border-left: 1px solid var(--bd); padding-left: 24px; display: flex; flex-direction: column; justify-content: center; gap: 10px; }
.prop-empty { background: var(--panel); border: 1px dashed var(--bd); border-radius: 12px; padding: 28px; text-align: center; color: var(--dim); font-size: 13.5px; }
.audit { background: var(--panel); border: 1px solid var(--bd); border-radius: 12px; overflow: hidden; }
.audit-row { display: flex; align-items: center; gap: 18px; padding: 13px 22px; border-bottom: 1px solid var(--row); font-size: 13px; }
.audit-row:last-child { border-bottom: none; }
.audit-row .when { width: 95px; color: var(--dim); font: 12px var(--mono); flex-shrink: 0; }
.audit-row .act { width: 205px; color: var(--link); font: 12px var(--mono); flex-shrink: 0; }
.audit-row .tgt { width: 185px; color: var(--t3); flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; }
.audit-row .by { flex: 1; color: var(--mut2); }
.audit-row .res { font: 12px var(--mono); }

.banner-flash { border-radius: 10px; padding: 10px 14px; background: var(--panel); border: 1px solid var(--bd); }
.banner-flash.ok { border-left: 4px solid var(--ok); }
.banner-flash.err { border-left: 4px solid var(--crit); color: var(--crit-t); }
footer { color: var(--dim); font-size: 12px; margin-top: 22px; text-align: center; }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }

@media (max-width: 1080px) {
  .duo, .groups, .setgrid { grid-template-columns: 1fr; }
  .prop { grid-template-columns: 1fr; }
  .prop-side { border-left: none; padding-left: 0; border-top: 1px solid var(--bd); padding-top: 18px; }
}
@media (max-width: 900px) {
  .hero { height: auto; min-height: 250px; }
  .hero-art { opacity: 0.35; width: 100%; }
  .hero-body { right: 32px; position: relative; left: 0; top: 0; padding: 26px 32px 58px; }
  .hero-logo { width: 100%; }
  .tabs { left: 16px; right: 16px; overflow-x: auto; }
  .deck { padding: 18px 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .hero-glow, .pulse { animation: none; }
}
"""

_STATUS_CLASS = {"critical": "crit", "warning": "warn", "unknown": "unk", "ok": "okc"}

# Status ink for design 2A: solid dot color, chip text/background, hero glow.
_INK = {
    "critical": {"chip": "CRIT", "tx": "var(--crit-t)", "solid": "var(--crit)",
                 "bg": "rgba(217,88,74,.14)", "glow": "rgba(217,88,74,.22)"},
    "warning": {"chip": "WARN", "tx": "var(--warn-t)", "solid": "var(--warn)",
                "bg": "rgba(217,161,59,.14)", "glow": "rgba(217,161,59,.20)"},
    "unknown": {"chip": "UNK", "tx": "var(--unk-t)", "solid": "var(--unk)",
                "bg": "rgba(125,138,160,.14)", "glow": "rgba(125,138,160,.16)"},
    "ok": {"chip": "OK", "tx": "var(--ok-t)", "solid": "var(--ok)",
           "bg": "rgba(76,181,114,.14)", "glow": "rgba(76,181,114,.16)"},
}

_HERO_MSG = {
    "ok": "The wall holds. Nothing needs you.",
    "warning": "Threats sighted — worth a look when you have a minute.",
    "critical": "The wall is breached. Start at the top.",
    "unknown": "Fog of war — Guardian could not see everything.",
}

_CATEGORY_PREFIXES = [
    ("docker", "Containers"), ("hass", "Home Assistant"), ("homeassistant", "Home Assistant"),
    ("dns", "Network"), ("tcp", "Network"), ("http", "Web services"), ("tls", "Certificates"),
    ("backup", "Backups"), ("systemd", "Services"), ("disk", "Storage"),
    ("firewall", "Security"), ("exposed", "Security"), ("ssh", "Security"),
    ("updates", "Updates"), ("mount", "Storage"),
]
_SEVERITY = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}


def _category(check_id: str) -> str:
    for prefix, label in _CATEGORY_PREFIXES:
        if check_id.startswith(prefix):
            return label
    return "Other"


def effective_group(check: HealthCheck) -> str:
    """The heading a check rolls up under: its explicit group, or a sensible
    one derived from its id so older snapshots still group cleanly."""
    return check.group or _category(check.id)


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
    for status in ("critical", "warning", "unknown"):
        if status in statuses:
            return status
    return "ok" if statuses else "unknown"


def _worst(checks: list[HealthCheck]) -> str:
    statuses = {c.status for c in checks}
    for status in STATUS_ORDER[:-1]:
        if status in statuses:
            return status
    return "ok"


def _counts(checks: list[HealthCheck]) -> dict[str, int]:
    return {status: sum(1 for c in checks if c.status == status and not c.acknowledged) for status in STATUS_ORDER}


def _fmt_time(created_at: str) -> str:
    try:
        stamp = datetime.fromisoformat(created_at)
        return stamp.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(created_at)


def _ago(created_at: str) -> str:
    try:
        stamp = datetime.fromisoformat(created_at)
        seconds = max(0, int((datetime.now(stamp.tzinfo) - stamp).total_seconds()))
    except (TypeError, ValueError):
        return "?"
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def _repairs_link(repairs_pending: int | None) -> str:  # kept for callers
    return ""


# --- the stronghold shell -------------------------------------------------


def _tabs(active: str, repairs_pending: int | None) -> str:
    entries = [("/", "Overview"), ("/repairs", "Repairs"), ("/settings", "Settings")]
    out = []
    for href, label in entries:
        if href == "/repairs" and repairs_pending is None:
            continue
        badge = ""
        if href == "/repairs" and repairs_pending:
            badge = f' <span class="rbadge">{repairs_pending}</span>'
        cls = "tab active" if href == active else "tab"
        out.append(f'<a class="{cls}" href="{href}">{label}{badge}</a>')
    return f'<nav class="tabs">{"".join(out)}</nav>'


def _hero(brand: dict[str, str], *, overall: str, msg: str, meta: str,
          active: str, repairs_pending: int | None) -> str:
    ink = _INK.get(overall, _INK["unknown"])
    art = f'<img class="hero-art" src="{brand["hero"]}" alt="">' if "hero" in brand else ""
    if "logotype-cut" in brand:
        logo = f'<img class="hero-logo" src="{brand["logotype-cut"]}" alt="Homelab Guardian">'
    elif "logotype" in brand:
        logo = f'<img class="hero-logo" src="{brand["logotype"]}" alt="Homelab Guardian">'
    else:
        logo = '<div class="hero-logo-text">Homelab Guardian</div>'
    label = f"{ink['chip']} — " + {"ok": "ALL SYSTEMS", "critical": "ATTENTION", "warning": "DEGRADED",
                                   "unknown": "PARTIAL VIEW"}.get(overall, "STATUS")
    return (
        f'<div class="hero">{art}<div class="hero-wash"></div>'
        f'<div class="hero-glow" style="--glow:{ink["glow"]}"></div>'
        f'<div class="hero-body">{logo}'
        f'<div class="hero-row"><span class="ochip" style="--oc:{ink["tx"]};--ocbg:{ink["bg"]}">{label}</span>'
        f'<span class="omsg">{html.escape(msg)}</span></div>'
        f'<div class="hero-meta"><span class="pulse"></span>{meta}</div>'
        f"</div>{_tabs(active, repairs_pending)}</div>"
    )


def _shell(head_title: str, hero_html: str, deck_html: str, *, refresh: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>{head_title}</title>{favicon_link()}<style>{PAGE_STYLE}</style></head>
<body><main>
<div class="shell-card">
{hero_html}
<div class="deck">
{deck_html}
</div>
</div>
<footer>Homelab Guardian — deterministic watch · approval-gated hands · never raw shell</footer>
</main></body></html>"""


# --- overview panels -------------------------------------------------------


# The topology chain, in physical order. Each stage matches by name/id so the
# strip shows the user's REAL devices (worst status of everything matched).
_CHAIN = [
    ("Modem", ("modem",)),
    ("Router", ("router", "gateway")),
    ("Switch", ("switch",)),
    ("Pi-hole", ("pi-hole", "pihole")),
]


def _render_network_strip(checks: list[HealthCheck]) -> str:
    active = [c for c in checks]
    consumed: set[str] = set()
    chain_nodes = []
    for label, needles in _CHAIN:
        matched = [c for c in active
                   if any(n in c.name.lower() or n in c.id.lower() for n in needles)]
        if not matched:
            continue
        consumed.update(c.id for c in matched)
        worst = overall_of(matched)
        ink = _INK.get(worst, _INK["unknown"])
        summary = "; ".join(c.summary for c in matched[:2])
        chain_nodes.append(
            f'<div class="net-node" title="{html.escape(summary)}">'
            f'<div class="net-box" style="--nb:{ink["solid"]}"></div>'
            f'<div class="net-name">{label}</div></div>'
        )
    # Hosts: the Infrastructure group's remaining targets as sentinel cards.
    hosts = [c for c in active
             if effective_group(c) == "Infrastructure" and c.id not in consumed]
    host_cards = []
    for check in hosts[:6]:
        ink = _INK.get(check.status, _INK["unknown"])
        ack = ' <span class="chip" style="--chbg:rgba(125,138,160,.14);--chtx:var(--unk-t)">ACK</span>' if check.acknowledged else ""
        host_cards.append(
            f'<div class="host-card"><div class="host-top">'
            f'<span class="host-name">{html.escape(check.name[:30])}</span>'
            f'<span class="host-dot" style="--gd:{ink["solid"]}"></span></div>'
            f'<div class="host-sub">{html.escape(check.summary[:60])}{ack}</div></div>'
        )
    if not chain_nodes and not host_cards:
        return ""
    strip = '<div class="net-node"><div class="net-wan">WAN</div><div class="net-name">Internet</div></div>'
    for node in chain_nodes:
        strip += '<div class="net-link"></div>' + node
    grid = f'<div class="net-link"></div><div class="host-grid">{"".join(host_cards)}</div>' if host_cards else ""
    more = f'<div class="pnote" style="margin:8px 0 0">+ {len(hosts) - 6} more in Infrastructure below</div>' if len(hosts) > 6 else ""
    return (f'<div class="panel"><h2 class="ptitle">Network</h2>'
            f'<div class="net">{strip}{grid}</div>{more}</div>')


def _render_changes(diff: ScanDiff) -> str:
    rows = []

    def row(arrow: str, color: str, text: str, meta: str) -> None:
        rows.append(
            f'<div class="chg"><span class="arrow" style="color:{color}">{arrow}</span>'
            f'<span>{text}</span><span class="meta">{meta}</span></div>'
        )

    for change in diff.regressions:
        ink = _INK.get(change.get("current_status", "critical"), _INK["critical"])
        row("▼", ink["tx"], f'<b>{html.escape(change["name"])}</b>: {change["previous_status"]} → {change["current_status"]}',
            html.escape(str(change.get("summary", "")))[:80])
    for change in diff.improvements:
        row("▲", "var(--ok-t)", f'<b>{html.escape(change["name"])}</b>: {change["previous_status"]} → {change["current_status"]}', "")
    for check in diff.new_checks:
        ink = _INK.get(check.get("status", "ok"), _INK["ok"])
        row("+", ink["tx"], f'<b>{html.escape(check["name"])}</b>: new check, currently {check["status"]}', "")
    for check in diff.removed_checks:
        row("−", "var(--dim)", f'<b>{html.escape(check["name"])}</b>: no longer checked', "")
    if not rows:
        rows.append('<div class="chg"><span class="arrow" style="color:var(--dim)">·</span>'
                    '<span style="color:var(--dim)">Nothing changed since the previous scan.</span></div>')
    return f'<div class="panel"><h2 class="ptitle">What changed</h2><div class="changes">{"".join(rows)}</div></div>'


def _render_counts(counts: dict[str, int], acked: int) -> str:
    tiles = []
    for status in STATUS_ORDER:
        ink = _INK[status]
        tiles.append(
            f'<div class="count" style="--cbg:{ink["bg"]};--ctx:{ink["tx"]}">'
            f'<b>{counts.get(status, 0)}</b><span>{STATUS_META[status][1].upper()}</span></div>'
        )
    if acked:
        tiles.append(f'<div class="count"><b>{acked}</b><span>ACKNOWLEDGED</span></div>')
    return f'<div class="counts">{"".join(tiles)}</div>'


def _render_briefing(narrative: str) -> str:
    paragraphs = "".join(f"<p>{html.escape(p.strip())}</p>" for p in narrative.split("\n\n") if p.strip())
    return f'<div class="panel briefing"><h2 class="ptitle">Briefing</h2>{paragraphs}</div>'


def _render_check_row(check: HealthCheck) -> str:
    ink = _INK.get(check.status, _INK["unknown"])
    if check.acknowledged:
        chip_label, chip_bg, chip_tx = "ACK", "rgba(125,138,160,.14)", "var(--unk-t)"
    else:
        chip_label, chip_bg, chip_tx = ink["chip"], ink["bg"], ink["tx"]
    name_col = ink["tx"] if check.status != "ok" and not check.acknowledged else "var(--t3)"
    parts = [
        f'<div class="grow"><div class="gdot" style="--gd:{ink["solid"]};'
        f'{"opacity:.45;" if check.acknowledged else ""}"></div><div class="body">',
        f'<div class="top"><div class="name" style="color:{name_col}">{html.escape(check.name)}</div>',
        f'<div class="chip" style="--chbg:{chip_bg};--chtx:{chip_tx}">{chip_label}</div></div>',
        f'<div class="summary">{html.escape(check.summary)}</div>',
    ]
    if check.status != "ok" and not check.acknowledged and check.recommended_action:
        parts.append(f'<div class="action">→ {html.escape(check.recommended_action)}</div>')
    if check.acknowledged and check.ack_note:
        parts.append(f'<div class="acknote">{html.escape(check.ack_note)}</div>')
    if check.evidence and check.status != "ok":
        evidence = html.escape(json.dumps(check.evidence, indent=2, default=str)[:2000])
        parts.append(f"<details><summary>evidence</summary><pre>{evidence}</pre></details>")
    parts.append("</div></div>")
    return "".join(parts)


def _render_groups(checks: list[HealthCheck]) -> str:
    grouped: dict[str, list[HealthCheck]] = {}
    for check in checks:
        grouped.setdefault(effective_group(check), []).append(check)

    def group_key(item: tuple[str, list[HealthCheck]]) -> tuple[int, str]:
        name, members = item
        worst = min((_SEVERITY.get(c.status, 3) for c in members if not c.acknowledged), default=3)
        return (worst, name.lower())

    cards = []
    for name, members in sorted(grouped.items(), key=group_key):
        members = sorted(members, key=lambda c: (_SEVERITY.get(c.status, 3), c.acknowledged, c.name.lower()))
        ok_count = sum(1 for c in members if c.status == "ok" or c.acknowledged)
        worst = overall_of(members)
        tally_color = _INK[worst]["tx"] if worst != "ok" else "var(--ok-t)"
        rows = "".join(_render_check_row(c) for c in members)
        cards.append(
            f'<div class="panel gcard" data-group="{html.escape(name)}">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px">'
            f'<h2 class="ptitle-lg">{html.escape(name)}</h2>'
            f'<span class="gtally" style="color:{tally_color}">{ok_count}/{len(members)} OK</span></div>'
            f"{rows}</div>"
        )
    return f'<div class="groups">{"".join(cards)}</div>'


def _render_history(history: list[tuple[int, str, dict[str, Any]]], current_id: int) -> str:
    if not history:
        return ""
    links = []
    for scan_id, created_at, snapshot in history[:20]:
        overall = overall_of(checks_from_snapshot(snapshot))
        ink = _INK.get(overall, _INK["unknown"])
        label = f"#{scan_id}"
        if scan_id == current_id:
            links.append(f'<span class="cur" style="color:{ink["tx"]}">{label}</span>')
        else:
            links.append(f'<a href="/scan/{scan_id}" title="{_fmt_time(created_at)}" '
                         f'style="color:{ink["tx"]}">{label}</a>')
    return (f'<div class="panel"><h2 class="ptitle">Scan history</h2>'
            f'<div class="histrip">{"".join(links)}</div></div>')


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
    counts = _counts(checks)
    acked = sum(1 for c in checks if c.acknowledged)
    narrative = snapshot.get("narrative") or ""
    app_name = html.escape(str(snapshot.get("app", "Homelab Guardian")))
    brand = brand or {}

    meta = (f"last scan {_ago(created_at)} ago · Scan #{scan_id} · {len(checks)} checks · read-only"
            + (f" · refreshes every {refresh_seconds}s" if refresh_seconds else ""))
    hero = _hero(brand, overall=overall, msg=_HERO_MSG.get(overall, ""), meta=meta,
                 active="/", repairs_pending=repairs_pending)
    deck = "".join([
        _render_network_strip(checks),
        f'<div class="duo">{_render_changes(diff)}{_render_counts(counts, acked)}</div>',
        _render_briefing(narrative) if narrative else "",
        _render_groups(checks),
        _render_history(history, scan_id),
    ])
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""
    label = STATUS_META.get(overall, ("", overall))[1]
    return _shell(f"{app_name} — {label}", hero, deck, refresh=refresh)


def render_empty_page() -> str:
    hero = _hero(brand_assets(), overall="unknown", msg="No scans yet.",
                 meta="waiting for the first scan", active="/", repairs_pending=None)
    deck = ('<div class="panel"><h2 class="ptitle">No scans yet</h2>'
            "<p>Run <code>guardian --config config.yaml</code> to produce the first scan, "
            "or start the server with <code>--interval</code> to scan continuously.</p></div>")
    return _shell("Homelab Guardian", hero, deck)


# --- settings ---------------------------------------------------------------


def _render_setting_inputs(settings: list[dict[str, Any]], editable: bool) -> str:
    if not settings:
        return ""
    disabled = "" if editable else " disabled"
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in settings:
        groups.setdefault(entry["group"], []).append(entry)
    parts = ['<div class="panel"><h2 class="ptitle-lg" style="margin-bottom:8px">Thresholds &amp; timing</h2>',
             '<div class="pnote">Numeric limits for the enabled checks. Out-of-range values reject the whole save.</div>']
    for group, entries in groups.items():
        parts.append(f'<div class="pnote" style="margin-top:12px;color:var(--mut2)">{html.escape(group)}</div>')
        for entry in entries:
            ctx = f'<span style="font-family:var(--mono);color:var(--dim)">{html.escape(entry["context"])}</span> · ' if entry.get("context") else ""
            step = "1" if entry["kind"] == "int" else "any"
            parts.append(
                f'<label class="setrow"><span class="sname">{html.escape(entry["label"])}</span>'
                f'<span class="sdesc">{ctx}{html.escape(entry["unit"])} · allowed {entry["min"]:g}–{entry["max"]:g}</span>'
                f'<input class="num" type="number" name="setting:{html.escape(entry["token"])}" '
                f'value="{entry["value"]:g}" min="{entry["min"]:g}" max="{entry["max"]:g}" step="{step}"{disabled}>'
                "</label>"
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
    hero_ctx: dict[str, Any] | None = None,
    posture: list[dict[str, Any]] | None = None,
) -> str:
    disabled = "" if editable else " disabled"
    rows = []
    for c in collectors:
        checked = " checked" if c["enabled"] else ""
        rows.append(
            f'<div class="setrow"><span class="sname">{html.escape(c["label"])}</span>'
            f'<span class="sdesc">{html.escape(c.get("description") or "")}</span>'
            f'<label class="switch"><input type="checkbox" name="collector:{html.escape(c["name"])}"{checked}{disabled}>'
            '<span class="track"></span><span class="knob"></span></label></div>'
        )
    collectors_panel = (
        '<div class="panel"><h2 class="ptitle-lg" style="margin-bottom:8px">Collectors</h2>'
        '<div class="pnote">Read-only against your infrastructure. Every one optional; every one degrades gracefully.</div>'
        + "".join(rows) + "</div>"
    )
    thresholds_panel = _render_setting_inputs(settings or [], editable)

    posture_rows = []
    for p in posture or []:
        on = p.get("on")
        pill = ('<span class="postpill" style="background:rgba(76,181,114,.14);color:var(--ok-t)">ON</span>'
                if on else '<span class="postpill" style="background:rgba(125,138,160,.14);color:var(--unk-t)">OFF</span>')
        posture_rows.append(
            f'<div class="setrow"><span style="flex:1"><span class="sname" style="width:auto;display:block">{html.escape(p["name"])}</span>'
            f'<span class="sdesc" style="display:block;margin-top:2px">{html.escape(p["desc"])}</span></span>{pill}</div>'
        )
    posture_panel = (
        '<div class="panel"><h2 class="ptitle-lg" style="margin-bottom:8px">Integrations &amp; repair</h2>'
        '<div class="pnote">Security posture is read-only here — armed and disarmed only in config.yaml.</div>'
        + "".join(posture_rows) + "</div>"
    ) if posture_rows else ""

    if saved:
        flash = '<div class="banner-flash ok">Saved. Changes take effect on the next scan.</div>'
    elif error:
        flash = f'<div class="banner-flash err">Could not save: {html.escape(error)}</div>'
    else:
        flash = ""

    if editable:
        who = f"Signed in as <b>{html.escape(identity.user)}</b>." if identity else ""
        logout = ' · <a href="/auth/logout">Sign out</a>' if auth_mode == "oidc" else ""
        left = (
            f'<form method="post" action="/settings">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf_token)}">'
            f'<div style="display:flex;flex-direction:column;gap:22px">{collectors_panel}{thresholds_panel}'
            f'<div class="savebar"><button class="btn-go" type="submit">Save</button>'
            f'<span class="notice">{who}{logout}</span></div></div></form>'
        )
    else:
        left = (
            '<div class="banner-flash err">Authentication is off — settings are read-only here. '
            "Set <code>web.auth.mode</code> in config.yaml (basic / forward_auth / oidc), then reload.</div>"
            f'<div style="display:flex;flex-direction:column;gap:22px;margin-top:14px">{collectors_panel}{thresholds_panel}</div>'
        )

    right = (
        f'<div style="display:flex;flex-direction:column;gap:22px">{posture_panel}'
        '<div class="warncard">Destructive actions (anything that deletes) always require a human — '
        "auto-approve is ignored for them, by construction.</div></div>"
    )
    deck = f"{flash}<div class=\"setgrid\"><div>{left}</div>{right}</div>"

    ctx = hero_ctx or {}
    hero = _hero(brand_assets(), overall=ctx.get("overall", "unknown"),
                 msg=ctx.get("msg", "Collector and threshold controls."),
                 meta=ctx.get("meta", "settings · saved straight into config.yaml"),
                 active="/settings", repairs_pending=ctx.get("repairs_pending"))
    return _shell("Homelab Guardian — Settings", hero, deck)


# --- repairs ----------------------------------------------------------------

_PROP_INK = {
    "proposed": ("PENDING", "rgba(217,161,59,.14)", "var(--warn-t)"),
    "approved": ("APPROVED", "rgba(76,181,114,.14)", "var(--ok-t)"),
    "denied": ("DENIED", "rgba(217,88,74,.14)", "var(--crit-t)"),
    "executed": ("EXECUTED", "rgba(76,181,114,.14)", "var(--ok-t)"),
    "failed": ("FAILED", "rgba(217,88,74,.14)", "var(--crit-t)"),
    "running": ("RUNNING", "rgba(99,164,216,.14)", "var(--acc)"),
}


def _render_proposal(p: dict[str, Any], *, editable: bool, csrf_token: str) -> str:
    plan_raw = p.get("plan_json")
    plan: dict[str, Any] = plan_raw if isinstance(plan_raw, dict) else {}
    argv = " ".join(p.get("argv") or plan.get("argv") or [])
    status = str(p.get("status", "proposed"))
    chip_label, chip_bg, chip_tx = _PROP_INK.get(status, ("?", "rgba(125,138,160,.14)", "var(--unk-t)"))
    action = html.escape(str(p.get("action", "")).replace("_", " "))
    target = html.escape(str(p.get("check_id", "")))

    grid = []

    def kv(k: str, v: str) -> None:
        grid.append(f'<div class="k">{k}</div><div class="v">{v}</div>')

    if plan.get("blast_radius"):
        kv("Blast radius", html.escape(str(plan["blast_radius"])))
    if plan.get("reversible"):
        kv("Reversible", html.escape(str(plan["reversible"])))
    if argv:
        kv("Exact argv", f"<code>{html.escape(argv)}</code>")
    if plan.get("risk"):
        kv("Risk tier", html.escape(str(plan["risk"])))
    if plan.get("preview"):
        preview = ", ".join(f"{k}={v}" for k, v in plan["preview"].items())
        kv("Preview", html.escape(preview[:300]))
    verify = p.get("verify_json") if isinstance(p.get("verify_json"), dict) else None
    if verify and p.get("executed_at"):
        kv("Verify", f'<b style="color:{_INK.get(str(verify.get("status")), _INK["unknown"])["tx"]}">'
                     f'{html.escape(str(verify.get("status")))}</b> — {html.escape(str(verify.get("summary", "")))}')

    meta = f'Proposed by {html.escape(str(p.get("proposed_by") or "—"))} · {html.escape(str(p.get("proposed_at") or ""))}'
    meta += (f' · proposal #{p.get("id")} · <code>{html.escape(str(p.get("action", "")))}</code>'
             f" · applies to <code>{target}</code>")
    if p.get("approved_by"):
        verb = "denied" if status == "denied" else "approved"
        meta += f' · {verb} by {html.escape(str(p["approved_by"]))}'

    if status == "proposed":
        if editable:
            side = (
                f'<form method="post" action="/repairs" style="display:flex;flex-direction:column;gap:10px">'
                f'<input type="hidden" name="csrf" value="{html.escape(csrf_token)}">'
                f'<input type="hidden" name="proposal_id" value="{p.get("id")}">'
                '<button class="btn-go" name="decision" value="approve" type="submit">Approve</button>'
                '<button class="btn-no" name="decision" value="deny" type="submit">Deny</button>'
                '<div class="notice" style="text-align:center">Approval is human-only. The agent can propose — never authorize.</div>'
                "</form>"
            )
        else:
            side = '<div class="notice">Enable <code>web.auth</code> to approve or deny here.</div>'
    elif status == "approved":
        side = (f'<div style="color:var(--ok-t);font-size:13px">Approved — run '
                f"<code>guardian repair execute {p.get('id')}</code> (or the agent will). Single-use, expires.</div>")
    elif status == "denied":
        side = '<div class="notice">Denied — proposal closed and recorded in the audit log.</div>'
    else:
        side = f'<div class="notice">{chip_label.lower()} — see the audit log below.</div>'

    return (
        f'<div class="prop"><div>'
        f'<div class="prop-title">{action.capitalize()} <code>{target.split("_")[-1]}</code>'
        f'<span class="pstat" style="--chbg:{chip_bg};--chtx:{chip_tx}">{chip_label}</span></div>'
        f'<div class="prop-meta">{meta}</div>'
        f'<div class="prop-grid">{"".join(grid)}</div>'
        f'</div><div class="prop-side">{side}</div></div>'
    )


def render_repairs_page(
    proposals: list[dict[str, Any]],
    *,
    editable: bool,
    csrf_token: str,
    repair_enabled: bool = True,
    notice: str | None = None,
    error: str | None = None,
    hero_ctx: dict[str, Any] | None = None,
) -> str:
    if not repair_enabled:
        deck = ('<div class="prop-empty">Repairs are disabled. '
                "Set <code>repair.enabled: true</code> in config.yaml to use approval-gated repairs (see docs/repair.md).</div>")
    else:
        flash = ""
        if notice:
            flash = f'<div class="banner-flash ok">{html.escape(notice)}</div>'
        elif error:
            flash = f'<div class="banner-flash err">{html.escape(error)}</div>'
        pending = [p for p in proposals if p.get("status") == "proposed"]
        done = [p for p in proposals if p.get("status") != "proposed"]
        parts = [flash, '<h2 class="ptitle-lg">Pending proposals</h2>']
        if pending:
            parts += [_render_proposal(p, editable=editable, csrf_token=csrf_token) for p in pending]
        else:
            parts.append('<div class="prop-empty">No pending proposals. Guardian only proposes whitelisted, '
                         "parameterized actions — never raw shell.</div>")
        if done:
            parts.append('<h2 class="ptitle-lg" style="margin-top:6px">Audit log</h2>')
            rows = []
            for p in done[:50]:
                _, chip_bg, chip_tx = _PROP_INK.get(str(p.get("status")), ("?", "", "var(--unk-t)"))
                rows.append(
                    f'<div class="audit-row"><div class="when">{html.escape(str(p.get("proposed_at") or ""))[:16]}</div>'
                    f'<div class="act">{html.escape(str(p.get("action", "")))}</div>'
                    f'<div class="tgt">{html.escape(str(p.get("check_id", "")))}</div>'
                    f'<div class="by">{html.escape(str(p.get("approved_by") or p.get("proposed_by") or "—"))}</div>'
                    f'<div class="res" style="color:{chip_tx}">{html.escape(str(p.get("status", "")))}</div></div>'
                )
            parts.append(f'<div class="audit">{"".join(rows)}</div>')
        deck = "".join(parts)

    ctx = hero_ctx or {}
    hero = _hero(brand_assets(), overall=ctx.get("overall", "unknown"),
                 msg=ctx.get("msg", "Approve or deny proposals — execution stays with the CLI and MCP."),
                 meta=ctx.get("meta", "repairs · approval-gated · never raw shell"),
                 active="/repairs", repairs_pending=ctx.get("repairs_pending"))
    return _shell("Homelab Guardian — Repairs", hero, deck)


def _posture_of(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Security-posture switches shown READ-ONLY on the settings page — arming
    and disarming these stays in config.yaml, never on a network surface."""
    mcp_cfg = config.get("mcp", {}) or {}
    repair_cfg = config.get("repair", {}) or {}
    notif = config.get("notifications", {}) or {}
    auth_mode = ((config.get("web") or {}).get("auth") or {}).get("mode", "none")
    return [
        {"name": "MCP write tools", "on": bool(mcp_cfg.get("allow_writes", False)),
         "desc": "The agent may manage acknowledgments (mcp.allow_writes)."},
        {"name": "Approval-gated repairs", "on": bool(repair_cfg.get("enabled", False)),
         "desc": "Playbooks can be proposed and, after human approval, executed (repair.enabled)."},
        {"name": "Agent notifications", "on": str(notif.get("mode", "direct")).lower() == "agent",
         "desc": "The attached agent is the voice; Telegram stays as the critical fallback."},
        {"name": "Dashboard auth", "on": auth_mode != "none",
         "desc": f"web.auth.mode = {auth_mode}."},
    ]


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

    def _hero_ctx(self, config: dict[str, Any]) -> dict[str, Any]:
        """Live status for the hero band on secondary pages."""
        conn = db.connect(self.database_path)
        try:
            scan = db.load_latest_scan(conn)
            pending = None
            if config.get("repair", {}).get("enabled", False):
                pending = sum(1 for p in db.list_repair_proposals(conn, limit=200) if p["status"] == "proposed")
        finally:
            conn.close()
        if scan is None:
            return {"overall": "unknown", "msg": "No scans yet.",
                    "meta": "waiting for the first scan", "repairs_pending": pending}
        checks = checks_from_snapshot(scan[2])
        overall = overall_of(checks)
        return {"overall": overall, "msg": _HERO_MSG.get(overall, ""),
                "meta": f"last scan {_ago(scan[1])} ago · Scan #{scan[0]} · {len(checks)} checks · read-only",
                "repairs_pending": pending}

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
            hero_ctx=self._hero_ctx(config),
            posture=_posture_of(config),
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
            hero_ctx=self._hero_ctx(config),
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
