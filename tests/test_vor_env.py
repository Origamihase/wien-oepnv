import base64
import importlib
import logging
import pytest
import requests
from pathlib import Path
from typing import Any

import src.providers.vor as vor


def test_access_id_env_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    # VOR_ACCESS_ID mit Leerzeichen wird entfernt und deaktiviert den Provider
    monkeypatch.setenv("VOR_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""

    # Fallback auf VAO_ACCESS_ID, ebenfalls Leerzeichen -> deaktiviert
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.setenv("VAO_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""

    # VAO_ACCESS_ID mit zusätzlichen Leerzeichen wird getrimmt
    monkeypatch.setenv("VAO_ACCESS_ID", " token ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "token"

    # Aufräumen
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""


def test_invalid_int_env_uses_defaults(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("VOR_BOARD_DURATION_MIN", "foo")
    monkeypatch.setenv("VOR_HTTP_TIMEOUT", "bar")
    monkeypatch.setenv("VOR_MAX_STATIONS_PER_RUN", "baz")
    monkeypatch.setenv("VOR_ROTATION_INTERVAL_SEC", "qux")

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BOARD_DURATION_MIN == 60
    assert vor.HTTP_TIMEOUT == 15
    assert vor.DEFAULT_MAX_STATIONS_PER_RUN == 2
    assert vor.MAX_STATIONS_PER_RUN == vor.DEFAULT_MAX_STATIONS_PER_RUN
    assert vor.ROTATION_INTERVAL_SEC == 1800

    for name in [
        "VOR_BOARD_DURATION_MIN",
        "VOR_HTTP_TIMEOUT",
        "VOR_MAX_STATIONS_PER_RUN",
        "VOR_ROTATION_INTERVAL_SEC",
    ]:
        assert any(name in r.getMessage() for r in caplog.records)

    for name in [
        "VOR_BOARD_DURATION_MIN",
        "VOR_HTTP_TIMEOUT",
        "VOR_MAX_STATIONS_PER_RUN",
        "VOR_ROTATION_INTERVAL_SEC",
    ]:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(vor)


def test_http_timeout_capped_at_slowloris_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security: VOR_HTTP_TIMEOUT must never exceed DEFAULT_HTTP_TIMEOUT (15s),
    # which is the Slowloris-defence ceiling for both connect and read
    # budgets. An env override (intentional misconfig, leaked CI env, or
    # compromised secret store) that raised the timeout would let a single
    # sluggish or attacker-controlled upstream peer hold a worker for hours,
    # exhausting the thread pool (VOR_MAX_WORKERS=10) and stalling the feed
    # build. The env var may still *tighten* the timeout below the ceiling.
    monkeypatch.setenv("VOR_HTTP_TIMEOUT", "99999")
    importlib.reload(vor)
    assert vor.HTTP_TIMEOUT == vor.DEFAULT_HTTP_TIMEOUT == 15

    monkeypatch.setenv("VOR_HTTP_TIMEOUT", "5")
    importlib.reload(vor)
    assert vor.HTTP_TIMEOUT == 5

    monkeypatch.delenv("VOR_HTTP_TIMEOUT", raising=False)
    importlib.reload(vor)
    assert vor.HTTP_TIMEOUT == vor.DEFAULT_HTTP_TIMEOUT


def test_max_requests_per_day_capped_at_contract_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security: VOR_MAX_REQUESTS_PER_DAY must never exceed the hard contract
    # cap of 100/day, regardless of an env override (intentional misconfig,
    # leaked CI env, or compromised secret store). Disabling this cap would
    # let the daily quota gate be bypassed in 8+ call sites that read
    # MAX_REQUESTS_PER_DAY, risking suspension of the access ID by VAO.
    monkeypatch.setenv("VOR_MAX_REQUESTS_PER_DAY", "99999")
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == vor.DEFAULT_MAX_REQUESTS_PER_DAY == 100

    # The env var may still *tighten* the budget below the contract cap.
    monkeypatch.setenv("VOR_MAX_REQUESTS_PER_DAY", "50")
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == 50

    monkeypatch.delenv("VOR_MAX_REQUESTS_PER_DAY", raising=False)
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == vor.DEFAULT_MAX_REQUESTS_PER_DAY


def test_invalid_bus_regex_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("VOR_BUS_INCLUDE_REGEX", "(")
    monkeypatch.setenv("VOR_BUS_EXCLUDE_REGEX", "(")

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BUS_INCLUDE_RE.pattern == vor.DEFAULT_BUS_INCLUDE_PATTERN
    assert vor.BUS_EXCLUDE_RE.pattern == vor.DEFAULT_BUS_EXCLUDE_PATTERN

    assert any("VOR_BUS_INCLUDE_REGEX" in record.getMessage() for record in caplog.records)
    assert any("VOR_BUS_EXCLUDE_REGEX" in record.getMessage() for record in caplog.records)

    monkeypatch.delenv("VOR_BUS_INCLUDE_REGEX", raising=False)
    monkeypatch.delenv("VOR_BUS_EXCLUDE_REGEX", raising=False)
    importlib.reload(vor)


@pytest.mark.parametrize(
    "redos_pattern",
    [
        # Classic nested unbounded quantifiers around a group. Each
        # entry covers a distinct ``[+*?]\s*\)\s*[+*]`` shape so the
        # heuristic stays grep-able for future variants. Patterns of
        # this shape exhibit exponential backtracking on non-matching
        # inputs like ``"a" * 32 + "!"``.
        "(a+)+$",
        "(a*)*$",
        "(a*)+$",
        "(a+)*$",
        "(.+)+$",
        "(?:a+)+$",
        "(a?)+$",
        "([a-z]+)+!",
        # Whitespace tolerated between the inner quantifier and the
        # closing paren / outer quantifier — operator-supplied patterns
        # should not be able to bypass the heuristic with formatting.
        "(a+ )+$",
    ],
)
def test_redos_bus_regex_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    redos_pattern: str,
) -> None:
    # Security: env-supplied include/exclude regexes must not enable a
    # ReDoS-vulnerable pattern. Heuristic detection rejects nested
    # unbounded quantifiers around groups before re.compile and falls
    # back to the project's vetted default pattern.
    monkeypatch.setenv("VOR_BUS_INCLUDE_REGEX", redos_pattern)
    monkeypatch.setenv("VOR_BUS_EXCLUDE_REGEX", redos_pattern)

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BUS_INCLUDE_RE.pattern == vor.DEFAULT_BUS_INCLUDE_PATTERN
    assert vor.BUS_EXCLUDE_RE.pattern == vor.DEFAULT_BUS_EXCLUDE_PATTERN
    assert any("ReDoS" in record.getMessage() for record in caplog.records)

    monkeypatch.delenv("VOR_BUS_INCLUDE_REGEX", raising=False)
    monkeypatch.delenv("VOR_BUS_EXCLUDE_REGEX", raising=False)
    importlib.reload(vor)


def test_oversized_bus_regex_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Security: an oversized env-supplied pattern (memory-exhaustion
    # vector during ``re.compile``) must be rejected before compilation
    # and fall back to the default.
    oversized = "a" * (vor.MAX_REGEX_PATTERN_LEN + 1)
    monkeypatch.setenv("VOR_BUS_INCLUDE_REGEX", oversized)

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BUS_INCLUDE_RE.pattern == vor.DEFAULT_BUS_INCLUDE_PATTERN
    assert any("zu lang" in record.getMessage() for record in caplog.records)

    monkeypatch.delenv("VOR_BUS_INCLUDE_REGEX", raising=False)
    importlib.reload(vor)


def test_default_bus_patterns_pass_redos_heuristic() -> None:
    # Sanity check: the project's own defaults must not be flagged by
    # the ReDoS heuristic, otherwise the fallback path is unreachable.
    assert vor._REDOS_NESTED_QUANTIFIER_RE.search(vor.DEFAULT_BUS_INCLUDE_PATTERN) is None
    assert vor._REDOS_NESTED_QUANTIFIER_RE.search(vor.DEFAULT_BUS_EXCLUDE_PATTERN) is None
    assert len(vor.DEFAULT_BUS_INCLUDE_PATTERN) <= vor.MAX_REGEX_PATTERN_LEN
    assert len(vor.DEFAULT_BUS_EXCLUDE_PATTERN) <= vor.MAX_REGEX_PATTERN_LEN


def test_safe_custom_bus_regex_still_compiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Operators must still be able to override the default patterns
    # with reasonable, non-ReDoS-vulnerable regexes.
    monkeypatch.setenv("VOR_BUS_INCLUDE_REGEX", r"(?i)^(?:Bus|Tram)\s+\d+")

    importlib.reload(vor)

    assert vor.BUS_INCLUDE_RE.pattern == r"(?i)^(?:Bus|Tram)\s+\d+"
    assert vor.BUS_INCLUDE_RE.match("Bus 100") is not None
    assert vor.BUS_INCLUDE_RE.match("U1") is None

    monkeypatch.delenv("VOR_BUS_INCLUDE_REGEX", raising=False)
    importlib.reload(vor)


def test_station_ids_fallback_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)
    monkeypatch.delenv("VOR_STATION_NAMES", raising=False)

    # Use a file inside the project directory (e.g. data/) to pass validation
    data_dir = vor.DATA_DIR
    ids_file = data_dir / "test_ids.txt"
    try:
        ids_file.write_text("  1001,1002\n1003  ", encoding="utf-8")
        monkeypatch.setenv("VOR_STATION_IDS_FILE", str(ids_file))

        importlib.reload(vor)

        assert vor.VOR_STATION_IDS == ["1001", "1002", "1003"]
    finally:
        if ids_file.exists():
            ids_file.unlink()

    monkeypatch.delenv("VOR_STATION_IDS_FILE", raising=False)
    importlib.reload(vor)


def test_station_ids_fallback_from_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)
    monkeypatch.delenv("VOR_STATION_NAMES", raising=False)
    monkeypatch.delenv("VOR_STATION_IDS_FILE", raising=False)

    importlib.reload(vor)

    ids = set(vor.VOR_STATION_IDS)
    assert len(ids) >= 50
    assert {"490009400", "430310100", "430470800"}.issubset(ids)


def test_refresh_access_credentials_reloads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "first")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "first"

    monkeypatch.setenv("VOR_ACCESS_ID", "second")
    refreshed = vor.refresh_access_credentials()

    assert refreshed == "second"
    assert vor.VOR_ACCESS_ID == "second"

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_base_url_prefers_secret(monkeypatch: pytest.MonkeyPatch) -> None:



    # Mock DNS to ensure secret.example.com is accepted
    def mock_resolve_env(self: Any, host: Any, record_type: Any, *args: Any, **kwargs: Any) -> Any:
        if record_type == 'A':
            from unittest.mock import MagicMock
            ans = MagicMock()
            ans.address = "93.184.216.34"
            return [ans]
        import dns.resolver
        raise dns.resolver.NoAnswer()  # type: ignore[no-untyped-call]

    monkeypatch.setattr(
        "dns.resolver.Resolver.resolve",
        mock_resolve_env
    )



    monkeypatch.setenv("VOR_BASE", "https://example.com/base")
    monkeypatch.setenv("VOR_BASE_URL", "https://secret.example.com/base")

    importlib.reload(vor)

    assert vor.VOR_BASE_URL == "https://secret.example.com/base/"
    assert vor.VOR_VERSION == "v1.11.0"

    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://example.com/base/v1.11.0/"
    )

    monkeypatch.delenv("VOR_BASE", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )


def test_apply_authentication_sets_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]
    # Clear Accept to allow setdefault to work (mimicking DummySession behavior)
    if "Accept" in session.headers:
        del session.headers["Accept"]

    vor.apply_authentication(session)

    assert session.headers["Accept"] == "application/json"
    assert "Authorization" not in session.headers
    assert isinstance(session.auth, vor.VorAuth)

    # Test Auth Application
    req = requests.PreparedRequest()
    # Must use VOR_BASE_URL to trigger injection
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == "Bearer secret"
    # User requested to inject accessId even if header present
    assert "accessId=secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_apply_authentication_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "user:secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]

    vor.apply_authentication(session)

    expected = base64.b64encode(b"user:secret").decode("ascii")
    # Verify auth object
    req = requests.PreparedRequest()
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == f"Basic {expected}"
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_apply_authentication_basic_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "Basic user:secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]

    vor.apply_authentication(session)

    expected = base64.b64encode(b"user:secret").decode("ascii")

    req = requests.PreparedRequest()
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == f"Basic {expected}"
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)
