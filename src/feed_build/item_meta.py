"""Feed order and the per-item data: links, sources, images and the state update.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET  # nosec B405
from datetime import datetime, timedelta
from typing import Any, cast, NamedTuple
from urllib.parse import quote, urlparse

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..utils.http import validate_http_url
from ..utils.logging import sanitize_log_arg
from ..utils.stats import append_disruption_row, extract_location_name
from ..providers.wl_plausibility import end_unknown

from .base import MEDIA_NS, _parse_datetime, _parse_first_seen, _parse_state_time, _to_utc, log
from .state import _identity_for_item, _lookup_state
from .time_line import _first_published, _incident_since
from .title_words import _is_baustelle, _is_wl_notice
from .item_history import _NEW_OCCURRENCE_KEY, _initial_first_seen, _note_announced_starts

__all__ = [
    "_sort_moment", "_CURRENT_INCIDENT_WINDOW", "_is_current_incident", "_recency_sort_key",
    "_build_canonical_link", "_ITEM_IMAGE_URL", "_ITEM_IMAGE_BYTES", "_ITEM_IMAGE_WIDTH", "_ITEM_IMAGE_HEIGHT",
    "_add_item_image", "_shown_end", "_item_source", "_source_item_link", "_cdata_content",
    "_placeholder_collides_with_formatted", "FormattedContent", "_update_item_state",
]


def _sort_moment(entry: dict[str, Any] | None, first_seen: datetime, now_utc: datetime) -> datetime:
    """The moment an item counts as new for the feed order.

    ``first_seen``, or the ``announced_start`` stored by
    :func:`_note_announced_starts` once it has come: an announced measure
    leads on the day it begins and then ages like any other item. Before
    its start :func:`_defer_upcoming_items` decides its place.
    """
    announced = _parse_state_time(entry, "announced_start")
    if announced is not None and first_seen < announced <= now_utc:
        return announced
    return first_seen


# Feed-ordering priority by category, applied as a tie-breaker when several
# items share the same ``first_seen``. Lower rank sorts first. ``Störung``
# (WL/ÖBB live disruptions AND the Stammstrecke delay/cancellation monitor,
# whose ``EVENT_CATEGORY`` is also ``"Störung"``) leads; ``Baustelle``
# (long-running construction) trails; every other category (e.g. WL
# ``Hinweis``) sits between. Keyed on the casefolded category so a provider
# emitting ``"störung"`` still matches. Operator policy (2026-05): disruptions
# matter more for the feed than construction.
_CATEGORY_FEED_RANK: dict[str, int] = {
    "störung": 0,
    "baustelle": 2,
}
_DEFAULT_CATEGORY_FEED_RANK = 1


def _category_feed_rank(item: FeedItem) -> int:
    """Return the feed-priority rank for *item*'s category (lower sorts first)."""
    category = item.get("category")
    if isinstance(category, str):
        return _CATEGORY_FEED_RANK.get(
            category.strip().casefold(), _DEFAULT_CATEGORY_FEED_RANK
        )
    return _DEFAULT_CATEGORY_FEED_RANK


# How long an unplanned disruption counts as current for the feed order —
# see :func:`_is_current_incident`.
_CURRENT_INCIDENT_WINDOW = timedelta(hours=24)


def _is_current_incident(
    item: FeedItem, entry: dict[str, Any] | None, first_seen: datetime, now_utc: datetime
) -> bool:
    """Whether ``item`` is an unplanned disruption that is happening now.

    Operator decision 2026-10-04: "Aktuelle Störungen sollen oberste
    Priorität haben. Vorangekündigte Baustellen sollen eine niedrigere
    Priorität haben." The FIFO sort alone let a fresh announcement or a
    notice newly let through the filter stand above running incidents
    (replayed builds, Vienna time): on 2026-10-03 at 15:01 "43A: Veranstaltung" stood above "9A:
    Rettungseinsatz", on 2026-10-04 at 16:30 "U6: Neue Donau, kein Halt"
    above four incidents (U1, 9, 18, 48A).

    An incident is what the time line shows as "[Seit hh:mm]"
    (:func:`_incident_since`: category "Störung", not planned, not reported
    as over) and has begun. It counts as current for
    ``_CURRENT_INCIDENT_WINDOW`` after it entered the feed: long-running
    items that read like incidents ("18: Haltestelle Stadionbrücke …
    aufgelassen" since 13.07., the daily "S80: ÖBB-Ersatzbus" since 25.09.)
    stay in the normal order.
    """
    if first_seen < now_utc - _CURRENT_INCIDENT_WINDOW:
        return False
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime) or _to_utc(start) > now_utc:
        return False
    published = _first_published(item, dict(entry) if entry else None)
    return _incident_since(item, start, published) is not None


