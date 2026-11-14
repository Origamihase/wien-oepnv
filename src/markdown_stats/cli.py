"""The statistics dashboard run: order of its steps and its exit code.

``main`` of ``scripts/generate_markdown_stats.py`` and the two helpers that
end it (the note of a failed write, the requests for a missing review).
Moved verbatim from the script (line limit, 2026-11-13); the script
re-exports ``main`` and stays the entry point. The options are in
:mod:`src.markdown_stats.cli_options`. A test that replaces a name ``main``
calls (``load_poll_status``, ``write_dashboard``, ``patch_readme_stats`` ...)
replaces it in this module.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

from src.feed.logging_safe import setup_script_logging
from src.feed.stammstrecke import FEED_WINDOW
from src.markdown_stats.cli_options import build_parser
from src.markdown_stats.direction_coverage import render_direction_coverage_note
from src.markdown_stats.markdown_report import render_markdown, write_dashboard
from src.markdown_stats.readme import (
    patch_readme_stats,
    render_readme_ausfaelle_block,
    render_readme_ausfaelle_live_block,
    render_readme_stammstrecke_block,
    render_readme_stammstrecke_live_block,
)
from src.markdown_stats.rows import (
    LOGGER,
    VIENNA_TZ,
    AusfallRow,
    StammstreckeRow,
    _filter_rows_by_timedelta,
    _filter_rows_by_window,
    aggregate_ausfaelle,
    aggregate_stammstrecke,
    aggregate_stoerungen,
    collect_year_data,
    earlier_rows_until_every_direction,
)
from src.markdown_stats.summary import (
    _live_fetch_covers,
    _poll_evidence,
    build_stats_summary,
    resolve_summary_path,
    write_stats_summary,
)
from src.markdown_stats.year_archive import (
    archived_years,
    dashboard_nav,
    overdue_reviews_reported,
    no_request_note,
    start_run,
    write_year_pages,
)
from src.utils.logging import sanitize_log_arg
from src.utils.stammstrecke_poll import (
    assess_poll,
    load_poll_status,
)
from src.utils.stats import STAMMSTRECKE_DIRECTIONS

__all__ = ["_finish", "_no_request_note", "main"]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns ``0`` on success (incl. the empty-data case), ``1`` on a
    fatal error (write failure, an invalid ``--readme-window-days`` or
    ``--now-iso``), and ``1`` after everything else was written when the
    page of a finished year could not be rendered or written, or, with
    ``--fail-on-missing-review``, while a finished year's review of the
    roadworks is overdue (:func:`_finish`). Parsing errors of
    individual CSV rows are tolerated and never propagate to the exit
    code — the dashboard is regenerated from whatever can be parsed.

    A failed write of the summary, the dashboard or the README ends the
    run at once with ``1``, before the requests for a missing review: what
    the website and the README show is the bigger alarm, and the review
    waits for a later dashboard run; the ERROR line says so
    (:func:`src.markdown_stats.year_archive.no_request_note`).
    """
    setup_script_logging(logging.INFO)
    start_run()  # each finding once per run, also when a process runs main twice

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.readme_window_days < 1:
        LOGGER.error(
            "--readme-window-days muss >= 1 sein, war %d.",
            args.readme_window_days,
        )
        return 1

    if args.now_iso is not None:
        try:
            now = datetime.fromisoformat(args.now_iso)
        except ValueError:
            LOGGER.error(
                "--now-iso konnte nicht geparst werden: %s",
                sanitize_log_arg(args.now_iso),
            )
            return 1
        if now.tzinfo is None:
            # Naive --now-iso input is interpreted as Europe/Vienna (the
            # project's canonical zone — every CSV writer normalises to
            # it via :func:`src.utils.stats.to_vienna`).
            now = now.replace(tzinfo=VIENNA_TZ)
        else:
            # Convert to the named ZoneInfo so ``strftime('%Z')`` renders
            # the friendly abbreviation ("CEST" / "CET") rather than the
            # raw offset ("UTC+02:00") that ``datetime.fromisoformat``
            # would otherwise carry through.
            now = now.astimezone(VIENNA_TZ)
    else:
        now = datetime.now(VIENNA_TZ)
    # Resolved after ``now``, not as an argparse default evaluated at parse
    # time: a run replayed with ``--now-iso 2027-01-01T00:15`` must cover
    # 2027, not the year of the machine clock.
    if args.year is None:
        args.year = now.year

    sm_rows, st_rows, au_rows = collect_year_data(
        args.year, stats_dir=args.stats_dir
    )
    LOGGER.info(
        "Stats geladen: %d Stammstrecke-Zeilen, %d Störungs-Zeilen, "
        "%d Ausfall-Zeilen aus %s.",
        len(sm_rows),
        len(st_rows),
        len(au_rows),
        sanitize_log_arg(str(args.stats_dir)),
    )

    # Load the cross-year Stammstrecke window for the README snapshot.
    # The nightly workflow runs at 00:15 Europe/Vienna, so a 30-day
    # cutoff in early January legitimately spans the previous calendar
    # year. ``collect_year_data`` returns empty lists for missing files,
    # so eagerly loading both years is safe even mid-year.
    cutoff = now - timedelta(days=args.readme_window_days)
    extra_years = sorted({cutoff.year, now.year} - {args.year})
    window_sm: list[StammstreckeRow] = list(sm_rows)
    window_au: list[AusfallRow] = list(au_rows)
    for extra_year in extra_years:
        extra_sm, _, extra_au = collect_year_data(
            extra_year, stats_dir=args.stats_dir
        )
        window_sm.extend(extra_sm)
        window_au.extend(extra_au)
    # From 31.01. the window no longer touches the previous year. A direction
    # that last reported there would count as never measured, and the caveat
    # below would end while the direction is still silent
    # (``earlier_rows_until_every_direction``); ``sm_window`` stays the
    # 30-day window.
    window_sm.extend(
        earlier_rows_until_every_direction(
            window_sm,
            before_year=min(cutoff.year, now.year, args.year),
            directions=STAMMSTRECKE_DIRECTIONS,
            stats_dir=args.stats_dir,
        )
    )
    sm_window = _filter_rows_by_window(
        window_sm, days=args.readme_window_days, now=now
    )
    au_window = _filter_rows_by_window(
        window_au, days=args.readme_window_days, now=now
    )
    # Audit B.1: the coverage caveat is derived from the delay ledger (the
    # cancellation ledger only carries rows when trains are actually cancelled,
    # so it cannot tell "no cancellations" apart from "direction not measured").
    # Computed here — before the dashboard branch — so the same wording feeds
    # docs/statistik.md and both 30-day README blocks. The annual dashboard
    # aggregate still contains the pre-outage rows, so absence-from-aggregate
    # alone would never fire there; the recent window is what reveals it.
    # Operator decision 2026-10-10: a dark corridor is named only while the
    # monitor's fetch works; a broken fetch is the health check's (red).
    poll_status = load_poll_status()
    fetch_working = assess_poll(poll_status, now=now).working
    coverage_note = render_direction_coverage_note(
        sm_window, now=now, all_rows=window_sm, fetch_working=fetch_working
    )
    if coverage_note:
        LOGGER.warning(
            "Eingeschränkte Richtungs-Abdeckung im %d-Tage-Fenster — "
            "veröffentlichte Kennzahlen decken nicht den gesamten Korridor ab.",
            args.readme_window_days,
        )

    # Audit E.3: the aggregation moved OUT of the ``--skip-dashboard``
    # branch. The website reads these numbers from
    # ``docs/stats-summary.json`` on every page load and must not be up
    # to 24 hours stale, so the roll-up now runs on every tick — ~14 000
    # rows, microseconds, against ~705 KB the browser no longer has to
    # download. Only the Markdown *render* stays behind the flag (that
    # is what churns the ``_Automatisch erzeugt am ..._`` timestamp
    # through the commit log every 30 minutes).
    sm_agg = aggregate_stammstrecke(sm_rows)
    st_agg = aggregate_stoerungen(st_rows)
    au_agg = aggregate_ausfaelle(au_rows)

    # The pages of finished years first, without a request to Gemini: the
    # summary and the dashboard name them. A failed page costs the exit code
    # only; the requests come last (_finish, review of 2026-11-10).
    pages_ok = True
    if not args.skip_dashboard:
        pages_ok = write_year_pages(
            now=now, year=args.year, stats_dir=args.stats_dir, output_path=args.output, ask=False
        )

    summary_path = resolve_summary_path(
        args.summary_path, markdown_output=args.output
    )
    try:
        write_stats_summary(
            build_stats_summary(
                year=args.year,
                generated_at=now,
                stammstrecke_rows=sm_rows,
                stammstrecke=sm_agg,
                stoerungen=st_agg,
                ausfaelle=au_agg,
                window_rows=sm_window,
                all_window_rows=window_sm,
                window_days=args.readme_window_days,
                poll=_poll_evidence(poll_status),
                # The website's year switch (docs/site.html, "Aktuelle
                # Meldungen") offers the years that have a page.
                archive_years=archived_years(args.output, before=args.year),
            ),
            output_path=summary_path,
        )
    except OSError as exc:
        # Same posture as the dashboard write below: the published page
        # renders from this file, so a failed write is a broken tick,
        # not a cosmetic miss.
        LOGGER.error(
            "Konnte Statistik-Summary nicht schreiben (%s): %s%s",
            sanitize_log_arg(str(summary_path)),
            sanitize_log_arg(str(exc)),
            _no_request_note(args, now),
        )
        return 1

    LOGGER.info(
        "Statistik-Summary geschrieben: %s.",
        sanitize_log_arg(str(summary_path)),
    )

    if args.skip_dashboard:
        # ``update-cycle.yml`` passes this flag on every 30-min tick
        # except the one that lands inside the 00:00 Europe/Vienna hour.
        # Skipping the Markdown render avoids the 30-min churn of the
        # ``_Automatisch erzeugt am ..._`` timestamp in the commit log.
        LOGGER.info(
            "Dashboard-Schritt übersprungen (--skip-dashboard) — "
            "README wird weiterhin gepatcht."
        )
    else:
        markdown = render_markdown(
            year=args.year,
            generated_at=now,
            stammstrecke=sm_agg,
            stoerungen=st_agg,
            ausfaelle=au_agg,
            nav=dashboard_nav(args.output, year=args.year),
            # The dashboard shows the whole year, so its caveat is judged
            # against the year's rows, not the README's 30-day window.
            coverage_note=render_direction_coverage_note(
                sm_rows,
                now=now,
                all_rows=window_sm,
                fetch_working=fetch_working,
                yearly=True,
            ),
        )

        try:
            write_dashboard(markdown, output_path=args.output)
        except OSError as exc:
            LOGGER.error(
                "Konnte Dashboard nicht schreiben (%s): %s%s",
                sanitize_log_arg(str(args.output)),
                sanitize_log_arg(str(exc)),
                _no_request_note(args, now),
            )
            return 1

        LOGGER.info(
            "Dashboard geschrieben: %s (%d Bytes).",
            sanitize_log_arg(str(args.output)),
            len(markdown.encode("utf-8")),
        )

    if args.skip_readme:
        return _finish(args, now, pages_ok=pages_ok)

    # Defense-in-depth: only patch the Stammstrecke marker when the
    # window actually carries rows. Without this gate, an unrelated
    # caller of ``main()`` that supplies a stats directory but forgets
    # to override ``--readme-path`` (or pass ``--skip-readme``) would
    # silently overwrite the production ``README.md`` with the
    # ``_wird berechnet…_`` placeholder block — that exact bug bit
    # ``tests/scripts/test_generate_markdown_stats/`` and stamped
    # placeholders into the committed README on 2026-05-09 (audit
    # trail in PR #1397).
    sections: dict[str, str] = {}
    if sm_window:
        sections["STAMMSTRECKE"] = render_readme_stammstrecke_block(
            sm_window,
            now=now,
            window_days=args.readme_window_days,
            coverage_note=coverage_note,
        )
    else:
        LOGGER.info(
            "STAMMSTRECKE README block skipped: 0 rows in %d-day window.",
            args.readme_window_days,
        )

    sm_live_window = _filter_rows_by_timedelta(
        window_sm, delta=FEED_WINDOW, now=now
    )
    sections["STAMMSTRECKE_LIVE"] = render_readme_stammstrecke_live_block(
        sm_live_window,
        now=now,
        fetch_covers_window=_live_fetch_covers(poll_status, now=now),
    )
    # Cancellation snapshots: always render — an explicit ``0`` is the
    # operationally-meaningful "stable service" signal, so the README
    # block is patched even when the window contains no cancellations.
    # Gating on ``sm_window``/``sm_live_window`` (delay data present)
    # ensures we still bail to "preserve existing README" when the
    # repository has no statistics at all (fresh clone, missing CSVs).
    au_live_window = _filter_rows_by_timedelta(
        window_au, delta=FEED_WINDOW, now=now
    )
    sections["AUSFAELLE_LIVE"] = render_readme_ausfaelle_live_block(
        au_live_window,
        now=now,
    )
    sections["AUSFAELLE"] = render_readme_ausfaelle_block(
        au_window,
        now=now,
        window_days=args.readme_window_days,
        coverage_note=coverage_note,
    )
    if not sm_window and not sm_live_window:
        LOGGER.info(
            "README patch skipped entirely: no data for any marker block in "
            "the %d-day window — existing README content preserved.",
            args.readme_window_days,
        )
        return _finish(args, now, pages_ok=pages_ok)
    try:
        patch_readme_stats(args.readme_path, sections)
    except OSError as exc:
        LOGGER.error(
            "Konnte README nicht aktualisieren (%s): %s%s",
            sanitize_log_arg(str(args.readme_path)),
            sanitize_log_arg(str(exc)),
            _no_request_note(args, now),
        )
        return 1

    return _finish(args, now, pages_ok=pages_ok)


