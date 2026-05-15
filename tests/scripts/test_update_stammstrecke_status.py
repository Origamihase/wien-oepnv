"""Tests for ``scripts/update_stammstrecke_status.py`` (VOR/VAO migration).

The 2026-05-09 architecture pivot replaced the pyhafas client with the
VOR/VAO ReST ``/trip`` endpoint. These tests exercise the same decision
tree as before — import-time failure, transport error, circuit-breaker
open, mean-below-threshold, mean-above-threshold, no S-Bahn legs
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
from src.providers import vor as vor_provider  # noqa: E402
from src.utils.circuit_breaker import CircuitBreaker  # noqa: E402


VIENNA_TZ = script.VIENNA_TZ


# ---- Fixtures --------------------------------------------------------------


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
    the parser treats as "no realtime signal available" and skips the
    leg entirely (rather than implicitly counting it as on-time).
    Numeric → realtime computed by adding *delay_minutes* to the
    scheduled origin time.
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


def test_is_sbahn_leg_rejects_non_regional() -> None:
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


def test_collect_delays_includes_sbahn_and_skips_legs_without_realtime() -> None:
    """End-to-end: S-Bahn ride legs with realtime contribute their
    delta; S-Bahn legs without ``rtTime`` (status unknown), cancelled
    legs and non-S-Bahn legs are all excluded.
    """

    trips = [
        _trip(leg_name="S 1", delay_minutes=4),
        _trip(leg_name="S 2", delay_minutes=10),
        # Regional train - included.
        _trip(leg_name="REX 7", delay_minutes=20, category="REX"),
        # Long-distance train - must be ignored.
        _trip(leg_name="RJ 1", delay_minutes=25, category="RJ"),
        # S-Bahn but cancelled — captured by the full observation
        # collector (with ``cancelled=True``), but the legacy thin
        # wrapper :func:`_collect_sbahn_delays_minutes` strips
        # cancellations so the delay-only sequence stays consistent
        # with its pre-cancellation-tracking contract.
        _trip(leg_name="S 3", delay_minutes=15, cancelled=True),
        # S-Bahn without rtTime — status unknown, dropped from the
        # sample (was implicitly on-time / 0.0 until 2026-05-11; that
        # contract pulled the 30-day mean to 0.2 min over a ~88%
        # missing-rtTime population, masking real delays).
        _trip(leg_name="S 80", delay_minutes=None),
    ]
    delays = script._collect_sbahn_delays_minutes(trips)
    assert delays == [4.0, 10.0, 20.0]


def test_leg_departure_delay_returns_none_when_rttime_missing() -> None:
    """A scheduled-but-no-rtTime leg yields ``None`` (status unknown).

    VAO omits ``rtTime`` both when realtime confirms on-time AND when
    no realtime signal is available. With no way to tell the cases
    apart at the leg level, the upstream contract changed (2026-05-11)
    from "implicit on-time → 0.0" to "unknown → drop". Without the
    drop, ~88% of stored samples were exact zeros and the 30-day
    mean ran at 0.2 min — visibly disconnected from operator
    experience.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "08:30:00",
            # NO rtTime field — VAO emits this both for "on-time" and
            # for "no realtime data"; we cannot tell which, so we drop.
        },
    }
    assert script._leg_departure_delay_minutes(leg) is None


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


def _make_response(
    *, status_code: int = 200, body: bytes = b""
) -> requests.Response:
    """Construct a :class:`requests.Response` with pre-populated body.

    The ``_content`` attribute is set directly so subsequent
    ``response.content`` lookups return the bytes verbatim — mirrors
    the post-fix shape of :func:`src.utils.http.request_safe` when
    ``raise_for_status=False`` is passed (the helper attaches the
    body to ``response._content`` before returning, regardless of
    status).
    """

    response = requests.Response()
    response.status_code = status_code
    response._content = body
    return response


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
    assert script._extract_http_status(err) == "[NO_RESPONSE]"


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
    assert script._extract_vao_error_code(err) == "[BAD_SHAPE]"


def test_extract_vao_error_code_rejects_oversize_canonical_shape() -> None:
    """A code that exceeds the canonical 32-char short-shape collapses
    to ``<malformed>`` rather than truncate (truncating could surface
    a partial secret if the source field was polluted)."""

    huge = "X" * 5000
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": huge}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "[BAD_SHAPE]"


def test_extract_vao_error_code_accepts_canonical_shapes() -> None:
    """Codes within the canonical regex are returned verbatim."""

    short = "ABC_DEF_GHI_JKL_MNO_PQR_STU"  # 27 chars — within limit
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": short}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == short

    over = "ABC_DEF_GHI_JKL_MNO_PQR_STU_VWXYZ"  # 33 chars — over limit
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": over}).encode("utf-8"),
    )
    assert script._extract_vao_error_code(err) == "[BAD_SHAPE]"


def test_extract_vao_error_code_caps_oversize_body() -> None:
    """A planted multi-MiB body is rejected before JSON parsing."""

    huge_body = b'{"errorCode": "H890", "padding": "' + b"X" * 65536 + b'"}'
    err = _make_http_error(status_code=400, body=huge_body)
    assert script._extract_vao_error_code(err) == "[MISSING]"


def test_extract_vao_error_code_returns_unknown_for_non_json_body() -> None:
    err = _make_http_error(
        status_code=500, body=b"<html>internal server error</html>"
    )
    assert script._extract_vao_error_code(err) == "[MISSING]"


def test_extract_vao_error_code_returns_unknown_for_non_dict_body() -> None:
    """A list / scalar at the top level is not a VAO error envelope."""

    err = _make_http_error(status_code=400, body=b'["H890"]')
    assert script._extract_vao_error_code(err) == "[MISSING]"


def test_extract_vao_error_code_returns_unknown_for_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._extract_vao_error_code(err) == "[MISSING]"


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
    assert "[REDACTED]" in rendered
    assert "Invalid accessId" not in rendered
    assert "errorCode" in rendered


