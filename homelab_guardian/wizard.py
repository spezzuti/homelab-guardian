from __future__ import annotations

import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

# `guardian init` exists so that a first-time homelabber never has to write
# YAML by hand: answer a few questions, optionally let Guardian look around
# the local network with plain TCP connect probes (no payloads, nothing
# written to any device), and get a working config.yaml plus next steps.

PROBE_TIMEOUT = 0.4
SUBNET_PROBE_THREADS = 128

# Recognizable homelab services by port. generous expected_status because
# landing pages commonly redirect or demand auth — reachability is the check.
KNOWN_SERVICES: list[dict[str, Any]] = [
    {"port": 8123, "label": "Home Assistant", "scheme": "http"},
    {"port": 8006, "label": "Proxmox VE", "scheme": "https"},
    {"port": 9443, "label": "Portainer", "scheme": "https"},
    {"port": 9000, "label": "Portainer (or other web service)", "scheme": "http"},
    {"port": 32400, "label": "Plex", "scheme": "http"},
    {"port": 8096, "label": "Jellyfin", "scheme": "http"},
    {"port": 81, "label": "Nginx Proxy Manager", "scheme": "http"},
    {"port": 3001, "label": "Uptime Kuma", "scheme": "http"},
    {"port": 5000, "label": "Synology DSM", "scheme": "http"},
    {"port": 5001, "label": "Synology DSM (HTTPS)", "scheme": "https"},
    {"port": 8443, "label": "UniFi (or other admin UI)", "scheme": "https"},
    {"port": 19999, "label": "Netdata", "scheme": "http"},
    {"port": 8080, "label": "Web service (port 8080)", "scheme": "http"},
    {"port": 53, "label": "DNS server (Pi-hole/AdGuard/router)", "scheme": "tcp"},
]

# Google Cast devices (Chromecast, Google/Nest speakers) answer on these
# generic web ports and would show up as bogus "Portainer/UniFi" findings.
# They are fingerprinted by their Cast control port and filtered out.
CAST_FINGERPRINT_PORT = 8009
CAST_AMBIGUOUS_PORTS = {8080, 8443, 9000}


@dataclass(slots=True)
class DiscoveredService:
    ip: str
    port: int
    label: str
    scheme: str
    hostname: str | None = None

    @property
    def display_name(self) -> str:
        where = self.hostname or self.ip
        return f"{self.label} at {where}"


