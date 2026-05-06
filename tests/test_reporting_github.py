import json
import logging

import pytest
import responses

from src.feed.reporting import RunReport


@responses.activate
def test_run_report_creates_github_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")
    # Bypass DNS check in test environment
    monkeypatch.setattr("src.utils.http.validate_http_url", lambda url, **kw: url)

    # Robustly patch verify_response_ip in all loaded modules where it might be used
    # This handles aliasing (src.utils vs utils) and imports in feed.reporting
    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json={"html_url": "https://github.com/demo/repo/issues/1"},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Unbekannter Fehler im Test")
    report.finish(build_successful=False)

    report.log_results()

    # Should not submit duplicate issue
    report.log_results()

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer secret-token"
    payload = json.loads(call.request.body)
    assert payload["title"].startswith("Fehlerbericht: Feed-Lauf")
    assert "Unbekannter Fehler im Test" in payload["body"]


@responses.activate
def test_run_report_logs_warning_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.delenv("FEED_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Fehler ohne Token")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")

    report.log_results()

    assert not responses.calls
    warning_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    assert any("Token oder Repository fehlen" in message for message in warning_messages)


@responses.activate
def test_run_report_sanitizes_github_error_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json={"message": "Bad\nrequest\tdata\rwith controls"},
        status=400,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Fehler ohne Erfolg")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")

    report.log_results()

    warning_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    assert any("Bad request data with controls" in message for message in warning_messages)
    assert all("\n" not in message and "\t" not in message for message in warning_messages)


@responses.activate
@pytest.mark.parametrize(
    "non_dict_body",
    [
        ["unexpected", "list"],
        "scalar string body",
        42,
        None,
    ],
)
def test_run_report_handles_non_dict_error_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    non_dict_body: object,
) -> None:
    """Zero Trust: a non-dict JSON error body must not crash the reporter.

    A misbehaving GitHub Enterprise proxy or unexpected upstream change could
    return a JSON list/scalar/null where a dict is expected. Calling .get()
    on that would raise AttributeError, which the reporter previously did
    not catch, breaking the feed-build flow.
    """
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json=non_dict_body,
        status=422,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")

    # Must not raise AttributeError or any other exception.
    report.log_results()

    warning_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    assert any(
        "GitHub-Antwort 422" in message for message in warning_messages
    ), f"Expected error log for status 422, got: {warning_messages}"


@responses.activate
@pytest.mark.parametrize(
    "non_dict_body",
    [
        ["unexpected"],
        "scalar",
        7,
        None,
    ],
)
def test_run_report_handles_non_dict_success_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    non_dict_body: object,
) -> None:
    """Zero Trust: a non-dict JSON success body must not crash the reporter."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json=non_dict_body,
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.INFO, logger="build_feed")

    # Must not raise AttributeError or any other exception.
    report.log_results()

    info_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    # The fallback log path ("Issue erstellt." without URL) must run.
    assert any(
        "Automatisches GitHub-Issue erstellt" in message for message in info_messages
    ), f"Expected creation log, got: {info_messages}"


@responses.activate
def test_run_report_rejects_non_string_html_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Zero Trust: html_url must be a string before being logged as a URL."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    import sys
    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(sys.modules[module_name], "verify_response_ip", lambda _: None)

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        # Non-string html_url should not be propagated to the log line.
        json={"html_url": {"nested": "object"}},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.INFO, logger="build_feed")

    report.log_results()

    info_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    # Should fall through to the "no URL" message rather than logging the dict.
    assert any(
        message.endswith("Automatisches GitHub-Issue erstellt.")
        for message in info_messages
    ), f"Expected URL-less success log, got: {info_messages}"
