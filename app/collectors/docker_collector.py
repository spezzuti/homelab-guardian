from __future__ import annotations

from typing import Any

from app.models import HealthCheck


def collect(config: dict[str, Any]) -> list[HealthCheck]:
    try:
        import docker
    except Exception as exc:
        return [HealthCheck("docker_sdk_missing", "Docker", "unknown", "Docker collector is enabled, but the Docker SDK could not be imported.", {"error": str(exc)}, "Install dependencies with pip install -r requirements.txt.")]

    try:
        client = docker.DockerClient(base_url=config.get("socket_url") or "unix://var/run/docker.sock")
        containers = client.containers.list(all=True)
    except Exception as exc:
        return [HealthCheck("docker_unavailable", "Docker", "unknown", "Docker collector could not inspect Docker.", {"error": str(exc)}, "Check Docker socket path and permissions. Treat socket access as sensitive.")]

    checks: list[HealthCheck] = []
    if not containers:
        return [HealthCheck("docker_no_containers", "Docker containers", "unknown", "No Docker containers were found.", {}, "If this host should run Docker workloads, check Docker context and socket configuration.")]

    for container in containers:
        attrs = container.attrs or {}
        state = attrs.get("State", {})
        status = state.get("Status", container.status)
        health = state.get("Health", {}).get("Status")
        labels = attrs.get("Config", {}).get("Labels") or {}
        compose_project = labels.get("com.docker.compose.project")
        evidence = {
            "container": container.name,
            "status": status,
            "health": health,
            "compose_project": compose_project,
            "ports": attrs.get("NetworkSettings", {}).get("Ports") or {},
            "mounts": attrs.get("Mounts") or [],
        }
        if status == "running" and health in (None, "healthy"):
            check_status = "ok"
            summary = f"Container {container.name} is running."
            action = "No action required."
        elif health == "unhealthy" or status in {"restarting", "dead"}:
            check_status = "critical"
            summary = f"Container {container.name} is {status} / {health or 'no healthcheck'}."
            action = "Inspect container logs and recent compose or image changes before restarting anything."
        else:
            check_status = "warning"
            summary = f"Container {container.name} is {status}."
            action = "Confirm whether this container is expected to be stopped."
        checks.append(HealthCheck(f"docker_container_{container.id[:12]}", f"Docker container: {container.name}", check_status, summary, evidence, action))

    return checks
