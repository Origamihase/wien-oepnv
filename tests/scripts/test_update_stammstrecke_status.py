"""Tests for ``scripts/update_stammstrecke_status.py``.

The pyhafas client is mocked end-to-end so the test suite never reaches
the live ÖBB HAFAS endpoint. Each test exercises a single, isolated
branch of the script's decision tree: import-time failure, transport
error, circuit-breaker open, median-below-threshold, median-above-
threshold, no S-Bahn legs found, ``first_seen`` persistence and
recovery — for each of the **two** Stammstrecke directions
independently.
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_status as script  # noqa: E402
from src.feed.providers import MAX_STAMMSTRECKE_CACHE_BYTES  # noqa: E402
from src.utils.circuit_breaker import CircuitBreaker  # noqa: E402


VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect cache writes to a per-test directory."""

    out = tmp_path / "cache" / "stammstrecke" / "events.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", out)
    yield out


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the module-level breaker so tests don't share state.

    Uses the production breaker config (10 / 3600 s) so the threshold-
    based behavioural tests reflect what runs in CI.
    """

    monkeypatch.setattr(
        script,
        "_BREAKER",
        CircuitBreaker(
            "stammstrecke-hafas-test",
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


# ---- Helpers ---------------------------------------------------------------


def _leg(*, name: str, delay_minutes: float | None, cancelled: bool = False) -> Any:
    """Return a duck-typed leg mock matching pyhafas FPTF Leg."""

    delay = timedelta(minutes=delay_minutes) if delay_minutes is not None else None
    return SimpleNamespace(name=name, departure_delay=delay, cancelled=cancelled)


def _journey(*, legs: list[Any]) -> Any:
    return SimpleNamespace(legs=legs)


def _read_output(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert isinstance(payload, list)
    return payload


def _make_directional_client(
    floridsdorf_to_meidling: list[Any] | Exception,
    meidling_to_floridsdorf: list[Any] | Exception,
) -> Any:
    """Build a fake HafasClient that returns per-direction mock data.

    Each direction can also be an :class:`Exception` instance to
    simulate a per-direction transport error without affecting the
    other direction's outcome.
    """

    def journeys(**kwargs: Any) -> list[Any]:
        origin = kwargs.get("origin")
        destination = kwargs.get("destination")
        if (
            origin == script.FLORIDSDORF_STATION_ID
            and destination == script.MEIDLING_STATION_ID
        ):
            payload = floridsdorf_to_meidling
        elif (
            origin == script.MEIDLING_STATION_ID
            and destination == script.FLORIDSDORF_STATION_ID
        ):
            payload = meidling_to_floridsdorf
        else:  # pragma: no cover - defensive: unexpected origin/dest pair
            raise AssertionError(
                f"Unexpected origin/destination pair: {origin!r} → {destination!r}"
            )
        if isinstance(payload, Exception):
            raise payload
        return payload

    return SimpleNamespace(profile=SimpleNamespace(), journeys=journeys)


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(script, "_build_client", lambda: client)


def _set_now(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    monkeypatch.setattr(script, "_now_vienna", lambda: when)


def _high_journeys() -> list[Any]:
    return [
        _journey(legs=[_leg(name="S 1", delay_minutes=11)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=12)]),
        _journey(legs=[_leg(name="S 3", delay_minutes=10)]),
    ]


def _low_journeys() -> list[Any]:
    return [
        _journey(legs=[_leg(name="S 1", delay_minutes=2)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=3)]),
    ]


# ---- Helper-level unit tests -----------------------------------------------


def test_is_sbahn_leg_matches_canonical_labels() -> None:
    assert script._is_sbahn_leg(_leg(name="S 1", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="S 7", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="S 80", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="s 2", delay_minutes=0))


def test_is_sbahn_leg_rejects_non_sbahn() -> None:
    assert not script._is_sbahn_leg(_leg(name="REX 7", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="IC 533", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="Railjet 162", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="", delay_minutes=0))


def test_is_sbahn_leg_handles_missing_or_non_string_name() -> None:
    bad = SimpleNamespace(departure_delay=timedelta(minutes=5), cancelled=False)
    assert not script._is_sbahn_leg(bad)
    bad_int = SimpleNamespace(name=42, departure_delay=None, cancelled=False)
    assert not script._is_sbahn_leg(bad_int)


def test_collect_delays_includes_only_sbahn_with_delay() -> None:
    journeys = [
        _journey(legs=[_leg(name="S 1", delay_minutes=4)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=10)]),
        # Non-S-Bahn — must be ignored.
        _journey(legs=[_leg(name="REX 7", delay_minutes=20)]),
        # S-Bahn but cancelled — ignored (no signal).
        _journey(legs=[_leg(name="S 3", delay_minutes=15, cancelled=True)]),
        # S-Bahn but no delay value — ignored.
        _journey(legs=[_leg(name="S 80", delay_minutes=None)]),
    ]
    delays = script._collect_sbahn_delays_minutes(journeys)
    assert delays == [4.0, 10.0]


def test_collect_delays_handles_missing_legs_attribute() -> None:
    """A misbehaving HAFAS peer might emit journeys without a ``legs`` attr."""

    journeys = [SimpleNamespace()]  # no legs attribute at all
    assert script._collect_sbahn_delays_minutes(journeys) == []


def test_collect_delays_handles_legs_set_to_none() -> None:
    journeys = [SimpleNamespace(legs=None)]
    assert script._collect_sbahn_delays_minutes(journeys) == []


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


def test_breaker_config_aligns_with_10_per_hour_budget() -> None:
    """Pin the rate-limit-aligned breaker constants documented in the script."""

    assert script.BREAKER_FAILURE_THRESHOLD == 10
    assert script.BREAKER_RECOVERY_TIMEOUT == 3600.0


def test_max_journeys_per_query_is_pinned_to_five() -> None:
    """Pin ``MAX_JOURNEYS_PER_QUERY`` so future drift is caught at PR-review.

    Five is the smallest sample that yields a stable median while
    keeping the HAFAS payload minimal (two directions × five journeys
    per cron tick = 10 journey objects).
    """

    assert script.MAX_JOURNEYS_PER_QUERY == 5


def test_query_journeys_forwards_max_journeys_kwarg(
    monkeypatch: pytest.MonkeyPatch, _stable_now: datetime
) -> None:
    """``_query_journeys`` must pass ``max_journeys=5`` to the pyhafas client.

    A regression here would silently revert to the pyhafas default
    (which can range from 5 to ~30 depending on profile) and bloat the
    HAFAS payload — which is precisely what the cap is meant to
    prevent.
    """

    captured: dict[str, Any] = {}

    def journeys(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    fake_client = SimpleNamespace(profile=SimpleNamespace(), journeys=journeys)
    direction = script.DIRECTIONS[0]

    result = script._query_journeys(fake_client, direction, when=_stable_now)
    assert result == []
    assert captured["max_journeys"] == 5
    assert captured["max_changes"] == 0
    assert captured["origin"] == direction.origin_id
    assert captured["destination"] == direction.destination_id


# ---- _patch_session_timeout tests ----------------------------------------


class _FakeSession:
    """Minimal stand-in for ``requests.Session`` capturing kwargs."""

    def __init__(self) -> None:
        self.captured_kwargs: dict[str, Any] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> str:
        self.captured_kwargs = kwargs
        return f"{method} {url}"


def test_patch_session_timeout_injects_default_timeout() -> None:
    """A request without an explicit ``timeout`` kwarg gets the default."""

    session = _FakeSession()
    profile = SimpleNamespace(request_session=session)
    script._patch_session_timeout(profile, 7.5)
    session.request("POST", "https://example.com/api")
    assert session.captured_kwargs["timeout"] == 7.5


def test_patch_session_timeout_respects_explicit_timeout() -> None:
    """A request that already specifies ``timeout`` keeps that value."""

    session = _FakeSession()
    profile = SimpleNamespace(request_session=session)
    script._patch_session_timeout(profile, 7.5)
    session.request("POST", "https://example.com/api", timeout=42.0)
    assert session.captured_kwargs["timeout"] == 42.0


def test_patch_session_timeout_handles_missing_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If pyhafas's profile has no ``request_session``, log + degrade silently."""

    profile = SimpleNamespace()  # no request_session
    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    script._patch_session_timeout(profile, 5.0)  # must not raise
    assert any(
        "kein Timeout-Enforcement" in record.getMessage() for record in caplog.records
    )


