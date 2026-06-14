from homelab_guardian.alerting import AlertEvents
from homelab_guardian.models import HealthCheck
from homelab_guardian.notifications import agent_notifier


def _check(check_id, status="critical", summary="down", **kw):
    return HealthCheck(check_id, check_id.title(), status, summary, **kw)


def _event(check_id="svc_a", to="critical", frm="ok"):
    return {
        "id": check_id, "name": check_id.title(), "previous_status": frm,
        "current_status": to, "summary": f"{check_id} is {to}", "scans_observed": 2,
    }


class _Resp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _Poster:
    """Records POST calls and returns a canned response (or raises)."""
    def __init__(self, response=None, raise_exc=None):
        self.response = response or _Resp()
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def _install(monkeypatch, poster):
    monkeypatch.setattr(agent_notifier.requests, "post", poster)


# --- payload + helpers -----------------------------------------------------

def test_has_critical():
    assert agent_notifier.has_critical(AlertEvents(confirmed=[_event(to="critical")]))
    assert not agent_notifier.has_critical(AlertEvents(confirmed=[_event(to="warning")]))
    assert not agent_notifier.has_critical(AlertEvents(recovered=[_event(to="critical")]))


def test_build_payload_enriches_with_evidence_and_action():
    checks = [_check("svc_a", "critical", "down", evidence={"port": 443},
                     recommended_action="Restart it", group="Applications")]
    payload = agent_notifier.build_payload(checks, AlertEvents(confirmed=[_event("svc_a")]), scan_id=7)
    assert payload["source"] == "homelab-guardian"
    assert payload["scan_id"] == 7
    item = payload["confirmed"][0]
    assert item["current_status"] == "critical"
    assert item["evidence"] == {"port": 443}
    assert item["recommended_action"] == "Restart it"
    assert item["group"] == "Applications"


def test_build_payload_surfaces_escalations():
    checks = [_check("svc_a", "critical", "down")]
    auto = [
        {"check_id": "svc_a", "action": "restart", "status": "executed",
         "recovered": False, "escalate": "reflex_failed", "summary": "still down"},
        {"check_id": "svc_b", "action": "restart", "status": "executed",
         "recovered": True, "escalate": None, "summary": "back"},
    ]
    payload = agent_notifier.build_payload(
        checks, AlertEvents(confirmed=[_event("svc_a")]), scan_id=1, auto_repairs=auto)
    assert len(payload["auto_repaired"]) == 2
    # Only the unrecovered one is escalated to the top level.
    assert [e["check_id"] for e in payload["escalations"]] == ["svc_a"]


def test_build_payload_no_escalations_key_when_all_recovered():
    auto = [{"check_id": "svc_b", "action": "restart", "status": "executed",
             "recovered": True, "escalate": None, "summary": "back"}]
    payload = agent_notifier.build_payload([], AlertEvents(), scan_id=1, auto_repairs=auto)
    assert "escalations" not in payload


# --- delivery --------------------------------------------------------------

def test_notify_disabled_does_not_post(monkeypatch):
    poster = _Poster()
    _install(monkeypatch, poster)
    assert agent_notifier.notify({"enabled": False, "webhook_url": "http://x"},
                                 [_check("a")], AlertEvents(confirmed=[_event()]), 1) is False
    assert poster.calls == []


def test_notify_nothing_to_send_returns_false_without_posting(monkeypatch):
    poster = _Poster()
    _install(monkeypatch, poster)
    # send_on problems + only a recovery → nothing worth pushing
    result = agent_notifier.notify(
        {"enabled": True, "webhook_url": "http://x", "send_on": "problems"},
        [_check("a", "ok")], AlertEvents(recovered=[_event(to="ok", frm="critical")]), 1)
    assert result is False
    assert poster.calls == []


def test_notify_delivers_and_posts_payload(monkeypatch):
    poster = _Poster()
    _install(monkeypatch, poster)
    ok = agent_notifier.notify(
        {"enabled": True, "webhook_url": "http://agent/hook"},
        [_check("svc_a")], AlertEvents(confirmed=[_event("svc_a")]), 5)
    assert ok is True
    assert len(poster.calls) == 1
    assert poster.calls[0]["url"] == "http://agent/hook"
    import json
    sent = json.loads(poster.calls[0]["data"])  # signed bytes, sent as data=
    assert sent["confirmed"][0]["id"] == "svc_a"
    assert poster.calls[0]["headers"]["Content-Type"] == "application/json"


def test_notify_missing_url_returns_false(monkeypatch):
    poster = _Poster()
    _install(monkeypatch, poster)
    assert agent_notifier.notify({"enabled": True}, [_check("a")],
                                 AlertEvents(confirmed=[_event()]), 1) is False
    assert poster.calls == []


def test_notify_http_error_returns_false(monkeypatch):
    _install(monkeypatch, _Poster(response=_Resp(status_code=502, text="bad gateway")))
    assert agent_notifier.notify({"enabled": True, "webhook_url": "http://x"},
                                 [_check("a")], AlertEvents(confirmed=[_event()]), 1) is False


def test_notify_network_error_returns_false(monkeypatch):
    import requests
    _install(monkeypatch, _Poster(raise_exc=requests.exceptions.ConnectionError("refused")))
    assert agent_notifier.notify({"enabled": True, "webhook_url": "http://x"},
                                 [_check("a")], AlertEvents(confirmed=[_event()]), 1) is False


