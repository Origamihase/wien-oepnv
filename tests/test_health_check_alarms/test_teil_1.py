"""Teil 1 der Tests aus tests/test_health_check_alarms.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.health_check as hc

from ._support import (
    _BAD_PROBE,
    _CHECKS,
    _Listing,
    _NOW,
    _OK_PROBE,
    _REVIEW_RUNS,
    _all_green,
    _both_feeds,
    _feed,
    _last_run,
    _live,
    _ok,
    _record,
    _review,
    _rss,
    _run,
    _runs,
    _stub,
    _updater,
    _urls,
    _watch_state,
)


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


def test_main_runs_every_check() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(hc.main)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert [name for name in _CHECKS if name not in called] == []
    assert len(_CHECKS) >= 13


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
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/health-check.yml").read_text(
        encoding="utf-8"
    )
    assert 'workflow_run:\n    workflows: ["Update cycle"]\n    types: [completed]' in workflow
    assert "- cron: '0 */6 * * *'" in workflow  # fallback when the Update run stops
    assert 'if [ "$EVENT" = "workflow_run" ] && [ "$minute" -ge 30 ]; then' in workflow
    assert "needs: gate\n    if: needs.gate.outputs.run == 'true'" in workflow
    cycle = (Path(__file__).resolve().parents[2] / ".github/workflows/update-cycle.yml").read_text(
        encoding="utf-8"
    )
    assert cycle.startswith("name: Update cycle\n")


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

    root = Path(__file__).resolve().parents[2]
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
