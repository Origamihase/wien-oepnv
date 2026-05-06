"""Verify that the GitHub issue reporter rejects untrusted repository slugs."""

from __future__ import annotations

import logging

import pytest
import responses

from src.feed.reporting import RunReport, _is_valid_github_repo_slug


@pytest.mark.parametrize(
    "slug",
    [
        # Plain owner/repo
        "demo/repo",
        "Origamihase/wien-oepnv",
        # Hyphens are allowed
        "my-org/my-repo",
        "a/b",
        # Dots and underscores allowed in name
        "owner/name.with.dots",
        "owner/name_with_under",
        # 39-char owner (max), 100-char name (max)
        "a" + "b" * 38 + "/" + "x" * 100,
    ],
)
def test_repo_slug_accepts_valid_format(slug: str) -> None:
    assert _is_valid_github_repo_slug(slug) is True


@pytest.mark.parametrize(
    "slug",
    [
        # URL-component injection: query string
        "owner/repo?injected=1",
        # URL-component injection: fragment
        "owner/repo#frag",
        # Path traversal — would rewrite to a different GitHub endpoint
        "../endpoint",
        "owner/../organizations",
        "owner/repo/extra",
        # Spaces / control chars
        "owner /repo",
        "owner\trepo",
        "owner\nrepo",
        # Empty parts
        "/repo",
        "owner/",
        "/",
        "",
        # Owner cannot start with hyphen
        "-owner/repo",
        # Slash count
        "ownerrepo",
        "a/b/c",
        # Unicode confusables / non-ASCII
        "owner/répo",
        # Over-long owner (40 chars > GitHub's 39 max)
        ("a" * 40) + "/repo",
        # Over-long name (101 chars > GitHub's 100 max)
        "owner/" + ("a" * 101),
    ],
)
def test_repo_slug_rejects_invalid_format(slug: str) -> None:
    assert _is_valid_github_repo_slug(slug) is False


@responses.activate
def test_submit_refuses_when_repo_slug_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting FEED_GITHUB_REPOSITORY to an injection payload must NOT send the request."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "owner/repo?leak=1")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "ghs_topsecret")

    # If the guard fails open, requests would target some weird URL on
    # api.github.com — register a passthrough so any leak is observable.
    responses.add_passthru("https://api.github.com")

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")
    report.log_results()

    # Critical: no outbound HTTP call was attempted.
    assert not responses.calls, "Request was sent despite invalid repository slug"
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]
    assert any(
        "owner/name-Schema" in message for message in warning_messages
    ), f"Expected guard warning not found in: {warning_messages}"
