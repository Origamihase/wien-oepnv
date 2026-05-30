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


# Strict line-token shape used by ``_extract_prefix_lines`` to gate the
# prefix-strip decision. A token must look like a real Vienna or ÖBB
# line code (digits + optional letter, OR a known operator letter
# prefix followed by digits, OR a single bare uppercase letter such as
# WL tram ``D``) to count. Without this gate the permissive
# ``[A-Za-z0-9]+`` regex below would happily classify a generic word
# like ``Achtung``, ``Information`` or ``Hinweis`` (every titlecased
# noun that ends with ``:``) as a "line code", causing the downstream
# ``_post_filter_wl`` rebuild to mangle the title into ``ACHTUNG: …``.
# Two shapes are accepted:
#
#   1. ``[A-Z]{0,4}\d{1,3}[A-Z]?`` — the canonical digit-bearing line
#      code. ``[A-Z]{0,4}`` covers ÖBB carrier prefixes (REX, RJ, RJX,
#      EC, ICE, IC, WB, NJ, CJX, S, U, N, R, D) and the WL bus-suffix
#      ``A``/``E`` is captured by the optional trailing ``[A-Z]?``.
#   2. ``[A-Z]`` — a single bare uppercase letter. Required by WL's
#      letters-only tram line ``D`` (Wien Hauptbahnhof — Nußdorf).
#      Real cache item ``D: D: Demonstration`` reproduced the stacking
#      bug when the original digit-only gate rejected ``D`` so
#      ``_ensure_line_prefix`` couldn't strip the existing ``D:``
#      prefix and ended up prepending another ``D:`` from
#      ``relatedLines``. The over-permissiveness (accepting any letter,
#      not just ``D`` / ``O``) is bounded here by the colon-line-prefix
#      shape ``LINE_PREFIX_STRIP_RE`` requires; the body-scanner gate
#      ``LINE_CODE_RE`` below tightens to ``[DO]`` because that surface
#      is unanchored and a sentence-start ``S`` in ``S-Bahn-Verkehr``
#      otherwise matches via ``\b[A-Z]\b`` as a phantom line.
#
# Multi-letter words without digits (``ACHTUNG``, ``INFORMATION``,
# ``HINWEIS``, ``LINIE``) still fail both shapes, so the original
# false-positive guard stays intact.
_STRICT_LINE_TOKEN_RE = re.compile(r"^(?:[A-Z]{0,4}\d{1,3}[A-Z]?|[A-Z])$")