def _baustelle_start_cap(item: FeedItem, moment: datetime, now_utc: datetime) -> datetime:
    """A Stadt Wien Baustelle counts as new no later than on its start day.

    The Baustellen GUID carries the end date, and a site whose end has passed
    leaves the state. When the city extends a site, it returns with a new
    GUID and counted as brand-new: "Burggasse 67", running since 28.07., end
    moved from 02.10. to 30.10. and listed again on 08.10., stood on place 9
    as the newest item of the feed. Its start says what it is: works running
    since July. Like a re-issued WL measure, it keeps its place. A site seen
    before its start is unaffected (:func:`_note_announced_starts`).
    """
    if not _is_baustelle(item):
        return moment
    start = _parse_datetime(item.get("starts_at"))
    if not isinstance(start, datetime):
        return moment
    start_utc = _to_utc(start)
    if start_utc <= now_utc and start_utc < moment:
        return start_utc
    return moment


def _recency_sort_key(
    item: FeedItem, state: dict[str, dict[str, Any]], now_utc: datetime
) -> tuple[int, float, int, float, str]:
    """FIFO-by-``first_seen`` ordering for the feed: newest-appeared first.

    Current incidents (:func:`_is_current_incident`) come before everything
    else; within each of the two groups the rules below apply.

    Sorts by the persisted (guid-stable) ``first_seen`` descending. An item
    not yet in the state counts as just-appeared (``now``) so genuinely new
    disruptions lead. An announced measure counts from its start once that
    has come (:func:`_sort_moment`). No-longer-valid items are already removed upstream by
    :func:`_drop_old_items`, so the visible Top-N are the newest still-valid
    disruptions; older ones fall off the bottom as fresher ones arrive.

    Ties on ``first_seen`` (a batch of items that surfaced in the SAME build —
    e.g. a fresh set of construction sites that all get ``first_seen = now``)
    are broken, in order, by:

    1. **Category priority** (:func:`_category_feed_rank`) — live disruptions
       (``Störung``: WL/ÖBB plus the Stammstrecke delay/cancellation monitor)
       outrank long-running ``Baustelle`` items; everything else (e.g. WL
       ``Hinweis``) sits between. Operator policy: Störungen are more important
       for the feed than Baustellen.
    2. **``pubDate`` descending** — the source's own recency signal; the more
       recently published item leads. A missing/unparseable ``pubDate`` sorts
       last within its group (``-inf`` → ``+inf`` after negation).
    3. **guid** — final deterministic tiebreaker for a stable total order
       (otherwise items tying on all of the above would shuffle between builds).
    """
    _, entry = _lookup_state(item, state)
    first_seen = _parse_first_seen(entry, None) or _initial_first_seen(item, now_utc)
    first_seen = _sort_moment(entry, first_seen, now_utc)
    first_seen = _baustelle_start_cap(item, first_seen, now_utc)
    pub = _parse_datetime(item.get("pubDate"))
    pub_ts = pub.timestamp() if isinstance(pub, datetime) else float("-inf")
    # Clamp a future pubDate to now: a bogus future publication date must not
    # rank an item ahead of genuinely-current items in this pubDate tiebreaker.
    pub_ts = min(pub_ts, now_utc.timestamp())
    guid_val = item.get("guid")
    guid_str = str(guid_val) if guid_val else _identity_for_item(item)
    tier = 0 if _is_current_incident(item, entry, first_seen, now_utc) else 1
    return (tier, -first_seen.timestamp(), _category_feed_rank(item), -pub_ts, guid_str)


def _build_canonical_link(candidate: Any, ident: str) -> str:
    """Return a canonical link for ``ident`` with a stable fallback anchor."""

    if isinstance(candidate, str):
        normalized = candidate.strip()
        if normalized:
            return normalized

    slug_source = ident or ""
    slug = quote(slug_source, safe="")
    base = (feed_config.FEED_LINK or "").strip()
    if not base:
        return f"#meldung-{slug}" if slug else ""

    anchor_prefix = "meldung"
    base = base.rstrip("/")
    if slug:
        return f"{base}#{anchor_prefix}-{slug}"
    return base


