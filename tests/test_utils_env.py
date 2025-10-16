import logging

from pathlib import Path

import pytest

from src.utils.env import get_bool_env, load_default_env_files


def test_get_bool_env_default(monkeypatch):
    monkeypatch.delenv("BOOL_TEST", raising=False)
    assert get_bool_env("BOOL_TEST", True) is True
    assert get_bool_env("BOOL_TEST", False) is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "True", " YES ", "on", "Y", "t"],
)
def test_get_bool_env_truthy(monkeypatch, value):
    monkeypatch.setenv("BOOL_TEST", value)
    assert get_bool_env("BOOL_TEST", False) is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "False", " no ", "OFF", "n", "F"],
)
def test_get_bool_env_falsy(monkeypatch, value):
    monkeypatch.setenv("BOOL_TEST", value)
    assert get_bool_env("BOOL_TEST", True) is False


def test_get_bool_env_empty_uses_default(monkeypatch):
    monkeypatch.setenv("BOOL_TEST", "   ")
    assert get_bool_env("BOOL_TEST", True) is True
    assert get_bool_env("BOOL_TEST", False) is False


def test_get_bool_env_invalid_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("BOOL_TEST", "maybe")
    with caplog.at_level(logging.WARNING, logger="build_feed"):
        assert get_bool_env("BOOL_TEST", False) is False
    assert "Ungültiger boolescher Wert" in caplog.text


def test_load_default_env_files_uses_repo_root(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    temp_env = repo_root / "temp_test_env.env"
    temp_env.write_text("FOO=bar\n", encoding="utf-8")
    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "temp_test_env.env")
    env: dict[str, str] = {}
    try:
        loaded = load_default_env_files(environ=env, override=True)
    finally:
        temp_env.unlink(missing_ok=True)
        monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)

    assert temp_env in loaded
    assert env.get("FOO") == "bar"
