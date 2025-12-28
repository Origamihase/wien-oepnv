import json
import logging

import responses

from feed.reporting import RunReport


@responses.activate
def test_run_report_creates_github_issue(monkeypatch):
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        json={"html_url": "https://github.com/demo/repo/issues/1"},
        status=201,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Unbekannter Fehler im Test")
    report.finish(build_successful=False)

    report.log_results()

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer secret-token"
    payload = json.loads(call.request.body)
    assert payload["title"].startswith("Fehlerbericht: Feed-Lauf")
    assert "Unbekannter Fehler im Test" in payload["body"]


@responses.activate
def test_run_report_logs_warning_when_credentials_missing(monkeypatch, caplog):
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
        record.message for record in caplog.records if record.name == "build_feed"
    ]
    assert any("Token oder Repository fehlen" in message for message in warning_messages)


@responses.activate
def test_run_report_sanitizes_github_error_details(monkeypatch, caplog):
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")
    monkeypatch.setattr("feed.reporting.verify_response_ip", lambda _: None)

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
        record.message for record in caplog.records if record.name == "build_feed"
    ]
    assert any("Bad request data with controls" in message for message in warning_messages)
    assert all("\n" not in message and "\t" not in message for message in warning_messages)
