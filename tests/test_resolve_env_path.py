import os
from pathlib import Path

import pytest

from src.feed import config as feed_config


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    # Ensure custom environment variable is always removed after a test
    monkeypatch.delenv("CUSTOM_PATH", raising=False)


def test_resolve_env_path_uses_default_for_whitespace(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "   \t   ")
    default = Path("log/fallback.log")

    resolved = feed_config.resolve_env_path("CUSTOM_PATH", default)

    assert resolved == default
    assert os.getenv("CUSTOM_PATH") == default.as_posix()


def test_resolve_env_path_normalizes_valid_input(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "  log/custom.log  ")
    default = Path("log/default.log")

    resolved = feed_config.resolve_env_path("CUSTOM_PATH", default)

    expected = Path("log/custom.log").resolve()
    assert resolved == expected
    assert os.getenv("CUSTOM_PATH") == expected.as_posix()


def test_resolve_env_path_raises_for_invalid_without_fallback(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "../evil/outside.log")
    default = Path("log/default.log")

    with pytest.raises(ValueError):
        feed_config.resolve_env_path("CUSTOM_PATH", default)

    # Environment variable should stay untouched on failure
    assert os.getenv("CUSTOM_PATH") == "../evil/outside.log"


def test_resolve_env_path_suffix_collision_requires_fallback(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "/tmp/docs/feed.xml")
    default = Path("docs/feed.xml")

    with pytest.raises(ValueError):
        feed_config.resolve_env_path("CUSTOM_PATH", default)

    assert os.getenv("CUSTOM_PATH") == "/tmp/docs/feed.xml"


def test_resolve_env_path_falls_back_when_allowed(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "../evil/outside.log")
    default = Path("log/default.log")

    resolved = feed_config.resolve_env_path(
        "CUSTOM_PATH", default, allow_fallback=True
    )

    assert resolved == default
    assert os.getenv("CUSTOM_PATH") == default.as_posix()
