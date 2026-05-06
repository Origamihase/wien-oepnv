from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache


SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "samples" / "baustellen_sample.geojson"


@pytest.mark.parametrize(
    "duration, expected_start, expected_end",
    [
        ("2025-11-05/2025-11-20", date(2025, 11, 5), date(2025, 11, 20)),
        ("2025-01-01T00:00:00+01:00/2025-01-02T12:00:00+01:00", date(2025, 1, 1), date(2025, 1, 2)),
    ],
)
def test_parse_range_handles_duration(duration: str, expected_start: date, expected_end: date) -> None:
    properties = {"DAUER": duration}
    start, end = update_baustellen_cache._parse_range(properties)
    assert start is not None and start.date() == expected_start
    assert end is not None and end.date() == expected_end


def test_collect_events_from_sample_payload() -> None:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    events = update_baustellen_cache._collect_events(payload)
    assert len(events) == 2
    first = events[0]
    assert first["category"] == "Baustelle"
    assert first["context"]["district"] == "06"
    assert first["starts_at"].startswith("2025-10-01")
    assert "location" in first


def test_main_uses_fallback_when_remote_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def fake_fetch_remote(url: str, timeout: int) -> None:
        return None

    def capture_cache(provider: str, items: list[dict[str, str]]) -> None:
        calls.append((provider, items))

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", capture_cache)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", str(SAMPLE_PATH))

    exit_code = update_baustellen_cache.main()

    assert exit_code == 0
    assert calls and calls[0][0] == "baustellen"
    assert len(calls[0][1]) == 2


def test_resolve_fallback_path_default_when_unset() -> None:
    assert update_baustellen_cache._resolve_fallback_path(None) == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    assert update_baustellen_cache._resolve_fallback_path("") == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    assert update_baustellen_cache._resolve_fallback_path("   ") == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_accepts_repo_relative() -> None:
    """Paths inside the repo (the legitimate use case) must be honoured."""
    relative = SAMPLE_PATH.relative_to(update_baustellen_cache.REPO_ROOT).as_posix()
    resolved = update_baustellen_cache._resolve_fallback_path(relative)
    assert resolved == SAMPLE_PATH


