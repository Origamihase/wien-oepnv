from collections.abc import Callable, Mapping
from typing import Any

from ..providers.wl_lines import line_order_key
from .merge_items import (
    _calculate_line_overlap,
    _is_wl_disruption,
    _may_merge,
    _merge_validity,
    _promote_newer_dates,
    member_guids,
)
from .merge_texts import (
    _DATE_PIECE_RE,
    _SENTENCE_TERMINATOR_RE,
    _TRAILING_DIRECTIONAL_CHARS,
    _collapse_common_prefix,
    _collapse_description_prefix,
    _date_sort_key,
    _drop_restated,
    _join_merged_names,
    _restates_title,
    _trim_trailing_directional,
)
from .merge_titles import (
    _LINE_PREFIX_RE,
    _LINE_TOKEN_RE,
    _PLATFORM_NUMBER_RE,
    _STOP_WORDS,
    _compute_overlap_cache,
    _get_tokens,
    _has_significant_overlap,
    _has_significant_overlap_cached,
    _normalize_name,
    _parse_title,
    _platform_numbers,
)

__all__ = [
    "_LINE_PREFIX_RE",
    "_LINE_TOKEN_RE",
    "_STOP_WORDS",
    "_parse_title",
    "_normalize_name",
    "_get_tokens",
    "_PLATFORM_NUMBER_RE",
    "_platform_numbers",
    "_has_significant_overlap",
    "_has_significant_overlap_cached",
    "_compute_overlap_cache",
    "_DATE_PIECE_RE",
    "_date_sort_key",
    "_collapse_common_prefix",
    "_SENTENCE_TERMINATOR_RE",
    "_collapse_description_prefix",
    "_TRAILING_DIRECTIONAL_CHARS",
    "_trim_trailing_directional",
    "_restates_title",
    "_drop_restated",
    "_join_merged_names",
    "_is_wl_disruption",
    "_may_merge",
    "_calculate_line_overlap",
    "_promote_newer_dates",
    "member_guids",
    "_merge_validity",
    "deduplicate_fuzzy",
]


