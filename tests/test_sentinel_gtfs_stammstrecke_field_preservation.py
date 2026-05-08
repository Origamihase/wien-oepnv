"""Sentinel: GTFS Stammstrecke cache field-preservation amplification.

The PR #1352 refactor moved the Stammstrecke provider from a live network
fetcher to a cache-driven provider with persistent state. The state file
``cache/gtfs_stammstrecke/events.json`` is read on every 30-minute write
cycle (``scripts/update_gtfs_cache.py``) AND on every 5-minute build_feed
cycle (``src/providers/gtfs_stammstrecke.py``). The write cycle uniquely
**preserves** ``events[0].guid`` and ``events[0].first_seen`` from the
existing cache document forward into the next document, so the rendered
``[Seit DD.MM.YYYY]`` anchor stays anchored to the start of the
disruption and the dedupe pipeline does not churn across refreshes.

The journal entry for the refactor (`.jules/sentinel.md`, *Architecture:
GTFS-RT Stammstrecke Demoted From Live Provider to Cache-Driven Provider*)
documented the cache as a new piece of persistent state and noted the
title is bounded by ``_coerce_int_minutes(X)`` interpolation. **But the
preservation logic does NOT validate the shape of the preserved guid /
first_seen strings.** A poisoned cache file (compromised CI runner /
partial flush + power loss / corrupted previous run / parallel
orchestrator process performing an atomic state swap mid-read) that
plants a multi-KiB string in either field is:

  (a) **persisted forward** by ``compute_next_state`` into the next
      cache document (write-side preservation loop) — the corruption
      survives every refresh until manual intervention;
  (b) **auto-committed** to the repo by the
      ``update-gtfs-cache.yml`` workflow (no human review on the
      30-minute cron tick);
  (c) **ingested into the RSS feed** by ``build_event_from_state``
      (read side flows ``state["guid"]`` directly into
      ``FeedItem.guid`` → ``ET.SubElement(item, "guid").text`` with NO
      length cap and NO control-character filter — the
      ``_sanitize_text`` filter from build_feed is applied to title /
      description / time-line, but ``guid`` bypasses it entirely);
  (d) **amplified** by the ``read_capped_json`` 50 MiB ceiling — a
      poisoned guid / first_seen up to ~50 MiB persists across every
      auto-refresh.

Persistence amplification: a single one-time write to the cache file
propagates indefinitely via the preservation loop, so the threat model
is "any actor who can write the cache file ONCE" — strictly weaker
than the "actor who can write on every refresh" assumption that other
caches' threat models live under.

This module pins:

  (1) **Write-side preservation cap on guid**: ``compute_next_state``
      MUST NOT preserve a guid string longer than
      ``MAX_PRESERVED_GUID_LENGTH``; oversized guids fall through to a
      freshly-synthesised ``make_guid`` value.

  (2) **Write-side preservation cap on first_seen**: ``compute_next_state``
      MUST NOT preserve a first_seen string longer than
      ``MAX_PRESERVED_FIRST_SEEN_LENGTH`` or one that fails ISO-8601
      parsing; oversized / unparseable first_seen values fall through
      to ``current_iso``.

  (3) **Write-side control-character filter**: a guid / first_seen
      string containing XML 1.0 control bytes (``\\x00``-``\\x08`` /
      ``\\x0B`` / ``\\x0C`` / ``\\x0E``-``\\x1F`` / ``\\x7F``) MUST be
      rejected so a poisoned cache cannot inject control bytes into
      the RSS feed via the ``<guid>`` element.

  (4) **Read-side guid validation**: ``build_event_from_state`` MUST
      synthesise a fresh guid via ``make_guid`` when the persisted
      guid fails the same length / control-char check.

  (5) **Cache file size cap is tightened** from the 50 MiB
      ``read_capped_json`` default to ``MAX_GTFS_STAMMSTRECKE_CACHE_BYTES``
      (256 KiB) — production state is ~1 KiB so the tighter cap leaves
      256x headroom while denying the multi-MiB amplification window.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from scripts import update_gtfs_cache as updater
from src.providers import gtfs_stammstrecke
from src.providers.gtfs_stammstrecke import (
    MAX_GTFS_STAMMSTRECKE_CACHE_BYTES,
    MAX_PRESERVED_FIRST_SEEN_LENGTH,
    MAX_PRESERVED_GUID_LENGTH,
    build_event_from_state,
    fetch_events,
    is_valid_preserved_first_seen,
    is_valid_preserved_guid,
)

VIENNA = ZoneInfo("Europe/Vienna")


# ============================================================================
# Preconditions: the canonical caps + helpers exist and are within bounds
# ============================================================================


def test_preserved_guid_cap_is_positive_and_reasonable() -> None:
    """A real make_guid output is 64 hex chars; cap MUST exceed that."""
    assert isinstance(MAX_PRESERVED_GUID_LENGTH, int)
    assert 64 < MAX_PRESERVED_GUID_LENGTH <= 4096


def test_preserved_first_seen_cap_is_positive_and_reasonable() -> None:
    """A canonical Vienna ISO datetime is ~32 chars; cap MUST exceed that."""
    assert isinstance(MAX_PRESERVED_FIRST_SEEN_LENGTH, int)
    assert 32 < MAX_PRESERVED_FIRST_SEEN_LENGTH <= 256


def test_cache_size_cap_is_tighter_than_read_capped_json_default() -> None:
    """Production cache state is ~1 KiB so the cap leaves >= 100x headroom
    while denying multi-MiB amplification."""
    assert isinstance(MAX_GTFS_STAMMSTRECKE_CACHE_BYTES, int)
    assert 4096 <= MAX_GTFS_STAMMSTRECKE_CACHE_BYTES <= 4 * 1024 * 1024


# ============================================================================
# Validators: shape contract
# ============================================================================


def test_is_valid_preserved_guid_accepts_typical_make_guid_output() -> None:
    typical = "a" * 64  # SHA256 hex shape
    assert is_valid_preserved_guid(typical) is True


def test_is_valid_preserved_guid_rejects_non_string() -> None:
    assert is_valid_preserved_guid(None) is False
    assert is_valid_preserved_guid(42) is False
    assert is_valid_preserved_guid(["g"]) is False


def test_is_valid_preserved_guid_rejects_empty_or_whitespace() -> None:
    assert is_valid_preserved_guid("") is False
    assert is_valid_preserved_guid("   ") is False


def test_is_valid_preserved_guid_rejects_oversized() -> None:
    poisoned = "a" * (MAX_PRESERVED_GUID_LENGTH + 1)
    assert is_valid_preserved_guid(poisoned) is False


def test_is_valid_preserved_guid_rejects_xml_control_chars() -> None:
    assert is_valid_preserved_guid("abc\x00def") is False
    assert is_valid_preserved_guid("abc\x01def") is False
    assert is_valid_preserved_guid("abc\x1fdef") is False
    assert is_valid_preserved_guid("abc\x7fdef") is False


def test_is_valid_preserved_first_seen_accepts_typical_iso_datetime() -> None:
    assert is_valid_preserved_first_seen("2026-05-08T08:30:00+02:00") is True


def test_is_valid_preserved_first_seen_rejects_oversized() -> None:
    poisoned = "2026-05-08T08:30:00+02:00" + ("X" * MAX_PRESERVED_FIRST_SEEN_LENGTH)
    assert is_valid_preserved_first_seen(poisoned) is False


def test_is_valid_preserved_first_seen_rejects_unparseable_iso() -> None:
    assert is_valid_preserved_first_seen("not a datetime") is False
    assert is_valid_preserved_first_seen("2026-05-08") is True  # date-only is parseable
    assert is_valid_preserved_first_seen("garbage") is False


def test_is_valid_preserved_first_seen_rejects_control_chars() -> None:
    """Even an ISO-parseable string with control bytes must be rejected."""
    poisoned = "2026-05-08T08:30:00+02:00\x00"
    assert is_valid_preserved_first_seen(poisoned) is False


# ============================================================================
# Write-side: compute_next_state must NOT preserve poisoned guid / first_seen
# ============================================================================


def _active_existing_with_guid(guid: str, first_seen: str = "2026-05-08T08:00:00+02:00") -> dict[str, Any]:
    return {
        "events": [
            {
                "guid": guid,
                "first_seen": first_seen,
                "updated": "2026-05-08T11:30:00+02:00",
                "average_delay_minutes": 11,
                "active_trips": 5,
            }
        ],
        "metadata": {"last_run": "2026-05-08T11:30:00+02:00", "version": 1},
    }


def test_compute_next_state_rejects_oversized_preserved_guid() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    poisoned_guid = "X" * (MAX_PRESERVED_GUID_LENGTH + 1024)
    existing = _active_existing_with_guid(poisoned_guid)
    later = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)

    document = updater.compute_next_state(snapshot, existing, now=later)

    event = document["events"][0]
    assert isinstance(event["guid"], str)
    assert len(event["guid"]) <= MAX_PRESERVED_GUID_LENGTH
    assert event["guid"] != poisoned_guid


def test_compute_next_state_rejects_control_char_preserved_guid() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    existing = _active_existing_with_guid("legit-shape\x00with-null-byte")
    later = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)

    document = updater.compute_next_state(snapshot, existing, now=later)

    event = document["events"][0]
    assert "\x00" not in event["guid"]
    # falls back to a freshly-synthesised SHA256 guid
    assert len(event["guid"]) == 64


def test_compute_next_state_rejects_oversized_preserved_first_seen() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    poisoned_first_seen = "2026-05-08T08:00:00+02:00" + ("X" * MAX_PRESERVED_FIRST_SEEN_LENGTH)
    existing = _active_existing_with_guid("preserved-guid", first_seen=poisoned_first_seen)
    now = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)

    document = updater.compute_next_state(snapshot, existing, now=now)

    event = document["events"][0]
    assert isinstance(event["first_seen"], str)
    assert len(event["first_seen"]) <= MAX_PRESERVED_FIRST_SEEN_LENGTH
    # falls back to the current_iso (start of a "fresh" disruption)
    assert event["first_seen"] == now.isoformat()


def test_compute_next_state_rejects_unparseable_preserved_first_seen() -> None:
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    existing = _active_existing_with_guid("preserved-guid", first_seen="not-an-iso-date")
    now = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)

    document = updater.compute_next_state(snapshot, existing, now=now)

    event = document["events"][0]
    assert event["first_seen"] == now.isoformat()


def test_compute_next_state_still_preserves_legitimate_guid_and_first_seen() -> None:
    """Regression: legitimate-shape preserved values MUST still survive."""
    snapshot = updater.StammstreckeStateSnapshot(
        average_delay_minutes=15.2,
        active_trips=8,
    )
    legit_guid = "a" * 64  # SHA256 hex shape
    legit_first_seen = "2026-05-08T08:00:00+02:00"
    existing = _active_existing_with_guid(legit_guid, first_seen=legit_first_seen)
    now = datetime(2026, 5, 8, 12, 30, tzinfo=VIENNA)

    document = updater.compute_next_state(snapshot, existing, now=now)

    event = document["events"][0]
    assert event["guid"] == legit_guid
    assert event["first_seen"] == legit_first_seen


# ============================================================================
# Read-side: build_event_from_state must NOT propagate poisoned guid
# ============================================================================


def _active_state_with_guid(guid: str) -> dict[str, Any]:
    return {
        "guid": guid,
        "first_seen": "2026-05-08T08:00:00+02:00",
        "updated": "2026-05-08T11:30:00+02:00",
        "average_delay_minutes": 12,
        "active_trips": 5,
    }


def test_build_event_from_state_rejects_oversized_guid() -> None:
    poisoned = "X" * (MAX_PRESERVED_GUID_LENGTH + 4096)
    item = build_event_from_state(_active_state_with_guid(poisoned))
    assert item is not None
    guid = item.get("guid")
    assert isinstance(guid, str)
    assert len(guid) <= MAX_PRESERVED_GUID_LENGTH
    assert guid != poisoned
    # falls back to a freshly-synthesised SHA256 hex guid
    assert len(guid) == 64


def test_build_event_from_state_rejects_control_char_guid() -> None:
    item = build_event_from_state(_active_state_with_guid("foo\x00bar"))
    assert item is not None
    guid = item.get("guid")
    assert isinstance(guid, str)
    assert "\x00" not in guid
    assert len(guid) == 64


def test_build_event_from_state_still_uses_legitimate_guid() -> None:
    """Regression: a legitimate explicit guid is still preserved."""
    legit = "explicit-legit-guid-1234"
    item = build_event_from_state(_active_state_with_guid(legit))
    assert item is not None
    assert item.get("guid") == legit


# ============================================================================
# End-to-end: poisoned cache file does not leak into FeedItem
# ============================================================================


def _write_cache_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_fetch_events_does_not_propagate_oversized_guid_to_feeditem(tmp_path: Path) -> None:
    poisoned = "X" * (MAX_PRESERVED_GUID_LENGTH + 8192)
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    _write_cache_document(
        cache_path,
        {
            "events": [_active_state_with_guid(poisoned)],
            "metadata": {"last_run": "2026-05-08T12:00:00+02:00", "version": 1},
        },
    )

    items = fetch_events(cache_path=cache_path)

    assert len(items) == 1
    guid = items[0].get("guid")
    assert isinstance(guid, str)
    assert len(guid) <= MAX_PRESERVED_GUID_LENGTH
    assert guid != poisoned


def test_fetch_events_drops_event_when_cache_exceeds_tighter_size_cap(
    tmp_path: Path,
) -> None:
    """A 1 MiB cache document MUST be rejected (the tighter cap fires)."""
    cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Bloat the metadata to push the file over MAX_GTFS_STAMMSTRECKE_CACHE_BYTES.
    bloat = "x" * (MAX_GTFS_STAMMSTRECKE_CACHE_BYTES + 4096)
    cache_path.write_text(
        json.dumps(
            {
                "events": [_active_state_with_guid("legit")],
                "metadata": {"last_run": "2026-05-08T12:00:00+02:00", "version": 1, "_bloat": bloat},
            }
        ),
        encoding="utf-8",
    )

    items = fetch_events(cache_path=cache_path)
    assert items == []


# ============================================================================
# Persistence amplification: ONE poisoned cache write must NOT propagate
# ============================================================================


def test_run_update_does_not_persist_oversized_guid_across_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A poisoned cache file must NOT have its bad guid copied forward.

    Exploit shape: attacker plants `events[0].guid = "X" * 1MB` once.
    Pre-fix every 30-minute refresh re-reads the bad guid via
    ``_existing_active_event`` → ``compute_next_state`` → ``write_state``,
    perpetuating the poisoned state forever (auto-committed to git on
    every cron tick). Post-fix the validator rejects the oversized guid
    and the next cache write contains a fresh SHA256 guid instead.
    """
    updater._BREAKER.reset()
    try:
        from tests.scripts.test_update_gtfs_cache import _make_entity, _make_feed

        monkeypatch.setattr(
            updater,
            "load_stop_id_index",
            lambda *_a, **_k: {"Wien Floridsdorf": frozenset({"8100008"})},
        )
        monkeypatch.setattr(updater, "fetch_blob", lambda *_a, **_k: b"opaque")
        feed = _make_feed(
            _make_entity(trip_id="trip-1", stop_delays=[("8100008", 720)]),
        )
        monkeypatch.setattr(updater, "parse_feed_message", lambda _blob: feed)

        cache_path = tmp_path / "cache" / "gtfs_stammstrecke" / "events.json"
        # Plant the poisoned cache: one-time write access is enough.
        poisoned_guid = "Y" * (MAX_PRESERVED_GUID_LENGTH + 4096)
        _write_cache_document(
            cache_path,
            {
                "events": [_active_state_with_guid(poisoned_guid)],
                "metadata": {"last_run": "2026-05-08T07:00:00+02:00", "version": 1},
            },
        )

        # Run a refresh — cycle copies forward the existing guid.
        now = datetime(2026, 5, 8, 12, 0, tzinfo=VIENNA)
        exit_code = updater.run_update(
            cache_path=cache_path,
            url="https://realtime.oebb.at/gtfs-rt/tripUpdates",
            now=now,
        )
        assert exit_code == 0

        # Read the persisted cache: the poisoned guid MUST NOT survive.
        document = json.loads(cache_path.read_text(encoding="utf-8"))
        assert len(document["events"]) == 1
        next_guid = document["events"][0]["guid"]
        assert isinstance(next_guid, str)
        assert next_guid != poisoned_guid
        assert len(next_guid) <= MAX_PRESERVED_GUID_LENGTH
    finally:
        updater._BREAKER.reset()


# ============================================================================
# Module-level invariants
# ============================================================================


def test_validators_are_module_exports() -> None:
    """The validators MUST be exported in __all__ so the canonical contract
    is discoverable to future maintainers."""
    assert "is_valid_preserved_guid" in gtfs_stammstrecke.__all__
    assert "is_valid_preserved_first_seen" in gtfs_stammstrecke.__all__
    assert "MAX_PRESERVED_GUID_LENGTH" in gtfs_stammstrecke.__all__
    assert "MAX_PRESERVED_FIRST_SEEN_LENGTH" in gtfs_stammstrecke.__all__
    assert "MAX_GTFS_STAMMSTRECKE_CACHE_BYTES" in gtfs_stammstrecke.__all__
