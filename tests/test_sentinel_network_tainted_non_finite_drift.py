"""Sentinel PoC: non-finite literal defence drift across the **network-
tainted** / **env-tainted** / **state-sidecar** JSON parser landscape —
the symmetric companion to the on-disk reader closure of PR #1503.

PR #1503 closed the reader-side gap for the eleven committed-state-file
readers (``read_capped_json`` + 10 callers).  Every ``json.loads(...)``
call inside those readers now pins ``parse_constant`` +
``parse_float`` hooks that reject the non-finite literal family
(``NaN`` / ``Infinity`` / ``-Infinity`` / ``1e1000``-scientific-
overflow).  The drift this round closes:

* **18 network-tainted / env-tainted / state-sidecar JSON parsers**
  outside the Round-1503 inventory were left at lenient-mode
  ``json.loads(content)`` / ``response.json()`` — every one is the
  symmetric companion to the writer-pin family for a DIFFERENT taint
  channel.

Threat model
============

The Round-1503 channel was *committed on-disk state files*
(``data/first_seen.json``, ``data/stations.json``,
``cache/<provider>/events.json``, etc.).  The channel this round
closes is everything that PR #1503 did NOT enumerate:

1. **Network-tainted JSON (13 sites)** — every HTTP response parsed
   into the build pipeline:

   * Wiener Linien (``src/providers/wl_fetch.py:_get_json``)
   * Google Places (``src/places/client.py`` 200-branch + error-
     formatter branch)
   * HAFAS (``src/places/hafas_client.py``)
   * OSM Overpass (``src/places/osm_client.py``)
   * GitHub API issue-submit (``src/feed/reporting.py`` error branch
     + success branch)
   * Overpass smoke probe (``scripts/check_overpass_status.py``)
   * VOR access-id verifier (``scripts/verify_vor_access_id.py``)
   * Wien Baustellen (``scripts/update_baustellen_cache.py``)
   * VAO ``/departureBoard`` Hbf (``scripts/update_stammstrecke_hbf.py``)
   * VAO ``/trip`` legacy (``scripts/update_stammstrecke_status.py``)
   * VAO error envelope (``scripts/update_stammstrecke_status.py``)

   A compromised upstream / DNS-hijack / MITM ships a body containing
   ``NaN`` / ``Infinity`` / ``-Infinity`` / ``1e1000`` literals.  The
   lenient-mode ``response.json()`` / ``json.loads(content)`` returns
   a Python structure with ``float('nan')`` / ``float('inf')`` inside.
   The non-finite float then poisons:

   - **Comparisons** (``nan != nan`` is True — breaks every dedup
     invariant and timestamp ordering check).
   - **Arithmetic** (``nan + x`` is nan — silently corrupts latency
     averages, retention windows, delay calculations).
   - **Round-trip back to the writer** — the writer-pin
     (``allow_nan=False`` from Round 1485/1487/1488/1491) catches the
     non-finite at serialisation time and raises ``ValueError``,
     crashing the cron pipeline mid-write with no recovery.

2. **Env-tainted JSON (2 sites)** — the ``BOUNDINGBOX_VIENNA``
   override:

   * ``scripts/fetch_google_places_stations.py:_parse_bounding_box``
   * ``scripts/update_station_directory.py:_parse_bounding_box``

   A leaked CI env / compromised secret store / hostile operator
   plants ``{"min_lat": NaN, ...}`` into the env variable.  The
   downstream ``float(data["min_lat"])`` coercion lifts NaN into the
   ``BoundingBox`` dataclass — every place-fetch URL constructed from
   the bounds carries the non-finite, and the writer-pin round-trip
   crashes the next persist.

3. **Disk-tainted state sidecars missed by Round 1503 (3 sites)** —
   the on-disk reader closure named the canonical helpers but did
   NOT enumerate three sibling readers that share the same threat
   model:

   * ``src/build_feed.py:_read_state_capped`` — the second
     ``data/first_seen.json`` reader, called from ``_save_state``
     to merge with existing state.  Same file as the Round-1503
     ``_load_state`` reader but a separate function.
   * ``scripts/update_stammstrecke_status.py:_load_pending_trips``
     (``cache/stammstrecke/pending_trips.json``)
   * ``scripts/update_stammstrecke_status.py:_load_recently_finalised``
     (``cache/stammstrecke/recently_finalised.json``)

The fix shape mirrors PR #1503 exactly: every parser site pins the
two canonical hooks (``_reject_non_finite_constant`` +
``_reject_non_finite_float`` from ``src/utils/files.py``), either
directly as kwargs or via the new ``loads_finite()`` wrapper.

Impact symmetry
===============

The post-fix behaviour is identical to PR #1503 — both hooks raise
``json.JSONDecodeError`` (a ``ValueError`` subclass), which every
network/env/disk parser's existing
``except (ValueError, RecursionError, json.JSONDecodeError)`` handler
catches transparently.  No per-callsite ``except`` widening was
needed.

Coverage
========

* 5 canonical-helper behavioural tests for the new
  ``loads_finite()`` wrapper and the two existing hooks.
* 18 inventory pins (source-grep) — one per enumerated site.
* Multiple per-site behavioural PoCs covering both ``NaN`` /
  ``Infinity`` literal tokens AND ``1e1000`` scientific-notation
  overflow, plus a finite-round-trip regression guard.

Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.
"""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path
from typing import Any

