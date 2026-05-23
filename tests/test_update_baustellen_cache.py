from __future__ import annotations

import json
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache


def _patch_http_layer_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the SSRF / DNS / IP-verification layer so a
    ``responses``-mocked URL can be consumed without hitting the real
    network.

    Defense-in-depth: TWO independent safeguards prevent a real
    network leak if a future test author calls this helper without
    activating ``responses``:

    1. **TEST-NET-1 fake IP** (``192.0.2.1``, RFC 5737 §3) — IANA-
       reserved for documentation-only use. Routers MUST NOT forward
       packets to this range, so a leaked HTTP request times out
       at the OS networking layer instead of reaching a real host
       (the previous fake ``8.8.8.8`` was Google Public DNS, which
       responds to TCP connects on port 443 — polite but still a
       real network round-trip).

    2. **Runtime guard via ``responses.is_enabled()``** — the
       patched ``_resolve_hostname_safe`` lambda first checks whether
       the ``responses`` mocker is currently active. If not, it
       raises ``RuntimeError`` with a clear message instructing the
       test author to wrap the HTTP-using code in
       ``@responses.activate``. This converts the silent-timeout
       failure mode into a fail-fast diagnostic.

    Combined with the existing test-isolation property of pytest's
    ``monkeypatch`` (per-test scope, auto-reverted), the helper is
    safe to use in any test that legitimately mocks HTTP via
    ``responses``.

    Patches the four layers in concert; missing any one leaks
    through to a real network call:

    * ``validate_http_url`` — patched on the **caller's namespace**
      (``update_baustellen_cache``) because the script does
      ``from utils.http import validate_http_url`` at import time,
      so patching only ``sys.modules['utils.http'].validate_http_url``
      would leave the script's local binding pointing at the
      original function.
    * ``verify_response_ip`` — patched on the **provider module**.
    * ``_resolve_hostname_safe`` — patched on the **provider
      module** with a fake resolver returning the TEST-NET-1 IP
      (and the runtime guard described above).
    * ``is_ip_safe`` — patched on the **provider module** to
      always return ``True`` so the safe-IP allowlist does not
      reject the TEST-NET-1 fallback (which is correctly classified
      as a documentation-range IP and would otherwise be rejected).
    """
    import responses as _responses_lib

    # RFC 5737 §3 TEST-NET-1 — guaranteed unrouteable so a leaked
    # HTTP request times out instead of reaching a real host.
    fake_addrinfo: list[tuple[Any, ...]] = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0)),
    ]

    def _is_responses_active() -> bool:
        """Detect whether the ``responses`` mocker has been started.

        ``responses.mock._patcher`` is ``None`` when the mocker is
        idle and a ``unittest.mock._patch`` instance after
        ``start()`` (set by both the ``@responses.activate``
        decorator and the ``responses.RequestsMock`` context
        manager). ``responses.is_enabled()`` is the public API in
        newer ``responses`` versions but is not present in the
        version pinned by this project (see requirements-dev.txt);
        the internal-attribute check is the documented fallback
        per the ``responses`` issue tracker.
        """
        return getattr(_responses_lib.mock, "_patcher", None) is not None

    def _guarded_resolve(_hostname: str) -> list[tuple[Any, ...]]:
        """Fail-fast guard: refuse to resolve when ``responses`` is not
        active. This converts the silent-timeout failure mode (the
        TEST-NET-1 IP would otherwise produce a 5-second timeout) into
        an immediate, actionable error pointing at the missing
        ``@responses.activate`` decorator.
        """
        if not _is_responses_active():
            raise RuntimeError(
                "_patch_http_layer_bypass was called but the "
                "``responses`` HTTP mocker is NOT currently active. "
                "Wrap the HTTP-using code in @responses.activate "
                "(or use the responses context manager). Refusing to "
                "let a real HTTP request reach the network — even via "
                "the TEST-NET-1 fallback IP — because doing so would "
                "produce a misleading 5-second timeout instead of a "
                "clear test-setup error."
            )
        return fake_addrinfo

    # 1) The caller's local binding (the script imports the name
    # directly, so the module-attribute patch below cannot reach it).
    monkeypatch.setattr(
        update_baustellen_cache, "validate_http_url", lambda url, **_kw: url
    )
    # 2) The provider-module attributes — these are looked up at call
    # time by the HTTP machinery itself, so patching the module
    # attribute is sufficient.
    for module_name in ("src.utils.http", "utils.http"):
        if module_name not in sys.modules:
            continue
        module = sys.modules[module_name]
        monkeypatch.setattr(module, "validate_http_url", lambda url, **kw: url)
        monkeypatch.setattr(module, "verify_response_ip", lambda _: None)
        monkeypatch.setattr(
            module, "_resolve_hostname_safe", _guarded_resolve
        )
        monkeypatch.setattr(module, "is_ip_safe", lambda _ip, **_kw: True)


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
    assert first["context"]["district"] == "21"
    assert first["starts_at"].startswith("2025-10-01")
    assert "location" in first
    assert first["location"]["coordinates"] == {"lat": 48.2562499, "lon": 16.4007}


def test_main_uses_fallback_when_remote_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def fake_fetch_remote(url: str, timeout: int) -> None:
        return None

    def capture_cache(provider: str, items: list[dict[str, str]]) -> None:
        calls.append((provider, items))

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", capture_cache)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", str(SAMPLE_PATH))
    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")

    exit_code = update_baustellen_cache.main()

    # Exit 2 = degraded: the cache came from the fallback sample, not a
    # live fetch. The previous silent ``return 0`` is what let the broken
    # WFS fetch hide for weeks.
    assert exit_code == 2
    assert calls and calls[0][0] == "baustellen"
    assert len(calls[0][1]) == 2
    assert any(
        "FALLBACK" in record.getMessage()
        for record in caplog.records
        if record.name == "update_baustellen_cache"
    )


def test_with_output_format_rewrites_only_the_token() -> None:
    rewritten = update_baustellen_cache._with_output_format(
        update_baustellen_cache.DEFAULT_DATA_URL, "application/json"
    )
    assert rewritten.endswith("outputFormat=application/json")
    # Every other parameter is preserved byte-for-byte.
    assert "typeName=ogdwien:BAUSTELLEOGD" in rewritten
    assert "srsName=EPSG:4326" in rewritten
    assert rewritten.startswith("https://data.wien.gv.at/daten/geo?")


def test_with_output_format_appends_when_absent() -> None:
    base = "https://data.wien.gv.at/daten/geo?service=WFS"
    assert (
        update_baustellen_cache._with_output_format(base, "geojson")
        == base + "&outputFormat=geojson"
    )


def test_main_negotiates_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured ``json`` token returns nothing (the production
    failure mode); the negotiation must fall through to the next variant
    and use it as a live success — NOT the degraded fallback."""
    seen: list[str] = []
    payload = {"type": "FeatureCollection", "features": []}

    def fake_fetch_remote(url: str, timeout: int) -> dict[str, Any] | None:
        seen.append(url)
        return payload if "outputFormat=application/json" in url else None

    cached: list[tuple[str, list[dict[str, Any]]]] = []
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(
        update_baustellen_cache, "write_cache", lambda p, items: cached.append((p, items))
    )

    exit_code = update_baustellen_cache.main()

    assert exit_code == 0  # live success via the negotiated format, not fallback
    assert seen[0].endswith("outputFormat=json")  # configured token tried first
    assert any("outputFormat=application/json" in url for url in seen)
    assert cached and cached[0][0] == "baustellen"


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("99999", update_baustellen_cache.MAX_BAUSTELLEN_TIMEOUT),
        ("21", update_baustellen_cache.MAX_BAUSTELLEN_TIMEOUT),
        ("0", 1),
        ("-5", 1),
        ("5", 5),
        ("", update_baustellen_cache.DEFAULT_BAUSTELLEN_TIMEOUT),
        ("garbage", update_baustellen_cache.DEFAULT_BAUSTELLEN_TIMEOUT),
    ],
)
def test_baustellen_timeout_env_is_clamped(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    """``BAUSTELLEN_TIMEOUT`` must never exceed ``MAX_BAUSTELLEN_TIMEOUT``.

    Without the cap a sluggish or attacker-controlled upstream peer could hold
    the fetch for ~28 hours via ``BAUSTELLEN_TIMEOUT=99999``, stalling the
    feed-build cron pipeline (Slowloris vector mirrored from
    ``MAX_PROVIDER_TIMEOUT`` and ``MAX_TIMEOUT_S``).
    """
    captured: list[int] = []

    def fake_fetch_remote(url: str, timeout: int) -> None:
        captured.append(timeout)
        return None

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", lambda *args, **kwargs: None)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", str(SAMPLE_PATH))
    monkeypatch.setenv("BAUSTELLEN_TIMEOUT", raw)

    update_baustellen_cache.main()

    # main() now negotiates the outputFormat, so _fetch_remote is called
    # once per candidate; every call must receive the clamped timeout.
    assert captured
    assert all(value == expected for value in captured)


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

    # Bypass DNS / IP checks (we're testing content-type filtering, not SSRF).
    _patch_http_layer_bypass(monkeypatch)

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

    _patch_http_layer_bypass(monkeypatch)

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


