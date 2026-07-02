from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from homelab_guardian.models import HealthCheck, HealthStatus


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _container_id(container: Any) -> str:
    for attr in ("id", "short_id"):
        try:
            value = getattr(container, attr, None)
        except Exception:
            value = None
        if value:
            return str(value)
    return "unknown"


def _container_name(container: Any) -> str:
    try:
        name = getattr(container, "name", None)
    except Exception:
        name = None
    return str(name) if name else "unknown"


def _exclude_patterns(config: dict[str, Any]) -> list[str]:
    patterns = config.get("exclude_containers", []) or []
    if isinstance(patterns, str):
        return [patterns]
    if not isinstance(patterns, list):
        return []
    return [str(pattern) for pattern in patterns if pattern]


def _is_excluded_container(container_name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(container_name, pattern) for pattern in patterns)


def _short_check_id(container: Any) -> str:
    container_id = _container_id(container)
    if container_id == "unknown":
        return "unknown"
    return container_id[:12]


def _safe_image_name(container: Any, attrs: dict[str, Any]) -> str:
    """Return the best available image identifier without assuming tags exist."""
    try:
        image = getattr(container, "image", None)
    except Exception:
        image = None

    if image is not None:
        try:
            tags = getattr(image, "tags", None)
        except Exception:
            tags = None
        if isinstance(tags, list):
            first_tag = next((tag for tag in tags if tag), None)
            if first_tag:
                return str(first_tag)

    config_image = _as_mapping(attrs.get("Config")).get("Image")
    if config_image:
        return str(config_image)

    if image is not None:
        try:
            short_id = getattr(image, "short_id", None)
        except Exception:
            short_id = None
        if short_id:
            return str(short_id)

    container_short_id = _container_id(container)
    if container_short_id != "unknown":
        return container_short_id

    return "unknown"


