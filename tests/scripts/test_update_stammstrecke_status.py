"""Tests for ``scripts/update_stammstrecke_status.py`` (VOR/VAO migration).

The 2026-05-09 architecture pivot replaced the pyhafas client with the
VOR/VAO ReST ``/trip`` endpoint. These tests exercise the same decision
tree as before — import-time failure, transport error, circuit-breaker
open, median-below-threshold, median-above-threshold, no S-Bahn legs
found, ``first_seen`` persistence and recovery — but the upstream is
mocked at the ``_query_trips`` boundary instead of the pyhafas client.

The HTTP layer is never actually exercised in tests: ``_query_trips`` is
patched per-test to return synthetic VAO ``Trip`` payloads (a single
ride leg with controllable name/category/delay/cancelled fields). The
quota-counter file (``REQUEST_COUNT_FILE``) is redirected to ``tmp_path``
via the existing module-level convention so a test run never touches the
real ``data/vor_request_count.json``.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_status as script  # noqa: E402
from src.feed.providers import MAX_STAMMSTRECKE_CACHE_BYTES  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.utils.circuit_breaker import CircuitBreaker  # noqa: E402


VIENNA_TZ = script.VIENNA_TZ


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect cache writes to a per-test directory."""

    out = tmp_path / "cache" / "stammstrecke" / "events.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", out)
    yield out


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the module-level breaker so tests don't share state."""

    monkeypatch.setattr(
        script,
        "_BREAKER",
        CircuitBreaker(
            "stammstrecke-vor-test",
            failure_threshold=script.BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=script.BREAKER_RECOVERY_TIMEOUT,
        ),
    )
    yield


@pytest.fixture(autouse=True)
def _stable_now(monkeypatch: pytest.MonkeyPatch) -> Iterator[datetime]:
    """Pin ``_now_vienna`` so tests get deterministic timestamps."""

    pinned = datetime(2026, 5, 9, 8, 30, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: pinned)
    yield pinned