# Präfix-Erkennung/Entfernung:
# Accept ``/``, ``+`` and ``,`` as separators between line codes. WL
# sometimes uses ``40+41:`` for items that affect two lines on the
# same corridor — the previous slash-only pattern silently treated
# the whole ``40+41`` as a body fragment, causing ``_ensure_line_prefix``
# to prepend ``40: `` on top of it (``40: 40+41: …``).
# Require whitespace OR end-of-string AFTER the colon
# (``:(?:\s+|$)``) so a sentence-starting time fragment like
# ``17:30 Uhr Verspätung`` doesn't match as a line prefix. Pre-fix
# the lenient ``:\s*`` regex consumed ``17:`` and left the body as
# ``30 Uhr Verspätung`` with lines=``[17]`` — the strict-line-token
# gate alone cannot catch this because ``17`` IS a valid line-code
# shape (the disambiguator is what comes after the colon, not what
# comes before it). Every real WL/ÖBB title in the cache carries
# ``: `` with a space, and the empty-body shape ``5:`` (returning
# just the prefix marker after a previous render dropped the body)
# is covered by the ``$`` alternative so ``_ensure_line_prefix``'s
# empty-body contract still holds.
# Each line-code token may be followed by a parenthetical qualifier
# such as ``(Schulkurs)`` (WL's school-run variant of a bus) or
# ``(Nachtbus)``. The qualifier is informational but does NOT change
# the line-code identity; it is preserved in the description body
# but stripped from the prefix block so the title round-trips as
# the canonical ``85A: …`` rather than ``85A (Schulkurs): …``.
LINE_PREFIX_STRIP_RE = re.compile(
    r"^\s*[A-Za-z0-9]+(?:\s*\([^)]+\))?"
    r"(?:\s*[/+,]\s*[A-Za-z0-9]+(?:\s*\([^)]+\))?){0,20}"
    r"\s*:(?:\s+|$)",
    re.IGNORECASE,
)
# Allow ``,`` (not only ``und``) as a connector between the
# comma-list and the trailing ``Rufbus`` token — real cache item
# ``56A, 60A, N60, Rufbus N61:`` uses the ``,`` form, and pre-fix
# the regex required ``und`` so the prefix slipped through
# unrecognised and surfaced as a stacked title.
LINES_COMPLEX_PREFIX_RE = re.compile(
    r"^\s*[A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)+"
    r"(?:\s*,?\s*(?:und\s+)?Rufbus\s+[A-Za-z0-9]+)?"
    r"(?:\s*\([^)]+\))?\s*:(?:\s+|$)",
    re.IGNORECASE,
)
RUF_BUS_PREFIX_RE = re.compile(
    r"^\s*Rufbus\s+([A-Za-z0-9]+)\s*:(?:\s+|$)", re.IGNORECASE
)


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

    Defence against false-positive matches on titles that *look*
    line-prefixed but aren't: the underlying regex
    :data:`LINE_PREFIX_STRIP_RE` is intentionally permissive
    (``[A-Za-z0-9]+`` before the colon) so it can absorb operator
    letter prefixes (``REX7``, ``RJX12``, ``41E``, ``10A``, ``N20``).
    That same permissiveness, pre-fix, treated generic word prefixes
    like ``Achtung: Sperre`` / ``Information: Test`` and sentence-
    starting numerics like ``17:30 Verspätung`` as line prefixes,
    producing a mangled rebuild (``ACHTUNG: Sperre``, lines=``[17]``).
    The :data:`_STRICT_LINE_TOKEN_RE` gate below rejects the strip
    when ANY extracted token fails strict line-code validation —
    leaving the title body untouched so the user-visible text stays
    readable even if WL ever ships a non-line-prefixed title.
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
            candidates: list[str] = []
            for tok in re.split(r"[,/+]", block):
                # Strip a parenthetical qualifier like ``(Schulkurs)``
                # before token-cleaning — the qualifier is sub-line
                # classification, not part of the line code itself.
                # Real cache item ``85A (Schulkurs):`` would otherwise
                # fail the strict-token gate and leave the title
                # stacked as ``85A: 85A (Schulkurs): …``.
                tok = re.sub(r"\s*\([^)]+\)\s*", " ", tok)
                cleaned = _clean_line_token(tok.strip())
                if cleaned:
                    candidates.append(cleaned)
            # Only commit the strip when EVERY extracted token looks
            # like a real line code. Otherwise the prefix is a generic
            # word (``Achtung:``, ``Hinweis:``) or a time fragment
            # (``17:30``) and stripping it would mangle the title.
            if candidates and all(_STRICT_LINE_TOKEN_RE.match(c) for c in candidates):
                for cleaned in candidates:
                    if cleaned not in seen:
                        seen.add(cleaned)
                        lines.append(cleaned)
                body = body[match.end():].strip()
                continue
            # No strip — abandon the prefix-loop so the body keeps its
            # original ``Achtung: …`` / ``17:30 …`` shape.
            break
        ruf_match = RUF_BUS_PREFIX_RE.match(body)
        if ruf_match:
            cleaned = _clean_line_token(ruf_match.group(1))
            if cleaned and _STRICT_LINE_TOKEN_RE.match(cleaned) and cleaned not in seen:
                seen.add(cleaned)
                lines.append(cleaned)
                body = body[ruf_match.end():].strip()
                continue
            break
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


