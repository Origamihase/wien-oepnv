"""One item that makes the feed chain fail leaves the run; the feed is built with the rest.

Until 2026-11-10 the first exception in the feed chain or in the rendering
of the German feed ended ``build_feed.main``: no new feed for anyone, in
every run, for as long as the source listed the item that caused it. The
audit of that day showed on a copy of the data how little it takes (one WL
disruption whose end names the year 9999, see
:data:`src.utils.vienna_clock.PLAUSIBLE_YEARS`).

Operator decision Michael 2026-11-10 (card "Ja, alle vier Teile"): an item
on which a step fails drops out of this run, the feed is built with the
other items, and the skipped item is reported (log, health report, and from
there the Feed-Wächter). The project's goal is a feed that keeps running
without manual steps.

How :func:`build_isolating` does it:

* The normal run is unchanged: the chain and the rendering run once over the
  collected items. Only a copy of the items is taken before, because the
  chain changes them in place. When the items cannot be copied, the run is
  built as before 2026-11-10, without the single-item trials, and a warning
  says so.
* When that run raises, every item of the copy is put through the chain and
  the rendering on its own, also an item that would not have reached the
  feed's ten places. An item that raises alone leaves the run. The state is
  read again from disk (the failed run may have changed it), the chain runs
  once more without those items, and each of them is reported.
* When no single item raises alone, when more than half of the items do (a
  fault of the code, not of an item: the feed would come out almost empty),
  or when the second run raises too, the first exception is raised as
  before: the build fails loudly and the published feed stays as it is.
* The rendering enters every new disruption into the statistics ledger
  (``data/stats/stoerungen_YYYY.csv``). Those rows are kept back
  (:func:`src.utils.stats.rows_held_back`) and written only for the run
  whose feed is returned: a failed run and the single-item trials count
  nothing.

docs/architecture-teil-7.md, §10, "Eine Meldung hält den Bau nicht an".
"""
from __future__ import annotations

import copy
import logging
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from ..feed_types import FeedItem
from ..utils.logging import sanitize_log_arg
from ..utils.stats import HeldRows, rows_held_back
from .base import _sanitize_text, log

__all__ = ["SkipReporter", "build_isolating"]

_THIS_FILE = Path(__file__).resolve()
_PACKAGE_ROOT = _THIS_FILE.parents[2]

State = dict[str, Any]
Assemble = Callable[[list[FeedItem], State], list[FeedItem]]
Render = Callable[[list[FeedItem], State], str]


class SkipReporter(Protocol):
    """What :func:`build_isolating` needs of the run report."""

    def add_skipped_item(self, source: str, title: str, error: str) -> None:
        """Same as ``RunReport.add_skipped_item``."""


def _where(exc: BaseException) -> str:
    """Class and place of *exc*: ``OverflowError in src/feed_build/filters.py:75``.

    The innermost frame under ``src/`` of this repository, the frames of
    this module aside; only the class when there is none. The message of the
    exception is left out: it can quote the text of the source.
    """
    place = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        try:
            resolved = Path(frame.filename).resolve()
            relative = resolved.relative_to(_PACKAGE_ROOT)
        except (OSError, ValueError):
            continue
        if resolved != _THIS_FILE and relative.parts and relative.parts[0] == "src":
            place = f"{relative.as_posix()}:{frame.lineno}"
    name = type(exc).__name__
    return f"{name} in {place}" if place else name


def _one_line(value: object, fallback: str) -> str:
    """*value* as one cleaned line for the log and the report."""
    return " ".join(_sanitize_text(str(value or "")).split()) or fallback


def _failing_alone(
    items: list[FeedItem], state: State, assemble: Assemble, render: Render
) -> dict[int, str]:
    """Index and failure of every item that raises when it runs alone.

    *state* is a throw-away copy shared by all trials. Logging is off for
    the trials: each one would repeat the lines of a whole build. Their
    ledger rows are dropped.
    """
    failing: dict[int, str] = {}
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with rows_held_back():
            for index, item in enumerate(items):
                try:
                    render(assemble([copy.deepcopy(item)], state), state)
                except Exception as exc:  # noqa: BLE001 - any failure names the item
                    failing[index] = _where(exc)
    finally:
        logging.disable(previous)
    return failing


def build_isolating(
    collected: list[FeedItem],
    state: State,
    *,
    assemble: Assemble,
    render: Render,
    reload_state: Callable[[], State],
    reset: Callable[[], None],
    report: SkipReporter,
    trial_assemble: Assemble | None = None,
) -> tuple[list[FeedItem], str, float]:
    """The ordered feed items, the German feed and the rendering time.

    *assemble* is the feed chain (``_assemble_feed_items``), *render* the
    German feed (``_make_rss``); both get the items and the state. *reset*
    clears what the caller counted in a failed run before the chain runs
    again, *reload_state* reads the state as the run found it.
    *trial_assemble* is the chain for the single-item trials when *assemble*
    counts for the caller (default: *assemble*), so the trials count nothing
    for the caller.
    """

    def attempt(items: list[FeedItem]) -> tuple[list[FeedItem], str, float, HeldRows]:
        with rows_held_back() as ledger_rows:
            ordered = assemble(items, state)
            started = perf_counter()
            rss = render(ordered, state)
            duration = perf_counter() - started
        return ordered, rss, duration, ledger_rows

    try:
        pristine = copy.deepcopy(collected)
    except Exception:  # noqa: BLE001 - an item that cannot be copied: build as before 2026-11-10
        log.warning("Meldungen nicht kopierbar – der Feed wird ohne Einzelprüfung gebaut.")
        ordered, rss, duration, ledger_rows = attempt(collected)
        ledger_rows.write()
        return ordered, rss, duration

    try:
        ordered, rss, duration, ledger_rows = attempt(collected)
    except Exception as first:
        log.warning(
            "Feed-Kette gescheitert (%s) – suche die Meldung, an der es liegt.",
            sanitize_log_arg(_where(first)),
        )
        failing = _failing_alone(pristine, reload_state(), trial_assemble or assemble, render)
        if not failing or len(failing) * 2 > len(pristine):
            log.warning(
                "%d von %d Meldungen scheitern einzeln – der Bau endet mit dem Fehler.",
                len(failing), len(pristine),
            )
            raise
        skipped: list[tuple[str, str, str]] = []
        for index, where in failing.items():
            entry = pristine[index]
            fields: Mapping[str, object] = entry if isinstance(entry, Mapping) else {}
            skipped.append(
                (_one_line(fields.get("source"), "unbekannt"), _one_line(fields.get("title"), "(ohne Titel)"), where)
            )
        state.clear()
        state.update(reload_state())
        reset()
        try:
            ordered, rss, duration, ledger_rows = attempt(
                [it for index, it in enumerate(pristine) if index not in failing]
            )
        except Exception as second:
            log.warning(
                "Auch ohne %d einzeln scheiternde Meldung(en) scheitert der Bau (%s) – er endet mit dem ersten Fehler.",
                len(failing), sanitize_log_arg(_where(second)),
            )
            raise first from None
        for source, title, where in skipped:
            log.error(
                "Meldung übersprungen, der Feed wurde ohne sie gebaut (%s): %s – %s",
                where, source, title,
            )
            report.add_skipped_item(source, title, where)
    # The last step of this function: a run whose chain or rendering failed
    # has entered nothing into the ledger. (The caller can still fail when it
    # writes the feed file; the rows are then entered, as before 2026-11-10.)
    ledger_rows.write()
    return ordered, rss, duration
