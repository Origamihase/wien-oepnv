"""A display ticker WL rewrote for a newer incident does not date it back.

Real case 14.11.2026: the ticker ``R577-160`` ("Feuerwehreinsatz / Züge
halten bei Linie 52", start 12:18) came with ``I20261114-0018``
("10, 60: Feuerwehreinsatz", from 12:16). At 13:31 WL rewrote it to
"Fahrtbehinderung / Falschparker" for ``I20261114-0020`` (from 13:12) and
kept its start; the feed showed "60: Falschparker [Seit 12:17]". The
60-minute window (``wl_plausibility.TICKER_LEAD_MAX``) let it through.
Operator decision 2026-11-14 ("Ja, als Klasse beheben"): a ticker of
another incident no longer moves the begin forward. Times are minutes from
``_START`` as in ``tests/test_wl_resolved.py``, whose WL shapes they reuse.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.providers import wl_resolved
from tests.test_wl_resolved import _at, _fetch, _message, _ticker

_FIRE = "Linie 60: Fahrtbehinderung. Grund: Feuerwehreinsatz im Bereich Hietzinger Hauptstraße."
_PARKER = "Linie 60: Kein Betrieb zwischen Hofwiesengasse und Hietzing U. Grund: Falschparker."


def _fire(status: str = "active") -> dict[str, Any]:
    return _message("I20261114-0018", status, "60", "60: Feuerwehreinsatz", _FIRE, closed=50)


def _parker() -> dict[str, Any]:
    return _message("I20261114-0020", "active", "60", "60: Falschparker", _PARKER, closed=200, start=55, created=56)


def _ticker_then() -> dict[str, Any]:
    return _ticker("R577-160", "Feuerwehreinsatz\nZüge halten bei Linie 52", began=1, line="60")


def _ticker_now() -> dict[str, Any]:
    return _ticker("R577-160", "Fahrtbehinderung\nFalschparker", began=1, line="60")


def _begins(infos: list[dict[str, Any]]) -> dict[str, datetime]:
    return wl_resolved.rewritten_starts(infos, lambda info: info.get("status") == "resolved")


def test_rewritten_ticker_takes_the_newer_incidents_start() -> None:
    begins = _begins([_fire("resolved"), _parker(), _ticker_now()])
    assert [moment.isoformat() for moment in begins.values()] == [datetime.fromisoformat(_at(55)).isoformat()]


def test_the_incident_is_remembered_after_it_left(tmp_path: Path) -> None:
    assert _begins([_fire(), _ticker_then()]) == {}
    path = tmp_path / "memory.json"
    wl_resolved.save_memory(path)
    wl_resolved.forget()
    wl_resolved.load_memory(path)
    begins = _begins([_parker(), _ticker_now()])
    assert list(begins) == [wl_resolved.ticker_key(_ticker_now())]
    assert next(iter(begins.values())) == datetime.fromisoformat(_at(55))


def test_a_ticker_ahead_of_its_own_message_keeps_its_start() -> None:
    # "10A: Rettungseinsatz" 07.11.2026: ticker 21:04, message 21:47, nothing before.
    ticker = _ticker("R88-410", "Fahrtbehinderung\nwegen Rettungseinsatz", began=12, line="60")
    assert _begins([_parker(), ticker]) == {}


def test_a_ticker_of_a_running_incident_keeps_its_start() -> None:
    assert _begins([_fire(), _ticker_then()]) == {}
    assert _begins([_fire(), _parker(), _ticker_then()]) == {}


def test_a_ticker_rewritten_while_its_incident_runs_takes_the_newer_start() -> None:
    assert _begins([_fire(), _ticker_then()]) == {}
    begins = _begins([_fire(), _parker(), _ticker_now()])
    assert list(begins.values()) == [datetime.fromisoformat(_at(55))]


def test_a_ticker_of_its_own_incident_keeps_its_start() -> None:
    ticker = _ticker("R577-160", "Fahrtbehinderung\nFalschparker", began=50, line="60")
    assert _begins([_fire(), _parker(), ticker]) == {}


def test_the_feed_item_begins_with_the_newer_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    _fetch(monkeypatch, [_fire(), _ticker_then()])
    items = _fetch(monkeypatch, [_parker(), _ticker_now()])
    begins = [item["pubDate"] for item in items if "Falschparker" in item["title"]]
    assert begins and all(begin == datetime.fromisoformat(_at(55)) for begin in begins)


def test_the_newer_start_stays_after_the_newer_incident_closed() -> None:
    assert _begins([_fire(), _ticker_then()]) == {}
    assert list(_begins([_parker(), _ticker_now()]).values()) == [datetime.fromisoformat(_at(55))]
    closed = _parker()
    closed["status"] = "resolved"
    assert list(_begins([closed, _ticker_now()]).values()) == [datetime.fromisoformat(_at(55))]
