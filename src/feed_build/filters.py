"""Feed filters: age, duplicates, topic budget, test messages and deferrals.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, UTC
from typing import Any

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..feed.reporting import DuplicateSummary
from ..utils.logging import sanitize_log_arg
from ..utils.text import html_to_text

from .base import _VIENNA_TZ, _WHITESPACE_RE, _parse_datetime, _parse_first_seen, _sanitize_text, _to_utc, log
from .display_titles import _CATEGORY_PREFIX_WORDS
from .state import _dedupe_key_for_item, _lookup_state
from .time_line import _is_all_clear
from .summary_text import _title_body
from .title_words import _leading_category_word

__all__ = [
    "_valid_until", "_drop_after", "_drop_old_items", "_summarize_duplicates", "_dedupe_items",
    "_topic_budget_key", "_apply_topic_budget", "_defer_repeated_route_titles", "_is_test_message",
    "_drop_test_messages", "_preview_last_day", "_defer_upcoming_items", "_defer_all_clear_items",
]


def _valid_until(item: FeedItem) -> datetime | None:
    """The moment *item* stops being valid, from its ``ends_at``.

    Stadt Wien delivers construction ends as a date ("2026-09-04Z"), read as
    Vienna midnight of that day. The time line takes such an end as the day
    ("Bis Fr 04.09."), so the works still apply on it; dropped at that
    midnight, every construction site left the feed at the start of its last
    day (all 13 that ran to their end between July and October 2026, one of
    them from place 3). Such an end counts until the next Vienna midnight.
    Every other end stands as given.
    """
    ends_at = item.get("ends_at")
    if not isinstance(ends_at, datetime):
        return None
    if not str(item.get("source") or "").strip().casefold().startswith("stadt wien"):
        return ends_at
    local = _to_utc(ends_at).astimezone(_VIENNA_TZ)
    if local.time() != time(0, 0):
        return ends_at
    next_day = local.date() + timedelta(days=1)
    return datetime(next_day.year, next_day.month, next_day.day, tzinfo=_VIENNA_TZ)


def _drop_after(item: FeedItem) -> datetime | None:
    """The moment (UTC) after which *item* leaves the feed, from :func:`_valid_until`.

    ``ENDS_AT_GRACE_MINUTES`` past the end, but never past the Vienna
    midnight that ends the day the time line names. Without that bound an
    end late in the day stayed over midnight with a date already over: on
    2026-10-20 at 00:00 Vienna the feed showed "Wien Hütteldorf ↔ Wien
    Hauptbahnhof … [Bis 19.10.]" (end 19.10. 23:59:59, Feed-Wächter issue
    #2044), and every ÖBB end of day and every Baustelle on its last day
    leaves the same way.
    """
    valid_until = _valid_until(item)
    if valid_until is None:
        return None
    graced = _to_utc(valid_until) + timedelta(minutes=feed_config.ENDS_AT_GRACE_MINUTES)
    ends_at = item.get("ends_at")
    if not isinstance(ends_at, datetime):
        return graced
    shown_day = _to_utc(ends_at).astimezone(_VIENNA_TZ).date() + timedelta(days=1)
    midnight = datetime(shown_day.year, shown_day.month, shown_day.day, tzinfo=_VIENNA_TZ)
    return min(graced, _to_utc(midnight))


def _drop_old_items(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
) -> tuple[list[FeedItem], set[str]]:
    """Entferne Items, die nicht mehr gültig oder zu lange im Feed sind.

    Zwei Regeln:

    1. **Ungültig → sofort raus:** ein ``ends_at`` in der Vergangenheit
       (über die ``ENDS_AT_GRACE_MINUTES`` hinaus, höchstens bis zur
       Wiener Mitternacht nach dem Endtag, :func:`_drop_after`) bedeutet,
       die Störung ist behoben → die Meldung macht sofort Platz. Ein reines Enddatum der
       Stadt-Wien-Baustellen gilt bis zum Ende dieses Tages
       (:func:`_valid_until`).
    2. **Alter → FIFO nach ``first_seen``:** das Alter zählt ab dem
       Auftauchen im Feed (``first_seen`` aus dem State), NICHT ab dem
       Quell-Startdatum. So fällt ein aktiver Langläufer nicht wegen eines
       alten Startdatums heraus; ein noch nicht im State vermerktes Item ist
       brandneu und wird hier nie ausgemustert.

    Der zweite Rückgabewert nennt, was hier ausschied. Der State-Eintrag
    einer solchen Meldung bleibt stehen (``main`` übergibt ``_save_state``
    seit 2026-11-12 keine Löschungen mehr); erst die Aufbewahrungsfrist
    ``STATE_RETENTION_DAYS`` vergisst ihn. Der Eintrag ist das, was der Feed
    von einer Meldung weiß. Ein Takt-Lauf sieht ein Ende hier fast nie, der
    Abruf lässt eine beendete Meldung vorher weg; ein Neubau zwischen zwei
    Takten liest sie noch aus dem Cache. Am 11.11.2026 löschte der Neubau um
    01:24 Wiener Zeit so den Eintrag von „66A: Busse halten
    Salvatorianerplatz“ (Ende 01:00, im Feed seit 03.10.). WL gab die
    Maßnahme um 04:40 wie jeden Tag neu aus, und sie stand als neu auf
    Platz 2 statt um Platz 23. Mit dem Eintrag unterscheidet
    ``_restart_recurring_occurrences`` die neu ausgegebene Maßnahme von
    einem neuen Auftreten, wie nach jedem Takt-Lauf. Aus demselben Grund
    bleibt eine Meldung jenseits von ``MAX_ITEM_AGE_DAYS`` draußen, solange
    ihr Eintrag besteht: Ohne Eintrag käme sie mit dem nächsten Bau als neu
    zurück. Die Aufbewahrungsfrist zählt ab ``first_seen``; führt die Quelle
    eine Meldung 600 Tage nach ihrem ersten Erscheinen noch, gilt sie danach
    wieder als neu.
    """

    out: list[FeedItem] = []
    dropped: set[str] = set()
    now_utc = _to_utc(now)
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]

        ident, state_entry = _lookup_state(it, state)

        ends_at = it.get("ends_at")
        drop_after = _drop_after(it)
        if drop_after is not None and drop_after < now_utc:
            dropped.add(ident)
            continue

        # Age out by how long the item has been in the feed (first_seen) —
        # FIFO by appearance ("man kennt die Meldung schon"). The source
        # start date is deliberately NOT used, so an active long-runner that
        # only recently surfaced is not retired for an old upstream start;
        # an item not yet in the state is brand-new and never aged out here.
        first_seen_dt = _parse_first_seen(state_entry, None)
        age_days: float | None = (
            (now_utc - first_seen_dt).total_seconds() / 86400.0
            if first_seen_dt is not None
            else None
        )

        if age_days is not None:
            if age_days > feed_config.ABSOLUTE_MAX_AGE_DAYS:
                dropped.add(ident)
                continue
            if age_days > feed_config.MAX_ITEM_AGE_DAYS:
                if not isinstance(ends_at, datetime):
                    dropped.add(ident)
                    continue

        out.append(it)

    # Duplicate-identity guard: when two items share a state-key (e.g. a
    # duplicate-guid pair across providers / plugins) and one expired or
    # aged out while the other survives, the loop above added the shared
    # identity to ``dropped`` from the expired sibling AND will re-emit the
    # state entry from the survivor in ``_make_rss``. Pre-fix
    # ``_save_state`` then unconditionally pruned the freshly-written
    # survivor entry — silent perpetual churn (re-publish via fresh
    # pubDate, FIFO age retirement disabled). Subtract the survivors'
    # identities so ``dropped`` carries only items with no surviving
    # twin.
    if dropped:
        dropped -= {_lookup_state(it, state)[0] for it in out}
    return out, dropped


def _summarize_duplicates(items: Sequence[FeedItem]) -> list[DuplicateSummary]:
    groups: dict[str, list[FeedItem]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        key, _ = _dedupe_key_for_item(it, warn_on_missing=False)
        groups.setdefault(key, []).append(it)

    summaries: list[DuplicateSummary] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        # Security (Trojan-Source canonical-floor scrub at the boundary):
        # ``key`` (derived from each item's ``guid`` / ``_identity`` /
        # ``link``) and each ``title`` are upstream-controlled — a
        # compromised provider / MITM / DNS hijack / poisoned cache
        # fallback can plant BiDi marks, Tag-block bytes, variation
        # selectors, or C0/C1 controls inside them. ``_sanitize_text``
        # strips the canonical-floor primitive family pinned by
        # ``_CONTROL_RE`` (the byte-exact mirror of
        # ``_INVISIBLE_DANGEROUS_RE``) so every downstream sink of
        # ``DuplicateSummary`` — ``docs/feed-health.md`` inline code
        # spans (via ``safe_markdown_codespan`` in
        # ``render_feed_health_markdown``), ``docs/feed-health.json``
        # (via ``_CONTROL_CHARS_RE`` scrub in
        # ``build_feed_health_payload``), and the operator-facing
        # lint-report stdout (line below) — inherits the canonical
        # floor in one place. The grouping above runs on the RAW key
        # so two items carrying the same poisoned guid still group
        # together; only the OUTPUT key handed to ``DuplicateSummary``
        # is scrubbed.
        sanitised_key = _sanitize_text(key)
        titles = tuple(
            _sanitize_text(str(entry.get("title") or "")) for entry in group[:3]
        )
        summaries.append(
            DuplicateSummary(
                dedupe_key=sanitised_key, count=len(group), titles=titles
            )
        )
    summaries.sort(key=lambda summary: summary.count, reverse=True)
    return summaries


def _dedupe_items(items: list[FeedItem]) -> list[FeedItem]:
    """
    Deduplicate items by identity/guid.

    When duplicates are found (same deduplication key), the "better" item is kept.
    Selection criteria for the "better" item:
    1. Later end date (indicates more up-to-date info on duration).
    2. More recent publication/start date.
    3. Longer description (indicates more detail).

    Args:
        items: List of item dictionaries.

    Returns:
        A list of unique item dictionaries.
    """

    def _recency_value(it: FeedItem) -> datetime:
        """Return a comparable timestamp describing how recent ``it`` is."""
        if "_calculated_recency" in it:
            return it["_calculated_recency"]

        candidates: list[datetime] = []
        for field_name in ("pubDate", "first_seen", "starts_at"):
            value = it.get(field_name)
            if isinstance(value, datetime):
                candidates.append(_to_utc(value))
            else:
                parsed = _parse_datetime(value)
                if isinstance(parsed, datetime):
                    candidates.append(_to_utc(parsed))

        if candidates:
            res = max(candidates)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_recency"] = res
        return res

    def _end_value(it: FeedItem) -> datetime:
        if "_calculated_end" in it:
            return it["_calculated_end"]

        ends = it.get("ends_at")
        if isinstance(ends, datetime):
            res = _to_utc(ends)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_end"] = res
        return res

    def _better(a: FeedItem, b: FeedItem) -> bool:
        """Return True if ``a`` is better than ``b`` according to recency and content."""

        a_end = _end_value(a)
        b_end = _end_value(b)
        if a_end > b_end:
            return True
        if a_end < b_end:
            return False

        # Bei gleichem Enddatum: Zuerst Aktualität, dann Länge
        if _recency_value(a) > _recency_value(b):
            return True
        if _recency_value(a) < _recency_value(b):
            return False

        a_len = len(a.get("description") or "")
        b_len = len(b.get("description") or "")
        return a_len > b_len

    seen: dict[str, int] = {}
    out: list[FeedItem] = []
    for it in items:
        key, _ = _dedupe_key_for_item(it)
        if key in seen:
            idx = seen[key]
            if _better(it, out[idx]):
                out[idx] = it
        else:
            seen[key] = len(out)
            out.append(it)
    return out


_TOPIC_REASON_TAIL_RE = re.compile(r"\bwegen\s+(\S+)\s*$", re.IGNORECASE)


def _topic_budget_key(item: FeedItem) -> tuple[str, str] | None:
    """The (reason word, local day) an item is budgeted under, or ``None``.

    The reason word is the WL ticker's leading category word
    (``Demonstration Betrieb ab …``, see ``_CATEGORY_PREFIX_WORDS``) or the
    one after a trailing ``wegen`` (``Fahrtbehinderung wegen
    Demonstration``). The day is the Vienna date of ``starts_at``, falling
    back to ``pubDate``. Items without a reason word or without a date are
    never budgeted — the budget exists for the one shape that floods the
    feed: one event, one ticker per line, all on the same day.
    """
    body = _title_body(str(item.get("title") or ""))
    reason = _leading_category_word(body).casefold()
    if not reason:
        tail = _TOPIC_REASON_TAIL_RE.search(body)
        candidate = tail.group(1).casefold().strip(".,!") if tail else ""
        reason = candidate if candidate in _CATEGORY_PREFIX_WORDS else ""
    if not reason:
        return None
    when = _parse_datetime(item.get("starts_at")) or _parse_datetime(item.get("pubDate"))
    if not isinstance(when, datetime):
        return None
    return reason, _to_utc(when).astimezone(_VIENNA_TZ).date().isoformat()


def _apply_topic_budget(items: list[FeedItem], limit: int) -> list[FeedItem]:
    """Let no single reason-and-day fill more than *limit* of the leading slots.

    Published 2026-09-19: one demonstration, one Wiener-Linien ticker per
    line, and — sorted newest-first — the ten slots of the feed went to
    that one event; the 25/26/27 replacement service, every ÖBB notice and
    the two construction-site items directly below the cap were invisible.
    Over 300 published revisions the same shape recurred on two of eight
    days (a Veranstaltung on 17.09., the demonstration on 19.09.), each time
    with eight same-reason tickers in the top ten.

    This pass keeps the sorted order and moves every item beyond the
    *limit*-th of its ``_topic_budget_key`` behind all the others, in their
    original order. Nothing is dropped: with a larger ``MAX_ITEMS`` the
    deferred items still appear, just later. ``limit`` 0 disables the pass.
    Items without a key (no reason word, no date) are never deferred.
    """
    if limit <= 0:
        return items
    seen: dict[tuple[str, str], int] = {}
    kept: list[FeedItem] = []
    deferred: list[FeedItem] = []
    for item in items:
        key = _topic_budget_key(item)
        if key is None:
            kept.append(item)
            continue
        seen[key] = seen.get(key, 0) + 1
        (kept if seen[key] <= limit else deferred).append(item)
    if deferred:
        log.info(
            "Platzbudget: %d Item(s) hinter das Feld gestellt (höchstens %d je Ursache und Tag).",
            len(deferred),
            limit,
        )
    return kept + deferred


_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def _repeated_route_title_key(item: FeedItem) -> str | None:
    """The key an ÖBB item shares with its other time windows, or ``None``."""
    if str(item.get("source") or "").strip().casefold() != "öbb":
        return None
    title = _WHITESPACE_RE.sub(" ", str(item.get("title") or "")).strip().casefold()
    return title or None


def _defer_repeated_route_titles(
    items: list[FeedItem],
    is_current: Callable[[FeedItem], bool] | None = None,
    leads: Callable[[FeedItem], bool] | None = None,
) -> list[FeedItem]:
    """Give one slot to each ÖBB title, held by the member that matters most today.

    ÖBB titles are the route and nothing else, so one route closed in three
    separate construction phases arrives as three items with one title::

        Wien Hauptbahnhof ↔ Gramatneusiedl    03.10. – 05.10.
        Wien Hauptbahnhof ↔ Gramatneusiedl    31.10. – 30.11.
        Wien Hauptbahnhof ↔ Gramatneusiedl    05.12. – 07.12.

    Each survives the dedupe passes (three GUIDs, three different texts)
    and the topic budget (a route title carries no reason word). On the
    displays the same line would stand three times and cost two other
    disruptions their slot.

    The items are not merged: the phases are different measures (single
    trains in October, no local trains at night later), and one text for
    all three would misstate two of them. Instead one item keeps its own
    place in the sorted order and the others move behind the field, in
    their original order — exactly like :func:`_apply_topic_budget`.
    Nothing is dropped: once the first phase has ended it leaves the feed
    and the next one holds the slot.

    Who holds the slot, in this order:

    1. A current incident (*is_current*, :func:`_is_current_incident`), the
       first one in the sorted order. ÖBB names a disruption by its route
       like a construction phase, and a phase that is running started
       earlier than any disruption today: an accident between Wien Hbf and
       Wien Westbahnhof would have gone behind the field next to the works
       there (11.09. to 01.11.2026), against the operator decision of
       2026-10-04 that current disruptions come first.
    2. A member that *leads* today (tier 1 of the sort key: what
       :func:`~.oebb_days._is_oebb_closure_day` counts as a closure today),
       of several the one whose window starts first. Operator decision
       2026-11-13 "Ja, Sperre des Tages zuerst": "Bei gleichem ÖBB-Titel
       hält die Sperre mit Sperrtag den Platz, das frühere Fenster rückt
       hinter das Feld." On 09.11.2026 from 04:31 to 06:31 the
       weekend-night window of "Wien Hauptbahnhof ↔ Gramatneusiedl" (31.10.
       to 30.11.; Monday does not count as one of its closure days) held
       the slot, and the item of the day ("Wegen Verlängerung der
       Bauarbeiten sind … bis voraussichtlich 05:30 Uhr keine Fahrten
       möglich") stood behind the field. The same order lets a closure of
       more than four weeks past its first week (decision 2026-11-12,
       :mod:`.oebb_days`), which as the earliest window of its title would
       hold the slot until it ends, give the slot to a weekend closure of
       the same route in its closure nights.
       Tier 1 is wider than a closure in the strict sense: an ÖBB text that
       names no day counts on every day of its period, also "waren bis
       06:34 Uhr nur eingeschränkt Fahrten möglich". And it is narrower: a
       closure of more than four weeks past its first week is a notice.
    3. Else the member whose window starts first: the phase that is running
       or comes next. A window that is only announced holds no slot in
       front (:func:`_defer_upcoming_items` moves it back afterwards), which
       is why the place in the sorted order alone does not decide.

    Among several members that lead, the window decides and not the place
    in the sorted order (independent check 2026-11-13): the build writes a
    state entry only for what it shows, so the member behind the field
    would count as just appeared in the next build and take the slot back,
    and a newer report that closes nothing ("nur eingeschränkt") would
    stand before a closure in its closure night. Only two windows that
    start on the same day are still told apart by their place in the sorted
    order, as before (``tests/test_repeated_route_titles.py``). The
    earliest window also holds on a Sunday morning, where the weekend-night
    window has its closure day too: an overrun report of the day that leads
    like it stands behind the field. Operator decision 2026-11-13, card
    "Soll am Sonntagmorgen die Meldung des Tages vor dem Wochenendfenster
    stehen?": "Nein, Fenster behält den Platz" ("Es bleibt, wie es ist: An
    Samstag und Sonntag steht das Wochenendfenster vorn, die Meldung des
    Tages hinter dem Feld."). "Wie es ist" includes the current incident: a
    report that counts as one, by the rules or by the stored Gemini answer
    (``src.ki_entscheidung``, named on the card), holds the slot on that
    morning too. Not named by the card and unchanged: on the first day of
    the window both start on the same day and the place in the sorted order
    decides as above, so the newer report of the day stands in front, also
    during the first closure night (``tests/test_oebb_title_slot.py``).
    """
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        key = _repeated_route_title_key(item)
        if key is not None:
            groups.setdefault(key, []).append(index)

    def window_start(index: int) -> tuple[datetime, int]:
        when = _parse_datetime(items[index].get("starts_at"))
        return (_to_utc(when) if isinstance(when, datetime) else _FAR_FUTURE, index)

    deferred_indices: set[int] = set()
    for members in groups.values():
        if len(members) > 1:
            current = [m for m in members if is_current is not None and is_current(items[m])]
            leading = [m for m in members if leads is not None and leads(items[m])]
            lead = current[0] if current else min(leading or members, key=window_start)
            deferred_indices.update(m for m in members if m != lead)
    if not deferred_indices:
        return items
    log.info(
        "Gleiche ÖBB-Titel: %d weitere(s) Zeitfenster hinter das Feld gestellt.",
        len(deferred_indices),
    )
    kept = [item for index, item in enumerate(items) if index not in deferred_indices]
    deferred = [item for index, item in enumerate(items) if index in deferred_indices]
    return kept + deferred


# Wiener Linien published two test messages on 2026-09-23 that reached the
# German feed for one cycle each: "71/72: Dies ist eine Testmeldung" ("Dies
# ist ein Test") and "62: F57f Test" ("F57 f Test"). On a display with ten
# slots each took the place of a real disruption (audit 2026-09-26).
#
# The filter audit of 2026-10-03 replayed every cached WL item since
# September 2025 and found three more shapes that WL's test entries take:
# the compounds "Testfall" ("38: Testfall", "40: Testfall", "72A: Testfall"
# with "Grund: Testfall im Bereich .") and "Testtext" ("WLB: Testtext GSC200
# Test für LB / WLB und Lauftext"), and a run of "Test" longer than five
# words ("Test Test Tes Test Test Test"). Neither compound is a word a real
# notice uses; "Testbetrieb" and "Testfahrt" stay out of the list.
_TEST_MESSAGE_RE = re.compile(
    r"\btest(?:meldung(?:en)?|f(?:a|ä)lle?|texte?)\b", re.IGNORECASE
)
_TEST_WORD_RE = re.compile(r"(?<![\w-])test(?![\w-])", re.IGNORECASE)
# "Tes" in "Test Test Tes Test" is a typo of the same word.
_TEST_RUN_WORD_RE = re.compile(r"^tes?t?$", re.IGNORECASE)
# A text this short that says "Test" is a test. A real message that mentions
# one says more ("Test-Fahrten der neuen Straßenbahn zwischen … und …"), and
# "Haltestelle" or "Testbetrieb" never match the word. Neither does a
# compound with a hyphen: "U2: Test-Fahrten neuer Züge" and "D:
# Covid-Test-Straße Haltestelle verlegt" are four words each and real
# (fund C, 2026-10-02); a bare "\b" took the hyphen for a word end.
MAX_TEST_TEXT_WORDS = 5


def _is_test_message(item: FeedItem) -> bool:
    """Whether ``item`` is a provider's test message rather than a disruption."""
    title = str(item.get("title") or "")
    text = html_to_text(str(item.get("description") or ""))
    if _TEST_MESSAGE_RE.search(title) or _TEST_MESSAGE_RE.search(text):
        return True
    return any(
        _TEST_WORD_RE.search(part) is not None
        and (len(part.split()) <= MAX_TEST_TEXT_WORDS or _only_test_words(part))
        for part in (title, text)
    )


def _only_test_words(text: str) -> bool:
    """Whether ``text`` says nothing but "Test", after an optional line prefix."""
    words = re.findall(r"[^\W\d_]+", text.split(":", 1)[-1])
    return bool(words) and all(_TEST_RUN_WORD_RE.match(w) for w in words)


def _drop_test_messages(items: list[FeedItem]) -> list[FeedItem]:
    """Remove providers' test messages before they can take a slot."""
    kept: list[FeedItem] = []
    for item in items:
        if _is_test_message(item):
            log.warning("Testmeldung verworfen: %s", sanitize_log_arg(str(item.get("title") or "")))
        else:
            kept.append(item)
    return kept


def _starts_after(item: FeedItem, last_day: date) -> bool:
    """Whether ``item`` begins on a Vienna day after *last_day*."""
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime):
        return False
    return _to_utc(start).astimezone(_VIENNA_TZ).date() > last_day


