# Dashboard authentication

`guardian serve` can gate the dashboard behind authentication. `/healthz` stays
open so uptime monitors don't need credentials. The mode is chosen in
`config.yaml` under `web.auth.mode`; default is `none` (open — unchanged from
before). Options are the point — pick what fits your stack.

The design principle mirrors the MCP server: **Guardian implements mechanisms,
not a list of named providers.** `forward_auth` alone covers Authelia, Authentik,
oauth2-proxy, Cloudflare Access, and the rest of the forward-auth ecosystem;
`oidc` covers any OpenID Connect IdP. No per-provider code.

## `basic` — built-in username/password

Easiest mode, zero dependencies. Send over HTTPS or a trusted LAN.

```yaml
web:
  auth:
    mode: basic
    username: admin
    password_env: GUARDIAN_WEB_PASSWORD   # set in .env; never put the password in config.yaml
```

`password:` (plaintext) is accepted as a fallback but discouraged. Credentials
are compared in constant time.

## `forward_auth` — trust an upstream proxy/IdP

For Authelia, Authentik, oauth2-proxy, Traefik/Caddy/nginx `auth_request`,
Cloudflare Access, Tailscale-serve — anything that authenticates in front and
injects identity headers.

```yaml
web:
  auth:
    mode: forward_auth
    trusted_proxies: ["127.0.0.1", "::1"]   # the proxy's source IP(s) / CIDRs
    user_header: Remote-User                # Authelia/Authentik default
    email_header: Remote-Email
    groups_header: Remote-Groups
    required_groups: []                     # optional allow-list
```

**Security:** identity headers are trusted **only** when the request's source IP
is in `trusted_proxies`. A request from anywhere else is denied and its headers
ignored — otherwise anyone reaching the port directly could spoof `Remote-User`.
Bind Guardian so only the proxy can reach it (localhost, or a private network the
proxy fronts), and list exactly the proxy's IP.

## `oidc` — native OpenID Connect login

Direct login against any OIDC provider (Authentik, Keycloak, Zitadel, …) with no
forward-auth proxy in front. Authorization-code flow with PKCE.

```yaml
web:
  auth:
    mode: oidc
    issuer: "https://auth.example.com/application/o/guardian/"
    client_id: "homelab-guardian"
    client_secret_env: GUARDIAN_OIDC_CLIENT_SECRET   # set in .env
    redirect_url: "http://192.168.50.10:8674/auth/callback"
    required_groups: []        # optional; needs the IdP to send a `groups` claim
    cookie_secure: false       # set true when served over HTTPS
```

Routes added: `/auth/login`, `/auth/callback`, `/auth/logout`. Sessions are an
in-memory cookie store (8h TTL; cleared on process restart — users just log in
again).

**Setup (Authentik example):** create an OAuth2/OpenID *Provider* with
redirect URI `http://<host>:8674/auth/callback`, note the client id + secret,
attach it to an *Application*, and use the application's OIDC issuer URL as
`issuer`. Keycloak/Zitadel are the same shape (create a confidential client,
copy issuer/client_id/secret).

**On JWT verification:** Guardian does not verify the `id_token` signature
locally (so it needs no crypto dependency). It's safe here because the token is
fetched over a **direct back-channel TLS call** to the issuer's token endpoint —
TLS authenticates the issuer and the channel is point-to-point. Guardian still
validates audience, issuer, expiry, and the login nonce. This is standard for a
confidential client using the code flow; it would **not** be safe for the
implicit flow, which Guardian does not use.

## Reuse

This same authenticator layer is what the future HTTP MCP transport will sit
behind — build the auth once, serve both the dashboard and a network MCP
endpoint with it.