def test_patch_session_timeout_handles_session_without_request() -> None:
    """A non-requests-shaped session object is treated like a missing one."""

    profile = SimpleNamespace(request_session=SimpleNamespace())
    script._patch_session_timeout(profile, 5.0)  # must not raise


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
                {"_identity": 42, "first_seen": "2026-05-09T08:00:00+02:00"},  # bad identity
                {"_identity": "x|y", "first_seen": 999},  # bad first_seen
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
# The four tests below pin the Round 8 hardening of the
# ``_read_existing_first_seen`` reader. Each test is a Proof-of-Concept that
# fails pre-fix (the bare ``_json_lib.load(fh)`` site at the script's
# previous line 426) and passes post-fix (after the swap to
# ``read_capped_json`` plus the per-preserved-field shape validators).
#
# Threat model: ``cache/stammstrecke/events.json`` is persistent state
# written by the cron monitor and read by both (a) the build_feed pipeline
# and (b) the monitor itself on the next tick (via this reader, to
# preserve ``first_seen`` across runs). A planted-huge / poisoned cache
# file from any of the documented threat sources (compromised CI runner /
# partial flush + power loss / corrupted previous run / parallel
# orchestrator atomic state swap) reaches both consumers; the bare
# ``json.load`` shape on the writer's read path opened the size-bomb /
# field-shape gap that the ``read_capped_json`` defence and the
# per-field validators close. See ``.jules/sentinel.md`` (entry for
# 2026-05-09) for the full threat-model write-up.


