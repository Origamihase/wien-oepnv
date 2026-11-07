"""What kind of long message a WL ``newsList`` text is, read from its lead.

WL names the category of a long message in its first words: as a heading
("<h2>Netzänderung</h2>", "<h2>Gleisbauarbeiten</h2>") or, in the older
format, as the lead of the first sentence ("Haltestellenverlegung der Linie
68A …"). :func:`lead_text` reads the first 2 000 characters as plain text,
so the rules here follow WL's own classification of the message and not
words anywhere in its body. ``wl_text.is_stop_notice`` reads the same lead.
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")

# A network change: a line gets a new stop or a new route ("9A: Errichtung
# einer Haltestelle" over "<h2>Netzänderung</h2> Die Linie 9A bekommt eine
# neue Haltestelle."). WL writes the heading in the singular; the plural and
# "Liniennetzänderung" count for the day it does not.
NETWORK_CHANGE_LEAD_RE = re.compile(r"^(?:linien)?netz(?:ä|ae)nderung(?:en)?\b", re.IGNORECASE)


def lead_text(description: str, limit: int = 2000) -> str:
    """The first *limit* characters of a WL text as plain words: tags to spaces, entities resolved."""
    if not description:
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", description[:limit])).split())


def is_network_change(description: str) -> bool:
    """Whether the WL text *description* announces a network change ("Netzänderung").

    Such a message restricts nothing — a line gets a stop — and names no
    restriction word, so it fell at the keyword gate of ``wl_fetch``: "9A:
    Errichtung einer Haltestelle" (new stop from 07.09.2026, in WL's list
    until 07.10.) and "57A: Errichtung einer Haltestelle" (from 05.09.,
    until 05.10.) in every fetch of 04.–07.10.2026. Since 2026-11-07 it
    enters the feed as a notice (operator decision "Als Hinweis
    aufnehmen"). WL's ``time.end`` of such a message is the end of the
    notice, weeks after the change (9A: a month; "N67: Geänderte
    Linienführung ab 05.12.2026", listed until 15.12.: ten days), not of
    the measure: the feed shows a network change without an end
    (``wl_fetch._notice_end``), also one that names a restriction word.
    """
    return NETWORK_CHANGE_LEAD_RE.match(lead_text(description)) is not None
