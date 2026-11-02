"""Teil 2 der Tests aus tests/scripts/test_update_stammstrecke_status.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import requests

from scripts import update_stammstrecke_status as script  # noqa: E402

from ._support import (
    VIENNA_TZ,
    _make_http_error,
    _make_pending,
    _make_response,
    _trip,
)


def test_extract_response_header_caps_oversize_value() -> None:
    """A planted huge ``Server`` header (zero-leak path: server-set, but
    an attacker-controlled CDN could still inject a multi-KiB string)
    is truncated at the body-keys cap with an ellipsis suffix."""

    err = _make_http_error(status_code=400, body=b"")
    assert err.response is not None
    err.response.headers["Server"] = "A" * 5000
    rendered = script._extract_response_header(err, "Server")
    assert len(rendered) == script._BODY_KEYS_MAX_LEN + 1  # +1 for the ellipsis
    assert rendered.endswith("…")


def test_extract_response_header_strips_whitespace_only() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert err.response is not None
    err.response.headers["Server"] = "   "
    assert script._extract_response_header(err, "Server") == "[MISSING]"


def test_process_direction_logs_status_and_error_code_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _stable_now: datetime,
) -> None:
    """The new HTTPError branch in :func:`_process_direction` must surface
    BOTH the HTTP status code and the VAO ``errorCode`` so the next
    workflow run reveals which failure mode tripped the request without
    leaking the post-VorAuth URL."""

    err = _make_http_error(
        status_code=401,
        body=json.dumps(
            {
                "errorCode": "H730",
                "errorText": "Authentication failed",
                "internalErrorCode": "AUTH_INV",
                "internalErrorText": "validator: bad accessId hash",
                "requestId": "8e7f2c9b-1234-5678-90ab-cdef12345678",
            }
        ).encode("utf-8"),
    )

    # Plant a few server-set headers so the new diagnostics surface the
    # gateway/auth/payload shape that triggered the failure.
    assert err.response is not None
    err.response.headers["Content-Type"] = "application/json"
    err.response.headers["Content-Length"] = "92"
    err.response.headers["Server"] = "VAO-RestProxy/1.11.0"
    err.response.headers["WWW-Authenticate"] = "Bearer realm=\"vao\""

    def boom(*args: object, **kwargs: object) -> object:
        raise err

    monkeypatch.setattr(script, "_query_trips", boom)

    with caplog.at_level(logging.WARNING, logger="update_stammstrecke_status"):
        status = script._process_direction(
            requests.Session(),
            script.DIRECTIONS[0],
            {},  # empty pending-trip ledger — error path never touches it
            when=_stable_now,
        )
    assert status == "error"
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "HTTP 401" in rendered
    assert "H730" in rendered
    # Body-keys diagnostic must surface the canonical envelope shape
    # without leaking any value.
    assert "body_keys=errorCode" in rendered
    # The four server-set header diagnostics must appear so a future
    # cron failure exposes the gateway / auth / payload shape
    # without further code changes.
    assert "ct=application/json" in rendered
    assert "cl=92" in rendered
    assert "server=VAO-RestProxy/1.11.0" in rendered
    assert "www_auth=Bearer" in rendered
    # The three new error-body diagnostics from the 2026-05-09
    # accessId-echoing audit must also appear: errorCode-length
    # (length-only fingerprint), errorText (human-readable diagnostic
    # string), and requestId (server-side trace ID for support).
    assert "code_len=4" in rendered  # H730 is 4 chars
    assert "err_text=Authentication failed" in rendered
    assert "req_id=8e7f2c9b-1234-5678-90ab-cdef12345678" in rendered
    # The internalError* diagnostics added on 2026-05-09 (sixth
    # iteration after VAO's 333-byte body exposed these fields) must
    # also surface so the next failure shape is one log-line away
    # from triage instead of another speculation cycle.
    assert "int_code=AUTH_INV" in rendered
    assert "int_text=validator: bad accessId hash" in rendered


def test_collect_delays_rejects_multi_ride_leg_trips() -> None:
    """Direct connections only: a 2-ride-leg trip must not contribute."""
    extra = {
        "type": "JNY",
        "name": "S 7",
        "category": "S",
        "Origin": {
            "name": "Wien Mitte",
            "date": "2026-05-09",
            "time": "08:30:00",
            "rtTime": "08:35:00",
        },
        "Destination": {
            "name": "Wien Meidling",
            "date": "2026-05-09",
            "time": "08:50:00",
        },
    }
    trips = [
        _trip(leg_name="S 1", delay_minutes=10, extra_legs=[extra]),
    ]
    assert script._collect_sbahn_delays_minutes(trips) == []


def test_collect_delays_tolerates_walk_legs_around_ride() -> None:
    """A walk leg before/after the single ride leg is OK (still "direct")."""
    walk_before = {
        "type": "WALK",
        "Origin": {"name": "Eingang"},
        "Destination": {"name": "Bahnsteig"},
    }
    walk_after = {
        "type": "WALK",
        "Origin": {"name": "Bahnsteig Z"},
        "Destination": {"name": "Ausgang"},
    }
    trips = [
        _trip(leg_name="S 1", delay_minutes=11, extra_legs=[walk_before, walk_after]),
    ]
    assert script._collect_sbahn_delays_minutes(trips) == [11.0]


def test_collect_delays_handles_missing_leg_list() -> None:
    """A misshapen Trip without ``LegList`` must skip silently."""
    trips: list[dict[str, Any]] = [{}]
    assert script._collect_sbahn_delays_minutes(trips) == []


def test_collect_delays_handles_single_leg_object_payload() -> None:
    """Some VAO peers serialise ``Leg`` as a single object (not a list)."""
    leg = _trip(leg_name="S 1", delay_minutes=11)["LegList"]["Leg"][0]
    trips = [{"LegList": {"Leg": leg}}]  # bare object, not list
    assert script._collect_sbahn_delays_minutes(trips) == [11.0]


def test_format_minutes_strips_trailing_zero() -> None:
    assert script._format_minutes(12.0) == "12"
    assert script._format_minutes(12.5) == "12.5"
    assert script._format_minutes(11.04) == "11"  # rounds to 11.0
    assert script._format_minutes(11.05) == "11.1"


def test_directions_table_covers_both_targets() -> None:
    """Sanity: DIRECTIONS contains exactly the two Stammstrecke directions."""

    targets = {d.target_label for d in script.DIRECTIONS}
    prefixes = {d.identity_prefix for d in script.DIRECTIONS}
    # Northbound label was renamed Floridsdorf → Praterstern on 2026-05-15
    # to match the symmetric "next Stammstrecke stop after Hbf" naming
    # (south = Meidling, north = Praterstern) and accommodate short-turn
    # trains that terminate at Praterstern without reaching Floridsdorf.
    assert targets == {"Meidling", "Praterstern"}
    assert prefixes == {
        "stammstrecke_delay_meidling",
        "stammstrecke_delay_praterstern",
    }


def test_directions_use_pinned_vor_ids() -> None:
    """A drift in ``data/stations.json`` must NOT silently re-point the
    monitor at a different stop. The VOR IDs are pinned in the script
    constants and asserted here so a future rename trips the test."""

    assert script.FLORIDSDORF_VOR_ID == "490033400"
    assert script.MEIDLING_VOR_ID == "490101500"
    origins = {d.origin_id for d in script.DIRECTIONS}
    destinations = {d.destination_id for d in script.DIRECTIONS}
    assert origins == {"490033400", "490101500"}
    assert destinations == {"490033400", "490101500"}


def test_short_target_label_resolves_via_station_directory() -> None:
    """``_short_target_label`` must round-trip canonical Vienna stations."""

    assert script._short_target_label("Wien Meidling") == "Meidling"
    assert script._short_target_label("Wien Floridsdorf") == "Floridsdorf"
    assert script._short_target_label("Meidling") == "Meidling"
    assert script._short_target_label("Floridsdorf") == "Floridsdorf"


def test_short_target_label_strips_wien_prefix_on_directory_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to a literal-with-prefix-stripped form on directory miss."""

    monkeypatch.setattr(script, "canonical_name", lambda _name: None)
    monkeypatch.setattr(script, "display_name", lambda _name: "")
    assert script._short_target_label("Wien Meidling") == "Meidling"
    assert script._short_target_label("Floridsdorf") == "Floridsdorf"


