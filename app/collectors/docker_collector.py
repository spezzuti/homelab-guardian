from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import HealthCheck


def _image_name(attrs: dict[str, Any], fallback: str | None) -> str:
    tags = attrs.get("Config", {}).get("Image")
    if tags:
        return str(tags)
    return fallback or "unknown"


def _ports(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    raw_ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
    ports: list[dict[str, Any]] = []
    for container_port, bindings in sorted(raw_ports.items()):
        if bindings is None:
            ports.append({"container_port": container_port, "published": []})
            continue
        ports.append(
            {
                "container_port": container_port,
                "published": [
                    {
                        "host_ip": binding.get("HostIp", ""),
                        "host_port": binding.get("HostPort", ""),
                    }
                    for binding in bindings
                ],
            }
        )
    return ports


def _mounts(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for mount in attrs.get("Mounts") or []:
        mounts.append(
            {
                "type": mount.get("Type"),
                "name": mount.get("Name"),
                "source": mount.get("Source"),
                "destination": mount.get("Destination"),
                "mode": mount.get("Mode"),
                "rw": mount.get("RW"),
                "propagation": mount.get("Propagation"),
            }
        )
    return mounts


def _compose_labels(labels: dict[str, str]) -> dict[str, str | None]:
    return {
        "project": labels.get("com.docker.compose.project"),
        "service": labels.get("com.docker.compose.service"),
        "container_number": labels.get("com.docker.compose.container-number"),
        "working_dir": labels.get("com.docker.compose.project.working_dir"),
        "config_files": labels.get("com.docker.compose.project.config_files"),
    }


def _status_for_container(status: str, health: str | None) -> tuple[str, str]:
    if health == "unhealthy" or status in {"restarting", "dead"}:
        return "critical", "Inspect logs and recent compose/image changes before restarting anything."
    if status in {"exited", "created", "paused", "removing"}:
        return "warning", "Confirm whether this container is expected to be stopped or paused."
    if status == "running" and health in {None, "healthy", "starting"}:
        if health == "starting":
            return "warning", "Container is running but healthcheck is still starting; check again soon."
        return "ok", "No action required."
    return "unknown", "Review Docker state and recent deployment activity."


def collect(config: dict[str, Any]) -> list[HealthCheck]:
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

    socket_url = config.get("socket_url") or "unix://var/run/docker.sock"
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
    for container in sorted(containers, key=lambda c: c.name):
        attrs = container.attrs or {}
        state = attrs.get("State", {}) or {}
        status = state.get("Status") or getattr(container, "status", "unknown")
        health = (state.get("Health") or {}).get("Status")
        restart_count = attrs.get("RestartCount")
        labels = attrs.get("Config", {}).get("Labels") or {}
        image = _image_name(attrs, getattr(getattr(container, "image", None), "tags", [None])[0] if getattr(container, "image", None) else None)
        check_status, action = _status_for_container(status, health)
        evidence = {
            "name": container.name,
            "image": image,
            "status": status,
            "health_status": health,
            "restart_count": restart_count,
            "ports": _ports(attrs),
            "mounts": _mounts(attrs),
            "compose": _compose_labels(labels),
        }
        health_text = f", health={health}" if health else ""
        restart_text = f", restart_count={restart_count}" if restart_count is not None else ""
        summary = f"{container.name}: image={image}, status={status}{health_text}{restart_text}."
        checks.append(
            HealthCheck(
                f"docker_container_{container.id[:12]}",
                f"Docker container: {container.name}",
                check_status,
                summary,
                evidence,
                action,
            )
        )

    return checks