def test_describe_error_body_keys_falls_back_when_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._describe_error_body_keys(err) == "[EMPTY_BODY]"


def test_describe_error_body_keys_falls_back_for_non_json_body() -> None:
    err = _make_http_error(status_code=500, body=b"<html>error</html>")
    assert script._describe_error_body_keys(err) == "[EMPTY_BODY]"


def test_describe_error_body_keys_falls_back_for_empty_object() -> None:
    err = _make_http_error(status_code=400, body=b"{}")
    assert script._describe_error_body_keys(err) == "[NO_KEYS]"


# ---- errorCode length / errorText / requestId diagnostics -----------------


def test_extract_vao_error_code_length_returns_string_length() -> None:
    """Length is a leak-free signal of whether errorCode is canonical
    (4-8 chars) or accessId-shaped (16+ chars)."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": "H890"}).encode("utf-8"),
    )
    assert script._extract_vao_error_code_length(err) == "4"


def test_extract_vao_error_code_length_for_token_shaped_value() -> None:
    """A 32-char token-shape value (suspiciously accessId-sized)."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorCode": "a" * 32}).encode("utf-8"),
    )
    assert script._extract_vao_error_code_length(err) == "32"


def test_extract_vao_error_code_length_falls_back_for_no_body() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert script._extract_vao_error_code_length(err) == "[EMPTY_BODY]"


def test_extract_vao_error_code_length_falls_back_for_missing_field() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"otherField": "x"}).encode("utf-8"),
    )
    assert script._extract_vao_error_code_length(err) == "[MISSING]"


def test_extract_vao_error_text_returns_text_field() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"errorCode": "H890", "errorText": "no journey found"}
        ).encode("utf-8"),
    )
    assert script._extract_vao_error_text(err) == "no journey found"


def test_extract_vao_error_text_falls_back_to_error_msg_field() -> None:
    """Some VAO peers emit ``errorMsg`` instead of ``errorText``."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorMsg": "auth failed"}).encode("utf-8"),
    )
    assert script._extract_vao_error_text(err) == "auth failed"


def test_extract_vao_error_text_truncates_oversize_value() -> None:
    huge = "X" * 5000
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorText": huge}).encode("utf-8"),
    )
    rendered = script._extract_vao_error_text(err)
    assert len(rendered) == script._ERROR_TEXT_MAX_LEN + 1  # +1 for ellipsis
    assert rendered.endswith("…")


def test_extract_vao_error_text_falls_back_for_empty_value() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"errorText": "   "}).encode("utf-8"),
    )
    assert script._extract_vao_error_text(err) == "[MISSING]"


def test_extract_vao_error_text_falls_back_for_no_body() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert script._extract_vao_error_text(err) == "[EMPTY_BODY]"


def test_extract_vao_request_id_returns_uuid_shape() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"requestId": "8e7f2c9b-1234-5678-90ab-cdef12345678"}
        ).encode("utf-8"),
    )
    assert (
        script._extract_vao_request_id(err)
        == "8e7f2c9b-1234-5678-90ab-cdef12345678"
    )


def test_extract_vao_request_id_returns_short_hex_token() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"requestId": "abc123def456"}).encode("utf-8"),
    )
    assert script._extract_vao_request_id(err) == "abc123def456"


def test_extract_vao_request_id_rejects_text_with_spaces() -> None:
    """A free-form ``requestId`` (e.g. echoing user input) collapses to
    the bad-shape sentinel so a planted value cannot leak via this
    diagnostic."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"requestId": "Invalid request from <accessId>"}
        ).encode("utf-8"),
    )
    assert script._extract_vao_request_id(err) == "[BAD_SHAPE]"


def test_extract_vao_request_id_falls_back_for_no_body() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert script._extract_vao_request_id(err) == "[EMPTY_BODY]"


# ---- internalErrorCode / internalErrorText diagnostics ----------------------


def test_extract_vao_internal_error_text_returns_text() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {
                "errorText": "location missing or invalid (LOCATION).",
                "internalErrorText": (
                    "Stop ID 'extId::490033400' could not be resolved"
                ),
            }
        ).encode("utf-8"),
    )
    assert script._extract_vao_internal_error_text(err) == (
        "Stop ID 'extId::490033400' could not be resolved"
    )


def test_extract_vao_internal_error_text_falls_back_to_text_out_field() -> None:
    """Some VAO peers emit ``internalErrorTextOut`` instead of
    ``internalErrorText``."""

    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"internalErrorTextOut": "validator: bad station id"}
        ).encode("utf-8"),
    )
    assert (
        script._extract_vao_internal_error_text(err)
        == "validator: bad station id"
    )


def test_extract_vao_internal_error_text_truncates_oversize() -> None:
    huge = "X" * 5000
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"internalErrorText": huge}).encode("utf-8"),
    )
    rendered = script._extract_vao_internal_error_text(err)
    assert len(rendered) == script._ERROR_TEXT_MAX_LEN + 1
    assert rendered.endswith("…")


def test_extract_vao_internal_error_text_falls_back_for_no_body() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert script._extract_vao_internal_error_text(err) == "[EMPTY_BODY]"


def test_extract_vao_internal_error_text_falls_back_for_missing_field() -> None:
    err = _make_http_error(
        status_code=400, body=json.dumps({"errorText": "X"}).encode("utf-8")
    )
    assert script._extract_vao_internal_error_text(err) == "[MISSING]"


def test_extract_vao_internal_error_code_returns_canonical() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps({"internalErrorCode": "LOC_INV"}).encode("utf-8"),
    )
    assert script._extract_vao_internal_error_code(err) == "LOC_INV"


def test_extract_vao_internal_error_code_rejects_non_canonical() -> None:
    err = _make_http_error(
        status_code=400,
        body=json.dumps(
            {"internalErrorCode": "Stop ID not found"}
        ).encode("utf-8"),
    )
    assert script._extract_vao_internal_error_code(err) == "[BAD_SHAPE]"


