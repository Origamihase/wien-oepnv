"""Teil 1 der Tests aus tests/scripts/test_update_stammstrecke_status.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json

import requests

from scripts import update_stammstrecke_status as script  # noqa: E402

from ._support import (
    _make_http_error,
    _trip,
)


# ---- Helper-level unit tests -----------------------------------------------


def test_is_sbahn_leg_matches_canonical_category() -> None:
    """The primary signal is ``leg.category`` in the accepted-category set.

    The set covers Vienna S-Bahn (``S``), Regional (``R``), Regional
    Express (``REX``), and Cityjet Express (``CJX``). ``CJX`` joined
    on 2026-05-17 after ÖBB rebranded selected REX rolling-stock; the
    same Stammstrecke-axis corridors are served.
    """
    assert script._is_sbahn_leg({"category": "S", "name": "S 1"})
    assert script._is_sbahn_leg({"category": "S", "name": "S 80"})
    assert script._is_sbahn_leg({"category": "R", "name": "R 81"})
    assert script._is_sbahn_leg({"category": "REX", "name": "REX 3"})
    assert script._is_sbahn_leg({"category": "CJX", "name": "CJX 9"})
    # Lowercase / mixed case is still accepted (the matcher upcases).
    assert script._is_sbahn_leg({"category": "s", "name": "S 7"})
    assert script._is_sbahn_leg({"category": "cjx", "name": "CJX 5"})


def test_is_sbahn_leg_matches_name_when_category_missing() -> None:
    """Fallback signal: ``leg.name`` matches ``(S|R|REX|CJX)\\d+``."""
    assert script._is_sbahn_leg({"name": "S 1"})
    assert script._is_sbahn_leg({"name": "S 7"})
    assert script._is_sbahn_leg({"name": "S 80"})
    assert script._is_sbahn_leg({"name": "s 2"})
    assert script._is_sbahn_leg({"name": "REX 3"})
    assert script._is_sbahn_leg({"name": "R 81"})
    # Cityjet Express — added 2026-05-17 after the ÖBB product rebrand.
    assert script._is_sbahn_leg({"name": "CJX 9"})
    assert script._is_sbahn_leg({"name": "CJX9"})
    assert script._is_sbahn_leg({"name": "cjx 5"})


def test_is_sbahn_leg_matches_nested_product_field() -> None:
    """Tertiary signal: ``leg.Product[].catOut`` or ``Product[].line``."""
    assert script._is_sbahn_leg(
        {"Product": [{"catOut": "S", "line": "S 7"}]}
    )
    assert script._is_sbahn_leg({"Product": [{"line": "S 80"}]})
    # Single Product object (not list) — also accepted.
    assert script._is_sbahn_leg({"Product": {"catOut": "S"}})
    # CJX via the nested-product shape (both catOut and line paths).
    assert script._is_sbahn_leg({"Product": [{"catOut": "CJX"}]})
    assert script._is_sbahn_leg({"Product": [{"line": "CJX 9"}]})
    assert script._is_sbahn_leg({"Product": {"catOut": "cjx"}})


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


def test_leg_departure_delay_handles_early_across_midnight_rollover() -> None:
    """Symmetric midnight wrap: a leg scheduled just AFTER midnight that
    departs a few minutes EARLY (rtTime on the prior day, no rtDate) must
    yield the true small negative delay (≈ −11 min), not a bogus ~+24 h
    that would skew the per-direction mean.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "00:05:00",
            "rtTime": "23:54:00",  # 11 min early, lands on the previous day.
        },
    }
    assert script._leg_departure_delay_minutes(leg) == -11.0


def test_leg_departure_delay_applies_midnight_heuristic_with_empty_rtdepdate() -> None:
    """bug: an EMPTY-string ``rtDepDate`` ('') is falsy but not ``None``, so
    without the ``or None`` normalisation the ``rt_date_explicit is None`` gate
    skipped the midnight-rollover heuristic — a 23:55 leg departing 00:05 then
    yielded a bogus ≈ −23h50m delay instead of the true ~10 min.
    """

    leg = {
        "type": "JNY",
        "name": "S 1",
        "category": "S",
        "Origin": {
            "date": "2026-05-09",
            "time": "23:55:00",
            "rtTime": "00:05:00",  # 10 min late, lands on the next day.
            "rtDepDate": "",  # empty string → must count as "no explicit date".
        },
    }
    assert script._leg_departure_delay_minutes(leg) == 10.0


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
