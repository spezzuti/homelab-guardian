import json
import subprocess

import pytest

from app.secrets import BitwardenSecretStore, EnvSecretStore, build_store


def _completed(stdout: str = "[]", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["bws"], returncode=returncode, stdout=stdout, stderr=stderr)


def _bws_payload(**secrets: str) -> str:
    return json.dumps([{"id": f"id-{k}", "key": k, "value": v} for k, v in secrets.items()])


@pytest.fixture
def token_env(monkeypatch):
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "0.test-token")


def test_env_store_reads_environment(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "value")
    store = EnvSecretStore()
    assert store.get("MY_SECRET") == "value"
    assert store.get("MISSING") is None
    assert store.get("") is None


def test_build_store_defaults_to_env():
    assert isinstance(build_store({}), EnvSecretStore)
    assert isinstance(build_store(None), EnvSecretStore)
    assert isinstance(build_store({"provider": "nonsense"}), EnvSecretStore)


def test_build_store_bitwarden():
    store = build_store({"provider": "bitwarden", "bitwarden": {"project_id": "p1"}})
    assert isinstance(store, BitwardenSecretStore)
    assert store.project_id == "p1"


def test_bitwarden_resolves_from_cli(token_env):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _completed(_bws_payload(HASS_TOKEN="abc123"))

    store = BitwardenSecretStore(runner=runner)
    assert store.get("HASS_TOKEN") == "abc123"
    assert calls[0][:3] == ["bws", "secret", "list"]


def test_environment_wins_over_bitwarden(token_env, monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "from-env")
    store = BitwardenSecretStore(runner=lambda *a, **k: _completed(_bws_payload(HASS_TOKEN="from-bws")))
    assert store.get("HASS_TOKEN") == "from-env"


def test_cache_prevents_repeated_cli_calls(token_env):
    calls = []
    clock_value = [0.0]

    def runner(command, **kwargs):
        calls.append(command)
        return _completed(_bws_payload(A="1"))

    store = BitwardenSecretStore(runner=runner, cache_seconds=300, clock=lambda: clock_value[0])
    store.get("A")
    store.get("A")
    assert len(calls) == 1
    clock_value[0] = 301.0
    store.get("A")
    assert len(calls) == 2


def test_missing_access_token_degrades(capsys):
    store = BitwardenSecretStore(access_token_env="DEFINITELY_NOT_SET_VAR")
    assert store.get("ANYTHING") is None
    assert store.degraded
    assert "Falling back to environment" in capsys.readouterr().out


def test_missing_cli_degrades(token_env, capsys):
    def runner(command, **kwargs):
        raise FileNotFoundError("bws not found")

    store = BitwardenSecretStore(runner=runner)
    assert store.get("ANYTHING") is None
    assert store.degraded


def test_cli_error_degrades_and_warns_once(token_env, capsys):
    store = BitwardenSecretStore(runner=lambda *a, **k: _completed("", returncode=1, stderr="bad token"))
    assert store.get("A") is None
    assert store.get("B") is None
    assert capsys.readouterr().out.count("Falling back") == 1


def test_project_id_passed_to_cli(token_env):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _completed("[]")

    store = BitwardenSecretStore(project_id="proj-123", runner=runner)
    store.get("A")
    assert calls[0][-1] == "proj-123"


def test_preload_reports_count(token_env):
    store = BitwardenSecretStore(runner=lambda *a, **k: _completed(_bws_payload(A="1", B="2")))
    assert store.preload() == 2
    assert not store.degraded
