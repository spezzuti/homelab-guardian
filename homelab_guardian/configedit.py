from __future__ import annotations

import os
import re as _re
import tempfile
from typing import Any

import yaml

# Minimal, comment-preserving editor for the one operation the dashboard's
# settings page needs in v1: toggling collectors.<name>.enabled. It edits the
# raw YAML text surgically (flip/insert just the `enabled:` line) so the user's
# comments, ordering, and hand-written structure survive a save. config.yaml
# stays the single source of truth — the dashboard edits the file, it does not
# maintain a parallel store.
#
# Richer edits (thresholds, targets) are deliberately out of scope here; they'd
# warrant a comment-preserving round-trip lib (ruamel) and are a later phase.

_FRIENDLY = {
    "ssh": "SSH hardening",
    "homeassistant": "Home Assistant",
    "backup_health": "Backup health",
    "exposed_services": "Exposed services",
    "disks": "Disk space",
    "backups": "Backup freshness",
    "systemd": "systemd services",
    "updates": "System updates",
    "firewall": "Host firewall",
    "network": "Network checks",
    "docker": "Docker",
    "mounts": "Mount health",
}

# One-line descriptions so similar-sounding collectors are unmistakable in the
# settings page. The two backup collectors in particular watch different things:
# `backups` looks at a destination *folder*; `backup_health` watches the backup
# *job* itself. Keep these short — they render under the label.
_DESCRIPTIONS = {
    "ssh": "Checks SSH is hardened (password auth off, key-only).",
    "homeassistant": "Checks Home Assistant is reachable and its entities are healthy.",
    "backup_health": "Checks a backup job — restic snapshot age or a systemd unit.",
    "exposed_services": "Flags services listening on non-loopback addresses.",
    "disks": "Checks free space on watched filesystems.",
    "backups": "Checks a backup folder for recent files.",
    "systemd": "Checks for failed or restart-looping systemd units.",
    "updates": "Checks for pending OS package updates.",
    "firewall": "Checks the host firewall is active and default-deny.",
    "network": "Checks DNS, TCP, HTTP, and TLS reachability targets.",
    "docker": "Checks container health (running / exited / unhealthy).",
    "mounts": "Checks configured NAS/NFS/CIFS mountpoints are actually mounted.",
}


def _friendly(name: str) -> str:
    return _FRIENDLY.get(name, name.replace("_", " ").title())


def _describe(name: str) -> str:
    return _DESCRIPTIONS.get(name, "")


def toggleable_collectors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every collector the dashboard can toggle, with its current enabled state."""
    from homelab_guardian.main import COLLECTORS

    collectors = config.get("collectors", {}) or {}
    out = []
    for name in COLLECTORS:
        cfg = collectors.get(name) or {}
        out.append({"name": name, "label": _friendly(name), "description": _describe(name), "enabled": bool(cfg.get("enabled", False))})
    return out


def _is_key_at_indent(line: str, indent: int, key: str) -> bool:
    if len(line) - len(line.lstrip(" ")) != indent:
        return False
    stripped = line.strip()
    return stripped == f"{key}:" or stripped.startswith(f"{key}:")


def apply_collector_toggles(text: str, desired: dict[str, bool]) -> str:
    """Return `text` with each collectors.<name>.enabled set per `desired`.
    Preserves everything else. Raises ValueError if the result wouldn't parse
    or wouldn't reflect the requested state."""
    if not desired:
        return text
    lines = text.split("\n")

    # Locate the top-level `collectors:` mapping.
    col_idx = None
    for i, line in enumerate(lines):
        if len(line) - len(line.lstrip(" ")) == 0 and line.split("#")[0].rstrip() == "collectors:":
            col_idx = i
            break
    if col_idx is None:
        # No collectors section — append one.
        block: list[str] = []
        for name, enabled in desired.items():
            block += [f"  {name}:", f"    enabled: {'true' if enabled else 'false'}"]
        lines += ["collectors:", *block]
        return _validate("\n".join(lines), desired)

    # End of the collectors block = next non-indented, non-comment, non-blank line.
    end = len(lines)
    for i in range(col_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) == 0:
            end = i
            break

    block = lines[col_idx + 1:end]

    for name, enabled in desired.items():
        value = "true" if enabled else "false"
        header = next((j for j, line in enumerate(block) if _is_key_at_indent(line, 2, name)), None)
        if header is None:
            block.insert(0, f"  {name}:")
            block.insert(1, f"    enabled: {value}")
            continue
        # Sub-block spans until the next indent-2 key.
        sub_end = len(block)
        for j in range(header + 1, len(block)):
            line = block[j]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if len(line) - len(line.lstrip(" ")) <= 2:
                sub_end = j
                break
        enabled_idx = next(
            (j for j in range(header + 1, sub_end) if _is_key_at_indent(block[j], 4, "enabled")),
            None,
        )
        if enabled_idx is not None:
            block[enabled_idx] = f"    enabled: {value}"
        else:
            block.insert(header + 1, f"    enabled: {value}")

    return _validate("\n".join(lines[:col_idx + 1] + block + lines[end:]), desired)