def test_patch_http_layer_bypass_fails_fast_without_responses_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_patch_http_layer_bypass`` must refuse to let a real HTTP
    request reach the network.

    Defense-in-depth contract: even if a future test author accidentally
    calls the helper without wrapping the HTTP-using code in
    ``@responses.activate``, the patched ``_resolve_hostname_safe`` must
    detect that the ``responses`` mocker is inactive and raise
    ``RuntimeError`` with a clear, actionable error message — instead of
    letting the request silently time out at the OS networking layer
    (the previous behaviour produced a 5-second hang via the TEST-NET-1
    fallback IP).
    """
    _patch_http_layer_bypass(monkeypatch)

    # ``responses`` is NOT activated here — invoking the production
    # fetch path therefore exercises the runtime guard inside the
    # patched ``_resolve_hostname_safe``.
    with pytest.raises(RuntimeError) as exc_info:
        update_baustellen_cache._fetch_remote(
            update_baustellen_cache.DEFAULT_DATA_URL, timeout=5
        )
    msg = str(exc_info.value)
    assert "responses" in msg
    # The error must explicitly point at the missing decorator so the
    # author doesn't need to spelunk into the helper to understand
    # the failure.
    assert "@responses.activate" in msg


@pytest.mark.parametrize(
    "payload",
    [
        # Truthy non-list values that the previous ``or []`` fallback let
        # through. ``42`` and ``True`` would crash with ``TypeError`` because
        # ``int`` / ``bool`` are not iterable; ``"abc"`` and the dict shape
        # would silently iterate characters / keys and pretend the upstream
        # returned zero features. Every shape must collapse to an empty
        # iterator instead so ``_collect_events`` stays in its documented
        # fallback path.
        {"type": "FeatureCollection", "features": 42},
        {"type": "FeatureCollection", "features": True},
        {"type": "FeatureCollection", "features": "abc"},
        {"type": "FeatureCollection", "features": {"a": "b"}},
        {"features": 123},
        {"features": "not-a-list"},
        {"data": {"features": 1}},
        {"data": {"features": True}},
        {"data": {"features": "x"}},
    ],
)
def test_iter_features_rejects_non_list_features(payload: dict[str, Any]) -> None:
    """Zero Trust: ``_iter_features`` must never iterate a non-list ``features``
    value extracted from the payload. The previous ``payload.get("features")
    or []`` only collapsed *falsy* values; truthy non-lists slipped through
    and either raised ``TypeError`` (int/bool) — propagating out of
    ``_collect_events`` and crashing the cache update — or silently emitted
    zero events. The fix must mirror the ``isinstance(raw, list)`` guard
    landed for the sibling VOR mapping loaders so the documented fallback
    runs instead.
    """
    result = list(update_baustellen_cache._iter_features(payload))
    assert result == []


def test_iter_features_returns_dicts_only() -> None:
    """Sanity: legitimate FeatureCollection payloads still yield the dict
    features (regression check that the new shape guard does not over-reject
    valid GeoJSON)."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"properties": {"BEZEICHNUNG": "Test"}},
            "not-a-dict",
            42,
            None,
            {"properties": {"BEZEICHNUNG": "Other"}},
        ],
    }
    result = list(update_baustellen_cache._iter_features(payload))
    assert len(result) == 2
    assert all(isinstance(f, dict) for f in result)