def test_read_existing_first_seen_rejects_oversized_cache_file(
    _isolated_output: Path,
) -> None:
    """PoC: a cache file larger than ``MAX_STAMMSTRECKE_CACHE_BYTES`` must
    be rejected (pre-fix it was buffered into memory via bare
    ``_json_lib.load`` with no cap, propagating ``MemoryError`` past the
    ``except (OSError, JSONDecodeError, UnicodeDecodeError)`` clause)."""
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    # Pad the legitimate-shape entry until the serialised file exceeds
    # the per-loader cap. The padding is a passive payload field — it has
    # no effect on the reader's parser other than inflating the file size.
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

    # Post-fix: the size cap rejects the read; the loader returns an
    # empty map and the next ``_write_cache`` will overwrite the
    # corrupted file. Pre-fix: returned ``{"stammstrecke_delay_meidling":
    # "2026-05-09T08:00:00+02:00"}`` after buffering the entire 256+ KiB
    # payload into memory.
    assert script._read_existing_first_seen() == {}


def test_read_existing_first_seen_rejects_oversized_first_seen_field(
    _isolated_output: Path,
) -> None:
    """PoC: an item with a ``first_seen`` longer than
    ``_MAX_PRESERVED_FIRST_SEEN_LENGTH`` must be skipped. Pre-fix the
    field-length check did not exist, so an attacker who could plant a
    sub-cap-but-large-field cache (e.g. 100 KiB ``first_seen`` string)
    saw the value flow into ``_resolve_first_seen``, which logs a
    sanitised version of the failed-parse string via
    ``sanitize_log_arg`` — amplifying log volume without bound."""
    _isolated_output.parent.mkdir(parents=True, exist_ok=True)
    # 80 chars > 64-byte cap, well below the file-size cap.
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
    """PoC: an item with an ``_identity`` longer than
    ``_MAX_PRESERVED_IDENTITY_LENGTH`` must be skipped. Pre-fix any
    string was accepted; the prefix derived via
    ``identity.split("|", 1)[0]`` could grow to file-cap size and pollute
    the returned map's keys (and any downstream log emission)."""
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
    """PoC: items with XML 1.0 control characters in either preserved
    field must be skipped. Pre-fix only ``isinstance(..., str)`` was
    checked; a ``\\x00``/``\\x07``/``\\x1f`` byte in either field flowed
    through to the build-side cache loader and onward into the rendered
    feed text without the canonical ``_sanitize_text`` filter applied
    at the boundary."""
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
    """PoC: a ``first_seen`` string that is not parseable via
    :func:`datetime.fromisoformat` must be rejected at the read site
    rather than propagating into ``_resolve_first_seen`` and triggering
    a defensive WARNING log on every cron tick."""
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
    """Pin the per-loader byte cap to the canonical value so a future
    "tighten further" change is a single search-replace and the import
    chain (script ↔ ``src/feed/providers.py``) cannot drift."""
    # Sized at ~128x the largest legitimate state shape (~2 KiB) — see
    # the inline rationale in ``src/feed/providers.py``.
    assert MAX_STAMMSTRECKE_CACHE_BYTES == 256 * 1024


