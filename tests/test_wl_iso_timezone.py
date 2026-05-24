from datetime import UTC

import pytest

from src.providers.wl_fetch import _best_ts, _iso


def test_iso_returns_utc_aware() -> None:
    dt = _iso("2024-07-01T12:00:00")
    assert dt is not None
    assert dt.tzinfo == UTC


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "2026-13-99",
        "2026-05-24T25:00:00",
        "TBD",
        "2026/05/24",
        "n/a",
        "0000-00-00",
    ],
)
def test_iso_returns_none_on_malformed_input(bad: str) -> None:
    """A malformed upstream timestamp must fail soft (return None), never raise.

    An unhandled ValueError here propagates out of the unguarded item
    loops in ``fetch_events`` and gets swallowed by
    ``update_wl_cache.py``'s broad ``except Exception`` — silently
    disabling the entire WL cache refresh for that cycle.
    """
    assert _iso(bad) is None


def test_best_ts_survives_malformed_timestamp_fields() -> None:
    """One bad field must not abort timestamp resolution for the item."""
    obj = {
        "time": {"start": "not-a-date", "end": "2024-07-01T12:00:00"},
    }
    ts = _best_ts(obj)
    assert ts is not None
    assert ts.tzinfo == UTC


def test_best_ts_all_malformed_returns_none() -> None:
    obj = {
        "time": {"start": "nope", "end": "also-bad"},
        "updated": "???",
        "attributes": {"lastUpdate": "garbage", "created": "2026/13/40"},
    }
    assert _best_ts(obj) is None
