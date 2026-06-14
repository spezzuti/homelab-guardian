from __future__ import annotations

import os
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
}


def _friendly(name: str) -> str:
    return _FRIENDLY.get(name, name.replace("_", " ").title())


def toggleable_collectors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every collector the dashboard can toggle, with its current enabled state."""
    from homelab_guardian.main import COLLECTORS

    collectors = config.get("collectors", {}) or {}
    out = []
    for name in COLLECTORS:
        cfg = collectors.get(name) or {}
        out.append({"name": name, "label": _friendly(name), "enabled": bool(cfg.get("enabled", False))})
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
        header = next((j for j, l in enumerate(block) if _is_key_at_indent(l, 2, name)), None)
        if header is None:
            block.insert(0, f"  {name}:")
            block.insert(1, f"    enabled: {value}")
            continue
        # Sub-block spans until the next indent-2 key.
        sub_end = len(block)
        for j in range(header + 1, len(block)):
            l = block[j]
            if not l.strip() or l.lstrip().startswith("#"):
                continue
            if len(l) - len(l.lstrip(" ")) <= 2:
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
