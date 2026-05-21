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
# Accept ``/``, ``+`` and ``,`` as separators between line codes. WL
# sometimes uses ``40+41:`` for items that affect two lines on the
# same corridor — the previous slash-only pattern silently treated
# the whole ``40+41`` as a body fragment, causing ``_ensure_line_prefix``
# to prepend ``40: `` on top of it (``40: 40+41: …``).
LINE_PREFIX_STRIP_RE = re.compile(
    r"^\s*[A-Za-z0-9]+(?:\s*[/+,]\s*[A-Za-z0-9]+){0,20}\s*:\s*", re.IGNORECASE
)
LINES_COMPLEX_PREFIX_RE = re.compile(
    r"^\s*[A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)+(?:(?:\s+und\s+)?Rufbus\s+[A-Za-z0-9]+)?(?:\s*\([^)]+\))?\s*:\s*",
    re.IGNORECASE,
)
RUF_BUS_PREFIX_RE = re.compile(r"^\s*Rufbus\s+([A-Za-z0-9]+)\s*:\s*", re.IGNORECASE)


def _extract_prefix_lines(title: str) -> tuple[str, list[str]]:
    """Strip leading line prefix(es) and return (body, lines_in_order).

    Handles stacked prefixes (``40: 40+41: …`` — a previous
    ``_ensure_line_prefix`` mishap that we now correct) and multiple
    separator styles (``/``, ``+``, ``,``). The lines returned are
    cleaned via :func:`_clean_line_token` and de-duplicated while
    preserving the order in which they first appear in the title —
    so ``41E/10A:`` extracts to ``["41E", "10A"]`` (original WL
    rendering kept) rather than a sorted ``["10A", "41E"]`` that
    would re-order the user-visible prefix unnecessarily.
    """
    if len(title) > 500:
        title = title[:500]
    seen: set[str] = set()
    lines: list[str] = []
    body = title.strip()
    # Loop ensures we handle stacked prefixes like ``40: 40+41: …``.
    # Five iterations is far more than any real WL/ÖBB title needs and
    # the loop terminates as soon as no prefix matches.
    for _ in range(5):
        match = LINES_COMPLEX_PREFIX_RE.match(body) or LINE_PREFIX_STRIP_RE.match(body)
        if match:
            block = body[: match.end()].rstrip(": \t")
            for tok in re.split(r"[,/+]", block):
                cleaned = _clean_line_token(tok.strip())
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    lines.append(cleaned)
            body = body[match.end():].strip()
            continue
        ruf_match = RUF_BUS_PREFIX_RE.match(body)
        if ruf_match:
            cleaned = _clean_line_token(ruf_match.group(1))
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                lines.append(cleaned)
            body = body[ruf_match.end():].strip()
            continue
        break
    return body, lines


def _strip_existing_line_block(title: str) -> str:
    """Entfernt vorhandene Linienblöcke am Anfang (Slash/Plus/Komma/Rufbus)."""
    body, _ = _extract_prefix_lines(title)
    return body


def _ensure_line_prefix(title: str, lines_disp: list[str]) -> str:
    """Sorgt für „L1/L2: …“. Entfernt vorhandene Präfixe zuerst.

    The existing prefix's lines are UNIONED with ``lines_disp`` so a
    WL item titled ``40+41: Betrieb ab Gersthof`` whose ``relatedLines``
    API field only carries ``["40"]`` still surfaces with both lines
    in the rendered title (``40/41: Betrieb ab Gersthof``). Without
    the union the API value would silently drop the ``41`` info that
    WL itself put into the title text.
    """
    if len(title) > 500:
        title = title[:500]

    body, existing_lines = _extract_prefix_lines(title)

    if not lines_disp and not existing_lines:
        return title

    # Union of provider-supplied lines and lines parsed from the title's
    # leading prefix block(s). Preserve the order from ``lines_disp``
    # first (typically already sorted by the caller) and append any
    # extra lines from the existing prefix in their original title-order
    # so ``41E/10A:`` round-trips unchanged.
    seen = set()
    merged: list[str] = []
    for tok in list(lines_disp) + list(existing_lines):
        cleaned = _clean_line_token(tok)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            merged.append(cleaned)

    if not merged:
        return body or title

    wanted = "/".join(merged)
    return f"{wanted}: {body}" if body else wanted


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
