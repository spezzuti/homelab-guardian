import os

from homelab_guardian.main import load_dotenv


def test_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / ".env") == 0


def test_loads_values_without_overriding(tmp_path, monkeypatch):
    monkeypatch.setenv("ALREADY_SET", "original")
    monkeypatch.delenv("DOTENV_NEW_VALUE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "ALREADY_SET=should-not-win\n"
        "DOTENV_NEW_VALUE='quoted value'\n"
        "MALFORMED LINE\n",
        encoding="utf-8",
    )
    loaded = load_dotenv(env_file)
    assert loaded == 1
    assert os.environ["ALREADY_SET"] == "original"
    assert os.environ["DOTENV_NEW_VALUE"] == "quoted value"
    monkeypatch.delenv("DOTENV_NEW_VALUE", raising=False)
