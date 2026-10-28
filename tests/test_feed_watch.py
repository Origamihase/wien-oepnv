"""Tests for the Feed-Wächter (``scripts/feed_watch.py``).

The fixtures are cut from real feed versions of 2026-10-09 (commits
3c4183287, 6f353c8a7 and their predecessors) and from the ÖBB cache of the
same day, so the three defects the watcher was built for are replayed with
the field shapes they had.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import pytest

from scripts import feed_watch as fw
from scripts import health_check

NBSP = " "
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_a_good_feed_breaks_no_rule() -> None:
    items = [
        _since("40A: Verkehrsüberlastung", "14:54"),
        _item(
            "18: LCC-Herbstmarathon am 11.10.2026",
            f"Linie 18: Betrieb nur zwischen Burggasse, Stadthalle U und Schlachthausgasse U. [Am{NBSP}So{NBSP}11.10.]",
            pub="Mon, 28 Sep 2026 00:00:00 +0200",
            first_seen="Fri, 09 Oct 2026 00:01:03 +0200",
            starts_at="Sun, 11 Oct 2026 00:00:00 +0200",
        ),
        _item("S80: ÖBB-Ersatzbus", "Ersatzbus zwischen Hütteldorf und Penzing. [Heute]"),
        _item(
            "1A/3A: Schwertgasse",
            f"Haltestellenverlegung. [Ab{NBSP}Mo{NBSP}12.10.{NBSP}bis{NBSP}31.03.2027]",
        ),
        _item("Burggasse 67", f"Die Haltestelle wird verlegt. [Seit{NBSP}heute]"),
        _item("26E: Gleisbauarbeiten", f"Ersatzbus 26E. [Seit{NBSP}02.10.]"),
    ] + _filler(4)
    result = _run(_feed(items))
    assert result.findings == []


def test_line_25_old_ticker_counted_as_new_is_found() -> None:
    """2026-10-09 12:01-20:30: the 07.10. stop ticker under the GUID and first_seen of a 09:36 delay."""
    ticker = _item(
        "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
        "Züge halten Donaufelder Straße 175-177. [Heute]",
        guid="1fada7614a788ccf1a33277fc05d810e42ffa22f80e787d1c7f2415eb1f42b8c",
        pub="Wed, 07 Oct 2026 00:00:18 +0200",
        first_seen="Fri, 09 Oct 2026 09:36:00 +0200",
        starts_at="Wed, 07 Oct 2026 00:00:18 +0200",
    )
    result = _run(_feed([_since("40A: Verkehrsüberlastung", "14:54"), ticker] + _filler(8)))
    assert _rules(result) == {"alte-meldung-als-neu"}
    assert "07.10. 00:00" in result.findings[0].text


def test_an_announcement_seen_before_its_start_is_no_old_message() -> None:
    """The marathon on 11.10. was published 28.09. and first seen 09.10.: on its day it reads [Heute]."""
    marathon = _item(
        "18: LCC-Herbstmarathon am 11.10.2026",
        "Linie 18: Betrieb nur zwischen Burggasse und Schlachthausgasse U. [Heute]",
        pub="Mon, 28 Sep 2026 00:00:00 +0200",
        first_seen="Fri, 09 Oct 2026 00:01:03 +0200",
        starts_at="Sun, 11 Oct 2026 00:00:00 +0200",
    )
    result = _run(_feed([marathon] + _filler(9), built="Sun, 11 Oct 2026 09:00:00 +0200"))
    assert result.findings == []


def _previous(items: Sequence[str], built: str) -> dict[str, Any]:
    """The state a run leaves for the next one."""
    return dict(_run(_feed(items, built=built)).state)


def test_polizeieinsatz_since_1308_at_2030_is_found_and_stays_flagged() -> None:
    """2026-10-09 20:30: "2/71: Polizeieinsatz [Seit 13:08]" for an incident from 20:08."""
    before = [
        _since("42: Falschparker", "19:40"),
        _since("26A: Polizeieinsatz", "19:27"),
        _item(
            "1A/2/2A/3A/4A/71/D: Demonstration am 10.10.2026",
            f"Wegen einer Demonstration kommt es zu Verkehrsmaßnahmen. [Am{NBSP}Sa{NBSP}10.10.]",
        ),
    ] + _filler(7)
    state = _previous(before, "Fri, 09 Oct 2026 20:00:49 +0200")
    at_2030 = [
        _since("10/46: Verkehrsunfall", "20:18"),
        _since("42: Falschparker", "19:40"),
        _since("1/D: Polizeieinsatz", "20:08"),
        _since("2/71: Polizeieinsatz", "13:08", pub="Fri, 09 Oct 2026 13:08:16 +0200"),
    ] + _filler(6)
    result = _run(_feed(at_2030, built="Fri, 09 Oct 2026 20:30:39 +0200"), state=state)
    assert _rules(result) == {"seit-zu-frueh"}
    assert result.findings[0].subject == "2/71: Polizeieinsatz"

    # 21:01: the bundle split into "2 [Seit 13:08]" and "71 [Seit 13:10]".
    at_2101 = [
        _since("2: Polizeieinsatz", "13:08"),
        _since("71: Polizeieinsatz", "13:10"),
    ] + _filler(8)
    later = _run(_feed(at_2101, built="Fri, 09 Oct 2026 21:01:00 +0200"), state=result.state)
    assert [f.subject for f in later.findings] == ["2: Polizeieinsatz", "71: Polizeieinsatz"]


def test_a_regrouped_incident_whose_line_was_on_screen_is_no_finding() -> None:
    """09.10. 17:01: "22A [Seit 13:52]" split off "16A/22A [Seit 13:45]", which stood there before."""
    state = _previous(
        [_since("16A/22A: Verkehrsüberlastung", "13:45")] + _filler(9), "Fri, 09 Oct 2026 16:31:01 +0200"
    )
    result = _run(
        _feed([_since("22A: Verkehrsüberlastung", "13:52")] + _filler(9), built="Fri, 09 Oct 2026 17:01:01 +0200"),
        state=state,
    )
    assert result.findings == []


def test_a_late_incident_on_a_full_screen_is_no_finding() -> None:
    """Incidents come first: with ten incidents on screen a new one could not show."""
    state = _previous(
        [_since(f"{n}: Rettungseinsatz", "19:30") for n in range(1, 11)], "Fri, 09 Oct 2026 20:00:00 +0200"
    )
    result = _run(
        _feed([_since("2/71: Polizeieinsatz", "13:08")] + _filler(9), built="Fri, 09 Oct 2026 20:30:00 +0200"),
        state=state,
    )
    assert result.findings == []


def test_without_a_recent_previous_build_the_since_rule_stays_silent() -> None:
    result = _run(_feed([_since("2/71: Polizeieinsatz", "13:08")] + _filler(9), built="Fri, 09 Oct 2026 20:30:00 +0200"))
    assert result.findings == []


OEBB_GUID = "https://fahrplan.oebb.at/bin/help.exe/dnl?tpl=showmap_external&messageID=4513467"
# The ÖBB cache entry of 2026-10-09 20:30, as ``cache/oebb_*/events.json`` holds it.
OEBB_SOURCE = (
    "Wegen einer Stellwerkstörung am Bahnhof waren zwischen <b>Wien Leopoldau Bahnhst (U)</b> und "
    "<b>Wien Süßenbrunn Bahnhst</b> bis 20:18 Uhr keine Fahrten möglich.<br/>Planen Sie derzeit noch "
    "bis zu 10 Minuten mehr Reisezeit ein.<br/><br/>Wir bitten um Entschuldigung."
)


def test_oebb_text_without_its_second_sentence_is_found() -> None:
    """2026-10-09 20:30: the second sentence did not fit behind the stations in long form."""
    cut = _item(
        "Wien Leopoldau ↔ Wien Süßenbrunn",
        "Wegen einer Stellwerkstörung am Bahnhof waren zwischen Wien Leopoldau Bahnhst (U) und Wien "
        "Süßenbrunn Bahnhst bis 20:18 Uhr keine Fahrten möglich. [Heute]",
        guid=OEBB_GUID,
    )
    result = _run(_feed([cut] + _filler(9)), oebb={OEBB_GUID: OEBB_SOURCE})
    assert _rules(result) == {"oebb-zweiter-satz"}
    assert "Planen Sie derzeit noch" in result.findings[0].text


def test_oebb_text_as_built_since_1993_is_no_finding() -> None:
    kept = _item(
        "Wien Leopoldau ↔ Wien Süßenbrunn",
        "Wegen einer Stellwerkstörung am Bahnhof waren bis 20:18 Uhr keine Fahrten möglich. Planen Sie "
        "derzeit noch bis zu 10 Minuten mehr Reisezeit ein. [Heute]",
        guid=OEBB_GUID,
    )
    assert _run(_feed([kept] + _filler(9)), oebb={OEBB_GUID: OEBB_SOURCE}).findings == []


def test_oebb_second_sentence_that_never_fits_is_no_finding() -> None:
    """A long second sentence may drop (#1993): it would not fit even without the stations."""
    source = OEBB_SOURCE.replace(
        "Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein.",
        "Fahrgäste mit gültigen ÖBB-Tickets können zwischen Wien Leopoldau Bahnhst (U) und Wien "
        "Süßenbrunn Bahnhst die Linien der Wiener Linien und die Regionalbusse ohne Aufpreis benutzen.",
    )
    cut = _item(
        "Wien Leopoldau ↔ Wien Süßenbrunn",
        "Wegen einer Stellwerkstörung am Bahnhof waren zwischen Wien Leopoldau Bahnhst (U) und Wien "
        "Süßenbrunn Bahnhst bis 20:18 Uhr keine Fahrten möglich. [Heute]",
        guid=OEBB_GUID,
    )
    assert _run(_feed([cut] + _filler(9)), oebb={OEBB_GUID: source}).findings == []


def test_duplicates_by_title_and_guid() -> None:
    twin = _item("D: Gleisbauarbeiten", f"Kein Betrieb zwischen Börse und Augasse. [Bis{NBSP}07.11.]")
    other = _item("D: Gleisbauarbeiten", "Züge halten in Schleife, Wipplingerstr 39. [Heute]")
    same_guid = [
        _item("1: Verkehrsunfall", "[Heute]", guid="g1"),
        _item("2: Verkehrsunfall", "[Heute]", guid="g1"),
    ]
    result = _run(_feed([twin, other] + same_guid + _filler(6)))
    texts = [f.text for f in result.findings if f.rule_id == "doppelter-eintrag"]
    assert texts == ["Platz 2 hat denselben Titel wie Platz 1", "Platz 4 hat dieselbe GUID wie Platz 3"]


def test_item_count() -> None:
    assert _rules(_run(_feed(_filler(9)))) == {"anzahl"}
    assert _rules(_run(_feed(_filler(10)))) == set()


@pytest.mark.parametrize(
    "time_line",
    [
        "01.09.2026 – 02.09.2026",  # the form before 2026-10-02
        "Bis 15.11.",  # narrow no-break space before #1971
        "Bis 15.11.",  # plain space: the line may wrap on the TV
        "Morgen",
        "Seit 13:08 Uhr",
    ],
)
def test_unknown_time_line_forms_are_found(time_line: str) -> None:
    item = _item("5: Stromstörung", f"Unregelmäßige Intervalle. [{time_line}]")
    assert _rules(_run(_feed([item] + _filler(9)))) == {"zeitzeile"}


def test_missing_time_line_is_found() -> None:
    item = _item("5: Stromstörung", "Unregelmäßige Intervalle.")
    assert _rules(_run(_feed([item] + _filler(9)))) == {"zeitzeile"}


def test_past_ends_and_future_since() -> None:
    items = [
        _item("25/27: Beschädigte Oberleitung", f"Kein Betrieb. [Am{NBSP}08.10.]"),
        _item("40: Veranstaltung", f"Umleitung. [Ab{NBSP}01.10.{NBSP}bis{NBSP}08.10.]"),
        _item("12A: Betriebsstörung", f"Unregelmäßige Intervalle. [Seit{NBSP}16:30]"),
        _item("13A: Gebrechen", f"Umleitung. Voraussichtlich bis 15:10 Uhr. [Seit{NBSP}14:00]"),
        _item("14A: Gebrechen", f"Umleitung. Voraussichtlich bis 17:10 Uhr. [Seit{NBSP}14:00]"),
    ] + _filler(5)
    result = _run(_feed(items, built="Fri, 09 Oct 2026 16:00:00 +0200"))
    assert [(f.rule_id, f.subject) for f in result.findings] == [
        ("ende-vorbei", "25/27: Beschädigte Oberleitung"),
        ("ende-vorbei", "40: Veranstaltung"),
        ("ende-vorbei", "12A: Betriebsstörung"),
        ("ende-vorbei", "13A: Gebrechen"),
    ]


def test_since_in_the_repeated_hour_is_not_after_the_build() -> None:
    # 25.10.2026: built 02:01 winter time (01:01 UTC), incident since 02:26
    # summer time (00:26 UTC), 35 minutes before. The builder writes "Seit
    # 02:26" (Gesamtprüfung 11.10.2026: the watcher read it as 25 minutes
    # after the build).
    items = [
        _since("U6: Rettungseinsatz", "02:26", pub="Sun, 25 Oct 2026 00:26:00 +0000"),
    ] + _filler(9)
    result = _run(_feed(items, built="Sun, 25 Oct 2026 02:01:00 +0100"))
    assert "ende-vorbei" not in _rules(result)


def test_since_after_the_build_in_the_repeated_hour_is_still_found() -> None:
    # Built 02:10 summer time (00:10 UTC): "Seit 02:40" is no moment yet.
    items = [_since("U6: Rettungseinsatz", "02:40", pub="Sun, 25 Oct 2026 00:05:00 +0000")]
    result = _run(_feed(items + _filler(9), built="Sun, 25 Oct 2026 02:10:00 +0200"))
    assert [(f.rule_id, f.subject) for f in result.findings] == [
        ("ende-vorbei", "U6: Rettungseinsatz")
    ]


def test_an_expected_end_after_midnight_is_not_past() -> None:
    items = [
        _item(
            "13A: Gebrechen",
            f"Umleitung. Voraussichtlich bis 00:30 Uhr. [Seit{NBSP}23:30]",
            pub="Fri, 09 Oct 2026 23:30:00 +0200",
        ),
        _item(
            "14A: Gebrechen",
            f"Umleitung. Voraussichtlich bis 23:40 Uhr. [Seit{NBSP}23:30]",
            pub="Fri, 09 Oct 2026 23:30:00 +0200",
        ),
    ] + _filler(8)
    result = _run(_feed(items, built="Fri, 09 Oct 2026 23:45:00 +0200"))
    assert [(f.rule_id, f.subject) for f in result.findings] == [("ende-vorbei", "14A: Gebrechen")]


def test_issue_requests_do_not_retry_a_post() -> None:
    # A create GitHub carried out but answered with an error would open the
    # issue twice (Gesamtprüfung 11.10.2026).
    source = (REPO_ROOT / "scripts" / "feed_watch.py").read_text(encoding="utf-8")
    assert 'allowed_methods=("GET", "PATCH")' in source


def test_length_and_courtesy() -> None:
    long_body = "Wort " * 40
    items = [
        _item("Märzstraße 49", f"{long_body.strip()}. [Bis{NBSP}30.10.]"),
        _item(
            "Wien Meidling ↔ Wien Liesing",
            "Keine Fahrten möglich. Wir bitten um Entschuldigung. [Heute]",
            guid="oebb-x",
        ),
    ] + _filler(8)
    assert [f.rule_id for f in _run(_feed(items)).findings] == ["textlaenge", "hoeflichkeitssatz"]


def test_stale_feed_is_found() -> None:
    result = _run(_feed(_filler(10)), now=datetime(2026, 10, 9, 16, 0, tzinfo=UTC))
    assert _rules(result) == {"feed-alter"}


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


def test_a_normal_run_matches_its_week() -> None:
    assert fw.check_sources(_sample(), _history(NOW), NOW) == []


def test_source_drift_is_found() -> None:
    findings = fw.check_sources(
        _sample(raw={"wl-stoerungen": 20}, cache={"wl": 0}, discarded={"oebb": 20}), _history(NOW), NOW
    )
    assert {(f.rule_id, f.subject) for f in findings} == {
        ("quelle-einbruch", "wl-stoerungen"),
        ("quelle-leer", "wl"),
        ("verwurf-sprung", "oebb"),
    }


def test_baustellen_may_shrink_to_zero() -> None:
    """Four sites are too few to call an empty cache a fault (open point f)."""
    assert fw.check_sources(_sample(cache={"baustellen": 0}), _history(NOW), NOW) == []


def test_source_rules_wait_for_a_reference() -> None:
    drifted = _sample(raw={"wl-stoerungen": 1}, cache={"wl": 0})
    assert fw.check_sources(drifted, _history(NOW, runs=10), NOW) == []


def test_samples_are_kept_for_seven_days() -> None:
    history = _history(NOW) + [{"t": (NOW - timedelta(days=8)).isoformat(), "raw": {}}]
    feed = fw.parse_feed(_feed(_filler(10), built="Sat, 10 Oct 2026 21:30:00 +0200"))
    result = fw.evaluate(feed, now=NOW, state={"samples": history}, oebb_cache={}, sources=_sample())
    assert len(result.state["samples"]) == 49
    assert result.state["samples"][-1]["t"] == NOW.isoformat()


# --- source without a fresh answer ----------------------------------------------

WL_DOWN = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)  # 16:00 Vienna, WL answered 502 until 23:00