def test_short_target_label_handles_directory_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash inside the directory lookup must NOT propagate out."""

    def boom(_name: str) -> str | None:
        raise RuntimeError("stations.json corrupt / unreadable")

    monkeypatch.setattr(script, "canonical_name", boom)
    monkeypatch.setattr(script, "display_name", lambda _name: "")
    assert script._short_target_label("Wien Meidling") == "Meidling"


def test_breaker_config_aligns_with_outage_budget() -> None:
    """Pin the breaker constants documented in the module docstring."""

    assert script.BREAKER_FAILURE_THRESHOLD == 10
    assert script.BREAKER_RECOVERY_TIMEOUT == 3600.0


def test_max_trips_per_query_is_pinned_to_six() -> None:
    """Pin ``MAX_TRIPS_PER_QUERY`` to the VAO contractual maximum.

    The VAO ``/trip`` endpoint accepts ``numF`` in the range 1..6.
    The 2026-05-09 Senior-API-Integration audit bumped the value from
    five to six so the per-sample mean is computed over the largest
    sample VAO permits in a single quota slot — important because the
    ``maxChange=0`` filter typically yields 4-6 S-Bahn legs after the
    strict-S product filter, and one extra data point increases
    sample stability without inflating quota usage.
    """

    assert script.MAX_TRIPS_PER_QUERY == 6


def test_query_timeout_bound_below_max() -> None:
    """``QUERY_TIMEOUT`` must stay strictly below ``MAX_QUERY_TIMEOUT``
    so the per-call HTTP budget is finite even after a future tweak."""

    assert 0 < script.QUERY_TIMEOUT <= script.MAX_QUERY_TIMEOUT


# ---- _query_trips parameter-shape tests -----------------------------------


def test_query_trips_passes_canonical_parameters(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """Pin the wire-format parameters: originId/destId with extId:: prefix
    in value, no format= query param, Accept header for negotiation.

    The 2026-05-09 audit (third iteration) established that VAO accepts
    external station IDs via ``originId=extId::<bare-numeric>`` —
    encoding the type via the value prefix per the
    "Identifikationsarten von Ortsobjekten" chapter of the
    Handbuch_VAO_ReST_API_latest.pdf. The previous attempts
    (PR #1387 ``originExtId=<bare-numeric>``) yielded HTTP 400 with
    empty body on this VAO instance; this iteration matches the
    ``trip.md`` curl example shape exactly minus the ``extId::``
    prefix that the manual mandates for non-OGD station IDs.
    """

    captured: dict[str, Any] = {}

    def fake_request(
        session: Any, endpoint: str, *, params: dict[str, str], **kwargs: Any
    ) -> requests.Response:
        captured["endpoint"] = endpoint
        captured["params"] = dict(params)
        captured["kwargs"] = kwargs
        return _make_response(status_code=200, body=b'{"Trip": []}')

    monkeypatch.setattr(script, "request_safe", fake_request)
    # Bypass the quota gate — the integration of charge → request is
    # tested separately below.
    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)

    direction = script.DIRECTIONS[0]
    trips = script._query_trips(session=cast(requests.Session, object()), direction=direction, when=_stable_now)
    assert trips == []
    assert captured["endpoint"].endswith("/trip")
    params = captured["params"]
    # ``format=json`` is intentionally absent — content negotiation is
    # handled exclusively via the Accept header so the request payload
    # exactly matches the trip.md curl example shape (which lists no
    # ``format`` parameter).
    assert "format" not in params
    # Bare-numeric station IDs in ``originId``/``destId`` — matches
    # the ``trip.md`` curl example AND the working
    # ``_fetch_departure_board_for_station`` shape in
    # ``src/providers/vor.py``. The 2026-05-09 cron run revealed that
    # VAO rejects the ``extId::<id>`` value-prefix form (Geminis
    # hypothesis from the manual) with HTTP 400 ``"location missing
    # or invalid (LOCATION)"``.
    assert params["originId"] == direction.origin_id
    assert params["destId"] == direction.destination_id
    assert "originExtId" not in params  # not the canonical VAO form
    assert "destExtId" not in params
    # The ``extId::`` value-prefix is explicitly absent — VAO rejects it.
    assert "extId::" not in params["originId"]
    assert "extId::" not in params["destId"]
    assert params["numF"] == "6"  # pinned MAX_TRIPS_PER_QUERY (VAO max)
    assert params["maxChange"] == "0"  # direct-connections-only
    assert params["rtMode"] == "SERVER_DEFAULT"
    assert params["date"] == _stable_now.strftime("%Y-%m-%d")
    assert params["time"] == _stable_now.strftime("%H:%M")
    # Content negotiation lives entirely in the Accept header now.
    assert captured["kwargs"]["headers"]["Accept"] == "application/json"
    assert captured["kwargs"]["allowed_content_types"] == ("application/json",)
    # CRITICAL: ``raise_for_status=False`` must be passed so the
    # request_safe except path does not close the response stream
    # before the body is read. See the docstring on ``_query_trips``
    # in the script for the full root-cause analysis.
    assert captured["kwargs"]["raise_for_status"] is False
    assert captured["kwargs"]["method"] == "GET"
    # Timeout is bound below MAX_QUERY_TIMEOUT.
    assert captured["kwargs"]["timeout"] <= script.MAX_QUERY_TIMEOUT


def test_query_trips_raises_http_error_with_populated_body_on_4xx(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """A 4xx response from request_safe must produce an HTTPError whose
    ``.response.content`` is fully populated — the diagnostic helpers
    downstream need the body to triage the failure shape.

    This is the regression-defence for the 2026-05-09 cron behaviour
    where ``fetch_content_safe(raise_for_status=True)`` closed the
    stream before the body was read, leaving ``response.content``
    empty even when ``Content-Length`` confirmed bytes were sent.
    """

    body_bytes = json.dumps({"errorCode": "H890"}).encode("utf-8")

    def fake_request(*args: Any, **kwargs: Any) -> requests.Response:
        # Mirror the production path: request_safe always returns a
        # response with ``_content`` already attached, even on 4xx,
        # because we passed ``raise_for_status=False``.
        return _make_response(status_code=400, body=body_bytes)

    monkeypatch.setattr(script, "request_safe", fake_request)
    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)

    with pytest.raises(requests.HTTPError) as exc_info:
        script._query_trips(
            session=cast(requests.Session, object()),
            direction=script.DIRECTIONS[0],
            when=_stable_now,
        )
    # The exception's response carries the body for downstream
    # diagnostic-extraction.
    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 400
    assert exc_info.value.response.content == body_bytes
    # And the body is decode-able by the same path the diagnostic
    # helpers use.
    assert script._extract_vao_error_code(exc_info.value) == "H890"


def test_query_trips_normalises_single_trip_payload(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """A bare-object ``Trip`` field (single-element list collapsed) must
    still produce a list."""

    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)
    trip_obj = _trip(leg_name="S 1", delay_minutes=10)

    def fake_request(*args: Any, **kwargs: Any) -> requests.Response:
        return _make_response(
            status_code=200,
            body=json.dumps({"Trip": trip_obj}).encode("utf-8"),
        )

    monkeypatch.setattr(script, "request_safe", fake_request)

    trips = script._query_trips(
        session=cast(requests.Session, object()), direction=script.DIRECTIONS[0], when=_stable_now
    )
    assert isinstance(trips, list)
    assert len(trips) == 1


def test_query_trips_raises_on_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """Any non-dict top-level payload must raise so the per-direction
    error-isolation branch runs."""

    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)
    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *a, **kw: _make_response(status_code=200, body=b'[1,2,3]'),
    )

    with pytest.raises(TypeError):
        script._query_trips(
            session=cast(requests.Session, object()), direction=script.DIRECTIONS[0], when=_stable_now
        )


def test_process_direction_charges_quota_before_breaker(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """Quota is reserved by ``_process_direction`` BEFORE the breaker-wrapped
    fetch, so a quota-exhausted run never sends a request. The charge was moved
    out of ``_query_trips`` to the caller so a ``_QuotaExceeded`` (a local budget
    signal) cannot count toward the circuit breaker's failure threshold."""

    call_order: list[str] = []

    monkeypatch.setattr(script, "_charge_one_request", lambda _now: call_order.append("charge"))

    def fake_query(*args: Any, **kwargs: Any) -> list[Any]:
        call_order.append("fetch")
        return []

    monkeypatch.setattr(script, "_query_trips", fake_query)
    script._BREAKER.reset()

    state: dict[str, Any] = {}
    result = script._process_direction(
        cast(requests.Session, object()), script.DIRECTIONS[0], state, when=_stable_now
    )
    assert call_order == ["charge", "fetch"]
    assert result == "ok"


def test_process_direction_quota_exhausted_does_not_trip_breaker(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """When the quota gate raises, no network call is made AND the circuit
    breaker's failure counter is NOT incremented. Pre-fix the charge ran inside
    the breaker-wrapped ``_query_trips``, so ~10 quota-blocked ticks OPENed the
    breaker for an hour and silently skipped the first ticks after the midnight
    quota reset."""

    def raising_charge(_now: datetime) -> None:
        raise script._QuotaExceeded("daily limit reached")

    fetched = {"called": False}

    def fake_query(*args: Any, **kwargs: Any) -> list[Any]:
        fetched["called"] = True
        return []

    monkeypatch.setattr(script, "_charge_one_request", raising_charge)
    monkeypatch.setattr(script, "_query_trips", fake_query)
    script._BREAKER.reset()
    failures_before = script._BREAKER.consecutive_failures

    state: dict[str, Any] = {}
    result = script._process_direction(
        cast(requests.Session, object()), script.DIRECTIONS[0], state, when=_stable_now
    )
    assert result == "quota_exceeded"
    assert fetched["called"] is False
    assert script._BREAKER.consecutive_failures == failures_before


def test_identity_key_is_deterministic_and_collision_free() -> None:
    """Same fields → same key; differing in any field → different key."""
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    key_a = script._identity_key("Meidling", "S 1", sched)
    key_b = script._identity_key("Meidling", "S 1", sched)
    assert key_a == key_b
    # Direction differs.
    assert script._identity_key("Floridsdorf", "S 1", sched) != key_a
    # Line name differs.
    assert script._identity_key("Meidling", "S 80", sched) != key_a
    # Scheduled differs.
    other = sched + timedelta(minutes=15)
    assert script._identity_key("Meidling", "S 1", other) != key_a


def test_identity_key_trims_whitespace_in_line_name() -> None:
    """Whitespace-only difference in line name must not produce a new key."""
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    assert script._identity_key("Meidling", "S 1", sched) == script._identity_key(
        "Meidling", " S 1 ", sched
    )


def test_purge_stale_entries_drops_only_entries_older_than_cutoff() -> None:
    """Entries with ``last_seen_at < cutoff`` are removed; others survive."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    cutoff = now - timedelta(hours=6)
    fresh = _make_pending(name="S 1", last_seen_at=now - timedelta(hours=2))
    stale = _make_pending(name="S 80", last_seen_at=now - timedelta(hours=12))
    state: dict[str, script._PendingTrip] = {
        "fresh-key": fresh,
        "stale-key": stale,
    }
    removed = script._purge_stale_entries(state, cutoff=cutoff)
    assert removed == 1
    assert "fresh-key" in state
    assert "stale-key" not in state


def test_load_pending_trips_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """A non-existent state file MUST yield an empty dict, never raise."""
    assert script._load_pending_trips(tmp_path / "missing.json") == {}


def test_load_pending_trips_returns_empty_when_file_empty(tmp_path: Path) -> None:
    """An empty state file is treated like a missing file (fresh start)."""
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    assert script._load_pending_trips(path) == {}


def test_load_pending_trips_recovers_from_corrupt_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt JSON in the state file is logged at WARNING and recovered."""
    path = tmp_path / "corrupt.json"
    path.write_text("{not even close to json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="update_stammstrecke_status"):
        result = script._load_pending_trips(path)
    assert result == {}
    assert any("korrupt" in r.getMessage() for r in caplog.records)


def test_load_pending_trips_drops_malformed_entries_but_keeps_valid_ones(
    tmp_path: Path,
) -> None:
    """One malformed entry alongside a good one must not poison the loader."""
    payload = {
        "valid-key": {
            "direction": "Meidling",
            "name": "S 1",
            "scheduled": "2026-05-09T08:00:00+02:00",
            "latest_delay_minutes": 4.0,
            "last_seen_at": "2026-05-09T08:00:00+02:00",
        },
        # Missing ``scheduled`` → dropped.
        "bad-key": {
            "direction": "Meidling",
            "name": "S 80",
            "latest_delay_minutes": 9.0,
            "last_seen_at": "2026-05-09T08:00:00+02:00",
        },
        # Top-level value isn't a mapping → dropped.
        "wrong-type": "not-a-mapping",
    }
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = script._load_pending_trips(path)
    # After the HIGH-1 fix the loader rebuilds the key via _identity_key,
    # so the raw disk key "valid-key" is replaced by the canonical form.
    sched = datetime.fromisoformat("2026-05-09T08:00:00+02:00")
    canonical_key = script._identity_key("Meidling", "S1", sched)
    assert set(result) == {canonical_key}
    assert result[canonical_key].name == "S1"
    assert result[canonical_key].latest_delay_minutes == 4.0


def test_save_pending_trips_round_trips_through_load(tmp_path: Path) -> None:
    """Save then load must reproduce the same in-memory state."""
    path = tmp_path / "state.json"
    state_in = {
        script._identity_key(
            "Meidling",
            "S 1",
            datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        ): _make_pending(latest_delay_minutes=4.5),
    }
    assert script._save_pending_trips(path, state_in) is True
    state_out = script._load_pending_trips(path)
    assert set(state_out) == set(state_in)
    only_key = next(iter(state_in))
    assert state_out[only_key].latest_delay_minutes == 4.5


def test_save_pending_trips_uses_atomic_write(tmp_path: Path) -> None:
    """A successful save replaces the target without leaving tmp files."""
    path = tmp_path / "state.json"
    state_in = {
        "a-key": _make_pending(latest_delay_minutes=1.0),
    }
    assert script._save_pending_trips(path, state_in) is True
    # No stray tmp files left behind in the directory.
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["state.json"]


# ---- Per-leg observation flow ---------------------------------------------


def test_collect_sbahn_leg_observations_returns_full_records() -> None:
    """The collector emits records with canonical name + scheduled + delay.

    H1 normalisation: the canonical form strips internal whitespace and
    upper-cases the result, so ``"S 1"`` and ``"S1"`` both produce
    ``"S1"`` here. This collapses VAO format drift before the value
    can ever drive an identity key.
    """
    trips = [
        _trip(leg_name="S 1", delay_minutes=4, leg_origin_time="08:00:00"),
        _trip(leg_name="S 80", delay_minutes=12, leg_origin_time="08:15:00"),
    ]
    observations = script._collect_sbahn_leg_observations(trips)
    assert [obs.name for obs in observations] == ["S1", "S80"]
    assert [obs.delay_minutes for obs in observations] == [4.0, 12.0]
    assert observations[0].scheduled == datetime(
        2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ
    )
    assert observations[1].scheduled == datetime(
        2026, 5, 9, 8, 15, tzinfo=VIENNA_TZ
    )


def test_collect_sbahn_leg_observations_skips_legs_without_name() -> None:
    """A leg with an empty or whitespace-only ``name`` cannot be deduped → dropped."""
    trip = _trip(leg_name="S 1", delay_minutes=4)
    # Surgically blank the leg's ``name`` field.
    trip["LegList"]["Leg"][0]["name"] = "   "
    observations = script._collect_sbahn_leg_observations([trip])
    assert observations == []