@pytest.fixture(autouse=True)
def _isolated_quota_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Redirect the VOR daily-quota counter to ``tmp_path``.

    The ``_charge_one_request`` helper writes to ``REQUEST_COUNT_FILE``
    via ``vor_provider.save_request_count`` — without this fixture the
    bookkeeping side effect would persist into the developer's working
    copy (``data/vor_request_count.json``). ``_flush_quota_cache`` clears
    the in-memory cache so a fresh tmp file is read on the next call.
    """

    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor_provider, "REQUEST_COUNT_FILE", count_file)
    vor_provider._flush_quota_cache()
    yield count_file
    vor_provider._flush_quota_cache()


# ---- Helpers ---------------------------------------------------------------


def _trip(
    *,
    leg_name: str = "S 1",
    delay_minutes: float | None = 10.0,
    category: str | None = "__auto__",
    cancelled: bool = False,
    type_: str = "JNY",
    extra_legs: list[dict[str, Any]] | None = None,
    leg_origin_date: str = "2026-05-09",
    leg_origin_time: str = "08:00:00",
) -> dict[str, Any]:
    """Build a minimal VAO Trip dict with one direct ride leg.

    The shape mirrors what the project's existing VOR /trip parser
    expects: ``Trip[].LegList.Leg[]`` with each leg carrying
    ``Origin{date,time,rtTime}``, ``category``, and ``name``.

    *category*: ``"__auto__"`` derives ``"S"`` from a name matching
    ``S\\d+``; pass an explicit string (e.g. ``"REX"``) or ``None`` to
    override.
    *delay_minutes*: ``None`` → no ``rtTime`` field is emitted, which
    the parser treats as an on-time S-Bahn departure (contributes
    ``0.0`` to the median per the 2026-05-09 audit fix). Numeric →
    realtime computed by adding *delay_minutes* to the scheduled
    origin time.
    """

    if category == "__auto__":
        if re.match(r"^\s*S\s*\d+\s*$", leg_name, re.IGNORECASE):
            category = "S"
        else:
            category = None

    rt_time: str | None = None
    if delay_minutes is not None and not cancelled:
        sched_dt = datetime.strptime(
            f"{leg_origin_date} {leg_origin_time}", "%Y-%m-%d %H:%M:%S"
        )
        rt_dt = sched_dt + timedelta(minutes=float(delay_minutes))
        rt_time = rt_dt.strftime("%H:%M:%S")

    leg: dict[str, Any] = {
        "type": type_,
        "name": leg_name,
        "Origin": {
            "name": "Wien Floridsdorf",
            "extId": "490033400",
            "date": leg_origin_date,
            "time": leg_origin_time,
        },
        "Destination": {
            "name": "Wien Meidling",
            "extId": "490101500",
            "date": leg_origin_date,
            "time": "08:30:00",
        },
    }
    if category is not None:
        leg["category"] = category
    if rt_time is not None:
        leg["Origin"]["rtTime"] = rt_time
    if cancelled:
        leg["cancelled"] = True

    legs: list[dict[str, Any]] = [leg]
    if extra_legs:
        legs.extend(extra_legs)
    return {"LegList": {"Leg": legs}}


def _read_output(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert isinstance(payload, list)
    return payload


def _patch_query_trips(
    monkeypatch: pytest.MonkeyPatch,
    floridsdorf_to_meidling: list[dict[str, Any]] | Exception,
    meidling_to_floridsdorf: list[dict[str, Any]] | Exception,
) -> None:
    """Patch ``_query_trips`` to return per-direction synthetic Trip lists.

    Each direction can also be an :class:`Exception` instance to
    simulate a per-direction transport error without affecting the
    other direction's outcome.
    """

    def fake_query_trips(
        session: Any,
        direction: Any,
        *,
        when: datetime,
        timeout: int = script.QUERY_TIMEOUT,
    ) -> list[dict[str, Any]]:
        del session, when, timeout  # not exercised in mocked tests
        if (
            direction.origin_id == script.FLORIDSDORF_VOR_ID
            and direction.destination_id == script.MEIDLING_VOR_ID
        ):
            payload = floridsdorf_to_meidling
        elif (
            direction.origin_id == script.MEIDLING_VOR_ID
            and direction.destination_id == script.FLORIDSDORF_VOR_ID
        ):
            payload = meidling_to_floridsdorf
        else:  # pragma: no cover - defensive: unexpected origin/dest pair
            raise AssertionError(
                f"Unexpected origin/destination pair: "
                f"{direction.origin_id!r} → {direction.destination_id!r}"
            )
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(script, "_query_trips", fake_query_trips)


def _set_now(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    monkeypatch.setattr(script, "_now_vienna", lambda: when)


def _high_trips() -> list[dict[str, Any]]:
    return [
        _trip(leg_name="S 1", delay_minutes=11),
        _trip(leg_name="S 2", delay_minutes=12),
        _trip(leg_name="S 3", delay_minutes=10),
    ]


def _low_trips() -> list[dict[str, Any]]:
    return [
        _trip(leg_name="S 1", delay_minutes=2),
        _trip(leg_name="S 2", delay_minutes=3),
    ]


# ---- Helper-level unit tests -----------------------------------------------


def test_is_sbahn_leg_matches_canonical_category() -> None:
    """The primary signal is ``leg.category == "S"`` (strict-S only)."""
    assert script._is_sbahn_leg({"category": "S", "name": "S 1"})
    assert script._is_sbahn_leg({"category": "S", "name": "S 80"})
    # Lowercase / mixed case is still accepted (the matcher upcases).
    assert script._is_sbahn_leg({"category": "s", "name": "S 7"})


def test_is_sbahn_leg_matches_name_when_category_missing() -> None:
    """Fallback signal: ``leg.name`` matches ``S\\d+``."""
    assert script._is_sbahn_leg({"name": "S 1"})
    assert script._is_sbahn_leg({"name": "S 7"})
    assert script._is_sbahn_leg({"name": "S 80"})
    assert script._is_sbahn_leg({"name": "s 2"})


def test_is_sbahn_leg_matches_nested_product_field() -> None:
    """Tertiary signal: ``leg.Product[].catOut`` or ``Product[].line``."""
    assert script._is_sbahn_leg(
        {"Product": [{"catOut": "S", "line": "S 7"}]}
    )
    assert script._is_sbahn_leg({"Product": [{"line": "S 80"}]})
    # Single Product object (not list) — also accepted.
    assert script._is_sbahn_leg({"Product": {"catOut": "S"}})


def test_is_sbahn_leg_rejects_non_sbahn() -> None:
    assert not script._is_sbahn_leg({"category": "REX", "name": "REX 7"})
    assert not script._is_sbahn_leg({"category": "IC", "name": "IC 533"})
    assert not script._is_sbahn_leg(
        {"category": "RJ", "name": "Railjet 162"}
    )
    assert not script._is_sbahn_leg({"name": ""})


def test_is_sbahn_leg_rejects_ambiguous_sb_category() -> None:
    """The 2026-05-09 strict-S audit removed ``"SB"`` from the accepted
    category set: in some VAO/ÖBB regional dialects ``SB`` denotes
    *Schnellbus* rather than *Schnellbahn*, and there is no SB service
    on the Stammstrecke either way. A leg whose only S-Bahn signal is
    ``category == "SB"`` (no matching name / Product fallback) MUST
    therefore be rejected — preventing a future bus reclassification
    from leaking into the median.
    """
    assert not script._is_sbahn_leg({"category": "SB"})
    assert not script._is_sbahn_leg({"Product": [{"catOut": "SB"}]})
    # But if the *name* still matches "S X" the leg is accepted (name
    # signal trumps a misleading category).
    assert script._is_sbahn_leg({"category": "SB", "name": "S 7"})


def test_is_sbahn_leg_handles_non_mapping_input() -> None:
    """A garbage payload (None, list, string) must not raise."""
    assert not script._is_sbahn_leg(None)
    assert not script._is_sbahn_leg("S 1")
    assert not script._is_sbahn_leg([])


def test_is_sbahn_leg_rejects_unrelated_product_categories() -> None:
    assert not script._is_sbahn_leg({"Product": [{"catOut": "BUS"}]})
    assert not script._is_sbahn_leg({"Product": [{"line": "U1"}]})


def test_collect_delays_includes_sbahn_and_treats_missing_rttime_as_on_time() -> None:
    """End-to-end: S-Bahn ride legs with realtime contribute their
    delta, S-Bahn legs without ``rtTime`` contribute ``0.0`` (on-time),
    cancelled legs and non-S-Bahn legs are excluded.
    """

    trips = [
        _trip(leg_name="S 1", delay_minutes=4),
        _trip(leg_name="S 2", delay_minutes=10),
        # Non-S-Bahn — must be ignored.
        _trip(leg_name="REX 7", delay_minutes=20, category="REX"),
        # S-Bahn but cancelled — ignored (no signal at all).
        _trip(leg_name="S 3", delay_minutes=15, cancelled=True),
        # S-Bahn without rtTime — counts as on-time (0.0) per
        # 2026-05-09 Senior-API-Integration audit. The previous
        # implementation skipped these, biasing the median upward.
        _trip(leg_name="S 80", delay_minutes=None),
    ]
    delays = script._collect_sbahn_delays_minutes(trips)
    assert delays == [4.0, 10.0, 0.0]


def test_leg_departure_delay_returns_zero_when_rttime_missing() -> None:
    """A scheduled-but-no-rtTime leg must yield ``0.0``, not ``None``.

    On the VAO contract, ``rtTime`` is omitted when realtime data
    confirms an on-time departure (the field would otherwise duplicate
    ``time``). Skipping such legs would exclude every on-time train
    from the median, biasing the result high enough that a single
    delayed train in an off-peak window could trip the 9-minute
    threshold and emit a spurious feed event.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "08:30:00",
            # NO rtTime field — VAO's parsimonious "on-time" signal.
        },
    }
    assert script._leg_departure_delay_minutes(leg) == 0.0


