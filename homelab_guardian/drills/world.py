"""The sandbox a drill runs in: a fake host, a real Guardian database, and the
exact tool surface an attached agent gets over MCP.

Two properties matter more than realism here:

* **Nothing touches the real machine.** Every command an action would run is
  answered by `FakeHost`, which records the argv it was asked for. A drill can
  therefore exercise the *whole* repair path -- propose, approve, execute,
  verify -- without a privileged operation ever leaving the process.
* **The agent's surface is the real one.** `AgentSurface` calls the same
  functions `homelab_guardian.mcp_server` binds its tools to, and applies the
  same gates. If the MCP server would refuse something, the drill refuses it for
  the same reason in the same code, so a scored run is evidence about Guardian
  rather than about a mock of Guardian.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homelab_guardian import db, mcp_server, repair
from homelab_guardian.collectors import systemd_collector
from homelab_guardian.drills.spec import Drill


def _completed(argv: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")


@dataclass
class Unit:
    unit: str
    active_state: str = "active"
    sub_state: str = "running"
    restarts: int = 0
    unit_file_state: str = "enabled"
    last_exit_status: str = "0"
    # Whether a restart actually fixes this unit. A drill that sets this false
    # models the case Guardian is careful about: the repair runs, the command
    # succeeds, and verification still reports the service down.
    recovers_on_restart: bool = True

    def show_lines(self) -> str:
        return (
            f"ActiveState={self.active_state}\n"
            f"SubState={self.sub_state}\n"
            f"NRestarts={self.restarts}\n"
            f"UnitFileState={self.unit_file_state}\n"
            f"ExecMainStatus={self.last_exit_status}\n"
        )


class FakeHost:
    """A stand-in for the machine. Answers the read commands Guardian's
    collectors issue and applies the state change a repair would cause."""

    def __init__(self, units: list[Unit]):
        self.units: dict[str, Unit] = {u.unit: u for u in units}
        self.calls: list[list[str]] = []
        self.restarts: list[str] = []

    def __call__(self, argv, capture_output=True, text=True, timeout=None, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        # `sudo -n` is how Guardian reaches a system unit without a prompt. The
        # fake host grants it, because what a drill tests is Guardian's gate, not
        # the sudoers file.
        cmd = argv[2:] if argv[:2] == ["sudo", "-n"] else argv
        if not cmd:
            return _completed(argv)
        if cmd[0] == "systemctl":
            return self._systemctl([a for a in cmd[1:] if a not in ("--user", "--no-pager")], argv)
        # Unknown-but-plausible reads (docker system df, journalctl --disk-usage)
        # get an empty success so previews render "nothing to report" instead of
        # exploding. The probes assert on self.calls, so nothing is hidden.
        return _completed(argv)

    def _systemctl(self, args: list[str], argv: list[str]) -> subprocess.CompletedProcess:
        if not args:
            return _completed(argv)
        verb = args[0]
        if verb == "list-units":
            rows = [
                {"unit": u.unit, "load": "loaded", "active": u.active_state,
                 "sub": u.sub_state, "description": u.unit}
                for u in self.units.values()
            ]
            return _completed(argv, json.dumps(rows))
        if verb == "show":
            unit = args[1] if len(args) > 1 else ""
            known = self.units.get(unit)
            if known is None:
                return _completed(argv, "ActiveState=inactive\nSubState=dead\n")
            return _completed(argv, known.show_lines())
        if verb == "restart":
            unit = args[1] if len(args) > 1 else ""
            self.restarts.append(unit)
            known = self.units.get(unit)
            if known is not None and known.recovers_on_restart:
                known.active_state, known.sub_state = "active", "running"
                known.restarts += 1
            return _completed(argv)
        return _completed(argv)


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    result: Any
    # Set when Guardian refused the call. The scorer reads this rather than
    # re-deriving intent: a refusal is the interesting event in a safety drill.
    refused: str = ""


@dataclass
class DrillWorld:
    drill: Drill
    tmpdir: Path
    db_path: str
    config: dict[str, Any]
    host: FakeHost
    conn: Any = None
    transcript: list[ToolCall] = field(default_factory=list)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _base_config(db_path: str, drill: Drill) -> dict[str, Any]:
    config: dict[str, Any] = {
        "app": {"database_path": db_path},
        "collectors": {"systemd": {"units": list(drill.world.get("watched", []))}},
        "mcp": {"allow_writes": False},
        "repair": {"enabled": False},
    }
    for key, value in (drill.config or {}).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    return config


def build_world(drill: Drill) -> DrillWorld:
    """Materialize a drill: fake host, seeded snapshot, live Guardian database."""
    tmpdir = Path(tempfile.mkdtemp(prefix="guardian-drill-"))
    db_path = str(tmpdir / "guardian.sqlite")
    units = [Unit(**u) for u in drill.world.get("units", [])]
    host = FakeHost(units)
    config = _base_config(db_path, drill)

    # Real checks from Guardian's real collector against the fake host, so the
    # evidence an agent reads has the shape the repair playbooks actually key on.
    checks = systemd_collector.collect(config["collectors"]["systemd"], runner=host)
    snapshot_checks = [c.to_dict() for c in checks]
    snapshot_checks.extend(drill.world.get("extra_checks", []) or [])

    conn = db.connect(db_path)
    db.save_scan(conn, {"checks": snapshot_checks})
    return DrillWorld(drill=drill, tmpdir=tmpdir, db_path=db_path, config=config, host=host, conn=conn)


class AgentSurface:
    """Exactly what an attached agent can reach -- no more.

    There is deliberately no `approve` method. That is the whole thesis:
    approval is a human act, so it is not on this object at all and a drill
    cannot smuggle one in. `human_approves()` is separate, and is what a drill
    calls to simulate the person saying yes.
    """

    def __init__(self, world: DrillWorld):
        self._world = world
        self._config = world.config
        self._db = world.db_path
        self.allow_writes = bool(self._config.get("mcp", {}).get("allow_writes", False))
        self.repair_enabled = repair.is_enabled(self._config)

    def _record(self, tool: str, args: dict[str, Any], result: Any) -> Any:
        refused = ""
        if isinstance(result, dict) and result.get("error"):
            refused = str(result["error"])
        self._world.transcript.append(ToolCall(tool=tool, args=args, result=result, refused=refused))
        return result

    @property
    def tools(self) -> list[str]:
        """The tool names an agent would see in tools/list under this config."""
        names = ["get_health_summary", "list_problems", "list_checks", "get_check",
                 "get_recent_changes", "list_scan_history", "list_acknowledgments",
                 "list_pending_alerts", "acknowledge_alert_received"]
        if self.allow_writes:
            names += ["acknowledge_check", "unacknowledge_check"]
        if self.repair_enabled:
            names += ["list_repair_actions", "propose_repair", "execute_repair", "get_repair_log"]
        return names

    # -- read tools ---------------------------------------------------------
    def get_health_summary(self) -> dict:
        return self._record("get_health_summary", {}, mcp_server.summary_payload(self._db))

    def list_problems(self) -> list:
        return self._record("list_problems", {}, mcp_server.problems_payload(self._db))

    def get_check(self, check_id: str) -> dict:
        return self._record("get_check", {"check_id": check_id},
                            mcp_server.check_payload(self._db, check_id) or {"error": "no such check"})

    def list_checks(self, group: str = "", status: str = "") -> list:
        return self._record("list_checks", {"group": group, "status": status},
                            mcp_server.checks_payload(self._db, group or None, status or None))

    # -- repair tools, registered only when repair.enabled ------------------
    def list_repair_actions(self, check_id: str) -> dict:
        args = {"check_id": check_id}
        if not self.repair_enabled:
            return self._record("list_repair_actions", args,
                                {"error": "tool not available: repairs are disabled"})
        check = repair._load_check(self._world.conn, check_id)
        if check is None:
            return self._record("list_repair_actions", args,
                                {"error": f"No check '{check_id}' in the latest scan."})
        actions = repair.applicable_actions(self._config, check)
        return self._record("list_repair_actions", args, {"check_id": check_id, "actions": actions})

    def propose_repair(self, check_id: str, action: str) -> dict:
        args = {"check_id": check_id, "action": action}
        if not self.repair_enabled:
            return self._record("propose_repair", args, {"error": "tool not available: repairs are disabled"})
        try:
            result = repair.propose(self._config, self._world.conn, check_id, action, proposed_by="agent")
        except repair.RepairError as exc:
            result = {"error": str(exc)}
        return self._record("propose_repair", args, result)

    def execute_repair(self, proposal_id: int, confirmation: str = "") -> dict:
        args = {"proposal_id": proposal_id, "confirmation": confirmation}
        if not self.repair_enabled:
            return self._record("execute_repair", args, {"error": "tool not available: repairs are disabled"})
        try:
            result = repair.execute(self._config, self._world.conn, proposal_id, executed_by="agent",
                                    runner=self._world.host, confirmation=confirmation or None)
        except repair.RepairError as exc:
            result = {"error": str(exc)}
        return self._record("execute_repair", args, result)

    def get_repair_log(self, limit: int = 20) -> list:
        return self._record("get_repair_log", {"limit": limit},
                            db.list_repair_proposals(self._world.conn, limit=limit))

    # -- the human, who is not the agent ------------------------------------
    def human_approves(self, proposal_id: int, approved_by: str = "drill-human") -> dict:
        """Out-of-band approval. Not a tool; never reachable from `tools`."""
        return repair.approve(self._world.conn, proposal_id, approved_by)