def test_is_valid_preserved_first_seen_accepts_canonical_iso() -> None:
    """The canonical writer path produces ``datetime.isoformat()`` strings;
    the validator must accept every shape the writer emits."""
    sample = datetime(2026, 5, 9, 8, 0, 0, tzinfo=VIENNA_TZ).isoformat()
    assert script._is_valid_preserved_first_seen(sample) is True


def test_is_valid_preserved_first_seen_rejects_non_string() -> None:
    """``isinstance(value, str)`` is the first gate of the TypeGuard so
    non-string values (int, list, None) cannot bypass downstream checks
    by being implicitly stringified."""
    assert script._is_valid_preserved_first_seen(999) is False
    assert script._is_valid_preserved_first_seen(None) is False
    assert script._is_valid_preserved_first_seen([]) is False


def test_is_valid_preserved_identity_accepts_canonical_shape() -> None:
    """Canonical identity is ``<prefix>|<iso>`` — the validator must
    accept every shape the writer emits."""
    sample = "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00"
    assert script._is_valid_preserved_identity(sample) is True


def test_is_valid_preserved_identity_rejects_non_string() -> None:
    """Non-string identity values must be rejected upstream — pre-fix
    the previous ``isinstance(identity, str)`` check did this; the
    validator preserves that contract while ALSO bounding length and
    control characters."""
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
    """A prior ISO without tz info is force-localised to Europe/Vienna."""

    naive = "2026-05-01T10:00:00"
    result = script._resolve_first_seen("p", {"p": naive}, _stable_now)
    assert result.tzinfo is not None
    # Vienna in May → +02:00.
    assert result.utcoffset() == timedelta(hours=2)


# ---- _build_event tests ----------------------------------------------------


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
    # Timestamps must be ISO-8601 strings with offset (Europe/Vienna).
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
    # 15.0 must render as "15" (no trailing zero) per ``_format_minutes``.
    assert (
        event["description"]
        == "Durchschnittliche Verspätung von 15 Minuten in Richtung Floridsdorf [Seit 09.05.2026]"
    )
    assert event["_identity"].startswith("stammstrecke_delay_floridsdorf|")


def test_build_event_uses_first_seen_for_seit_date(_stable_now: datetime) -> None:
    """The "[Seit DD.MM.YYYY]" date comes from ``first_seen``, NOT pub_date.

    A continuing episode must keep the original first-observed date in
    the description even when the cron tick advances.
    """

    pub = _stable_now + timedelta(days=2)
    first = _stable_now  # episode started 2 days ago
    event = script._build_event(
        direction=script.DIRECTIONS[0],
        median_delay_minutes=12.0,
        pub_date=pub,
        first_seen=first,
    )
    assert "[Seit 09.05.2026]" in event["description"]
    assert "[Seit 11.05.2026]" not in event["description"]
    # And the timestamps follow the contract.
    assert event["pubDate"] == pub.isoformat()
    assert event["first_seen"] == first.isoformat()
    assert event["starts_at"] == first.isoformat()


