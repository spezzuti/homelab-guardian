from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homelab_guardian import db, web
from homelab_guardian.collectors import systemd_collector

# Approval-gated repair. See docs/repair.md for the full safety design. The rule
# this code exists to enforce:
#
#   Guardian executes ONLY named, parameterized, whitelisted actions — never raw
#   shell, never an LLM-generated command — and only after a human has approved
#   that specific proposal. propose() may be called by an agent; approve() is for
#   a human (CLI/dashboard); execute() refuses anything not approved.
#
# Action parameters are derived from Guardian's own validated check evidence
# (e.g. the unit name comes from the failing check, not from caller text) and
# re-checked against an explicit allowlist, then passed as argv elements. There
# is deliberately no generic "run a command" action.


class RepairError(Exception):
    """A repair was refused: disabled, not applicable, not allowed, or not approved."""


def is_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("repair", {}).get("enabled", False))


def _playbook_config(config: dict[str, Any], action: str) -> dict[str, Any]:
    return (config.get("repair", {}).get("playbooks", {}) or {}).get(action, {}) or {}


# --- registry --------------------------------------------------------------
# Each playbook: applies_to(check)->bool, plan(config,pcfg,check)->dict (raises
# RepairError if not permitted), verify(config,runner)->HealthCheck|None.

def _normalize_allowed(pcfg: dict[str, Any], key: str, id_field: str) -> set[str]:
    """The allowlist for a playbook: a list of names, or of {<id_field>: name}."""
    out: set[str] = set()
    for entry in pcfg.get(key, []) or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict) and entry.get(id_field):
            out.add(str(entry[id_field]))
    return out


def _systemd_applies(check) -> bool:
    return (
        check.id.startswith("systemd_unit_")
        and isinstance(check.evidence, dict)
        and bool(check.evidence.get("unit"))
    )


def _systemd_plan(config: dict[str, Any], pcfg: dict[str, Any], check) -> dict[str, Any]:
    unit = str(check.evidence.get("unit"))
    bus = str(check.evidence.get("bus", "system"))
    user = bus == "user"
    allowed = _normalize_allowed(pcfg, "allowed_units", "unit")
    if unit not in allowed:
        raise RepairError(
            f"Unit '{unit}' is not in the allowlist. Add it to "
            f"repair.playbooks.restart_systemd_unit.allowed_units to permit this repair."
        )
    # User-bus units need no elevation. System units go through `sudo -n` so a
    # missing scoped-sudoers grant fails fast instead of hanging on a prompt.
    argv = (["systemctl", "--user", "restart", unit] if user
            else ["sudo", "-n", "systemctl", "restart", unit])
    return {
        "action": "restart_systemd_unit",
        "check_id": check.id,
        "params": {"unit": unit, "bus": bus},
        "argv": argv,
        "blast_radius": f"Restarts only {unit} ({bus} bus) — a brief interruption of that one service.",
        "reversible": "A restart is the standard recovery; the prior state was failed or looping.",
        "needs_privilege": not user,
        "timeout": float(pcfg.get("timeout", 60)),
    }


def _systemd_verify(config: dict[str, Any], check_id: str, runner) -> Any:
    sys_cfg = config.get("collectors", {}).get("systemd", {}) or {}
    for c in systemd_collector.collect(sys_cfg, runner=runner):
        if c.id == check_id:
            return c
    return None


def _docker_applies(check) -> bool:
    return (
        check.id.startswith("docker_container_")
        and isinstance(check.evidence, dict)
        and bool(check.evidence.get("name"))
    )


def _docker_plan(config: dict[str, Any], pcfg: dict[str, Any], check) -> dict[str, Any]:
    name = str(check.evidence.get("name"))
    allowed = _normalize_allowed(pcfg, "allowed_containers", "name")
    if name not in allowed:
        raise RepairError(
            f"Container '{name}' is not in the allowlist. Add it to "
            f"repair.playbooks.restart_container.allowed_containers to permit this repair."
        )
    # Whether `docker` needs elevation depends on the host's socket permissions;
    # use_sudo lets a deployment opt into a scoped `sudo -n docker restart` grant.
    use_sudo = bool(pcfg.get("use_sudo", False))
    argv = (["sudo", "-n", "docker", "restart", name] if use_sudo
            else ["docker", "restart", name])
    return {
        "action": "restart_container",
        "check_id": check.id,
        "params": {"container": name},
        "argv": argv,
        "blast_radius": f"Restarts only the container {name} — a brief interruption of that one container.",
        "reversible": "A restart is the standard recovery; the prior state was exited/unhealthy/restarting.",
        "needs_privilege": use_sudo,
        "timeout": float(pcfg.get("timeout", 60)),
    }


