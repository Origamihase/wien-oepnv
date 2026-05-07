import hashlib
import re
from typing import Any

# Line-prefix grammar tolerant of two real-world spellings:
#
#   "U6: …", "1/2: …", "13A/14A: …"   (WL-style — no internal whitespace)
#   "REX 7: …", "S 50: …", "REX 7/REX 8: …"  (ÖBB-style — letters and digits
#   separated by whitespace)
#
# Without the optional inner space the ÖBB tokens silently fell through, so a
# disruption surfaced once via VOR ("REX7") and once via ÖBB ("REX 7") — two
# items in the feed for the exact same incident. Internal whitespace inside a
# token is normalised away in :func:`_parse_title` before the line set is
# compared.
_LINE_PREFIX_RE = re.compile(
    r"^\s*("
    # ÖBB style: REX 7, S 50, RJX 12. The optional ``-Bahn`` segment
    # tolerates verbose spellings like ``S-Bahn 50`` and ``U-Bahn 6``
    # so they can fuzzy-merge with the compact ``S 50`` / ``U6``
    # variants from another provider.
    r"[A-Za-z]+(?:-[Bb]ahn)?\s*\d{1,3}[A-Za-z]?"
    r"(?:\s*/\s*[A-Za-z]+(?:-[Bb]ahn)?\s*\d{1,3}[A-Za-z]?)*"
    r"|[A-Za-z0-9]+(?:\s*/\s*[A-Za-z0-9]+){0,20}"  # WL style: 1/2, U6, 13A
    r")\s*:\s*"
)
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3}[A-Z]?)$")

# Tokens that must not by themselves drive a fuzzy merge. The baseline list
# covers generic disruption verbs ("Störung", "Ausfall", …) that show up in
# nearly every title; the extended list adds German articles, prepositions
# and connector words ("im", "am", "bereich", …) so that two titles which
# only share these fillers do NOT get merged.
#
# Why this matters: without prepositions in the stop list, "U1: Störung im
# Bereich Praterstern" and "U1: Störung im Bereich Karlsplatz" share the
# tokens {"störung", "im", "bereich"} and exceed the 0.4 overlap threshold
# despite referring to two completely different stations.
_STOP_WORDS = {
    # Generic disruption nouns
    "störung",
    "stoerung",
    "ausfall",
    "ausfälle",
    "ausfaelle",
    "einschränkung",
    "einschraenkung",
    "betrieb",
    "verkehr",
    "verkehrsbehinderung",
    "fahrtbehinderung",
    "behinderung",
    "verspätung",
    "verspaetung",
    "verspätungen",
    "verspaetungen",
    "info",
    "information",
    "meldung",
    "hinweis",
    "sperre",
    "sperrung",
    "gesperrt",
    "umleitung",
    "ersatzverkehr",
    "kurzführung",
    "kurzfuehrung",
    "fahrt",
    "fahrten",
    "linie",
    "linien",
    "bauarbeiten",
    # Equipment/state words that recur across unrelated incidents
    "aufzug",
    "aufzüge",
    "aufzuege",
    "aufzugsinfo",
    "lift",
    "fahrstuhl",
    "fahrtreppe",
    "fahrtreppen",
    "fahrtreppeninfo",
    "rolltreppe",
    "rolltreppen",
    "defekt",
    "kaputt",
    "gestört",
    "gestoert",
    "blockiert",
    "weichenstörung",
    "weichenstoerung",
    "signalstörung",
    "signalstoerung",
    "polizeieinsatz",
    "rettungseinsatz",
    "feuerwehreinsatz",
    "notarzteinsatz",
    "personenschaden",
    "fahrzeugschaden",
    # German articles
    "der",
    "die",
    "das",
    "des",
    "den",
    "dem",
    "ein",
    "eine",
    "einer",
    "einem",
    "eines",
    # German prepositions
    "in",
    "im",
    "an",
    "am",
    "auf",
    "aus",
    "bei",
    "mit",
    "von",
    "vom",
    "zu",
    "zum",
    "zur",
    "nach",
    "durch",
    "für",
    "fuer",
    "gegen",
    "ohne",
    "um",
    "unter",
    "über",
    "ueber",
    "wegen",
    "während",
    "waehrend",
    "zwischen",
    "vor",
    "hinter",
    "neben",
    "ab",
    "bis",
    # German conjunctions
    "und",
    "oder",
    "aber",
    "sowie",
    "sondern",
    # Generic place-fillers
    "bereich",
    "höhe",
    "hoehe",
    "richtung",
    "fahrtrichtung",
    "station",
    "haltestelle",
    "haltestellen",
    "bahnhof",
    "bahnhst",
    "hbf",
    "bf",
    "bhf",
}


