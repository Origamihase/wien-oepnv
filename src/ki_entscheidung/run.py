"""One build's pass over the unclear disruptions: read the answers, ask once, apply, save.

:class:`Decider` is made once by ``build_feed.main`` and handed to the feed
chain. ``decide`` may send one request for the whole build; ``apply`` (the
single-item trials) and :func:`apply_stored` (``feed lint``, replays, every
other caller of ``_assemble_feed_items``) only read. Nothing here raises
into the build: whatever fails, the items stay as the rules see them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from ..feed_build.base import _VIENNA_TZ, _parse_datetime, _parse_first_seen, _to_utc, log
from ..feed_types import FeedItem
from ..utils.env import get_bool_env
from ..utils.logging import sanitize_log_arg
from . import ask as asking
from . import store
from .candidates import Candidate, candidates_of, describe, sudden_begin

__all__ = [
    "ENABLED_ENV", "ASK_WINDOW", "MAX_TRIES", "MAX_REQUESTS_PER_DAY", "MAX_PER_REQUEST", "REFUSAL_HOLD", "OUTPUT_NAME",
    "Decider", "apply_stored",
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
# Builds that may ask about one event of a message, answered or not.
MAX_TRIES: Final = 3
# Measured 20.09. to 12.11.2026: about two builds a week ask. The cap
# bounds a day on which something goes wrong; it counts the builds that
# asked, read from their stored moments (``store.requests_on``). One build
# asks the chain until a model answers: one request as a rule, at most one
# per model.
MAX_REQUESTS_PER_DAY: Final = 6
# The most the replay showed in one build (71E, 26E, 26, 27 on 20.09.2026).
# The probe's eight took 11 to 13 s to answer; a short request leaves room
# in ``ask.DEADLINE_S``. What does not fit is asked by the next build.
MAX_PER_REQUEST: Final = 4
# How long a refused key stays red in the Health check without a new
# request: within a day the KI-Wächter, which uses the same key, has run
# and reports a dead key itself.
REFUSAL_HOLD: Final = timedelta(hours=24)
# The step output the Update run records as side step "KI-Entscheidung".
OUTPUT_NAME: Final = "ki_outcome"


def _moment(value: object) -> datetime | None:
    moment = _parse_datetime(value) if isinstance(value, str) else None
    return _to_utc(moment) if isinstance(moment, datetime) else None


def _event_begin(candidate: Candidate) -> datetime | None:
    """The begin of the event *candidate* reports now, to the second (the stored moments carry no more)."""
    begin = sudden_begin(candidate)
    return begin.replace(microsecond=0) if begin is not None else None


def _answer_holds(record: Mapping[str, Any] | None, candidate: Candidate) -> bool:
    """Whether *record* holds an answer about the event *candidate* reports now.

    The store key is the message's GUID, and a WL GUID carries no date:
    "9: Züge halten bei Linie 52" answered "ploetzlich" during an incident
    returns under the same key and with the same text when WL enters a
    planned measure days later. An answer given before the begin the
    message shows now is about an earlier event and says nothing here; the
    same holds when a ticker that stayed in the feed is given a new begin.
    Within one event the answer stands while the text is updated.
    """
    if record is None or record.get("art") not in store.KINDS:
        return False
    decided, begin = _moment(record.get("entschieden")), _event_begin(candidate)
    return decided is not None and begin is not None and decided >= begin


def _tries(record: Mapping[str, Any] | None, candidate: Candidate) -> int:
    """How many builds have asked about the event *candidate* reports now."""
    begin = _event_begin(candidate)
    if record is None or begin is None:
        return 0
    return sum(1 for asked in map(_moment, record.get("gefragt", ())) if asked is not None and asked >= begin)


def _apply(found: Sequence[Candidate], records: Mapping[str, Mapping[str, Any]]) -> None:
    """Mark each candidate with the kind stored for its event; ``unklar`` and no answer leave it to the rules."""
    for candidate in found:
        record = records.get(candidate.key)
        if record is not None and _answer_holds(record, candidate) and record["art"] != store.UNCLEAR:
            candidate.item["_ki_art"] = record["art"]


def apply_stored(items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
    """Mark the unclear disruptions among *items* with the stored answers; asks nobody, writes nothing."""
    try:
        records = store.load()
        _apply(candidates_of(items, state, _to_utc(now), records), records)
    except Exception as exc:  # the rules decide when anything here fails
        log.warning("KI-Entscheidung: gespeicherte Antworten nicht angewendet (%s).", type(exc).__name__)


class Decider:
    """The answers of one build: loaded once, asked for at most once, saved once."""

    def __init__(self, now: datetime, *, key: str | None = None, enabled: bool | None = None) -> None:
        self.now = _to_utc(now)
        self.key = asking.api_key() if key is None else key.strip()
        self.enabled = get_bool_env(ENABLED_ENV, True) if enabled is None else enabled
        self.records, self.refused = store.read()
        self._before = store.render(self.records, self.refused)
        self._asked = False
        self._failed = False
        self._due = False
        self._spent = False

    # -- the two hooks of the feed chain ------------------------------------

    def apply(self, items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
        """Mark *items* with the answers this build holds; never asks."""
        try:
            _apply(candidates_of(items, state, self.now, self.records), self.records)
        except Exception as exc:
            log.warning("KI-Entscheidung: Antworten nicht angewendet (%s).", type(exc).__name__)

    def decide(self, items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime) -> None:
        """Ask for the candidates that still need an answer (once a build), then mark *items*."""
        try:
            found = candidates_of(items, state, self.now, self.records)
            today = self.now.astimezone(_VIENNA_TZ).date().isoformat()
            for candidate in found:
                self._seen(candidate, today)
            open_ones = [candidate for candidate in found if self._needs_answer(candidate)]
            if open_ones and not self._asked:
                try:
                    self._ask(open_ones, items)
                except Exception as exc:  # the stored answers of the other messages still apply below
                    self._failed = True
                    log.warning("KI-Entscheidung: Anfrage abgebrochen (%s), es gelten die Regeln.", type(exc).__name__)
            self._due = any(self._needs_answer(candidate) for candidate in found)
            self._spent = any(self._unanswered(candidate) for candidate in found)
            _apply(found, self.records)
        except Exception as exc:
            # Red in the Health check: a pass that breaks off in every build would otherwise read as clean.
            self._failed = True
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
        """Whether a request for *candidate* is still due: fresh, tries left, no answer for this event and text."""
        record = self.records.get(candidate.key)
        answered = record is not None and _answer_holds(record, candidate) and record.get("abdruck") == candidate.print
        return not answered and _tries(record, candidate) < MAX_TRIES and self._fresh(candidate)

    def _unanswered(self, candidate: Candidate) -> bool:
        """Whether a fresh message has used up its requests without any answer for its event."""
        record = self.records.get(candidate.key)
        return not _answer_holds(record, candidate) and _tries(record, candidate) >= MAX_TRIES and self._fresh(candidate)

    def _ask(self, open_ones: Sequence[Candidate], items: Sequence[FeedItem]) -> None:
        if not self.enabled or not self.key:
            return
        if store.requests_on(self.records, self.now.astimezone(_VIENNA_TZ).date()) >= MAX_REQUESTS_PER_DAY:
            log.warning("KI-Entscheidung: %d Anfragen heute, keine weitere; es gelten die Regeln.", MAX_REQUESTS_PER_DAY)
            return
        self._asked = True
        try:
            # The limits above are read from the file; a build that cannot write it must not ask.
            store.store_path()
        except Exception as exc:
            self._failed = True
            log.warning("KI-Entscheidung: Speicher nicht nutzbar (%s), keine Anfrage.", type(exc).__name__)
            return
        batch = list(open_ones[:MAX_PER_REQUEST])
        stamp = self.now.isoformat(timespec="seconds")
        answer = asking.ask([describe(number, candidate, items) for number, candidate in enumerate(batch, 1)], self.key)
        if answer.key_rejected:
            # No try of the messages: with a working key they are asked again.
            self.refused = stamp
            return
        if answer.requests <= 0:
            # Nothing was sent (no valid model name, a prompt beyond the connector's limit).
            self._failed = True
            return
        self.refused = ""
        today = self.now.astimezone(_VIENNA_TZ).date().isoformat()
        decided = answer.value or {}
        for number, candidate in enumerate(batch, 1):
            record = self.records.setdefault(candidate.key, {"gefragt": []})
            record["gefragt"] = [*record["gefragt"], stamp][-MAX_REQUESTS_PER_DAY:]
            record.update(klasse=candidate.kind, titel=str(candidate.item.get("title") or "")[:200], zuletzt=today)
            if number not in decided:
                continue
            kind, reason = decided[number]
            record.update(art=kind, grund=reason, modell=answer.model, abdruck=candidate.print, entschieden=stamp)
            log.info(
                "KI-Entscheidung: %s → %s (%s)",
                sanitize_log_arg(str(candidate.item.get("title") or "")), kind, sanitize_log_arg(answer.model),
            )
        log.info("KI-Entscheidung: %d Meldung(en) gefragt, %d beantwortet, %d Anfrage(n).", len(batch), len(decided), answer.requests)

    # -- after the build ----------------------------------------------------

    def _refused_lately(self) -> bool:
        refused = _moment(self.refused)
        return refused is not None and self.now - refused < REFUSAL_HOLD

    def outcome(self) -> str:
        """What the Health check should learn of this build (side step "KI-Entscheidung").

        ``failure`` (red at once): the requests are on and cannot run. The
        key is empty; or Google refused it, which stays red until a request
        is accepted again, for :data:`REFUSAL_HOLD` at most (a build
        without an unclear message asks nothing and would otherwise report
        green an hour after the refusal); or the pass broke off, or nothing
        could be sent or stored. The owner's rule (2026-10-28): a step that
        cannot run must be noticed. ``overloaded`` (a note, as for the
        KI-Wächter since 2026-11-04): a new message got no answer from any
        of its :data:`MAX_TRIES` builds, or it waits while the day's
        requests are used up; the feed shows it by the rules, and the note
        ends by itself when the message is no longer new. It never grows
        into a failure: every build without an unclear message is a
        ``success``. Models that stay silent for days show in the
        KI-Wächter's own record, which asks the same chain every day.
        Nothing while tries are left: the next build asks again.
        ``success`` otherwise, also when ``KI_ENTSCHEIDUNG=0`` switched the
        requests off: what the owner switched off is no fault, and an
        earlier red must not stand for good.
        """
        if not self.enabled:
            return "success"
        if not self.key or self._failed or self._refused_lately():
            return "failure"
        if self._spent or (self._due and not self._asked):
            return "overloaded"
        return "" if self._due else "success"

    def finish(self) -> None:
        """Save what changed and report the outcome; a build without the key writes no file."""
        if self.enabled and self.key:
            try:
                store.prune(self.records, self.now.astimezone(_VIENNA_TZ).date())
                store.save(self.records, self._before, self.refused if self._refused_lately() else "")
            except Exception as exc:
                self._failed = True
                log.warning("KI-Entscheidung: Speichern gescheitert (%s).", type(exc).__name__)
        try:
            _write_output(self.outcome())
        except Exception as exc:
            log.warning("KI-Entscheidung: Ergebnis nicht gemeldet (%s).", type(exc).__name__)


def _write_output(outcome: str) -> None:
    """Append ``ki_outcome=<outcome>`` to ``$GITHUB_OUTPUT`` (GitHub Actions only)."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target or not outcome:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{OUTPUT_NAME}={outcome}\n")
