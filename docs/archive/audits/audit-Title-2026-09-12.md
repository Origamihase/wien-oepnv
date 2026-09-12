# Audit Report: Verbose Baustellen Titles

## Affected Component
- `src/providers/baustellen.py` (Title Processing)
- `scripts/update_baustellen_cache.py` (`_feature_to_event` title mapping)

## Display Issue Description
Currently, the OGD Stadt-Wien WFS feed provides highly verbose titles for construction sites (`baustellen`). These titles often contain redundant filler words and extraneous location details which make the feed entries excessively long and out of place compared to other concise transit updates.

**Examples identified:**
1. `Märzstraße 49 bis Kreuzung Huglgasse sowie Kreuzung Huglgasse bis Kreuzung Hütteldorfer Straße`
   - Issue: The word "Kreuzung" is repeated unnecessarily and disrupts readability.
2. `Kennedybrücke zwischen Schönbrunner Schloßstraße und Hadikgasse, auf Seite "Otto Wagner Hofpavillon"`
   - Issue: Trailing specifics such as `, auf Seite ...` (or alternatively `, Höhe ...`) add visual noise to a feed meant for quick scanning.

## Proposed Solution
Introduce a robust regex-based sanitization function (`tidy_title`) within the Baustellen provider module.

**Logic required:**
1. Strip occurrences of the redundant string `\bKreuzung\s+` (case-insensitive).
2. Strip trailing, overly-specific location addenda matching patterns like `,\s*(?:auf Seite|Höhe)\b.*$`.
3. Compress any resulting multi-space gaps (e.g., `\s{2,}`) back into a single space, and strip leading/trailing whitespace.
4. Hook this function into `scripts/update_baustellen_cache.py` inside `_feature_to_event`, right after extracting the title from properties, ensuring the processed titles are persisted in `cache/baustellen/events.json`.

This logic will preserve the essential context ("Märzstraße 49 bis Huglgasse") while trimming the excess.
