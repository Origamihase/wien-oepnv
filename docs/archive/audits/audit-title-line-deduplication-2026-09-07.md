# Audit: Title Line Deduplication

## Executive Summary
This report analyzes a bug where redundant line prefixes are rendering in feed titles (e.g., `3A: 3A Netzänderung Betrieb ab Riemergasse`). The investigation revealed that Wiener Linien sometimes provides upstream titles where the line identifier appears twice (once with a colon, once without). The `_extract_prefix_lines` function correctly extracts the first line identifier (`3A`) due to the presence of a colon, but leaves the second, colon-less identifier (`3A`) in the title body since it does not match the strict line-prefix extraction regexes (`LINES_COMPLEX_PREFIX_RE` and `LINE_PREFIX_STRIP_RE`). As a result, the feed re-prepends the canonical line prefix to the body, producing the duplicated `3A: 3A` output. The translation pipeline preserves this structure, resulting in a mirrored duplicated string in the English feed.

## Data Comparison

The following table compares the raw data returned by Wiener Linien (extracted from `cache/wl_9d709a/events.json`) with the output in the generated RSS feeds.

| Field / Output | Value |
| :--- | :--- |
| **Raw Upstream JSON Title** | `3A: 3A Netzänderung Betrieb ab Riemergasse` |
| **German Feed Output (`docs/feed.xml`)** | `3A: 3A Netzänderung Betrieb ab Riemergasse` |
| **English Feed Output (`docs/feed.en.xml`)** | `3A: 3A Network change service from Riemergasse` |

## Code Pipeline Tracing

The path of the title through the pipeline reveals why the duplication is preserved rather than collapsed:

1. **Extraction from Source (`src/providers/wl_fetch.py`)**:
   - Upstream data provides the field `title` or `name` which gets fetched and assigned to `title_raw`.
   - The value is originally `3A: 3A Netzänderung Betrieb ab Riemergasse`.
   - `_tidy_title_wl(title_raw)` cleans it slightly, but does not alter this structure.
2. **Prefix Extraction (`src/providers/wl_lines.py`)**:
   - `_extract_prefix_lines(title)` looks for a colon-delimited line prefix block at the start of the title using `LINES_COMPLEX_PREFIX_RE` and `LINE_PREFIX_STRIP_RE`.
   - It successfully matches `3A:` because of the colon.
   - The body is parsed as `3A Netzänderung Betrieb ab Riemergasse` and returned along with `['3A']` as the `prefix_lines`.
   - The regex does not consume the second `3A` because there is no colon or separator pattern matching it in `LINE_PREFIX_STRIP_RE`.
3. **Recombination (`src/build_feed.py`)**:
   - In `_post_filter_wl`, the pipeline reconstructs the title: `rebuilt = f"{canonical}: {body}"`.
   - Since `canonical` is `"3A"` and `body` is `"3A Netzänderung Betrieb ab Riemergasse"`, the final string remains `3A: 3A Netzänderung Betrieb ab Riemergasse`.
4. **Translation**:
   - In `src/build_feed.py`, the MarianMT translation pipeline receives the string. The entity masking regex captures `3A` as an entity to prevent mangling. The translated suffix `Network change service from Riemergasse` is appended to the restored mask, resulting in `3A: 3A Network change service from Riemergasse`.

## Recommended Fix

A targeted sanitization step must be applied to the remaining `body` string to strip an exact duplicate of the canonical line prefix if it occurs without a colon.

Modify `_post_filter_wl` in `src/build_feed.py` when reconstructing the rebuilt string:
```python
import re

body, prefix_lines = _extract_prefix_lines(cleaned)
if prefix_lines and body:
    canonical = "/".join(prefix_lines)

    # NEW SANITIZATION STEP: Strip redundant line prefix at start of body
    escaped_canonical = re.escape(canonical)
    body = re.sub(rf"^{escaped_canonical}\s*[:\-–]?\s*", "", body, count=1, flags=re.IGNORECASE)

    rebuilt = f"{canonical}: {body}"
```

This safely normalizes cases where the line is repeated without affecting non-duplicative text. Since the task is read-only, this code change has **not** been applied to the project.