def test_extract_vao_internal_error_code_falls_back_for_no_body() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert script._extract_vao_internal_error_code(err) == "[EMPTY_BODY]"


# ---- Response header diagnostics ------------------------------------------


def test_extract_response_header_returns_set_value() -> None:
    err = _make_http_error(status_code=400, body=b"")
    assert err.response is not None
    err.response.headers["Content-Type"] = "application/json; charset=utf-8"
    assert (
        script._extract_response_header(err, "Content-Type")
        == "application/json; charset=utf-8"
    )


def test_extract_response_header_falls_back_when_no_response() -> None:
    err = requests.HTTPError("network failure", response=None)
    assert script._extract_response_header(err, "Server") == "[NO_RESPONSE]"


def test_extract_response_header_falls_back_when_header_absent() -> None:
    err = _make_http_error(status_code=400, body=b"")
    # No headers set — every name should fall back to the missing sentinel.
    assert script._extract_response_header(err, "WWW-Authenticate") == "[MISSING]"


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
    trips = script._query_trips(session=object(), direction=direction, when=_stable_now)
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
            session=object(),
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
    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *a, **kw: _make_response(status_code=200, body=b'[1,2,3]'),
    )

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

    def fake_request(*args: Any, **kwargs: Any) -> requests.Response:
        call_order.append("fetch")
        return _make_response(status_code=200, body=b'{"Trip": []}')

    monkeypatch.setattr(script, "_charge_one_request", fake_charge)
    monkeypatch.setattr(script, "request_safe", fake_request)

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

    def fake_request(*args: Any, **kwargs: Any) -> requests.Response:
        fetched["called"] = True
        return _make_response(status_code=200, body=b"")

    monkeypatch.setattr(script, "_charge_one_request", raising_charge)
    monkeypatch.setattr(script, "request_safe", fake_request)

    with pytest.raises(script._QuotaExceeded):
        script._query_trips(
            session=object(),
            direction=script.DIRECTIONS[0],
            when=_stable_now,
        )
    assert fetched["called"] is False


# ---- Pending-trip state persistence ---------------------------------------


def _make_pending(
    *,
    direction: str = "Meidling",
    name: str = "S 1",
    scheduled: datetime | None = None,
    latest_delay_minutes: float = 0.0,
    last_seen_at: datetime | None = None,
) -> script._PendingTrip:
    """Build a ``_PendingTrip`` with deterministic Vienna timestamps."""
    base = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    return script._PendingTrip(
        direction=direction,
        name=name,
        scheduled=scheduled or base,
        latest_delay_minutes=latest_delay_minutes,
        last_seen_at=last_seen_at or base,
    )


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


# ---- Cancellation capture --------------------------------------------------


def test_collect_sbahn_leg_observations_captures_cancelled_legs() -> None:
    """Cancelled legs are surfaced as observations with ``cancelled=True``.

    Pre-2026-05-15 the collector silently dropped cancelled legs (they
    have no usable delay signal, so the delay-only pipeline could not
    consume them). The cancellation-tracking rework routes them through
    the same pending-trip ledger so the finalise pass can split them
    out into ``data/stats/ausfaelle_<YYYY>.csv``.
    """

    trips = [
        _trip(leg_name="S 1", delay_minutes=4, leg_origin_time="08:00:00"),
        _trip(leg_name="S 2", cancelled=True, leg_origin_time="08:15:00"),
    ]
    observations = script._collect_sbahn_leg_observations(trips)
    assert len(observations) == 2
    by_name = {obs.name: obs for obs in observations}
    assert by_name["S1"].cancelled is False
    assert by_name["S1"].delay_minutes == 4.0
    assert by_name["S2"].cancelled is True
    # Cancelled observations carry a placeholder ``0.0`` so the dataclass
    # parses; the finalise pass MUST NOT fold this value into a delay
    # mean. Pinning the value here protects against a future refactor
    # surfacing a non-zero placeholder into the delay ledger.
    assert by_name["S2"].delay_minutes == 0.0


def test_collect_sbahn_leg_observations_captures_origin_cancelled() -> None:
    """The ``Origin.cancelled`` flag also triggers a cancellation observation.

    VAO splits the cancellation flag across two possible locations:
    the leg itself (``leg.cancelled``) and the leg's origin
    (``leg.Origin.cancelled``). Both shapes are observed in
    production payloads; both must reach the cancellation ledger.
    """

    trip = _trip(leg_name="S 1", delay_minutes=4)
    # Surgically mark only the Origin as cancelled (clear the leg-level
    # flag the helper sets when ``cancelled=True``).
    trip["LegList"]["Leg"][0]["Origin"]["cancelled"] = True
    observations = script._collect_sbahn_leg_observations([trip])
    assert len(observations) == 1
    assert observations[0].cancelled is True


def test_leg_is_cancelled_pins_strict_boolean_contract() -> None:
    """The cancellation flag MUST be a strict Python ``True``.

    Refusing fuzzy spellings (``"true"`` / ``1``) defends against a
    poisoned cache file forging cancellations to flood the ledger.
    """

    assert script._leg_is_cancelled({"cancelled": True}) is True
    assert script._leg_is_cancelled({"Origin": {"cancelled": True}}) is True
    # Fuzzy strings / truthy numbers MUST NOT count as cancellations.
    assert script._leg_is_cancelled({"cancelled": "true"}) is False
    assert script._leg_is_cancelled({"cancelled": 1}) is False
    assert script._leg_is_cancelled({}) is False


