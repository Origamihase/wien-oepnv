"""The dashboard reads one small document, from its own origin.

Audit E.3: ``docs/assets/site.js`` used to fetch the three raw yearly
ledgers straight from ``raw.githubusercontent.com`` on every page load —
``stammstrecke_2026.csv`` (430 KB), ``stoerungen_2026.csv`` (187 KB) and
``ausfaelle_2026.csv`` (87 KB), ~705 KB together — and roll them up in
the browser to render a handful of KPI tiles and bar charts. Two
problems, one growth and one origin:

* the ledgers are *yearly* files, so a December visitor downloads the
  January rows too (~1.3 MB extrapolated for the Stammstrecke alone);
* ``raw.githubusercontent.com`` is not a delivery CDN, has its own rate
  limits, and sits outside the GitHub Pages availability promise. When
  it throttled, the page stayed up and every chart failed.

The roll-up already happened once per tick in
``scripts/generate_markdown_stats.py`` for the Markdown dashboard.
Emitting the same numbers as ``docs/stats-summary.json`` costs nothing
extra and replaces ~705 KB with ~3 KB that stays flat all year.

These tests pin the three halves that make that safe: the generator
emits the document on *every* tick, the shape it emits is the shape the
page indexes, and the page no longer reaches for the foreign origin.
They read the shipped assets from disk rather than executing them, so
no JS runtime is required (same approach as
``tests/test_dashboard_delay_threshold.py``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.generate_markdown_stats import (
    SUMMARY_FILENAME,
    SUMMARY_SCHEMA_VERSION,
    StammstreckeRow,
    _direction_coverage,
    _live_window_delay,
    aggregate_ausfaelle,
    aggregate_stammstrecke,
    aggregate_stoerungen,
    build_stats_summary,
    main,
    resolve_summary_path,
    write_stats_summary,
)
from src.utils.stats import STAMMSTRECKE_DIRECTIONS

VIENNA_TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=VIENNA_TZ)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _REPO_ROOT / "docs"
_SITE_JS = _DOCS / "assets" / "site.js"
_SITE_HTML = _DOCS / "site.html"
_SUMMARY = _DOCS / SUMMARY_FILENAME


def _site_js() -> str:
    return _SITE_JS.read_text(encoding="utf-8")


def _rows(
    *offsets_and_directions: tuple[timedelta, str, float],
) -> list[StammstreckeRow]:
    out: list[StammstreckeRow] = []
    for offset, direction, delay in offsets_and_directions:
        stamp = NOW - offset
        out.append(
            StammstreckeRow(
                timestamp=stamp,
                weekday=stamp.strftime("%a")[:2],
                hour=stamp.hour,
                direction=direction,
                delay_minutes=delay,
            )
        )
    return out


def _summary_from(rows: list[StammstreckeRow]) -> dict[str, object]:
    return build_stats_summary(
        year=2026,
        generated_at=NOW,
        stammstrecke_rows=rows,
        stammstrecke=aggregate_stammstrecke(rows),
        stoerungen=aggregate_stoerungen([]),
        ausfaelle=aggregate_ausfaelle([]),
        window_rows=rows,
        all_window_rows=rows,
        window_days=30,
    )


def _section(summary: dict[str, object], name: str) -> dict[str, object]:
    """Narrow one section of the summary for ``mypy --strict``."""
    value = summary[name]
    assert isinstance(value, dict), f"{name} must be an object"
    return value


# ----- the generator emits on every tick ------------------------------


def test_summary_is_written_even_when_the_dashboard_is_skipped(
    tmp_path: Path,
) -> None:
    """The decisive one.

    ``update-cycle.yml`` passes ``--skip-dashboard`` on every tick except
    the one inside the 00:00 Europe/Vienna hour, so a summary emitted
    only in the dashboard branch would be up to 24 hours stale on a page
    that refreshes every five minutes. The aggregation deliberately sits
    *outside* that branch.
    """
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir()
    (stats_dir / "stammstrecke_2026.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2026-09-14T11:30:00+02:00,Mo,11,Meidling,1.5\n",
        encoding="utf-8",
    )
    out = tmp_path / "docs" / "statistik.md"
    # ``--summary-path`` is passed explicitly rather than relying on the
    # default: if the resolver ever regresses, this test must still not
    # be able to write over the published ``docs/stats-summary.json``.
    # The defaulting itself is covered directly by
    # ``test_summary_path_follows_the_markdown_output``.
    summary_path = tmp_path / "docs" / SUMMARY_FILENAME
    exit_code = main(
        [
            "--year", "2026",
            "--stats-dir", str(stats_dir),
            "--output", str(out),
            "--summary-path", str(summary_path),
            "--skip-readme",
            "--skip-dashboard",
            "--now-iso", NOW.isoformat(),
        ]
    )

    assert exit_code == 0
    assert summary_path.exists(), (
        "no summary on a --skip-dashboard tick — the page would serve "
        "numbers up to 24 hours old"
    )
    assert not out.exists(), "--skip-dashboard must still skip the Markdown"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SUMMARY_SCHEMA_VERSION
    assert payload["stammstrecke"]["total_observations"] == 1


def test_summary_path_follows_the_markdown_output(tmp_path: Path) -> None:
    """A redirected ``--output`` must take the summary with it.

    Otherwise any caller that points the dashboard at a temporary
    directory — every test does — silently overwrites the published
    ``docs/stats-summary.json``. The same class of mistake once stamped
    placeholder blocks into the committed README.
    """
    markdown = tmp_path / "sub" / "statistik.md"
    assert resolve_summary_path(None, markdown_output=markdown) == (
        tmp_path / "sub" / SUMMARY_FILENAME
    )


def test_explicit_summary_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "elsewhere.json"
    assert (
        resolve_summary_path(explicit, markdown_output=tmp_path / "statistik.md")
        == explicit
    )


# ----- the numbers the page needs -------------------------------------


def test_live_window_reports_no_sample_as_none_not_zero() -> None:
    """An empty window is "n/a", never "0.0 min".

    Zero minutes of delay is a *statement about the service*; the
    absence of a sample is not. Collapsing the two would have the tile
    claim punctuality during an outage.
    """
    stale = _rows((timedelta(hours=3), "Meidling", 4.0))
    assert _live_window_delay(stale, now=NOW) == (None, 0)


def test_live_window_averages_only_the_trailing_hour() -> None:
    rows = _rows(
        (timedelta(minutes=10), "Meidling", 2.0),
        (timedelta(minutes=50), "Meidling", 4.0),
        (timedelta(minutes=90), "Meidling", 99.0),  # outside the window
    )
    average, count = _live_window_delay(rows, now=NOW)
    assert (average, count) == (3.0, 2)


def test_coverage_ships_evidence_not_a_verdict() -> None:
    """The banner's rule stays in one place — the page.

    The generator reports rows-in-window and last-seen per direction;
    ``site.js`` decides. Shipping a boolean instead would fork the rule
    across two languages, and the northbound restart has to clear the
    banner by itself.
    """
    rows = _rows(
        (timedelta(days=1), "Meidling", 1.0),
        (timedelta(days=40), "Praterstern", 2.0),
    )
    coverage = _direction_coverage(
        [r for r in rows if r.direction == "Meidling"],
        all_rows=rows,
        window_days=30,
    )
    directions = coverage["directions"]
    assert isinstance(directions, dict)
    assert directions["Meidling"]["rows_in_window"] == 1
    assert directions["Praterstern"]["rows_in_window"] == 0
    assert directions["Praterstern"]["last_seen"] is not None, (
        "the banner names the date a silent direction was last seen"
    )


def test_every_canonical_direction_appears_even_with_no_rows() -> None:
    coverage = _direction_coverage([], all_rows=[], window_days=30)
    directions = coverage["directions"]
    assert isinstance(directions, dict)
    assert set(directions) >= set(STAMMSTRECKE_DIRECTIONS)


def test_hour_keys_are_zero_padded_strings() -> None:
    """``site.js`` indexes the hour maps with ``HOURS`` = "00".."23".

    An integer key, or "9" instead of "09", would make every bar read
    zero — silently, because a missing bucket legitimately means "no
    observations".
    """
    # The fixture deliberately lands on a SINGLE-digit hour: that is the
    # only case where ``str(hour)`` and ``f"{hour:02d}"`` differ, so a
    # midday sample would let the bug through.
    summary = _summary_from(_rows((timedelta(hours=4), "Meidling", 1.0)))
    hours = _section(summary, "stammstrecke")["by_hour_avg"]
    assert isinstance(hours, dict)
    assert list(hours) == ["08"], (
        f"expected a zero-padded single-digit hour key, got {sorted(hours)}"
    )
    assert all(re.fullmatch(r"\d{2}", key) for key in hours), sorted(hours)

    js_hours = re.search(
        r"const HOURS = ([^;]+);", _site_js()
    )
    assert js_hours is not None, "site.js must declare HOURS"
    assert "padStart(2" in js_hours.group(1), (
        "site.js builds HOURS zero-padded; the summary keys must match"
    )


def test_floats_are_rounded_so_the_file_does_not_churn(tmp_path: Path) -> None:
    """The file is committed every 30 minutes.

    Full binary precision would rewrite it on every tick for digits the
    dashboard never renders (it shows one decimal), turning the commit
    log into noise.
    """
    rows = _rows(
        (timedelta(minutes=5), "Meidling", 1.0),
        (timedelta(minutes=6), "Meidling", 2.0),
        (timedelta(minutes=7), "Meidling", 2.0),
    )
    out = tmp_path / SUMMARY_FILENAME
    write_stats_summary(_summary_from(rows), output_path=out)
    text = out.read_text(encoding="utf-8")
    assert "1.6666" not in text, "floats reached the file unrounded"
    payload = json.loads(text)
    assert payload["stammstrecke"]["avg_delay_minutes"] == 1.67


def test_non_finite_values_become_null_not_nan(tmp_path: Path) -> None:
    """``NaN``/``Infinity`` are not JSON, and the browser would take the
    whole dashboard down over one of them.

    ``JSON.parse`` throws on a bare ``NaN`` literal (RFC 8259 §6 has no
    such token), so a single non-finite average would not degrade one
    tile — it would fail the fetch and drop every panel into the error
    line. They are mapped to ``null``, which renders through the same
    "no data" path an empty window already uses.
    """
    out = tmp_path / SUMMARY_FILENAME
    write_stats_summary(
        {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "stammstrecke": {
                "avg_delay_minutes": float("nan"),
                "max_delay_minutes": float("inf"),
            },
        },
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text, text
    payload = json.loads(text)  # would raise on a non-standard literal
    assert payload["stammstrecke"]["avg_delay_minutes"] is None
    assert payload["stammstrecke"]["max_delay_minutes"] is None


def test_bidi_control_characters_are_stripped(tmp_path: Path) -> None:
    """Provider and line labels come from upstream feeds.

    The file is committed to ``main`` and served from Pages, so a
    right-to-left override in a provider name would reorder what a
    reader sees in the diff, the GitHub UI and the rendered page. Same
    ingestion-boundary scrub as ``src/places/merge.py:write_stations``.
    """
    out = tmp_path / SUMMARY_FILENAME
    write_stats_summary(
        {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "stoerungen": {"by_provider": {"Wiener\u202elinien": 3}},
        },
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "\u202e" not in text and "\u202E" not in text
    payload = json.loads(text)
    assert list(payload["stoerungen"]["by_provider"]) == ["Wienerlinien"]
    # Legitimate German content must survive the scrub untouched.
    assert "Meidling" in json.dumps(
        json.loads(_SUMMARY.read_text(encoding="utf-8")), ensure_ascii=False
    )


def test_summary_carries_every_field_the_page_reads() -> None:
    """A field contract, checked against the shipped ``site.js``.

    Renaming a key on either side is a silent break: the page would
    render zeros rather than fail, because a missing bucket is a
    legitimate zero.
    """
    summary = _summary_from(_rows((timedelta(minutes=5), "Meidling", 1.0)))
    js = _site_js()

    for path, accessor in (
        (("stammstrecke", "total_observations"), "stats.total_observations"),
        (("stammstrecke", "avg_delay_minutes"), "stats.avg_delay_minutes"),
        (("stammstrecke", "max_delay_minutes"), "stats.max_delay_minutes"),
        (("stammstrecke", "threshold_exceedances"), "stats.threshold_exceedances"),
        (("stammstrecke", "by_weekday_avg"), "stats.by_weekday_avg"),
        (("stammstrecke", "by_hour_avg"), "stats.by_hour_avg"),
        (("stammstrecke", "by_direction"), "stats.by_direction"),
        (("stammstrecke", "live"), "stats.live"),
        (("stammstrecke", "coverage"), "stats.coverage"),
        (("stoerungen", "total"), "stats.total"),
        (("stoerungen", "by_provider"), "stats.by_provider"),
        (("stoerungen", "by_weekday"), "stats.by_weekday"),
        (("stoerungen", "by_hour"), "stats.by_hour"),
        (("ausfaelle", "by_line"), "stats.by_line"),
    ):
        section, key = path
        assert key in _section(summary, section), f"summary lost {section}.{key}"
        assert accessor in js, f"site.js no longer reads {accessor}"

    assert "live.avg_delay_minutes" in js
    assert "entry.rows_in_window" in js
    assert "entry.last_seen" in js


# ----- the page stopped talking to the foreign origin -----------------


def test_site_js_no_longer_fetches_from_raw_githubusercontent() -> None:
    # Line comments are stripped first: the constant block deliberately
    # explains *why* the host is gone, and that prose is not a fetch.
    code_only = re.sub(r"//.*", "", _site_js())
    assert "raw.githubusercontent.com" not in code_only, (
        "the statistics panels must read the same-origin summary"
    )


def test_summary_url_is_same_origin() -> None:
    match = re.search(r'const SUMMARY_URL = "([^"]+)";', _site_js())
    assert match is not None, "site.js must declare SUMMARY_URL"
    url = match.group(1)
    assert "//" not in url and not url.startswith("/"), (
        f"SUMMARY_URL must stay a same-origin relative path, got {url!r}"
    )
    assert url == SUMMARY_FILENAME


def test_csp_no_longer_allows_the_foreign_origin() -> None:
    """The real security win: one fewer origin the page may talk to.

    ``connect-src`` is what actually permits the fetch; leaving the host
    listed after the last caller is gone keeps an exfiltration channel
    open for no reason.
    """
    html = _SITE_HTML.read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp is not None, "site.html must carry a CSP meta tag"
    connect = re.search(r"connect-src ([^;]+);", csp.group(1))
    assert connect is not None, "the CSP must declare connect-src"
    assert "raw.githubusercontent.com" not in connect.group(1), (
        "raw.githubusercontent.com is still reachable from the page"
    )
    assert "'self'" in connect.group(1)


def test_page_and_generator_agree_on_the_schema_version() -> None:
    match = re.search(r"const SUMMARY_SCHEMA_VERSION = (\d+);", _site_js())
    assert match is not None, "site.js must declare SUMMARY_SCHEMA_VERSION"
    assert int(match.group(1)) == SUMMARY_SCHEMA_VERSION, (
        "a version bump on one side only makes the page refuse every "
        "document the generator writes"
    )


def test_page_refuses_an_unknown_schema_version() -> None:
    js = _site_js()
    assert "data.schema_version !== SUMMARY_SCHEMA_VERSION" in js, (
        "without the guard a future shape renders as a dashboard of "
        "zeros instead of an error"
    )


# ----- what is actually committed -------------------------------------


def test_committed_summary_is_readable_and_current(
) -> None:
    """The file ships with the site.

    Without it the dashboard is empty from the moment this merges until
    the first tick regenerates it.
    """
    assert _SUMMARY.exists(), f"docs/{SUMMARY_FILENAME} must be committed"
    payload = json.loads(_SUMMARY.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SUMMARY_SCHEMA_VERSION
    for section in ("stammstrecke", "stoerungen", "ausfaelle"):
        assert section in payload


@pytest.mark.parametrize("section", ["stammstrecke", "stoerungen", "ausfaelle"])
def test_committed_summary_is_far_smaller_than_the_ledgers(section: str) -> None:
    """The point of the exercise, measured rather than asserted.

    Each raw ledger alone dwarfs the whole summary; together they are the
    ~705 KB this change removes from every page load.
    """
    ledger = _REPO_ROOT / "data" / "stats" / f"{section}_2026.csv"
    if not ledger.exists():  # pragma: no cover - fresh clone without stats
        pytest.skip(f"{ledger.name} not present")
    assert _SUMMARY.stat().st_size < ledger.stat().st_size