def _ticks(codes: Sequence[Mapping[str, int]], start: datetime, state: object = None) -> list[list[fw.Finding]]:
    """One check per half-hour tick, the state carried as in the Update cycle."""
    out: list[list[fw.Finding]] = []
    for n, tick in enumerate(codes):
        findings, state = fw.check_fresh_answers(tick, None, state, start + timedelta(minutes=30 * n))
        out.append(findings)
    return out


def test_parse_fetch_exit_reads_the_workflow_output() -> None:
    assert fw.parse_fetch_exit("wl=1 oebb=0 baustellen=124") == {"wl": 1, "oebb": 0, "baustellen": 124}
    assert fw.parse_fetch_exit("wl= oebb=x stammstrecke=1") == {}
    assert fw.parse_fetch_exit(None) == {}


def test_wl_outage_of_21_october_speaks_after_two_hours() -> None:
    ok = {"wl": 0, "oebb": 0, "baustellen": 0}
    down = {"wl": 1, "oebb": 0, "baustellen": 0}
    found = _ticks([ok] + [down] * 6 + [ok, ok], WL_DOWN - timedelta(minutes=30))
    assert [bool(f) for f in found] == [False, False, False, False, True, True, True, False, False]
    first = found[4][0]
    assert (first.rule_id, first.subject) == ("quelle-ohne-antwort", "Wiener Linien")
    assert first.text == (
        "seit 2 h 00 min keine brauchbare Antwort (Abruf endet mit Exit 1); "
        "der Feed nutzt die letzte gute Antwort von 21.10. 15:30"
    )


