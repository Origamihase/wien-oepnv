"""Verify that the GitHub issue reporter only sends tokens to trusted hosts."""

from __future__ import annotations

import logging

import pytest
import responses

from src.feed.reporting import RunReport, _is_trusted_github_api


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com",
        "https://api.github.com/",
        "https://github.example.com/api/v3",
        "https://github.example.com/api/v3/",
        "https://ghe.corp.local/api/graphql",
    ],
)
def test_is_trusted_github_api_accepts_known_endpoints(url: str) -> None:
    assert _is_trusted_github_api(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # Typo squat — close to api.github.com but not exact.
        "https://api.gihub.com",
        "https://api.github.com.evil.com",
        # Host change with no GHE-style path.
        "https://evil.com",
        "https://attacker.example.com",
        # Wrong path (not /api/v3 nor /api/graphql).
        "https://github.example.com/api/v4",
        "https://github.example.com/repos/x/y",
        # Plain HTTP (not allowed for credential transport).
        "ftp://api.github.com",
        "",
        "not-a-url",
    ],
)
def test_is_trusted_github_api_rejects_untrusted_endpoints(url: str) -> None:
    assert _is_trusted_github_api(url) is False


@responses.activate
def test_submit_refuses_when_api_url_is_untrusted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting FEED_GITHUB_API_URL to a non-GitHub host must NOT leak the token."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "ghs_topsecrettoken")
    monkeypatch.setenv("FEED_GITHUB_API_URL", "https://evil.example.com")

    # If the guard fails open, the token would be POSTed to evil.example.com.
    # We register a passthrough so any leak is observable as a recorded call.
    responses.post(
        "https://evil.example.com/repos/demo/repo/issues",
        json={"html_url": "https://evil.example.com/issues/1"},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")
    report.log_results()

    # Critical assertion: NO outbound HTTP call was made — token is contained.
    assert not responses.calls, "Token was leaked to a non-GitHub host"
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]
    assert any(
        "kein bekannter GitHub-Endpunkt" in message for message in warning_messages
    ), f"Expected guard warning not found in: {warning_messages}"
