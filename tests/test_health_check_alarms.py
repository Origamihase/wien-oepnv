"""The paths by which an outage reaches the operator: a red health check.

A red run is what makes GitHub mail the maintainer, so every rule that turns
a check red, and ``main()``'s exit code, needs a test that fails when the rule
breaks. The test-suite audit of 2026-10-28 broke seven rules one at a time:
five left the whole suite green (a failed fetch, exit 1, read as OK; a feed
without a new build for 4 h; a station run older than 8 days or with a failed
sub-script; ``main()`` returning 0 with a red check). Both red health checks
between 14.10. and 28.10.2026 (Wiener Linien 21.10., ÖBB 27.10.) were exit 1.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest

import scripts.health_check as hc

_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(hc, "REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _updater(repo: Path, name: str, exit_code: int, output: str = "") -> str:
    """A stand-in updater that prints *output* and exits with *exit_code*."""
    script = repo / "scripts" / name
    script.write_text(
        f"import sys\nprint({output!r})\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    return name


# --- Quellen: the exit code of the real updater decides ------------------


def test_exit_0_is_green_and_names_the_count(repo: Path) -> None:
    script = _updater(repo, "ok.py", 0, "Updated ÖBB cache with 17 events.")
    check = hc.check_source("ÖBB", script)
    assert check.ok
    assert "17 Datensätze" in check.summary


def test_exit_1_a_failed_fetch_is_red_and_names_the_reason(repo: Path) -> None:
    """The common real case: WL 21.10.2026, ÖBB 27.10.2026."""
    script = _updater(
        repo,
        "wl.py",
        1,
        "2026-10-21 17:31:04,986 ERROR src.providers.wl_fetch: Verbindung zu "
        "https://www.wienerlinien.at/ogd_realtime/trafficInfoList schlug nach 3 "
        "Versuchen endgültig fehl: 502 Server Error: Bad Gateway",
    )
    check = hc.check_source("Wiener Linien", script)
    assert not check.ok
    assert "exit 1" in check.summary
    assert "502 Server Error" in check.detail


def test_exit_2_demo_data_is_red(repo: Path) -> None:
    check = hc.check_source("Baustellen (OGD)", _updater(repo, "bst.py", 2))
    assert not check.ok
    assert "FALLBACK" in check.summary


@pytest.mark.parametrize("exit_code", [4, 124, 137])
def test_any_other_non_zero_exit_is_red(repo: Path, exit_code: int) -> None:
    """124/137 are what ``timeout`` returns for a killed fetcher."""
    check = hc.check_source("ÖBB", _updater(repo, "other.py", exit_code))
    assert not check.ok
    assert f"exit {exit_code}" in check.summary


def test_a_hung_updater_is_red(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = repo / "scripts" / "hang.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(hc, "FETCH_TIMEOUT_S", 1)
    check = hc.check_source("ÖBB", "hang.py")
    assert not check.ok
    assert "Zeitüberschreitung" in check.summary


# --- Feed-Build: a stalled update cycle -----------------------------------


def _feed(repo: Path, built: datetime) -> None:
    (repo / "docs" / "feed.xml").write_text(
        "<rss><channel><lastBuildDate>"
        f"{format_datetime(built)}"
        "</lastBuildDate></channel></rss>",
        encoding="utf-8",
    )


def test_a_fresh_feed_is_green(repo: Path) -> None:
    _feed(repo, _NOW - timedelta(minutes=30))
    assert hc.check_feed_freshness(_NOW).ok


def test_a_feed_just_inside_the_limit_is_green(repo: Path) -> None:
    _feed(repo, _NOW - timedelta(hours=hc.FEED_MAX_AGE_H) + timedelta(minutes=1))
    assert hc.check_feed_freshness(_NOW).ok


def test_a_feed_past_the_limit_is_red(repo: Path) -> None:
    _feed(repo, _NOW - timedelta(hours=hc.FEED_MAX_AGE_H) - timedelta(minutes=1))
    check = hc.check_feed_freshness(_NOW)
    assert not check.ok
    assert "kein neuer Build" in check.summary


@pytest.mark.parametrize(
    "content",
    ["", "<rss><channel></channel></rss>", "<rss><lastBuildDate>gestern</lastBuildDate></rss>"],
    ids=["empty", "no-lastBuildDate", "unparseable"],
)
def test_a_broken_feed_is_red(repo: Path, content: str) -> None:
    (repo / "docs" / "feed.xml").write_text(content, encoding="utf-8")
    assert not hc.check_feed_freshness(_NOW).ok


def test_a_missing_feed_is_red(repo: Path) -> None:
    assert not hc.check_feed_freshness(_NOW).ok


# --- Veröffentlichung: the live copy on GitHub Pages (Architektur-Review 31.10.) ---


def _rss(built: datetime) -> str:
    return (
        "<rss><channel><lastBuildDate>"
        + format_datetime(built)
        + "</lastBuildDate></channel></rss>"
    )


def _both_feeds(repo: Path, built: datetime) -> None:
    for name in hc.PUBLISHED_FEEDS:
        (repo / "docs" / name).write_text(_rss(built), encoding="utf-8")


def _live(monkeypatch: pytest.MonkeyPatch, by_url: dict[str, object]) -> list[str]:
    """Serve *by_url* in place of GitHub Pages; a value that is an exception is raised."""
    seen: list[str] = []

    def fetch(url: str) -> str | None:
        seen.append(url)
        value = by_url[url]
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    monkeypatch.setattr(hc, "_fetch_live_feed", fetch)
    return seen


def _urls() -> dict[str, str]:
    return {name: f"{hc.PAGES_BASE_URL}/{name}" for name in hc.PUBLISHED_FEEDS}


def test_live_feeds_that_match_the_repo_are_green(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _both_feeds(repo, _NOW - timedelta(minutes=20))
    seen = _live(monkeypatch, {u: _rss(_NOW - timedelta(minutes=20)) for u in _urls().values()})
    check = hc.check_published_feeds(_NOW)
    assert check.ok and not check.note
    assert "feed.xml aktuell" in check.summary and "feed.en.xml aktuell" in check.summary
    # Both feeds are fetched from the Pages site, nothing else.
    assert sorted(seen) == sorted(_urls().values())
    assert all(u.startswith("https://") for u in seen)


def test_a_live_copy_a_tick_behind_is_green(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pages deploys a minute or two after the commit; the check runs in between."""
    _both_feeds(repo, _NOW)
    _live(monkeypatch, {u: _rss(_NOW - timedelta(minutes=31)) for u in _urls().values()})
    check = hc.check_published_feeds(_NOW)
    assert check.ok and not check.note
    assert "31m hinter dem Repo" in check.summary


