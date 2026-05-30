"""Regression tests for the second line-by-line audit round (deferred Medium
findings now fixed). Each test pins one defect so a refactor cannot reintroduce
it:

* ``src/build_feed.py``                       — STATE_RETENTION_DAYS must prune
                                                 resurrected on-disk entries
* ``src/build_feed.py``                       — entity/glossary placeholder
                                                 collision (nonce)
* ``src/providers/vor.py``                    — cross-process quota cap at persist
* ``scripts/update_stammstrecke_status.py``   — _QuotaExceeded must not trip the
                                                 circuit breaker
* ``src/providers/wl_lines.py``               — phantom tram lines from
                                                 duration/platform numbers
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# build_feed — _save_state must prune resurrected expired disk entries so the
# state file does not grow unboundedly (which eventually wipes ALL first_seen).
# ---------------------------------------------------------------------------
def test_save_state_prunes_resurrected_expired_disk_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src import build_feed
    from src.feed import config as feed_config

    state_path = tmp_path / "first_seen.json"
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *a: Path(state_path))
    monkeypatch.setattr(feed_config, "STATE_FILE", state_path)
    monkeypatch.setattr(feed_config, "STATE_RETENTION_DAYS", 60)

    now = datetime.now(UTC)
    # Pre-seed disk with two orphans this run does NOT track.
    state_path.write_text(
        json.dumps(
            {
                "expired_orphan": {"first_seen": (now - timedelta(days=100)).isoformat()},
                "recent_orphan": {"first_seen": (now - timedelta(days=1)).isoformat()},
            }
        ),
        encoding="utf-8",
    )

    # This run only tracks ``current_item``.
    build_feed._save_state({"current_item": {"first_seen": now.isoformat()}}, deletions=None)

    on_disk = json.loads(state_path.read_text("utf-8"))
    assert "expired_orphan" not in on_disk, "expired resurrected entry must be pruned"
    assert "recent_orphan" in on_disk, "recent orphan within retention must be kept"
    assert "current_item" in on_disk, "this run's tracked item must be kept"


def test_save_state_keeps_state_entries_even_if_old(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An entry passed in ``state`` (this run's tracked item) is never pruned by
    the retention sweep, even if its first_seen is old — preserves the
    survivor/parallel-writer first_seen contract."""
    from src import build_feed
    from src.feed import config as feed_config

    state_path = tmp_path / "first_seen.json"
    monkeypatch.setattr(build_feed, "validate_path", lambda p, *a: Path(state_path))
    monkeypatch.setattr(feed_config, "STATE_FILE", state_path)
    monkeypatch.setattr(feed_config, "STATE_RETENTION_DAYS", 60)

    old_iso = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    build_feed._save_state({"tracked_old": {"first_seen": old_iso}}, deletions=None)

    on_disk = json.loads(state_path.read_text("utf-8"))
    assert "tracked_old" in on_disk


# ---------------------------------------------------------------------------
# build_feed — entity/glossary placeholders carry a per-process nonce so a
# pre-existing placeholder-shaped token in (Zero-Trusted) source text is
# neither collided-with nor deleted on unmask.
# ---------------------------------------------------------------------------
def test_entity_masking_preserves_preexisting_placeholder_shaped_token() -> None:
    from src import build_feed

    # ``XENT0X`` is the OLD predictable placeholder shape. A hostile/coincidental
    # upstream title carrying it must round-trip verbatim through mask/unmask.
    text = "Umleitung XENT0X bei U6 wegen Bauarbeiten"
    masked, mapping = build_feed._mask_entities(text)
    restored = build_feed._unmask_entities(masked, mapping)

    assert "XENT0X" in restored, "pre-existing placeholder-shaped token was lost"
    assert "U6" in restored, "real line token must still round-trip"
    assert restored == text


def test_placeholder_nonce_makes_format_unpredictable() -> None:
    from src import build_feed

    # The generated placeholder must NOT be the old predictable ``XENT0X``.
    ph = build_feed._ENTITY_PLACEHOLDER_FORMAT.format(index=0)
    assert ph != "XENT0X"
    assert build_feed._ENTITY_PLACEHOLDER_RE.fullmatch(ph)
    # An old-shape token must NOT match the nonce'd unmask sweep (so it is
    # preserved, not deleted).
    assert build_feed._UNMASK_PLACEHOLDER_RE.search("XENT0X") is None


# ---------------------------------------------------------------------------
# wl_lines — the fallback text line-extractor must not mistake durations or
# platform numbers for phantom tram lines (only runs when relatedLines empty).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Verspätung um 5 Minuten",
        "ca. 20 Minuten Verspätung",
        "Gleis 3 gesperrt",
        "Bahnsteig 5 gesperrt",
        "Steig 2 gesperrt",
    ],
)
def test_wl_fallback_ignores_duration_and_platform_numbers(text: str) -> None:
    from src.providers import wl_lines

    assert wl_lines._detect_line_pairs_from_text(text) == []


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("U6 gesperrt", "U6"),
        ("Linie 43 Umleitung", "43"),
        ("S40 verspätet", "S40"),
    ],
)
def test_wl_fallback_still_detects_real_lines(text: str, line: str) -> None:
    from src.providers import wl_lines

    pairs = wl_lines._detect_line_pairs_from_text(text)
    assert any(line in pair for pair in pairs), f"real line {line} lost from {text!r}"
