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


# ---------------------------------------------------------------------------
# Zero-Trust shape validation for the fallback file
# ---------------------------------------------------------------------------
# ``_load_fallback`` previously returned ``cast(dict[str, Any], json.loads(raw))``
# without a runtime ``isinstance`` guard. ``cast`` lies to the type checker, so
# a fallback file containing a JSON list / scalar / null would slip through and
# crash the very next step in ``_iter_features`` — exactly on the failure path
# (network down) where the fallback is supposed to keep the cache up. The
# tests below pin the shape check that closes that gap.


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("[]", "list"),
        ("null", "NoneType"),
        ("42", "int"),
        ('"a string"', "str"),
    ],
)
def test_load_fallback_rejects_non_object_shapes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    body: str,
    kind: str,
) -> None:
    """A non-object fallback body must return None and log the actual shape."""
    import logging

    fallback = tmp_path / "fallback.json"
    fallback.write_text(body, encoding="utf-8")

    caplog.set_level(logging.ERROR, logger="update_baustellen_cache")
    result = update_baustellen_cache._load_fallback(fallback)

    assert result is None
    assert any(
        "kein JSON-Objekt" in record.getMessage() and kind in record.getMessage()
        for record in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_load_fallback_accepts_valid_object(tmp_path: Path) -> None:
    """A well-formed object fallback must still load (no regression)."""
    fallback = tmp_path / "fallback.json"
    fallback.write_text(
        '{"type": "FeatureCollection", "features": []}', encoding="utf-8"
    )

    result = update_baustellen_cache._load_fallback(fallback)

    assert result == {"type": "FeatureCollection", "features": []}


def test_load_fallback_rejects_invalid_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An undecodable fallback body must return None (existing behaviour)."""
    import logging

    fallback = tmp_path / "fallback.json"
    fallback.write_text("not json {{{", encoding="utf-8")

    caplog.set_level(logging.ERROR, logger="update_baustellen_cache")
    result = update_baustellen_cache._load_fallback(fallback)

    assert result is None
    assert any(
        "ungültiges JSON" in record.getMessage() for record in caplog.records
    )
