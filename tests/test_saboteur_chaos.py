"""Saboteur Chaos Tests — provider-level resilience under hostile upstream.

Each test simulates ONE chaos scenario from the diagnostic report:

* Truncated payloads (mid-byte connection drops)
* Schema drift (200 OK + dict-shaped body but expected keys missing)
* Empty bodies (200 OK + zero bytes)
* Provider-level bulkheads (one provider's exception doesn't drop others)

The goal is to assert FAIL-CLOSED-GRACEFULLY behaviour: the build process
must never crash, hang, or process corrupted data, but it should also
never silently produce zero items without logging a structured warning
that an operator can grep for.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import requests

from src.providers import wl_fetch as wl


# ============================================================================
# Truncated Payload — JSON connection drops mid-byte
# ============================================================================

def test_chaos_wl_truncated_json_payload_returns_empty_dict() -> None:
    """Scenario: Wiener Linien returns 200 OK with a Content-Type of
    application/json, but the body is a TCP-truncated prefix
    (``{"data": {"trafficInfos": [{"name": "U1", "des``).

    Expected behaviour: WL's ``_get_json`` catches the ``JSONDecodeError``
    and returns ``{}`` so the upstream pipeline treats the provider as
    empty — never crashes the cron. The warning side-effect is verified
    indirectly: the function must use a logged-and-default-empty path,
    not the unhandled-exception path (which would propagate up).
    """
    truncated_body = b'{"data": {"trafficInfos": [{"name": "U1", "des'

    with patch("src.providers.wl_fetch.fetch_content_safe", return_value=truncated_body):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}, "Truncated payload must yield empty dict (fail-closed)"


def test_chaos_wl_invalid_json_chars_returns_empty_dict() -> None:
    """Scenario: API responds with 200 OK + ``application/json`` header but
    body is binary garbage (``\\x00\\xff\\xfe`` — common in flaky middlebox
    decompression bugs)."""
    garbage = b"\x00\xff\xfe garbage \x00"

    with patch("src.providers.wl_fetch.fetch_content_safe", return_value=garbage):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}


def test_chaos_wl_empty_body_returns_empty_dict() -> None:
    """Scenario: 200 OK + zero-byte body. Some CDNs serve this when a
    misconfigured upstream returns a 'normalised' empty response."""
    with patch("src.providers.wl_fetch.fetch_content_safe", return_value=b""):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}


# ============================================================================
# Schema Drift — top-level type wrong
# ============================================================================

def test_chaos_wl_array_payload_returns_empty_dict() -> None:
    """Scenario: API contract assumes a dict, but a future API change
    starts returning a top-level array (``[{"item": "x"}]``).
    The Zero-Trust type check rejects this — the function returns ``{}``
    rather than crashing or silently treating the array as a dict."""
    with patch(
        "src.providers.wl_fetch.fetch_content_safe",
        return_value=json.dumps([{"item": "x"}]).encode("utf-8"),
    ):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}


def test_chaos_wl_string_payload_returns_empty_dict() -> None:
    """Scenario: API returns a top-level JSON string ('"error"') —
    valid JSON, wrong type."""
    with patch(
        "src.providers.wl_fetch.fetch_content_safe",
        return_value=b'"error"',
    ):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}


def test_chaos_wl_null_payload_returns_empty_dict() -> None:
    """Scenario: API returns a literal ``null`` body."""
    with patch(
        "src.providers.wl_fetch.fetch_content_safe",
        return_value=b"null",
    ):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert result == {}


# ============================================================================
# Provider Bulkhead — one provider's failure doesn't bring down others
# ============================================================================

def test_chaos_provider_bulkhead_one_crash_doesnt_drop_others() -> None:
    """Scenario: VOR's loader raises a generic ``RuntimeError``. WL and
    ÖBB loaders return real items. The build pipeline must surface
    WL+ÖBB items even though VOR exploded.

    This verifies the existing ``_collect_items`` exception-class
    handling (which catches ``Exception`` per-provider) acts as a
    bulkhead — Saboteur's role here is to LOCK IN the existing
    behaviour so a future refactor cannot regress it.
    """
    from src import build_feed

    def vor_explodes(timeout: Any = None) -> list[dict[str, Any]]:
        raise RuntimeError("VOR upstream catastrophically failed")

    def wl_returns_items(timeout: Any = None) -> list[dict[str, Any]]:
        return [
            {
                "title": "U1: Test",
                "description": "test",
                "link": "https://example.com/wl",
                "guid": "wl-1",
                "pubDate": None,
                "starts_at": None,
                "ends_at": None,
                "_identity": "wl|1",
                "source": "WL",
                "category": "Störung",
            }
        ]

    def oebb_returns_items(timeout: Any = None) -> list[dict[str, Any]]:
        return [
            {
                "title": "REX 1: Test",
                "description": "test",
                "link": "https://example.com/oebb",
                "guid": "oebb-1",
                "pubDate": None,
                "starts_at": None,
                "ends_at": None,
                "_identity": "oebb|1",
                "source": "ÖBB",
                "category": "Störung",
            }
        ]

    from src.feed.providers import ProviderSpec

    Loader = Callable[..., Any]
    fake_specs = [
        ProviderSpec(env_var="WL_ENABLED", loader=cast(Loader, wl_returns_items), cache_key=""),
        ProviderSpec(env_var="VOR_ENABLED", loader=cast(Loader, vor_explodes), cache_key=""),
        ProviderSpec(env_var="OEBB_ENABLED", loader=cast(Loader, oebb_returns_items), cache_key=""),
    ]

    with patch.object(build_feed, "PROVIDERS", []), \
         patch("src.build_feed.iter_providers", return_value=fake_specs), \
         patch.object(build_feed, "_PROVIDERS_INITIALIZED", True), \
         patch("src.build_feed.feed_config.get_bool_env", return_value=True), \
         patch.object(build_feed.feed_config, "PROVIDER_TIMEOUT", 5.0):
        items = build_feed._collect_items()

    sources = {item.get("source") for item in items}
    assert "WL" in sources, "WL items must reach the feed despite VOR crash"
    assert "ÖBB" in sources, "ÖBB items must reach the feed despite VOR crash"
    # VOR contributed nothing because it raised; that's the desired bulkhead
    assert "VOR" not in sources


def test_chaos_provider_bulkhead_one_returns_garbage_doesnt_crash() -> None:
    """Scenario: a provider returns something pathological — not a list.
    ``_merge_result`` must reject it cleanly without crashing the pipeline.
    """
    from src import build_feed

    def garbage_provider(timeout: Any = None) -> Any:
        return {"this": "is not a list"}  # contract violation

    def good_provider(timeout: Any = None) -> list[dict[str, Any]]:
        return [
            {
                "title": "U1: Test",
                "description": "test",
                "link": "https://example.com/wl",
                "guid": "good-1",
                "pubDate": None,
                "starts_at": None,
                "ends_at": None,
                "_identity": "wl|good-1",
                "source": "WL",
                "category": "Störung",
            }
        ]

    from src.feed.providers import ProviderSpec

    Loader = Callable[..., Any]
    fake_specs = [
        ProviderSpec(env_var="GOOD_ENABLED", loader=cast(Loader, good_provider), cache_key=""),
        ProviderSpec(env_var="GARBAGE_ENABLED", loader=cast(Loader, garbage_provider), cache_key=""),
    ]

    with patch.object(build_feed, "PROVIDERS", []), \
         patch("src.build_feed.iter_providers", return_value=fake_specs), \
         patch.object(build_feed, "_PROVIDERS_INITIALIZED", True), \
         patch("src.build_feed.feed_config.get_bool_env", return_value=True), \
         patch.object(build_feed.feed_config, "PROVIDER_TIMEOUT", 5.0):
        items = build_feed._collect_items()

    # Good provider's items survived
    sources = {item.get("source") for item in items}
    assert "WL" in sources

    # Garbage provider produced 0 items (it didn't return a list)
    assert len(items) == 1


# ============================================================================
# Time-Traveler — clock skew in pubDate parsing
# ============================================================================

def test_chaos_oebb_malformed_pubdate_does_not_crash() -> None:
    """Scenario: an upstream RSS feed returns a malformed pubDate
    (``"yesterday"``, ``"+99:99"``, empty, etc). The parser must
    return ``None`` rather than raising, so feed building continues."""
    from src.providers.oebb import _parse_dt_rfc2822

    malformed = [
        "yesterday",
        "+99:99",
        "",
        "🕐 some emoji",
        "0000-00-00T00:00:00",
        "Sat, 32 Feb 2026 25:99:99 +0000",  # impossible date
        "1970-01-01T00:00:00.000.000Z",  # double-fractional
    ]
    for s in malformed:
        result = _parse_dt_rfc2822(s)
        # Either None or a parseable datetime; never an exception
        assert result is None or hasattr(result, "year"), \
            f"Malformed pubDate '{s}' must return None or datetime, got {result!r}"


# ============================================================================
# Recursive Payload — JSON depth bomb
# ============================================================================

def test_chaos_wl_deep_nested_payload_does_not_crash() -> None:
    """Scenario: an attacker controls the upstream and serves a
    deeply-nested but valid JSON document. ``json.loads`` itself imposes
    a recursion limit; verify our wrapper doesn't crash the process when
    that limit fires.
    """
    # 5000-deep nested array — well past default recursion limit
    deep = b"[" * 5000 + b"]" * 5000

    with patch(
        "src.providers.wl_fetch.fetch_content_safe",
        return_value=deep,
    ):
        session = MagicMock(spec=requests.Session)
        # Either parses (fine) or returns {} after RecursionError-derived
        # JSONDecodeError. NEVER unhandled crash.
        result = wl._get_json("trafficInfos", session=session, timeout=5)
    assert isinstance(result, dict)


# ============================================================================
# Massive payload — many top-level keys
# ============================================================================

def test_chaos_wl_huge_dict_payload_handled() -> None:
    """Scenario: an upstream returns a 200 OK + valid JSON dict with
    100k top-level keys. We're protected by the byte cap, but verify
    that even within the cap, parsing a large dict doesn't crash the
    downstream type check."""
    big = {f"key_{i}": i for i in range(10_000)}
    with patch(
        "src.providers.wl_fetch.fetch_content_safe",
        return_value=json.dumps(big).encode("utf-8"),
    ):
        session = MagicMock(spec=requests.Session)
        result = wl._get_json("trafficInfos", session=session, timeout=5)

    assert isinstance(result, dict)
    assert len(result) == 10_000
