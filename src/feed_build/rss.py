"""The RSS document: GeoRSS geometry, one ``<item>`` (``_emit_item``), the channel
metadata and licence lines, and ``_make_rss``.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
import secrets
import xml.etree.ElementTree as ET  # nosec B405
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlparse

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..providers.wl_text import STOP_NOTICE_LEAD_RE
from ..utils.text import html_to_text

from .base import ATOM_NS, GEORSS_NS, _coerce_datetime_field, _fmt_rfc2822, _sanitize_text, _to_utc
from .display_titles import _section_collisions
from .title_words import _is_baustelle, _short_title_collisions
from .item_meta import (
    _add_item_image, _item_source, _placeholder_collides_with_formatted, _shown_end, _update_item_state,
)
from .oebb_lead import _OEBB_TITLE_LINES_RE
from .item_format import _escape_description_markup, _format_item_content

__all__ = [
    "_item_geometry", "_emit_item", "_channel_metadata", "_make_rss",
]


# GeoRSS (operator decision 2026-10-16, "Eine präzise Variante würde das
# Projekt aufwerten. Falsche Daten bringen uns hingegen nichts."): an item
# carries a ``<georss:point>`` or ``<georss:line>`` only where its place is
# certain, and most items carry none, by design.
#
# Measured over every notice title since 2026-07-01 (reports/
# georss-einschaetzung-2026-10-16.md): taking every station a text names, in
# text order, draws wrong shapes for Wiener Linien — 15 % of the names follow
# "Richtung" (the line's terminus, not the place), 26 % of the lines zigzag
# through detours and replacement lines, street names hit stops named after
# them ("Simmeringer Hauptstraße" is the stop Hauptwerkstätte). So:
#
# * ÖBB: the title only. One station → point; exactly one "A ↔ B" → a line
#   from A to B (a straight segment between the two stations, never a
#   computed midpoint). A title with several sections ("A ↔ B / C ↔ D"), a
#   chain of three or more, or any part that is not exactly a station name
#   gets nothing.
# * Stadt Wien Baustellen: the site's own coordinate from the city's data
#   (the first point of its geometry), only inside Vienna.
# * Wiener Linien: only a stop relocation or closure ("15A: Max-Mauermann-
#   Gasse" with "Haltestellenverlegung …"), whose title names exactly one stop
#   that the directory lists for one of the title's lines. Every other WL
#   notice — the large majority — gets nothing.
#
# Coordinates come only from open datasets with known terms (ÖBB-Infrastruktur
# GeoNetz under CC BY 3.0 AT, Wiener Linien OGD and Stadt Wien OGD under
# CC BY 4.0), never from the
# directory's merged OSM / HAFAS values
# (:func:`src.utils.stations.open_data_coordinate`). A station without an
# open coordinate leaves the item without geo.

_WL_STOP_TITLE_RE = re.compile(r"^([A-Za-z0-9]+(?:/[A-Za-z0-9]+)*):\s*(\S.*)$")

_Coord = tuple[float, float]


def _oebb_title_geometry(title: str) -> tuple[_Coord, ...] | None:
    from ..utils.stations import open_data_coordinate, station_named_in_text

    rest = _OEBB_TITLE_LINES_RE.sub("", title.strip(), count=1)
    if not rest or "/" in rest or ":" in rest:
        return None
    parts = [part.strip() for part in rest.split("↔")]
    if len(parts) > 2 or not all(parts):
        return None
    names: list[str] = []
    for part in parts:
        info = station_named_in_text(part)
        if info is None:
            return None
        if info.name not in names:
            names.append(info.name)
    coords: list[_Coord] = []
    for name in names:
        hit = open_data_coordinate(name)
        if hit is None:
            return None
        coords.append((hit.latitude, hit.longitude))
    return tuple(coords)


def _wl_stop_notice_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    from ..utils.stations import open_data_coordinate, station_named_in_text, station_wl_lines

    if not STOP_NOTICE_LEAD_RE.match(html_to_text(str(item.get("description") or ""))[:200]):
        return None
    match = _WL_STOP_TITLE_RE.match(str(item.get("title") or "").strip())
    if match is None:
        return None
    info = station_named_in_text(match.group(2).strip())
    if info is None:
        return None
    if not set(match.group(1).split("/")) & station_wl_lines(info.name):
        return None
    hit = open_data_coordinate(info.name, prefer="wl")
    return ((hit.latitude, hit.longitude),) if hit else None


def _baustelle_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    from ..utils.stations import is_in_vienna

    location = cast(dict[str, Any], item).get("location")
    coordinates = location.get("coordinates") if isinstance(location, dict) else None
    if not isinstance(coordinates, dict):
        return None
    lat, lon = coordinates.get("lat"), coordinates.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if not is_in_vienna(lat, lon):
        return None
    return ((float(lat), float(lon)),)


def _item_geometry(item: FeedItem) -> tuple[_Coord, ...] | None:
    """The certain place of ``item`` as one point or a two-point line, or ``None``.

    See the GeoRSS block comment above for the rules per source.
    """
    source = str(item.get("source") or "").strip()
    if source == "ÖBB":
        return _oebb_title_geometry(str(item.get("title") or ""))
    if source == "Wiener Linien":
        return _wl_stop_notice_geometry(item)
    if _is_baustelle(item):
        return _baustelle_geometry(item)
    return None


def _georss_coords(coords: tuple[_Coord, ...]) -> str:
    return " ".join(f"{lat:.6f} {lon:.6f}" for lat, lon in coords)


def _emit_item(
    it: FeedItem,
    now: datetime,
    state: dict[str, dict[str, Any]],
    *,
    lang: str = "de",
    split_reason: bool = True,
    drop_section: bool = True,
) -> tuple[str, ET.Element, dict[str, str]]:
    """Convert a normalized item dictionary into an RSS <item> element and CDATA replacements.

    Args:
        it: The normalized item dictionary.
        now: The current datetime (used for relative time calculations).
        state: The state dictionary (used to persist first_seen timestamps).
        lang: Target output language (``"de"`` or ``"en"``). When ``"en"``
            the formatter applies a translation overlay (cached in
            ``state[ident]["translations"]["en"]``).
        split_reason: Whether a WL ticker title may shrink to its cause
            (see :func:`_finish_reason_title`); ``False`` for an item whose
            short title another visible item already has.
        drop_section: Whether a long Baustellen title may move its section
            into the description (see :func:`_baustellen_display`); ``False``
            when another visible item would then read the same.

    Returns:
        A tuple containing:
         - The item identity (str)
         - The generated ElementTree.Element
         - A dictionary mapping placeholder strings to their CDATA-wrapped content.
    """
    it_dict = cast(dict[str, Any], it)
    pubDate = _coerce_datetime_field(it_dict, "pubDate")
    starts_at = _coerce_datetime_field(it_dict, "starts_at")
    ends_at = _coerce_datetime_field(it_dict, "ends_at")

    ident, fs_dt = _update_item_state(it, now, state)

    formatted = _format_item_content(
        it,
        ident,
        starts_at if isinstance(starts_at, datetime) else None,
        ends_at if isinstance(ends_at, datetime) else None,
        lang=lang,
        state=state,
        split_reason=split_reason,
        drop_section=drop_section,
    )

    if not isinstance(pubDate, datetime) and feed_config.FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=feed_config.FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # Generate unique placeholders.
    # We use a cryptographically secure random token to ensure uniqueness within the document.
    # ``_placeholder_collides_with_formatted`` verifies the candidate
    # placeholders do not appear in ANY of the eight text-bearing
    # ``FormattedContent`` fields — closing the upstream-controlled
    # collision gap on ``link`` / ``guid`` / ``desc_text_truncated`` /
    # ``title_cdata`` / ``desc_cdata`` that the pre-fix inline check
    # missed (only ``desc_html`` / ``raw_desc`` / ``title_out`` were
    # examined).
    max_attempts = 100
    attempts = 0
    while True:
        if attempts >= max_attempts:
            raise RuntimeError("Konnte keinen eindeutigen Platzhalter generieren")
        uid = secrets.token_hex(16)
        PH_CONTENT = f"___CDATA_CONTENT_{uid}___"
        PH_TITLE = f"___CDATA_TITLE_{uid}___"
        if not _placeholder_collides_with_formatted(PH_CONTENT, PH_TITLE, formatted):
            break
        attempts += 1

    # --- ElementTree Construction ---
    item = ET.Element("item")

    # Title
    ET.SubElement(item, "title").text = PH_TITLE

    # Link
    ET.SubElement(item, "link").text = formatted.link

    # GUID
    guid_elem = ET.SubElement(item, "guid")
    guid_elem.text = formatted.guid

    # guid attributes (isPermaLink)
    parsed = urlparse(formatted.guid)
    if not (parsed.scheme and parsed.netloc and formatted.guid == formatted.link):
        guid_elem.set("isPermaLink", "false")

    # pubDate
    if isinstance(pubDate, datetime):
        ET.SubElement(item, "pubDate").text = _fmt_rfc2822(pubDate)

    # Source tag (see ``_ITEM_SOURCES``): the name of the source, with its
    # page in the ``url`` attribute RSS 2.0 requires.
    item_source = _item_source(it_dict)
    if item_source is not None:
        source_elem = ET.SubElement(item, "source")
        source_elem.text = item_source.name_en if lang == "en" else item_source.name_de
        source_elem.set("url", item_source.url)

    # One plain image for every item, so TV slides stay uniformly grey.
    _add_item_image(item)

    # Extensions
    ET.SubElement(item, "{https://wien-oepnv.example/schema}first_seen").text = _fmt_rfc2822(fs_dt)

    if isinstance(starts_at, datetime):
        ET.SubElement(item, "{https://wien-oepnv.example/schema}starts_at").text = _fmt_rfc2822(starts_at)

    shown_end = _shown_end(it, ends_at)
    if shown_end is not None:
        ET.SubElement(item, "{https://wien-oepnv.example/schema}ends_at").text = _fmt_rfc2822(shown_end)

    # GeoRSS: one point or a line where the place is certain, else nothing
    # (see :func:`_item_geometry`). Language-independent, so DE and EN agree.
    geometry = _item_geometry(it)
    if geometry:
        tag = "point" if len(geometry) == 1 else "line"
        ET.SubElement(item, f"{{{GEORSS_NS}}}{tag}").text = _georss_coords(geometry)

    # Description
    # Security (stored HTML/JS injection on the public feed — ``<description>``
    # sibling of the ``<content:encoded>`` output-encoding fix): ``<description>``
    # is an XML TEXT node, so ElementTree escapes ``<>&`` for XML *well-formedness*
    # on serialise. That alone is NOT enough — a conformant RSS reader XML-decodes
    # the node exactly ONCE and the overwhelming majority then render the result
    # as HTML (RSS 2.0 ``<description>`` is HTML by convention; ``content:encoded``
    # was added only to carry the *full* body). ``desc_text_truncated`` carries
    # plain display text in which an upstream ``&lt;img onerror=…&gt;`` has already
    # been decoded by ``html_to_text`` into a live ``<img onerror=…>``; without
    # output-encoding here that tag would execute in the subscriber's reader after
    # its single XML-decode. Escape the markup characters at this sink so the
    # reader's lone XML-decode yields inert ``&lt;img…&gt;`` *source* text. This
    # is the single per-item ``<description>`` sink for both the DE and EN feeds
    # (``_emit_item`` is invoked once per language with the language-resolved
    # ``formatted``). ``&`` is deliberately NOT escaped here — see
    # :func:`_escape_description_markup` for why that is complete and why
    # escaping it was a display bug.
    ET.SubElement(item, "description").text = _escape_description_markup(
        formatted.desc_text_truncated
    )

    # content:encoded
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = PH_CONTENT

    replacements = {
        PH_CONTENT: f"<![CDATA[{formatted.desc_cdata}]]>",
        PH_TITLE: f"<![CDATA[{formatted.title_cdata}]]>",
    }

    return ident, item, replacements


# CC BY 4.0, section 3(a)(1): the attribution names the licence with its URI
# and says that the material was modified. The feed shortens and reformats the
# notice texts (titles, descriptions) and the English feed machine-translates
# them, so both channel lines say so and link the licence and NOTICE.md in
# full (a feed reader has no repository to resolve a bare file name against).
_CC_BY_4_URL = "https://creativecommons.org/licenses/by/4.0/"
# The ÖBB-Infrastruktur GeoNetz on data.oebb.at is CC BY 3.0 AT, not 4.0, and
# asks for the attribution "Datenquelle: ÖBB-Infrastruktur AG" with the name
# linked to infrastruktur.oebb.at (dataset page, read 17.10.2026). A feed
# line cannot carry a hyperlink, so the URL follows the name.
_CC_BY_3_AT_URL = "https://creativecommons.org/licenses/by/3.0/at/deed.de"
_OEBB_INFRA_URL = "https://infrastruktur.oebb.at/de/"
_NOTICE_URL = "https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md"

# VAO "Start" contract, point 5.3: name the VAO "Start" access as the basis
# of the Stammstrecke data (see the comment above ``_CHANNEL_COPYRIGHT_DE``).
_VAO_BASIS_DE = (
    "Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der "
    "Verkehrsauskunft Österreich VAO GmbH."
)
_VAO_BASIS_EN = (
    "S-Bahn trunk-line data: based on the VAO “Start” access of "
    "Verkehrsauskunft Österreich VAO GmbH."
)

# Michael, 17.10.2026 (licence audit): the channel says first that the project is unofficial and that
# the linked original notices are authoritative, so nobody reads the
# shortened or translated texts as an operator's own announcement. Channel
# level only; titles and texts on the EasySignage displays stay unchanged.
_UNOFFICIAL_DE = (
    "Inoffizielles Projekt ohne Verbindung zu Wiener Linien, ÖBB, Stadt Wien "
    "und VOR/VAO; maßgeblich sind die verlinkten Originalmeldungen."
)
_UNOFFICIAL_EN = (
    "Unofficial project, not affiliated with Wiener Linien, ÖBB, the City of "
    "Vienna or VOR/VAO; the linked original notices are authoritative."
)

# Channel-level metadata for the English feed mirror (docs/feed.en.xml).
# Kept as a module-level constant so the EN strings are reviewable in one
# place and never reach the upstream-controlled translation pipeline —
# channel metadata is build-time-owned, not item-derived.
_CHANNEL_METADATA_EN: dict[str, str] = {
    "title": "Vienna Public Transport — Disruptions & Commuter Info",
    "description": (
        "Active disruptions, construction works and restrictions from "
        "official sources (Wiener Linien, ÖBB, VOR/VAO, City of Vienna)."
    ),
    "language": "en",
    "copyright": (
        f"{_UNOFFICIAL_EN} "
        "Code: MIT licence. Notice texts remain with their sources and are "
        "shortened, edited and machine-translated for this feed: "
        "Wiener Linien and City of Vienna – data.wien.gv.at (CC BY 4.0, "
        f"{_CC_BY_4_URL}), ÖBB (© ÖBB, not openly licensed). GeoRSS coordinates: "
        f"Datenquelle: ÖBB-Infrastruktur AG ({_OEBB_INFRA_URL}; GeoNetz, CC BY 3.0 AT, "
        f"{_CC_BY_3_AT_URL}), Wiener Linien (OGD stops) and City of Vienna – "
        f"data.wien.gv.at (CC BY 4.0). {_VAO_BASIS_EN} Details: {_NOTICE_URL}"
    ),
}

# Michael, 16.10.2026: the code stays MIT, the notice texts are not ours to
# license. The channel names the sources and their terms once; titles and
# texts stay untouched, so nothing changes on the EasySignage displays (each
# item names its source in ``<source>`` since 17.10.2026, ``_ITEM_SOURCES``).
# VAO is named as the basis of the Stammstrecke data, not as a licensor:
# the Stammstrecke delay notice (``src.feed.stammstrecke``, ``<source>``
# VOR/VAO) is our own text computed from VAO departure data. The VAO "Start"
# contract (point 5.3) requires the service to state unmistakably that the
# VAO "Start" access is its basis; Michael chose on 17.10.2026 to carry that
# sentence in both feeds, the site footer, docs/statistik.md, README and
# NOTICE.md (docs/architecture.md §7). Should the contract end, point 7.7
# requires removing it again.
# The GeoRSS coordinates (:func:`_item_geometry`) come only from the three
# open datasets ``open_data_coordinate`` and the Baustellen data accept
# (ÖBB-Infrastruktur GeoNetz, Wiener Linien OGD stops, Stadt Wien), so the
# line names exactly those; never OSM, HAFAS or Google.
_CHANNEL_COPYRIGHT_DE = (
    f"{_UNOFFICIAL_DE} "
    "Code: MIT-Lizenz. Die Meldungstexte bleiben bei ihren Quellen und sind "
    "für den Feed gekürzt und bearbeitet: "
    "Wiener Linien und Stadt Wien – data.wien.gv.at (CC BY 4.0, "
    f"{_CC_BY_4_URL}), ÖBB (© ÖBB, keine offene Lizenz). GeoRSS-Koordinaten: "
    f"Datenquelle: ÖBB-Infrastruktur AG ({_OEBB_INFRA_URL}; GeoNetz, CC BY 3.0 AT, "
    f"{_CC_BY_3_AT_URL}), Wiener Linien (OGD-Haltestellen) und Stadt Wien – "
    f"data.wien.gv.at (CC BY 4.0). {_VAO_BASIS_DE} Details: {_NOTICE_URL}"
)


def _channel_metadata(lang: str) -> dict[str, str]:
    """Resolve channel-level metadata for ``lang`` (``"de"`` or ``"en"``)."""
    if lang == "en":
        return _CHANNEL_METADATA_EN
    return {
        "title": feed_config.FEED_TITLE,
        "description": feed_config.FEED_DESC,
        "language": "de",
        "copyright": _CHANNEL_COPYRIGHT_DE,
    }


def _make_rss(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
    *,
    lang: str = "de",
) -> str:
    """
    Generate the full RSS XML document from a list of items using ElementTree.

    Args:
        items: List of item dictionaries.
        now: Current timestamp.
        state: State dictionary for tracking items.
        lang: Target language for the output (``"de"`` or ``"en"``).
            Drives channel metadata, ``<language>``, the atom self
            ``href`` (``feed.xml`` vs ``feed.en.xml``) and the per-item
            translation overlay forwarded to :func:`_emit_item`.

    Returns:
        The generated RSS XML string with CDATA sections.
    """
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    metadata = _channel_metadata(lang)
    feed_filename = "feed.en.xml" if lang == "en" else "feed.xml"

    # Security: route the env-controlled FEED_TITLE / FEED_DESC through
    # the canonical ``_sanitize_text`` (``_CONTROL_RE`` strip — C0/C1
    # controls + DEL + BiDi format controls + zero-width chars + line
    # separators + BOM) before they land in the channel-level RSS XML.
    # Pre-fix the env vars flowed verbatim into ``<title>`` / ``<description>``,
    # letting a leaked CI env / compromised secret store / intentional
    # misconfig plant CVE-2021-42574 Trojan-Source primitives (RLO, LSEP,
    # ZWSP, …) into the published ``docs/feed.xml`` channel metadata —
    # the prominent "feed name" displayed by every subscriber's RSS
    # reader. Mirrors the per-item sinks already routed through the
    # helper (title / description / time-line / guid) so the closing-
    # checklist of the *Trojan-Source RSS Drift* family covers BOTH
    # per-item AND channel-level RSS sinks. ``FEED_LINK`` is already
    # pinned by ``validate_public_feed_url`` (HTTPS-only + GitHub host
    # allow-list + ``_UNSAFE_URL_CHARS`` strip) so its emission needs
    # no additional sanitisation. The EN strings come from the
    # module-level constant ``_CHANNEL_METADATA_EN`` and are still routed
    # through the sanitiser for defense-in-depth uniformity.
    ET.SubElement(channel, "title").text = _sanitize_text(metadata["title"])
    ET.SubElement(channel, "link").text = feed_config.FEED_LINK
    ET.SubElement(channel, "description").text = _sanitize_text(metadata["description"])

    # Atom self/alternate-Links + Sprache. Diese drei Tags wurden früher
    # vom Perl-basierten "Normalize feed metadata (SEO)"-Step in
    # .github/workflows/build-feed.yml nachträglich injiziert. Generierung
    # direkt im Python-Builder hält das XML strukturell wohlgeformt und
    # entfernt den Sprachen-Mix in CI.
    pages_base = feed_config.PAGES_BASE_URL.rstrip("/")
    atom_alternate = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_alternate.set("rel", "alternate")
    atom_alternate.set("type", "text/html")
    atom_alternate.set("href", f"{pages_base}/")
    atom_self = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_self.set("rel", "self")
    atom_self.set("type", "application/rss+xml")
    atom_self.set("href", f"{pages_base}/{feed_filename}")
    ET.SubElement(channel, "language").text = metadata["language"]
    ET.SubElement(channel, "copyright").text = metadata["copyright"]

    ET.SubElement(channel, "lastBuildDate").text = _fmt_rfc2822(now)
    ET.SubElement(channel, "ttl").text = str(feed_config.FEED_TTL)

    item_replacements: dict[str, str] = {}
    shown = items[: max(feed_config.MAX_ITEMS, 0)]
    # Decided on the visible items together, from the German titles, so DE
    # and EN shorten the same items.
    keep_long = _short_title_collisions(shown)
    keep_section = _section_collisions(shown)
    for index, it in enumerate(shown):
        _ident, elem, repl = _emit_item(
            it,
            now,
            state,
            lang=lang,
            split_reason=index not in keep_long,
            drop_section=index not in keep_section,
        )
        channel.append(elem)
        item_replacements.update(repl)

    # Pretty print the tree
    if hasattr(ET, "indent"):
        ET.indent(rss, space="  ", level=0)

    # Serialize to string using native ElementTree declaration
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    xml_str = xml_bytes.decode("utf-8")

    # Inject CDATA
    for placeholder, cdata in item_replacements.items():
        xml_str = xml_str.replace(placeholder, cdata)

    return cast(str, xml_str)