def test_pending_trip_json_round_trip_preserves_cancelled() -> None:
    """The pending-trip ledger round-trips the ``cancelled`` flag.

    A cron tick that observes a cancellation, persists the ledger,
    crashes, and resumes on the next tick MUST re-load the
    cancellation flag — otherwise the train would silently demote to
    a "regular delay observation" and end up in the wrong CSV at
    finalisation time.
    """

    trip = script._PendingTrip(
        direction="Meidling",
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=0.0,
        last_seen_at=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        cancelled=True,
    )
    payload = script._trip_to_json(trip)
    assert payload["cancelled"] is True
    parsed = script._trip_from_json(payload)
    assert parsed is not None
    assert parsed.cancelled is True


def test_pending_trip_json_legacy_entry_loads_as_not_cancelled() -> None:
    """Backwards-compat: a ledger entry without the ``cancelled`` key
    loads as a regular (non-cancelled) observation.

    The repo carries cached ``pending_trips.json`` files written before
    the cancellation-tracking schema; on first start after the upgrade
    the loader MUST accept those entries verbatim rather than discard
    them. Discarding would force every in-flight train back to "fresh
    observation" status, breaking the latest-wins delay-reading
    contract.
    """

    legacy_payload = {
        "direction": "Meidling",
        "name": "S1",
        "scheduled": "2026-05-09T08:30:00+02:00",
        "latest_delay_minutes": 3.5,
        "last_seen_at": "2026-05-09T08:00:00+02:00",
        # NO ``cancelled`` key — pre-2026-05-15 ledger shape.
    }
    parsed = script._trip_from_json(legacy_payload)
    assert parsed is not None
    assert parsed.cancelled is False
    assert parsed.latest_delay_minutes == 3.5


def test_observe_legs_inserts_new_trip_with_now_as_last_seen() -> None:
    """A fresh observation must land in state with ``last_seen_at = now``."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    state: dict[str, script._PendingTrip] = {}
    obs = script._SbahnLegObservation(
        name="S 1",
        scheduled=datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ),
        delay_minutes=4.0,
    )
    written = script._observe_legs(
        state, [obs], direction="Meidling", now=now
    )
    assert written == 1
    assert len(state) == 1
    only_entry = next(iter(state.values()))
    assert only_entry.direction == "Meidling"
    assert only_entry.name == "S 1"
    assert only_entry.latest_delay_minutes == 4.0
    assert only_entry.last_seen_at == now


def test_observe_legs_overwrites_existing_with_latest_delay() -> None:
    """The same train re-observed must yield the *latest* delay value.

    Mirrors the user's scenario verbatim: at T=40min the train shows
    5 min late; at T=10min the train shows 15 min late. We expect
    the 15-min reading to survive in state.
    """
    state: dict[str, script._PendingTrip] = {}
    scheduled = datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ)

    # First observation 40 min before departure: 5 min delay.
    earlier = datetime(2026, 5, 9, 8, 5, tzinfo=VIENNA_TZ)
    script._observe_legs(
        state,
        [
            script._SbahnLegObservation(
                name="S 1", scheduled=scheduled, delay_minutes=5.0
            )
        ],
        direction="Meidling",
        now=earlier,
    )

    # Second observation 10 min before departure: 15 min delay.
    later = datetime(2026, 5, 9, 8, 35, tzinfo=VIENNA_TZ)
    script._observe_legs(
        state,
        [
            script._SbahnLegObservation(
                name="S 1", scheduled=scheduled, delay_minutes=15.0
            )
        ],
        direction="Meidling",
        now=later,
    )

    assert len(state) == 1
    only_entry = next(iter(state.values()))
    assert only_entry.latest_delay_minutes == 15.0
    assert only_entry.last_seen_at == later


def test_finalize_departed_returns_only_past_scheduled_trips() -> None:
    """Trips with ``scheduled > now`` stay in state; ``scheduled <= now`` finalise."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    past = _make_pending(
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    future = _make_pending(
        name="S80",
        scheduled=datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=2.0,
    )
    state = {"past-key": past, "future-key": future}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    # Finaliser returns full ``_PendingTrip`` records so the caller
    # can scope the CSV row timestamp to the actual scheduled
    # departure (M2 — cross-year boundary).
    assert [trip.latest_delay_minutes for trip in finalised] == [5.0]
    # Only the past entry was popped.
    assert "past-key" not in state
    assert "future-key" in state


def test_finalize_departed_filters_by_direction() -> None:
    """A departed train for the *other* direction is left untouched."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    meidling_trip = _make_pending(
        direction="Meidling",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    floridsdorf_trip = _make_pending(
        direction="Floridsdorf",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=10.0,
    )
    state = {"m": meidling_trip, "f": floridsdorf_trip}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    assert [trip.latest_delay_minutes for trip in finalised] == [5.0]
    assert "m" not in state
    assert "f" in state


def test_finalize_departed_returns_delays_in_scheduled_order() -> None:
    """Order of returned trips must follow scheduled time ascending.

    A deterministic order keeps the resulting CSV row's mean
    reproducible across runs and platforms.
    """
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    later = _make_pending(
        name="S80",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=10.0,
    )
    earlier = _make_pending(
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=4.0,
    )
    # Insert in reverse-scheduled order so dict iteration order would
    # produce the wrong sequence without explicit sort.
    state = {"later-key": later, "earlier-key": earlier}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    assert [trip.latest_delay_minutes for trip in finalised] == [4.0, 10.0]


def test_finalize_departed_empty_state_returns_empty_list() -> None:
    """Empty state must yield an empty finalisation list, never raise."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    state: dict[str, script._PendingTrip] = {}
    assert script._finalize_departed(state, direction="Meidling", now=now) == []


# ---- End-to-end: observe across two ticks, finalise the latest reading ----


