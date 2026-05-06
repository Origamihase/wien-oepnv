"""Utilities for parsing and manipulating Wiener Linien line information."""

from __future__ import annotations

import re
from typing import Any


def _clean_line_token(s: str) -> str:
    s = str(s or "")
    s = re.sub(r"^\s*Rufbus\s+", "", s, flags=re.IGNORECASE)  # „Rufbus “ strippen
    s = re.sub(r"\s+", "", s).upper()
    return s


def _tok(v: Any) -> str:
    if v is None:
        return ""
    token = re.sub(r"[^A-Za-z0-9+]", "", str(v))
    token = _clean_line_token(token)
    return token if token else ""


def _display_line(s: str) -> str:
    return _clean_line_token(s)


# Präfix-Erkennung/Entfernung:
LINE_PREFIX_STRIP_RE = re.compile(r"^\s*[A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20}\s*:\s*", re.IGNORECASE)
LINES_COMPLEX_PREFIX_RE = re.compile(
    r"^\s*[A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)+(?:(?:\s+und\s+)?Rufbus\s+[A-Za-z0-9]+)?(?:\s*\([^)]+\))?\s*:\s*",
    re.IGNORECASE,
)
RUF_BUS_PREFIX_RE = re.compile(r"^\s*Rufbus\s+([A-Za-z0-9]+)\s*:\s*", re.IGNORECASE)


def _strip_existing_line_block(title: str) -> str:
    """Entfernt vorhandene Linienblöcke am Anfang (Slash-/Komma-/Rufbus-Varianten)."""
    if len(title) > 500:
        title = title[:500]

    t = LINE_PREFIX_STRIP_RE.sub("", title)
    t = RUF_BUS_PREFIX_RE.sub("", t)
    if ":" in t:
        pre, post = t.split(":", 1)
        if ("," in pre) or ("Rufbus" in pre) or ("(" in pre):
            t = post.strip()
    return t


def _ensure_line_prefix(title: str, lines_disp: list[str]) -> str:
    """Sorgt für „L1/L2: …“. Entfernt vorhandene Präfixe zuerst."""
    if len(title) > 500:
        title = title[:500]

    if not lines_disp:
        return title
    wanted = "/".join(lines_disp)
    if re.match(rf"^\s*{re.escape(wanted)}\s*:\s*", title, re.IGNORECASE):
        rest = re.sub(rf"^\s*{re.escape(wanted)}\s*:\s*", "", title, flags=re.IGNORECASE).strip()
        return title if rest else wanted
    stripped = _strip_existing_line_block(title).strip()
    return f"{wanted}: {stripped}" if stripped else wanted


# Fallback-Linien aus Titeltext — vorher Datum/Zeit/Adressen maskieren
LINE_CODE_RE = re.compile(
    r"\b(?:U\d{1,2}|S\d{1,2}|N\d{1,3}|[0-9]{1,3}[A-Z]?|[A-Z])\b",
    re.IGNORECASE,
)
RUF_BUS_RE = re.compile(r"Rufbus\s+([A-Za-z0-9]+)", re.IGNORECASE)
DATE_FULL_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})\b")
DATE_SHORT_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\b")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
ADDRESS_NO_RE = re.compile(
    # Two shapes covered:
    #   1) Compound street names where the suffix is glued onto the prefix
    #      ("Wienerstraße 12", "Pasettistraße 200"). Suffix list is the
    #      common Wien street terminology.
    #   2) Two-word forms with a space and an abbreviation
    #      ("Währinger Str 200", "Mariahilfer Str. 12", "Dornbacher
    #      Straße 85"). Without this branch the trailing number was
    #      picked up as a transit-line code by ``LINE_CODE_RE`` and
    #      surfaced in the cached title prefix as ``41E/200``.
    r"\b("
    r"[A-Za-zÄÖÜäöüß\-]+"
    r"(?:gasse|straße|strasse|platz|allee|weg|steig|ufer|brücke|kai|ring|gürtel|lände|damm|markt)"
    r"|"
    r"[A-Za-zÄÖÜäöüß\-]+\s+(?:Straße|Strasse|Str\.?|Gasse|Platz|Allee|Weg|Steig|Ufer|Brücke|Kai|Ring|Gürtel|Lände|Damm|Markt)"
    r")"
    # Numeric tail: house number, optional range ("236-238"), and optional
    # alpha suffix ("12a"). Without the range, "Breitenfurter Straße
    # 236-238" leaves "-238" behind which then matches LINE_CODE_RE as
    # a phantom line.
    r"\s+\d+(?:\s*[-–—/]\s*\d+)?[A-Za-z]?\b",
    re.IGNORECASE,
)
ADDRESS_NO_PRE_RE = re.compile(
    r"\b(?:ggü\.?|gegenüber|Nr\.?|Nummer|Hausnr\.?|Objekt|Stiege|Tür|Top)\s+\d+\b",
    re.IGNORECASE,
)


def _mask_dates_times_addresses(t: str) -> str:
    if len(t) > 500:
        t = t[:500]
    t = DATE_FULL_RE.sub(" ", t)
    t = DATE_SHORT_RE.sub(" ", t)
    t = TIME_RE.sub(" ", t)
    t = ADDRESS_NO_RE.sub(r"\1", t)  # Zahl nach Straßentyp entfernen
    t = ADDRESS_NO_PRE_RE.sub(" ", t)  # Zahl nach Präfix (ggü. 12) entfernen
    return t


def _detect_line_pairs_from_text(text: str) -> list[tuple[str, str]]:
    if text and len(text) > 500:
        text = text[:500]
    t = _mask_dates_times_addresses(text or "")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    # „Rufbus Nxx“ zuerst
    for m in RUF_BUS_RE.findall(t):
        tok = _tok(m)
        if tok and tok not in seen:
            seen.add(tok)
            pairs.append((tok, _display_line(m)))
    # generische Codes
    for m in LINE_CODE_RE.findall(t):
        tok = _tok(m)
        if tok and tok not in seen:
            seen.add(tok)
            pairs.append((tok, _display_line(m)))
    return pairs


def _make_line_pairs_from_related(rel_lines: list[Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for x in rel_lines:
        tok = _tok(x)
        if not tok or tok in seen:
            continue
        seen.add(tok)
        pairs.append((tok, _display_line(x)))
    return pairs


def _merge_line_pairs(
    base_pairs: list[tuple[str, str]], add_pairs: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    existing = {tok for tok, _ in base_pairs}
    out = list(base_pairs)
    for tok, disp in add_pairs:
        if tok not in existing:
            out.append((tok, disp))
            existing.add(tok)
    return out


def _line_tokens_from_pairs(pairs: list[tuple[str, str]]) -> list[str]:
    return [tok for tok, _ in pairs]


def _line_display_from_pairs(pairs: list[tuple[str, str]]) -> list[str]:
    return [disp for _, disp in pairs]


__all__ = [
    "_detect_line_pairs_from_text",
    "_make_line_pairs_from_related",
    "_merge_line_pairs",
    "_line_tokens_from_pairs",
    "_line_display_from_pairs",
    "_ensure_line_prefix",
]
