"""Utility functions for Wiener Linien text handling and filtering."""

from __future__ import annotations

import re

from .wl_period import (
    STOP_NOTICE_LEAD_RE,
    extract_duration_from_description,
    extract_end_from_description,
    extract_start_from_description,
    is_stop_notice,
)
from .wl_title_dates import _MONTHS_DE, _resolve_missing_year, extract_date_from_title
from .wl_title_tidy import _tidy_title_wl
from .wl_topic_key import _title_core, _topic_key_from_title

# ---------------- Relevanz-/Ausschluss-Filter ----------------

KW_RESTRICTION = re.compile(
    # Match the restriction root anywhere inside a German compound noun
    # (``Baustelle``, ``Teilsperre``, ``Streckensperre``, ``Bauarbeiten``,
    # ``Verspätungen``, ``Einschränkungen``, ``Signalstörung`` …). The
    # bare-root pattern with ``\b`` on BOTH sides MISSED every inflection /
    # compound of the prefix-roots below (``sperr``, ``baustell``, ``verspät``,
    # ``einschränk``, ``unterbrech`` …): only the handful of roots that happen
    # to be standalone words (``umleitung``, ``verkehr``, ``gesperrt``,
    # standalone ``Störung`` / ``Ausfall``) ever matched, so the bulk of real
    # WL disruption items were silently dropped at the mandatory inclusion
    # gate in ``wl_fetch`` (and not rescued past ``KW_EXCLUDE``). Same fix
    # shape as the sibling ``FACILITY_ONLY`` regex below, which documents this
    # exact ``\b``-on-both-sides pitfall. Keyword list is unchanged.
    r"""
    \b\w*(
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
        # Stop relocations and closures count since 2026-10-04 (operator
        # decision "Alle aufnehmen"). Before, one passed only when its
        # reason happened to end in "-arbeiten" ("Kabelarbeiten": 16 in,
        # "Hausbau", "Straßenbau", "Gleisbau": 24 out).
        | verleg         # stop moved (Haltestellenverlegung, verlegt)
        | auflass        # stop closed (Haltestellenauflassung)
    )\w*\b
    # The same measures as a verb. A WL news item states its measure in a
    # sentence, and the roots above are nouns: "18: LCC-Herbstmarathon am
    # 11.10.2026" ("wird die Linie 18 kurz geführt") and "U6: Neue Donau,
    # kein Halt Richtung Floridsdorf" ("wird ... durchfahren", only notice
    # with a line for that closure) were both dropped at the news gate
    # (raw data ``data/raw/wl/verworfen.json``, 2026-10-04), while
    # "47B: Laufveranstaltung" passed on an incidental "Umleitung".
    | \bkurz\s*ge(?:führt|fuehrt)\b               # short service
    | \bumgeleitet\b                              # detoured
    | \bdurchf(?:ahr|ähr|aehr)\w*                 # station passed without stop
    | \bkein(?:en)?\s+(?:halt|betrieb)\b          # no stop / no service
    | \beingestellt\b                             # service suspended
    | \baufgelassen\b                             # stop closed
    | \bentf(?:ällt|aellt|allen)\b                # stop/trip cancelled
    """,
    re.IGNORECASE | re.VERBOSE,
)

KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|eroeffnung|service(?:-info)?|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE,
)

FACILITY_ONLY = re.compile(
    # Match the facility root anywhere inside a German compound noun
    # (``Personenlift``, ``Aufzugsanlage``, ``Liftbetrieb`` etc.) —
    # the bare-root pattern with ``\b`` on both sides misses real
    # ÖBB titles like ``Technische Störung des Personenlift``.
    r"\b\w*(?:aufzug|aufz(?:ü|ue)ge|lift|fahrstuhl|"
    r"fahrtreppen?(?:info)?|rolltreppen?|aufzugsinfo|"
    # The folding ramps of the trains count like a lift (operator decision
    # 2026-10-09): "U1: Klapprampensperre am 10.10.2026", WL's only form
    # since July 2026; the trains run as usual.
    r"klapprampen?)\w*\b",
    re.IGNORECASE,
)


def _is_facility_only(*texts: str) -> bool:
    """Return ``True`` if the combined text refers only to facilities."""

    t = " ".join([x for x in texts if x]).lower()
    if len(t) > 500:
        t = t[:500]
    return bool(FACILITY_ONLY.search(t))



__all__ = [
    "KW_RESTRICTION",
    "KW_EXCLUDE",
    "STOP_NOTICE_LEAD_RE",
    "is_stop_notice",
    "_is_facility_only",
    "_tidy_title_wl",
    "_title_core",
    "_topic_key_from_title",
    "extract_date_from_title",
    "extract_duration_from_description",
    "extract_end_from_description",
    "extract_start_from_description",
    "FACILITY_ONLY",
    "_MONTHS_DE",
    "_resolve_missing_year",
]
