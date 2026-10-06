"""The feed across the clock changes of Europe/Vienna.

Summer time ends on 25.10.2026: at 03:00 the clocks go back to 02:00, the
hour from 02:00 to 03:00 comes twice, and the day has 25 hours. It begins
again on 28.03.2027: 02:00 jumps to 03:00. The examples come from real data:
the night of 26./27.09.2026 replayed four weeks later (48A accident,
"25: Schadhaftes Fahrzeug"), the ÖBB cache since the clocks went forward on
29.03.2026, and the S-Bahn ledger, which has rows at 02:00 and 03:00 on
weekend nights.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import src.build_feed as bf  # noqa: E402
from scripts import update_stammstrecke_hbf as hbf  # noqa: E402
from src.feed import stammstrecke as stammstrecke_module  # noqa: E402
from src.providers.oebb import _parse_dt_rfc2822  # noqa: E402
from src.utils.stats import StammstreckeObservation, read_recent_stammstrecke_observations  # noqa: E402

VIENNA = ZoneInfo("Europe/Vienna")
NBSP = " "
SUMMER = timedelta(hours=2)
WINTER = timedelta(hours=1)


def _utc(day: int, hour: int, minute: int = 0, *, month: int = 10, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _local(when: datetime) -> datetime:
    """*when* in Vienna time, as the feed passes it."""
    return when.astimezone(VIENNA)


def _line(start: datetime, end: datetime | None, now: datetime, since: datetime | None) -> str:
    return bf.format_local_times(
        _local(start), _local(end) if end else None, _local(now), since=_local(since) if since else None
    ).replace(NBSP, " ")


# ---- Time line --------------------------------------------------------------


def test_an_incident_from_the_first_two_oclock_hour_keeps_its_time_in_the_second() -> None:
    # 48A accident at 02:26 summer time (00:26 UTC), build at 02:01 winter
    # time (01:01 UTC), 35 minutes later. Its wall clock reads later than
    # the build's, which made the line fall back to "Heute".
    began = _utc(25, 0, 26)
    assert began.astimezone(VIENNA).utcoffset() == SUMMER
    now = _utc(25, 1, 1)
    assert now.astimezone(VIENNA).utcoffset() == WINTER
    assert _line(began, _utc(25, 2, 0), now, began) == "Seit 02:26"


def test_an_incident_after_the_build_stays_unshown() -> None:
    # The other way round: an incident at 02:40 winter time is still in
    # the future for a build at 02:50 summer time.
    began = _utc(25, 1, 40)
    now = _utc(25, 0, 50)
    assert _line(began, None, now, began) == "Seit heute"


def test_an_end_in_the_second_two_oclock_hour_is_after_a_start_in_the_first() -> None:
    # Start 02:50 summer time, end 02:20 winter time: thirty minutes later,
    # not before the start. The end used to be dropped as implausible.
    start = _utc(25, 0, 50)
    end = _utc(25, 1, 20)
    kept = bf._plausible_end(start.astimezone(VIENNA), end.astimezone(VIENNA), _utc(25, 0, 55))
    # Compared in UTC: a datetime with fold=1 never equals one in another zone.
    assert kept is not None and kept.astimezone(UTC) == end


def test_the_25_hour_day_is_one_day() -> None:
    # Everything from 00:00 summer time to 23:59 winter time is "today".
    start = _utc(24, 22, 0)  # 25.10. 00:00 summer time
    end = _utc(25, 22, 59)  # 25.10. 23:59 winter time
    assert _line(start, end, _utc(25, 12, 0), None) == "Heute"
    assert bf._time_line_day(end.astimezone(VIENNA), end.astimezone(VIENNA).date()) == "So 25.10."


# ---- ÖBB publication times ----------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        # Winter messages as ÖBB has sent them since 29.03.2026: the wall
        # clock of the publication with the summer offset of the fetch.
        ("Fri, 19 Dec 2025 10:07:13 +0200", datetime(2025, 12, 19, 9, 7, 13, tzinfo=UTC)),
        ("Fri, 27 Mar 2026 11:42:37 +0200", datetime(2026, 3, 27, 10, 42, 37, tzinfo=UTC)),
        # Summer messages after 25.10.2026 will carry the winter offset.
        ("Sat, 24 Oct 2026 15:10:21 +0100", datetime(2026, 10, 24, 13, 10, 21, tzinfo=UTC)),
        # A stamp whose offset fits stays as sent.
        ("Sat, 03 Oct 2026 15:23:00 +0200", datetime(2026, 10, 3, 13, 23, tzinfo=UTC)),
        ("Mon, 01 Jan 2024 10:00:00 +0100", datetime(2024, 1, 1, 9, 0, tzinfo=UTC)),
        # In the repeated hour both offsets fit; the one sent decides.
        ("Sun, 25 Oct 2026 02:30:00 +0200", datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),
        ("Sun, 25 Oct 2026 02:30:00 +0100", datetime(2026, 10, 25, 1, 30, tzinfo=UTC)),
        # Not a Vienna offset: an exact moment, left alone.
        ("Mon, 02 Feb 2026 08:04:19 +0000", datetime(2026, 2, 2, 8, 4, 19, tzinfo=UTC)),
    ],
)
def test_oebb_publication_time_is_vienna_wall_clock(sent: str, expected: datetime) -> None:
    parsed = _parse_dt_rfc2822(sent)
    assert parsed == expected
    if not sent.endswith("+0000"):
        assert parsed is not None and parsed.astimezone(VIENNA).strftime("%H:%M:%S") == sent[17:25]


def test_oebb_winter_message_after_midnight_keeps_its_day() -> None:
    # Read with the summer offset, a message from 00:30 on 15.01. fell on
    # 14.01. ("Seit 14.01." in the time line of an ÖBB message without a
    # validity period).
    parsed = _parse_dt_rfc2822("Thu, 15 Jan 2026 00:30:00 +0200")
    assert parsed is not None and parsed.astimezone(VIENNA).date().isoformat() == "2026-01-15"


# ---- Stammstrecke monitor ---------------------------------------------------


def _as_written(when: datetime) -> datetime:
    """*when* with a fixed offset, as the ledger reader returns its rows."""
    offset = when.astimezone(VIENNA).utcoffset()
    assert offset is not None
    return when.astimezone(timezone(offset))


def _rows(now: datetime, minutes_ago: list[int], delay: float) -> list[StammstreckeObservation]:
    return [
        StammstreckeObservation(
            timestamp=_as_written(now - timedelta(minutes=ago)),
            direction="Praterstern",
            delay_minutes=delay,
        )
        for ago in minutes_ago
    ]


def _events(now_utc: datetime, rows: list[StammstreckeObservation], tmp_path: Path) -> list[dict[str, Any]]:
    with patch.object(stammstrecke_module, "read_recent_stammstrecke_observations", return_value=rows):
        return stammstrecke_module.compute_stammstrecke_events(
            now=now_utc.astimezone(VIENNA), episode_starts_path=tmp_path / "episode_starts.json"
        )


def test_stammstrecke_window_is_one_real_hour_when_the_clocks_go_forward(tmp_path: Path) -> None:
    # 28.03.2027, 03:30 summer time: two delayed ticks 20 and 50 minutes
    # ago. ``now - 1 h`` on the wall clock was 02:30, a time that does not
    # exist and reads as 03:30, so the window was empty.
    now = _utc(28, 1, 30, month=3, year=2027)
    assert now.astimezone(VIENNA).strftime("%H:%M") == "03:30"
    events = _events(now, _rows(now, [50, 20], 12.0), tmp_path)
    assert len(events) == 1


def test_stammstrecke_window_is_one_real_hour_when_the_clocks_go_back(tmp_path: Path) -> None:
    # 25.10.2026, 03:30 winter time: ticks 80 and 110 minutes ago lie
    # outside the hour. ``now - 1 h`` on the wall clock was 02:30 summer
    # time, two real hours back.
    now = _utc(25, 2, 30)
    events = _events(now, _rows(now, [110, 80], 12.0), tmp_path)
    assert events == []


def test_ledger_reader_window_is_real_hours(tmp_path: Path) -> None:
    stats = tmp_path / "stats"
    stats.mkdir()
    rows = [
        ("2026-10-25T01:40:00+02:00", "So", "01"),  # 23:40 UTC, 2:50 h before
        ("2026-10-25T02:45:00+02:00", "So", "02"),  # 00:45 UTC, first 02:45
        ("2026-10-25T02:15:00+01:00", "So", "02"),  # 01:15 UTC, second 02:15
    ]
    (stats / "stammstrecke_2026.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        + "".join(f"{ts},{wd},{h},Praterstern,12.00\n" for ts, wd, h in rows),
        encoding="utf-8",
    )
    now = _utc(25, 2, 30)  # 03:30 winter time
    found = read_recent_stammstrecke_observations(
        now=now.astimezone(VIENNA), window=timedelta(hours=2), stats_dir=stats
    )
    assert [obs.timestamp.isoformat() for obs in found] == [rows[1][0], rows[2][0]]


@pytest.mark.parametrize(
    ("dep", "minutes"),
    [
        # 28.03.2027: due 01:55 winter time, gone 03:05 summer time.
        ({"date": "2027-03-28", "time": "01:55:00", "rtTime": "03:05:00"}, 10.0),
        # 25.10.2026: due 01:55 summer time, gone 03:05 winter time.
        ({"date": "2026-10-25", "time": "01:55:00", "rtTime": "03:05:00"}, 130.0),
        # Within the repeated hour the wall clocks decide, as before.
        ({"date": "2026-10-25", "time": "02:10:00", "rtTime": "02:16:00"}, 6.0),
        ({"date": "2026-10-25", "time": "02:55:00", "rtTime": "03:05:00"}, 10.0),
        # An ordinary day is unchanged.
        ({"date": "2026-05-15", "time": "08:00:00", "rtTime": "08:07:00"}, 7.0),
    ],
)
def test_vao_delay_across_the_clock_change(dep: dict[str, str], minutes: float) -> None:
    assert hbf._departure_delay_minutes(dep) == minutes