def test_short_outages_and_runs_without_exit_codes_stay_silent() -> None:
    ok = {"wl": 0, "oebb": 0, "baustellen": 0}
    oebb_down = {"wl": 0, "oebb": 1, "baustellen": 0}
    # ÖBB on 14.10.: two failed runs in a row, the longest outage besides 21.10.
    assert not any(_ticks([ok, oebb_down, oebb_down, ok], WL_DOWN))
    # A run whose fetch step gave no exit codes changes nothing.
    _, state = fw.check_fresh_answers(ok, None, None, WL_DOWN)
    findings, kept = fw.check_fresh_answers({}, None, state, WL_DOWN + timedelta(hours=5))
    assert findings == [] and kept == state


def test_a_source_failing_from_the_first_run_counts_from_its_first_failure() -> None:
    down = {"wl": 0, "oebb": 0, "baustellen": 2}
    found = _ticks([down] * 5, WL_DOWN)
    assert [bool(f) for f in found] == [False, False, False, False, True]
    assert found[4][0].text == "seit 2 h 00 min keine brauchbare Antwort (Abruf endet mit Exit 2)"


def test_vao_poll_without_success_for_two_hours_is_found() -> None:
    def poll(attempt: str, success: str) -> fw.PollSample:
        return fw.PollSample(
            last_attempt=datetime.fromisoformat(attempt),
            last_result="error",
            last_error="ValueError",
            last_success=datetime.fromisoformat(success),
        )

    early = poll("2026-10-21T18:31:41+02:00", "2026-10-21T17:01:54+02:00")
    late = poll("2026-10-21T19:01:41+02:00", "2026-10-21T17:01:54+02:00")
    assert fw.check_fresh_answers({}, early, None, datetime.fromisoformat("2026-10-21T18:32:00+02:00"))[0] == []
    findings, _ = fw.check_fresh_answers({}, late, None, datetime.fromisoformat("2026-10-21T19:02:00+02:00"))
    assert [(f.rule_id, f.subject, f.text) for f in findings] == [
        (
            "quelle-ohne-antwort",
            "Stammstrecke (VAO)",
            "letzter erfolgreicher Abruf 21.10. 17:01, vor 2 h 00 min; letzter Versuch 19:01: error ValueError",
        )
    ]


