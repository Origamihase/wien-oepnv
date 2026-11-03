"""ÖBB "A ↔ B" titles from extracted routes: line prefix, route collapse, affected lines.

Moved verbatim from ``src/providers/oebb.py`` (700-line limit); the facade
``src.providers.oebb`` re-exports the names other code uses.
"""

from __future__ import annotations

import re

from ..utils.stations import StationInfo, display_name, station_info, station_lines
from ..utils.geo import calculate_distance_meters
from .oebb_title import _LEADING_LINE_PREFIX_RE
from .oebb_routes import _extract_routes
from .oebb_relevance import _route_is_wien_relevant


# ---------------- Title formatting helpers ----------------

# Recognises a leading line marker (REX 7, S 50, RJX 12, …) so we can preserve
# it even when we rebuild the title from extracted endpoints.
#
# The colon is MANDATORY (``:\s*``, not ``:?\s*``) so a colonless title that
# happens to start with a token + digit (``"R 5 Wien Hbf"``, ``"D 100 Wien"``)
# is NOT misparsed as line-prefixed. Pre-fix the optional colon glued an
# imaginary prefix onto such titles, mangling the downstream rebuild. It is
# the pattern of :data:`_LEADING_LINE_PREFIX_RE`, several lines included:
# ``_post_filter_oebb`` re-derives a cached ``R 40/REX 41: …`` title, and a
# prefix it did not recognise would be put in front a second time.
_LINE_PREFIX_RE = _LEADING_LINE_PREFIX_RE


def _extract_line_prefix(title: str) -> tuple[str, str]:
    """Split off a leading line marker from *title*.

    Returns ``(line_prefix, remaining_title)``. The line prefix is empty when
    *title* doesn't start with a recognised marker.
    """
    if not title:
        return "", ""
    match = _LINE_PREFIX_RE.match(title)
    if not match:
        return "", title.strip()
    return match.group(1).strip(), title[match.end():].strip()


# Compact directory names sometimes use abbreviations (Westbf, Hbf, …) that
# look truncated in the feed. We expand them only for the user-facing title.
_STATION_NAME_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bWestbf\b"), "Westbahnhof"),
    (re.compile(r"\bOstbf\b"), "Ostbahnhof"),
    (re.compile(r"\bNordbf\b"), "Nordbahnhof"),
    (re.compile(r"\bSüdbf\b"), "Südbahnhof"),
    (re.compile(r"\bHbf\b"), "Hauptbahnhof"),
    (re.compile(r"-Bf\b"), "-Bahnhof"),
)


def _expand_station_abbreviations(name: str) -> str:
    """Expand common Bf/Hbf abbreviations for readability."""
    for pattern, replacement in _STATION_NAME_EXPANSIONS:
        name = pattern.sub(replacement, name)
    return name


def _try_chain_routes(canonical_routes: list[tuple[str, str]]) -> list[str] | None:
    """Collapse routes that share endpoints into a single linear chain.

    Real ÖBB items frequently describe a corridor disruption as several
    overlapping segments::

        Wien Westbahnhof ↔ Wien Hütteldorf
        Wien Hütteldorf ↔ Tullnerbach-Pressbaum
        Wien Westbahnhof ↔ St. Pölten Hauptbahnhof

    Rendered with ``" / "`` between routes that's awkward and repeats
    the hub station twice. The three segments above are actually one
    linear chain:

        St. Pölten Hauptbahnhof ↔ Wien Westbahnhof ↔ Wien Hütteldorf
            ↔ Tullnerbach-Pressbaum

    We accept the routes as chainable when:

    * Every station has degree ≤ 2 (no branching hub).
    * Exactly two stations have degree 1 (the chain endpoints).
    * The graph is connected (the traversal hits every node).

    Orientation rule: prefer a Vienna endpoint at the start so the
    Wien-focused feed leads with a Wien name when possible; otherwise
    use the alphabetically smaller endpoint for determinism.

    Returns the ordered station list, or ``None`` if the routes don't
    form a chain (branches, cycles, multiple disconnected paths) — the
    caller then falls back to the existing ``" / "`` renderer.
    """
    if not canonical_routes:
        return None

    adj: dict[str, set[str]] = {}
    for a, b in canonical_routes:
        if a == b:
            return None  # self-loop, can't chain
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    # No node may branch — chains are degree-≤-2 by construction.
    if any(len(neighbours) > 2 for neighbours in adj.values()):
        return None

    # A chain has exactly two endpoints (degree 1). Zero → cycle,
    # more than two → disconnected forest of paths.
    endpoints = sorted(n for n, neighbours in adj.items() if len(neighbours) == 1)
    if len(endpoints) != 2:
        return None

    start = endpoints[0]
    for ep in endpoints:
        info = station_info(ep)
        if info and info.in_vienna:
            start = ep
            break

    chain: list[str] = [start]
    prev: str | None = None
    current = start
    while True:
        next_set = adj[current] - ({prev} if prev else set())
        if not next_set:
            break
        next_node = next(iter(next_set))
        chain.append(next_node)
        prev = current
        current = next_node

    # Disconnected components masquerade as chains with two endpoints;
    # require traversal to cover every node to confirm connectedness.
    if len(chain) != len(adj):
        return None
    return chain