def _ports(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    raw_ports = _as_mapping(_as_mapping(attrs.get("NetworkSettings")).get("Ports"))
    ports: list[dict[str, Any]] = []
    for container_port, bindings in sorted(raw_ports.items(), key=lambda item: str(next(iter(item), ""))):
        if not bindings:
            ports.append({"container_port": container_port, "published": []})
            continue
        published: list[dict[str, Any]] = []
        if isinstance(bindings, list):
            for binding in bindings:
                binding_map = _as_mapping(binding)
                published.append(
                    {
                        "host_ip": binding_map.get("HostIp", ""),
                        "host_port": binding_map.get("HostPort", ""),
                    }
                )
        ports.append({"container_port": container_port, "published": published})
    return ports


def _mounts(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    raw_mounts = attrs.get("Mounts") or []
    if not isinstance(raw_mounts, list):
        return mounts
    for mount in raw_mounts:
        mount_map = _as_mapping(mount)
        mounts.append(
            {
                "type": mount_map.get("Type"),
                "name": mount_map.get("Name"),
                "source": mount_map.get("Source"),
                "destination": mount_map.get("Destination"),
                "mode": mount_map.get("Mode"),
                "rw": mount_map.get("RW"),
                "propagation": mount_map.get("Propagation"),
            }
        )
    return mounts


def _compose_labels(labels: dict[str, Any]) -> dict[str, str | None]:
    labels = _as_mapping(labels)
    return {
        "project": labels.get("com.docker.compose.project"),
        "service": labels.get("com.docker.compose.service"),
        "container_number": labels.get("com.docker.compose.container-number"),
        "working_dir": labels.get("com.docker.compose.project.working_dir"),
        "config_files": labels.get("com.docker.compose.project.config_files"),
    }


def _status_for_container(status: str, health: str | None, exit_code: Any = None) -> tuple[HealthStatus, str]:
    if health == "unhealthy" or status in {"restarting", "dead"}:
        return "critical", "Inspect logs and recent compose/image changes before restarting anything."
    if status in {"exited", "created", "paused", "removing"}:
        # An application-error exit (1..127) is a crash, not an intentional stop.
        # Signal exits (>=128, e.g. 137/143 from `docker stop`) stay a warning so
        # a deliberately-stopped container doesn't read as critical.
        if status == "exited" and isinstance(exit_code, int) and 1 <= exit_code <= 127:
            return "critical", f"Container exited with code {exit_code} (crashed, not a clean stop) — inspect its logs before restarting."
        return "warning", "Confirm whether this container is expected to be stopped or paused."
    if status == "running" and health in {None, "healthy", "starting"}:
        if health == "starting":
            return "warning", "Container is running but healthcheck is still starting; check again soon."
        return "ok", "No action required."
    return "unknown", "Review Docker state and recent deployment activity."


def _container_error_check(container: Any, exc: Exception) -> HealthCheck:
    container_id = _container_id(container)
    container_name = _container_name(container)
    return HealthCheck(
        f"docker_container_metadata_error_{container_id[:12] if container_id != 'unknown' else container_name}",
        f"Docker container metadata unreadable: {container_name}",
        "warning",
        "Docker API is reachable, but Guardian could not read metadata for one container.",
        {"container_id": container_id, "container_name": container_name, "error": str(exc)},
        "Inspect this container's Docker metadata on the host with read-only Docker commands such as docker inspect.",
    )


def _container_check(container: Any) -> HealthCheck:
    attrs = _as_mapping(getattr(container, "attrs", {}) or {})
    state = _as_mapping(attrs.get("State"))
    try:
        fallback_status = getattr(container, "status", "unknown")
    except Exception:
        fallback_status = "unknown"
    status = str(state.get("Status") or fallback_status or "unknown")
    health_value = _as_mapping(state.get("Health")).get("Status")
    health = str(health_value) if health_value else None
    restart_count = attrs.get("RestartCount")
    exit_code = state.get("ExitCode")
    labels = _as_mapping(_as_mapping(attrs.get("Config")).get("Labels"))
    name = _container_name(container)
    image = _safe_image_name(container, attrs)
    check_status, action = _status_for_container(status, health, exit_code)
    evidence = {
        "id": _container_id(container),
        "name": name,
        "image": image,
        "status": status,
        "health_status": health,
        "exit_code": exit_code,
        "restart_count": restart_count,
        "ports": _ports(attrs),
        "mounts": _mounts(attrs),
        "compose": _compose_labels(labels),
    }
    health_text = f", health={health}" if health else ""
    restart_text = f", restart_count={restart_count}" if restart_count is not None else ""
    summary = f"{name}: image={image}, status={status}{health_text}{restart_text}."
    return HealthCheck(
        f"docker_container_{_short_check_id(container)}",
        f"Docker container: {name}",
        check_status,
        summary,
        evidence,
        action,
    )


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    try:
        import docker
    except Exception as exc:
        return [
            HealthCheck(
                "docker_sdk_missing",
                "Docker",
                "unknown",
                "Docker collector is enabled, but the Docker SDK could not be imported.",
                {"error": str(exc)},
                "Install dependencies with pip install -r requirements.txt.",
            )
        ]

    socket_url = os.environ.get("DOCKER_HOST") or config.get("socket_url") or "unix:///var/run/docker.sock"
    socket_path = None
    if socket_url.startswith("unix://"):
        raw_socket_path = socket_url.removeprefix("unix://")
        if not raw_socket_path.startswith("/"):
            raw_socket_path = f"/{raw_socket_path}"
        socket_path = Path(raw_socket_path)
    if socket_path and not socket_path.exists():
        return [
            HealthCheck(
                "docker_socket_missing",
                "Docker",
                "unknown",
                "Docker collector is enabled, but /var/run/docker.sock was not found. Run Guardian on a Docker host or mount the Docker socket into the container.",
                {"socket_url": socket_url, "socket_path": str(socket_path), "exists": False},
                "Run Guardian on a Docker host, mount /var/run/docker.sock intentionally, or disable the Docker collector for this machine.",
            )
        ]
    try:
        client = docker.DockerClient(base_url=socket_url)
        client.ping()
        containers = client.containers.list(all=True)
    except Exception as exc:
        return [
            HealthCheck(
                "docker_unavailable",
                "Docker",
                "unknown",
                "Docker collector is enabled, but Docker could not be reached through the configured socket. The likely cause is socket permissions, a missing Docker daemon, or a container without the socket mounted.",
                {"socket_url": socket_url, "socket_path": str(socket_path) if socket_path else None, "error": str(exc)},
                "Check Docker socket path and permissions. Do not mount the Docker socket unless you intentionally accept the risk.",
            )
        ]

    if not containers:
        return [
            HealthCheck(
                "docker_no_containers",
                "Docker containers",
                "unknown",
                "Docker is reachable, but no containers were found.",
                {"socket_url": socket_url, "container_count": 0},
                "If this host should run Docker workloads, check Docker context and socket configuration.",
            )
        ]

    checks: list[HealthCheck] = []
    readable_count = 0
    error_count = 0
    excluded_count = 0
    excluded_patterns = _exclude_patterns(config)
    for container in sorted(containers, key=_container_name):
        container_name = _container_name(container)
        if _is_excluded_container(container_name, excluded_patterns):
            excluded_count += 1
            continue
        try:
            checks.append(_container_check(container))
            readable_count += 1
        except Exception as exc:
            checks.append(_container_error_check(container, exc))
            error_count += 1

    scanned_count = readable_count + error_count
    checks.insert(
        0,
        HealthCheck(
            "docker_inventory_summary",
            "Docker inventory",
            "warning" if error_count else "ok",
            f"Docker API is reachable. Read {readable_count} of {scanned_count} non-excluded container(s). Excluded: {excluded_count}. Metadata errors: {error_count}.",
            {
                "socket_url": socket_url,
                "socket_path": str(socket_path) if socket_path else None,
                "container_count": len(containers),
                "scanned_containers": scanned_count,
                "readable_containers": readable_count,
                "metadata_errors": error_count,
                "excluded_containers": excluded_count,
                "excluded_patterns": excluded_patterns,
            },
            "Review container metadata warnings." if error_count else "No action required.",
        ),
    )
    return checks