def _preview_last_day(today: date, preview_days: int) -> date:
    """The last Vienna day whose announcements keep their normal place.

    *preview_days* after *today*, but on a Friday and a Saturday at least the
    coming Monday (operator decision 2026-10-03, "Wochenende mit"): a detour
    from Monday must stand in front on Friday, for anyone who sees the
    display only on workdays. ``preview_days`` 0 keeps today only.
    """
    last_day = today + timedelta(days=preview_days)
    if preview_days > 0 and today.weekday() in (4, 5):  # Friday, Saturday
        last_day = max(last_day, today + timedelta(days=7 - today.weekday()))
    return last_day


def _defer_upcoming_items(
    items: list[FeedItem], now: datetime, preview_days: int
) -> list[FeedItem]:
    """Let an announced item take only a slot nothing running needs.

    The feed sorts newest first, and an announcement is new the day it
    appears. On 2026-10-02 at 18:00 five of the ten slots held items that had
    not begun — "20A: Bauarbeiten" from 13.10., the R 40 closure from 31.10.
    — while "N71: Ersatzverkehr" and "62: ÖBB Bauarbeiten", both valid that
    evening, stood on places 11 and 12. On a display someone reads in
    passing, what applies now comes first.

    Every item that begins after the Vienna day *preview_days* from today
    moves behind the field, in its original order — after the items
    :func:`_apply_topic_budget` moved back, before the all-clears. Nothing is
    dropped: with fewer running items the announcement still fills a slot,
    and from *preview_days* before its start it holds its normal place, from
    Friday on for one that begins on Monday (:func:`_preview_last_day`). An
    item without a start is never deferred.
    """
    last_day = _preview_last_day(_to_utc(now).astimezone(_VIENNA_TZ).date(), preview_days)
    deferred = [item for item in items if _starts_after(item, last_day)]
    if not deferred:
        return items
    log.info(
        "Angekündigte Meldungen: %d hinter das Feld gestellt (Beginn nach %s).",
        len(deferred),
        last_day.strftime("%d.%m.%Y"),
    )
    return [item for item in items if not _starts_after(item, last_day)] + deferred


def _defer_all_clear_items(items: list[FeedItem]) -> list[FeedItem]:
    """Let an all-clear take only a slot nothing else needs.

    ÖBB publishes the end of a disruption as its own message ("Aufhebung
    Verkehrseinschränkung: St. Pölten Hauptbahnhof"). It is new, so the FIFO
    sort puts it on top, and on a display with ten slots it held one for about
    an hour, in place of a running disruption (audit 2026-09-25, A.2: three
    such items since August).

    Operator decision 2026-09-25: a running disruption matters more than an
    all-clear, but an all-clear is better than an empty slot. So every
    all-clear moves behind the whole field, in its original order — after the
    items :func:`_defer_repeated_route_titles` and :func:`_apply_topic_budget`
    moved back, too. Nothing is dropped: with fewer than ``MAX_ITEMS`` other
    items the all-clear still fills a slot.
    """
    deferred = [item for item in items if _is_all_clear(item)]
    if not deferred:
        return items
    log.info("Entwarnungen: %d hinter das Feld gestellt.", len(deferred))
    return [item for item in items if not _is_all_clear(item)] + deferred