def _no_request_note(args: argparse.Namespace, now: datetime) -> str:
    """What a failed write costs the review: the end of its ERROR line (:func:`no_request_note`)."""
    asks = not args.skip_dashboard and args.year == now.year
    return no_request_note(year=args.year, stats_dir=args.stats_dir) if asks else ""


def _finish(args: argparse.Namespace, now: datetime, *, pages_ok: bool) -> int:
    """The end of a run that wrote everything else: the requests, then the exit code.

    Only the dashboard run of the current year asks Gemini for a missing
    review (:func:`write_year_pages`). The exit code is 1 when a year page
    failed (*pages_ok*) and, with ``--fail-on-missing-review``, from the
    fourth day of the year while a review is overdue, so the health check
    shows it: the review can fail for days (overload, a rejected text, a
    dead key). Only update-cycle.yml passes the flag (review of
    2026-11-10): build-feed.yml and the manual refresh run the script
    without ``continue-on-error`` before they publish.
    """
    if not args.skip_dashboard and args.year == now.year:
        asked = write_year_pages(
            now=now, year=args.year, stats_dir=args.stats_dir, output_path=args.output, ask=True
        )
        pages_ok = asked and pages_ok
    overdue = (
        args.fail_on_missing_review
        and args.year == now.year
        and overdue_reviews_reported(now=now, stats_dir=args.stats_dir)
    )
    return 0 if pages_ok and not overdue else 1