import pytest
import requests


SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT = (
    "network/env/sidecar non-finite literal drift"
)


# ---------------------------------------------------------------------------
# Canonical-helper behavioural tests for the new ``loads_finite`` wrapper.
# ---------------------------------------------------------------------------


def test_loads_finite_rejects_NaN_literal_token() -> None:
    """``loads_finite`` MUST raise JSONDecodeError on a planted ``NaN`` token."""
    from src.utils.files import loads_finite

    with pytest.raises(json.JSONDecodeError):
        loads_finite('{"x": NaN}')


def test_loads_finite_rejects_Infinity_literal_tokens() -> None:
    """Both ``Infinity`` AND ``-Infinity`` tokens must be rejected."""
    from src.utils.files import loads_finite

    with pytest.raises(json.JSONDecodeError):
        loads_finite('{"x": Infinity}')
    with pytest.raises(json.JSONDecodeError):
        loads_finite('{"x": -Infinity}')


def test_loads_finite_rejects_scientific_overflow() -> None:
    """``1e1000`` overflow bypasses ``parse_constant`` but is caught by
    ``parse_float``."""
    from src.utils.files import loads_finite

    with pytest.raises(json.JSONDecodeError):
        loads_finite('{"x": 1e1000}')
    with pytest.raises(json.JSONDecodeError):
        loads_finite('{"x": -1e1000}')


def test_loads_finite_accepts_finite_payload() -> None:
    """The wrapper MUST NOT regress legitimate finite payloads."""
    from src.utils.files import loads_finite

    parsed = loads_finite(
        '{"lat": 48.18568, "lon": 16.37534, "count": 12, "name": "Hbf"}'
    )
    assert isinstance(parsed, dict)
    assert parsed["lat"] == pytest.approx(48.18568)
    assert parsed["lon"] == pytest.approx(16.37534)
    assert parsed["count"] == 12
    assert parsed["name"] == "Hbf"


def test_loads_finite_accepts_bytes_and_bytearray() -> None:
    """The wrapper must accept the same input types as ``json.loads``."""
    from src.utils.files import loads_finite

    assert loads_finite(b'{"x": 1.0}') == {"x": 1.0}
    assert loads_finite(bytearray(b'{"x": 2.0}')) == {"x": 2.0}


# ---------------------------------------------------------------------------
# Inventory pins (source-grep): every enumerated site must carry the hooks.
# ---------------------------------------------------------------------------


_NETWORK_HOOK_TOKENS = (
    # Either uses the new ``loads_finite`` wrapper, OR passes the two hooks
    # directly as kwargs (the ``response.json()`` shape).
    "loads_finite(",
    "parse_constant=_reject_non_finite_constant",
)