def _try_star_routes(canonical_routes: list[tuple[str, str]]) -> tuple[str, list[str]] | None:
    """Return ``(hub, others)`` when every route starts or ends at one station.

    A star does not chain, and the ``" / "`` renderer repeated the hub in
    every segment. Published 2026-09-30 for 36 versions, 144 characters::

        REX 41: Wien Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern / Wien
            Franz-Josefs-Bahnhof ↔ Wien Heiligenstadt / Wien
            Franz-Josefs-Bahnhof ↔ Wien Nußdorf

    With the hub named once it reads ``Wien Franz-Josefs-Bahnhof ↔
    St.Andrä-Wördern / Wien Heiligenstadt / Wien Nußdorf``. The others keep
    the order of the description.
    """
    if len(canonical_routes) < 3:
        return None
    shared = set(canonical_routes[0]).intersection(*(set(pair) for pair in canonical_routes[1:]))
    if len(shared) != 1:
        return None
    (hub,) = shared
    return hub, [b if a == hub else a for a, b in canonical_routes]


# How much longer the way from A to B via a station X may be than the direct
# distance A–B for X to still count as lying on the stretch A ↔ B. The
# Franz-Josefs-Bahn bends north along the Danube before it turns west: Wien
# Nußdorf adds 11 % to Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau, the
# most of any station on that line in the cache.
_CORRIDOR_DETOUR_FACTOR = 1.2


def _lies_on_route(
    point: StationInfo | None, start: StationInfo | None, end: StationInfo | None
) -> bool:
    """``True`` if *point* is a station on the way between *start* and *end*.

    Two conditions, both required: the detour via *point* stays within
    :data:`_CORRIDOR_DETOUR_FACTOR` of the direct distance, and one ÖBB line
    serves all three stations (:func:`station_lines`). The second rules out
    a station of another line that merely lies in the same direction —
    Flughafen Wien lies straight between Wien Hauptbahnhof and Bruck an der
    Leitha, but no line runs through all three. Unknown coordinates or
    lines count as "not on the way".
    """
    if point is None or start is None or end is None:
        return False
    coords: list[tuple[float, float]] = []
    for info in (point, start, end):
        if info.latitude is None or info.longitude is None:
            return False
        coords.append((info.latitude, info.longitude))
    (p_lat, p_lon), (s_lat, s_lon), (e_lat, e_lon) = coords
    direct = calculate_distance_meters(s_lat, s_lon, e_lat, e_lon)
    if not direct > 0:
        return False
    via = calculate_distance_meters(s_lat, s_lon, p_lat, p_lon) + calculate_distance_meters(
        p_lat, p_lon, e_lat, e_lon
    )
    if via > direct * _CORRIDOR_DETOUR_FACTOR:
        return False
    return bool(
        station_lines(point.name) & station_lines(start.name) & station_lines(end.name)
    )


