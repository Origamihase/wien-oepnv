"""Utility functions for Wiener Linien text handling and filtering."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

# ---------------- Relevanz-/Ausschluss-Filter ----------------

KW_RESTRICTION = re.compile(
    r"""
    \b(
        umleitung       # detour
        | ersatzverkehr  # replacement service
        | unterbrech     # interruption
        | sperr          # closure prefix
        | gesperrt       # blocked
        | störung        # disruption (umlaut)
        | stoerung       # disruption (oe)
        | arbeiten       # works
        | baustell       # construction site
        | einschränk     # restriction (umlaut)
        | verspät        # delay
        | ausfall        # outage
        | verkehr        # traffic
        | kurzführung    # short service (umlaut)
        | kurzfuehrung   # short service (ue)
        | teilbetrieb    # partial service
        | pendelverkehr  # shuttle service
        | kurzstrecke    # short route
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|eroeffnung|service(?:-info)?|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE,
)

FACILITY_ONLY = re.compile(
    r"\b(aufzug|aufzüge|aufzuege|lift|fahrstuhl|fahrtreppe|fahrtreppen|rolltreppe|rolltreppen|aufzugsinfo|fahrtreppeninfo)\b",
    re.IGNORECASE,
)


def _is_facility_only(*texts: str) -> bool:
    """Return ``True`` if the combined text refers only to facilities."""

    t = " ".join([x for x in texts if x]).lower()
    return bool(FACILITY_ONLY.search(t))


# ---------------- Titel-Kosmetik ----------------

_LABELS = [
    r"bauarbeiten",
    r"straßenbauarbeiten",
    r"strassenbauarbeiten",
    r"gleisbauarbeiten",
    r"verkehrsinfo",
    r"verkehrsinformation",
    r"verkehrsmeldung",
    r"störung",
    r"stoerung",
    r"hinweis",
    r"serviceinfo",
    r"service\-info",
    r"information",
]
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_LABELS) + r")\s*(?:[-:–—/]\s*|\s+))+",
    re.IGNORECASE,
)


def _is_informative(rest: str) -> bool:
    return bool(rest and re.search(r"[A-Za-zÄÖÜäöüß0-9]{3,}", rest))


def _tidy_title_wl(title: str) -> str:
    """Entfernt generische Label am Anfang, wenn danach informativer Text steht."""

    t = (title or "").strip()
    if not t:
        return t
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        t = stripped
    t = re.sub(r"[<>«»‹›]+", "", t)  # spitze Klammern/Anführungen
    return re.sub(r"\s{2,}", " ", t).strip(" -–—:/\t")


# ---------------- Datum aus Titel extrahieren ----------------

_DATE_FROM_TITLE_RE = re.compile(r"ab\s+(\d{1,2})\.(\d{1,2})\.(\d{4})?", re.IGNORECASE)

def extract_date_from_title(title: str, reference_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    Extrahierte ein Datum der Form 'ab DD.MM.[YYYY]' aus dem Titel.
    Falls das Jahr fehlt, wird versucht, es anhand von reference_date zu raten.
    """
    if not title:
        return None

    match = _DATE_FROM_TITLE_RE.search(title)
    if not match:
        return None

    day_str, month_str, year_str = match.groups()
    day, month = int(day_str), int(month_str)

    if reference_date is None:
        reference_date = datetime.now(timezone.utc)

    if year_str:
        year = int(year_str)
    else:
        # Jahr raten:
        # Zunächst aktuelles Jahr (basierend auf reference_date)
        year = reference_date.year
        try:
            candidate = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None

        # Wenn das Datum mehr als 3 Monate in der Vergangenheit liegt im Vergleich zum Referenzdatum,
        # nehmen wir an, dass das nächste Jahr gemeint ist (z.B. im Dez 'ab Jan').
        if candidate < reference_date - timedelta(days=90):
            year += 1

    try:
        # Wir setzen die Zeit auf 04:00 (Betriebsbeginn?) oder 00:00?
        # Um Konsistenz mit Kalendern zu wahren, ist 00:00 (Mitternacht) am sichersten.
        # Da wir UTC nutzen, ist es 00:00 UTC.
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------- „Kernbegriff/Topic“ für Dedupe ----------------

TITLE_TOPIC_TOKENS = {
    "falschparker",
    "polizeieinsatz",
    "rettungseinsatz",
    "unfall",
    "signalstörung",
    "signalstoerung",
    "umleitung",
    "ersatzverkehr",
    "kurzführung",
    "kurzfuehrung",
    "sperre",
    "gesperrt",
}

_GENERIC_FILLER = re.compile(
    r"\b(fahrtbehinderung|verkehrsbehinderung|behinderung|störung|stoerung|hinweis|meldung|serviceinfo|service\-info|"
    r"betrieb\s+ab.*|betrieb\s+nur.*)\b",
    re.IGNORECASE,
)


def _title_core(t: str) -> str:
    t2 = _tidy_title_wl(t)
    t2 = re.sub(r"[^\wäöüÄÖÜß]+", " ", t2, flags=re.UNICODE)
    t2 = re.sub(r"\s{2,}", " ", t2).strip().casefold()
    return t2


def _topic_key_from_title(raw: str) -> str:
    t = _GENERIC_FILLER.sub(" ", raw or "")
    t = re.sub(r"[^\wäöüÄÖÜß]+", " ", t, flags=re.UNICODE).casefold()
    toks = {w for w in t.split() if w in TITLE_TOPIC_TOKENS}
    if toks:
        return " ".join(sorted(toks))
    return _title_core(raw)


__all__ = [
    "KW_RESTRICTION",
    "KW_EXCLUDE",
    "_is_facility_only",
    "_tidy_title_wl",
    "_title_core",
    "_topic_key_from_title",
    "extract_date_from_title",
]
