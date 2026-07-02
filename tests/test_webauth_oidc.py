import base64
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

import homelab_guardian.webauth_oidc as oidc
from homelab_guardian.webauth import Identity
from homelab_guardian.webauth_oidc import (
    OidcAuth,
    decode_jwt_payload,
    pkce_pair,
    validate_claims,
)


class FakeHandler:
    def __init__(self, headers=None, path="/"):
        self.headers = headers or {}
        self.path = path
        self.client_address = ("127.0.0.1", 5)
        self.sent = None

    def _send(self, body, status=200, content_type="", extra_headers=None):
        self.sent = {"body": body, "status": status, "headers": dict(extra_headers or [])}

    def _redirect(self, location, extra_headers=None):
        self.sent = {"redirect": location, "headers": dict(extra_headers or [])}


def _jwt(claims: dict) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


def test_pkce_pair_is_s256():
    verifier, challenge = pkce_pair()
    assert verifier != challenge
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_decode_jwt_payload():
    claims = decode_jwt_payload(_jwt({"sub": "abc", "email": "a@b.c"}))
    assert claims["sub"] == "abc" and claims["email"] == "a@b.c"


def test_validate_claims_accepts_good_and_rejects_bad():
    good = {"aud": "cid", "iss": "https://idp/", "exp": time.time() + 60, "nonce": "n"}
    validate_claims(good, "cid", "https://idp", "n")  # trailing-slash tolerant
    with pytest.raises(ValueError):
        validate_claims({**good, "aud": "other"}, "cid", "https://idp", "n")
    with pytest.raises(ValueError):
        validate_claims({**good, "exp": time.time() - 1}, "cid", "https://idp", "n")
    with pytest.raises(ValueError):
        validate_claims({**good, "nonce": "wrong"}, "cid", "https://idp", "n")


def test_from_config_requires_core_fields():
    with pytest.raises(ValueError):
        OidcAuth.from_config({"client_id": "x"})  # missing issuer + redirect_url


_FULL_CFG = {
    "issuer": "https://idp",
    "client_id": "cid",
    "redirect_url": "http://host/auth/callback",
    "client_secret_env": "GUARDIAN_OIDC_CLIENT_SECRET",
}


def test_from_config_fails_closed_when_secret_unresolved():
    # client_secret_env is configured but neither secrets store nor env has it
    # (e.g. the vault was unreachable at startup) -> refuse, don't serve broken login.
    with pytest.raises(ValueError, match="client secret"):
        OidcAuth.from_config(_FULL_CFG, secrets=None)


def test_from_config_accepts_resolved_secret():
    class _S:
        def get(self, k):
            return "resolved" if k == "GUARDIAN_OIDC_CLIENT_SECRET" else None

    auth = OidcAuth.from_config(_FULL_CFG, secrets=_S())
    assert auth.client_secret == "resolved"


def test_from_config_public_client_without_secret_env_is_allowed():
    # No client_secret_env set -> a public/PKCE client; the secret check doesn't apply.
    cfg = {k: v for k, v in _FULL_CFG.items() if k != "client_secret_env"}
    auth = OidcAuth.from_config(cfg, secrets=None)
    assert auth.client_secret == ""


def _auth() -> OidcAuth:
    return OidcAuth(
        issuer="https://idp",
        client_id="cid",
        client_secret="shh",
        redirect_url="http://host/auth/callback",
    )


def test_session_identify_and_expiry():
    auth = _auth()
    auth._sessions["sid"] = (Identity(user="alice"), time.time() + 60)
    h = FakeHandler(headers={"Cookie": "guardian_session=sid"})
    assert auth.identify(h).user == "alice"
    # expired session
    auth._sessions["sid"] = (Identity(user="alice"), time.time() - 1)
    assert auth.identify(FakeHandler(headers={"Cookie": "guardian_session=sid"})) is None
    # no cookie
    assert auth.identify(FakeHandler()) is None


def test_challenge_redirects_to_login():
    h = FakeHandler()
    _auth().challenge(h)
    assert h.sent["redirect"] == "/auth/login"


def test_callback_without_matching_state_cookie_is_rejected():
    # Login-CSRF guard: a valid pending state + code must still be refused if the
    # browser doesn't present the matching state cookie set at /auth/login.
    auth = _auth()
    import time as _t

    auth._pending["STATE"] = ("n", "v", _t.time() + 60)
    h = FakeHandler(path="/auth/callback?code=abc&state=STATE")  # no cookie
    auth._callback(h)
    assert h.sent["status"] == 400


def test_login_sets_state_cookie():
    import homelab_guardian.webauth_oidc as oidc

    auth = _auth()
    auth._discovery = {"authorization_endpoint": "https://idp/authorize"}
    h = FakeHandler(path="/auth/login")
    auth._login(h)
    cookie = h.sent["headers"]["Set-Cookie"]
    assert cookie.startswith(f"{oidc.STATE_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_callback_unknown_state_is_rejected():
    h = FakeHandler(path="/auth/callback?code=x&state=unknown")
    _auth()._callback(h)
    assert h.sent["status"] == 400


def test_callback_happy_path_sets_session(monkeypatch):
    auth = _auth()
    auth._discovery = {"token_endpoint": "https://idp/token", "authorization_endpoint": "https://idp/auth"}
    # seed a pending login
    nonce, verifier = "nonce123", "verifier"
    auth._pending["STATE"] = (nonce, verifier, time.time() + 60)
    id_token = _jwt({
        "aud": "cid", "iss": "https://idp", "exp": time.time() + 60, "nonce": nonce,
        "preferred_username": "bob", "email": "bob@x.y", "groups": ["admins"],
    })
    fake_resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id_token": id_token})
    monkeypatch.setattr(oidc, "requests", SimpleNamespace(post=lambda *a, **k: fake_resp))

    h = FakeHandler(path="/auth/callback?code=abc&state=STATE",
                    headers={"Cookie": "guardian_oidc_state=STATE"})
    auth._callback(h)
    assert h.sent["redirect"] == "/"
    cookie = h.sent["headers"]["Set-Cookie"]
    assert cookie.startswith("guardian_session=") and "HttpOnly" in cookie
    # the new session authenticates a follow-up request
    sid = cookie.split("guardian_session=")[1].split(";")[0]
    assert auth.identify(FakeHandler(headers={"Cookie": f"guardian_session={sid}"})).user == "bob"


def test_callback_required_group_enforced(monkeypatch):
    auth = OidcAuth("https://idp", "cid", "shh", "http://host/cb", required_groups=["admins"])
    auth._discovery = {"token_endpoint": "https://idp/token"}
    auth._pending["S"] = ("n", "v", time.time() + 60)
    id_token = _jwt({"aud": "cid", "iss": "https://idp", "exp": time.time() + 60, "nonce": "n", "groups": ["users"]})
    fake_resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id_token": id_token})
    monkeypatch.setattr(oidc, "requests", SimpleNamespace(post=lambda *a, **k: fake_resp))
    h = FakeHandler(path="/auth/callback?code=abc&state=S",
                    headers={"Cookie": "guardian_oidc_state=S"})
    auth._callback(h)
    assert h.sent["status"] == 403
