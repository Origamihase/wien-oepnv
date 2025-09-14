"""Utility functions for Wiener Linien text handling and filtering."""

from __future__ import annotations

import re

# ---------------- Relevanz-/Ausschluss-Filter ----------------

KW_RESTRICTION = re.compile(
    r"\b(umleitung|ersatzverkehr|unterbrech|sperr|gesperrt|störung|stoerung|arbeiten|baustell|einschränk|verspät|ausfall|verkehr"
    r"|kurzführung|kurzfuehrung|teilbetrieb|pendelverkehr|kurzstrecke)\b",
    re.IGNORECASE,
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
]

