# Deduplication Quality Analysis

This document summarizes the analysis of the deduplication logic in `src/build_feed.py` and the data providers.

## Core Logic (`src/build_feed.py`)

The deduplication logic (`_dedupe_items`) is robust and uses a "best-candidate" strategy:
1.  **Grouping**: Items are grouped by `_identity` (explicit ID), `guid`, or a hash of their content (`source|title|description`).
2.  **Selection**: When duplicates are found, the system selects the "better" item based on:
    *   **Ends At**: Items ending later are preferred (keeps active disruptions visible).
    *   **Recency**: Newer items (`pubDate`, `starts_at`) are preferred.
    *   **Content Length**: Longer descriptions are preferred (assumed to contain more information).

## Provider Compliance

The quality of deduplication relies heavily on providers supplying stable IDs.

| Provider | Status | Analysis |
| :--- | :--- | :--- |
| **Wiener Linien** | ✅ Excellent | Constructs stable `_identity` from Category, Lines, and Date (e.g., `wl|störung|L=U1|D=2023-10-01`). Updates to text do not break the ID. |
| **ÖBB** | ✅ Excellent | Uses RSS `guid` or explicit `_identity` derived from it. Handles title cleanup to prevent cosmetic changes from breaking IDs. |
| **VOR** | ✅ Good | Constructs `guid` from `message['id']`. If ID is missing, falls back to hashing `head`+`text`. Most API messages have IDs. |

## Verification

A test suite (`tests/test_deduplication_quality.py`) was added to verify:
*   Exact duplicates are merged.
*   Updates with the same GUID replace older items.
*   "Better" items (e.g., later end date) are preserved.
*   Fallback behavior (hashing) works for identical content but fails for updates (creates duplicates) if no GUID is present.

## Conclusion

The deduplication quality is **high**. The system correctly handles updates and merges duplicates for all configured providers. The only theoretical weakness (duplicates on text updates without GUIDs) is mitigated by all current providers implementing stable IDs.
