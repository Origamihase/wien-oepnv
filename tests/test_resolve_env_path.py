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
    assert os.getenv("CUSTOM_PATH") == "   \t   "


def test_resolve_env_path_normalizes_valid_input(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "  log/custom.log  ")
    default = Path("log/default.log")

    resolved = feed_config.resolve_env_path("CUSTOM_PATH", default)

    expected = Path("log/custom.log").resolve()
    assert resolved == expected
    assert os.getenv("CUSTOM_PATH") == "  log/custom.log  "


def test_resolve_env_path_does_not_overwrite_env_on_refresh(monkeypatch):
    monkeypatch.setenv("CUSTOM_PATH", "data/feed.rss")
    default = Path("data/default.rss")

    # First call (simulating initial load)
    resolved_1 = feed_config.resolve_env_path("CUSTOM_PATH", default)
    # Second call (simulating refresh_from_env)
    resolved_2 = feed_config.resolve_env_path("CUSTOM_PATH", default)

    expected = Path("data/feed.rss").resolve()
    assert resolved_1 == expected
    assert resolved_2 == expected
    # The environment variable should retain its original relative value
    assert os.getenv("CUSTOM_PATH") == "data/feed.rss"


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
    assert os.getenv("CUSTOM_PATH") == "../evil/outside.log"
