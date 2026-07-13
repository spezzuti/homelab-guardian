from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets as secrets_mod
import threading
import time
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from homelab_guardian.webauth import Authenticator, Identity

# Native OpenID Connect login for the dashboard — works with any OIDC provider
# (Authentik, Keycloak, Zitadel, ...). Authorization-code flow with PKCE.
#
# Why no JWT signature verification (and therefore no crypto dependency): the
# id_token is fetched over a DIRECT back-channel TLS call from the server to the
# issuer's token endpoint (no browser in that hop). TLS already authenticates the
# issuer and the channel is point-to-point, so the token is trustworthy as
# received. We still validate the claims that matter for this flow — audience,
# issuer, expiry, and the nonce that binds the token to our login request. This
# is the standard, accepted simplification for a confidential client using the
# code flow; it is NOT safe for the implicit flow (front-channel tokens), which
# Guardian does not use.

SESSION_COOKIE = "guardian_session"
# Binds the callback to the browser that started the login: the CSRF `state` is
# also written here as a cookie and must match the state the IdP echoes back.
STATE_COOKIE = "guardian_oidc_state"
_SESSION_TTL = 8 * 3600
_PENDING_TTL = 600
_SCOPE = "openid profile email"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets_mod.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode (not verify) a JWT's claims. Trust is established by the TLS
    back-channel the token arrived on — see the module docstring."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("id_token is not a well-formed JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))


