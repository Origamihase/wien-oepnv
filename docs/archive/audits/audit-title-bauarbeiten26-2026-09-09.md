# Diagnostic Audit: "Bauarbeiten26" Title Anomaly

**Date:** 2026-09-09
**Affected Component:** `src/providers/wl_text.py` -> `_tidy_title_wl()`
**Display Issue Description:** Anomalous title `<title><![CDATA[ 17A: Bauarbeiten26 ]]></title>` observed in the Wiener Linien feed.

## Executive Summary
An investigation into the anomalous feed title `"17A: Bauarbeiten26"` reveals a flaw in the text sanitization pipeline. The issue is not upstream data quality, but rather an over-eager regular expression in `_tidy_title_wl()` (`src/providers/wl_text.py`) that partially strips dates but leaves 2-digit years stranded, which are then concatenated with preceding text.

## Data Comparison Table
| Stage | Title Value |
| :--- | :--- |
| **Raw JSON Title (Upstream / Deduced)** | `Bauarbeiten ab 14.09.26` |
| **Final Feed Output (Cached)** | `17A: Bauarbeiten26` |

## Root Cause Analysis
The anomaly is caused by the date-stripping regex in `src/providers/wl_text.py`:

```python
t = re.sub(r"\s+ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4})?", "", t, flags=re.IGNORECASE)
```

If the upstream Wiener Linien API provides a title containing a 2-digit year (e.g., `"Bauarbeiten ab 14.09.26"`), the regex behavior is as follows:
1. `\s+ab\s+` matches `" ab "`.
2. `\d{1,2}\.\d{1,2}\.` matches `"14.09."`.
3. `(?:\d{4})?` expects an optional 4-digit year. Since `"26"` is only 2 digits, it does not match, but the overall regex still matches the preceding part because the year is optional.

The substring `" ab 14.09."` is removed, leaving `"Bauarbeiten"` and `"26"`. A subsequent step collapses spaces, resulting in the malformed string `"Bauarbeiten26"`.

## Recommended Solution (Conceptual)
To fix this, the regex should be updated to account for 2-digit years as well as 4-digit years.

**Proposed Regex Modification:**
```python
# Before
r"\s+ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4})?"

# After
r"\s+ab\s+\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?"
```
This change would properly consume the 2-digit year, leaving only `"Bauarbeiten"`. As per strict task constraints, this fix has not been applied to the codebase.