def test_main_end_to_end_finalises_with_latest_delay_across_two_ticks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The user's exact scenario, end-to-end.

    Cron tick 1 (08:00): the lookahead reports train ``S 1`` scheduled
    at 08:45 with a 5-min delay. The train has not departed yet, so
    no CSV row is written; the ledger keeps the 5-min reading.

    Cron tick 2 (08:35): the lookahead now reports the same train
    with a 15-min delay (the delay grew as the actual departure
    approached). The train is still in the future (scheduled 08:45 >
    now 08:35), so the ledger overwrites the entry to 15 min. Still
    no CSV row.

    Cron tick 3 (08:50): the train has departed. The ledger entry
    finalises; a CSV row is written with the *latest* delay of 15
    min — NOT the stale 5-min reading from tick 1.
    """
    # Redirect every persistence target into tmp_path so the test
    # never touches the developer's working copy.
    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    # Capture every ``append_stammstrecke_row`` call instead of writing
    # an actual CSV file — the test asserts on the captured records.
    csv_calls: list[dict[str, Any]] = []

    def fake_append(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        csv_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_append)

    # The /trip endpoint stays mocked; we paint the leg's realtime
    # delta differently across the three ticks.
    delay_by_tick: dict[datetime, float] = {}

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        # Only the Meidling direction's lookahead carries the test
        # train; the opposite direction returns no trips so finalisation
        # is exercised as direction-scoped.
        if direction.target_label != "Meidling":
            return []
        # The train has departed; the API no longer returns it.
        if when >= datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=delay_by_tick.get(when, 0.0),
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # --- Tick 1: 08:00, observed delay = 5 min ----------------------------
    tick1 = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick1)
    delay_by_tick[tick1] = 5.0
    assert script.main() == 0
    assert csv_calls == [], "Tick 1 must not write a CSV row (train still in future)"
    state_after_tick1 = script._load_pending_trips(state_path)
    assert len(state_after_tick1) == 1
    only = next(iter(state_after_tick1.values()))
    assert only.latest_delay_minutes == 5.0

    # --- Tick 2: 08:35, observed delay = 15 min ---------------------------
    tick2 = datetime(2026, 5, 9, 8, 35, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick2)
    delay_by_tick[tick2] = 15.0
    assert script.main() == 0
    assert csv_calls == [], "Tick 2 must not write a CSV row (train still in future)"
    state_after_tick2 = script._load_pending_trips(state_path)
    assert len(state_after_tick2) == 1
    only = next(iter(state_after_tick2.values()))
    assert only.latest_delay_minutes == 15.0, (
        "Latest observation must overwrite the earlier 5-min reading"
    )

    # --- Tick 3: 08:50, train has departed; finalise with 15 -------------
    tick3 = datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick3)
    assert script.main() == 0
    assert len(csv_calls) == 1
    persisted = csv_calls[0]
    assert persisted["direction"] == "Meidling"
    assert persisted["delay_minutes"] == 15.0, (
        "Finalisation must use the latest (15 min) reading, not the "
        "stale 5-min one"
    )
    # M2: the CSV row's timestamp anchors to the train's scheduled
    # departure (08:45), not the cron tick wall clock (08:50) — so
    # a cron tick straddling a year boundary still writes the row
    # into the correct calendar year of the actual departure.
    assert persisted["timestamp"] == datetime(
        2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ
    )
    # Ledger emptied after finalisation.
    state_after_tick3 = script._load_pending_trips(state_path)
    assert state_after_tick3 == {}
    # M4: the recently-finalised ledger holds exactly the train we
    # just committed so a stray VAO re-emission cannot resurrect it.
    finalised_after_tick3 = script._load_recently_finalised(finalised_path)
    assert len(finalised_after_tick3) == 1


def test_main_routes_cancelled_trains_to_ausfaelle_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cancelled S-Bahn train is finalised into the cancellation CSV.

    End-to-end: a cron tick observes a cancelled train scheduled in
    the future. The pending-trip ledger carries the ``cancelled=True``
    flag across ticks. A later tick (post-departure-time) finalises
    the train; the finalise pass routes it to ``append_ausfall_row``
    instead of ``append_stammstrecke_row`` so the delay CSV is NOT
    polluted with a placeholder zero-delay row and the cancellation
    CSV captures the discrete event.
    """

    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    stammstrecke_calls: list[dict[str, Any]] = []
    ausfaelle_calls: list[dict[str, Any]] = []

    def fake_stammstrecke(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        stammstrecke_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    def fake_ausfall(
        *,
        timestamp: datetime,
        direction: str,
        line: str,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        ausfaelle_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "line": line,
            }
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_stammstrecke)
    monkeypatch.setattr(script, "append_ausfall_row", fake_ausfall)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # Train is observed up to its scheduled departure; after that
        # VAO no longer returns it.
        if when >= datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                cancelled=True,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # --- Tick 1: observe the cancellation; not yet finalised ---------------
    tick1 = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick1)
    assert script.main() == 0
    assert stammstrecke_calls == []
    assert ausfaelle_calls == []
    state_after_tick1 = script._load_pending_trips(state_path)
    assert len(state_after_tick1) == 1
    only = next(iter(state_after_tick1.values()))
    assert only.cancelled is True

    # --- Tick 2: post-departure-time; finalise to ausfaelle CSV ------------
    tick2 = datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick2)
    assert script.main() == 0
    # The cancelled train MUST land in the ausfaelle ledger, NOT the
    # delay ledger (which would otherwise be polluted with a placeholder
    # ``delay_minutes=0.0`` row).
    assert stammstrecke_calls == []
    assert len(ausfaelle_calls) == 1
    persisted = ausfaelle_calls[0]
    assert persisted["direction"] == "Meidling"
    assert persisted["line"] == "S1"
    assert persisted["timestamp"] == datetime(
        2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ
    )