def _docker_verify(config: dict[str, Any], check_id: str, runner) -> Any:
    # The Docker collector reads via the Docker SDK, not `runner`; re-run it and
    # find the same check. (runner is unused here — verify's contract is just
    # "re-read the check", however that collector does it.)
    from homelab_guardian.collectors import docker_collector

    docker_cfg = config.get("collectors", {}).get("docker", {}) or {}
    for c in docker_collector.collect(docker_cfg):
        if c.id == check_id:
            return c
    return None


# --- disk-reclaim playbooks (destructive family) ---------------------------
# These apply to a FAILING disk check. Unlike restart, the action is a fixed
# whitelisted reclaim (not derived from the check) — the disk check only triggers
# applicability. Each carries a read-only `preview` (concrete effects) and a risk
# tier; `docker_prune` is `destructive` and so can never be auto-approved.

def _disk_applies(check) -> bool:
    return (
        check.id.startswith("disk_")
        and check.status in {"warning", "critical"}
        and isinstance(check.evidence, dict)
    )


def _disk_verify(config: dict[str, Any], check_id: str, runner) -> Any:
    from homelab_guardian.collectors import disk_collector

    for c in disk_collector.collect(config.get("collectors", {}).get("disks", {}) or {}):
        if c.id == check_id:
            return c
    return None