def test_a_live_copy_newer_than_the_checkout_is_green(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick published after this run checked out: the live site is ahead, fine."""
    _both_feeds(repo, _NOW - timedelta(minutes=40))
    _live(monkeypatch, {u: _rss(_NOW) for u in _urls().values()})
    assert hc.check_published_feeds(_NOW).ok


def test_a_live_copy_hours_behind_the_repo_is_red_and_names_both_stands(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case nothing else catches: Pages stopped deploying, every run is green."""
    _both_feeds(repo, _NOW)
    stale = _NOW - timedelta(hours=hc.LIVE_LAG_MAX_H, minutes=1)
    urls = _urls()
    _live(monkeypatch, {urls["feed.xml"]: _rss(stale), urls["feed.en.xml"]: _rss(_NOW)})
    check = hc.check_published_feeds(_NOW)
    assert not check.ok
    assert "GitHub Pages liefert nicht mehr aus" in check.summary
    assert "feed.xml: Live-Stand 4h 1m hinter dem Repo" in check.detail
    # Vienna time with its zone, never a UTC moment labelled as local time.
    assert f"live {hc._fmt_moment(stale)}" in check.detail and f"Repo {hc._fmt_moment(_NOW)}" in check.detail
    assert "08:59 CET" in check.detail and "13:00 CET" in check.detail
    assert "feed.en.xml" not in check.detail


def test_a_live_copy_just_inside_the_limit_is_green(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _both_feeds(repo, _NOW)
    _live(monkeypatch, {u: _rss(_NOW - timedelta(hours=hc.LIVE_LAG_MAX_H) + timedelta(minutes=1)) for u in _urls().values()})
    assert hc.check_published_feeds(_NOW).ok


def test_a_failed_live_fetch_is_a_note_not_red(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One failed request says nothing about duration (alarm line 2026-10-28)."""
    _both_feeds(repo, _NOW)
    urls = _urls()
    _live(monkeypatch, {urls["feed.xml"]: ConnectionError("boom"), urls["feed.en.xml"]: _rss(_NOW)})
    check = hc.check_published_feeds(_NOW)
    assert check.ok and check.note
    assert "feed.xml: Live-Abruf fehlgeschlagen (ConnectionError: boom)" in check.summary
    assert "feed.en.xml aktuell" in check.detail


@pytest.mark.parametrize(
    "content",
    ["", "<rss><channel></channel></rss>", "<rss><lastBuildDate>gestern</lastBuildDate></rss>"],
    ids=["empty", "no-lastBuildDate", "unparseable"],
)
def test_an_unreadable_live_copy_is_a_note(repo: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    _both_feeds(repo, _NOW)
    _live(monkeypatch, {u: content for u in _urls().values()})
    check = hc.check_published_feeds(_NOW)
    assert check.ok and check.note
    assert "Live-Kopie" in check.summary


def test_an_unreadable_repo_copy_is_a_note_here_and_red_in_feed_build(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Feed-Build`` already turns red for it; this check must not double-count."""
    (repo / "docs" / "feed.en.xml").write_text(_rss(_NOW), encoding="utf-8")
    seen = _live(monkeypatch, {u: _rss(_NOW) for u in _urls().values()})
    check = hc.check_published_feeds(_NOW)
    assert check.ok and check.note
    assert "feed.xml: Repo-Kopie nicht lesbar" in check.summary
    assert seen == [_urls()["feed.en.xml"]]
    assert not hc.check_feed_freshness(_NOW).ok


@pytest.mark.parametrize(
    ("explicit", "repository", "expected"),
    [
        ("", "", "https://origamihase.github.io/wien-oepnv"),
        ("", "Origamihase/wien-oepnv", "https://origamihase.github.io/wien-oepnv"),
        ("", "Someone/fork-name", "https://someone.github.io/fork-name"),
        ("https://example.github.io/site/", "Someone/fork-name", "https://example.github.io/site"),
    ],
    ids=["default", "this-repository", "fork", "explicit"],
)
def test_the_pages_address_follows_the_repository(
    monkeypatch: pytest.MonkeyPatch, explicit: str, repository: str, expected: str
) -> None:
    """Like build-feed.yml: a fork checks its own site, never this project's."""
    monkeypatch.setenv("PAGES_BASE_URL", explicit)
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    assert hc._pages_base_url() == expected


def test_a_red_published_check_is_a_lasting_fault_in_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _all_green(monkeypatch)
    red = hc.Check("Veröffentlichung", ok=False, summary="FEHLER — GitHub Pages liefert nicht mehr aus")
    monkeypatch.setattr(hc, "check_published_feeds", lambda _now: red)
    assert hc.main() == 1
    assert "::error title=Health: Veröffentlichung::" in capsys.readouterr().out


# --- Stationsverzeichnis: the weekly station run --------------------------


def _last_run(repo: Path, **fields: object) -> None:
    payload: dict[str, object] = {
        "timestamp": (_NOW - timedelta(days=3)).isoformat(),
        "sub_scripts": [
            {"name": "update_station_directory.py", "exit_code": 0},
            {"name": "update_wl_stations.py", "exit_code": 0},
        ],
        "validation": {"security_issues": 0},
    }
    payload.update(fields)
    (repo / "data" / "stations_last_run.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_clean_recent_station_run_is_green(repo: Path) -> None:
    _last_run(repo)
    assert hc.check_stations(_NOW).ok


def test_a_failed_sub_script_is_red_and_named(repo: Path) -> None:
    _last_run(
        repo,
        sub_scripts=[
            {"name": "update_station_directory.py", "exit_code": 0},
            {"name": "update_wl_stations.py", "exit_code": 1},
        ],
    )
    check = hc.check_stations(_NOW)
    assert not check.ok
    assert "update_wl_stations.py exit 1" in check.summary


def test_a_station_run_past_the_limit_is_red(repo: Path) -> None:
    stale = _NOW - timedelta(hours=hc.STATIONS_MAX_AGE_H) - timedelta(minutes=1)
    _last_run(repo, timestamp=stale.isoformat())
    check = hc.check_stations(_NOW)
    assert not check.ok
    assert "keine Aktualisierung" in check.summary


def test_a_station_run_just_inside_the_limit_is_green(repo: Path) -> None:
    recent = _NOW - timedelta(hours=hc.STATIONS_MAX_AGE_H) + timedelta(minutes=1)
    _last_run(repo, timestamp=recent.isoformat())
    assert hc.check_stations(_NOW).ok


def test_security_issues_in_the_validation_are_red(repo: Path) -> None:
    _last_run(repo, validation={"security_issues": 2})
    assert not hc.check_stations(_NOW).ok


def test_a_missing_run_report_is_red(repo: Path) -> None:
    assert not hc.check_stations(_NOW).ok


# --- main(): a red check must make the run red ----------------------------


def _checks() -> list[str]:
    """Every check function of the module, read from the module itself.

    Until 2026-11-01 the tests below named the checks by hand; the check
    ``Übersetzung`` came to ``main()`` without them, so dropping it went
    unnoticed and the main() tests read the real
    ``data/translation_status.json`` (review of 2026-11-01).
    """
    return sorted(
        name
        for name, value in vars(hc).items()
        if name.startswith("check_")
        and inspect.isfunction(value)
        and value.__module__ == hc.__name__
    )


# Read once at import, before any test replaces a check.
_CHECKS = _checks()
_SIGNATURES = {name: inspect.signature(getattr(hc, name)) for name in _CHECKS}


def _stub(name: str, result: hc.Check) -> Callable[..., hc.Check]:
    """A stand-in that takes only what the real check takes, so a call with
    the wrong arguments in ``main()`` still fails here."""
    signature = _SIGNATURES[name]

    def stub(*args: object, **kwargs: object) -> hc.Check:
        signature.bind(*args, **kwargs)
        return result

    return stub


def test_main_runs_every_check() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(hc.main)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert [name for name in _CHECKS if name not in called] == []
    assert len(_CHECKS) >= 13


def _all_green(monkeypatch: pytest.MonkeyPatch) -> None:
    healthy = hc.Check("OK", ok=True, summary="OK")
    for name in _CHECKS:
        monkeypatch.setattr(hc, name, _stub(name, healthy))
    monkeypatch.setattr(hc, "assess_source", lambda probe, *_a: probe)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def test_main_is_green_when_every_check_is_green(monkeypatch: pytest.MonkeyPatch) -> None:
    _all_green(monkeypatch)
    assert hc.main() == 0


@pytest.mark.parametrize("target", _CHECKS)
def test_main_is_red_when_one_check_is_red(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    _all_green(monkeypatch)
    red = hc.Check("Kaputt", ok=False, summary="FEHLER — kaputt")
    monkeypatch.setattr(hc, target, _stub(target, red))
    assert hc.main() == 1
    out = capsys.readouterr().out
    assert "::error title=Health: Kaputt::FEHLER — kaputt" in out
    assert "ERGEBNIS: " in out and "Kaputt" in out


def test_main_stays_green_for_a_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A note (Feed-Wächter, Stammstrecke direction) never turns the run red."""
    _all_green(monkeypatch)
    note = hc.Check("Hinweis", ok=True, note=True, summary="HINWEIS — nur ein Hinweis")
    monkeypatch.setattr(hc, "check_feed_watch", _stub("check_feed_watch", note))
    assert hc.main() == 0
    assert "::warning title=Health: Hinweis::HINWEIS" in capsys.readouterr().out



# --- Nebenschritte: a side step that cannot run (decision 2026-10-28) -----


def _record(repo: Path, file_name: str, updated: datetime, **steps: dict[str, str]) -> None:
    folder = repo / "data" / "side_steps"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"updated": updated.isoformat(), "steps": steps}
    (folder / file_name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def side_steps(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(hc, "SIDE_STEP_DIR", repo / "data" / "side_steps")
    return repo


def _ok(at: datetime) -> dict[str, str]:
    return {"outcome": "success", "at": at.isoformat(), "last_success": at.isoformat()}


def test_side_steps_all_clean_is_green(side_steps: Path) -> None:
    tick = _NOW - timedelta(minutes=20)
    _record(side_steps, "update-cycle.json", tick, Statistik=_ok(tick), Linienprüfung=_ok(tick))
    _record(side_steps, "update-stations.json", _NOW - timedelta(days=3),
            **{"ÖBB-Stationslinien (HAFAS)": _ok(_NOW - timedelta(days=3))})
    audit = _NOW - timedelta(hours=20)
    _record(side_steps, "ki-waechter.json", audit, **{"KI-Wächter": _ok(audit)})
    _record(side_steps, "seo-guard.json", _NOW - timedelta(hours=20),
            **{"Repository-Metadaten": _ok(_NOW - timedelta(hours=20))})
    check = hc.check_side_steps(_NOW)
    assert check.ok and not check.note
    assert "alle 5 Nebenschritte" in check.summary


def test_a_ki_waechter_that_did_not_run_for_a_day_and_a_half_is_red(side_steps: Path) -> None:
    """The daily audit records its run; a dead token or a stuck trigger must be noticed (2026-10-31)."""
    tick = _NOW - timedelta(minutes=20)
    _record(side_steps, "update-cycle.json", tick, Statistik=_ok(tick))
    _record(side_steps, "update-stations.json", _NOW, **{"ÖBB-Stationslinien (HAFAS)": _ok(_NOW)})
    stale = _NOW - timedelta(hours=37)
    _record(side_steps, "ki-waechter.json", stale, **{"KI-Wächter": _ok(stale)})
    check = hc.check_side_steps(_NOW)
    assert not check.ok
    assert "KI-Wächter: Nebenschritte seit" in check.summary
    failed = {"outcome": "failure", "at": tick.isoformat(), "last_success": stale.isoformat()}
    _record(side_steps, "ki-waechter.json", tick, **{"KI-Wächter": failed})
    check = hc.check_side_steps(_NOW)
    assert not check.ok and "KI-Wächter (KI-Wächter) gescheitert" in check.summary


def test_a_failed_side_step_is_red_and_named(side_steps: Path) -> None:
    tick = _NOW - timedelta(minutes=20)
    failed = {
        "outcome": "failure",
        "at": tick.isoformat(),
        "last_success": (tick - timedelta(minutes=30)).isoformat(),
        "run": "https://github.com/o/r/actions/runs/42",
    }
    _record(side_steps, "update-cycle.json", tick, Statistik=failed, Linienprüfung=_ok(tick))
    _record(side_steps, "update-stations.json", _NOW, **{"ÖBB-Stationslinien (HAFAS)": _ok(_NOW)})
    check = hc.check_side_steps(_NOW)
    assert not check.ok
    assert check.summary == (
        "FEHLER — Statistik (Update-Lauf) gescheitert am 28.10. 12:40, zuletzt sauber am "
        "28.10. 12:10 — https://github.com/o/r/actions/runs/42"
    )


def test_a_side_step_that_never_worked_says_so(side_steps: Path) -> None:
    _record(side_steps, "update-stations.json", _NOW,
            **{"ÖBB-Stationslinien (HAFAS)": {"outcome": "failure", "at": _NOW.isoformat()}})
    check = hc.check_side_steps(_NOW)
    assert not check.ok
    assert "ÖBB-Stationslinien (HAFAS) (Stationslauf) gescheitert" in check.summary
    assert "noch nie sauber" in check.summary


def test_a_stale_record_is_red(side_steps: Path) -> None:
    """The recorder itself could not run: a failure would go unseen again."""
    old = _NOW - timedelta(hours=hc.FEED_MAX_AGE_H) - timedelta(minutes=1)
    _record(side_steps, "update-cycle.json", old, Statistik=_ok(old))
    _record(side_steps, "update-stations.json", _NOW, **{"ÖBB-Stationslinien (HAFAS)": _ok(_NOW)})
    check = hc.check_side_steps(_NOW)
    assert not check.ok
    assert "Update-Lauf: Nebenschritte seit 4h 1m nicht erfasst" in check.summary


def test_an_unreadable_record_is_red(side_steps: Path) -> None:
    folder = side_steps / "data" / "side_steps"
    folder.mkdir(parents=True)
    (folder / "update-cycle.json").write_text("{", encoding="utf-8")
    assert not hc.check_side_steps(_NOW).ok


def test_no_record_yet_is_a_note(side_steps: Path) -> None:
    """Before the first run with the recorder."""
    check = hc.check_side_steps(_NOW)
    assert check.ok and check.note
    assert "noch kein Stand" in check.summary


def test_one_record_missing_is_a_note_beside_a_green_one(side_steps: Path) -> None:
    tick = _NOW - timedelta(minutes=20)
    _record(side_steps, "update-cycle.json", tick, Statistik=_ok(tick))
    check = hc.check_side_steps(_NOW)
    assert check.ok and check.note
    assert "Stationslauf: noch kein Stand" in check.detail


# --- Quellen: three hours without a usable answer (decision 2026-10-28) ---


def _watch_state(repo: Path, monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    state: dict[str, object] = {"updated": (_NOW - timedelta(minutes=5)).isoformat(), "fetch": {}}
    state.update(fields)
    path = repo / "data" / "feed_watch.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(hc, "FEED_WATCH_STATE", path)


_OK_PROBE = hc.Check("Wiener Linien", ok=True, summary="OK — 105 Datensätze geholt")
_BAD_PROBE = hc.Check("Wiener Linien", ok=False, summary="FEHLER — Abruf fehlgeschlagen (exit 1)",
                      detail="502 Server Error: Bad Gateway")


def test_a_source_without_an_answer_for_three_hours_is_red(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WL 21.10.2026: 502 from 16:01 to 23:01; red from 19:01, whatever the probe sees."""
    last_ok = _NOW - timedelta(hours=hc.SOURCE_STALE_H) - timedelta(minutes=1)
    _watch_state(repo, monkeypatch, fetch={"wl": {"last_ok": last_ok.isoformat(), "last_exit": 1}})
    for probe in (_BAD_PROBE, _OK_PROBE):
        check = hc.assess_source(probe, "update_wl_cache.py", _NOW)
        assert not check.ok
        assert check.summary == (
            "FEHLER — seit 3h 1m keine brauchbare Antwort im Update-Lauf (Grenze 3h 0m)"
        )
        assert check.detail.startswith("Stichprobe jetzt: ")


def test_a_failed_probe_inside_three_hours_is_a_note(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blip the next Update run already got past must not send a mail."""
    last_ok = _NOW - timedelta(hours=hc.SOURCE_STALE_H) + timedelta(minutes=1)
    _watch_state(repo, monkeypatch, fetch={"wl": {"last_ok": last_ok.isoformat()}})
    check = hc.assess_source(_BAD_PROBE, "update_wl_cache.py", _NOW)
    assert check.ok and check.note
    assert "Stichprobe gescheitert" in check.summary
    assert "502 Server Error" in check.detail


def test_a_good_probe_and_a_fresh_record_stay_green(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch_state(repo, monkeypatch, fetch={"oebb": {"last_ok": _NOW.isoformat()}})
    probe = hc.Check("ÖBB", ok=True, summary="OK — 9 Datensätze geholt")
    assert hc.assess_source(probe, "update_oebb_cache.py", _NOW) == probe


def test_failing_since_counts_before_the_first_good_answer(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    since = _NOW - timedelta(hours=4)
    _watch_state(repo, monkeypatch, fetch={"baustellen": {"failing_since": since.isoformat(), "last_exit": 1}})
    assert not hc.assess_source(_OK_PROBE, "update_baustellen_cache.py", _NOW).ok


@pytest.mark.parametrize(
    "fields",
    [
        {"error": "ValueError: x"},
        {"updated": (_NOW - timedelta(hours=hc.FEED_MAX_AGE_H, minutes=1)).isoformat()},
        {"fetch": {}},
    ],
    ids=["watcher-error", "stale-state", "no-entry"],
)
def test_without_a_trusted_record_the_probe_decides(
    repo: Path, monkeypatch: pytest.MonkeyPatch, fields: dict[str, object]
) -> None:
    old = (_NOW - timedelta(hours=5)).isoformat()
    _watch_state(repo, monkeypatch, **({"fetch": {"wl": {"last_ok": old}}} | fields))
    assert hc.assess_source(_BAD_PROBE, "update_wl_cache.py", _NOW) == _BAD_PROBE
    assert hc.assess_source(_OK_PROBE, "update_wl_cache.py", _NOW) == _OK_PROBE


def test_main_judges_every_source_by_its_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() passes every probe through :func:`assess_source`."""
    real_assess = hc.assess_source
    _all_green(monkeypatch)
    monkeypatch.setattr(hc, "assess_source", real_assess)
    stale = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    path = repo / "data" / "feed_watch.json"
    path.write_text(json.dumps({"updated": fresh, "fetch": {
        "wl": {"last_ok": stale, "last_exit": 1}, "oebb": {"last_ok": fresh}, "baustellen": {"last_ok": fresh},
    }}), encoding="utf-8")
    monkeypatch.setattr(hc, "FEED_WATCH_STATE", path)
    assert hc.main() == 1
    out = capsys.readouterr().out
    assert "::error title=Health: OK::FEHLER — seit 5h 0m keine brauchbare Antwort" in out
    assert out.count("::error") == 1


def test_the_update_run_starts_the_health_check_once_an_hour() -> None:
    """28.10.2026: no scheduled run from 13:19 to 23:19 Vienna, the outage never turned red."""
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/health-check.yml").read_text(
        encoding="utf-8"
    )
    assert 'workflow_run:\n    workflows: ["Update cycle"]\n    types: [completed]' in workflow
    assert "- cron: '0 */6 * * *'" in workflow  # fallback when the Update run stops
    assert 'if [ "$EVENT" = "workflow_run" ] && [ "$minute" -ge 30 ]; then' in workflow
    assert "needs: gate\n    if: needs.gate.outputs.run == 'true'" in workflow
    cycle = (Path(__file__).resolve().parents[1] / ".github/workflows/update-cycle.yml").read_text(
        encoding="utf-8"
    )
    assert cycle.startswith("name: Update cycle\n")


# --- Werkzeuge: the latest run of a pull-request workflow (Fund B, 31.10.) ---


class _Listing:
    """Runs as the Actions API serves them: by ``created_at``, newest first,
    only the page the query asks for (``per_page``, ``page``). Registered
    under the path without its query, so a smaller page size or a missing
    page parameter changes what the check reads."""

    def __init__(self, runs: list[dict[str, object]]) -> None:
        self.runs = runs

    def answer(self, query: str) -> dict[str, object]:
        asked = parse_qs(query)
        per_page, page = int(asked["per_page"][0]), int(asked.get("page", ["1"])[0])
        ordered = sorted(self.runs, key=lambda run: str(run["created_at"]), reverse=True)
        return {"workflow_runs": ordered[(page - 1) * per_page : page * per_page]}


def _runs(monkeypatch: pytest.MonkeyPatch, by_path: dict[str, object]) -> list[str]:
    seen: list[str] = []

    def api(path: str) -> object:
        seen.append(path)
        base, _, query = path.partition("?")
        value = by_path[path] if path in by_path else by_path[base]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, _Listing):
            return value.answer(query)
        return value

    monkeypatch.setattr(hc, "_github_api_json", api)
    return seen


_REVIEW_RUNS = (
    f"/actions/workflows/claude-code-review.yml/runs?status=completed&per_page={hc.TOOLING_RUNS_PER_PAGE}"
)


def _run(conclusion: str | None, finished: datetime = _NOW - timedelta(minutes=50)) -> dict[str, object]:
    return {
        "workflow_runs": [
            {
                "conclusion": conclusion,
                "updated_at": finished.isoformat(),
                "html_url": "https://github.com/Origamihase/wien-oepnv/actions/runs/1",
            }
        ]
    }


def test_a_green_review_run_is_green(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _runs(monkeypatch, {_REVIEW_RUNS: _run("success")})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and not check.note
    assert "Code-Review zuletzt sauber am 28.10. 12:10" in check.summary
    assert seen == [_REVIEW_RUNS]


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure", None])
def test_a_failed_review_run_is_red_and_links_the_run(
    monkeypatch: pytest.MonkeyPatch, conclusion: str | None
) -> None:
    """An expired CLAUDE_CODE_OAUTH_TOKEN fails every review run (since 31.10.)."""
    _runs(monkeypatch, {_REVIEW_RUNS: _run(conclusion)})
    check = hc.check_tooling_runs(_NOW)
    assert not check.ok
    assert "Code-Review gescheitert am 28.10. 12:10" in check.summary
    assert "actions/runs/1" in check.summary


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral"])
def test_a_cancelled_review_run_is_a_note(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _runs(monkeypatch, {_REVIEW_RUNS: _run(conclusion)})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.note
    assert f"letzter Lauf {conclusion}" in check.summary


def test_the_latest_review_run_is_chosen_by_run_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026: the API gave runs created 08:00-08:59 UTC a created_at one
    hour late (end of US daylight saving time) and listed them first."""
    late = _NOW - timedelta(minutes=50)
    runs: list[dict[str, object] | str] = [
        {"run_number": 520, "id": 11, "conclusion": "failure", "updated_at": late.isoformat(),
         "html_url": "https://github.com/Origamihase/wien-oepnv/actions/runs/11"},
        "kein Lauf",
        {"run_number": 521, "id": 12, "conclusion": "success", "updated_at": late.isoformat(),
         "html_url": "https://github.com/Origamihase/wien-oepnv/actions/runs/12"},
    ]
    _runs(monkeypatch, {_REVIEW_RUNS: {"workflow_runs": runs}})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and not check.note, check.summary
    first, third = runs[0], runs[2]
    assert isinstance(first, dict) and isinstance(third, dict)
    first["conclusion"], third["conclusion"] = "success", "failure"
    check = hc.check_tooling_runs(_NOW)
    assert not check.ok and "actions/runs/12" in check.summary


def _review(number: int, conclusion: str) -> dict[str, object]:
    return {"run_number": number, "id": number, "conclusion": conclusion,
            "updated_at": (_NOW - timedelta(minutes=50)).isoformat(),
            "html_url": f"https://github.com/Origamihase/wien-oepnv/actions/runs/{number}"}


@pytest.mark.parametrize("before", ["failure", "success"])
def test_skipped_dependabot_runs_do_not_hide_the_last_review(
    monkeypatch: pytest.MonkeyPatch, before: str
) -> None:
    """01.11.2026: Dependabot rebased nine pull requests in a row (#508-#516),
    and the Code-Review skips them (no secrets); the review before them counts."""
    skipped = [_review(number, "skipped") for number in range(516, 507, -1)]
    _runs(monkeypatch, {_REVIEW_RUNS: {"workflow_runs": [*skipped, _review(507, before)]}})
    check = hc.check_tooling_runs(_NOW)
    if before == "failure":
        assert not check.ok and "actions/runs/507" in check.summary, check.summary
    else:
        assert check.ok and not check.note, check.summary


def test_a_window_of_only_skipped_runs_is_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [_review(number, "skipped") for number in range(hc.TOOLING_RUNS_PER_PAGE, 0, -1)]
    _runs(monkeypatch, {_REVIEW_RUNS: {"workflow_runs": runs}})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.note and "letzter Lauf skipped" in check.summary


def test_dependabot_bursts_fit_into_the_tooling_window() -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / ".github/dependabot.yml").read_text(encoding="utf-8"))
    open_limit = sum(int(update.get("open-pull-requests-limit", 5)) for update in config["updates"])
    assert hc.TOOLING_RUNS_PER_PAGE > open_limit


def test_the_review_runs_read_reach_past_the_runs_listed_an_hour_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """01.11.2026, 09:25 UTC: the API listed #521, #520 and #519 (created
    08:26-08:34 UTC, shown 09:26-09:34 UTC) before #522 (09:20 UTC). Reading
    only the first listed run picks #521."""
    def review(number: int, created: datetime, conclusion: str) -> dict[str, object]:
        return {"run_number": number, "id": number, "conclusion": conclusion,
                "created_at": created.isoformat(), "updated_at": created.isoformat(),
                "html_url": f"https://github.com/Origamihase/wien-oepnv/actions/runs/{number}"}

    runs = [review(522, _NOW - timedelta(minutes=5), "failure")] + [
        review(number, _NOW + timedelta(minutes=minutes), "success")
        for number, minutes in ((521, 9), (520, 5), (519, 1))
    ]
    _runs(monkeypatch, {"/actions/workflows/claude-code-review.yml/runs": _Listing(runs)})
    check = hc.check_tooling_runs(_NOW)
    assert not check.ok and check.summary.endswith("/actions/runs/522"), check.summary

    runs[0]["conclusion"], runs[1]["conclusion"] = "success", "failure"
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and not check.note, check.summary


def test_an_unreachable_actions_api_is_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_REVIEW_RUNS: RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.note
    assert "Actions-API nicht abgefragt (RuntimeError: GITHUB_TOKEN" in check.summary


def test_a_workflow_that_never_ran_is_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_REVIEW_RUNS: {"workflow_runs": []}})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.note
    assert "noch kein abgeschlossener Lauf" in check.summary


def test_the_api_call_is_read_only_with_the_run_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Origamihase/wien-oepnv")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    calls: list[tuple[object, ...]] = []

    class _Session:
        headers: dict[str, str] = {}

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def fetch(session: object, url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs.get("allowed_content_types"), kwargs.get("max_bytes")))
        return b'{"workflow_runs": []}'

    monkeypatch.setattr(hc, "session_with_retries", lambda *_a, **_kw: _Session())
    monkeypatch.setattr(hc, "fetch_content_safe", fetch)
    assert hc._github_api_json(_REVIEW_RUNS) == {"workflow_runs": []}
    assert calls == [
        (
            "https://api.github.com/repos/Origamihase/wien-oepnv" + _REVIEW_RUNS,
            ("application/json",),
            hc.GITHUB_API_MAX_BYTES,
        )
    ]
    assert _Session.headers["Authorization"] == "Bearer t0ken"


def test_without_a_token_the_api_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Origamihase/wien-oepnv")
    monkeypatch.setattr(hc, "fetch_content_safe", lambda *_a, **_kw: pytest.fail("no request expected"))
    with pytest.raises(RuntimeError):
        hc._github_api_json(_REVIEW_RUNS)


def test_the_review_workflow_fails_when_the_review_cannot_run() -> None:
    """Fund B of the Architektur-Review (31.10.): no continue-on-error on the
    review step any more, the Health check holds ``actions: read`` and the
    token to read the run's conclusion, fork PRs (no secrets) are skipped."""
    repo_root = Path(__file__).resolve().parents[1]
    review = (repo_root / ".github" / "workflows" / "claude-code-review.yml").read_text(encoding="utf-8")
    step = review.split("id: claude-review", 1)[1].split("- name:", 1)[0]
    code = "\n".join(line for line in step.splitlines() if not line.strip().startswith("#"))
    assert "continue-on-error" not in code
    assert "github.event.pull_request.head.repo.full_name == github.repository" in review
    health = (repo_root / ".github" / "workflows" / "health-check.yml").read_text(encoding="utf-8")
    assert "actions: read" in health
    assert "GITHUB_TOKEN: ${{ github.token }}" in health
    assert {name for name, _label in hc.TOOLING_WORKFLOWS} == {"claude-code-review.yml"}


# --- Pages-Build: the latest run of "pages build and deployment" (01.11.) ---

_WORKFLOWS = "/actions/workflows?per_page=100"


def _pages_page(page: int) -> str:
    return f"/actions/workflows/189040121/runs?status=completed&per_page={hc.PAGES_RUNS_PER_PAGE}&page={page}"


_PAGES_RUNS = _pages_page(1)
_PAGES_WORKFLOW = {
    "workflows": [
        {"id": 189039912, "path": ".github/workflows/build-feed.yml"},
        {"id": 189040121, "path": "dynamic/pages/pages-build-deployment"},
    ]
}


def _pages_run(
    run_id: int, conclusion: str, minutes_ago: int, message: str = "chore: update cycle"
) -> dict[str, object]:
    started = _NOW - timedelta(minutes=minutes_ago)
    return {
        "id": run_id,
        "run_number": run_id,
        "conclusion": conclusion,
        "created_at": started.isoformat(),
        "updated_at": (started + timedelta(minutes=1)).isoformat(),
        "html_url": f"https://github.com/Origamihase/wien-oepnv/actions/runs/{run_id}",
        "head_commit": {"message": message},
    }


def _jobs(failed_step: str | None) -> dict[str, object]:
    """Jobs of a Pages run as the Actions API lists them (01.11.2026): the
    steps after a failed one are ``skipped`` (run 41285931114)."""
    build_steps = ["Set up job", "Pull ghcr.io/actions/jekyll-build-pages:v1.0.13", "Checkout",
                   "Build with Jekyll", "Upload artifact"]
    deploy_steps = ["Set up job", "Deploy to GitHub Pages"]

    def steps(names: list[str]) -> list[dict[str, str]]:
        failed = names.index(failed_step) if failed_step in names else len(names)
        return [
            {"name": n, "conclusion": "success" if i < failed else "failure" if i == failed else "skipped"}
            for i, n in enumerate(names)
        ]

    return {
        "jobs": [
            {"id": 7001, "name": "build", "steps": steps(build_steps)},
            {"id": 7002, "name": "deploy", "steps": steps(deploy_steps)},
        ]
    }


_BUILD_ANNOTATIONS = "/check-runs/7001/annotations?per_page=50"


def _annotation(tail: str) -> list[dict[str, object]]:
    """The error annotation of a failed Jekyll step: the build's output on
    one line, as actions/jekyll-build-pages 1.0.13 writes it."""
    return [
        {"annotation_level": "warning", "message": "Node.js 20 is deprecated."},
        {"annotation_level": "failure",
         "message": " Logging at level: debug GitHub Pages: github-pages v232 ... Layout source: theme "
                    "GitHub Metadata: Calling @client.repository(\"Origamihase/wien-oepnv\", "
                    "{:accept=>\"application/vnd.github.drax-preview+json\"}) github-pages 232 | " + tail},
    ]


def _pages(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, object]],
           jobs: dict[int, object] | None = None, *more_pages: object) -> list[str]:
    # The build job's annotations name no error of GitHub's API.
    by_path: dict[str, object] = {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": runs},
                                  _BUILD_ANNOTATIONS: []}
    for page, value in enumerate(more_pages, start=2):
        by_path[_pages_page(page)] = value
    for run_id, value in (jobs or {}).items():
        by_path[f"/actions/runs/{run_id}/jobs?per_page=20"] = value
    return _runs(monkeypatch, by_path)


def _pages_annotated(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, object]],
                     jobs: dict[int, object], annotations: object) -> list[str]:
    """Like :func:`_pages`, and the build job's annotations answer *annotations*."""
    by_path: dict[str, object] = {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": runs},
                                  _BUILD_ANNOTATIONS: annotations}
    for run_id, value in jobs.items():
        by_path[f"/actions/runs/{run_id}/jobs?per_page=20"] = value
    return _runs(monkeypatch, by_path)


