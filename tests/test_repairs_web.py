import base64
import http.client
import re
import secrets as secrets_mod
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

from homelab_guardian import db, web
from homelab_guardian.config import load_config
from homelab_guardian.models import HealthCheck
from homelab_guardian.webauth import build_authenticator

CONFIG = """app:
  database_path: {db}
repair:
  enabled: {enabled}
web:
  auth:
    mode: {mode}
    username: admin
    password: pw
"""

PLAN = {"argv": ["sudo", "-n", "systemctl", "restart", "x.service"],
        "blast_radius": "Restarts only x.service.", "params": {"unit": "x.service"}}


def _start(tmp_path, enabled="true", mode="basic", seed=True):
    dbp = str(tmp_path / "g.sqlite")
    conn = db.connect(dbp)
    db.save_scan(conn, {"app": "HG", "checks": [HealthCheck("a", "A", "ok", "x").to_dict()]})
    pid = None
    if seed:
        pid = db.create_repair_proposal(conn, "systemd_unit_x", "restart_systemd_unit", PLAN, proposed_by="agent")
    conn.close()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG.format(db=dbp, enabled=enabled, mode=mode), encoding="utf-8")
    config = load_config(str(cfg))
    handler = type("BoundH", (web.GuardianRequestHandler,), {
        "database_path": dbp, "config_path": str(cfg),
        "auth": build_authenticator(config),
        "auth_mode": (config.get("web") or {}).get("auth", {}).get("mode", "none"),
        "csrf_secret": secrets_mod.token_bytes(16), "refresh_seconds": 0,
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, dbp, pid


def _basic():
    return "Basic " + base64.b64encode(b"admin:pw").decode()


def _req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data


def test_repairs_requires_auth(tmp_path):
    server, _, _ = _start(tmp_path)
    try:
        resp, _ = _req(server.server_address[1], "GET", "/repairs")
        assert resp.status == 401
    finally:
        server.shutdown()


def test_repairs_lists_proposal_with_approve_button(tmp_path):
    server, _, pid = _start(tmp_path)
    try:
        resp, body = _req(server.server_address[1], "GET", "/repairs", {"Authorization": _basic()})
        assert resp.status == 200
        assert f"#{pid}" in body and "restart_systemd_unit" in body
        assert "systemctl restart x.service" in body
        assert 'value="approve"' in body and 'value="deny"' in body
    finally:
        server.shutdown()


def test_repairs_disabled_shows_notice(tmp_path):
    server, _, _ = _start(tmp_path, enabled="false", seed=False)
    try:
        resp, body = _req(server.server_address[1], "GET", "/repairs", {"Authorization": _basic()})
        assert resp.status == 200 and "Repairs are disabled" in body
    finally:
        server.shutdown()


def test_approve_updates_db(tmp_path):
    server, dbp, pid = _start(tmp_path)
    port = server.server_address[1]
    try:
        _, body = _req(port, "GET", "/repairs", {"Authorization": _basic()})
        csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        post = urllib.parse.urlencode({"csrf": csrf, "proposal_id": pid, "decision": "approve"})
        resp, _ = _req(port, "POST", "/repairs",
                       {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"}, post)
        assert resp.status == 302
        row = db.get_repair_proposal(db.connect(dbp), pid)
        assert row["status"] == "approved" and row["approved_by"] == "admin"
    finally:
        server.shutdown()


def test_deny_updates_db(tmp_path):
    server, dbp, pid = _start(tmp_path)
    port = server.server_address[1]
    try:
        _, body = _req(port, "GET", "/repairs", {"Authorization": _basic()})
        csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        post = urllib.parse.urlencode({"csrf": csrf, "proposal_id": pid, "decision": "deny"})
        resp, _ = _req(port, "POST", "/repairs",
                       {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"}, post)
        assert resp.status == 302
        assert db.get_repair_proposal(db.connect(dbp), pid)["status"] == "denied"
    finally:
        server.shutdown()


def test_approve_rejects_bad_csrf(tmp_path):
    server, dbp, pid = _start(tmp_path)
    try:
        post = urllib.parse.urlencode({"csrf": "wrong", "proposal_id": pid, "decision": "approve"})
        resp, _ = _req(server.server_address[1], "POST", "/repairs",
                       {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"}, post)
        assert resp.status == 403
        assert db.get_repair_proposal(db.connect(dbp), pid)["status"] == "proposed"  # unchanged
    finally:
        server.shutdown()


def test_dashboard_header_shows_repairs_link_with_count(tmp_path):
    server, _, _ = _start(tmp_path)  # one proposed proposal
    try:
        resp, body = _req(server.server_address[1], "GET", "/", {"Authorization": _basic()})
        assert resp.status == 200
        assert 'href="/repairs"' in body
        assert 'class="rbadge">1</span>' in body  # one pending
    finally:
        server.shutdown()


def test_dashboard_no_repairs_link_when_disabled(tmp_path):
    server, _, _ = _start(tmp_path, enabled="false", seed=False)
    try:
        resp, body = _req(server.server_address[1], "GET", "/", {"Authorization": _basic()})
        assert 'href="/repairs"' not in body
    finally:
        server.shutdown()