def _run_read(runner, argv: list[str], timeout: float = 15) -> str:
    """Best-effort read-only command for previews. Returns stripped stdout or ''."""
    try:
        r = runner(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _docker_prune_plan(config, pcfg, check) -> dict[str, Any]:
    allow_volumes = bool(pcfg.get("allow_volumes", False))
    argv = ["docker", "system", "prune", "-f"] + (["--volumes"] if allow_volumes else [])
    scope = " INCLUDING named volumes (data loss!)" if allow_volumes else " (images/containers/networks/build cache — not volumes)"
    return {
        "action": "docker_prune", "check_id": check.id, "params": {"allow_volumes": allow_volumes},
        "argv": argv,
        "blast_radius": f"Removes unused Docker data{scope}. Stopped containers and dangling images are deleted.",
        "reversible": "Not reversible — pruned images/containers are gone (re-pullable / re-creatable).",
        "needs_privilege": False, "timeout": float(pcfg.get("timeout", 120)),
    }


def _docker_prune_preview(config, pcfg, check, runner) -> dict[str, Any]:
    return {"docker_system_df": _run_read(runner, ["docker", "system", "df"], 20) or "unavailable"}


def _journal_vacuum_plan(config, pcfg, check) -> dict[str, Any]:
    cap = str(pcfg.get("vacuum_size", "200M"))
    return {
        "action": "journal_vacuum", "check_id": check.id, "params": {"vacuum_size": cap},
        "argv": ["sudo", "-n", "journalctl", f"--vacuum-size={cap}"],
        "blast_radius": f"Trims the systemd journal to {cap}; older log history is deleted.",
        "reversible": "Not reversible — vacuumed journal entries are gone.",
        "needs_privilege": True, "timeout": float(pcfg.get("timeout", 60)),
    }


def _journal_vacuum_preview(config, pcfg, check, runner) -> dict[str, Any]:
    return {
        "current_journal_usage": _run_read(runner, ["journalctl", "--disk-usage"], 15) or "unavailable",
        "vacuum_target": str(pcfg.get("vacuum_size", "200M")),
    }


def _apt_clean_plan(config, pcfg, check) -> dict[str, Any]:
    return {
        "action": "apt_clean", "check_id": check.id, "params": {},
        "argv": ["sudo", "-n", "apt-get", "clean"],
        "blast_radius": "Deletes cached .deb downloads from /var/cache/apt/archives.",
        "reversible": "Harmless — packages re-download on the next apt operation; no system change.",
        "needs_privilege": True, "timeout": float(pcfg.get("timeout", 60)),
    }


def _apt_clean_preview(config, pcfg, check, runner) -> dict[str, Any]:
    out = _run_read(runner, ["du", "-sh", "/var/cache/apt/archives"], 15)
    return {"apt_cache_size": (out.split()[0] if out else "unavailable")}


# prune_dir is the sharp one: it deletes USER files. The target dirs come from an
# explicit allowlist (never the check / never caller text), with an age filter,
# and it carries a backup precondition — refuse to delete when Guardian's own
# backup_health is not ok-and-present.

def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024:
            return f"{f:.0f}{unit}"
        f /= 1024
    return f"{f:.0f}PB"


def _backup_checks(latest_checks) -> list:
    return [c for c in latest_checks if getattr(c, "group", "") == "Backups"]


def _prune_dir_plan(config, pcfg, check) -> dict[str, Any]:
    paths = [str(p) for p in (pcfg.get("allowed_paths") or [])]
    if not paths:
        raise RepairError("prune_dir has no allowed_paths configured; nothing is permitted.")
    days = int(pcfg.get("older_than_days", 14))
    argv = ["find", *paths, "-type", "f", "-mtime", f"+{days}", "-delete"]
    return {
        "action": "prune_dir", "check_id": check.id,
        "params": {"paths": paths, "older_than_days": days},
        "argv": argv,
        "blast_radius": f"Permanently deletes files older than {days}d under: {', '.join(paths)}. User data — irreversible.",
        "reversible": "NOT reversible — deleted files are gone; restore from backup if needed.",
        "needs_privilege": False, "timeout": float(pcfg.get("timeout", 120)),
    }


def _prune_dir_preview(config, pcfg, check, runner) -> dict[str, Any]:
    paths = [str(p) for p in (pcfg.get("allowed_paths") or [])]
    days = int(pcfg.get("older_than_days", 14))
    if not paths:
        return {"error": "no allowed_paths"}
    out = _run_read(runner, ["find", *paths, "-type", "f", "-mtime", f"+{days}", "-printf", "%s\\n"], 30)
    sizes = [int(x) for x in out.split() if x.isdigit()]
    total = sum(sizes)
    return {"files": len(sizes), "bytes": total, "approx": _human_bytes(total), "older_than_days": days}


def _prune_dir_precond(config, pcfg, check, latest_checks):
    """Mandatory-but-narrow backup interlock. Configured-but-not-ok backup → hard
    refuse. No backup signal at all → allow unless require_fresh_backup (then
    refuse). Healthy backup → allow."""
    backups = _backup_checks(latest_checks)
    if backups:
        if any(c.status != "ok" for c in backups):
            return ("prune_dir refused: a Backups check is not ok — do not delete user files "
                    "while the backup safety net is down.")
        return None
    if bool(pcfg.get("require_fresh_backup", False)):
        return ("prune_dir requires a verified backup, but no Backups check is configured. "
                "Add backup_health, or set require_fresh_backup: false to override.")
    return None


def _prune_dir_warn(config, pcfg, check, latest_checks):
    if not _backup_checks(latest_checks):
        return "No backup_health signal configured — deleting user files without a verified backup."
    return None


PLAYBOOKS: dict[str, dict[str, Any]] = {
    "restart_systemd_unit": {
        "applies_to": _systemd_applies, "plan": _systemd_plan, "verify": _systemd_verify,
        "risk": "moderate",
    },
    "restart_container": {
        "applies_to": _docker_applies, "plan": _docker_plan, "verify": _docker_verify,
        "risk": "moderate",
    },
    "docker_prune": {
        "applies_to": _disk_applies, "plan": _docker_prune_plan, "verify": _disk_verify,
        "preview": _docker_prune_preview, "risk": "destructive",
    },
    "journal_vacuum": {
        "applies_to": _disk_applies, "plan": _journal_vacuum_plan, "verify": _disk_verify,
        "preview": _journal_vacuum_preview, "risk": "moderate",
    },
    "apt_clean": {
        "applies_to": _disk_applies, "plan": _apt_clean_plan, "verify": _disk_verify,
        "preview": _apt_clean_preview, "risk": "low",
    },
    "prune_dir": {
        "applies_to": _disk_applies, "plan": _prune_dir_plan, "verify": _disk_verify,
        "preview": _prune_dir_preview, "preconditions": _prune_dir_precond,
        "warn": _prune_dir_warn, "risk": "destructive",
    },
}


# --- check lookup ----------------------------------------------------------

def _load_check(conn, check_id: str):
    scan = db.load_latest_scan(conn)
    if scan is None:
        return None
    for c in web.checks_from_snapshot(scan[2]):
        if c.id == check_id:
            return c
    return None


def applicable_actions(config: dict[str, Any], check) -> list[str]:
    """Repair actions available for a failing check (enabled playbooks only)."""
    if not is_enabled(config) or check is None or check.status == "ok":
        return []
    out = []
    for action, pb in PLAYBOOKS.items():
        if _playbook_config(config, action).get("enabled", False) and pb["applies_to"](check):
            out.append(action)
    return out


def build_plan(
    config: dict[str, Any], check, action: str, latest_checks=None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    """Validate everything and resolve a concrete plan. Raises RepairError if the
    repair is not permitted. Changes nothing — this is the dry run. Carries the
    risk tier; runs the playbook's preconditions (cross-collector interlocks,
    evaluated against `latest_checks`) and computes its read-only `preview`."""
    if not is_enabled(config):
        raise RepairError("Repairs are disabled (repair.enabled is false).")
    pb = PLAYBOOKS.get(action)
    if pb is None:
        raise RepairError(f"Unknown repair action '{action}'.")
    pcfg = _playbook_config(config, action)
    if not pcfg.get("enabled", False):
        raise RepairError(f"Repair action '{action}' is not enabled.")
    if check is None:
        raise RepairError("No such check in the latest scan.")
    if check.status == "ok":
        raise RepairError(f"Check '{check.id}' is not failing; nothing to repair.")
    if not pb["applies_to"](check):
        raise RepairError(f"'{action}' does not apply to check '{check.id}'.")

    # Preconditions: cross-collector interlocks against Guardian's own state.
    precond = pb.get("preconditions")
    if precond is not None and latest_checks is not None:
        reason = precond(config, pcfg, check, latest_checks)
        if reason:
            raise RepairError(reason)

    plan = pb["plan"](config, pcfg, check)
    plan["risk"] = pb.get("risk", "moderate")

    # Warn (non-blocking advisory, e.g. "no verified backup") carried on the plan.
    warn_fn = pb.get("warn")
    if warn_fn is not None and latest_checks is not None:
        w = warn_fn(config, pcfg, check, latest_checks)
        if w:
            plan["warning"] = w

    # Preview: a read-only estimate of the concrete effect, best-effort.
    preview_fn = pb.get("preview")
    if preview_fn is not None:
        try:
            plan["preview"] = preview_fn(config, pcfg, check, runner)
        except Exception as exc:
            plan["preview"] = {"error": str(exc)}
    return plan


def _all_latest_checks(conn) -> list:
    scan = db.load_latest_scan(conn)
    return web.checks_from_snapshot(scan[2]) if scan is not None else []


# --- orchestration (propose / approve / deny / execute) --------------------

def propose(config: dict[str, Any], conn, check_id: str, action: str, proposed_by: str = "") -> dict[str, Any]:
    """Create a proposal (dry run + recorded plan). Auto-approves only if the
    playbook opts in via auto_approve. Returns the proposal id, plan, and status."""
    plan = build_plan(config, _load_check(conn, check_id), action, latest_checks=_all_latest_checks(conn))
    proposal_id = db.create_repair_proposal(conn, check_id, action, plan, proposed_by=proposed_by)
    status = "proposed"
    # Auto-approve is opt-in AND never available to destructive actions.
    if _playbook_config(config, action).get("auto_approve", False) and plan.get("risk") != "destructive":
        db.set_repair_decision(conn, proposal_id, "approved", "auto-approve")
        status = "approved"
    return {"proposal_id": proposal_id, "status": status, "plan": plan}


def approve(conn, proposal_id: int, approved_by: str) -> dict[str, Any]:
    if db.set_repair_decision(conn, proposal_id, "approved", approved_by):
        return {"proposal_id": proposal_id, "status": "approved"}
    p = db.get_repair_proposal(conn, proposal_id)
    if p is None:
        raise RepairError(f"No proposal #{proposal_id}.")
    raise RepairError(f"Proposal #{proposal_id} is '{p['status']}', not awaiting approval.")


def deny(conn, proposal_id: int, denied_by: str) -> dict[str, Any]:
    if db.set_repair_decision(conn, proposal_id, "denied", denied_by):
        return {"proposal_id": proposal_id, "status": "denied"}
    p = db.get_repair_proposal(conn, proposal_id)
    if p is None:
        raise RepairError(f"No proposal #{proposal_id}.")
    raise RepairError(f"Proposal #{proposal_id} is '{p['status']}', not awaiting a decision.")


def _within_loop_guard(config, conn, action, check_id) -> bool:
    limit = int(_playbook_config(config, action).get("max_attempts_per_hour", 3))
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return db.count_recent_repair_executions(conn, action, check_id, since) < limit


def execute(
    config: dict[str, Any],
    conn,
    proposal_id: int,
    executed_by: str = "",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    confirmation: str | None = None,
) -> dict[str, Any]:
    """Run an APPROVED proposal's action, then verify recovery. Refuses anything
    not approved, already executed, or over the loop guard. Never raises on a
    failed action — it records and reports the failure."""
    if not is_enabled(config):
        raise RepairError("Repairs are disabled (repair.enabled is false).")
    p = db.get_repair_proposal(conn, proposal_id)
    if p is None:
        raise RepairError(f"No proposal #{proposal_id}.")
    if p["status"] != "approved":
        raise RepairError(f"Proposal #{proposal_id} is '{p['status']}', not approved — cannot execute.")
    plan = p["plan_json"] if isinstance(p["plan_json"], dict) else {}
    if plan.get("risk") == "destructive" and p.get("approved_by") == "auto-approve":
        raise RepairError("Destructive repairs cannot run on an auto-approval; they require explicit human approval.")
    # Optional typed-confirmation gate for destructive actions (off by default).
    require_typed = (bool(config.get("repair", {}).get("require_typed_confirmation", False))
                     or bool(_playbook_config(config, p["action"]).get("require_typed_confirmation", False)))
    if plan.get("risk") == "destructive" and require_typed and str(confirmation) != str(proposal_id):
        raise RepairError(
            f"This destructive repair requires typed confirmation — re-run with the token '{proposal_id}'."
        )
    if not _within_loop_guard(config, conn, p["action"], p["check_id"]):
        raise RepairError(
            f"Loop guard: '{p['action']}' on '{p['check_id']}' has already run its hourly limit. "
            "It keeps not recovering — this needs a human, not another restart."
        )

    argv = p["argv"] if isinstance(p["argv"], list) else []
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=float(plan.get("timeout", 60)))
        result = {
            "ran": True,
            "exit_code": completed.returncode,
            "ok": completed.returncode == 0,
            "stdout": (completed.stdout or "")[:500],
            "stderr": (completed.stderr or "")[:500],
        }
    except Exception as exc:  # FileNotFoundError, TimeoutExpired, OSError, ...
        result = {"ran": False, "ok": False, "error": str(exc)}

    verify = {"verified": False, "status": "unknown", "summary": "Verification did not run."}
    if result.get("ok"):
        check = PLAYBOOKS[p["action"]]["verify"](config, p["check_id"], runner)
        if check is not None:
            verify = {"verified": check.status == "ok", "status": check.status, "summary": check.summary}
        else:
            verify = {"verified": False, "status": "unknown", "summary": "Could not re-read the check after the repair."}

    status = "executed" if (result.get("ok") and verify.get("verified")) else "failed"
    db.record_repair_execution(conn, proposal_id, status, result, verify)
    return {"proposal_id": proposal_id, "status": status, "result": result, "verify": verify}
