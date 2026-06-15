import base64

import pytest

from homelab_guardian.webauth import (
    BasicAuth,
    ForwardAuth,
    NoAuth,
    build_authenticator,
)


class FakeHandler:
    def __init__(self, headers=None, client_ip="127.0.0.1"):
        self.headers = headers or {}
        self.client_address = (client_ip, 54321)
        self.sent = None

    def _send(self, body, status=200, content_type="", extra_headers=None):
        self.sent = {"body": body, "status": status, "headers": dict(extra_headers or [])}

    def _redirect(self, location, extra_headers=None):
        self.sent = {"redirect": location, "headers": dict(extra_headers or [])}


def _basic_header(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# --- none ---

def test_no_auth_always_allows():
    assert NoAuth().identify(FakeHandler()) is not None


# --- basic ---

def test_basic_correct_credentials():
    auth = BasicAuth("admin", "s3cret")
    ident = auth.identify(FakeHandler({"Authorization": _basic_header("admin", "s3cret")}))
    assert ident is not None and ident.user == "admin"


def test_basic_wrong_password_or_missing():
    auth = BasicAuth("admin", "s3cret")
    assert auth.identify(FakeHandler({"Authorization": _basic_header("admin", "nope")})) is None
    assert auth.identify(FakeHandler({})) is None
    assert auth.identify(FakeHandler({"Authorization": "Bearer x"})) is None


def test_basic_challenge_sets_www_authenticate():
    h = FakeHandler()
    BasicAuth("admin", "s3cret").challenge(h)
    assert h.sent["status"] == 401
    assert "Basic" in h.sent["headers"]["WWW-Authenticate"]


def test_basic_from_config_requires_password():
    with pytest.raises(ValueError):
        BasicAuth.from_config({"username": "admin"})


# --- forward_auth ---

def test_forward_auth_trusted_source_with_user():
    auth = ForwardAuth(trusted_proxies=["127.0.0.1"])
    ident = auth.identify(FakeHandler({"Remote-User": "alice", "Remote-Groups": "admins,users"}, client_ip="127.0.0.1"))
    assert ident is not None and ident.user == "alice"
    assert "admins" in ident.groups


def test_forward_auth_ignores_headers_from_untrusted_source():
    auth = ForwardAuth(trusted_proxies=["127.0.0.1"])
    # spoofed header from a non-proxy IP must be rejected outright
    assert auth.identify(FakeHandler({"Remote-User": "attacker"}, client_ip="192.168.0.50")) is None


def test_forward_auth_trusted_but_no_user_header():
    auth = ForwardAuth(trusted_proxies=["127.0.0.1"])
    assert auth.identify(FakeHandler({}, client_ip="127.0.0.1")) is None


def test_forward_auth_required_groups():
    auth = ForwardAuth(trusted_proxies=["10.0.0.0/8"], required_groups=["admins"])
    ok = auth.identify(FakeHandler({"Remote-User": "a", "Remote-Groups": "admins"}, client_ip="10.1.2.3"))
    assert ok is not None
    denied = auth.identify(FakeHandler({"Remote-User": "b", "Remote-Groups": "users"}, client_ip="10.1.2.3"))
    assert denied is None


# --- factory ---

# --- end-to-end: the do_GET gate over a real server thread ---

import http.client
import threading
from http.server import ThreadingHTTPServer

from homelab_guardian import db, web
from homelab_guardian.models import HealthCheck


def _start_server(tmp_path, auth_config):
    dbp = str(tmp_path / "g.sqlite")
    conn = db.connect(dbp)
    db.save_scan(conn, {"app": "HG", "checks": [HealthCheck("a", "A", "ok", "x").to_dict()]})
    conn.close()
    # The dashboard handler loads its config on each request; give it a real file
    # in tmp_path so the test never depends on a config.yaml in the working dir
    # (CI's clean checkout has none — this was a silent cross-machine failure).
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text("app:\n  name: Homelab Guardian\n", encoding="utf-8")
    handler = type(
        "BoundH",
        (web.GuardianRequestHandler,),
        {"database_path": dbp, "config_path": str(cfgp),
         "auth": build_authenticator(auth_config), "refresh_seconds": 0},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp, body


def test_basic_auth_gates_the_dashboard(tmp_path):
    server = _start_server(tmp_path, {"web": {"auth": {"mode": "basic", "username": "admin", "password": "pw"}}})
    port = server.server_address[1]
    try:
        resp, _ = _get(port, "/")
        assert resp.status == 401 and "Basic" in (resp.getheader("WWW-Authenticate") or "")
        resp, _ = _get(port, "/healthz")  # health stays open
        assert resp.status == 200
        resp, body = _get(port, "/", headers={"Authorization": _basic_header("admin", "pw")})
        assert resp.status == 200 and "Homelab Guardian" in body
    finally:
        server.shutdown()


def test_forward_auth_gates_the_dashboard(tmp_path):
    server = _start_server(tmp_path, {"web": {"auth": {"mode": "forward_auth", "trusted_proxies": ["127.0.0.1"]}}})
    port = server.server_address[1]
    try:
        resp, _ = _get(port, "/")  # no identity header -> denied
        assert resp.status == 401
        resp, body = _get(port, "/", headers={"Remote-User": "alice"})  # trusted localhost + header -> allowed
        assert resp.status == 200 and "Homelab Guardian" in body
    finally:
        server.shutdown()


def test_serve_fails_closed_on_unresolved_oidc_secret():
    # serve() must refuse to start (return non-zero) rather than bring up a
    # dashboard whose OIDC login can't complete because the secret didn't load.
    cfg = {
        "app": {"database_path": ":memory:"},
        "web": {"auth": {"mode": "oidc", "issuer": "https://idp", "client_id": "cid",
                         "redirect_url": "http://h/cb",
                         "client_secret_env": "GUARDIAN_OIDC_CLIENT_SECRET_TESTONLY_UNSET"}},
    }
    assert web.serve(cfg) == 1


def test_build_authenticator_modes():
    assert isinstance(build_authenticator({}), NoAuth)
    assert isinstance(
        build_authenticator({"web": {"auth": {"mode": "basic", "username": "u", "password": "p"}}}),
        BasicAuth,
    )
    assert isinstance(
        build_authenticator({"web": {"auth": {"mode": "forward_auth"}}}),
        ForwardAuth,
    )
    with pytest.raises(ValueError):
        build_authenticator({"web": {"auth": {"mode": "bogus"}}})