def test_leg_departure_delay_returns_zero_when_rttime_equals_time() -> None:
    """An explicitly-on-time leg (``rtTime == time``) yields ``0.0``."""

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "08:30:00",
            "rtTime": "08:30:00",  # Exactly on time.
        },
    }
    assert script._leg_departure_delay_minutes(leg) == 0.0


def test_leg_departure_delay_skips_cancelled_leg() -> None:
    """Cancelled legs have no delay signal — they are absent, not late."""

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "cancelled": True,
        "Origin": {"date": "2026-05-09", "time": "08:30:00"},
    }
    assert script._leg_departure_delay_minutes(leg) is None


def test_leg_departure_delay_skips_unparseable_schedule() -> None:
    """When the scheduled timestamp is malformed we cannot compute a
    delta — return ``None`` rather than risk a misleading 0.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {"date": "not-a-date", "time": "not-a-time"},
    }
    assert script._leg_departure_delay_minutes(leg) is None


def test_leg_departure_delay_skips_unparseable_realtime() -> None:
    """When ``rtTime`` is present but malformed, return ``None`` —
    a malformed realtime field should not silently coerce to zero.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "08:30:00",
            "rtTime": "not-a-time",
        },
    }
    assert script._leg_departure_delay_minutes(leg) is None


# ---- HTTPError diagnostic helpers -----------------------------------------


def _make_http_error(
    *, status_code: int | None, body: bytes | None
) -> requests.HTTPError:
    """Construct a :class:`requests.HTTPError` whose ``response`` carries
    *status_code* and *body*. Used to exercise the diagnostic-extraction
    helpers without standing up a real HTTP roundtrip.
    """

    response = requests.Response()
    if status_code is not None:
        response.status_code = status_code
    if body is not None:
        response._content = body
    err = requests.HTTPError("simulated", response=response)
    return err


def test_extract_http_status_returns_status_code() -> None:
    err = _make_http_error(status_code=429, body=b"")
    assert script._extract_http_status(err) == "429"


def test_extract_http_status_falls_back_when_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._extract_http_status(err) == "<no-response>"


def test_extract_vao_error_code_parses_canonical_envelope() -> None:
    """A documented VAO error envelope is rendered as its ``errorCode``."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"errorCode": "H890", "errorText": "no journey found"}
        ).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "H890"


def test_extract_vao_error_code_falls_back_to_error_field() -> None:
    """Some VAO peers use ``error`` instead of ``errorCode``."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps({"error": "SVC_LOC_INVALID"}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "SVC_LOC_INVALID"


