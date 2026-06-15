import pytest

from homelab_guardian.config import deep_merge, load_config


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_non_mapping_root_raises(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(config)


def test_empty_file_yields_defaults(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("", encoding="utf-8")
    loaded = load_config(config)
    assert loaded["app"]["name"] == "Homelab Guardian"
    assert loaded["collectors"]["docker"]["enabled"] is False
    assert loaded["notifications"]["telegram"]["enabled"] is False


def test_override_merges_deeply(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "collectors:\n  docker:\n    enabled: true\n", encoding="utf-8"
    )
    loaded = load_config(config)
    assert loaded["collectors"]["docker"]["enabled"] is True
    # sibling defaults survive the override
    assert loaded["collectors"]["docker"]["socket_url"] == "unix:///var/run/docker.sock"
    # collectors are opt-in: network stays off until a config enables it
    assert loaded["collectors"]["network"]["enabled"] is False


def test_deep_merge_replaces_non_dict_values():
    base = {"a": {"b": 1, "c": [1, 2]}}
    merged = deep_merge(base, {"a": {"c": [3]}})
    assert merged == {"a": {"b": 1, "c": [3]}}
    assert base["a"]["c"] == [1, 2]
