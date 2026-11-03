"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/scripts/test_update_stammstrecke_status.py.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

from scripts import update_stammstrecke_status as script  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.providers import vor_quota  # noqa: E402
from src.utils.circuit_breaker import CircuitBreaker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
    monkeypatch.setattr(vor_quota, "REQUEST_COUNT_FILE", count_file)
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


__all__ = [
    "REPO_ROOT",
    "VIENNA_TZ",
    "_fresh_breaker",
    "_high_trips",
    "_isolated_quota_counter",
    "_low_trips",
    "_make_http_error",
    "_make_pending",
    "_make_response",
    "_patch_query_trips",
    "_read_output",
    "_set_now",
    "_stable_now",
    "_trip",
]