def _validate(new_text: str, desired: dict[str, bool]) -> str:
    parsed = yaml.safe_load(new_text)
    if not isinstance(parsed, dict):
        raise ValueError("edited config is no longer a YAML mapping")
    collectors = parsed.get("collectors") or {}
    for name, enabled in desired.items():
        got = bool((collectors.get(name) or {}).get("enabled", False))
        if got != enabled:
            raise ValueError(f"failed to set collectors.{name}.enabled = {enabled}")
    return new_text


# --- v2: whitelisted numeric edits (thresholds & timing) ---------------------
#
# Same philosophy as the toggles above: surgical text edits so the user's
# comments and structure survive, then a full parse to PROVE the edit landed.
# An edit path is a sequence of steps — a mapping key (str) or a list selector
# (list_key, id_key, id_value) that picks the item whose id_key equals
# id_value. Only paths produced by editable_settings() are ever applied, so
# the dashboard cannot write arbitrary config locations.


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_item_start(line: str) -> bool:
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ")


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """One past the last line of the block opened by the key line at start.
    A `- ` item at the SAME indent as its key still belongs to the block
    (compact YAML list style), so only a non-item line at <= indent ends it."""
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = _indent_of(lines[i])
        if line_indent > indent:
            continue
        if line_indent == indent and _is_item_start(lines[i]):
            continue
        return i
    return len(lines)


def _find_key_line(lines: list[str], key: str, start: int, end: int, indent: int) -> int | None:
    for i in range(start, end):
        if _indent_of(lines[i]) != indent or _is_item_start(lines[i]):
            continue
        head = lines[i].split("#")[0].strip()
        if head == f"{key}:" or head.startswith(f"{key}: "):
            return i
    return None