def _resolve_item_link(candidate: Any, ident: str) -> str:
    """Return the per-item ``<link>`` value, enforcing HTTPS-only.

    Security: ``validate_http_url`` accepts both ``http`` and ``https``.
    Without an HTTPS-only pin at this boundary a future upstream
    regression (legitimate or attacker-injected) that returned
    ``http://`` URLs would publish plaintext ``<link>`` elements into
    ``docs/feed.xml``. Every subscriber's RSS reader follows the
    ``<link>`` click via a fresh request and many do NOT consult the
    HSTS preload list before the click — a plaintext URL is therefore
    a documented TLS-strip primitive on the subscriber base.

    Three-step resolution mirrors the canonical pattern used by
    ``validate_public_feed_url`` (which the ``feed_config.FEED_LINK``
    fallback is itself validated by):

    1. Build canonical link via ``_build_canonical_link``.
    2. Pass through ``validate_http_url`` (SSRF / syntax / scheme).
    3. Reject ``http://`` results — fall back to the HTTPS-pinned
       ``feed_config.FEED_LINK`` so the published feed never carries
       ``<link>http://...</link>``.

    Mirrors the HTTPS-only pin from the 2026-05-09 *Public Feed URL
    Allow-List Drift* round and the 2026-05-10 *HTTPS-only Provider
    URL Drift* rounds (PRs #1415 / #1416) for every other publishing
    surface.
    """
    raw_link = _build_canonical_link(candidate, ident)
    sanitized = validate_http_url(raw_link, check_dns=False) if raw_link else ""
    if sanitized and not sanitized.lower().startswith("https://"):
        log.warning(
            "Item %s carries plaintext http:// link; falling back to "
            "feed link to prevent TLS-strip on subscribers.",
            ident,
        )
        sanitized = ""
    if raw_link and not sanitized:
        log.warning(
            "Item %s has potentially unsafe/invalid link %r; falling back to feed link.",
            ident,
            raw_link,
        )
    return sanitized or feed_config.FEED_LINK


@dataclasses.dataclass(frozen=True, slots=True)
class _ItemSource:
    """Who an item comes from: its ``<source>`` tag and its link target.

    ``hosts`` are the domains an item link may point to; any other link
    (a dead catalogue page, another operator's site) is replaced by
    ``link``, the operator's own page for its current notices.
    """

    name_de: str
    name_en: str
    url: str
    hosts: tuple[str, ...]
    link: str


_OEBB_STRECKENINFO_URL = (
    "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"
)
_WL_BETRIEBSINFO_URL = "https://www.wienerlinien.at/betriebsinfo"
_WIEN_BAUSTELLEN_URL = "https://www.wien.gv.at/verkehr/baustellen"

# Michael, 17.10.2026: "Meldungen von der ÖBB sollen zu ÖBB verlinken und eine
# Quellenangabe als Tag beinhalten. Meldungen der Wiener Linien sollen zu den
# Wiener Linien verlinken und eine Quellenangabe als Tag für die Wiener Linien
# haben. Das Selbe für Baustellenmeldungen der Stadt Wien u.s.w." So every
# item links to the operator it is about and carries an RSS ``<source>``
# naming where it comes from. The Stammstrecke monitor reports ÖBB S-Bahn
# trains, so it links to the ÖBB-Streckeninformation like the ÖBB notices
# (his yes 17.10.2026), while its tag names the data behind it, VOR/VAO.
# Keyed by the item's ``source`` field; an unknown source keeps its link and
# gets no tag.
_ITEM_SOURCES: dict[str, _ItemSource] = {
    "Wiener Linien": _ItemSource(
        "Wiener Linien",
        "Wiener Linien",
        _WL_BETRIEBSINFO_URL,
        ("wienerlinien.at",),
        _WL_BETRIEBSINFO_URL,
    ),
    "ÖBB": _ItemSource(
        "ÖBB", "ÖBB", _OEBB_STRECKENINFO_URL, ("oebb.at",), _OEBB_STRECKENINFO_URL
    ),
    "Stadt Wien – Baustellen": _ItemSource(
        "Stadt Wien",
        "City of Vienna",
        _WIEN_BAUSTELLEN_URL,
        ("wien.gv.at",),
        _WIEN_BAUSTELLEN_URL,
    ),
    "VOR/VAO": _ItemSource(
        "VOR/VAO",
        "VOR/VAO",
        "https://www.verkehrsauskunft.at/start",
        ("oebb.at",),
        _OEBB_STRECKENINFO_URL,
    ),
}