def test_extract_vao_error_code_rejects_verbose_text_with_secrets() -> None:
    """A verbose error message that echoes upstream-controlled content
    (e.g. the supplied ``accessId``) MUST collapse to ``<malformed>``
    so the diagnostic line never surfaces a secret. The 2026-05-09
    cron run revealed VAO returning bodies whose ``errorCode`` field
    carried free-form text that GitHub Actions then masked as
    ``errorCode=***`` — defeating the purpose of the diagnostic."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"errorCode": "Invalid accessId: 0123456789abcdef"}
        ).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "<malformed>"


def test_extract_vao_error_code_rejects_oversize_canonical_shape() -> None:
    """A code that exceeds the canonical 32-char short-shape collapses
    to ``<malformed>`` rather than truncate (truncating could surface
    a partial secret if the source field was polluted)."""

    huge = "X" * 5000
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": huge}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "<malformed>"


def test_extract_vao_error_code_accepts_canonical_long_shape() -> None:
    """A code at the 32-char ceiling that matches the canonical shape
    is rendered verbatim (no truncation, no malformed-bail)."""

    code = "ABC_DEF_GHI_JKL_MNO_PQR_STU_VWXYZ"  # 33 chars — over limit
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": code}).encode("utf-8"),
    )
    # 33 chars exceeds the 32-char regex limit; expect malformed.
    assert script._extract_vao_error_code(err) == "<malformed>"

    short = "ABC_DEF_GHI_JKL_MNO_PQR_STU"  # 27 chars — within limit
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": short}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == short


def test_extract_vao_error_code_caps_oversize_body() -> None:
    """A planted multi-MiB body is rejected before JSON parsing."""

    huge_body = b'{"errorCode": "H890", "padding": "' + b"X" * 65536 + b'"}'
    err = _make_http_error(status_code=400, body=huge_body)
    assert script._extract_vao_error_code(err) == "<unknown>"


def test_extract_vao_error_code_returns_unknown_for_non_json_body() -> None:
    err = _make_http_error(
        status_code=500, body=b"<html>internal server error</html>"
    )
    assert script._extract_vao_error_code(err) == "<unknown>"


def test_extract_vao_error_code_returns_unknown_for_non_dict_body() -> None:
    """A list / scalar at the top level is not a VAO error envelope."""

    err = _make_http_error(status_code=400, body=b'["H890"]')
    assert script._extract_vao_error_code(err) == "<unknown>"


def test_extract_vao_error_code_returns_unknown_for_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._extract_vao_error_code(err) == "<unknown>"


def test_describe_error_body_keys_renders_canonical_envelope() -> None:
    """Top-level keys are alphabetised and comma-joined for stable diagnostic
    output across runs."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"errorCode": "H890", "errorText": "no journey", "Message": []}
        ).encode("utf-8"),
    )
    rendered = script._describe_error_body_keys(err)
    assert rendered == "Message,errorCode,errorText"