def test_poll_status_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "poll_status.json"
    path.write_text(
        json.dumps(
            {
                "last_attempt": "2026-10-21T20:01:44+02:00",
                "last_error": "ValueError",
                "last_result": "error",
                "last_success": "2026-10-21T17:01:54+02:00",
            }
        ),
        encoding="utf-8",
    )
    sample = fw.load_poll_sample(path)
    assert sample is not None and sample.last_result == "error"
    assert sample.last_success == datetime.fromisoformat("2026-10-21T17:01:54+02:00")
    assert fw.load_poll_sample(tmp_path / "missing.json") is None


def test_evaluate_keeps_the_fetch_state() -> None:
    feed = fw.parse_feed(_feed(_filler(10), built="Wed, 21 Oct 2026 16:00:00 +0200"))
    first = fw.evaluate(feed, now=WL_DOWN, state={}, oebb_cache={}, sources=None, fetch_exit={"wl": 1})
    assert first.state["fetch"]["wl"]["failing_since"] == WL_DOWN.isoformat()
    later = WL_DOWN + timedelta(hours=2)
    second = fw.evaluate(feed, now=later, state=first.state, oebb_cache={}, sources=None, fetch_exit={"wl": 1})
    assert [f.rule_id for f in second.findings if f.rule_id == "quelle-ohne-antwort"] == ["quelle-ohne-antwort"]


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