def test_a_green_pages_run_is_green_and_cancelled_runs_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub cancels a run a newer commit supersedes (165 of 1,972 from 01.10. to 01.11.2026)."""
    seen = _pages(monkeypatch, [_pages_run(3, "cancelled", 5), _pages_run(2, "success", 30)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note
    assert check.summary == "OK — letzter Lauf sauber am 28.10. 12:31"
    assert seen == [_WORKFLOWS, _PAGES_RUNS]


def test_a_failed_jekyll_build_is_red_at_once_and_names_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026: from the merge of #2092 every build failed in Jekyll."""
    runs = [
        _pages_run(5, "failure", 20),
        _pages_run(4, "cancelled", 25),
        _pages_run(3, "failure", 50, "Merge pull request #2092 from Origamihase/x\n\nKI-Wächter"),
        _pages_run(2, "success", 80),
    ]
    _pages(monkeypatch, runs, {5: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary == (
        "FEHLER — Jekyll-Build gescheitert am 28.10. 12:41, 2 Läufe in Folge seit 28.10. 12:10 "
        "(Inhalt unter docs/ prüfen); GitHub Pages liefert den alten Stand aus"
    )
    assert check.detail == (
        "zuerst nach Commit „Merge pull request #2092 from Origamihase/x“ — "
        "https://github.com/Origamihase/wien-oepnv/actions/runs/5"
    )


def test_one_failed_jekyll_build_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
           {3: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Jekyll-Build gescheitert am 28.10. 12:41 (Inhalt unter docs/ prüfen)" in check.summary
    assert check.detail.startswith("Commit „chore: update cycle“ — ")


@pytest.mark.parametrize(
    "failed_step", ["Deploy to GitHub Pages", "Pull ghcr.io/actions/jekyll-build-pages:v1.0.13", None]
)
def test_one_failure_on_githubs_side_is_a_note(monkeypatch: pytest.MonkeyPatch, failed_step: str | None) -> None:
    """October 2026: four deploy failures at GitHub, each gone with the next
    run; on 05.10. the deploy job was cancelled without a failed step."""
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)], {3: _jobs(failed_step)})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert check.summary == (
        "HINWEIS — Auslieferung durch GitHub gescheitert am 28.10. 12:41; der nächste Lauf liefert neu aus"
    )