def _assert_non_finite_pin(func: Any, *, where: str) -> None:
    """Assert ``func`` source pins the non-finite defence in some form."""
    source = inspect.getsource(func)
    if "loads_finite(" in source:
        return  # via wrapper
    # Otherwise both hooks must be present as kwargs.
    assert "parse_constant=_reject_non_finite_constant" in source, (
        f"{where}: missing ``parse_constant=_reject_non_finite_constant`` "
        f"pin. A planted NaN / Infinity literal from the taint source "
        f"propagates as ``float('nan')`` / ``float('inf')`` past the "
        f"parse boundary into Python computation.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )
    assert "parse_float=_reject_non_finite_float" in source, (
        f"{where}: missing ``parse_float=_reject_non_finite_float`` "
        f"pin. A planted ``1e1000`` scientific-notation overflow bypasses "
        f"``parse_constant`` and lands ``float('inf')`` in the parsed "
        f"structure.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )


def test_inventory_loads_finite_helper_pins_both_hooks() -> None:
    """The new ``loads_finite`` wrapper must pin both hooks itself."""
    from src.utils.files import loads_finite

    source = inspect.getsource(loads_finite)
    assert "parse_constant=_reject_non_finite_constant" in source
    assert "parse_float=_reject_non_finite_float" in source


def test_inventory_wl_fetch_get_json_pins_hooks() -> None:
    from src.providers.wl_fetch import _get_json

    _assert_non_finite_pin(
        _get_json,
        where="src/providers/wl_fetch.py:_get_json (Wiener Linien API)",
    )


def test_inventory_places_client_post_pins_hooks() -> None:
    from src.places.client import GooglePlacesClient

    _assert_non_finite_pin(
        GooglePlacesClient._post,
        where=(
            "src/places/client.py:GooglePlacesClient._post "
            "(Google Places 200-branch)"
        ),
    )


def test_inventory_places_client_format_error_message_pins_hooks() -> None:
    from src.places.client import GooglePlacesClient

    _assert_non_finite_pin(
        GooglePlacesClient._format_error_message,
        where=(
            "src/places/client.py:GooglePlacesClient._format_error_message "
            "(Google Places error-formatter)"
        ),
    )


def test_inventory_hafas_client_pins_hooks() -> None:
    from src.places import hafas_client

    _assert_non_finite_pin(
        hafas_client._fetch_hafas_location,
        where=(
            "src/places/hafas_client.py:_fetch_hafas_location "
            "(HAFAS Mgate LocMatch upstream)"
        ),
    )


def test_inventory_osm_client_pins_hooks() -> None:
    from src.places.osm_client import OSMOverpassClient

    _assert_non_finite_pin(
        OSMOverpassClient._fetch_payload,
        where=(
            "src/places/osm_client.py:OSMOverpassClient._fetch_payload "
            "(OSM Overpass upstream)"
        ),
    )


def test_inventory_reporting_submit_pins_hooks() -> None:
    from src.feed.reporting import _GithubIssueReporter

    _assert_non_finite_pin(
        _GithubIssueReporter.submit,
        where=(
            "src/feed/reporting.py:_GithubIssueReporter.submit "
            "(GitHub API issue submission — error + success branches)"
        ),
    )


def test_inventory_check_overpass_status_pins_hooks() -> None:
    from scripts.check_overpass_status import _evaluate_response

    _assert_non_finite_pin(
        _evaluate_response,
        where=(
            "scripts/check_overpass_status.py:_evaluate_response "
            "(Overpass smoke probe)"
        ),
    )


def test_inventory_verify_vor_access_id_pins_hooks() -> None:
    """Source-grep the verify script's module-level body for the hook."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_vor_access_id.py"
    source = path.read_text(encoding="utf-8")
    assert "loads_finite(content.decode" in source, (
        "scripts/verify_vor_access_id.py: missing ``loads_finite`` "
        "wrapper at the VOR-response parse boundary.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )


def test_inventory_update_baustellen_cache_pins_hooks() -> None:
    from scripts.update_baustellen_cache import _load_json_from_content

    _assert_non_finite_pin(
        _load_json_from_content,
        where=(
            "scripts/update_baustellen_cache.py:_load_json_from_content "
            "(Wien Baustellen upstream)"
        ),
    )


def test_inventory_update_stammstrecke_hbf_pins_hooks() -> None:
    """Source-grep ``_fetch_departure_board`` for ``loads_finite``."""
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "update_stammstrecke_hbf.py"
    )
    source = path.read_text(encoding="utf-8")
    # Walk the function range around the post-VAO parse to assert the pin.
    assert "payload = loads_finite(content)" in source, (
        "scripts/update_stammstrecke_hbf.py: missing ``loads_finite`` "
        "wrapper at the VAO /departureBoard response parse.\n\nMarker: "
        f"{SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )


def test_inventory_update_stammstrecke_status_pending_trips_pins_hooks() -> None:
    from scripts.update_stammstrecke_status import _load_pending_trips

    _assert_non_finite_pin(
        _load_pending_trips,
        where=(
            "scripts/update_stammstrecke_status.py:_load_pending_trips "
            "(cache/stammstrecke/pending_trips.json state sidecar)"
        ),
    )


def test_inventory_update_stammstrecke_status_recently_finalised_pins_hooks() -> None:
    from scripts.update_stammstrecke_status import _load_recently_finalised

    _assert_non_finite_pin(
        _load_recently_finalised,
        where=(
            "scripts/update_stammstrecke_status.py:_load_recently_finalised "
            "(cache/stammstrecke/recently_finalised.json state sidecar)"
        ),
    )


def test_inventory_update_stammstrecke_status_trip_pins_hooks() -> None:
    """Source-grep the VAO /trip fetch helper for the pin."""
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "update_stammstrecke_status.py"
    )
    source = path.read_text(encoding="utf-8")
    # Two distinct callsites in this script: /trip parse + error-envelope decode.
    assert source.count("loads_finite(") >= 4, (
        "scripts/update_stammstrecke_status.py: expected >=4 "
        "``loads_finite`` callsites (pending_trips load + "
        "recently_finalised load + /trip parse + error-envelope decode) "
        f"\n\nMarker: {SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT}"
    )


def test_inventory_fetch_google_places_stations_pins_hooks() -> None:
    from scripts.fetch_google_places_stations import _parse_bounding_box

    _assert_non_finite_pin(
        _parse_bounding_box,
        where=(
            "scripts/fetch_google_places_stations.py:_parse_bounding_box "
            "(BOUNDINGBOX_VIENNA env override)"
        ),
    )


def test_inventory_update_station_directory_bounding_box_pins_hooks() -> None:
    from scripts.update_station_directory import _parse_bounding_box

    _assert_non_finite_pin(
        _parse_bounding_box,
        where=(
            "scripts/update_station_directory.py:_parse_bounding_box "
            "(BOUNDINGBOX_VIENNA env override — sibling)"
        ),
    )


def test_inventory_build_feed_read_state_capped_pins_hooks() -> None:
    from src import build_feed

    _assert_non_finite_pin(
        build_feed._read_state_capped,
        where=(
            "src/build_feed.py:_read_state_capped "
            "(disk-tainted sibling reader of data/first_seen.json — missed by Round 1503)"
        ),
    )


# ---------------------------------------------------------------------------
# Behavioural PoC: the new ``loads_finite`` wrapper at the wl_fetch site.
# ---------------------------------------------------------------------------


def test_poc_wl_fetch_get_json_rejects_planted_NaN(monkeypatch: pytest.MonkeyPatch) -> None:
    """A compromised Wiener Linien upstream returning ``NaN`` must NOT
    propagate a non-finite float into the parsed dict.

    PRE-FIX: ``json.loads(content)`` returns ``{"latency_ms":
    float('nan')}`` from a planted ``{"latency_ms": NaN}`` body.  The
    function passes the isinstance(data, dict) check and returns the
    NaN-bearing dict to the caller, who then performs latency
    arithmetic and persists the result — round-trip-crashing the
    writer pin (Round 1485) at the next state save.

    POST-FIX: ``loads_finite`` raises ``json.JSONDecodeError``, the
    surrounding ``except (ValueError, json.JSONDecodeError,
    RecursionError)`` returns ``{}``, and the caller falls through
    to the empty-payload recovery path.

    Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.
    """
    from src.providers import wl_fetch

    def _mock_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return b'{"latency_ms": NaN, "ok": true}'

    monkeypatch.setattr(wl_fetch, "fetch_content_safe", _mock_fetch)
    monkeypatch.setattr(wl_fetch, "validate_http_url", lambda _u: True)

    # Use the public _get_json entry point; the inner _fetch closure
    # runs over a Session passed via session_with_retries — patch it.
    class _FakeSession:
        def __enter__(self) -> _FakeSession:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *_a, **_k: _FakeSession())

    result = wl_fetch._get_json("trafficInfoList", session=_FakeSession())
    # Post-fix: planted NaN is treated as a decode failure → {}.
    assert result == {}, (
        "wl_fetch._get_json failed to reject planted NaN literal — "
        "compromised Wiener Linien upstream propagates ``float('nan')`` "
        "into the build pipeline."
    )


def test_poc_wl_fetch_get_json_rejects_planted_scientific_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``1e1000`` upstream value must NOT propagate as ``float('inf')``."""
    from src.providers import wl_fetch

    def _mock_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return b'{"latency_ms": 1e1000}'

    monkeypatch.setattr(wl_fetch, "fetch_content_safe", _mock_fetch)
    monkeypatch.setattr(wl_fetch, "validate_http_url", lambda _u: True)

    class _FakeSession:
        def __enter__(self) -> _FakeSession:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *_a, **_k: _FakeSession())

    result = wl_fetch._get_json("trafficInfoList", session=_FakeSession())
    assert result == {}


def test_poc_wl_fetch_get_json_finite_payload_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finite upstream payload must still parse correctly post-fix."""
    from src.providers import wl_fetch

    def _mock_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return b'{"latency_ms": 12.5, "ok": true}'

    monkeypatch.setattr(wl_fetch, "fetch_content_safe", _mock_fetch)
    monkeypatch.setattr(wl_fetch, "validate_http_url", lambda _u: True)

    class _FakeSession:
        def __enter__(self) -> _FakeSession:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *_a, **_k: _FakeSession())

    result = wl_fetch._get_json("trafficInfoList", session=_FakeSession())
    assert result == {"latency_ms": 12.5, "ok": True}
    assert math.isfinite(result["latency_ms"])


# ---------------------------------------------------------------------------
# Behavioural PoC: response.json() shape (Google Places / OSM / HAFAS / GitHub).
# ---------------------------------------------------------------------------


def _make_response(body: bytes, status_code: int = 200) -> requests.Response:
    """Construct a real ``requests.Response`` with the given body bytes.

    We populate the internal ``_content`` and ``_content_consumed`` flags
    so ``response.json()`` runs the actual stdlib parse path (with our
    pinned hooks), not a mock that bypasses the security boundary.
    """
    resp = requests.Response()
    resp._content = body
    resp._content_consumed = True
    resp.status_code = status_code
    resp.encoding = "utf-8"
    resp.url = "https://example.com/test"
    return resp


def test_poc_response_json_with_hooks_rejects_NaN_token() -> None:
    """Sanity check: ``response.json(parse_constant=..., parse_float=...)``
    actually rejects planted NaN.

    This proves that ``requests.Response.json(**kwargs)`` correctly
    forwards the hooks to the underlying ``json.loads`` — the
    foundation that every ``response.json()`` callsite in the
    inventory pins above relies on.
    """
    from src.utils.files import (
        _reject_non_finite_constant,
        _reject_non_finite_float,
    )

    resp = _make_response(b'{"x": NaN}')
    with pytest.raises((ValueError, requests.exceptions.JSONDecodeError)):
        resp.json(
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )


def test_poc_response_json_with_hooks_rejects_scientific_overflow() -> None:
    """``response.json(**hooks)`` must reject ``1e1000`` overflow."""
    from src.utils.files import (
        _reject_non_finite_constant,
        _reject_non_finite_float,
    )

    resp = _make_response(b'{"x": 1e1000}')
    with pytest.raises((ValueError, requests.exceptions.JSONDecodeError)):
        resp.json(
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )


def test_poc_response_json_with_hooks_accepts_finite() -> None:
    """``response.json(**hooks)`` MUST NOT regress finite payloads."""
    from src.utils.files import (
        _reject_non_finite_constant,
        _reject_non_finite_float,
    )

    resp = _make_response(b'{"lat": 48.18, "lon": 16.37}')
    payload = resp.json(
        parse_constant=_reject_non_finite_constant,
        parse_float=_reject_non_finite_float,
    )
    assert payload == {"lat": 48.18, "lon": 16.37}


# ---------------------------------------------------------------------------
# Behavioural PoC: env-tainted BOUNDINGBOX_VIENNA parser.
# ---------------------------------------------------------------------------


def test_poc_fetch_google_places_bounding_box_rejects_NaN() -> None:
    """A hostile ``BOUNDINGBOX_VIENNA`` env containing NaN must raise.

    PRE-FIX: ``json.loads('{"min_lat": NaN, ...}')`` returns
    ``{"min_lat": float('nan'), ...}``, then ``float(data["min_lat"])``
    is ``float('nan')``, and the ``BoundingBox`` dataclass accepts it.
    Every place-fetch URL constructed from the bounds carries the
    non-finite coordinate.

    POST-FIX: ``loads_finite`` raises ``json.JSONDecodeError``, the
    outer ``except`` re-raises as ``ValueError("BOUNDINGBOX_VIENNA must
    be valid JSON")``.
    """
    from scripts.fetch_google_places_stations import _parse_bounding_box

    with pytest.raises(ValueError, match="BOUNDINGBOX_VIENNA"):
        _parse_bounding_box('{"min_lat": NaN, "min_lng": 16.0, "max_lat": 48.5, "max_lng": 16.8}')


def test_poc_fetch_google_places_bounding_box_rejects_scientific_overflow() -> None:
    """A ``1e1000`` env override must raise the same canonical error."""
    from scripts.fetch_google_places_stations import _parse_bounding_box

    with pytest.raises(ValueError, match="BOUNDINGBOX_VIENNA"):
        _parse_bounding_box(
            '{"min_lat": 1e1000, "min_lng": 16.0, "max_lat": 48.5, "max_lng": 16.8}'
        )


def test_poc_fetch_google_places_bounding_box_finite_passes() -> None:
    """A finite-coordinate env override must round-trip post-fix."""
    from scripts.fetch_google_places_stations import _parse_bounding_box

    bbox = _parse_bounding_box(
        '{"min_lat": 48.1, "min_lng": 16.2, "max_lat": 48.3, "max_lng": 16.5}'
    )
    assert bbox is not None
    assert math.isfinite(bbox.min_lat)
    assert math.isfinite(bbox.min_lng)
    assert math.isfinite(bbox.max_lat)
    assert math.isfinite(bbox.max_lng)


def test_poc_update_station_directory_bounding_box_rejects_NaN() -> None:
    """Mirror PoC for the sibling ``BOUNDINGBOX_VIENNA`` parser."""
    from scripts.update_station_directory import _parse_bounding_box

    with pytest.raises(ValueError, match="BOUNDINGBOX_VIENNA"):
        _parse_bounding_box(
            '{"min_lat": Infinity, "min_lng": 16.0, "max_lat": 48.5, "max_lng": 16.8}'
        )


# ---------------------------------------------------------------------------
# Behavioural PoC: disk-sidecar readers missed by Round 1503.
# ---------------------------------------------------------------------------


def test_poc_build_feed_read_state_capped_rejects_planted_NaN(tmp_path: Path) -> None:
    """The sibling state reader ``_read_state_capped`` must reject NaN.

    PRE-FIX: a poisoned ``data/first_seen.json`` (the same file as the
    Round-1503 ``_load_state`` reader, but read from the save path's
    merge step) returns ``{"k": {"first_seen": NaN}}`` lenient-mode.
    The downstream merge propagates NaN past every comparison.

    POST-FIX: ``parse_constant`` raises ``json.JSONDecodeError``,
    caught by the broad ``except Exception`` and rewritten to ``{}``
    per the existing corrupt-state recovery contract.

    Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.
    """
    from src import build_feed

    poisoned = tmp_path / "first_seen.json"
    poisoned.write_bytes(b'{"id1": {"first_seen": "2026-05-15T00:00:00+00:00", "delay": NaN}}')

    result = build_feed._read_state_capped(poisoned)
    assert result == {}, (
        "build_feed._read_state_capped failed to reject planted NaN "
        "literal — the data/first_seen.json merge reader propagates "
        "``float('nan')`` past the writer-pin defence."
    )


def test_poc_build_feed_read_state_capped_finite_payload_round_trips(tmp_path: Path) -> None:
    """The disk-sidecar reader MUST NOT regress finite payloads."""
    from src import build_feed

    healthy = tmp_path / "first_seen.json"
    healthy.write_text(
        json.dumps(
            {"id1": {"first_seen": "2026-05-15T00:00:00+00:00"}},
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    result = build_feed._read_state_capped(healthy)
    assert isinstance(result, dict)
    assert "id1" in result
    assert result["id1"]["first_seen"] == "2026-05-15T00:00:00+00:00"


def test_poc_stammstrecke_pending_trips_rejects_planted_NaN(tmp_path: Path) -> None:
    """The pending-trips state sidecar must reject NaN.

    PRE-FIX: a poisoned ``cache/stammstrecke/pending_trips.json``
    landed ``float('nan')`` into the per-trip delay field, which then
    propagated past every comparison in the ledger-merge logic.

    POST-FIX: ``loads_finite`` raises ``json.JSONDecodeError``,
    caught by the ``except (ValueError, RecursionError)`` and routed
    to the empty-ledger fresh-start recovery path with a WARNING
    diagnostic.
    """
    from scripts.update_stammstrecke_status import _load_pending_trips

    poisoned = tmp_path / "pending_trips.json"
    poisoned.write_bytes(b'{"key": {"delay": NaN, "name": "Test"}}')

    result = _load_pending_trips(poisoned)
    assert result == {}, (
        "_load_pending_trips failed to reject planted NaN literal — "
        "the pending-trips state sidecar propagates ``float('nan')`` "
        "past the writer-pin defence."
    )


def test_poc_stammstrecke_recently_finalised_rejects_planted_Infinity(tmp_path: Path) -> None:
    """The recently-finalised state sidecar must reject Infinity."""
    from scripts.update_stammstrecke_status import _load_recently_finalised

    poisoned = tmp_path / "recently_finalised.json"
    poisoned.write_bytes(b'{"key": Infinity}')

    result = _load_recently_finalised(poisoned)
    assert result == {}


# ---------------------------------------------------------------------------
# Behavioural PoC: the verify_vor_access_id loads_finite path.
# ---------------------------------------------------------------------------


def test_poc_loads_finite_at_verify_vor_access_id_call_shape() -> None:
    """Behavioural check at the canonical wrapper level — proves the
    fix shape used by the ``verify_vor_access_id.py`` callsite is
    correct (``loads_finite(content.decode("utf-8"))``)."""
    from src.utils.files import loads_finite

    content = b'{"latitude": NaN}'
    with pytest.raises(json.JSONDecodeError):
        loads_finite(content.decode("utf-8"))


# ---------------------------------------------------------------------------
# Round-trip symmetry: every fix is the network/env/disk-tainted companion
# of the writer-pin family (Round 1485/1487/1488/1491).
# ---------------------------------------------------------------------------


def test_round_trip_symmetry_loads_finite_then_dumps_rejects_planted() -> None:
    """End-to-end proof of symmetry.

    A compromised upstream returns ``{"v": NaN}``.

    1. ``loads_finite(body)`` raises ``json.JSONDecodeError`` at the
       reader end (this PR's defence).
    2. Without the reader pin, ``float('nan')`` would round-trip back
       to the writer.  Demonstrate that ``json.dumps(..., allow_nan=
       False)`` (the canonical writer pin from Round 1485) would catch
       it on the way out — proving the two defences together close
       the loop at BOTH ends.
    """
    from src.utils.files import loads_finite

    # End A: reader.
    with pytest.raises(json.JSONDecodeError):
        loads_finite(b'{"v": NaN}')

    # End B: writer (existing canonical pin).
    nan_val = float("nan")
    with pytest.raises(ValueError, match="(?i)nan|finite|out of range|not allowed"):
        json.dumps({"v": nan_val}, allow_nan=False)
