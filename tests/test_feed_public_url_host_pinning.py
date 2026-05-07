"""Verify that FEED_LINK / PAGES_BASE_URL env overrides only accept GitHub-hosted hosts.

Both env vars are interpolated into the public RSS feed (channel ``<link>``,
per-item ``<link>`` fallback, atom self/alternate hrefs). Without a host pin,
an env override would weaponise the published feed as a phishing redirect for
every subscriber. Mirrors ``test_provider_url_host_pinning`` for WL/ÖBB.
"""

from __future__ import annotations

import logging

import pytest

from src.feed import config as feed_config


def _refresh(monkeypatch: pytest.MonkeyPatch, var: str, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(var, raising=False)
    else:
        monkeypatch.setenv(var, value)
    feed_config.refresh_from_env()


def test_validated_feed_public_url_accepts_github_com() -> None:
    assert feed_config._validated_feed_public_url("https://github.com/foo/bar")


def test_validated_feed_public_url_accepts_github_io_subdomain() -> None:
    assert feed_config._validated_feed_public_url("https://example.github.io/repo")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/feed",
        "https://gihub.com/foo/bar",  # typosquat
        "https://github.com.evil.com/foo",  # suffix attack on github.com
        "https://example.github.io.evil.com/foo",  # suffix attack on github.io
        "https://evil-github.io/foo",  # missing leading dot
        "https://github.io/foo",  # bare apex without subdomain
    ],
)
def test_validated_feed_public_url_rejects_untrusted(url: str) -> None:
    assert feed_config._validated_feed_public_url(url) is None


def test_feed_link_falls_back_to_default_on_untrusted_host(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="src.feed.config")
    try:
        _refresh(monkeypatch, "FEED_LINK", "https://evil.example.com/wien-oepnv")
        # Falls back to the default; the public feed link is NOT attacker-controlled.
        assert feed_config.FEED_LINK == "https://github.com/Origamihase/wien-oepnv"
        assert any(
            "is not a known GitHub host" in record.getMessage()
            for record in caplog.records
        )
    finally:
        _refresh(monkeypatch, "FEED_LINK", None)


def test_pages_base_url_falls_back_to_default_on_untrusted_host(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="src.feed.config")
    try:
        _refresh(monkeypatch, "PAGES_BASE_URL", "https://evil.example.com/wien-oepnv")
        # Falls back to the default; atom self/alternate hrefs are NOT attacker-controlled.
        assert feed_config.PAGES_BASE_URL == "https://origamihase.github.io/wien-oepnv"
        assert any(
            "is not a known GitHub host" in record.getMessage()
            for record in caplog.records
        )
    finally:
        _refresh(monkeypatch, "PAGES_BASE_URL", None)


def test_feed_link_accepts_fork_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        _refresh(monkeypatch, "FEED_LINK", "https://github.com/forker/wien-oepnv")
        assert feed_config.FEED_LINK == "https://github.com/forker/wien-oepnv"
    finally:
        _refresh(monkeypatch, "FEED_LINK", None)


def test_pages_base_url_accepts_fork_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        _refresh(monkeypatch, "PAGES_BASE_URL", "https://forker.github.io/wien-oepnv")
        assert feed_config.PAGES_BASE_URL == "https://forker.github.io/wien-oepnv"
    finally:
        _refresh(monkeypatch, "PAGES_BASE_URL", None)