def local_ip() -> str | None:
    """Best-effort local LAN IP via a UDP socket that never sends a packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("198.51.100.1", 80))  # TEST-NET-2, nothing is sent
            return probe.getsockname()[0]
    except OSError:
        return None


def subnet_hosts(ip: str) -> list[str]:
    """All other hosts in the /24 around ip."""
    prefix = ip.rsplit(".", 1)[0]
    return [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != ip]


def _port_open(ip: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _reverse_name(ip: str) -> str | None:
    try:
        name = socket.gethostbyaddr(ip)[0]
        return name if name and name != ip else None
    except OSError:
        return None


def discover(
    hosts: list[str],
    services: list[dict[str, Any]] | None = None,
    port_checker: Callable[[str, int], bool] = _port_open,
    resolve_names: bool = True,
) -> list[DiscoveredService]:
    """TCP-connect probe every host/known-port pair. Read-only: a connect
    is opened and immediately closed, no bytes are sent to the service."""
    services = services if services is not None else KNOWN_SERVICES
    pairs = [(host, service) for host in hosts for service in services]

    found: list[DiscoveredService] = []
    with ThreadPoolExecutor(max_workers=SUBNET_PROBE_THREADS) as pool:
        results = pool.map(lambda pair: port_checker(pair[0], pair[1]["port"]), pairs)
        for (host, service), is_open in zip(pairs, results):
            if is_open:
                found.append(
                    DiscoveredService(ip=host, port=service["port"], label=service["label"], scheme=service["scheme"])
                )

    cast_candidates = {s.ip for s in found if s.port in CAST_AMBIGUOUS_PORTS}
    cast_hosts = {ip for ip in cast_candidates if port_checker(ip, CAST_FINGERPRINT_PORT)}
    found = [s for s in found if not (s.ip in cast_hosts and s.port in CAST_AMBIGUOUS_PORTS)]

    if resolve_names:
        for item in found:
            item.hostname = _reverse_name(item.ip)

    found.sort(key=lambda s: (tuple(int(p) for p in s.ip.split(".")), s.port))
    return found


def _check_id(prefix: str, service: DiscoveredService) -> str:
    return f"{prefix}_{service.ip.replace('.', '_')}_{service.port}"


def build_config(
    discovered: list[DiscoveredService],
    homeassistant: dict[str, Any] | None = None,
    telegram: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
    secrets_provider: str = "env",
    host_checks: bool = False,
    backup_unit: str | None = None,
    mounts: list[str] | None = None,
    mcp: dict[str, Any] | None = None,
    web_auth: dict[str, Any] | None = None,
    repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the config.yaml structure from wizard answers. Pure function:
    no prompts, no filesystem, fully unit-testable."""
    http_checks: list[dict[str, Any]] = []
    tcp_checks: list[dict[str, Any]] = []
    ha_ips = set()

    for service in discovered:
        if service.label == "Home Assistant":
            ha_ips.add(service.ip)
        if service.scheme == "tcp":
            tcp_checks.append(
                {
                    "id": _check_id("tcp", service),
                    "name": service.display_name,
                    "host": service.ip,
                    "port": service.port,
                    "timeout": 3,
                }
            )
            continue
        check: dict[str, Any] = {
            "id": _check_id("http", service),
            "name": service.display_name,
            "url": f"{service.scheme}://{service.ip}:{service.port}",
            "expected_status": [200, 301, 302, 401, 403],
            "timeout": 5,
        }
        if service.scheme == "https":
            check["verify_tls"] = False
        http_checks.append(check)

    config: dict[str, Any] = {
        "app": {
            "name": "Homelab Guardian",
            "report_path": "reports/latest.md",
            "database_path": "data/guardian.sqlite",
        },
        "collectors": {
            "network": {
                "enabled": True,
                "dns_checks": [
                    {"id": "dns_public_sanity", "name": "Public DNS resolution", "hostname": "example.com"}
                ],
                "tcp_checks": tcp_checks,
                "http_checks": http_checks,
            },
            "backups": {"enabled": False, "paths": []},
            # empty paths = watch the system drive Guardian runs on
            "disks": {"enabled": True, "paths": []},
        },
    }

    if host_checks:
        # The host-hardening collectors are read-only and need no configuration
        # when Guardian runs on the Linux host it's watching — enable the four
        # zero-config ones. backup_health needs a target, so it's added only
        # when the user names a backup unit.
        config["collectors"]["firewall"] = {"enabled": True}
        config["collectors"]["exposed_services"] = {"enabled": True}
        config["collectors"]["ssh"] = {"enabled": True}
        config["collectors"]["updates"] = {"enabled": True}

    if backup_unit:
        config["collectors"]["backup_health"] = {
            "enabled": True,
            "repos": [{"id": "backup_job", "name": "Backup job", "tool": "systemd", "unit": backup_unit}],
        }

    if secrets_provider != "env":
        config["secrets"] = {"provider": secrets_provider}

    ha = dict(homeassistant or {})
    if not ha.get("url") and ha_ips:
        ha["url"] = f"http://{sorted(ha_ips)[0]}:8123"
    if ha.get("url"):
        config["collectors"]["homeassistant"] = {
            "enabled": True,
            "url": ha["url"],
            "token_env": ha.get("token_env", "HOMEASSISTANT_TOKEN"),
        }

    if telegram:
        config["notifications"] = {
            "telegram": {
                "enabled": True,
                "bot_token_env": telegram.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
                "chat_id_env": telegram.get("chat_id_env", "TELEGRAM_CHAT_ID"),
                "send_on": telegram.get("send_on", "changes"),
            }
        }

    if ai:
        config["ai"] = {
            "enabled": True,
            "base_url": ai["base_url"],
            "model": ai["model"],
            "api_key_env": ai.get("api_key_env", "GUARDIAN_AI_API_KEY"),
        }

    if mounts:
        config["collectors"]["mounts"] = {
            "enabled": True,
            "mounts": [{"path": p} for p in mounts],
        }

    # MCP: let an agent read Guardian's verified state. Writes stay off by
    # default (read tools only). A remote agent needs the HTTP transport, which
    # refuses to start without a bearer token, so we wire the token env name.
    if mcp is not None:
        mcp_cfg: dict[str, Any] = {"allow_writes": bool(mcp.get("allow_writes", False))}
        if mcp.get("remote"):
            mcp_cfg["http"] = {"token_env": mcp.get("token_env", "GUARDIAN_MCP_TOKEN")}
        config["mcp"] = mcp_cfg

    # Dashboard auth — only the keys the chosen mode needs. The wizard offers
    # the built-in `basic` mode; richer modes (forward_auth, oidc) are config.
    if web_auth:
        config["web"] = {"auth": dict(web_auth)}

    # A single, conservative self-healing repair: restart one named systemd unit
    # on failure. require-approval by default (auto_approve stays off); the unit
    # must be one the user explicitly named, so the allowlist is never broad.
    if repair and repair.get("unit"):
        config["repair"] = {
            "enabled": True,
            "playbooks": {
                "restart_systemd_unit": {
                    "enabled": True,
                    "allowed_units": [repair["unit"]],
                    "auto_approve": False,
                    "max_attempts_per_hour": 3,
                }
            },
        }

    return config