# Michael, 19.10.2026: the TV slides (EasySignage) shall all look the same
# grey; he does not like the red Wiener-Linien background. Without an image of
# its own, EasySignage shows the preview image (og:image) of the item link, and
# wienerlinien.at/betriebsinfo serves either a missing "image.png" (grey slide)
# or the red WL logo (red slide), at random. So every item of every source
# carries the same image of our own as ``<enclosure>`` and ``<media:content>``;
# under EasySignage's dark layer white reads as the grey he chose. The links
# stay with their operators (see ``_ITEM_SOURCES``).
# Michael, 23.10.2026: the grey gets a woodchip-wallpaper ("Raufaser") look in
# Full HD, in the same grey. The image is a near-white woodchip wall computed
# by scripts/make_feed_item_image.py (no photo); under the dark layer its
# median is the former grey, the chips show as soft shadows.
# ``_ITEM_IMAGE_BYTES`` is the file size RSS 2.0 asks for in ``length``;
# tests/test_item_image.py keeps it equal to the file in docs/assets/.
_ITEM_IMAGE_URL = "https://origamihase.github.io/wien-oepnv/assets/feed-item-raufaser.jpg"
_ITEM_IMAGE_BYTES = 281823
_ITEM_IMAGE_TYPE = "image/jpeg"
_ITEM_IMAGE_WIDTH = 1920
_ITEM_IMAGE_HEIGHT = 1080


def _add_item_image(item: ET.Element) -> None:
    """Attach the shared item image (see ``_ITEM_IMAGE_URL``)."""
    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", _ITEM_IMAGE_URL)
    enclosure.set("length", str(_ITEM_IMAGE_BYTES))
    enclosure.set("type", _ITEM_IMAGE_TYPE)
    media = ET.SubElement(item, f"{{{MEDIA_NS}}}content")
    media.set("url", _ITEM_IMAGE_URL)
    media.set("type", _ITEM_IMAGE_TYPE)
    media.set("medium", "image")
    media.set("width", str(_ITEM_IMAGE_WIDTH))
    media.set("height", str(_ITEM_IMAGE_HEIGHT))


def _shown_end(it: FeedItem, ends_at: object) -> datetime | None:
    """The end the item's time line names, or ``None``.

    WL's 11:11 expiry date is no end when the text names none of its own
    (:func:`wl_plausibility.end_unknown`, operator decision 2026-10-08). The
    time line leaves it out, and so does ``ext:ends_at``, which the website
    shows as "Bis": "48A: Neubaugasse, Neustiftgasse" read "[Seit 19.10.]" on
    the TV and "Bis: 08.10.2027, 11:11" on the website. The item keeps the
    date as its expiry inside the pipeline.
    """
    end = ends_at if isinstance(ends_at, datetime) else None
    if _is_wl_notice(it) and end_unknown(str(it.get("description") or ""), end):
        return None
    return end


def _item_source(it: Any) -> _ItemSource | None:
    """Return the :class:`_ItemSource` for item ``it`` or ``None`` when unknown."""
    source = it.get("source")
    return _ITEM_SOURCES.get(source) if isinstance(source, str) else None