@pytest.mark.parametrize(
    "tail",
    [
        # Pages run #23181, 16.07.2026 22:31 UTC (and #23182, #23183): the next run built.
        "Error: GET https://api.github.com/repos/Origamihase/wien-oepnv: 503 - <!DOCTYPE html>",
        # Pages run #20334, 23.05.2026 10:01 UTC: the next run, five minutes later, built.
        "Error: The GitHub API credentials you provided aren't valid. ",
        # jekyll-github-metadata 2.16.1 asks for /pages first; Octokit::BadGateway.
        "Error:  GET https://api.github.com/repos/Origamihase/wien-oepnv/pages: 502 - Server Error",
    ],
)
def test_one_jekyll_failure_on_githubs_api_is_a_note(monkeypatch: pytest.MonkeyPatch, tail: str) -> None:
    seen = _pages_annotated(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
                            {3: _jobs("Build with Jekyll")}, _annotation(tail))
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note, check.summary
    assert check.summary == (
        "HINWEIS — Jekyll-Build an GitHubs API gescheitert am 28.10. 12:41; der nächste Lauf baut neu"
    )
    assert _BUILD_ANNOTATIONS in seen


def test_two_jekyll_failures_on_githubs_api_in_a_row_are_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.07.2026: three runs in a row failed on a 503 of the API (22:31-23:31 UTC)."""
    runs = [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50), _pages_run(2, "success", 80)]
    _pages_annotated(monkeypatch, runs, {4: _jobs("Build with Jekyll")}, _annotation(
        "Error: GET https://api.github.com/repos/Origamihase/wien-oepnv: 503 - <!DOCTYPE html>"))
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary == (
        "FEHLER — Jekyll-Build an GitHubs API gescheitert am 28.10. 12:41, 2 Läufe in Folge seit "
        "28.10. 12:10; GitHub Pages liefert den alten Stand aus"
    )


@pytest.mark.parametrize(
    "annotations",
    [
        # 01.11.2026: the output was cut at 4096 characters before the error.
        [{"annotation_level": "failure", "message": " Logging at level: debug ... Rendering: reference/trip.md"}],
        _annotation("Error:  Liquid syntax error (line 1): Unknown tag 'endraw'"),
        # A warning that quotes the API is not the step's error.
        [{"annotation_level": "warning",
          "message": "github-pages 232 | Error: GET https://api.github.com/repos/x/y: 503 - x"}],
        RuntimeError("HTTP 403 Resource not accessible by integration"),
        {"message": "Not Found"},
    ],
)
def test_a_jekyll_failure_not_shown_to_be_githubs_api_stays_red(
    monkeypatch: pytest.MonkeyPatch, annotations: object
) -> None:
    _pages_annotated(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
                     {3: _jobs("Build with Jekyll")}, annotations)
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Jekyll-Build gescheitert am 28.10. 12:41 (Inhalt unter docs/ prüfen)" in check.summary


def test_the_health_check_may_read_the_annotations() -> None:
    health = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "health-check.yml").read_text(
        encoding="utf-8"
    )
    assert "checks: read" in health


def test_two_failures_on_githubs_side_in_a_row_are_red(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50), _pages_run(2, "success", 80)]
    _pages(monkeypatch, runs, {4: _jobs("Deploy to GitHub Pages")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Auslieferung durch GitHub gescheitert am 28.10. 12:41, 2 Läufe in Folge seit 28.10. 12:10" in check.summary


@pytest.mark.parametrize("jobs", [RuntimeError("HTTP 502"), {"jobs": []}, {}])
def test_a_failed_run_whose_jobs_cannot_be_read_is_red(monkeypatch: pytest.MonkeyPatch, jobs: object) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20), _pages_run(2, "success", 50)], {3: jobs})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "Pages-Lauf gescheitert am 28.10. 12:41" in check.summary


@pytest.mark.parametrize("conclusion", ["timed_out", "startup_failure", "action_required"])
def test_every_other_conclusion_counts_as_failed(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _pages(monkeypatch, [_pages_run(3, conclusion, 20)], {3: _jobs("Build with Jekyll")})
    assert not hc.check_pages_build(_NOW).ok


def _failed_page(page: int) -> dict[str, object]:
    first = (page - 1) * hc.PAGES_RUNS_PER_PAGE
    return {"workflow_runs": [_pages_run(1000 - i, "failure", 10 + 5 * i)
                              for i in range(first, first + hc.PAGES_RUNS_PER_PAGE)]}


def test_while_every_run_read_failed_the_next_page_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026, 10:02 UTC: the 20 runs before reached back only to 08:30
    UTC (merges came in), so the start of the streak (#2092, 07:43 UTC) lay
    on the next page."""
    page1 = _failed_page(1)["workflow_runs"]
    assert isinstance(page1, list)
    page2 = [_pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    seen = _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f", {hc.PAGES_RUNS_PER_PAGE + 1} Läufe in Folge seit 28.10. 10:30 (Inhalt" in check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert seen == [_WORKFLOWS, _PAGES_RUNS, _pages_page(2), "/actions/runs/1000/jobs?per_page=20",
                    _BUILD_ANNOTATIONS]


def test_a_streak_longer_than_the_runs_read_says_at_least(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_failed_page(page) for page in range(1, hc.PAGES_MAX_PAGES + 1)]
    first = pages[0]["workflow_runs"]
    assert isinstance(first, list)
    seen = _pages(monkeypatch, first, {1000: _jobs("Build with Jekyll")}, *pages[1:])
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    total = hc.PAGES_RUNS_PER_PAGE * hc.PAGES_MAX_PAGES
    assert f"mindestens {total} Läufe in Folge (Inhalt" in check.summary
    assert check.detail == "https://github.com/Origamihase/wien-oepnv/actions/runs/1000"
    assert _pages_page(hc.PAGES_MAX_PAGES + 1) not in seen


def test_a_success_listed_ahead_of_newer_runs_does_not_end_the_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """01.11.2026: the API listed #29084 (created 08:56 UTC) as 09:56 UTC,
    ahead of the runs of 09:00-09:55 UTC. Had it succeeded and the build
    broken with the next run, page 1 would have held that success and page 2
    the first failed run: the streak counted from page 1 alone was one short
    and named the wrong commit."""
    page1 = [_pages_run(n, "failure", 10 + 5 * (120 - n)) for n in range(120, 101, -1)]
    page1.insert(10, _pages_run(100, "success", 55))
    assert len(page1) == hc.PAGES_RUNS_PER_PAGE
    page2 = [_pages_run(101, "failure", 120, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(99, "success", 130)]
    seen = _pages(monkeypatch, page1, {120: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert ", 20 Läufe in Folge seit 28.10. 11:00 (Inhalt" in check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert _pages_page(2) in seen


def test_cancelled_runs_take_places_on_a_page_and_the_next_page_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """01.11.2026, from 09:53 UTC: 17 failed and 3 cancelled runs filled
    page 1; the start of the streak lay on page 2."""
    page1 = [_pages_run(1000 - i, "cancelled" if i in (3, 8, 9) else "failure", 10 + 5 * i)
             for i in range(hc.PAGES_RUNS_PER_PAGE)]
    page2 = [_pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    seen = _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok and ", 18 Läufe in Folge seit" in check.summary, check.summary
    assert check.detail.startswith("zuerst nach Commit „Merge pull request #2092 from Origamihase/x“")
    assert _pages_page(2) in seen


def test_a_run_listed_on_two_pages_counts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that finishes between two page requests moves every run down by
    one, so the last run of page 1 comes back first on page 2."""
    page1 = _failed_page(1)["workflow_runs"]
    assert isinstance(page1, list)
    page2 = [page1[-1], _pages_run(980, "failure", 150, "Merge pull request #2092 from Origamihase/x"),
             _pages_run(979, "success", 160)]
    _pages(monkeypatch, page1, {1000: _jobs("Build with Jekyll")}, {"workflow_runs": page2})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f", {hc.PAGES_RUNS_PER_PAGE + 1} Läufe in Folge seit 28.10. 10:30 (Inhalt" in check.summary


def test_a_later_page_that_does_not_answer_keeps_what_was_read(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _failed_page(1)["workflow_runs"]
    assert isinstance(first, list)
    _pages(monkeypatch, first, {1000: _jobs("Build with Jekyll")}, RuntimeError("HTTP 502"))
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert f"mindestens {hc.PAGES_RUNS_PER_PAGE} Läufe in Folge (Inhalt" in check.summary


def test_a_good_run_on_a_full_first_page_ends_the_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each further page is some 260 KB and one more call per check."""
    runs = [_pages_run(1000, "failure", 10)] + [_pages_run(999 - i, "success", 40 + 30 * i) for i in range(30)]
    seen = _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, "/actions/workflows/189040121/runs": _Listing(runs),
                               "/actions/runs/1000/jobs?per_page=20": _jobs("Build with Jekyll"),
                               _BUILD_ANNOTATIONS: []})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert seen == [_WORKFLOWS, _PAGES_RUNS, "/actions/runs/1000/jobs?per_page=20", _BUILD_ANNOTATIONS]


def test_a_first_page_that_does_not_answer_is_a_note_that_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: RuntimeError("HTTP 502")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert check.summary == "HINWEIS — Actions-API nicht abgefragt (RuntimeError: HTTP 502)"


def test_a_page_that_is_not_full_ends_the_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """A streak that began with the first run of all: no further page."""
    seen = _pages(monkeypatch, [_pages_run(4, "failure", 20), _pages_run(3, "failure", 50)],
                  {4: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "mindestens 2 Läufe in Folge" in check.summary
    assert _pages_page(2) not in seen


def test_the_latest_pages_run_is_chosen_by_run_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026: the API listed #29084 (created 08:56 UTC, shown as 09:56
    UTC) before #29099 (09:55 UTC). By the API's order a fix after a broken
    build stayed red, and a break after a good run stayed green."""
    shifted = _pages_run(29084, "failure", 10)
    newer = _pages_run(29099, "success", 15)
    older = _pages_run(29083, "success", 70)
    _pages(monkeypatch, [shifted, newer, older], {29084: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note, check.summary

    shifted["conclusion"], newer["conclusion"] = "success", "failure"
    _pages(monkeypatch, [shifted, newer, older], {29099: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.detail.endswith("/actions/runs/29099")


def test_a_run_time_after_the_check_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """01.11.2026, 09:01 UTC: run #29084 had ended at 08:57 UTC, the API
    said 09:57 UTC."""
    late = _pages_run(4, "failure", -50)
    _pages(monkeypatch, [late, _pages_run(3, "failure", 20), _pages_run(2, "success", 50)],
           {4: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert check.summary.startswith("FEHLER — Jekyll-Build gescheitert, 2 Läufe in Folge seit 28.10. 12:40 (")

    _runs(monkeypatch, {_REVIEW_RUNS: _run("success", _NOW + timedelta(minutes=60))})
    check = hc.check_tooling_runs(_NOW)
    assert check.ok and check.summary == "OK — Code-Review zuletzt sauber"

    # A run that ended while the check ran keeps its time.
    _runs(monkeypatch, {_REVIEW_RUNS: _run("success", _NOW + timedelta(minutes=3))})
    check = hc.check_tooling_runs(_NOW)
    assert check.summary == "OK — Code-Review zuletzt sauber am 28.10. 13:03"

    # A streak whose first run the API dates after the check: no start time.
    _pages(monkeypatch, [_pages_run(5, "failure", -50), _pages_run(4, "failure", -40), _pages_run(3, "success", 80)],
           {5: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert check.summary.startswith("FEHLER — Jekyll-Build gescheitert, 2 Läufe in Folge (Inhalt"), check.summary


def test_one_failed_jekyll_build_with_nothing_before_names_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(monkeypatch, [_pages_run(3, "failure", 20)], {3: _jobs("Build with Jekyll")})
    check = hc.check_pages_build(_NOW)
    assert not check.ok
    assert "in Folge" not in check.summary
    assert check.detail.startswith("Commit „chore: update cycle“ — ")


def test_a_github_failure_after_a_good_run_is_a_note_whatever_came_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streak ends at the last good run: an older failure does not count."""
    runs = [_pages_run(5, "failure", 20), _pages_run(4, "success", 50), _pages_run(3, "failure", 80)]
    _pages(monkeypatch, runs, {5: _jobs("Deploy to GitHub Pages")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note, check.summary


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral"])
def test_runs_github_did_not_finish_are_skipped(monkeypatch: pytest.MonkeyPatch, conclusion: str) -> None:
    _pages(monkeypatch, [_pages_run(3, conclusion, 5), _pages_run(2, "success", 30)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and not check.note, check.summary


def test_malformed_answers_are_a_note_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: {"workflows": [{"id": "189040121", "path": hc.PAGES_WORKFLOW_PATH}]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein Workflow" in check.summary

    _runs(monkeypatch, {_WORKFLOWS: _PAGES_WORKFLOW, _PAGES_RUNS: {"workflow_runs": ["kein Lauf", None]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein abgeschlossener Pages-Lauf" in check.summary


def test_the_pages_of_runs_fit_the_api_cap() -> None:
    """A run is about 13 to 15 KB of JSON as the check reads it (20 Pages
    runs were 257,496 bytes and 30 review runs 405,393 bytes on 01.11.2026;
    GitHub sends more, pretty-printed, to the default User-Agents of curl and
    wget); an answer
    over GITHUB_API_MAX_BYTES would leave the check a note for good."""
    assert hc.PAGES_RUNS_PER_PAGE * 16_000 < hc.GITHUB_API_MAX_BYTES
    assert hc.TOOLING_RUNS_PER_PAGE * 16_000 < hc.GITHUB_API_MAX_BYTES


def test_pages_notes_when_nothing_can_be_said(monkeypatch: pytest.MonkeyPatch) -> None:
    _runs(monkeypatch, {_WORKFLOWS: RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "Actions-API nicht abgefragt (RuntimeError: GITHUB_TOKEN" in check.summary

    _runs(monkeypatch, {_WORKFLOWS: {"workflows": [{"id": 1, "path": ".github/workflows/test.yml"}]}})
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein Workflow dynamic/pages/pages-build-deployment gefunden" in check.summary

    _pages(monkeypatch, [_pages_run(3, "cancelled", 20)])
    check = hc.check_pages_build(_NOW)
    assert check.ok and check.note
    assert "kein abgeschlossener Pages-Lauf" in check.summary
