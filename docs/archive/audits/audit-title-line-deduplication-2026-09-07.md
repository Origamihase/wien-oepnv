# Audit: Title Line Deduplication & Redundant Prefix Analysis

**Date:** 2026-09-07
**Scope:** Wiener Linien Provider (`src/providers/wl_lines.py`, `src/providers/wl_fetch.py`) & Feed Processing (`src/build_feed.py`)
**Status:** Diagnostic Audit (Read-Only)

---

## 1. Executive Summary

This diagnostic audit investigates redundant line identifiers rendering in feed item titles (e.g., `<title><![CDATA[ 3A: 3A Netzänderung Betrieb ab Riemergasse ]]>`).

The investigation revealed that upstream Wiener Linien OGD Realtime payloads (`cache/wl_9d709a/events.json`) occasionally provide titles where the line identifier is stated twice at the beginning: once with a colon, immediately followed by the bare line identifier (e.g., `"3A: 3A Netzänderung..."`).

While `_extract_prefix_lines` in `src/providers/wl_lines.py` correctly parses and consumes the first token (`"3A:"`), it leaves the second, bare token (`"3A"`) untouched at the start of the title body because strict line-prefix regexes require a trailing separator. When the feed pipeline subsequently reconstructs the canonical title (`rebuilt = f"{canonical}: {body}"`), the line number is duplicated. The MarianMT translation pipeline protects these tokens via entity masking, thereby reflecting the identical duplication into `docs/feed.en.xml`.

**Cross-Provider Diagnostic Check**: A brief inspection of `src/providers/oebb.py` and `src/providers/vor.py` confirmed that this issue is strictly isolated to Wiener Linien OGD payloads. ÖBB uses a different mechanism (`_apply_route_title`) and VAO/VOR data formats differently, meaning they do not exhibit this specific `line: line body` duplicate prefix bug.

---

## 2. Source vs. Output Data Comparison

| Stage | Path / Source | Title Representation |
| :--- | :--- | :--- |
| **Upstream Source** | `cache/wl_9d709a/events.json` (`title` / `name`) | `3A: 3A Netzänderung Betrieb ab Riemergasse` |
| **Provider Parsed** | `_extract_prefix_lines()` output | `prefix_lines = ['3A']`<br>`body = '3A Netzänderung Betrieb ab Riemergasse'` |
| **German Feed** | `docs/feed.xml` (`<title>`) | `3A: 3A Netzänderung Betrieb ab Riemergasse` |
| **English Feed** | `docs/feed.en.xml` (`<title>`) | `3A: 3A Network change service from Riemergasse` |

---

## 3. Code Pipeline Tracing & Root Cause

1. **Upstream Ingestion (`src/providers/wl_fetch.py`)**
   - The raw JSON title string is captured as `title_raw`.
   - Function `_tidy_title_wl(title_raw)` performs baseline whitespace and punctuation normalization, but intentionally does not alter line numbers or semantics.

2. **Prefix Extraction (`src/providers/wl_lines.py`)**
   - `_extract_prefix_lines(title)` uses `LINES_COMPLEX_PREFIX_RE` and `LINE_PREFIX_STRIP_RE`.
   - It matches and strips `"3A:"` based on the colon delimiter.
   - The remaining string `"3A Netzänderung Betrieb ab Riemergasse"` is returned as `body`. The regex does not match the second `"3A"` because it is not followed by a recognized separator (e.g. `:`, `,`, `/`).

3. **Feed Recombination (`src/build_feed.py`)**
   - Inside `_post_filter_wl()`, the canonical representation is computed as `canonical = "/".join(prefix_lines)`.
   - The title is reformatted:
     ```python
     rebuilt = f"{canonical}: {body}"
     ```
   - Since `body` still starts with `"3A"`, the result is `"3A: 3A Netzänderung..."`.