def _drop_contained_routes(
    pairs: list[tuple[str, str]], infos: dict[str, StationInfo | None]
) -> list[tuple[str, str]]:
    """Drop every route that is a part of another route of the same message.

    ÖBB lists each affected line with its own stretch. The Franz-Josefs-Bahn
    works of November 2026 named four, all starting at Wien Franz-Josefs-
    Bahnhof and ending at St.Andrä-Wördern, Tulln an der Donau, Wien
    Heiligenstadt and Wien Nußdorf, and the title listed all four ends
    (125 characters on a display read from a distance). Every one of them
    lies on the way to Tulln an der Donau, so the title is that one
    stretch, ``Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau``; the
    description keeps each line's own stretch.

    A route is part of another when each of its ends is an end of the
    other or lies on the way between them (:func:`_lies_on_route`).
    Routes that are not part of another one stay as they are.
    """
    if len(pairs) < 2:
        return pairs

    def part_of(inner: tuple[str, str], outer: tuple[str, str]) -> bool:
        ends = {name.casefold() for name in outer}
        return all(
            name.casefold() in ends
            or _lies_on_route(infos.get(name), infos.get(outer[0]), infos.get(outer[1]))
            for name in inner
        )

    return [
        pair
        for pair in pairs
        if not any(other is not pair and part_of(pair, other) for other in pairs)
    ]


def _format_route_title(routes: list[tuple[str, str]], line_prefix: str = "") -> str:
    """Build a clean ``A ↔ B`` title from extracted route(s).

    For each route we use the canonical station name from the directory when
    available (so ``Wien Westbf`` → ``Wien Westbahnhof``). The Vienna endpoint
    is placed first to keep the feed visually consistent. Multiple routes are
    joined with ``" / "`` to indicate that several segments are affected.

    When multiple routes share endpoints to form a corridor (e.g. ``A ↔ B``,
    ``B ↔ C``, ``A ↔ D`` collapses to the linear chain ``D ↔ A ↔ B ↔ C``),
    we render them as one ``A ↔ B ↔ C ↔ D`` string instead — the
    "/ "-separated form repeats the hub station and is harder to scan.

    Routes that resolve to the same canonical endpoint pair (e.g. one
    description writes ``St. Pölten`` while another writes ``St.Pölten``)
    are deduplicated here — the upstream extraction keys on raw casefold
    text so whitespace variants both survive. Without this pass the
    formatted title repeats the same route twice.
    """
    if not routes:
        return ""

    # Step 1: canonicalise every endpoint pair and dedup case-insensitively.
    canonical_pairs: list[tuple[str, str]] = []
    seen_canon: set[tuple[str, str]] = set()
    infos: dict[str, StationInfo | None] = {}
    for raw_a, raw_b in routes:
        info_a = station_info(raw_a)
        info_b = station_info(raw_b)
        name_a = info_a.name if info_a else raw_a
        name_b = info_b.name if info_b else raw_b
        # Apply feed-display overrides (also strips a trailing "(VOR)"
        # provenance suffix that is irrelevant for the user-facing title).
        name_a = display_name(name_a)
        name_b = display_name(name_b)
        name_a = _expand_station_abbreviations(name_a)
        name_b = _expand_station_abbreviations(name_b)
        infos.setdefault(name_a, info_a)
        infos.setdefault(name_b, info_b)

        # Vienna endpoint always goes first when only one side is in Vienna.
        a_in_vienna = bool(info_a and info_a.in_vienna)
        b_in_vienna = bool(info_b and info_b.in_vienna)
        if b_in_vienna and not a_in_vienna:
            name_a, name_b = name_b, name_a

        canon_key: tuple[str, str] = tuple(  # type: ignore[assignment]
            sorted((name_a.casefold(), name_b.casefold()))
        )
        if canon_key in seen_canon:
            continue
        seen_canon.add(canon_key)
        canonical_pairs.append((name_a, name_b))

    if not canonical_pairs:
        return ""

    # Step 2: a stretch that is part of another one is not repeated.
    canonical_pairs = _drop_contained_routes(canonical_pairs, infos)

    # Step 3: try to render multi-route titles as a single chain.
    title_body: str
    if len(canonical_pairs) >= 2:
        chain = _try_chain_routes(canonical_pairs)
        star = _try_star_routes(canonical_pairs)
        if chain is not None:
            title_body = " ↔ ".join(chain)
        elif star is not None:
            title_body = f"{star[0]} ↔ " + " / ".join(star[1])
        else:
            title_body = " / ".join(f"{a} ↔ {b}" for a, b in canonical_pairs)
    else:
        a, b = canonical_pairs[0]
        title_body = f"{a} ↔ {b}"

    if line_prefix:
        return f"{line_prefix}: {title_body}"
    return title_body