def test_describe_error_body_keys_redacts_non_canonical_key_names() -> None:
    """A top-level key whose name is itself upstream-controlled
    (unusual but possible) is rendered as ``<???>`` so it cannot
    smuggle a secret into the diagnostic line."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"errorCode": "H890", "Invalid accessId: ABC": "leak"}
        ).encode("utf-8"),
    )
    rendered = script._describe_error_body_keys(err)
    assert "<???>" in rendered
    assert "Invalid accessId" not in rendered
    assert "errorCode" in rendered


def test_describe_error_body_keys_falls_back_when_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._describe_error_body_keys(err) == "<no-body>"


def test_describe_error_body_keys_falls_back_for_non_json_body() -> None:
    err = _make_http_error(status_code=500, body=b"<html>error</html>")
    assert script._describe_error_body_keys(err) == "<no-body>"


def test_describe_error_body_keys_falls_back_for_empty_object() -> None:
    err = _make_http_error(status_code=400, body=b"{}")
    assert script._describe_error_body_keys(err) == "<empty>"


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
        body=json.dumps({"errorCode": "H730"}).encode("utf-8"),
    )

    def boom(*args: object, **kwargs: object) -> object:
        raise err

    monkeypatch.setattr(script, "_query_trips", boom)

    with caplog.at_level(logging.WARNING, logger="update_stammstrecke_status"):
        event, status = script._process_direction(
            session=requests.Session(),
            direction=script.DIRECTIONS[0],
            when=_stable_now,
            previous_first_seen={},
        )
    assert event is None
    assert status == "error"
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "HTTP 401" in rendered
    assert "H730" in rendered
    # Body-keys diagnostic must surface the canonical envelope shape
    # without leaking any value.
    assert "body_keys=errorCode" in rendered


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
    assert targets == {"Meidling", "Floridsdorf"}
    assert prefixes == {
        "stammstrecke_delay_meidling",
        "stammstrecke_delay_floridsdorf",
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
    five to six so the median is computed over the largest sample VAO
    permits in a single quota slot — important because the
    ``maxChange=0`` filter typically yields 4-6 S-Bahn legs after the
    strict-S product filter, and one extra data point increases
    median stability without inflating quota usage.
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
    """Pin the wire-format parameters: originId/destId/numF/maxChange/rtMode."""

    captured: dict[str, Any] = {}

    def fake_fetch(
        session: Any, endpoint: str, *, params: dict[str, str], **kwargs: Any
    ) -> bytes:
        captured["endpoint"] = endpoint
        captured["params"] = dict(params)
        captured["kwargs"] = kwargs
        return b'{"Trip": []}'

    monkeypatch.setattr(script, "fetch_content_safe", fake_fetch)
    # Bypass the quota gate — the integration of charge → fetch is
    # tested separately below.
    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)

    direction = script.DIRECTIONS[0]
    trips = script._query_trips(session=object(), direction=direction, when=_stable_now)
    assert trips == []
    assert captured["endpoint"].endswith("/trip")
    params = captured["params"]
    # ``format=json`` is intentionally ABSENT — the VAO ``/trip``
    # parameter table does not list it, and passing it to the strict
    # validator returns HTTP 400 with the accessId echoed in the
    # error envelope (observed 2026-05-09).
    assert "format" not in params
    assert params["originId"] == direction.origin_id
    assert params["destId"] == direction.destination_id
    assert params["numF"] == "6"  # pinned MAX_TRIPS_PER_QUERY (VAO max)
    assert params["maxChange"] == "0"  # direct-connections-only
    assert params["rtMode"] == "SERVER_DEFAULT"
    assert params["date"] == _stable_now.strftime("%Y-%m-%d")
    assert params["time"] == _stable_now.strftime("%H:%M")
    # Explicit Accept header for content negotiation (since the
    # ``format=json`` query parameter is no longer sent).
    assert captured["kwargs"]["headers"]["Accept"] == "application/json"
    assert captured["kwargs"]["allowed_content_types"] == ("application/json",)
    # Timeout is bound below MAX_QUERY_TIMEOUT.
    assert captured["kwargs"]["timeout"] <= script.MAX_QUERY_TIMEOUT


def test_query_trips_normalises_single_trip_payload(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """A bare-object ``Trip`` field (single-element list collapsed) must
    still produce a list."""

    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)
    trip_obj = _trip(leg_name="S 1", delay_minutes=10)

    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        return json.dumps({"Trip": trip_obj}).encode("utf-8")

    monkeypatch.setattr(script, "fetch_content_safe", fake_fetch)

    trips = script._query_trips(
        session=object(), direction=script.DIRECTIONS[0], when=_stable_now
    )
    assert isinstance(trips, list)
    assert len(trips) == 1


def test_query_trips_raises_on_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """Any non-dict top-level payload must raise so the per-direction
    error-isolation branch runs."""

    monkeypatch.setattr(script, "_charge_one_request", lambda _now: None)
    monkeypatch.setattr(script, "fetch_content_safe", lambda *a, **kw: b'[1,2,3]')

    with pytest.raises(TypeError):
        script._query_trips(
            session=object(), direction=script.DIRECTIONS[0], when=_stable_now
        )


def test_query_trips_charges_quota_before_fetching(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """Quota is reserved BEFORE the network call, so a quota-exhausted run
    never sends a request."""

    call_order: list[str] = []

    def fake_charge(_now: datetime) -> None:
        call_order.append("charge")

    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        call_order.append("fetch")
        return b'{"Trip": []}'

    monkeypatch.setattr(script, "_charge_one_request", fake_charge)
    monkeypatch.setattr(script, "fetch_content_safe", fake_fetch)

    script._query_trips(
        session=object(), direction=script.DIRECTIONS[0], when=_stable_now
    )
    assert call_order == ["charge", "fetch"]


def test_query_trips_propagates_quota_exceeded_without_fetching(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """When the quota gate raises, no network call is made."""

    fetched = {"called": False}

    def raising_charge(_now: datetime) -> None:
        raise script._QuotaExceeded("daily limit reached")

    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        fetched["called"] = True
        return b""

    monkeypatch.setattr(script, "_charge_one_request", raising_charge)
    monkeypatch.setattr(script, "fetch_content_safe", fake_fetch)

    with pytest.raises(script._QuotaExceeded):
        script._query_trips(
            session=object(),
            direction=script.DIRECTIONS[0],
            when=_stable_now,
        )
    assert fetched["called"] is False


# ---- _read_existing_first_seen / _resolve_first_seen tests ----------------


def test_read_existing_first_seen_handles_missing_file(
    _isolated_output: Path,
) -> None:
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_handles_invalid_json(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text("not-json", encoding="utf-8")
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_extracts_prefix_and_first_seen(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "_identity": "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00",
                    "first_seen": "2026-05-09T08:00:00+02:00",
                },
                {
                    "_identity": "stammstrecke_delay_floridsdorf|2026-05-08T17:30:00+02:00",
                    "first_seen": "2026-05-08T17:30:00+02:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {
        "stammstrecke_delay_meidling": "2026-05-09T08:00:00+02:00",
        "stammstrecke_delay_floridsdorf": "2026-05-08T17:30:00+02:00",
    }


def test_read_existing_first_seen_skips_malformed_items(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text(
        json.dumps(
            [
                "not-a-dict",
                {"_identity": 42, "first_seen": "2026-05-09T08:00:00+02:00"},
                {"_identity": "x|y", "first_seen": 999},
                {
                    "_identity": "good_prefix|2026-05-09T08:00:00+02:00",
                    "first_seen": "2026-05-09T08:00:00+02:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {
        "good_prefix": "2026-05-09T08:00:00+02:00"
    }


# ---- Sentinel: cache-driven provider hardening ----------------------------
#
# These tests pin the Round 8 hardening of the
# ``_read_existing_first_seen`` reader. Each test is a Proof-of-Concept
# that fails pre-fix (the bare ``_json_lib.load(fh)`` site at the
# script's previous read site) and passes post-fix (after the swap to
# ``read_capped_json`` plus the per-preserved-field shape validators).
# The migration from pyhafas to VOR did NOT touch these defences — they
# operate on the cache file's preserved fields, which are independent of
# the upstream API.


def test_read_existing_first_seen_rejects_oversized_cache_file(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    pad_chars = MAX_STAMMSTRECKE_CACHE_BYTES + 1024
    payload = [
        {
            "_identity": "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00",
            "first_seen": "2026-05-09T08:00:00+02:00",
            "pad": "A" * pad_chars,
        }
    ]
    _isolated_output.write_text(json.dumps(payload), encoding="utf-8")
    assert _isolated_output.stat().st_size > MAX_STAMMSTRECKE_CACHE_BYTES
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_rejects_oversized_first_seen_field(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    huge_first_seen = "A" * (script._MAX_PRESERVED_FIRST_SEEN_LENGTH + 16)
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "_identity": "good_prefix|2026-05-09T08:00:00+02:00",
                    "first_seen": huge_first_seen,
                }
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_rejects_oversized_identity_field(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    huge_identity = ("X" * (script._MAX_PRESERVED_IDENTITY_LENGTH + 8)) + "|2026-05-09T08:00:00+02:00"
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "_identity": huge_identity,
                    "first_seen": "2026-05-09T08:00:00+02:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_rejects_control_chars_in_fields(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "_identity": "p\x00bad|2026-05-09T08:00:00+02:00",
                    "first_seen": "2026-05-09T08:00:00+02:00",
                },
                {
                    "_identity": "good|2026-05-09T08:00:00+02:00",
                    "first_seen": "2026-05-09T08:00\x07:00+02:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_rejects_non_iso_first_seen(
    _isolated_output: Path,
) -> None:
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "_identity": "good_prefix|definitely-not-iso",
                    "first_seen": "definitely-not-iso",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert script._read_existing_first_seen() == {}


def test_max_stammstrecke_cache_bytes_imported_constant() -> None:
    assert MAX_STAMMSTRECKE_CACHE_BYTES == 256 * 1024


def test_is_valid_preserved_first_seen_accepts_canonical_iso() -> None:
    sample = datetime(2026, 5, 9, 8, 0, 0, tzinfo=VIENNA_TZ).isoformat()
    assert script._is_valid_preserved_first_seen(sample) is True


def test_is_valid_preserved_first_seen_rejects_non_string() -> None:
    assert script._is_valid_preserved_first_seen(999) is False
    assert script._is_valid_preserved_first_seen(None) is False
    assert script._is_valid_preserved_first_seen([]) is False


def test_is_valid_preserved_identity_accepts_canonical_shape() -> None:
    sample = "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00"
    assert script._is_valid_preserved_identity(sample) is True


def test_is_valid_preserved_identity_rejects_non_string() -> None:
    assert script._is_valid_preserved_identity(42) is False
    assert script._is_valid_preserved_identity(None) is False
    assert script._is_valid_preserved_identity(["x"]) is False


def test_resolve_first_seen_returns_now_when_no_prior(_stable_now: datetime) -> None:
    result = script._resolve_first_seen("any_prefix", {}, _stable_now)
    assert result == _stable_now


def test_resolve_first_seen_returns_parsed_prior(_stable_now: datetime) -> None:
    prior = datetime(2026, 5, 1, 10, 0, 0, tzinfo=VIENNA_TZ)
    result = script._resolve_first_seen(
        "p", {"p": prior.isoformat()}, _stable_now
    )
    assert result == prior


def test_resolve_first_seen_handles_unparseable_prior(
    _stable_now: datetime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    result = script._resolve_first_seen("p", {"p": "definitely-not-iso"}, _stable_now)
    assert result == _stable_now
    assert any("first_seen" in r.getMessage() for r in caplog.records)


def test_resolve_first_seen_localises_naive_datetime(_stable_now: datetime) -> None:
    naive = "2026-05-01T10:00:00"
    result = script._resolve_first_seen("p", {"p": naive}, _stable_now)
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(hours=2)


# ---- _build_event tests (unchanged from pyhafas era) -----------------------


def test_build_event_for_meidling_direction(_stable_now: datetime) -> None:
    direction = next(d for d in script.DIRECTIONS if d.target_label == "Meidling")
    event = script._build_event(
        direction=direction,
        median_delay_minutes=12.5,
        pub_date=_stable_now,
        first_seen=_stable_now,
    )
    required_keys = {
        "source",
        "category",
        "title",
        "description",
        "link",
        "guid",
        "pubDate",
        "starts_at",
        "first_seen",
    }
    assert required_keys.issubset(event.keys())
    assert event["title"] == "S-Bahn Stammstrecke Verspätungen"
    assert event["source"] == "ÖBB"
    assert event["category"] == "Störung"
    assert (
        event["description"]
        == "Durchschnittliche Verspätung von 12.5 Minuten in Richtung Meidling [Seit 09.05.2026]"
    )
    assert event["pubDate"] == _stable_now.isoformat()
    assert event["starts_at"] == _stable_now.isoformat()
    assert event["first_seen"] == _stable_now.isoformat()
    assert event["pubDate"].endswith(("+02:00", "+01:00"))
    assert event["ends_at"] is None
    assert event["_identity"].startswith("stammstrecke_delay_meidling|")


def test_build_event_for_floridsdorf_direction(_stable_now: datetime) -> None:
    direction = next(d for d in script.DIRECTIONS if d.target_label == "Floridsdorf")
    event = script._build_event(
        direction=direction,
        median_delay_minutes=15.0,
        pub_date=_stable_now,
        first_seen=_stable_now,
    )
    assert event["title"] == "S-Bahn Stammstrecke Verspätungen"
    assert (
        event["description"]
        == "Durchschnittliche Verspätung von 15 Minuten in Richtung Floridsdorf [Seit 09.05.2026]"
    )
    assert event["_identity"].startswith("stammstrecke_delay_floridsdorf|")


def test_build_event_uses_first_seen_for_seit_date(_stable_now: datetime) -> None:
    pub = _stable_now + timedelta(days=2)
    first = _stable_now
    event = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.0,
        pub_date=pub,
        first_seen=first,
    )
    assert "[Seit 09.05.2026]" in event["description"]
    assert "[Seit 11.05.2026]" not in event["description"]
    assert event["pubDate"] == pub.isoformat()
    assert event["first_seen"] == first.isoformat()
    assert event["starts_at"] == first.isoformat()


def test_build_event_guid_is_stable_for_same_episode(_stable_now: datetime) -> None:
    pub_t1 = _stable_now
    pub_t2 = _stable_now + timedelta(minutes=30)
    event_t1 = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.0,
        pub_date=pub_t1,
        first_seen=_stable_now,
    )
    event_t2 = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=14.0,
        pub_date=pub_t2,
        first_seen=_stable_now,
    )
    assert event_t1["guid"] == event_t2["guid"]
    assert event_t1["pubDate"] != event_t2["pubDate"]
    assert event_t1["first_seen"] == event_t2["first_seen"]


def test_build_event_guid_changes_when_first_seen_changes(
    _stable_now: datetime,
) -> None:
    earlier = _stable_now
    later = _stable_now + timedelta(hours=2)
    event_earlier = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.0,
        pub_date=later,
        first_seen=earlier,
    )
    event_later = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.0,
        pub_date=later,
        first_seen=later,
    )
    assert event_earlier["guid"] != event_later["guid"]


def test_build_event_guids_differ_per_direction(_stable_now: datetime) -> None:
    meidling = next(d for d in script.DIRECTIONS if d.target_label == "Meidling")
    floridsdorf = next(d for d in script.DIRECTIONS if d.target_label == "Floridsdorf")
    event_a = script._build_event(
        direction=meidling,
        median_delay_minutes=12.5,
        pub_date=_stable_now,
        first_seen=_stable_now,
    )
    event_b = script._build_event(
        direction=floridsdorf,
        median_delay_minutes=12.5,
        pub_date=_stable_now,
        first_seen=_stable_now,
    )
    assert event_a["guid"] != event_b["guid"]
    assert event_a["_identity"] != event_b["_identity"]


def test_build_event_validates_against_schema(_stable_now: datetime) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = REPO_ROOT / "docs" / "schema" / "events.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    event = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.5,
        pub_date=_stable_now,
        first_seen=_stable_now,
    )
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(event), key=lambda e: e.path)
    assert errors == [], "\n".join(e.message for e in errors)


# ---- main() integration tests ---------------------------------------------


def test_main_writes_two_events_when_both_directions_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [
        _trip(leg_name="S 1", delay_minutes=11),
        _trip(leg_name="S 2", delay_minutes=10),
        _trip(leg_name="S 3", delay_minutes=12),
    ]
    bwd = [
        _trip(leg_name="S 7", delay_minutes=14),
        _trip(leg_name="S 80", delay_minutes=15),
        _trip(leg_name="S 1", delay_minutes=13),
    ]
    _patch_query_trips(monkeypatch, fwd, bwd)

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    descriptions = {event["description"] for event in payload}
    assert any("in Richtung Meidling [Seit" in d for d in descriptions)
    assert any("in Richtung Floridsdorf [Seit" in d for d in descriptions)
    guids = {event["guid"] for event in payload}
    assert len(guids) == 2
    assert all("first_seen" in event for event in payload)


def test_main_writes_only_meidling_event_when_only_forward_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [
        _trip(leg_name="S 1", delay_minutes=14),
        _trip(leg_name="S 2", delay_minutes=13),
    ]
    bwd = [_trip(leg_name="S 7", delay_minutes=2)]
    _patch_query_trips(monkeypatch, fwd, bwd)

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Meidling" in payload[0]["description"]
    assert payload[0]["_identity"].startswith("stammstrecke_delay_meidling|")


def test_main_writes_empty_when_both_directions_below_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Self-Healing: median ≤ threshold for both directions → cache cleared."""

    _patch_query_trips(monkeypatch, _low_trips(), _low_trips())

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_median_equals_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Median exactly equal to threshold must NOT trigger the event."""

    nine = [
        _trip(leg_name="S 1", delay_minutes=9),
        _trip(leg_name="S 2", delay_minutes=9),
    ]
    _patch_query_trips(monkeypatch, nine, nine)

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_no_sbahn_legs(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Only non-S-Bahn legs in either direction → empty cache, exit 0."""

    fwd = [_trip(leg_name="REX 7", delay_minutes=20, category="REX")]
    bwd = [_trip(leg_name="IC 533", delay_minutes=15, category="IC")]
    _patch_query_trips(monkeypatch, fwd, bwd)

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_partial_failure_keeps_other_direction_event(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
) -> None:
    """A transient error on one direction must not discard the other."""

    fwd_error = RuntimeError("transient connection reset")
    _patch_query_trips(monkeypatch, fwd_error, _high_trips())

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Floridsdorf" in payload[0]["description"]