def validate_claims(claims: dict[str, Any], client_id: str, issuer: str, nonce: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in audiences:
        raise ValueError("id_token audience does not match client_id")
    iss = str(claims.get("iss", "")).rstrip("/")
    if iss != issuer.rstrip("/"):
        raise ValueError("id_token issuer mismatch")
    if float(claims.get("exp", 0)) <= now:
        raise ValueError("id_token has expired")
    if nonce and claims.get("nonce") != nonce:
        raise ValueError("id_token nonce mismatch")


class OidcAuth(Authenticator):
    owned_paths = ("/auth/login", "/auth/callback", "/auth/logout")

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_url: str,
        required_groups: list[str] | None = None,
        cookie_secure: bool = False,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_url = redirect_url
        self.required_groups = set(required_groups or [])
        self.cookie_secure = cookie_secure
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[Identity, float]] = {}
        self._pending: dict[str, tuple[str, str, float]] = {}  # state -> (nonce, verifier, expiry)
        self._discovery: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any], secrets: Any = None) -> "OidcAuth":
        issuer = str(cfg.get("issuer", "")).strip()
        client_id = str(cfg.get("client_id", "")).strip()
        redirect_url = str(cfg.get("redirect_url", "")).strip()
        secret_env = str(cfg.get("client_secret_env", ""))
        client_secret = (secrets.get(secret_env) if secrets is not None and hasattr(secrets, "get") and secret_env else None) or os.environ.get(secret_env, "")
        missing = [k for k, v in (("issuer", issuer), ("client_id", client_id), ("redirect_url", redirect_url)) if not v]
        # Fail closed on a *configured-but-unresolved* client secret. If the admin
        # named a secret env (client_secret_env) but it came back empty, the secrets
        # backend was almost certainly unreachable at startup (e.g. a transient bws
        # DNS failure). Starting anyway serves a login whose token exchange can only
        # fail — a silent half-broken dashboard. Refusing to start instead surfaces
        # the problem loudly and lets the supervisor (systemd) retry once the vault
        # is reachable. A genuinely public client simply leaves client_secret_env
        # unset, and this check does not apply.
        if secret_env and not client_secret:
            missing.append(f"client secret (env {secret_env} resolved empty — is the secrets backend reachable?)")
        if missing:
            raise ValueError(f"auth mode 'oidc' is missing required config: {', '.join(missing)}")
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_url=redirect_url,
            required_groups=list(cfg.get("required_groups", []) or []),
            cookie_secure=bool(cfg.get("cookie_secure", False)),
        )

    # --- discovery -------------------------------------------------------

    def _discover(self) -> dict[str, Any]:
        if self._discovery is None:
            resp = requests.get(self.issuer + "/.well-known/openid-configuration", timeout=10)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    # --- session store ---------------------------------------------------

    def _prune(self, now: float) -> None:
        for store in (self._sessions, self._pending):
            for key in [k for k, v in store.items() if v[-1] <= now]:
                store.pop(key, None)

    def _session_identity(self, handler: Any) -> Identity | None:
        cookie_header = handler.headers.get("Cookie", "")
        if not cookie_header:
            return None
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
        except Exception:
            return None
        morsel = jar.get(SESSION_COOKIE)
        if morsel is None:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            entry = self._sessions.get(morsel.value)
            return entry[0] if entry else None

    # --- Authenticator contract -----------------------------------------

    def identify(self, handler: Any) -> Identity | None:
        return self._session_identity(handler)

    def challenge(self, handler: Any) -> None:
        handler._redirect("/auth/login")

    def handle(self, handler: Any, path: str) -> None:
        if path == "/auth/login":
            self._login(handler)
        elif path == "/auth/callback":
            self._callback(handler)
        elif path == "/auth/logout":
            self._logout(handler)
        else:  # pragma: no cover
            handler._send("not found", status=404, content_type="text/plain; charset=utf-8")

    # --- routes ----------------------------------------------------------

    def _login(self, handler: Any) -> None:
        try:
            authorize = self._discover()["authorization_endpoint"]
        except Exception as exc:  # discovery/network failure
            from homelab_guardian.web import render_auth_page  # lazy: avoids web↔auth cycle

            handler._send(
                render_auth_page(
                    "Sign-in unavailable",
                    "Guardian could not reach the identity provider "
                    f"({html.escape(str(exc))}). Check that it is up and that "
                    "<code>web.auth.issuer</code> in config.yaml points at it.",
                    link_href="/auth/login", link_label="Try Again",
                ),
                status=502,
            )
            return
        state = secrets_mod.token_urlsafe(24)
        nonce = secrets_mod.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        with self._lock:
            self._pending[state] = (nonce, verifier, time.time() + _PENDING_TTL)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_url,
            "scope": _SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        handler._redirect(
            authorize + ("&" if "?" in authorize else "?") + urlencode(params),
            extra_headers=[("Set-Cookie", self._state_cookie(state))],
        )

    def _callback(self, handler: Any) -> None:
        query = parse_qs(urlparse(handler.path).query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        # Bind the callback to the browser that began the login: the state we set
        # as a cookie at /auth/login must match the state the IdP echoed back.
        # Without this, an attacker-initiated login's valid state+code could be
        # replayed in a victim's browser to log them into the attacker's account
        # (login CSRF / session fixation). Constant-time compared.
        cookie_state = self._cookie_value(handler, STATE_COOKIE)
        with self._lock:
            self._prune(time.time())
            pending = self._pending.pop(state, None)
        state_ok = bool(state and cookie_state and hmac.compare_digest(cookie_state, state))
        if not code or pending is None or not state_ok:
            from homelab_guardian.web import render_auth_page

            handler._send(
                render_auth_page(
                    "Sign-in expired",
                    "The sign-in attempt expired or did not match this browser — "
                    "this happens when the login page sits open too long.",
                    link_href="/auth/login", link_label="Sign In Again",
                ),
                status=400,
            )
            return
        nonce, verifier, _ = pending
        try:
            token_endpoint = self._discover()["token_endpoint"]
            resp = requests.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_url,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": verifier,
                },
                timeout=10,
            )
            resp.raise_for_status()
            id_token = resp.json()["id_token"]
            claims = decode_jwt_payload(id_token)
            validate_claims(claims, self.client_id, self.issuer, nonce)
        except Exception as exc:
            from homelab_guardian.web import render_auth_page

            handler._send(
                render_auth_page(
                    "Sign-in failed",
                    f"The identity provider rejected the sign-in: {html.escape(str(exc))}.",
                    link_href="/auth/login", link_label="Try Again",
                ),
                status=400,
            )
            return

        groups = tuple(claims.get("groups", []) or [])
        if self.required_groups and not (self.required_groups & set(groups)):
            from homelab_guardian.web import render_auth_page

            handler._send(
                render_auth_page(
                    "Not allowed",
                    "Your account is not in a group this dashboard allows. "
                    "Add it to an allowed group in the identity provider, then sign in again.",
                    link_href="/auth/login", link_label="Sign In Again",
                ),
                status=403,
            )
            return

        identity = Identity(
            user=str(claims.get("preferred_username") or claims.get("email") or claims.get("sub", "user")),
            email=str(claims.get("email", "")),
            groups=groups,
        )
        session_id = secrets_mod.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = (identity, time.time() + _SESSION_TTL)
        handler._redirect("/", extra_headers=[
            ("Set-Cookie", f"{STATE_COOKIE}=; Max-Age=0; Path=/"),  # single-use state, clear it
            ("Set-Cookie", self._cookie(session_id)),
        ])

    def _logout(self, handler: Any) -> None:
        cookie_header = handler.headers.get("Cookie", "")
        jar = SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            jar = SimpleCookie()
        morsel = jar.get(SESSION_COOKIE)
        if morsel is not None:
            with self._lock:
                self._sessions.pop(morsel.value, None)
        handler._redirect("/", extra_headers=[("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/")])

    def _cookie(self, session_id: str) -> str:
        cookie = f"{SESSION_COOKIE}={session_id}; HttpOnly; Path=/; SameSite=Lax"
        if self.cookie_secure:
            cookie += "; Secure"
        return cookie

    def _state_cookie(self, state: str) -> str:
        # SameSite=Lax so it still rides the top-level GET redirect back from the
        # IdP; short-lived, matching the pending-login TTL.
        cookie = f"{STATE_COOKIE}={state}; HttpOnly; Path=/; Max-Age={_PENDING_TTL}; SameSite=Lax"
        if self.cookie_secure:
            cookie += "; Secure"
        return cookie

    @staticmethod
    def _cookie_value(handler: Any, name: str) -> str | None:
        cookie_header = handler.headers.get("Cookie", "")
        if not cookie_header:
            return None
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
        except Exception:
            return None
        morsel = jar.get(name)
        return morsel.value if morsel is not None else None