def deduplicate_fuzzy(
    items: list[dict[str, Any]],
    *,
    announced: Callable[[Mapping[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """
    Merges items that are likely the same event affecting overlapping lines.

    Criteria:
    1. Significant name overlap (tokens or substring).
    2. > 30% line overlap.
    3. Both items announced (begin after the preview day, *announced*) or
       neither; a WL stop relocation never with a WL disruption
       (``_may_merge``, 2026-11-07).

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

    # Stable, input-order-independent iteration. ``deduplicate_fuzzy``
    # uses first-match-wins on the inner loop (``break`` after the merge),
    # so the SURVIVOR of any merge group is whichever item arrives in
    # ``items`` first. Without this sort the result depends on the
    # arbitrary upstream concatenation order — three items with a
    # transitive overlap graph (``A↔C``, ``B↔C``, but no direct ``A↔B``)
    # produce ``[AC, B]`` from ``[A, B, C]`` and ``[BC, A]`` from
    # ``[B, A, C]`` because ``C`` merges with whichever of ``A`` / ``B``
    # the iteration encountered first. Sorting by the per-item stable
    # identity tuple makes survivor selection deterministic across
    # runs, so the merged item's preserved ``_identity`` / ``guid``
    # (see the peer-merge branch below) stays the same cycle to cycle.
    items = sorted(
        items,
        key=lambda it: (
            str(it.get("_identity") or ""),
            str(it.get("guid") or ""),
            str(it.get("title") or ""),
        ),
    )

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
        plat = _platform_numbers(name)

        for idx, existing in enumerate(merged_items):
            ex_lines, ex_name, ex_norm_name, ex_tokens = merged_cache[idx]

            # If existing has no lines, can't merge based on line overlap
            if not ex_lines:
                continue

            line_overlap = _calculate_line_overlap(lines, ex_lines)

            # Optimization: Check lines first (cheaper)
            if _may_merge(line_overlap, existing, item, announced):
                if _has_significant_overlap_cached(
                    norm_name, ex_norm_name, tokens, ex_tokens,
                    plat, _platform_numbers(ex_name),
                ):
                    # Provider priority logic — legacy VOR disruption provider only.
                    # The standalone VOR disruption provider (source == "vor") was
                    # removed 2026-05-11. The only remaining "vor"-ish source is the
                    # Stammstrecke delay monitor (source "VOR/VAO"): an INDIRECT,
                    # derived signal that must NOT override real ÖBB / WL disruptions.
                    # Match the provider name EXACTLY so "vor/vao" is excluded from
                    # this priority and merges as a normal peer (ÖBB stays master).
                    p1 = (existing.get("provider") or existing.get("source") or "").lower()
                    p2 = (item.get("provider") or item.get("source") or "").lower()

                    is_vor_existing = p1 == "vor"
                    is_oebb_existing = "oebb" in p1 or "öbb" in p1
                    is_vor_item = p2 == "vor"
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
                                # Try a smarter collapse first — when
                                # ``ex_name`` and ``name`` share a
                                # substantial word-aligned prefix
                                # (e.g. ``Veranstaltung am 09.06.2026``
                                # + ``Veranstaltung am 03.06.2026``),
                                # keep the prefix once and join the
                                # differing suffixes with ``, ``
                                # instead of ` & `. Falls through to
                                # the legacy ``&``-join if the
                                # collapse declines (short prefix,
                                # long suffix, ÖBB ``↔`` chain).
                                new_name = _join_merged_names(ex_name, name)

                    # Reconstruct Title, lines in WL's order (``line_order_key``)
                    all_lines.sort(key=line_order_key)
                    lines_part = "/".join(all_lines)
                    new_title = f"{lines_part}: {new_name}"
                    existing_copy["title"] = new_title

                    # 3. Merge Descriptions
                    desc1 = existing_copy.get("description", "") or ""
                    desc2 = item.get("description", "") or ""

                    # A description the merged title already states adds
                    # nothing and must not be stacked under the other
                    # one — see ``_restates_title``. Checked on both
                    # sides so the result does not depend on which item
                    # happened to arrive first. ``desc1`` is written
                    # back unconditionally: it is either unchanged or
                    # emptied, and a branch here would push this
                    # baselined function's C901 up.
                    desc1, desc2 = _drop_restated(desc1, desc2, new_name)
                    existing_copy["description"] = desc1

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
                                # Strip trailing WL directional markers
                                # (``<``/``>``) from each side BEFORE
                                # joining so the marker doesn't end up
                                # in the middle of the combined text
                                # (real case: ``Betrieb ab Gersthof <``
                                # + ``Linie 40: …`` → ``Betrieb ab
                                # Gersthof < Linie 40: …`` reads as
                                # a stray glyph in the user's feed).
                                clean1 = _trim_trailing_directional(desc1)
                                clean2 = _trim_trailing_directional(desc2)
                                # The TITLE above collapses a shared
                                # word-aligned prefix via
                                # ``_join_merged_names``; the description
                                # used to concatenate verbatim, shared
                                # prefix and all. Published side by side::
                                #
                                #   T: O: Schadhafter Bus Betrieb ab
                                #      Praterstern, Quartier Belvedere
                                #   D: Schadhafter Bus Betrieb ab
                                #      Praterstern Schadhafter Bus Betrieb
                                #      ab Quartier Belvedere
                                #
                                # Same collapse, same constraints (≥10
                                # shared chars on a word boundary, new
                                # suffix ≤60, no ÖBB ``↔`` chain) plus
                                # one of its own: sentence-form texts
                                # decline. When it declines, the legacy
                                # blank-line join stands — two long,
                                # unrelated sentences read better
                                # stacked than comma-joined.
                                existing_copy["description"] = (
                                    _collapse_description_prefix(clean1, clean2)
                                    or f"{clean1}\n\n{clean2}".strip()
                                )
                        elif desc2:
                            existing_copy["description"] = desc2

                    _promote_newer_dates(existing_copy, item)
                    _merge_validity(existing_copy, item)
                    existing_copy["_members"] = sorted(set(member_guids(existing)) | set(member_guids(item)))

                    # 4. Preserve survivor's GUID and ``_identity``.
                    # Pre-fix the peer-merge rehashed ``guid = sha256(new_title)``
                    # and set ``_identity = guid`` to "signal clients see a new
                    # / updated item". That broke ``first_seen`` tracking: the
                    # build-time state lookup keys on the guid (preferred by
                    # ``_state_key_for_item``) with a legacy fallback to
                    # ``_identity_for_item``. Both keys came from the same
                    # ``sha256(new_title)``, so every cycle in which the
                    # merge grouping or the combined title shifted produced
                    # a fresh hash → fresh state key → fresh ``first_seen``,
                    # and the merged disruption was perpetually treated as
                    # brand-new (re-published via the fresh-pubDate window,
                    # FIFO retirement could never fire). Survivor selection
                    # is now deterministic via the top-of-function sort, so
                    # the survivor's ``guid`` and ``_identity`` (carried
                    # forward by the ``dict(existing)`` copy above) stay
                    # stable across cycles. Updates flow through ``title``
                    # / ``description`` / ``pubDate`` — the channels RSS
                    # clients actually use to detect updates.
                    existing_copy.pop("_calculated_identity", None)

                    merged_items[idx] = existing_copy
                    # Title was rebuilt above (lines_part + new_name); future
                    # comparisons against this slot must use the merged
                    # title's parse, not the pre-merge one.
                    merged_cache[idx] = _compute_overlap_cache(new_title)

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