# Fallback-Linien aus Titeltext — vorher Datum/Zeit/Adressen maskieren.
#
# Case-sensitivity (no ``re.IGNORECASE``): pre-fix the regex matched any
# lowercase letter via the bare ``[A-Z]`` alternative under
# ``IGNORECASE``, so a sentence-start German particle in the title
# (``"Bauarbeiten a Karlsplatz"``, ``"e Linie"`` etc.) was extracted
# as ``"a"`` / ``"e"``, upper-cased to ``"A"`` / ``"E"`` by
# ``_clean_line_token`` and then accepted by ``_STRICT_LINE_TOKEN_RE``
# as a bus / tram line letter. Real WL line codes are always
# canonically upper-cased (``U6``, ``S40``, ``41E``, ``D``), so
# requiring uppercase rejects the false positive without missing any
# legitimate token.
#
# Single-letter alternative is ``[DO]`` (not ``[A-Z]``): ``D`` (Wien
# Hauptbahnhof — Nußdorf) and ``O`` (Praterstern — Raxstraße) are the
# ONLY single-letter line codes in the entire WL/ÖBB/VOR network. Pre-
# fix the unconstrained ``[A-Z]`` alternative extracted any standalone
# uppercase letter as a phantom line — real example title ``Information
# zu S-Bahn-Verkehr in Wien`` yielded ``[('S', 'S')]`` because ``\bS\b``
# matches the ``S`` in ``S-Bahn`` (``-`` is a non-word boundary). The
# phantom line then flowed into ``_wl_identity`` (wrong bucket /
# first_seen key) AND into the rendered title via ``_ensure_line_prefix``
# (``S: Information zu S-Bahn-Verkehr…``). Same bug for ``A`` in ``A bis
# Karlsplatz`` (sentence-start preposition). Tightening to ``[DO]``
# preserves both real single-letter tram lines while rejecting every
# other standalone uppercase letter. Multi-character tokens (``U6``,
# ``S40``, ``41E``, ``REX7``) are unaffected — they match the
# digit-bearing alternatives.
LINE_CODE_RE = re.compile(
    r"\b(?:U\d{1,2}|S\d{1,2}|N\d{1,3}|[0-9]{1,3}[A-Z]?|[DO])\b",
)
RUF_BUS_RE = re.compile(r"Rufbus\s+([A-Za-z0-9]+)", re.IGNORECASE)
DATE_FULL_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})\b")
DATE_SHORT_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\b")
# ``(?::\d{2})?`` captures the optional seconds component. Pre-fix the
# regex matched only ``HH:MM``; an ``HH:MM:SS`` timestamp left ``:SS``
# behind, and the trailing 2-digit ``SS`` was then extracted as a phantom
# WL line via the ``[0-9]{1,3}[A-Z]?`` branch of ``LINE_CODE_RE``
# (``Sperre 17:30:45 Ausfall`` -> phantom line ``45``).
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
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
# ``[A-Za-z]?`` captures the optional alpha suffix on a house number.
# Pre-fix the trailing ``\b`` required the number to end at a word boundary
# WITHOUT a letter (``Stiege 12`` masked, ``Stiege 12A`` did NOT — the
# ``12`` matched but ``A`` then escaped to ``LINE_CODE_RE`` and surfaced as
# a phantom line ``12A``). Mirrors the alpha-suffix already supported by
# the sibling ``ADDRESS_NO_RE`` numeric-tail above (``[A-Za-z]?\b``).
ADDRESS_NO_PRE_RE = re.compile(
    r"\b(?:ggü\.?|gegenüber|Nr\.?|Nummer|Hausnr\.?|Objekt|Stiege|Tür|Top)\s+\d+[A-Za-z]?\b",
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
        # Defensive shape check (matches the sibling
        # :func:`_stop_names_from_related` in ``wl_fetch.py``): WL
        # ``relatedLines`` is documented as a flat list of line-code
        # strings (``["U1", "U2"]``). A misbehaving / compromised
        # upstream peer (or a tampered proxy response) may ship
        # ``[{"name": "U1"}]`` instead — ``_tok`` would then call
        # ``str(dict)`` and produce the garbage token ``"nameU1"``,
        # which lands in the bucket key, the GUID, and the emitted
        # title verbatim. Skip non-string entries so the malformed
        # item simply contributes no tokens rather than poisoning
        # the bucket.
        if not isinstance(x, str):
            continue
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