def test_one_issue_per_rule_opened_once_and_closed_after_two_good_runs() -> None:
    client = FakeClient()
    broken = _filler(9)
    first = _cycle(broken, {}, client, 0)
    second = _cycle(broken, first.state, client, 30)
    assert client.calls == [("issues", None), ("create", "Feed-Wächter: Weniger oder mehr als zehn Einträge")]
    assert second.state["rules"]["anzahl"]["issue"] == 101

    good = _filler(10)
    third = _cycle(good, second.state, client, 31)
    assert ("closed", 101) not in client.calls  # one good run is not enough
    fourth = _cycle(good, third.state, client, 32)
    assert client.calls[-2:] == [("comment", 101), ("closed", 101)]
    assert "anzahl" not in fourth.state["rules"]


def test_an_open_issue_is_reused_and_a_recent_one_reopened() -> None:
    title = "Feed-Wächter: Weniger oder mehr als zehn Einträge"
    client = FakeClient([{"number": 7, "title": title, "state": "open"}])
    result = _cycle(_filler(9), {}, client, 0)
    assert result.state["rules"]["anzahl"]["issue"] == 7
    assert [c for c in client.calls if c[0] == "create"] == []

    closed = (datetime(2026, 10, 9, 10, 0, tzinfo=UTC)).isoformat().replace("+00:00", "Z")
    client = FakeClient([{"number": 8, "title": title, "state": "closed", "closed_at": closed}])
    result = _cycle(_filler(9), {}, client, 0)
    assert result.state["rules"]["anzahl"]["issue"] == 8
    assert ("open", 8) in client.calls


