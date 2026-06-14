import base64
import http.client
import re
import secrets as secrets_mod
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import yaml

from homelab_guardian import db, web
from homelab_guardian.config import load_config
from homelab_guardian.models import HealthCheck
from homelab_guardian.webauth import build_authenticator

CONFIG_BASIC = """app:
  database_path: {db}
collectors:
  docker:
    enabled: false
  network:
    enabled: true
web:
  auth:
    mode: basic
    username: admin
    password: pw
"""

CONFIG_NONE = """app:
  database_path: {db}
collectors:
  docker:
    enabled: false
"""


def _start(tmp_path, config_text):
    dbp = str(tmp_path / "g.sqlite")
    conn = db.connect(dbp)
    db.save_scan(conn, {"app": "HG", "checks": [HealthCheck("a", "A", "ok", "x").to_dict()]})
    conn.close()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(config_text.format(db=dbp), encoding="utf-8")
    config = load_config(str(cfg_path))
    handler = type(
        "BoundH",
        (web.GuardianRequestHandler,),
        {
            "database_path": dbp,
            "config_path": str(cfg_path),
            "auth": build_authenticator(config),
            "auth_mode": (config.get("web") or {}).get("auth", {}).get("mode", "none"),
            "csrf_secret": secrets_mod.token_bytes(16),
            "refresh_seconds": 0,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, str(cfg_path)


def _basic():
    return "Basic " + base64.b64encode(b"admin:pw").decode()


def _req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, data


def test_settings_requires_auth(tmp_path):
    server, _ = _start(tmp_path, CONFIG_BASIC)
    port = server.server_address[1]
    try:
        resp, _ = _req(port, "GET", "/settings")
        assert resp.status == 401
    finally:
        server.shutdown()


def test_settings_renders_for_authed_user(tmp_path):
    server, _ = _start(tmp_path, CONFIG_BASIC)
    port = server.server_address[1]
    try:
        resp, body = _req(port, "GET", "/settings", {"Authorization": _basic()})
        assert resp.status == 200
        assert "Collectors" in body and 'name="collector:docker"' in body
        # The two backup collectors are easy to confuse — their descriptions
        # must spell out the difference (folder freshness vs. the job itself).
        assert "Checks a backup folder for recent files." in body
        assert "Checks a backup job" in body
    finally:
        server.shutdown()


def test_toggleable_collectors_carry_descriptions():
    from homelab_guardian.configedit import toggleable_collectors

    rows = {c["name"]: c for c in toggleable_collectors({"collectors": {}})}
    assert rows["backups"]["description"] == "Checks a backup folder for recent files."
    assert rows["backup_health"]["description"].startswith("Checks a backup job")
    # Every registered collector should carry a non-empty description.
    assert all(c["description"] for c in rows.values())


def test_save_toggles_collector_in_config_file(tmp_path):
    server, cfg_path = _start(tmp_path, CONFIG_BASIC)
    port = server.server_address[1]
    try:
        _, body = _req(port, "GET", "/settings", {"Authorization": _basic()})
        csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        post = urllib.parse.urlencode({"csrf": csrf, "collector:docker": "on", "collector:network": "on"})
        resp, _ = _req(
            port, "POST", "/settings",
            {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"},
            post,
        )
        assert resp.status == 302
        saved = yaml.safe_load(open(cfg_path, encoding="utf-8").read())
        assert saved["collectors"]["docker"]["enabled"] is True   # was false -> toggled on
        assert saved["collectors"]["network"]["enabled"] is True   # stayed on
    finally:
        server.shutdown()


def test_save_rejects_bad_csrf(tmp_path):
    server, _ = _start(tmp_path, CONFIG_BASIC)
    port = server.server_address[1]
    try:
        post = urllib.parse.urlencode({"csrf": "wrong", "collector:docker": "on"})
        resp, _ = _req(
            port, "POST", "/settings",
            {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"},
            post,
        )
        assert resp.status == 403
    finally:
        server.shutdown()


def test_post_body_to_error_paths_gets_clean_status(tmp_path):
    # An early error response (403/404) must still consume the POST body, or
    # closing the socket with the body unread resets the connection (Windows
    # WinError 10053) and the client never sees the status. Send a sizeable
    # body to widen the window for a reset.
    server, _ = _start(tmp_path, CONFIG_NONE)
    port = server.server_address[1]
    big = urllib.parse.urlencode({"csrf": "x", "payload": "z" * 50_000})
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        resp, _ = _req(port, "POST", "/settings", hdr, big)  # 403: editing needs auth
        assert resp.status == 403
        resp, _ = _req(port, "POST", "/does-not-exist", hdr, big)  # 404
        assert resp.status == 404
    finally:
        server.shutdown()


def test_no_auth_mode_is_read_only(tmp_path):
    server, _ = _start(tmp_path, CONFIG_NONE)
    port = server.server_address[1]
    try:
        resp, body = _req(port, "GET", "/settings")  # NoAuth lets the page render
        assert resp.status == 200 and "Authentication is off" in body
        resp, _ = _req(port, "POST", "/settings", {"Content-Type": "application/x-www-form-urlencoded"}, "csrf=x")
        assert resp.status == 403  # editing blocked without auth
    finally:
        server.shutdown()
