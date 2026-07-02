from homelab_guardian.collectors import homeassistant_collector


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _collect(payload, monkeypatch):
    monkeypatch.setattr(
        homeassistant_collector.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    return homeassistant_collector.collect(
        {"url": "http://ha.local", "token_env": "X"}, secrets=_Secrets()
    )


class _Secrets:
    def get(self, name):
        return "token"


def test_missing_url_is_unknown():
    checks = homeassistant_collector.collect({})
    assert checks[0].id == "ha_missing_url"
    assert checks[0].status == "unknown"


def test_all_entities_healthy_is_ok(monkeypatch):
    checks = _collect([{"entity_id": "light.a", "state": "on"}], monkeypatch)
    assert checks[0].status == "ok"


def test_unavailable_entities_flagged(monkeypatch):
    payload = [{"entity_id": f"s.{i}", "state": "unavailable"} for i in range(3)]
    checks = _collect(payload, monkeypatch)
    assert checks[0].status == "warning"
    assert checks[0].evidence["affected_count"] == 3


def test_non_dict_element_does_not_crash(monkeypatch):
    # A proxy or malformed API can slip a non-dict into the list; the collector
    # must skip it rather than raise AttributeError and lose every check.
    payload = ["not-a-dict", None, {"entity_id": "light.a", "state": "unavailable"}]
    checks = _collect(payload, monkeypatch)
    assert checks[0].status == "warning"
    assert checks[0].evidence["affected_count"] == 1
