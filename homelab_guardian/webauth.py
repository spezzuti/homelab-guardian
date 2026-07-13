from __future__ import annotations

import base64
import hmac
import ipaddress
import os
from dataclasses import dataclass, field
from typing import Any

# Pluggable authentication for the web dashboard. One small contract, several
# mechanisms — so Guardian trusts an open standard rather than carrying a list
# of supported identity providers:
#
#   none          — open (the historical behaviour; default).
#   basic         — built-in HTTP Basic username/password. Zero dependencies.
#   forward_auth  — trust identity headers from a configured upstream proxy.
#                   This one mode covers Authelia, Authentik, oauth2-proxy,
#                   Traefik/Caddy/nginx auth_request, Cloudflare Access, ... —
#                   anything that fronts the app and injects an identity header.
#   oidc          — native OpenID Connect login (see OidcAuth, added separately).
#
# Security note for forward_auth: identity headers are only trusted when the
# request's source IP is in `trusted_proxies`. Otherwise anyone who can reach
# the port directly could spoof `Remote-User` and authenticate as anyone — so a
# request from an untrusted source is denied outright, headers ignored.


@dataclass
class Identity:
    user: str
    email: str = ""
    groups: tuple[str, ...] = field(default_factory=tuple)


def _resolve_secret(env_name: str, secrets: Any) -> str | None:
    if not env_name:
        return None
    if secrets is not None and hasattr(secrets, "get"):
        value = secrets.get(env_name)
        if value:
            return value
    return os.environ.get(env_name)


class Authenticator:
    """Base contract. The web handler passes itself in so an authenticator can
    read headers / client address and write its own responses (401, redirect)."""

    # Paths this authenticator services itself (e.g. OIDC's /auth/* routes),
    # dispatched before the auth gate. Matched by exact path or prefix.
    owned_paths: tuple[str, ...] = ()

    def owns(self, path: str) -> bool:
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.owned_paths)

    def handle(self, handler: Any, path: str) -> None:  # pragma: no cover - overridden by OIDC
        handler._send("not found", status=404, content_type="text/plain; charset=utf-8")

    def identify(self, handler: Any) -> Identity | None:
        raise NotImplementedError

    def challenge(self, handler: Any) -> None:
        from homelab_guardian.web import render_auth_page  # lazy: avoids web↔auth cycle

        handler._send(
            render_auth_page("Sign-in required",
                             "This dashboard requires authentication."),
            status=401,
        )


class NoAuth(Authenticator):
    """Open access — the dashboard's historical behaviour."""

    def identify(self, handler: Any) -> Identity | None:
        return Identity(user="anonymous")


class BasicAuth(Authenticator):
    """Built-in HTTP Basic. Easiest mode; credentials ride the Authorization
    header (send over HTTPS or a trusted LAN). Compared in constant time."""

    def __init__(self, username: str, password: str, realm: str = "Homelab Guardian"):
        self.username = username
        self.password = password
        self.realm = realm

    @classmethod
    def from_config(cls, cfg: dict[str, Any], secrets: Any = None) -> "BasicAuth":
        username = str(cfg.get("username", "admin"))
        password = _resolve_secret(str(cfg.get("password_env", "")), secrets) or str(cfg.get("password", ""))
        if not password:
            raise ValueError(
                "auth mode 'basic' needs a password: set web.auth.password_env (preferred) "
                "or web.auth.password in config.yaml."
            )
        return cls(username, password, realm=str(cfg.get("realm", "Homelab Guardian")))

    def identify(self, handler: Any) -> Identity | None:
        header = handler.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return None
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        user, sep, password = decoded.partition(":")
        if not sep:
            return None
        user_ok = hmac.compare_digest(user, self.username)
        pass_ok = hmac.compare_digest(password, self.password)
        if user_ok and pass_ok:
            return Identity(user=user)
        return None

    def challenge(self, handler: Any) -> None:
        from homelab_guardian.web import render_auth_page

        handler._send(
            render_auth_page("Sign-in required",
                             "Enter the dashboard username and password when the "
                             "browser asks. Credentials live in config.yaml under "
                             "<code>web.auth</code>."),
            status=401,
            extra_headers=[("WWW-Authenticate", f'Basic realm="{self.realm}"')],
        )


class ForwardAuth(Authenticator):
    """Trust identity headers injected by an upstream proxy/IdP, but ONLY when
    the request arrives from a trusted source. Covers Authelia, Authentik,
    oauth2-proxy, Cloudflare Access, and friends with one mechanism."""

    def __init__(
        self,
        trusted_proxies: list[str],
        user_header: str = "Remote-User",
        email_header: str = "Remote-Email",
        groups_header: str = "Remote-Groups",
        required_groups: list[str] | None = None,
    ):
        self.networks = []
        for entry in trusted_proxies:
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                continue
        self.user_header = user_header
        self.email_header = email_header
        self.groups_header = groups_header
        self.required_groups = set(required_groups or [])

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "ForwardAuth":
        return cls(
            trusted_proxies=list(cfg.get("trusted_proxies", ["127.0.0.1", "::1"]) or []),
            user_header=str(cfg.get("user_header", "Remote-User")),
            email_header=str(cfg.get("email_header", "Remote-Email")),
            groups_header=str(cfg.get("groups_header", "Remote-Groups")),
            required_groups=list(cfg.get("required_groups", []) or []),
        )

    def _trusted_source(self, handler: Any) -> bool:
        try:
            client = ipaddress.ip_address(handler.client_address[0])
        except (ValueError, IndexError, TypeError):
            return False
        return any(client in net for net in self.networks)

    def identify(self, handler: Any) -> Identity | None:
        # Untrusted source: never read the headers — they could be forged.
        if not self._trusted_source(handler):
            return None
        user = handler.headers.get(self.user_header, "").strip()
        if not user:
            return None
        groups = tuple(
            g.strip() for g in handler.headers.get(self.groups_header, "").split(",") if g.strip()
        )
        if self.required_groups and not (self.required_groups & set(groups)):
            return None
        return Identity(user=user, email=handler.headers.get(self.email_header, "").strip(), groups=groups)

    def challenge(self, handler: Any) -> None:
        from homelab_guardian.web import render_auth_page

        handler._send(
            render_auth_page("Sign-in required",
                             "Reach this dashboard through your authenticating "
                             "proxy — direct requests carry no identity."),
            status=401,
        )


def build_authenticator(config: dict[str, Any], secrets: Any = None) -> Authenticator:
    """Construct the authenticator named by web.auth.mode in config."""
    auth_cfg = (config.get("web") or {}).get("auth") or {}
    mode = str(auth_cfg.get("mode", "none")).lower()
    if mode == "none":
        return NoAuth()
    if mode == "basic":
        return BasicAuth.from_config(auth_cfg, secrets)
    if mode == "forward_auth":
        return ForwardAuth.from_config(auth_cfg)
    if mode == "oidc":
        from homelab_guardian.webauth_oidc import OidcAuth

        return OidcAuth.from_config(auth_cfg, secrets)
    raise ValueError(f"Unknown web.auth.mode '{mode}' (expected none|basic|forward_auth|oidc).")