def _child_indent(lines: list[str], start: int, end: int, parent_indent: int) -> int | None:
    """Indent of the block's mapping children (first non-item content line)."""
    for i in range(start, end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#") or _is_item_start(lines[i]):
            continue
        if _indent_of(lines[i]) > parent_indent:
            return _indent_of(lines[i])
    return None


def _line_key_value(line: str) -> tuple[str, str] | None:
    head = line.split("#")[0].strip()
    if head.startswith("- "):
        head = head[2:].strip()
    if ":" not in head:
        return None
    key, _, value = head.partition(":")
    return key.strip(), value.strip().strip("'\"")


def _select_item(lines: list[str], start: int, end: int, key_indent: int,
                 id_key: str, id_value: str) -> tuple[int, int, int] | None:
    """(item_start, item_end, item_key_indent) of the list item under the key
    block [start, end) whose id_key equals id_value. Handles both compact
    (dash at the key's indent) and indented (dash deeper) list styles."""
    dash_indent = None
    for i in range(start, end):
        if _is_item_start(lines[i]) and _indent_of(lines[i]) >= key_indent:
            dash_indent = _indent_of(lines[i])
            break
    if dash_indent is None:
        return None
    item_starts = [i for i in range(start, end)
                   if _is_item_start(lines[i]) and _indent_of(lines[i]) == dash_indent]
    for pos, item_start in enumerate(item_starts):
        item_end = item_starts[pos + 1] if pos + 1 < len(item_starts) else end
        for i in range(item_start, item_end):
            kv = _line_key_value(lines[i])
            if kv and kv[0] == id_key and kv[1] == str(id_value):
                return item_start, item_end, dash_indent + 2
    return None


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("only numeric settings are editable")
    if isinstance(value, int):
        return str(value)
    return f"{value:g}"


def _replace_scalar_line(line: str, key: str, rendered: str) -> str:
    match = _re.match(r"^(\s*(?:- )?" + _re.escape(key) + r":\s*)([^#\n]*?)(\s*#.*)?$", line)
    if match is None:
        raise ValueError(f"could not rewrite the {key} line")
    return f"{match.group(1)}{rendered}{match.group(3) or ''}"


def set_scalar(text: str, steps: list[Any], value: Any) -> str:
    """Return text with the numeric scalar at `steps` set to value. The scalar
    line is edited in place (trailing comment preserved) or inserted into its
    existing parent block; parent sections are never created."""
    lines = text.split("\n")
    rendered = _format_number(value)
    start, end, indent = 0, len(lines), 0
    anchor = None  # line to insert after when the scalar key is absent

    for step in steps[:-1]:
        if isinstance(step, tuple):
            list_key, id_key, id_value = step
            key_line = _find_key_line(lines, list_key, start, end, indent)
            if key_line is None:
                raise ValueError(f"config has no '{list_key}' section on that path")
            block_close = _block_end(lines, key_line, indent)
            found = _select_item(lines, key_line + 1, block_close, indent, id_key, id_value)
            if found is None:
                raise ValueError(f"no {list_key} item with {id_key}={id_value!r}")
            start, end, indent = found
            anchor = start
        else:
            key_line = _find_key_line(lines, step, start, end, indent)
            if key_line is None:
                raise ValueError(f"config has no '{step}' section on that path")
            end = _block_end(lines, key_line, indent)
            start = key_line + 1
            child = _child_indent(lines, start, end, indent)
            indent = child if child is not None else indent + 2
            anchor = key_line

    scalar_key = steps[-1]
    scalar_line = None
    for i in range(start, end):
        kv = _line_key_value(lines[i])
        at_indent = _indent_of(lines[i]) == indent or (
            _is_item_start(lines[i]) and _indent_of(lines[i]) + 2 == indent
        )
        if kv and kv[0] == scalar_key and at_indent:
            scalar_line = i
            break
    if scalar_line is not None:
        lines[scalar_line] = _replace_scalar_line(lines[scalar_line], scalar_key, rendered)
    else:
        insert_at = (anchor if anchor is not None else start - 1) + 1
        lines.insert(insert_at, f"{' ' * indent}{scalar_key}: {rendered}")
    return "\n".join(lines)


def _walk(parsed: Any, steps: list[Any]) -> Any:
    node = parsed
    for step in steps:
        if isinstance(step, tuple):
            list_key, id_key, id_value = step
            items = (node or {}).get(list_key) or []
            node = next((it for it in items if isinstance(it, dict) and str(it.get(id_key)) == str(id_value)), None)
        else:
            node = (node or {}).get(step) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def apply_setting_edits(text: str, edits: list[tuple[list[Any], Any]]) -> str:
    """Apply whitelisted scalar edits, then parse and PROVE each one landed."""
    for steps, value in edits:
        text = set_scalar(text, steps, value)
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ValueError("edited config is no longer a YAML mapping")
    for steps, value in edits:
        got = _walk(parsed, steps)
        if got is None or float(got) != float(value):
            raise ValueError(f"failed to set {'.'.join(str(s) for s in steps)} = {value}")
    return text


# The whitelist. Static entries are app-level scalars; per-item threshold
# entries are generated from the live config below. `kind` drives parsing
# and rendering; min/max bound what the dashboard will accept.

_STATIC_SETTINGS: list[dict[str, Any]] = [
    {"steps": ["app", "retention_days"], "label": "Snapshot retention",
     "unit": "days", "kind": "float", "min": 1, "max": 3650, "default": 60,
     "group": "Application"},
    {"steps": ["notifications", "telegram", "confirm_scans"], "label": "Confirm scans before alerting",
     "unit": "scans", "kind": "int", "min": 1, "max": 10, "default": 1,
     "group": "Notifications"},
    {"steps": ["notifications", "agent", "ack_timeout_minutes"], "label": "Agent ack fallback deadline",
     "unit": "minutes", "kind": "float", "min": 1, "max": 1440, "default": 10,
     "group": "Notifications"},
]

_ITEM_SETTINGS: list[dict[str, Any]] = [
    {"section": ["collectors", "disks"], "list_key": "paths", "id_keys": ["id", "path"],
     "fields": [("warn_percent", "Warn at", "% used", 50, 99, "float", 85),
                ("critical_percent", "Critical at", "% used", 50, 100, "float", 95)]},
    {"section": ["collectors", "network"], "list_key": "tls_checks", "id_keys": ["id", "host"],
     "fields": [("warn_days", "Warn under", "days left", 1, 365, "float", 14),
                ("critical_days", "Critical under", "days left", 0, 90, "float", 3)]},
    {"section": ["collectors", "backup_health"], "list_key": "repos", "id_keys": ["id", "name", "repo", "unit"],
     "fields": [("max_age_hours", "Warn over", "hours old", 1, 8760, "float", 26),
                ("critical_age_hours", "Critical over", "hours old", 1, 8760, "float", 72)]},
]


def _token(steps: list[Any]) -> str:
    parts = []
    for step in steps:
        if isinstance(step, tuple):
            parts.append(f"{step[0]}[{step[1]}={step[2]}]")
        else:
            parts.append(str(step))
    return ".".join(parts)


def editable_settings(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every numeric setting the dashboard may edit, with its current value.
    Recomputed from the config on BOTH render and save — the save path only
    accepts tokens present in this registry, never a client-supplied path."""
    out: list[dict[str, Any]] = []

    def add(steps: list[Any], spec: dict[str, Any], current: Any, group: str, context: str = "") -> None:
        value = spec.get("default") if current is None else current
        if value is None:
            return
        try:
            value = int(value) if spec["kind"] == "int" else float(value)
        except (TypeError, ValueError):
            return  # unparseable hand-written value: leave it alone entirely
        out.append({
            "token": _token(steps), "steps": steps, "label": spec["label"],
            "unit": spec["unit"], "kind": spec["kind"], "min": spec["min"],
            "max": spec["max"], "value": value, "group": group, "context": context,
        })

    for spec in _STATIC_SETTINGS:
        parent = _walk(config, spec["steps"][:-1])
        if not isinstance(parent, dict):
            continue  # section not configured — nothing to anchor an edit to
        add(spec["steps"], spec, parent.get(spec["steps"][-1]), spec["group"])

    for item_spec in _ITEM_SETTINGS:
        section = _walk(config, item_spec["section"])
        if not isinstance(section, dict) or not section.get("enabled", False):
            continue
        items = section.get(item_spec["list_key"]) or []
        group = _friendly(item_spec["section"][-1])
        for item in items:
            if not isinstance(item, dict):
                continue
            id_key = next((k for k in item_spec["id_keys"] if item.get(k)), None)
            if id_key is None:
                continue
            selector = (item_spec["list_key"], id_key, str(item[id_key]))
            for field, label, unit, lo, hi, kind, default in item_spec["fields"]:
                spec = {"label": label, "unit": unit, "kind": kind,
                        "min": lo, "max": hi, "default": default}
                steps = [*item_spec["section"], selector, field]
                add(steps, spec, item.get(field), group, context=str(item[id_key]))

    return out


def parse_setting_edits(config: dict[str, Any], form_values: dict[str, str]) -> list[tuple[list[Any], Any]]:
    """Validate posted values against the registry; return the edits whose
    value actually changed. Raises ValueError on a non-numeric or out-of-range
    submission (the whole save is rejected — no partial writes)."""
    edits: list[tuple[list[Any], Any]] = []
    for entry in editable_settings(config):
        raw = form_values.get(entry["token"])
        if raw is None or not str(raw).strip():
            continue
        try:
            value = int(str(raw).strip()) if entry["kind"] == "int" else float(str(raw).strip())
        except ValueError:
            raise ValueError(f"{entry['label']}: {raw!r} is not a number") from None
        if not (entry["min"] <= value <= entry["max"]):
            raise ValueError(f"{entry['label']}: {value:g} is outside {entry['min']}–{entry['max']}")
        if value != entry["value"]:
            edits.append((entry["steps"], value))
    return edits


def write_config(path: str, new_text: str) -> None:
    """Atomically replace the config file, keeping a single .bak of the prior
    version. The new text is validated by the caller before this is called."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as src:
                prior = src.read()
            with open(path + ".bak", "w", encoding="utf-8") as bak:
                bak.write(prior)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