def _parse_title(title: str) -> tuple[set[str], str]:
    """
    Parses a title into a set of lines and the event name.
    Example: "1/2: Event Name" -> ({"1", "2"}, "Event Name")
    Whitespace inside a token is collapsed so "REX 7" matches "REX7" across
    providers.
    """
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return set(), title or ""

    lines_str = m.group(1)
    event_name = title[m.end() :].strip()

    lines = set()
    for raw in lines_str.split("/"):
        # Strip a verbose ``-Bahn`` suffix so "S-Bahn 50" and "S 50"
        # produce the same canonical token "S50" — without this both
        # would coexist in the feed for the same incident.
        cleaned = re.sub(r"-bahn", "", raw, flags=re.IGNORECASE)
        # Drop inner whitespace so "REX 7" and "REX7" become the same token.
        token = re.sub(r"\s+", "", cleaned).upper()
        if _LINE_TOKEN_RE.match(token):
            lines.add(token)

    return lines, event_name


def _normalize_name(name: str) -> str:
    """Removes digits and lowercases the name for comparison."""
    return re.sub(r"\d+", "", name).lower().strip()


def _get_tokens(name: str) -> set[str]:
    """Splits name into tokens by non-alphanumeric characters."""
    return set(x for x in re.split(r"\W+", _normalize_name(name)) if x)


def _has_significant_overlap(name1: str, name2: str) -> bool:
    """
    Checks if names share significant words or a long common substring.
    """
    return _has_significant_overlap_cached(
        _normalize_name(name1),
        _normalize_name(name2),
        _get_tokens(name1),
        _get_tokens(name2),
    )


def _has_significant_overlap_cached(
    n1: str, n2: str, t1: set[str], t2: set[str]
) -> bool:
    """Variant of ``_has_significant_overlap`` that consumes pre-computed
    normalized names and token sets.

    Performance: ``deduplicate_fuzzy``'s outer/inner loop is O(n²) over
    ``merged_items``. The original ``_has_significant_overlap`` recomputed
    ``_normalize_name`` (re.sub + lower + strip) and ``_get_tokens``
    (which itself calls ``_normalize_name`` again, plus an re.split) on
    BOTH names per pair — five regex operations × n² pairs. Caching the
    parsed values once when an item is appended to ``merged_items`` and
    reusing them on every comparison drops the parse work from O(n²) to
    O(n) without changing the comparison semantics.
    """
    if not n1 or not n2:
        return False

    # 1. Token Overlap
    intersection = t1 & t2
    union = t1 | t2
    if not union:
        return False

    # Compare only meaningful (non-stop-word) tokens. The intent: two
    # disruption titles must share something distinctive (typically a
    # station/place token) — sharing only "Störung" or "im Bereich" is not
    # enough.
    meaningful_intersection = intersection - _STOP_WORDS
    meaningful_union = union - _STOP_WORDS

    if not meaningful_union:
        # Both titles consist solely of stop words. Only merge if they are
        # token-identical — otherwise different stop-word combinations like
        # "Sperre wegen Bauarbeiten" and "Sperre wegen Polizeieinsatz"
        # would be falsely lumped together.
        return t1 == t2

    if not meaningful_intersection:
        # Distinguishing tokens exist somewhere, but none are shared. The
        # titles describe different events at the same generic level.
        return False

    # Token overlap threshold — measured against the meaningful tokens so
    # that sharing many fillers does not inflate the score.
    if len(meaningful_intersection) / len(meaningful_union) >= 0.4:
        return True

    return False


