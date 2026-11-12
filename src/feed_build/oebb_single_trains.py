"""ÖBB notices about single trains stay out of the feed.

Operator decision 2026-11-11, Michael: "Einzelzüge sollen nicht in den Feed."
It came with his answer "Nein, nur echte Sperren" to the question whether
single cancelled ÖBB trains should rank like closures ahead of the WL notices
(:mod:`.oebb_days`, independent check 2026-11-11).

A notice is about single trains when its lead sentence, the sentence the feed
shows first (``_oebb_shown_sentences`` in :mod:`.oebb_lead`), names its trains
by number or as "einzelne" trains (:data:`_SINGLE_TRAINS_RE`):

* "Wegen Bauarbeiten kann … am 28.11.2026 … der S1-Zug 23626 nicht fahren."
* "TEILAUSFALL: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 zwischen
  Wien Hütteldorf … und Wien Hbf … nicht fahren."
* "Wegen Bauarbeiten wird von 19.03.2027 bis 30.03.2027 der Zug RJ 12265
  umgeleitet."
* "… die Züge REX6(7777), S60(26085), S60(26086) und REX6(7776) …", "… die
  Züge REX 1 (2334, 2338 und 2342) nicht fahren."

"Einige" trains without numbers stay (operator decision of 2026-11-12, card
"Zählen ÖBB-Meldungen über „einige Züge“ ohne Zugnummer auch als
Einzelzüge?", answer "Nein, sie bleiben im Feed"): "einige Nahverkehrszüge nicht fahren"
(Wien Hbf ↔ Gramatneusiedl/Götzendorf/Bruck an der Leitha from 08.03. to
22.06.2026), "einige REX 1-Züge … können in Wien Mitte-Landstraße, Wien
Praterstern … nicht halten" say that the section runs fewer trains, often for
weeks; a train number tells the viewer nothing.

The lead decides, as for the closure days, because it is what the displays
show. A notice in which any sentence says that no trains run or stop
(``_oebb_closes_line``) stays, whatever its lead: "keine REX50-Züge fahren"
comes with a list of REX 51 and S 50 trains, and the closure is the news.
Undated leads are no exception: every ÖBB lead naming trains by number from
01.05. to 11.11.2026 carried dates (planned works); an unplanned disruption
names its section ("Zugfahrten nur eingeschränkt möglich") and stays.

Lines named in the plural are no train numbers ("Für die Züge REX 63 und
REX 64 haben wir … einen Autobus organisiert"): a regional line has one or
two digits, its trains three to five ("der Zug REX 1826"). Long-distance
trains have their own categories and may have two digits ("der Zug RJX 62").

The notice stays in the ÖBB cache and in ``data/raw/oebb/``; only the feed
leaves it out (``_post_filter_oebb`` in :mod:`.cache_filters`).
"""

from __future__ import annotations

import re
from typing import Any

from .base import _OEBB_SOURCE
from .oebb_days import _oebb_closes_line, _plain
from .oebb_lead import _oebb_shown_sentences

__all__ = ["_SINGLE_TRAINS_RE", "_oebb_names_single_trains", "_oebb_single_trains_only"]

# "Zug", "Zuges", "Züge", "Zügen"; ÖBB's markup sometimes splits "Zü ge".
_TRAIN_WORD = r"Z(?:ug(?:es)?|ü\s?gen?)"
# Long-distance categories: their trains may have two digits (RJX 62).
_LONG_DISTANCE = r"(?:RJX|RJ|ECE|EC|EN|ICE|IC|IR|NJ|D|WB)"
_TRAIN_ID = (
    r"(?:[A-Z]{1,4}\s?\d{0,3}\s?\(\d{3,5}\b"  # REX64(2679), S1 (4000), REX(7603), REX 1 (2334, …)
    rf"|{_LONG_DISTANCE}\s?\d{{2,5}}\b"  # RJ 263, EC 196, NJ 19946, D 816
    r"|(?:[A-Z]{1,4}\s?)?\d{3,5}\b)"  # REX 1826, 3802; not the line "REX 63"
)
_SINGLE_TRAINS_RE = re.compile(
    # der Zug RJ 263, der S1-Zug 23626, die REX 41-Züge 2153 und 2157, die
    # S3- Züge 7563, die Züge 3802, 3803 …, der Zug/Bus REX(7603)
    rf"\b{_TRAIN_WORD}(?:/Bus)?\s+{_TRAIN_ID}"
    # a long-distance train of two digits behind its category: der RJX-Zug 62
    rf"|\b{_LONG_DISTANCE}-{_TRAIN_WORD}\s+\d{{2,5}}\b"
    # einzelne Züge, einzelne Nahverkehrszüge, einzelne S50-Züge, einzelne REX 1-Züge
    r"|\beinzelne\s+(?:[\w-]+\s+)?[\w-]*(?:Züge|züge)\b"
)


def _oebb_names_single_trains(sentence: str) -> bool:
    """Whether an ÖBB sentence names trains by number or as "einzelne" trains."""
    return bool(_SINGLE_TRAINS_RE.search(_plain(sentence)))


def _oebb_single_trains_only(item: Any) -> bool:
    """Whether *item* is an ÖBB notice about single trains (operator decision 2026-11-11).

    True when the lead sentence the feed shows names single trains
    (:func:`_oebb_names_single_trains`) and no sentence closes the line
    (``_oebb_closes_line``).
    """
    if not isinstance(item, dict) or item.get("source") != _OEBB_SOURCE:
        return False
    sentences, shown = _oebb_shown_sentences(item)
    if not shown or any(_oebb_closes_line(sentence) for sentence in sentences):
        return False
    return _oebb_names_single_trains(shown[0])