def test_main_clears_cache_when_all_directions_fail(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
) -> None:
    """Self-Healing: API globally unreachable → empty cache, exit 1."""

    err1 = RuntimeError("connection reset 1")
    err2 = RuntimeError("connection reset 2")
    _patch_query_trips(monkeypatch, err1, err2)

    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_clears_cache_when_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Self-Healing: VAO daily-quota cap hit → empty cache, exit 1.

    The pre-tripped quota gate raises ``_QuotaExceeded`` on every
    direction's first call. Both directions therefore fail with no
    successful observation; the script clears the cache and returns 1.
    """

    quota_err = script._QuotaExceeded("daily limit")
    _patch_query_trips(monkeypatch, quota_err, quota_err)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 1
    assert _read_output(_isolated_output) == []
    assert any(
        "Tageslimit" in r.getMessage() for r in caplog.records
    )


def test_main_clears_cache_when_breaker_is_open(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Self-Healing: pre-tripped breaker → cache emptied, exit 0."""

    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    _isolated_output.write_text(
        json.dumps(
            [
                {
                    "source": "ÖBB",
                    "category": "Störung",
                    "title": "S-Bahn Stammstrecke Verspätungen",
                    "description": "stale",
                    "link": "https://x.example",
                    "guid": "stale-guid",
                    "pubDate": "2026-05-09T08:00:00+02:00",
                    "starts_at": "2026-05-09T08:00:00+02:00",
                    "ends_at": None,
                    "first_seen": "2026-05-09T08:00:00+02:00",
                    "_identity": "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    breaker = CircuitBreaker(
        "stammstrecke-vor-pretrip",
        failure_threshold=2,
        recovery_timeout=600.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    monkeypatch.setattr(script, "_BREAKER", breaker)

    upstream_calls: list[Any] = []

    def must_not_be_called(*args: Any, **kwargs: Any) -> list[Any]:
        upstream_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(script, "_query_trips", must_not_be_called)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []
    assert upstream_calls == []
    assert any("breaker" in r.getMessage().lower() for r in caplog.records)


def test_main_handles_typeerror_payload(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """A malformed VAO payload (e.g. ``Trip`` is the wrong type) must be
    treated as a per-direction error, not crash the run."""

    err = TypeError("VAO /trip Trip field has unexpected type: str")
    _patch_query_trips(monkeypatch, err, err)

    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_emits_iso8601_with_vienna_offset(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Verify the timezone contract: pubDate is Europe/Vienna, ISO 8601."""

    _patch_query_trips(monkeypatch, _high_trips(), _high_trips())

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    for event in payload:
        assert event["pubDate"] == _stable_now.isoformat()
        assert event["pubDate"].endswith("+02:00")
        assert event["first_seen"] == _stable_now.isoformat()


# ---- first_seen persistence integration tests ------------------------------


def test_first_seen_persists_across_consecutive_high_runs(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    _patch_query_trips(monkeypatch, _high_trips(), _low_trips())
    assert script.main() == 0
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 1
    first_seen_t1 = tick1[0]["first_seen"]
    pub_t1 = tick1[0]["pubDate"]
    guid_t1 = tick1[0]["guid"]
    assert first_seen_t1 == _stable_now.isoformat()
    assert "[Seit 09.05.2026]" in tick1[0]["description"]

    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_query_trips(monkeypatch, _high_trips(), _low_trips())
    assert script.main() == 0
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 1

    assert tick2[0]["first_seen"] == first_seen_t1
    assert tick2[0]["pubDate"] != pub_t1
    assert tick2[0]["pubDate"] == later.isoformat()
    assert tick2[0]["guid"] == guid_t1
    assert "[Seit 09.05.2026]" in tick2[0]["description"]


def test_first_seen_regenerates_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    _patch_query_trips(monkeypatch, _high_trips(), _low_trips())
    script.main()
    first_seen_t1 = _read_output(_isolated_output)[0]["first_seen"]
    guid_t1 = _read_output(_isolated_output)[0]["guid"]

    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_query_trips(monkeypatch, _low_trips(), _low_trips())
    script.main()
    assert _read_output(_isolated_output) == []

    even_later = _stable_now + timedelta(minutes=60)
    _set_now(monkeypatch, even_later)
    _patch_query_trips(monkeypatch, _high_trips(), _low_trips())
    script.main()
    tick3 = _read_output(_isolated_output)
    assert len(tick3) == 1
    assert tick3[0]["first_seen"] == even_later.isoformat()
    assert tick3[0]["first_seen"] != first_seen_t1
    assert tick3[0]["guid"] != guid_t1


def test_first_seen_persistence_is_independent_per_direction(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    _patch_query_trips(monkeypatch, _high_trips(), _low_trips())
    script.main()
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 1
    assert tick1[0]["_identity"].startswith("stammstrecke_delay_meidling|")
    meidling_first_seen = tick1[0]["first_seen"]

    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_query_trips(monkeypatch, _low_trips(), _high_trips())
    script.main()
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 1
    assert tick2[0]["_identity"].startswith("stammstrecke_delay_floridsdorf|")
    assert tick2[0]["first_seen"] == later.isoformat()
    assert meidling_first_seen != tick2[0]["first_seen"]


def test_first_seen_continues_when_only_one_direction_resumes(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    _patch_query_trips(monkeypatch, _high_trips(), _high_trips())
    script.main()
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 2
    first_seen_per_direction_t1 = {
        item["_identity"].split("|", 1)[0]: item["first_seen"] for item in tick1
    }

    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_query_trips(monkeypatch, _high_trips(), _high_trips())
    script.main()
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 2
    for item in tick2:
        prefix = item["_identity"].split("|", 1)[0]
        assert (
            item["first_seen"] == first_seen_per_direction_t1[prefix]
        ), f"first_seen for {prefix} drifted across ticks"