def test_iter_features_handles_data_wrapper_with_non_list_features() -> None:
    """The ``"data": {...}`` branch must apply the same shape guard — a
    ``data`` envelope containing a non-list ``features`` field must not
    crash when iterated."""
    payload = {"data": {"features": "should-not-iterate-as-chars"}}
    result = list(update_baustellen_cache._iter_features(payload))
    assert result == []


def test_collect_events_survives_malformed_features(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a payload whose ``features`` field is a truthy non-list
    must produce zero events without raising. Without the shape guard,
    ``int`` / ``bool`` shapes raised ``TypeError`` out of ``_collect_events``
    and aborted the whole cache update — bypassing the documented
    fallback path used when the network is unreachable."""
    import logging

    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    payload = {"type": "FeatureCollection", "features": 99999}
    events = update_baustellen_cache._collect_events(payload)
    assert events == []


def test_fetch_remote_handles_json_depth_bomb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Resilience: a malicious or pathological upstream serving a deeply-
    nested JSON document must not crash the cron job. ``json.loads`` raises
    ``RecursionError`` (NOT a subclass of ``JSONDecodeError``) when the
    body exceeds Python's recursion limit. The previous
    ``except (UnicodeDecodeError, json.JSONDecodeError)`` clause in
    ``_load_json_from_content`` would let ``RecursionError`` propagate and
    terminate the process — the same drift fixed canonically in
    ``src/providers/wl_fetch.py`` and ``src/providers/vor.py``.
    Verifying the parity here means a future change to the parser cannot
    silently regress the depth-bomb defence."""
    import logging

    deep = b"[" * 5000 + b"]" * 5000

    def fake_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return deep

    monkeypatch.setattr(update_baustellen_cache, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(
        update_baustellen_cache, "validate_http_url", lambda url, **_kw: url
    )

    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    result = update_baustellen_cache._fetch_remote(
        update_baustellen_cache.DEFAULT_DATA_URL, timeout=5
    )
    assert result is None


def test_load_fallback_handles_json_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Defence-in-depth: a tampered or accidentally committed fallback
    file containing a deeply-nested JSON document must be rejected with a
    clear error log instead of crashing the cron job via a propagated
    ``RecursionError``. The fallback is the documented offline path when
    the network is unreachable, so a depth-bomb here would deny both
    fetch *and* fallback simultaneously."""
    import logging

    poisoned = tmp_path / "poisoned.geojson"
    poisoned.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

    caplog.set_level(logging.ERROR, logger="update_baustellen_cache")
    result = update_baustellen_cache._load_fallback(poisoned)
    assert result is None
    assert any(
        "ungültiges JSON" in record.getMessage()
        for record in caplog.records
        if record.name == "update_baustellen_cache"
    )
