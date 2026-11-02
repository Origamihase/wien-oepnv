"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_feed_watch.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import pytest

from scripts import feed_watch as fw

NBSP = " "

REPO_ROOT = Path(__file__).resolve().parents[2]


def _item(
    title: str,
    description: str,
    *,
    guid: str | None = None,
    pub: str = "Fri, 09 Oct 2026 10:00:00 +0200",
    first_seen: str | None = None,
    starts_at: str | None = None,
) -> str:
    parts = [
        "<item>",
        f"<title><![CDATA[{title}]]></title>",
        "<link>https://www.wienerlinien.at/ogd_realtime</link>",
        f'<guid isPermaLink="false">{escape(guid or uuid.uuid4().hex)}</guid>',
        f"<pubDate>{pub}</pubDate>",
        f"<ext:first_seen>{first_seen or pub}</ext:first_seen>",
    ]
    if starts_at:
        parts.append(f"<ext:starts_at>{starts_at}</ext:starts_at>")
    parts += [f"<description>{description}</description>", "</item>"]
    return "".join(parts)


def _feed(items: Sequence[str], built: str = "Fri, 09 Oct 2026 16:00:00 +0200") -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<rss xmlns:ext="https://wien-oepnv.example/schema" version="2.0"><channel>'
        f"<title>ÖPNV Störungen Wien &amp; Pendler</title><lastBuildDate>{built}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>"
    )


def _filler(count: int, start: int = 0) -> list[str]:
    """Planned notices as the feed shows them below the incidents."""
    return [
        _item(
            f"{start + n}A: Haltestelle {start + n}",
            f"Haltestellenverlegung der Linie {start + n}A. Grund: Bauarbeiten. [Bis{NBSP}31.03.2027]",
            pub="Wed, 07 Oct 2026 00:00:00 +0200",
            starts_at="Wed, 07 Oct 2026 00:00:00 +0200",
        )
        for n in range(1, count + 1)
    ]


def _since(title: str, clock: str, pub: str | None = None) -> str:
    return _item(
        title,
        f"Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen. [Seit{NBSP}{clock}]",
        pub=pub or f"Fri, 09 Oct 2026 {clock}:00 +0200",
    )


def _run(
    feed_xml: str,
    *,
    state: Mapping[str, Any] | None = None,
    oebb: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> fw.Result:
    feed = fw.parse_feed(feed_xml)
    assert feed.built is not None
    return fw.evaluate(
        feed,
        now=now or feed.built + timedelta(minutes=2),
        state=state or {},
        oebb_cache=oebb or {},
        sources=None,
    )


def _rules(result: fw.Result) -> set[str]:
    return {finding.rule_id for finding in result.findings}


def _previous(items: Sequence[str], built: str) -> dict[str, Any]:
    """The state a run leaves for the next one."""
    return dict(_run(_feed(items, built=built)).state)


OEBB_GUID = "https://fahrplan.oebb.at/bin/help.exe/dnl?tpl=showmap_external&messageID=4513467"

# The ÖBB cache entry of 2026-10-09 20:30, as ``cache/oebb_*/events.json`` holds it.
OEBB_SOURCE = (
    "Wegen einer Stellwerkstörung am Bahnhof waren zwischen <b>Wien Leopoldau Bahnhst (U)</b> und "
    "<b>Wien Süßenbrunn Bahnhst</b> bis 20:18 Uhr keine Fahrten möglich.<br/>Planen Sie derzeit noch "
    "bis zu 10 Minuten mehr Reisezeit ein.<br/><br/>Wir bitten um Entschuldigung."
)


# --- sources ---------------------------------------------------------------------


def _history(now: datetime, runs: int = 48) -> list[dict[str, Any]]:
    """A quiet week: the counts of 2026-10-10."""
    return [
        {
            "t": (now - timedelta(minutes=30 * (n + 1))).isoformat(),
            "raw": {
                "wl-stoerungen": 60 + n % 5,
                "wl-hinweise": 90,
                "oebb": 165,
                "baustellen-linien": 53,
                "baustellen-punkte": 23,
            },
            "discarded": {"wl": 17, "oebb": 140, "baustellen": 69},
            "cache": {"wl": 100, "oebb": 10, "baustellen": 4},
        }
        for n in range(runs)
    ]


def _sample(**changes: Any) -> fw.SourceSample:
    sample = fw.SourceSample(
        raw={"wl-stoerungen": 62, "wl-hinweise": 90, "oebb": 165, "baustellen-linien": 53, "baustellen-punkte": 23},
        discarded={"wl": 17, "oebb": 140, "baustellen": 69},
        cache={"wl": 100, "oebb": 10, "baustellen": 4},
    )
    for section, values in changes.items():
        getattr(sample, section).update(values)
    return sample


NOW = datetime(2026, 10, 10, 19, 30, tzinfo=UTC)


# --- source without a fresh answer ----------------------------------------------

WL_DOWN = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)  # 16:00 Vienna, WL answered 502 until 23:00


def _ticks(codes: Sequence[Mapping[str, int]], start: datetime, state: object = None) -> list[list[fw.Finding]]:
    """One check per half-hour tick, the state carried as in the Update cycle."""
    out: list[list[fw.Finding]] = []
    for n, tick in enumerate(codes):
        findings, state = fw.check_fresh_answers(tick, None, state, start + timedelta(minutes=30 * n))
        out.append(findings)
    return out


# --- issues ----------------------------------------------------------------------


class FakeClient:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.calls: list[tuple[str, Any]] = []
        self.next_number = 100

    def issues(self) -> list[dict[str, Any]]:
        self.calls.append(("issues", None))
        return self.existing

    def create(self, title: str, body: str) -> int:
        self.next_number += 1
        self.calls.append(("create", title))
        return self.next_number

    def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", number))

    def set_state(self, number: int, state: str) -> None:
        self.calls.append((state, number))


def _cycle(feed_items: Sequence[str], state: Mapping[str, Any], client: FakeClient, minute: int) -> fw.Result:
    built = datetime(2026, 10, 9, 14, minute, tzinfo=UTC)
    result = _run(_feed(feed_items, built=built.strftime("%a, %d %b %Y %H:%M:%S +0000")), state=state)
    fw.sync_issues(client, result, built)  # type: ignore[arg-type]
    return result


# --- the script never blocks the publish ------------------------------------------


@pytest.fixture
def data_file() -> Iterator[Path]:
    path = REPO_ROOT / "data" / f"test_cli_feed_watch_{uuid.uuid4().hex}.json"
    yield path
    path.unlink(missing_ok=True)


__all__ = [
    "FakeClient",
    "NBSP",
    "NOW",
    "OEBB_GUID",
    "OEBB_SOURCE",
    "REPO_ROOT",
    "WL_DOWN",
    "_cycle",
    "_feed",
    "_filler",
    "_history",
    "_item",
    "_previous",
    "_rules",
    "_run",
    "_sample",
    "_since",
    "_ticks",
    "data_file",
]