def _link_on_hosts(link: str, hosts: tuple[str, ...]) -> bool:
    """Return whether ``link`` points to one of ``hosts`` or a subdomain."""
    host = (urlparse(link).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def _source_item_link(it: Any, ident: str) -> str:
    """Return the item ``<link>``: the resolved link while it stays with the
    item's operator, else the operator's own notices page."""
    link = _resolve_item_link(it.get("link"), ident)
    src = _item_source(it)
    if src is not None and not _link_on_hosts(link, src.hosts):
        return src.link
    return link


def _cdata_content(s: str) -> str:
    """Prepare a string for inclusion in a CDATA block, handling ']]>'."""
    return s.replace("]]>", "]]]]><![CDATA[>")


def _placeholder_collides_with_formatted(
    ph_content: str, ph_title: str, formatted: FormattedContent
) -> bool:
    """Return True if either placeholder appears in any text-bearing field
    of *formatted*.

    Security (CDATA placeholder collision drift closure):
    ``_emit_item`` injects ``ph_content`` / ``ph_title`` as the ``.text``
    of ``<content:encoded>`` / ``<title>`` ElementTree elements and
    later substitutes them via a global ``xml_str.replace(...)`` pass
    for CDATA-wrapped content. The downstream replace is element-agnostic
    — any occurrence of either placeholder ANYWHERE in the serialised
    XML gets replaced, including inside ``<link>`` / ``<guid>`` /
    ``<description>`` element text whose values come from upstream-
    controlled fields. Pre-fix the loop only verified absence in three
    of the eight ``FormattedContent`` text fields (``desc_html``,
    ``raw_desc``, ``title_out``); the remaining five (``link``, ``guid``,
    ``desc_text_truncated``, ``title_cdata``, ``desc_cdata``) leaked
    upstream-controlled spans into the placeholder-replacement target
    set without a collision check. An upstream item whose ``link`` or
    ``guid`` coincidentally matched the random placeholder pattern
    would corrupt the serialised RSS XML (CDATA injected into the
    wrong element, original element text consumed by the replacement).
    Practical exploitability requires predicting a 128-bit UID
    (astronomically low for a remote attacker), but the project's
    Zero-Trust upstream contract (AGENTS.md §3) demands defense-in-
    depth at every upstream-data boundary regardless of practical
    exploitability — a legitimate URL that happens to embed the
    placeholder pattern (highly unlikely yet possible) would silently
    corrupt the public feed. The check examines every text-bearing
    ``FormattedContent`` field, mirroring the ``isinstance`` Zero-Trust
    guards added at every other upstream JSON-parse site
    (``src/providers/vor.py``, ``src/providers/wl_fetch.py``).
    """
    text_fields = (
        formatted.link,
        formatted.guid,
        formatted.title_out,
        formatted.title_cdata,
        formatted.desc_html,
        formatted.desc_cdata,
        formatted.desc_text_truncated,
        formatted.raw_desc,
    )
    return any(ph_content in field or ph_title in field for field in text_fields)


class FormattedContent(NamedTuple):
    guid: str
    link: str
    title_cdata: str
    desc_text_truncated: str
    desc_cdata: str
    raw_desc: str
    title_out: str
    desc_html: str


def _update_item_state(it: FeedItem, now: datetime, state: dict[str, dict[str, Any]]) -> tuple[str, datetime]:
    ident, st = _lookup_state(it, state)
    is_strictly_new = not st
    if not st:
        st = {"first_seen": _initial_first_seen(it, _to_utc(now)).isoformat()}
    # A recurring WL disruption keeps its entry (the guid holds no date) but
    # begins a new occurrence (:func:`_restart_recurring_occurrences`). It is
    # a new disruption for the statistics too: "U6: Rettungseinsatz" on
    # 25.10. is not the one of 23.10. From 25.09. (when the restart came) to
    # 27.10.2026 1,060 such occurrences reached the feed uncounted, the old rule
    # counted 348 in the same replay of all 1,690 builds); the rows were added to
    # the ledger afterwards (data/stats/README.md).
    is_new_occurrence = is_strictly_new or bool(st.pop(_NEW_OCCURRENCE_KEY, False))
    state[ident] = st
    _note_announced_starts([it], state, now)
    # Legacy-key migration cleanup: ``_lookup_state`` returns the modern
    # guid-shaped key in ``ident``, but on a guid-key miss it falls back
    # to the legacy ``_identity_for_item`` entry to migrate forward. Pre-
    # fix we wrote the entry under the new guid key but left the legacy
    # entry on disk; ``STATE_RETENTION_DAYS`` (60d) bounded the bloat,
    # but every subsequent build kept comparing the same now-redundant
    # legacy key in ``_lookup_state``'s fallback path until the prune
    # eventually fired. Dropping the legacy entry once the migration has
    # written the new key keeps ``data/first_seen.json`` tidy and saves
    # the legacy-lookup cost on every cycle.
    legacy = _identity_for_item(it)
    if legacy != ident and legacy in state:
        state.pop(legacy, None)

    try:
        fs_dt = datetime.fromisoformat(st["first_seen"])
    except Exception:
        log.warning("first_seen Parsefehler: %r – fallback to now", st.get("first_seen"))
        fs_dt = _to_utc(now)
        st["first_seen"] = fs_dt.isoformat()

    # Stats: log the first appearance of each occurrence of a disruption
    # into ``data/stats/stoerungen_YYYY.csv``: a state-cache miss above
    # (neither ``ident`` nor ``guid`` had a prior entry) or an entry marked
    # as a new occurrence. Reruns of the same occurrence — a long-lived ÖBB
    # Streckeninformation that survives many feed builds — record a single
    # row. Best-effort: any I/O failure inside ``append_disruption_row`` is
    # logged and swallowed so the feed build itself never blocks on stats I/O.
    if is_new_occurrence:
        try:
            append_disruption_row(
                timestamp=_to_utc(now),
                provider=str(it.get("source") or "unbekannt"),
                location_name=extract_location_name(cast(dict[str, Any], it)),
            )
        except Exception as exc:  # pragma: no cover - defensive; writer is no-throw
            log.warning(
                "Disruption-Stats konnten nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )
    return ident, fs_dt