def _natural_keys(text: str) -> list[str | int]:
    """Helper for natural sorting of line numbers (e.g. U1, U2, U10)."""
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', text)]


def _calculate_line_overlap(lines1: set[str], lines2: set[str]) -> float:
    if not lines1 or not lines2:
        return 0.0
    intersection = len(lines1 & lines2)
    union = len(lines1 | lines2)
    return intersection / union


def _promote_newer_dates(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Copy any date field from *source* into *target* when it is newer.

    The dedup loop tolerates four spellings of the publication date for
    historic compatibility (``pubDate`` / ``pubdate`` / ``pub_date`` /
    ``updated``); the VOR↔ÖBB merge branches must use the same set so an
    incoming item with a newer ÖBB report bumps the merged item forward
    rather than keeping the older VOR timestamp.
    """
    for date_key in ("pubDate", "pubdate", "pub_date", "updated"):
        target_date = target.get(date_key)
        source_date = source.get(date_key)
        if target_date and source_date:
            try:
                if source_date > target_date:
                    target[date_key] = source_date
            except TypeError:
                # Mixed datetime / str types — leave the target unchanged.
                pass
        elif source_date and not target_date:
            target[date_key] = source_date


def _compute_overlap_cache(
    title: str,
) -> tuple[set[str], str, str, set[str]]:
    """Pre-compute the parse + normalize + tokenize results for one title.

    Performance: returned tuple captures everything ``deduplicate_fuzzy``'s
    inner loop needs — line set (from ``_parse_title``), event name,
    normalized name, and token set. The caller stores this alongside each
    ``merged_items`` entry so the inner loop never has to re-parse a
    previously-seen title.
    """
    lines, name = _parse_title(title)
    return lines, name, _normalize_name(name), _get_tokens(name)


def deduplicate_fuzzy(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merges items that are likely the same event affecting overlapping lines.

    Criteria:
    1. Significant name overlap (tokens or substring).
    2. > 30% line overlap.

    Performance:

    * **Parse cache (Apex pillar).** Maintains a parallel ``merged_cache``
      list of pre-computed ``(lines, name, normalized_name, tokens)``
      tuples mirroring ``merged_items``. Without it, every inner-loop
      iteration recomputed ``_parse_title`` + ``_normalize_name`` × 2 +
      ``_get_tokens`` × 2 (each with its own internal ``_normalize_name``)
      for the same ``existing`` entry, giving an O(n²) regex workload for
      what is fundamentally an O(n) parse problem. The cache reduces
      total parse work from O(n²) to O(n); set-comparison work in the
      inner loop stays O(n²) but operates on already-built sets, which
      is purely arithmetic and far cheaper.
    * **Shallow merge copy.** The merge branches use ``dict(existing)`` /
      ``dict(item)`` instead of ``copy.deepcopy`` because every mutation
      below targets a top-level scalar key (``description``, ``title``,
      ``guid``, ``_identity``, ``_calculated_identity``, the four date
      keys handled by ``_promote_newer_dates``). No nested structure is
      mutated in place — assignments replace the whole value — so the
      original dict's nested references stay untouched. Drops the
      per-merge cost from O(item-size) deep traversal to O(top-level-keys).
    """
    merged_items: list[dict[str, Any]] = []
    # Parallel cache mirroring merged_items[idx] — same length, same order.
    # Items with no lines (which can never participate in line-overlap
    # merges) get a placeholder empty cache; the line-set check at the
    # top of the inner loop short-circuits before any normalize/token
    # work is touched.
    merged_cache: list[tuple[set[str], str, str, set[str]]] = []

    for item in items:
        merged = False
        title = item.get("title", "")
        lines, name = _parse_title(title)

        # Skip items without lines? No, requirement says "affected lines".
        # If no lines, overlap is 0. So effectively skipped.
        if not lines:
            merged_items.append(item)
            merged_cache.append((set(), "", "", set()))
            continue

        # Compute the new item's normalize+token cache once before the
        # inner loop — reused on every existing-item comparison.
        norm_name = _normalize_name(name)
        tokens = _get_tokens(name)

        for idx, existing in enumerate(merged_items):
            ex_lines, ex_name, ex_norm_name, ex_tokens = merged_cache[idx]

            # If existing has no lines, can't merge based on line overlap
            if not ex_lines:
                continue

            line_overlap = _calculate_line_overlap(lines, ex_lines)

            # Optimization: Check lines first (cheaper)
            if line_overlap > 0.3:
                if _has_significant_overlap_cached(
                    norm_name, ex_norm_name, tokens, ex_tokens
                ):
                    # Provider Priority Logic
                    # VOR > ÖBB. If one is VOR and other is ÖBB, we prioritize VOR.
                    p1 = (existing.get("provider") or existing.get("source") or "").lower()
                    p2 = (item.get("provider") or item.get("source") or "").lower()

                    is_vor_existing = "vor" in p1
                    is_oebb_existing = "oebb" in p1 or "öbb" in p1
                    is_vor_item = "vor" in p2
                    is_oebb_item = "oebb" in p2 or "öbb" in p2

                    # Case 1: Existing is VOR, Item is ÖBB -> Keep Existing, merge ÖBB desc if useful
                    if is_vor_existing and is_oebb_item:
                        # Shallow copy is sufficient and intentional: every
                        # mutation below targets a top-level scalar key
                        # (``description``, ``_identity``, ``_calculated_identity``,
                        # the four date fields handled by ``_promote_newer_dates``).
                        # No nested structure is mutated in place — assignments
                        # replace the whole value — so the original dict's
                        # nested references stay untouched. Replaces a former
                        # ``copy.deepcopy(existing)`` call that was an O(item-size)
                        # allocation on every merge in an O(n²) loop.
                        new_existing = dict(existing)

                        desc_vor = new_existing.get("description", "") or ""
                        desc_oebb = item.get("description", "") or ""
                        if desc_oebb and " ".join(desc_oebb.split()) not in " ".join(desc_vor.split()):
                            new_existing["description"] = f"{desc_vor}\n\n{desc_oebb}".strip()

                        # Promote the newer pubDate so feed ordering reflects
                        # the latest report regardless of which provider
                        # currently owns the master record.
                        _promote_newer_dates(new_existing, item)

                        new_existing["_identity"] = new_existing.get("guid", "")
                        new_existing.pop("_calculated_identity", None)
                        # Update the list with the modified copy
                        merged_items[idx] = new_existing
                        # Title is unchanged (we keep VOR's master title) but
                        # refresh the cache slot defensively from the new dict
                        # so any future drift in the merge logic stays correct.
                        merged_cache[idx] = _compute_overlap_cache(
                            new_existing.get("title", "")
                        )
                        # Do NOT update GUID or Title from ÖBB (keep VOR master data)
                        merged = True
                        break

                    # Case 2: Existing is ÖBB, Item is VOR -> Replace Existing with Item
                    if is_oebb_existing and is_vor_item:
                        # Shallow copy of ``item`` for the same reason as Case 1:
                        # only top-level keys are mutated. The deep-copy here was
                        # paying for nested datetime / list traversal that no
                        # subsequent code touches.
                        new_existing = dict(item)

                        desc_oebb = existing.get("description", "") or ""
                        desc_vor = item.get("description", "") or ""

                        # Append ÖBB desc if not present
                        if desc_oebb and " ".join(desc_oebb.split()) not in " ".join(desc_vor.split()):
                            new_existing["description"] = f"{desc_vor}\n\n{desc_oebb}".strip()

                        # Same pubDate promotion as Case 1: take whichever
                        # report is newer (the master record may have been
                        # the older one).
                        _promote_newer_dates(new_existing, existing)

                        new_existing["_identity"] = new_existing.get("guid", "")
                        new_existing.pop("_calculated_identity", None)
                        merged_items[idx] = new_existing
                        # Replaced ÖBB-existing with VOR-item: title now
                        # comes from the VOR side, refresh the cache.
                        merged_cache[idx] = _compute_overlap_cache(
                            new_existing.get("title", "")
                        )
                        merged = True
                        break

                    # Standard Merge Logic (Peers)

                    # Same shallow-copy rationale as the VOR/ÖBB branches above —
                    # the peer-merge mutates ``title``, ``description``, ``guid``,
                    # ``_identity``, ``_calculated_identity`` and the date fields,
                    # all top-level scalars. The previous ``copy.deepcopy`` was
                    # the dominant allocator inside the O(n²) merge loop.
                    existing_copy = dict(existing)

                    # 1. Combine Lines
                    all_lines = sorted(list(lines | ex_lines))

                    # 2. Combine Names
                    new_name = ex_name
                    if name != ex_name:
                        if name in ex_name:
                            new_name = ex_name
                        elif ex_name in name:
                            new_name = name
                        else:
                            # Avoid duplicates in combined name
                            # e.g. "A & B" merged with "B" -> "A & B"
                            # Simple check:
                            parts = [p.strip() for p in ex_name.split("&")]
                            if name not in parts:
                                new_name = f"{ex_name} & {name}"

                    # Reconstruct Title
                    # Sort lines naturally (alphanumeric)?
                    # They are strings. '1', '10', '2'. We might want numeric sort if possible.
                    # But strict string sort is okay for now.
                    # Better sort: U1, U2... 1, 2...
                    # Existing build_feed doesn't seem to sort lines explicitly in title, just preserves them.
                    # Let's try to sort numerically if possible.
                    all_lines.sort(key=_natural_keys)
                    lines_part = "/".join(all_lines)
                    new_title = f"{lines_part}: {new_name}"
                    existing_copy["title"] = new_title

                    # 3. Merge Descriptions
                    desc1 = existing_copy.get("description", "") or ""
                    desc2 = item.get("description", "") or ""

                    if desc1 != desc2:
                        if desc1 and desc2:
                             # Check for containment
                            norm_desc1 = " ".join(desc1.split())
                            norm_desc2 = " ".join(desc2.split())
                            if norm_desc1 in norm_desc2:
                                existing_copy["description"] = desc2
                            elif norm_desc2 in norm_desc1:
                                existing_copy["description"] = desc1
                            else:
                                existing_copy["description"] = f"{desc1}\n\n{desc2}".strip()
                        elif desc2:
                            existing_copy["description"] = desc2

                    _promote_newer_dates(existing_copy, item)

                    # 4. Update GUID
                    # We create a new deterministic GUID based on the new title.
                    # This ensures clients see it as a new/updated item.
                    existing_copy["guid"] = hashlib.sha256(new_title.encode("utf-8")).hexdigest()
                    existing_copy["_identity"] = existing_copy["guid"]
                    existing_copy.pop("_calculated_identity", None)

                    merged_items[idx] = existing_copy
                    # Title was rebuilt above (lines_part + new_name); future
                    # comparisons against this slot must use the merged
                    # title's parse, not the pre-merge one.
                    merged_cache[idx] = _compute_overlap_cache(new_title)

                    # We might also want to merge start/end times?
                    # The requirement doesn't specify. Let's keep existing (usually "better" item).
                    # Actually, if we merge A into B, B is the "merged_item".
                    # We keep B's base properties.

                    merged = True
                    break

        if not merged:
            merged_items.append(item)
            # Reuse the (lines, name) we already parsed for this item;
            # only the normalize/token step was deferred until we knew
            # the item wasn't going to short-circuit on the no-lines
            # branch above. Avoids an extra _parse_title call.
            merged_cache.append((lines, name, norm_name, tokens))

    return merged_items