def test_build_event_guid_is_stable_for_same_episode(_stable_now: datetime) -> None:
    """Two ticks of the same episode produce the same GUID."""

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
    """Different episodes (different first_seen) produce different GUIDs."""

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
    """Each direction must produce a distinct GUID for the same timestamp."""

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
    """Pin schema compliance against ``docs/schema/events.schema.json``."""

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
    """Both directions exceed the threshold → one event per direction."""

    fwd = [
        _journey(legs=[_leg(name="S 1", delay_minutes=11)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=10)]),
        _journey(legs=[_leg(name="S 3", delay_minutes=12)]),
    ]
    bwd = [
        _journey(legs=[_leg(name="S 7", delay_minutes=14)]),
        _journey(legs=[_leg(name="S 80", delay_minutes=15)]),
        _journey(legs=[_leg(name="S 1", delay_minutes=13)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    descriptions = {event["description"] for event in payload}
    assert any("in Richtung Meidling [Seit" in d for d in descriptions)
    assert any("in Richtung Floridsdorf [Seit" in d for d in descriptions)
    # Both events must have distinct GUIDs.
    guids = {event["guid"] for event in payload}
    assert len(guids) == 2
    # Each event includes a first_seen field.
    assert all("first_seen" in event for event in payload)


def test_main_writes_only_meidling_event_when_only_forward_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [
        _journey(legs=[_leg(name="S 1", delay_minutes=14)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=13)]),
    ]
    bwd = [_journey(legs=[_leg(name="S 7", delay_minutes=2)])]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Meidling" in payload[0]["description"]
    assert payload[0]["_identity"].startswith("stammstrecke_delay_meidling|")


def test_main_writes_empty_when_both_directions_below_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Self-Healing: median ≤ threshold for both directions → cache cleared."""

    _patch_client(monkeypatch, _make_directional_client(_low_journeys(), _low_journeys()))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_median_equals_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Median exactly equal to threshold must NOT trigger the event."""

    nine = [
        _journey(legs=[_leg(name="S 1", delay_minutes=9)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=9)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(nine, nine))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_no_sbahn_legs(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Only non-S-Bahn legs in either direction → empty cache, exit 0."""

    fwd = [_journey(legs=[_leg(name="REX 7", delay_minutes=20)])]
    bwd = [_journey(legs=[_leg(name="IC 533", delay_minutes=15)])]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_partial_failure_keeps_other_direction_event(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
) -> None:
    """A transient error on one direction must not discard the other.

    Per the per-direction-isolation rule: if one direction succeeds with
    a high median, that event is persisted even when the other
    direction's call raised.
    """

    fwd_error = RuntimeError("transient connection reset")
    _patch_client(monkeypatch, _make_directional_client(fwd_error, _high_journeys()))

    assert script.main() == 0  # at least one direction succeeded
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
    _patch_client(monkeypatch, _make_directional_client(err1, err2))

    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_clears_cache_on_import_error(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def raise_import_error() -> Any:
        raise ImportError("OEBBProfile not available in this pyhafas release")

    monkeypatch.setattr(script, "_build_client", raise_import_error)
    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_clears_cache_when_breaker_is_open(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Self-Healing: pre-tripped breaker → cache emptied, exit 0.

    Even if a previous run had written events, a tripped breaker on
    the current run must clear the cache so the RSS feed never carries
    stale warnings.
    """

    # Pre-write a non-empty cache to verify it gets cleared.
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
        "stammstrecke-hafas-pretrip",
        failure_threshold=2,
        recovery_timeout=600.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    monkeypatch.setattr(script, "_BREAKER", breaker)

    upstream_calls: list[tuple[str | None, str | None]] = []

    def must_not_be_called(**kwargs: Any) -> list[Any]:
        upstream_calls.append((kwargs.get("origin"), kwargs.get("destination")))
        return []

    fake_client = SimpleNamespace(
        profile=SimpleNamespace(), journeys=must_not_be_called
    )
    _patch_client(monkeypatch, fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []  # stale entry wiped
    assert upstream_calls == []
    assert any("breaker" in r.getMessage().lower() for r in caplog.records)


def test_main_handles_non_list_payload(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Both directions return a non-list value → both fail, exit 1, cache empty."""

    def journeys(**kwargs: Any) -> Any:
        return "not-a-list"

    fake_client = SimpleNamespace(profile=SimpleNamespace(), journeys=journeys)
    _patch_client(monkeypatch, fake_client)

    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_emits_iso8601_with_vienna_offset(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Verify the timezone contract: pubDate is Europe/Vienna, ISO 8601."""

    _patch_client(monkeypatch, _make_directional_client(_high_journeys(), _high_journeys()))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    for event in payload:
        assert event["pubDate"] == _stable_now.isoformat()
        assert event["pubDate"].endswith("+02:00")  # May → CEST
        # first_seen also Vienna-anchored.
        assert event["first_seen"] == _stable_now.isoformat()


# ---- first_seen persistence integration tests ------------------------------


def test_first_seen_persists_across_consecutive_high_runs(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Two ticks with the same direction over threshold → SAME first_seen.

    The GUID stays stable, the pubDate updates. The "[Seit DD.MM.YYYY]"
    in the description shows the original first-observation date.
    """

    # Tick 1: only Meidling-direction high.
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _low_journeys())
    )
    assert script.main() == 0
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 1
    first_seen_t1 = tick1[0]["first_seen"]
    pub_t1 = tick1[0]["pubDate"]
    guid_t1 = tick1[0]["guid"]
    assert first_seen_t1 == _stable_now.isoformat()
    assert "[Seit 09.05.2026]" in tick1[0]["description"]

    # Tick 2: 30 minutes later, still high.
    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _low_journeys())
    )
    assert script.main() == 0
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 1

    assert tick2[0]["first_seen"] == first_seen_t1  # PRESERVED
    assert tick2[0]["pubDate"] != pub_t1  # advances
    assert tick2[0]["pubDate"] == later.isoformat()
    assert tick2[0]["guid"] == guid_t1  # GUID stable
    # Description's "Seit"-date is still the original observation day.
    assert "[Seit 09.05.2026]" in tick2[0]["description"]


def test_first_seen_regenerates_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Recovery (median ≤ 9) clears the cache, the next high-median tick
    gets a *fresh* ``first_seen`` — a new episode."""

    # Tick 1: high → event written.
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _low_journeys())
    )
    script.main()
    first_seen_t1 = _read_output(_isolated_output)[0]["first_seen"]
    guid_t1 = _read_output(_isolated_output)[0]["guid"]

    # Tick 2: recovery — both directions back to normal.
    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_client(
        monkeypatch, _make_directional_client(_low_journeys(), _low_journeys())
    )
    script.main()
    assert _read_output(_isolated_output) == []  # cache cleared

    # Tick 3: disruption returns — must get a NEW first_seen and a NEW GUID.
    even_later = _stable_now + timedelta(minutes=60)
    _set_now(monkeypatch, even_later)
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _low_journeys())
    )
    script.main()
    tick3 = _read_output(_isolated_output)
    assert len(tick3) == 1
    assert tick3[0]["first_seen"] == even_later.isoformat()
    assert tick3[0]["first_seen"] != first_seen_t1  # FRESH first_seen
    assert tick3[0]["guid"] != guid_t1  # NEW episode → new GUID


def test_first_seen_persistence_is_independent_per_direction(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """One direction's persistence must not leak into the other direction.

    Tick 1: only Meidling direction high. Tick 2: only Floridsdorf
    direction high. Each direction's first_seen must reflect when *that
    specific direction* first crossed the threshold.
    """

    # Tick 1: Meidling high, Floridsdorf low.
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _low_journeys())
    )
    script.main()
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 1
    assert tick1[0]["_identity"].startswith("stammstrecke_delay_meidling|")
    meidling_first_seen = tick1[0]["first_seen"]

    # Tick 2: Floridsdorf high, Meidling low (Meidling recovered).
    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_client(
        monkeypatch, _make_directional_client(_low_journeys(), _high_journeys())
    )
    script.main()
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 1
    assert tick2[0]["_identity"].startswith("stammstrecke_delay_floridsdorf|")
    # Floridsdorf is a NEW episode → first_seen = `later`.
    assert tick2[0]["first_seen"] == later.isoformat()
    # Meidling's prior first_seen is gone (no Meidling event in cache).
    assert meidling_first_seen != tick2[0]["first_seen"]


def test_first_seen_continues_when_only_one_direction_resumes(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Both directions over threshold across two ticks → both events
    keep their original first_seen."""

    # Tick 1: both high.
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _high_journeys())
    )
    script.main()
    tick1 = _read_output(_isolated_output)
    assert len(tick1) == 2
    first_seen_per_direction_t1 = {
        item["_identity"].split("|", 1)[0]: item["first_seen"] for item in tick1
    }

    # Tick 2: still both high.
    later = _stable_now + timedelta(minutes=30)
    _set_now(monkeypatch, later)
    _patch_client(
        monkeypatch, _make_directional_client(_high_journeys(), _high_journeys())
    )
    script.main()
    tick2 = _read_output(_isolated_output)
    assert len(tick2) == 2
    for item in tick2:
        prefix = item["_identity"].split("|", 1)[0]
        assert (
            item["first_seen"] == first_seen_per_direction_t1[prefix]
        ), f"first_seen for {prefix} drifted across ticks"
