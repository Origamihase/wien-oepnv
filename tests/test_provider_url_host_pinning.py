"""Verify that provider URL env overrides only accept official upstream hosts."""

from __future__ import annotations

import importlib
import logging

import pytest


# WL_RSS_URL — Wiener Linien OGD endpoint
# ─────────────────────────────────────────


def _reload_wl(monkeypatch: pytest.MonkeyPatch, value: str | None) -> object:
    """Re-import wl_fetch with a controlled WL_RSS_URL value."""
    if value is None:
        monkeypatch.delenv("WL_RSS_URL", raising=False)
    else:
        monkeypatch.setenv("WL_RSS_URL", value)
    import src.providers.wl_fetch as wl_fetch

    return importlib.reload(wl_fetch)


def test_wl_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_wl(monkeypatch, None)
    assert module.WL_BASE == "https://www.wienerlinien.at/ogd_realtime"


def test_wl_accepts_official_host(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_wl(
        monkeypatch, "https://www.wienerlinien.at/ogd_realtime/v2"
    )
    assert module.WL_BASE.startswith("https://www.wienerlinien.at/")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/ogd_realtime",
        "https://api.wienerlinien.at",  # not the OGD host
        "https://www.wienerlinien.at.evil.com",  # suffix attack
        "https://wienerlinien.at",  # missing www. — not the canonical host
    ],
)
def test_wl_rejects_untrusted_host(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    caplog.set_level(logging.WARNING, logger="src.providers.wl_fetch")
    module = _reload_wl(monkeypatch, url)
    # Falls back to the default (and the link/fetch never goes to the attacker).
    assert module.WL_BASE == "https://www.wienerlinien.at/ogd_realtime"
    assert any(
        "kein bekannter Wiener-Linien-Host" in record.getMessage()
        for record in caplog.records
    )


# OEBB_RSS_URL — ÖBB Fahrplan endpoint
# ─────────────────────────────────────


def _reload_oebb(monkeypatch: pytest.MonkeyPatch, value: str | None) -> object:
    """Re-import oebb with a controlled OEBB_RSS_URL value."""
    if value is None:
        monkeypatch.delenv("OEBB_RSS_URL", raising=False)
    else:
        monkeypatch.setenv("OEBB_RSS_URL", value)
    import src.providers.oebb as oebb

    return importlib.reload(oebb)


def test_oebb_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_oebb(monkeypatch, None)
    assert module.OEBB_URL.startswith("https://fahrplan.oebb.at/")


def test_oebb_accepts_official_host(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_oebb(
        monkeypatch,
        "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&",
    )
    assert module.OEBB_URL.startswith("https://fahrplan.oebb.at/")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/feed.rss",
        "https://oebb.at/some-feed",  # the OEBB main site, not Fahrplan
        "https://fahrplan.oebb.at.evil.com",  # suffix attack
    ],
)
def test_oebb_rejects_untrusted_host(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    caplog.set_level(logging.WARNING, logger="src.providers.oebb")
    module = _reload_oebb(monkeypatch, url)
    # Falls back to the default; the per-item link fallback is NOT attacker-controlled.
    assert module.OEBB_URL.startswith("https://fahrplan.oebb.at/")
    assert any(
        "kein bekannter ÖBB-Fahrplan-Host" in record.getMessage()
        for record in caplog.records
    )