def test_resolve_fallback_path_blocks_outside_repo(tmp_path: Path) -> None:
    """An env-controlled absolute path outside the repo must be rejected."""
    outside = tmp_path / "evil.json"
    outside.write_text('{"features": []}', encoding="utf-8")
    resolved = update_baustellen_cache._resolve_fallback_path(str(outside))
    assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_blocks_traversal_via_dotdot() -> None:
    """A relative path that escapes the repo via ../ must be rejected."""
    resolved = update_baustellen_cache._resolve_fallback_path("../../etc/passwd")
    assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_blocks_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the repo pointing outside must be rejected by resolve()."""
    target = tmp_path / "outside.json"
    target.write_text('{"features": []}', encoding="utf-8")
    link = update_baustellen_cache.REPO_ROOT / "data" / "samples" / "_pytest_symlink_test.json"
    link.symlink_to(target)
    try:
        resolved = update_baustellen_cache._resolve_fallback_path(
            str(link.relative_to(update_baustellen_cache.REPO_ROOT))
        )
        assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    finally:
        link.unlink(missing_ok=True)


def test_resolve_data_url_default_when_unset() -> None:
    assert (
        update_baustellen_cache._resolve_data_url(None)
        == update_baustellen_cache.DEFAULT_DATA_URL
    )
    assert (
        update_baustellen_cache._resolve_data_url("")
        == update_baustellen_cache.DEFAULT_DATA_URL
    )
    assert (
        update_baustellen_cache._resolve_data_url("   ")
        == update_baustellen_cache.DEFAULT_DATA_URL
    )


def test_resolve_data_url_accepts_official_host() -> None:
    """The Stadt Wien OGD host is the only legitimate override target."""
    candidate = (
        "https://data.wien.gv.at/daten/geo?service=WFS&typeName=ogdwien:BAUSTELLEOGD"
    )
    resolved = update_baustellen_cache._resolve_data_url(candidate)
    assert resolved == candidate


@pytest.mark.parametrize(
    "url",
    [
        # Arbitrary attacker-controlled host
        "https://evil.example.com/baustellen.json",
        # Suffix attack: looks like the official host but isn't
        "https://data.wien.gv.at.evil.com/baustellen.json",
        # Different Vienna subdomain (e.g., not OGD)
        "https://www.wien.gv.at/baustellen.json",
        # Different OGD provider
        "https://data.gv.at/baustellen.json",
    ],
)
def test_resolve_data_url_rejects_untrusted_host(
    caplog: pytest.LogCaptureFixture, url: str
) -> None:
    """An env-controlled URL pointing outside the OGD allowlist must NOT be used."""
    import logging

    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    resolved = update_baustellen_cache._resolve_data_url(url)
    # Must fall back to the default — no fetch goes to the attacker.
    assert resolved == update_baustellen_cache.DEFAULT_DATA_URL
    assert any(
        "kein bekannter Stadt-Wien-OGD-Host" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize(
    "content_type, body",
    [
        # CDN/WAF error pages and proxy responses are typically text/html.
        # Without explicit content-type pinning these would be fed straight
        # into json.loads() — wasting cycles on a guaranteed parse failure
        # and potentially missing legitimate retry/fallback paths.
        ("text/html", "<html>WAF block</html>"),
        # A misconfigured upstream serving plain text (e.g., maintenance
        # page) must also be rejected at the request layer.
        ("text/plain", "not json"),
        # Defence-in-depth: defang accidental XML responses from a WFS
        # endpoint that flips outputFormat behind a feature flag.
        ("application/xml", "<error/>"),
    ],
)
def test_fetch_remote_rejects_unexpected_content_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    content_type: str,
    body: str,
) -> None:
    """The Baustellen WFS endpoint advertises GeoJSON; everything else is
    a CDN error, a WAF block, or an upstream misconfiguration — none of
    which should reach _load_json_from_content. The other providers
    (WL/VOR/ÖBB) already pin allowed_content_types; this confirms parity.

    Asserting the failure happens at the *request* layer (Invalid
    Content-Type) rather than the *parser* layer (Invalid JSON) is the
    point — without ``allowed_content_types`` the body is still read into
    memory and handed to ``json.loads``, which works only by accident."""
    import logging
    import responses
    import sys

    # Bypass DNS / IP checks (we're testing content-type filtering, not SSRF).
    for module_name in ("src.utils.http", "utils.http"):
        if module_name in sys.modules:
            monkeypatch.setattr(
                sys.modules[module_name], "validate_http_url", lambda url, **kw: url
            )
            monkeypatch.setattr(
                sys.modules[module_name], "verify_response_ip", lambda _: None
            )

    @responses.activate
    def run() -> None:
        responses.get(
            update_baustellen_cache.DEFAULT_DATA_URL,
            body=body,
            status=200,
            content_type=content_type,
        )
        caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
        result = update_baustellen_cache._fetch_remote(
            update_baustellen_cache.DEFAULT_DATA_URL, timeout=5
        )
        assert result is None, (
            f"Expected None for Content-Type {content_type!r}, got {result!r}"
        )
        # The rejection must come from the request layer (Invalid Content-Type)
        # so a future change to _load_json_from_content can't accidentally
        # accept non-JSON payloads.
        warning_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "update_baustellen_cache"
        ]
        assert any(
            "Invalid Content-Type" in message for message in warning_messages
        ), (
            f"Content-Type {content_type!r} must be rejected at the request "
            f"layer, not silently dropped by the JSON parser. Got: "
            f"{warning_messages!r}"
        )

    run()


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        # RFC 7946 GeoJSON registration — the OGD endpoint may emit this.
        "application/geo+json",
        # Older WFS/Apache mod_geowfs deployments use text/json.
        "text/json",
    ],
)
def test_fetch_remote_accepts_geojson_variants(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    """The accepted-content-type set must cover real-world WFS GeoJSON
    responses — overshooting (rejecting a legitimate variant) would put the
    feed into permanent fallback mode."""
    import responses
    import sys

    for module_name in ("src.utils.http", "utils.http"):
        if module_name in sys.modules:
            monkeypatch.setattr(
                sys.modules[module_name], "validate_http_url", lambda url, **kw: url
            )
            monkeypatch.setattr(
                sys.modules[module_name], "verify_response_ip", lambda _: None
            )

    @responses.activate
    def run() -> None:
        responses.get(
            update_baustellen_cache.DEFAULT_DATA_URL,
            body='{"features": []}',
            status=200,
            content_type=content_type,
        )
        result = update_baustellen_cache._fetch_remote(
            update_baustellen_cache.DEFAULT_DATA_URL, timeout=5
        )
        assert result == {"features": []}, (
            f"Expected dict for Content-Type {content_type!r}, got {result!r}"
        )

    run()