def test_main_mixed_finalisation_keeps_delay_and_cancellation_distinct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the same tick finalises both a delayed train and a
    cancelled train, each lands in its own CSV ledger.

    Anti-regression: a naive "always write one stammstrecke row per
    direction per tick (aggregating ALL trains)" would have folded
    the cancelled train's placeholder ``0.0`` into the delay mean —
    biasing it downward. The split MUST happen before the mean
    computation.
    """

    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    stammstrecke_calls: list[dict[str, Any]] = []
    ausfaelle_calls: list[dict[str, Any]] = []

    def fake_stammstrecke(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        stammstrecke_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    def fake_ausfall(
        *,
        timestamp: datetime,
        direction: str,
        line: str,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        ausfaelle_calls.append(
            {"timestamp": timestamp, "direction": direction, "line": line}
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_stammstrecke)
    monkeypatch.setattr(script, "append_ausfall_row", fake_ausfall)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # Past-departure tick → empty response (already-departed
        # trains no longer surface in the lookahead).
        if when >= datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=6,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:30:00",
            ),
            _trip(
                leg_name="S 2",
                cancelled=True,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            ),
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # Tick 1 → both trains land in the pending ledger.
    monkeypatch.setattr(
        script, "_now_vienna", lambda: datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    )
    assert script.main() == 0

    # Tick 2 → both scheduled times are in the past; finalise.
    monkeypatch.setattr(
        script,
        "_now_vienna",
        lambda: datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ),
    )
    assert script.main() == 0
    # Delay CSV holds exactly one row for the delayed S1 — the
    # cancelled S2's placeholder ``0.0`` MUST NOT contaminate the mean.
    assert len(stammstrecke_calls) == 1
    assert stammstrecke_calls[0]["delay_minutes"] == 6.0
    # Cancellation CSV holds exactly one row for the cancelled S2.
    assert len(ausfaelle_calls) == 1
    assert ausfaelle_calls[0]["line"] == "S2"


# ---- Hardening: H1 / M1 / M2 / M3 / M4 / L3 -------------------------------


def test_canonical_line_name_collapses_whitespace_and_strips_pipe() -> None:
    """H1 + M1 normalisation: VAO format drift + separator injection."""
    assert script._canonical_line_name("S 2") == "S2"
    assert script._canonical_line_name("S2") == "S2"
    assert script._canonical_line_name("  s 80  ") == "S80"
    # M1: a pipe inside ``name`` cannot escape into the identity key.
    assert script._canonical_line_name("S 1|fake") == "S1FAKE"
    assert script._canonical_line_name("") == ""
    assert script._canonical_line_name("   ") == ""


def test_identity_key_is_invariant_under_name_format_drift() -> None:
    """H1: ``"S 2"`` and ``"S2"`` MUST produce the same identity key.

    Without this, a VAO format flip between cron ticks would split
    the same physical train into two ledger entries — the exact
    double-counting the dedup pipeline is supposed to prevent.
    """
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    assert script._identity_key("Meidling", "S 2", sched) == script._identity_key(
        "Meidling", "S2", sched
    )
    assert script._identity_key("Meidling", " s 2 ", sched) == script._identity_key(
        "Meidling", "S2", sched
    )


def test_identity_key_strips_pipe_to_prevent_separator_collision() -> None:
    """M1: a literal pipe in ``name`` cannot collide with a real key."""
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    poisoned = script._identity_key("Meidling", "S 1|Floridsdorf", sched)
    legitimate = script._identity_key(
        "Floridsdorf",
        "S 1",
        sched,
    )
    assert poisoned != legitimate


def test_collect_sbahn_leg_observations_canonicalises_name_at_extraction() -> None:
    """H1: identical physical trains (spaced + no-space) produce ONE state entry."""
    state: dict[str, script._PendingTrip] = {}

    # Tick 1: VAO emits the spaced format.
    trip_spaced = _trip(
        leg_name="S 2", delay_minutes=4, leg_origin_time="08:00:00"
    )
    obs_spaced = script._collect_sbahn_leg_observations([trip_spaced])
    script._observe_legs(
        state,
        obs_spaced,
        direction="Meidling",
        now=datetime(2026, 5, 9, 7, 30, tzinfo=VIENNA_TZ),
    )

    # Tick 2: VAO emits the no-space format for the SAME physical train
    # (same scheduled departure).
    trip_compact = _trip(
        leg_name="S2", delay_minutes=10, leg_origin_time="08:00:00"
    )
    obs_compact = script._collect_sbahn_leg_observations([trip_compact])
    script._observe_legs(
        state,
        obs_compact,
        direction="Meidling",
        now=datetime(2026, 5, 9, 7, 55, tzinfo=VIENNA_TZ),
    )

    # Both observations land under the same identity key — exactly one
    # entry survives, and the latest reading wins.
    assert len(state) == 1
    only = next(iter(state.values()))
    assert only.latest_delay_minutes == 10.0


def test_load_pending_trips_normalises_legacy_spaced_names(
    tmp_path: Path,
) -> None:
    """A ledger written before the H1 fix used ``"S 2"`` (with space).

    On load, the parser canonicalises the name to ``"S2"`` AND rebuilds
    the dict key via ``_identity_key`` so a subsequent observation of the
    same train (using the no-space form VAO now emits) hits the existing
    entry instead of creating a duplicate.
    """
    sched_iso = "2026-05-09T08:00:00+02:00"
    payload = {
        "Meidling|S 2|2026-05-09T08:00:00+02:00": {
            "direction": "Meidling",
            "name": "S 2",
            "scheduled": sched_iso,
            "latest_delay_minutes": 3.0,
            "last_seen_at": "2026-05-09T07:30:00+02:00",
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = script._load_pending_trips(path)
    assert len(state) == 1
    only = next(iter(state.values()))
    assert only.name == "S2"
    # The key must be the canonical form, not the old spaced form.
    sched = datetime.fromisoformat(sched_iso)
    assert script._identity_key("Meidling", "S2", sched) in state
    assert "Meidling|S 2|2026-05-09T08:00:00+02:00" not in state


def test_finalize_departed_splits_by_scheduled_year_at_year_boundary() -> None:
    """M2: trips scheduled in different years group into separate finalisations.

    A cron tick at 00:05 on Jan 1 should produce one CSV row for each
    scheduled year if it happens to finalise trains on both sides of
    the boundary. The grouping itself is in ``main()`` — here we
    verify that the finaliser returns full records the caller can
    group on.
    """
    now = datetime(2027, 1, 1, 0, 5, tzinfo=VIENNA_TZ)
    last_2026 = _make_pending(
        direction="Meidling",
        scheduled=datetime(2026, 12, 31, 23, 55, tzinfo=VIENNA_TZ),
        latest_delay_minutes=2.0,
    )
    first_2027 = _make_pending(
        direction="Meidling",
        scheduled=datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    state = {"y2026": last_2026, "y2027": first_2027}
    finalised = script._finalize_departed(state, direction="Meidling", now=now)
    years = {trip.scheduled.year for trip in finalised}
    assert years == {2026, 2027}


def test_finalize_departed_registers_recently_finalised_keys() -> None:
    """M4: keys popped here land in *recently_finalised* with timestamp *now*."""
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    departed = _make_pending(
        direction="Meidling",
        name="S2",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=4.0,
    )
    key = script._identity_key(
        "Meidling",
        "S2",
        datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
    )
    state = {key: departed}
    recently_finalised: dict[str, datetime] = {}
    script._finalize_departed(
        state,
        direction="Meidling",
        now=now,
        recently_finalised=recently_finalised,
    )
    assert recently_finalised == {key: now}


def test_observe_legs_skips_keys_in_recently_finalised() -> None:
    """M4: a recently-finalised key cannot be re-inserted into pending state.

    Anomalous VAO re-emission (a finalised train re-appearing in the
    lookahead) is silently dropped instead of producing a second CSV
    row downstream.
    """
    sched = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    key = script._identity_key("Meidling", "S 2", sched)
    recently_finalised = {
        key: datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ),
    }
    state: dict[str, script._PendingTrip] = {}
    obs = script._SbahnLegObservation(
        name="S2",
        scheduled=sched,
        delay_minutes=7.0,
    )
    written = script._observe_legs(
        state,
        [obs],
        direction="Meidling",
        now=datetime(2026, 5, 9, 9, 5, tzinfo=VIENNA_TZ),
        recently_finalised=recently_finalised,
    )
    assert written == 0
    assert state == {}


def test_purge_finalised_entries_drops_only_old_keys() -> None:
    """M4 TTL: the recently-finalised ledger evicts entries older than cutoff."""
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    cutoff = now - timedelta(hours=6)
    finalised = {
        "fresh": now - timedelta(hours=2),
        "stale": now - timedelta(hours=12),
    }
    removed = script._purge_finalised_entries(finalised, cutoff=cutoff)
    assert removed == 1
    assert "fresh" in finalised
    assert "stale" not in finalised


def test_load_recently_finalised_returns_empty_when_missing(tmp_path: Path) -> None:
    """A missing file is fresh-start, not an error."""
    assert script._load_recently_finalised(tmp_path / "absent.json") == {}


def test_save_recently_finalised_round_trips_through_load(tmp_path: Path) -> None:
    """JSON persistence is lossless across save → load."""
    path = tmp_path / "finalised.json"
    state_in = {
        "Meidling|S2|2026-05-09T08:30:00+02:00": datetime(
            2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ
        )
    }
    assert script._save_recently_finalised(path, state_in) is True
    state_out = script._load_recently_finalised(path)
    assert set(state_out) == set(state_in)
    only_key = next(iter(state_in))
    assert state_out[only_key] == state_in[only_key]


def test_load_recently_finalised_recovers_from_corrupt_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt JSON in the companion ledger logs WARNING and falls back to empty."""
    path = tmp_path / "corrupt.json"
    path.write_text("{not even json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="update_stammstrecke_status"):
        result = script._load_recently_finalised(path)
    assert result == {}
    assert any("korrupt" in r.getMessage() for r in caplog.records)


def test_ledger_lock_is_a_context_manager_that_returns_to_caller(
    tmp_path: Path,
) -> None:
    """M3: the lock context manager yields exactly once and releases on exit.

    The actual mutual-exclusion is delegated to ``fcntl.flock`` on
    POSIX — covered by an integration smoke test, not unit assertion
    (file descriptors are notoriously hard to assert on across
    Python versions).
    """
    lock_path = tmp_path / "lock"
    entered = False
    with script._ledger_lock(lock_path):
        entered = True
        assert lock_path.exists(), "lock file is created on first acquire"
    assert entered is True


def test_collect_sbahn_leg_observations_preserves_negative_delays() -> None:
    """L3: VAO can report ``rtTime < time`` (early departure).

    Negative delays are legitimate signal — they bring the running
    mean down, which is what we want.  Locked here so a future
    sanitiser-rewrite doesn't accidentally clamp them at zero.
    """
    trip = {
        "LegList": {
            "Leg": [
                {
                    "type": "JNY",
                    "name": "S 1",
                    "category": "S",
                    "Origin": {
                        "name": "Wien Floridsdorf",
                        "date": "2026-05-09",
                        "time": "08:00:00",
                        # rtTime two minutes BEFORE time — early departure.
                        "rtTime": "07:58:00",
                    },
                    "Destination": {
                        "name": "Wien Meidling",
                        "date": "2026-05-09",
                        "time": "08:30:00",
                    },
                }
            ]
        }
    }
    observations = script._collect_sbahn_leg_observations([trip])
    assert len(observations) == 1
    assert observations[0].delay_minutes == -2.0


def test_main_suppresses_re_observation_of_finalised_train(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M4 end-to-end: a VAO re-emission of a finalised train doesn't
    produce a second CSV row.

    Anomalous-VAO scenario: the same physical S-Bahn appears in the
    lookahead of TICK A (gets observed and finalised because already
    departed) AND again in the lookahead of TICK B (because the VAO
    upstream forgot to update its train list). Without the
    recently-finalised gate, TICK B would re-observe and re-finalise
    the train → second CSV row. With the gate, the train is
    silently dropped on TICK B.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    csv_calls: list[dict[str, Any]] = []

    def fake_append(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        csv_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_append)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # The same train appears in every tick's lookahead — VAO bug
        # / lookahead-boundary anomaly. Origin time stays fixed.
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=10.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # Tick A: train has already departed at observation time; gets
    # observed AND finalised in the same tick.
    tick_a = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_a)
    assert script.main() == 0
    assert len(csv_calls) == 1

    # Tick B: anomalous re-emission. Without the suppression gate
    # this would produce a SECOND CSV row for the same physical
    # train.
    tick_b = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_b)
    assert script.main() == 0
    assert len(csv_calls) == 1, (
        "Recently-finalised gate must suppress the duplicate — "
        "without it, the train would be counted twice."
    )


