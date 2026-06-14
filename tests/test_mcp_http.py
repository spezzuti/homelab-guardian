import asyncio

from homelab_guardian.mcp_server import BearerAuthMiddleware, _resolve_http_token


class _App:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


def _drive(mw, auth_header):
    sent = []

    async def receive():
        return {}

    async def send(msg):
        sent.append(msg)

    headers = [(b"authorization", auth_header)] if auth_header is not None else []
    asyncio.run(mw({"type": "http", "headers": headers}, receive, send))
    return sent


def test_bearer_allows_correct_token():
    app = _App()
    assert _drive(BearerAuthMiddleware(app, "s3cret"), b"Bearer s3cret") == []  # passed through
    assert app.called


def test_bearer_rejects_wrong_token():
    app = _App()
    sent = _drive(BearerAuthMiddleware(app, "s3cret"), b"Bearer nope")
    assert not app.called and sent[0]["status"] == 401


def test_bearer_rejects_missing_header():
    app = _App()
    sent = _drive(BearerAuthMiddleware(app, "s3cret"), None)
    assert not app.called and sent[0]["status"] == 401


def test_bearer_passes_non_http_scopes():
    # lifespan / websocket scopes must pass through untouched.
    app = _App()

    async def receive():
        return {}

    async def send(msg):
        pass

    asyncio.run(BearerAuthMiddleware(app, "s3cret")({"type": "lifespan"}, receive, send))
    assert app.called


def test_resolve_http_token_from_env(monkeypatch):
    monkeypatch.setenv("GUARDIAN_MCP_TOKEN", "abc123")
    assert _resolve_http_token({"mcp": {"http": {"token_env": "GUARDIAN_MCP_TOKEN"}}}) == "abc123"


def test_resolve_http_token_empty_when_unconfigured():
    assert _resolve_http_token({"mcp": {"http": {}}}) == ""
    assert _resolve_http_token({}) == ""
