import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Set, Tuple

# Regex adapted from build_feed.py
_LINE_PREFIX_RE = re.compile(r"^\s*([A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20})\s*:\s*")
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3})$")


def _parse_title(title: str) -> Tuple[Set[str], str]:
    """
    Parses a title into a set of lines and the event name.
    Example: "1/2: Event Name" -> ({"1", "2"}, "Event Name")
    """
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return set(), title or ""

    lines_str = m.group(1)
    event_name = title[m.end() :].strip()

    lines = set()
    for raw in lines_str.split("/"):
        token = raw.strip().upper()
        if _LINE_TOKEN_RE.match(token):
            lines.add(token)

    return lines, event_name


def _normalize_name(name: str) -> str:
    """Removes digits and lowercases the name for comparison."""
    return re.sub(r"\d+", "", name).lower().strip()


def _get_tokens(name: str) -> Set[str]:
    """Splits name into tokens by non-alphanumeric characters."""
    return set(x for x in re.split(r"\W+", _normalize_name(name)) if x)


def _has_significant_overlap(name1: str, name2: str) -> bool:
    """
    Checks if names share significant words or a long common substring.
    """
    n1 = _normalize_name(name1)
    n2 = _normalize_name(name2)

    if not n1 or not n2:
        return False

    t1 = _get_tokens(name1)
    t2 = _get_tokens(name2)

    # 1. Token Overlap
    # Filter out very short tokens (e.g. "a", "of") if necessary?
    # Prompt says: "ignoring numbers/years" (already done in normalize)
    if not t1.isdisjoint(t2):
        return True

    # 2. Substring Overlap (for compound words like Silvesterlauf vs Silvesterpfad)
    # Check for Longest Common Substring
    match = SequenceMatcher(None, n1, n2).find_longest_match(0, len(n1), 0, len(n2))
    # Threshold: 5 chars seems reasonable for "significant" (e.g. "Umbau", "Demo", "Silve" from Silvester)
    if match.size >= 5:
        return True

    return False


def _calculate_line_overlap(lines1: Set[str], lines2: Set[str]) -> float:
    if not lines1 or not lines2:
        return 0.0
    intersection = len(lines1 & lines2)
    union = len(lines1 | lines2)
    return intersection / union


def deduplicate_fuzzy(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merges items that are likely the same event affecting overlapping lines.

    Criteria:
    1. Significant name overlap (tokens or substring).
    2. > 30% line overlap.
    """
    merged_items: List[Dict[str, Any]] = []

    for item in items:
        merged = False
        title = item.get("title", "")
        lines, name = _parse_title(title)

        # Skip items without lines? No, requirement says "affected lines".
        # If no lines, overlap is 0. So effectively skipped.
        if not lines:
            merged_items.append(item)
            continue

        for existing in merged_items:
            ex_title = existing.get("title", "")
            ex_lines, ex_name = _parse_title(ex_title)

            # If existing has no lines, can't merge based on line overlap
            if not ex_lines:
                continue

            line_overlap = _calculate_line_overlap(lines, ex_lines)

            # Optimization: Check lines first (cheaper)
            if line_overlap > 0.3:
                if _has_significant_overlap(name, ex_name):
                    # Merge Logic

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
                    def natural_keys(text):
                        return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', text)]

                    all_lines.sort(key=natural_keys)
                    lines_part = "/".join(all_lines)
                    new_title = f"{lines_part}: {new_name}"
                    existing["title"] = new_title

                    # 3. Merge Descriptions
                    desc1 = existing.get("description", "") or ""
                    desc2 = item.get("description", "") or ""

                    if desc1 != desc2:
                        if desc1 and desc2:
                             # Check for containment
                            if desc1 in desc2:
                                existing["description"] = desc2
                            elif desc2 in desc1:
                                existing["description"] = desc1
                            else:
                                existing["description"] = f"{desc1}\n\n{desc2}"
                        elif desc2:
                            existing["description"] = desc2

                    # 4. Update GUID
                    # We create a new deterministic GUID based on the new title.
                    # This ensures clients see it as a new/updated item.
                    existing["guid"] = hashlib.sha256(new_title.encode("utf-8")).hexdigest()

                    # We might also want to merge start/end times?
                    # The requirement doesn't specify. Let's keep existing (usually "better" item).
                    # Actually, if we merge A into B, B is the "merged_item".
                    # We keep B's base properties.

                    merged = True
                    break

        if not merged:
            merged_items.append(item)

    return merged_items