# ---------------- Public ----------------


# Match an affected line code in the description. We require the line
# code to be followed by a clear "Züge"/"Zug" disruption signal so we
# don't pick up alternative-route references like
# ``U3: Wien Ottakring <=> Hütteldorfer Straße`` (which were silently
# prepended as the title prefix and produced wrong titles like
# ``U3: Wien Westbahnhof ↔ St. Pölten`` for an S-Bahn disruption).
#
# ``R`` and ``CJX`` count too: "keine R 40-Züge" was missed on 2026-10-01,
# and the second line of the message, "REX 41", became the prefix of a
# message about four lines (audit 2026-10-01, open point 3).
_LINE_TOKEN_RE = re.compile(
    r"\b((?:REX|CJX|S(?:-Bahn)?|U|R)\s*\d+)\s*[-\s]Z[üu]g",
    re.IGNORECASE,
)


def _normalize_line_token(token: str) -> str:
    """Render an ÖBB line marker in the canonical ``LETTERS DIGITS`` form.

    The description text sometimes glues the letter prefix to the digit
    suffix (``REX7-Züge``, ``S50-Züge``) or uses the more verbose
    ``S-Bahn 50``. Both forms should surface in the user-facing title
    as ``REX 7`` / ``S 50`` for consistency with the official ÖBB
    rendering. We collapse interior whitespace runs and then insert a
    single space between the trailing letter and leading digit when
    they are glued together.
    """
    cleaned = re.sub(r"\s+", " ", token).strip()
    return re.sub(r"^([A-Za-z][A-Za-z-]*)(\d)", r"\1 \2", cleaned)


def _affected_lines(desc: str) -> str:
    """Every line *desc* says is disrupted, in the order it names them.

    ``R 40/REX 41/REX 4/S 40`` for the Franz-Josefs-Bahn works of November
    2026. One of four in front of the route read as if only that line
    were affected. In the ÖBB cache since September 3 of 45 messages
    named several lines (``REX 50/REX 51/S 50``, ``REX 6/REX 65``).
    """
    lines: list[str] = []
    for match in _LINE_TOKEN_RE.finditer(desc):
        line = _normalize_line_token(match.group(1))
        if line.casefold() not in {known.casefold() for known in lines}:
            lines.append(line)
    return "/".join(lines)


def _apply_route_title(title: str, desc: str) -> str:
    """Reconstruct a clean ``A ↔ B`` title from the authoritative endpoints
    parsed out of the description ("zwischen X und Y") whenever the route is
    Wien-relevant. Also injects a recognised line token (REX/S/U + number)
    from the description when not already present in the title. Supersedes
    messy raw titles like "Bauarbeiten: Flughafen Wien Wien Mitte-Landstraße"
    with canonical station names and drops the category prefix.

    Line-prefix sourcing: the description's ``-Züge`` mention is the
    authoritative signal for *which* line is disrupted (the WL ÖBB feed
    occasionally lists alternative U-Bahn routes by name, and a naive
    "first line token in description" match prepended those alternative
    line codes as a bogus title prefix). When the description identifies
    affected lines, they override any existing title prefix, all of them
    (:func:`_affected_lines`); otherwise the title's own prefix is kept.
    """
    desc_line = _affected_lines(desc)
    existing_line_prefix, _ = _extract_line_prefix(title)
    # Prefer the description-sourced affected line when it disagrees with
    # the cached title prefix (older cache items occasionally carry a
    # stale or wrong prefix like ``U3:`` for an S 50 disruption); fall
    # back to the existing prefix when the description has no affected-
    # line signal at all.
    if desc_line:
        effective_prefix = desc_line
    else:
        effective_prefix = existing_line_prefix

    routes = _extract_routes(title, desc)
    relevant_routes = [
        (a, b) for (a, b) in routes if _route_is_wien_relevant(a, b)
    ]
    if relevant_routes:
        return _format_route_title(relevant_routes, effective_prefix)

    if desc_line and not existing_line_prefix:
        return f"{desc_line}: {title}"
    return title
