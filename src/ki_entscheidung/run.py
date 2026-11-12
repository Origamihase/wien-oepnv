"""One build's pass over the unclear disruptions: read the answers, ask once, apply, save.

:class:`Decider` is made once by ``build_feed.main`` and handed to the feed
chain. ``decide`` may send one request for the whole build; ``apply`` (the
single-item trials) and :func:`apply_stored` (``feed lint``, replays, every
other caller of ``_assemble_feed_items``) only read. Nothing here raises
into the build: whatever fails, the items stay as the rules see them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from ..feed_build.base import _VIENNA_TZ, _parse_first_seen, _to_utc, log
from ..feed_types import FeedItem
from ..utils.env import get_bool_env
from ..utils.logging import sanitize_log_arg
from . import ask as asking
from . import store
from .candidates import Candidate, candidates_of, describe, sudden_begin

__all__ = [
    "ENABLED_ENV", "ASK_WINDOW", "MAX_TRIES", "MAX_REQUESTS_PER_DAY", "MAX_PER_REQUEST", "OUTPUT_NAME", "Decider",
    "apply_stored",
]

# ``KI_ENTSCHEIDUNG=0`` switches the requests off; stored answers still apply.
ENABLED_ENV: Final = "KI_ENTSCHEIDUNG"
# A message is put to the model only while it is new: it entered the feed
# within this window, and the begin a "ploetzlich" would show lies within
# it too. The Feed-Wächter reports a "[Seit hh:mm]" that first appears more
# than three hours after its clock time (``feed_watch.model.SINCE_LATE``,
# "seit-zu-frueh"); a later answer would produce exactly that line. The
# quarter of an hour below it is room for the minutes between the build's
# clock and the feed's.
ASK_WINDOW: Final = timedelta(hours=2, minutes=45)
# Requests that may carry one message, answered or not.
MAX_TRIES: Final = 3
# Measured 20.09. to 12.11.2026: about three unclear messages a week. The
# cap bounds a day on which something goes wrong; it is read from the
# stored moments of the requests (``store.requests_on``).
MAX_REQUESTS_PER_DAY: Final = 6
MAX_PER_REQUEST: Final = 8
# The step output the Update run records as side step "KI-Entscheidung".
OUTPUT_NAME: Final = "ki_outcome"


def _set_kind(item: FeedItem, record: dict[str, Any] | None) -> None:
    kind = record.get("art") if record else None
    if kind == store.SUDDEN:
        item["_ki_art"] = store.SUDDEN
    elif kind == store.PLANNED:
        item["_ki_art"] = store.PLANNED


def _apply(found: Sequence[Candidate], records: dict[str, dict[str, Any]]) -> None:
    """Mark each candidate with its stored kind; ``unklar`` and no answer leave it to the rules."""
    for candidate in found:
        _set_kind(candidate.item, records.get(candidate.key))


def apply_stored(items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
    """Mark the unclear disruptions among *items* with the stored answers; asks nobody, writes nothing."""
    try:
        found = candidates_of(items, state, _to_utc(now))
        if found:
            _apply(found, store.load())
    except Exception as exc:  # the rules decide when anything here fails
        log.warning("KI-Entscheidung: gespeicherte Antworten nicht angewendet (%s).", type(exc).__name__)


class Decider:
    """The answers of one build: loaded once, asked for at most once, saved once."""

    def __init__(self, now: datetime, *, key: str | None = None, enabled: bool | None = None) -> None:
        self.now = _to_utc(now)
        self.key = asking.api_key() if key is None else key.strip()
        self.enabled = get_bool_env(ENABLED_ENV, True) if enabled is None else enabled
        self.records = store.load()
        self._before = store.render(self.records)
        self._asked = False
        self._rejected = False
        self._due = False
        self._spent = False

    # -- the two hooks of the feed chain ------------------------------------

    def apply(self, items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
        """Mark *items* with the answers this build holds; never asks."""
        try:
            _apply(candidates_of(items, state, self.now), self.records)
        except Exception as exc:
            log.warning("KI-Entscheidung: Antworten nicht angewendet (%s).", type(exc).__name__)

    def decide(self, items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
        """Ask for the candidates that still need an answer (once a build), then mark *items*."""
        try:
            found = candidates_of(items, state, self.now)
            today = self.now.astimezone(_VIENNA_TZ).date().isoformat()
            for candidate in found:
                self._seen(candidate, today)
            open_ones = [candidate for candidate in found if self._needs_answer(candidate)]
            if open_ones and not self._asked:
                self._ask(open_ones, items)
            self._due = any(self._needs_answer(candidate) for candidate in found)
            self._spent = any(self._unanswered(candidate) for candidate in found)
            _apply(found, self.records)
        except Exception as exc:
            log.warning("KI-Entscheidung: Durchlauf abgebrochen (%s), es gelten die Regeln.", type(exc).__name__)

    # -- one build's bookkeeping --------------------------------------------

    def _seen(self, candidate: Candidate, today: str) -> None:
        record = self.records.get(candidate.key)
        if record is not None and record.get("zuletzt") != today:
            record["zuletzt"] = today

    def _fresh(self, candidate: Candidate) -> bool:
        """Whether the message is new: in the feed and begun within :data:`ASK_WINDOW`."""
        first_seen = _parse_first_seen(dict(candidate.entry) if candidate.entry else None, None)
        if first_seen is not None and self.now - first_seen > ASK_WINDOW:
            return False
        begin = sudden_begin(candidate)
        return begin is not None and self.now - begin <= ASK_WINDOW

    def _needs_answer(self, candidate: Candidate) -> bool:
        """Whether a request for *candidate* is still due: fresh, tries left, no answer for this text."""
        record = self.records.get(candidate.key)
        if record is None:
            return self._fresh(candidate)
        answered = "art" in record and record.get("abdruck") == candidate.print
        return not answered and len(record["gefragt"]) < MAX_TRIES and self._fresh(candidate)

    def _unanswered(self, candidate: Candidate) -> bool:
        """Whether a fresh message has used up its requests without any answer."""
        record = self.records.get(candidate.key)
        return (
            record is not None and "art" not in record and len(record["gefragt"]) >= MAX_TRIES and self._fresh(candidate)
        )

    def _ask(self, open_ones: Sequence[Candidate], items: Sequence[FeedItem]) -> None:
        if not self.enabled or not self.key:
            return
        if store.requests_on(self.records, self.now.astimezone(_VIENNA_TZ).date()) >= MAX_REQUESTS_PER_DAY:
            log.warning("KI-Entscheidung: %d Anfragen heute, keine weitere; es gelten die Regeln.", MAX_REQUESTS_PER_DAY)
            return
        batch = list(open_ones[:MAX_PER_REQUEST])
        self._asked = True
        stamp = self.now.isoformat(timespec="seconds")
        today = self.now.astimezone(_VIENNA_TZ).date().isoformat()
        for candidate in batch:
            record = self.records.setdefault(candidate.key, {"gefragt": []})
            record["gefragt"] = [*record["gefragt"], stamp][-MAX_TRIES:]
            record.update(klasse=candidate.kind, titel=str(candidate.item.get("title") or "")[:200], zuletzt=today)
        answer = asking.ask([describe(number, candidate, items) for number, candidate in enumerate(batch, 1)], self.key)
        self._rejected = answer.key_rejected
        decided = answer.value or {}
        for number, candidate in enumerate(batch, 1):
            if number not in decided:
                continue
            kind, reason = decided[number]
            self.records[candidate.key].update(
                art=kind, grund=reason, modell=answer.model, abdruck=candidate.print,
                entschieden=stamp,
            )
            log.info(
                "KI-Entscheidung: %s → %s (%s)",
                sanitize_log_arg(str(candidate.item.get("title") or "")), kind, sanitize_log_arg(answer.model),
            )
        log.info("KI-Entscheidung: %d Meldung(en) gefragt, %d beantwortet, %d Anfrage(n).", len(batch), len(decided), answer.requests)

    # -- after the build ----------------------------------------------------

    def outcome(self) -> str:
        """What the Health check should learn of this build (side step "KI-Entscheidung").

        ``failure`` (red at once): the requests are on and the key is empty
        or Google refused it. The owner's rule (2026-10-28): a step that
        cannot run must be noticed. ``overloaded`` (a note, as for the
        KI-Wächter since 2026-11-04): a new message got no answer from any
        of its :data:`MAX_TRIES` requests, or it waits while the day's
        requests are used up; the feed shows it by the rules, and the note
        ends by itself when the message is no longer new. Nothing while
        tries are left: the next build asks again. ``success`` otherwise.
        """
        if not self.enabled:
            return ""
        if not self.key or self._rejected:
            return "failure"
        if self._spent or (self._due and not self._asked):
            return "overloaded"
        return "" if self._due else "success"

    def finish(self) -> None:
        """Save what changed and report the outcome; a build without the key writes nothing."""
        try:
            if self.enabled and self.key:
                store.prune(self.records, self.now.astimezone(_VIENNA_TZ).date())
                store.save(self.records, self._before)
            _write_output(self.outcome())
        except Exception as exc:
            log.warning("KI-Entscheidung: Speichern gescheitert (%s).", type(exc).__name__)


def _write_output(outcome: str) -> None:
    """Append ``ki_outcome=<outcome>`` to ``$GITHUB_OUTPUT`` (GitHub Actions only)."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target or not outcome:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{OUTPUT_NAME}={outcome}\n")