def test_notify_adds_auth_header_from_token_env(monkeypatch):
    poster = _Poster()
    _install(monkeypatch, poster)

    class _Secrets:
        def get(self, name):
            return "s3cr3t" if name == "AGENT_TOKEN" else ""

    agent_notifier.notify(
        {"enabled": True, "webhook_url": "http://x", "token_env": "AGENT_TOKEN"},
        [_check("a")], AlertEvents(confirmed=[_event()]), 1, secrets=_Secrets())
    assert poster.calls[0]["headers"]["Authorization"] == "Bearer s3cr3t"


def test_notify_signs_body_with_hmac(monkeypatch):
    import hashlib
    import hmac as hmac_mod
    poster = _Poster()
    _install(monkeypatch, poster)

    class _Secrets:
        def get(self, name):
            return "whsecret" if name == "WH_SECRET" else ""

    agent_notifier.notify(
        {"enabled": True, "webhook_url": "http://x", "hmac_secret_env": "WH_SECRET",
         "event_type": "guardian.health_change"},
        [_check("a")], AlertEvents(confirmed=[_event()]), 1, secrets=_Secrets())
    call = poster.calls[0]
    expected = "sha256=" + hmac_mod.new(b"whsecret", call["data"].encode(), hashlib.sha256).hexdigest()
    assert call["headers"]["X-Hub-Signature-256"] == expected
    assert call["headers"]["X-GitHub-Event"] == "guardian.health_change"


# --- dispatcher: mode switch + critical-fallback ---------------------------

def _spy(monkeypatch):
    """Record telegram + agent notify calls without sending anything."""
    from homelab_guardian import main
    calls = {"telegram": [], "agent": []}
    monkeypatch.setattr(main.telegram_notifier, "notify",
                        lambda *a, **kw: calls["telegram"].append(kw) or True)
    monkeypatch.setattr(agent_notifier, "notify",
                        lambda *a, **kw: calls["agent_delivered"])
    calls["agent_delivered"] = True
    return calls


def _dispatch(config, checks, events, db_path):
    config.setdefault("app", {})["database_path"] = str(db_path)
    from homelab_guardian import main
    main._dispatch_notifications(config, checks, events, 1, None)


def _pending_ids(db_path):
    from homelab_guardian import db
    return [p["check_id"] for p in db.list_pending_alerts(db.connect(str(db_path)))]


def test_direct_mode_uses_telegram_only(monkeypatch, tmp_path):
    calls = _spy(monkeypatch)
    _dispatch({"notifications": {"telegram": {"enabled": True}}},
              [_check("a", "critical")], AlertEvents(confirmed=[_event()]), tmp_path / "g.sqlite")
    assert len(calls["telegram"]) == 1
    assert calls["telegram"][0].get("force") is None  # normal send, not forced


def test_agent_mode_delivered_records_pending_no_fallback(monkeypatch, tmp_path):
    calls = _spy(monkeypatch)
    calls["agent_delivered"] = True
    dbp = tmp_path / "g.sqlite"
    _dispatch({"notifications": {"mode": "agent", "agent": {"enabled": True},
                                 "telegram": {"enabled": True}}},
              [_check("a", "critical")], AlertEvents(confirmed=[_event("svc_a", to="critical")]), dbp)
    assert calls["telegram"] == []           # agent took it; Guardian stays quiet
    assert _pending_ids(dbp) == ["svc_a"]    # but the critical now awaits the agent's ack


def test_agent_mode_critical_undelivered_triggers_fallback(monkeypatch, tmp_path):
    calls = _spy(monkeypatch)
    calls["agent_delivered"] = False  # agent unreachable
    dbp = tmp_path / "g.sqlite"
    _dispatch({"notifications": {"mode": "agent", "agent": {"enabled": True},
                                 "telegram": {"enabled": True}}},
              [_check("a", "critical")], AlertEvents(confirmed=[_event(to="critical")]), dbp)
    assert len(calls["telegram"]) == 1
    assert calls["telegram"][0]["force"] is True
    assert "agent unreachable" in calls["telegram"][0]["prefix"]
    assert _pending_ids(dbp) == []  # delivery failed → immediate fallback, nothing pending


def test_agent_mode_noncritical_undelivered_no_fallback(monkeypatch, tmp_path):
    calls = _spy(monkeypatch)
    calls["agent_delivered"] = False
    _dispatch({"notifications": {"mode": "agent", "agent": {"enabled": True},
                                 "telegram": {"enabled": True}}},
              [_check("a", "warning")], AlertEvents(confirmed=[_event(to="warning")]), tmp_path / "g.sqlite")
    assert calls["telegram"] == []  # only criticals fall back


def test_agent_mode_fallback_disabled(monkeypatch, tmp_path):
    calls = _spy(monkeypatch)
    calls["agent_delivered"] = False
    _dispatch({"notifications": {"mode": "agent",
                                 "agent": {"enabled": True, "critical_fallback": False},
                                 "telegram": {"enabled": True}}},
              [_check("a", "critical")], AlertEvents(confirmed=[_event(to="critical")]), tmp_path / "g.sqlite")
    assert calls["telegram"] == []  # fallback opted out