# ---- Post-merge audit follow-up tests (HIGH/MEDIUM) ----------------------


def test_load_pending_trips_rebuilds_canonical_key_from_old_disk_key(
    tmp_path: Path,
) -> None:
    """HIGH 1: legacy disk key ``"Meidling|S 2|…"`` must be replaced by the
    canonical key ``"Meidling|S2|…"`` so that a subsequent observation of
    the same train (using the no-space form) hits the existing entry
    instead of inserting a duplicate.

    Before the fix, ``_load_pending_trips`` kept the raw disk key via
    ``state[str(key)] = trip``.  After the fix it calls ``_identity_key``
    so the stored key always matches the key any observer would compute.
    """
    sched_iso = "2026-05-09T08:00:00+02:00"
    payload = {
        "Meidling|S 2|2026-05-09T08:00:00+02:00": {
            "direction": "Meidling",
            "name": "S 2",
            "scheduled": sched_iso,
            "latest_delay_minutes": 3.0,
            "last_seen_at": "2026-05-09T07:30:00+02:00",
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = script._load_pending_trips(path)
    assert len(state) == 1
    sched = datetime.fromisoformat(sched_iso)
    expected_key = script._identity_key("Meidling", "S2", sched)
    assert expected_key in state, (
        f"Expected canonical key {expected_key!r} in state; got {list(state.keys())}"
    )
    # The old spaced key must NOT survive in the dict.
    assert "Meidling|S 2|2026-05-09T08:00:00+02:00" not in state


def test_canonical_line_name_handles_none_and_falsy_values() -> None:
    """MEDIUM 3: ``str(value or "")`` silently collapses falsy non-None
    values such as ``0`` (``str(0 or "")`` → ``""``).  The corrected
    form ``str(value) if value is not None else ""`` must return the
    string representation for any non-None input.
    """
    # None → empty string (no change from old behaviour).
    assert script._canonical_line_name(None) == ""
    # 0 is falsy but not None; old code would return ""; new must return "0".
    assert script._canonical_line_name(0) == "0"
    # False is falsy but not None.
    assert script._canonical_line_name(False) == "FALSE"
    # Empty string → empty string (unchanged).
    assert script._canonical_line_name("") == ""


def test_save_order_recently_finalised_before_pending_trips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MEDIUM 1: ``_save_recently_finalised`` must be called BEFORE
    ``_save_pending_trips`` in main().

    If the process crashes between the two writes the safer failure mode
    is:  recently_finalised durable + pending not yet removed  (train is
    re-observed but silently suppressed by the gate on the next tick)
    rather than:  pending removed + recently_finalised not yet written
    (train is gone from pending but the gate doesn't know → it can be
    double-finalised if VAO re-emits it).

    This test verifies the call order by recording which save function
    fires first.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    call_order: list[str] = []

    real_save_finalised = script._save_recently_finalised
    real_save_pending = script._save_pending_trips

    def spy_save_recently_finalised(path: Any, data: Any) -> bool:
        call_order.append("recently_finalised")
        return real_save_finalised(path, data)

    def spy_save_pending_trips(path: Any, state: Any) -> bool:
        call_order.append("pending_trips")
        return real_save_pending(path, state)

    monkeypatch.setattr(script, "_save_recently_finalised", spy_save_recently_finalised)
    monkeypatch.setattr(script, "_save_pending_trips", spy_save_pending_trips)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout, when
        return []

    monkeypatch.setattr(script, "_query_trips", fake_query)

    assert script.main() == 0
    assert call_order == ["recently_finalised", "pending_trips"], (
        f"Expected recently_finalised saved before pending_trips; got {call_order}"
    )


def test_format_drift_finalized_train_suppressed_on_respaced_reemission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """HIGH 1 + M4 integration: a train finalised as ``"S1"`` must NOT
    be double-finalised when the VAO re-emits it with the old
    ``"S 1"`` spacing.

    The recently-finalised gate stores canonical keys produced by
    ``_identity_key``.  ``_identity_key`` calls ``_canonical_line_name``
    which normalises ``"S 1"`` → ``"S1"``.  So the re-emitted ``"S 1"``
    form maps to the same key as the already-recorded ``"S1"`` entry and
    is silently suppressed.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    csv_calls: list[dict[str, Any]] = []

    def fake_append(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        csv_calls.append({"direction": direction, "delay_minutes": delay_minutes})
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_append)

    # Tick A: VAO emits the train with canonical name "S1" (no space).
    # Train already past scheduled time → immediately finalised.
    def query_tick_a(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        return [
            _trip(
                leg_name="S1",
                delay_minutes=5.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    tick_a = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_a)
    monkeypatch.setattr(script, "_query_trips", query_tick_a)
    assert script.main() == 0
    assert len(csv_calls) == 1

    # Tick B: VAO re-emits the SAME train with the OLD spaced name "S 1".
    # The canonical gate must recognise it as already finalised.
    def query_tick_b(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=5.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    tick_b = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_b)
    monkeypatch.setattr(script, "_query_trips", query_tick_b)
    assert script.main() == 0
    assert len(csv_calls) == 1, (
        "Re-emission with spaced name 'S 1' must be suppressed because "
        "'S1' was already finalised in tick A."
    )