def render_config_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Interactive front-end
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{prompt} ({hint}): ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _collect_env_secret(env_lines: list[str], name: str, description: str) -> None:
    import getpass

    value = getpass.getpass(f"  Paste {description} (input hidden, Enter to skip): ").strip()
    if value:
        env_lines.append(f"{name}={value}")


def run_init(output_path: str = "config.yaml", force: bool = False, discover_network: bool | None = None) -> int:
    output = Path(output_path)
    if output.exists() and not force:
        print(f"{output} already exists. Re-run with --force to overwrite it.")
        return 1

    print()
    print("Homelab Guardian setup")
    print("======================")
    print("A few questions, then a working config.yaml. Everything Guardian")
    print("does is read-only; you can edit the file by hand at any time.")
    print()

    discovered: list[DiscoveredService] = []
    if discover_network is None:
        discover_network = _ask_yes_no(
            "Scan your local network for common homelab services?\n"
            "(read-only TCP connect probes on your own /24 subnet)",
            default=True,
        )
    if discover_network:
        ip = local_ip()
        if ip is None:
            print("Could not determine the local network address; skipping discovery.")
        else:
            print(f"Scanning {ip.rsplit('.', 1)[0]}.0/24 — this takes a few seconds...")
            discovered = discover(subnet_hosts(ip))
            if discovered:
                print(f"Found {len(discovered)} service(s):")
                for service in discovered:
                    print(f"  - {service.display_name} ({service.ip}:{service.port})")
            else:
                print("No known services found. You can add checks by hand later.")
    print()

    env_lines: list[str] = []

    ha: dict[str, Any] = {}
    ha_found = any(s.label == "Home Assistant" for s in discovered)
    if ha_found or _ask_yes_no("Do you run Home Assistant?", default=False):
        if not ha_found:
            ha["url"] = _ask("Home Assistant URL", "http://homeassistant.local:8123")
        print("  Guardian reads Home Assistant with a long-lived access token:")
        print("  your HA profile picture -> Security -> Long-lived access tokens -> Create.")
        _collect_env_secret(env_lines, "HOMEASSISTANT_TOKEN", "the Home Assistant token")
        ha["token_env"] = "HOMEASSISTANT_TOKEN"
    print()

    telegram: dict[str, Any] | None = None
    if _ask_yes_no("Send scan results to Telegram?", default=False):
        print("  Create a bot with @BotFather to get a token. To find your chat id,")
        print("  message @userinfobot, then send your new bot any message once.")
        _collect_env_secret(env_lines, "TELEGRAM_BOT_TOKEN", "the Telegram bot token")
        chat_id = _ask("  Your Telegram chat id")
        if chat_id:
            env_lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
        telegram = {"send_on": "changes"}
    print()

    ai: dict[str, Any] | None = None
    if _ask_yes_no("Add a plain-English AI briefing to reports? (bring your own model)", default=False):
        choice = _ask("  Provider — openrouter, ollama, or custom base URL", "openrouter")
        if choice.lower() == "openrouter":
            ai = {"base_url": "https://openrouter.ai/api/v1", "model": _ask("  Model", "anthropic/claude-haiku-4.5")}
            _collect_env_secret(env_lines, "GUARDIAN_AI_API_KEY", "your OpenRouter API key")
        elif choice.lower() == "ollama":
            ai = {"base_url": _ask("  Ollama URL", "http://localhost:11434/v1"), "model": _ask("  Model", "qwen3:14b")}
        else:
            ai = {"base_url": choice, "model": _ask("  Model name")}
            _collect_env_secret(env_lines, "GUARDIAN_AI_API_KEY", "the API key (skip if none needed)")
    print()

    host_checks = False
    backup_unit: str | None = None
    if sys.platform.startswith("linux"):
        if _ask_yes_no(
            "Is Guardian running on the Linux host you want to keep an eye on?\n"
            "(enables read-only host checks: firewall, SSH config, pending updates, exposed services)",
            default=True,
        ):
            host_checks = True
            if _ask_yes_no("  Watch a backup job's health via its systemd unit?", default=False):
                unit = _ask("  Backup unit name (e.g. restic-backup.service)")
                backup_unit = unit or None
    print()

    mounts: list[str] | None = None
    if _ask_yes_no("Watch any NAS/NFS/CIFS mountpoints? (a dropped share is a silent failure)", default=False):
        raw = _ask("  Mountpoint paths, comma-separated (e.g. /mnt/nas, /mnt/media)")
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        mounts = paths or None
    print()

    # --- v0.3 actuator surface: agent (MCP), dashboard auth, self-healing ---
    mcp: dict[str, Any] | None = None
    mcp_token: str | None = None
    if _ask_yes_no("Attach an AI agent (Claude, a local agent, ...) over MCP?", default=False):
        print("  Read-only by default — the agent reads Guardian's verified state.")
        remote = _ask_yes_no("  Is the agent on a DIFFERENT machine (needs the HTTP transport)?", default=False)
        mcp = {"allow_writes": False, "remote": remote}
        if remote:
            import secrets as _secrets
            mcp_token = _secrets.token_urlsafe(32)
            mcp["token_env"] = "GUARDIAN_MCP_TOKEN"
            env_lines.append(f"GUARDIAN_MCP_TOKEN={mcp_token}")
            print("  Generated a bearer token (saved to .env as GUARDIAN_MCP_TOKEN).")
    print()

    web_auth: dict[str, Any] | None = None
    if _ask_yes_no("Password-protect the web dashboard? (recommended if you'll expose it)", default=False):
        username = _ask("  Username", "admin")
        _collect_env_secret(env_lines, "GUARDIAN_WEB_PASSWORD", "a dashboard password (you choose it)")
        web_auth = {"mode": "basic", "username": username, "password_env": "GUARDIAN_WEB_PASSWORD"}
        print("  (For SSO via Authentik/Authelia/OIDC instead, see docs/auth.md.)")
    print()

    repair: dict[str, Any] | None = None
    if sys.platform.startswith("linux") and _ask_yes_no(
        "Enable ONE guarded self-healing repair? (restart a named systemd unit when it fails)",
        default=False,
    ):
        print("  Guardian proposes the restart; nothing runs until a human approves")
        print("  (auto-approve stays off). Only the unit you name here is ever eligible.")
        unit = _ask("  Unit to allow restarting (e.g. marcus-backup.service)")
        if unit:
            repair = {"unit": unit}
            print(f"  Grant Guardian's user a scoped sudoers line for just this, e.g.:")
            print(f"    <user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart {unit}")
            print("  (user-bus units need no sudo). See docs/repair.md for the privilege model.")
    print()

    secrets_provider = "env"
    if _ask_yes_no("Use Bitwarden Secrets Manager instead of a local .env file? (advanced)", default=False):
        secrets_provider = "bitwarden"
        print("  Set BWS_ACCESS_TOKEN in your environment; secret keys in Bitwarden")
        print("  should match the names Guardian uses (HOMEASSISTANT_TOKEN, ...).")

    config = build_config(
        discovered, homeassistant=ha or None, telegram=telegram, ai=ai,
        secrets_provider=secrets_provider, host_checks=host_checks, backup_unit=backup_unit,
    )
    output.write_text(render_config_yaml(config), encoding="utf-8")
    print(f"\nWrote {output}")

    if env_lines and secrets_provider == "env":
        env_path = output.parent / ".env"
        existing = env_path.read_text(encoding="utf-8").rstrip() + "\n" if env_path.exists() else ""
        env_path.write_text(existing + "\n".join(env_lines) + "\n", encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass
        print(f"Wrote {env_path} (keep it private; it is gitignored)")

    if mcp is not None:
        # Print the ready-to-paste MCP client config so attaching an agent is a
        # copy, not a hunt through docs.
        import json as _json
        cfg_path = str(output.resolve())
        if mcp.get("remote"):
            print()
            print("Attach a REMOTE agent: run the HTTP server on this host —")
            print(f"  guardian mcp --http --config {output}")
            print("  then point the agent at http://<this-host>:8675 with the bearer token")
            print("  from .env (GUARDIAN_MCP_TOKEN). See docs/mcp.md.")
        else:
            snippet = {"mcpServers": {"homelab-guardian": {
                "command": "guardian", "args": ["mcp", "--config", cfg_path]}}}
            print()
            print("Attach a local agent — add this to your MCP client config:")
            print("  " + _json.dumps(snippet, indent=2).replace("\n", "\n  "))

    print()
    print("Next steps:")
    print(f"  1. guardian doctor --config {output}    # preflight check")
    print(f"  2. guardian --config {output}           # first scan")
    print(f"  3. guardian serve --config {output} --interval 900   # web view + recurring scans")
    print("Reports land in reports/latest.md and at http://localhost:8674 while serving.")
    if repair is not None:
        print("Repair is armed but human-gated: `guardian repair list <check>` to see proposals,")
        print("`guardian repair approve <id>` to authorize. Auto-approve stays off. See docs/repair.md.")
    return 0