4. **NMT Pipeline (`src/build_feed.py`)**
   - The translation module masks proper nouns and line markers (e.g., using `XENT` placeholders).
   - Both occurrences of `"3A"` are masked and preserved verbatim, locking the duplication into the English output.

---

## 4. Nuances & Edge Cases (Identified Weaknesses in Naive Fixes)

Any future fix must account for the following architectural and regex edge cases:

1. **Word Boundary Protection (`\b`):**
   - A naive stripping regex like `^{line}` would erroneously truncate line numbers on single-digit routes (e.g., route `1` followed by `"10er Garnitur getauscht"` or `"10. Bezirk"` would be corrupted into `"0er Garnitur..."` or `"0. Bezirk"`).
   - A strict word boundary `\b` is mandatory.

2. **Multi-Line Disruptions:**
   - When multiple lines are affected (e.g. `prefix_lines = ['11A', '11B']`), the canonical prefix is `"11A/11B"`.
   - Upstream descriptions frequently repeat only *one* of the lines (e.g., `"11A, 11B: 11A Gleisschaden..."`). Stripping only against `canonical` (`"11A/11B"`) would fail to remove the redundant `"11A"`.
   - Stripping candidates must include `canonical` **and** each element of `prefix_lines`.

3. **Candidate Order / Prefix Shadowing:**
   - The list of candidates MUST be sorted by length descending (`sorted(..., key=len, reverse=True)`). Otherwise, a shorter prefix might shadow a longer one (e.g. `1` could match before `11A`), leading to partial strips or failing to strip the most specific redundant prefix.

4. **Leading Whitespace & Dash Variations:**
   - The regex must support optional leading whitespace (`^\s*`) and full dash variants (hyphen `-`, en-dash `–`, em-dash `—`) between the line identifier and the rest of the text, so `3A - Netzänderung` correctly reduces to `Netzänderung`.

5. **Architectural Placement (Provider vs. Feed Builder):**
   - While `_post_filter_wl()` in `src/build_feed.py` could fix the title, placing the normalization directly inside `_extract_prefix_lines()` in `src/providers/wl_lines.py` is architecturally superior:
     - It ensures `body` is consistently clean across all downstream consumers and export formats.
     - It allows isolated, lightweight unit testing in `tests/test_parse_lines_from_title.py` without mocking the feed generator.

---

## 5. Recommended Solution (For Future Implementation)

When ready to implement, `_extract_prefix_lines()` in `src/providers/wl_lines.py` should be augmented to strip immediate residual leading line identifiers from `body`:

```python
# Conceptual fix for src/providers/wl_lines.py:
if prefix_lines and body:
    # Build candidate tokens: joined canonical + individual route identifiers
    canonical = "/".join(prefix_lines)
    candidates = [re.escape(canonical)] + [re.escape(line) for line in prefix_lines]

    # Sort candidates by length descending to prevent prefix shadowing
    candidates = sorted(candidates, key=len, reverse=True)

    # Strip redundant leading line identifier with strict word boundary, supporting whitespace and dash variants
    redundant_pattern = rf"^\s*(?:{'|'.join(candidates)})\b\s*[:\-–—]?\s*"
    body = re.sub(redundant_pattern, "", body, count=1, flags=re.IGNORECASE).strip()

```

### Proposed Regression Test Cases:

* **Single-Line Duplicate:** `"3A: 3A Netzänderung"` -> Line: `3A`, Body: `"Netzänderung"`
* **Multi-Line Duplicate:** `"11A, 11B: 11A Gleisschaden"` -> Lines: `['11A', '11B']`, Body: `"Gleisschaden"`
* **Word Boundary Guard:** `"1: 10er Garnitur im Einsatz"` -> Line: `1`, Body: `"10er Garnitur im Einsatz"` (must not alter `10`)
* **Colon Variations:** `"U1: U1 - Gleisarbeiten"` -> Line: `U1`, Body: `"Gleisarbeiten"`