def test_issue_errors_are_retried_next_run() -> None:
    class Broken(FakeClient):
        def issues(self) -> list[dict[str, Any]]:
            raise RuntimeError("GitHub GET /issues: HTTP 403")

    client = Broken()
    result = _cycle(_filler(9), {}, client, 0)
    assert "issue" not in result.state["rules"]["anzahl"]


def test_issues_need_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEED_WATCH_ISSUES", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Origamihase/wien-oepnv")
    assert fw.issue_client_from_env() is None
    monkeypatch.setenv("FEED_WATCH_ISSUES", "1")
    monkeypatch.setenv("GITHUB_API_URL", "https://evil.example/api/v3")
    assert fw.issue_client_from_env() is None
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    assert fw.issue_client_from_env() is not None


# --- the script never blocks the publish ------------------------------------------


@pytest.fixture
def data_file() -> Iterator[Path]:
    path = REPO_ROOT / "data" / f"test_cli_feed_watch_{uuid.uuid4().hex}.json"
    yield path
    path.unlink(missing_ok=True)


def test_main_writes_its_state(data_file: Path, tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    built = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed.write_text(_feed(_filler(9), built=built), encoding="utf-8")
    code = fw.main(
        ["--feed", str(feed), "--state", str(data_file), "--cache-dir", str(tmp_path), "--raw-dir", str(tmp_path), "--no-issues"]
    )
    assert code == 0
    state = json.loads(data_file.read_text(encoding="utf-8"))
    assert [f["rule"] for f in state["findings"]] == ["anzahl"]
    assert "error" not in state


def test_main_survives_a_broken_feed(data_file: Path, tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    feed.write_text("<rss><channel><item>", encoding="utf-8")
    assert fw.main(["--feed", str(feed), "--state", str(data_file), "--no-issues"]) == 0
    assert "ParseError" in json.loads(data_file.read_text(encoding="utf-8"))["error"]


def test_main_survives_a_forbidden_state_path(tmp_path: Path) -> None:
    assert fw.main(["--feed", str(tmp_path / "missing.xml"), "--state", str(tmp_path / "s.json"), "--no-issues"]) == 0


# --- Health check ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "ok", "note", "summary"),
    [
        (None, True, True, "noch kein Stand"),
        ({"updated": "2026-10-10T19:30:00+00:00", "findings": []}, True, False, "OK — alle Regeln halten"),
        (
            {
                "updated": "2026-10-10T19:30:00+00:00",
                "findings": [{"rule": "seit-zu-frueh", "title": "„Seit“-Zeit früher als die Störung", "subject": "2/71", "text": "x"}],
            },
            True,
            True,
            "HINWEIS — 1 Regel(n) verletzt",
        ),
        # A watcher that could not check is a side step that cannot run: red
        # since 2026-10-28 (operator decision, "ein Fehler sollte auffallen").
        ({"updated": "2026-10-10T19:30:00+00:00", "error": "ParseError: x"}, False, False, "FEHLER — Wächter konnte nicht prüfen"),
        ({"updated": "2026-10-09T10:00:00+00:00", "findings": []}, True, True, "kein aktueller Stand"),
    ],
)
def test_health_check_shows_findings_as_a_note_and_a_broken_watcher_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any] | None,
    ok: bool,
    note: bool,
    summary: str,
) -> None:
    path = tmp_path / "feed_watch.json"
    if state is not None:
        path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(health_check, "FEED_WATCH_STATE", path)
    check = health_check.check_feed_watch(datetime(2026, 10, 10, 19, 45, tzinfo=UTC))
    assert check.ok is ok
    assert check.note is note
    assert summary in check.summary
