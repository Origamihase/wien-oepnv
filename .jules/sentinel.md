# Sentinel's Journal

## 2026-05-25 - Stored HTML/JS Injection (XSS) in Published `<description>` — Unescaped Sibling of the `<content:encoded>` Output-Encoding Fix

**Vulnerability:** The 2026-05-24 round HTML-escaped the `<content:encoded>`
CDATA body but EXPLICITLY left the per-item `<description>` XML text node
unescaped, with the comment *"``desc_text`` below is for the `<description>`
XML text node and is left unescaped — ElementTree applies the correct XML
escaping there."* That reasoning conflates XML-**structural** safety with
HTML-**rendering** safety — the exact confusion the 2026-05-24 entry itself
diagnosed for `<content:encoded>`. `<description>` is rendered as HTML by the
overwhelming majority of RSS readers (RSS 2.0 convention; `content:encoded`
was added only to carry the *full* body). The same upstream
`&lt;img src=x onerror=…&gt;` that `html_to_text`
(`HTMLParser(convert_charrefs=True)`) decodes into a LIVE `<img onerror=…>`
in `summary` (`src/build_feed.py:_format_item_content`) flows unescaped through
`_compose_description` → `desc_text_truncated` → `<description>`.text
(`_emit_item`). ElementTree escapes `<>&` exactly ONCE for XML
well-formedness; a conformant reader XML-decodes that node exactly once →
`<img onerror=…>` → and (rendering description as HTML) executes it. Stored
XSS on the public feed (`https://origamihase.github.io/wien-oepnv/feed.xml`),
the direct sibling of the `<content:encoded>` vector. `_sanitize_text`
(`_CONTROL_RE`) strips only invisible/control/BiDi/zero-width chars and NEVER
`<` (0x3C) / `>` (0x3E), so it never closed this. Confirmed by an end-to-end
PoC (`tests/test_description_html_injection.py`) that drives the real
`_emit_item`, serialises the actually-published `<description>` element to XML,
re-parses it (the reader's single XML-decode), then HTML-renders the result
with a reader-accurate `HTMLParser(convert_charrefs=True)`: pre-fix the live
`img` / `script` start tag + the `onerror` event-handler attribute survive
(`live_tags == ['img']` / `['script']`); post-fix the bytes are inert escaped
source (`&lt;img…&gt;`).

**Learning:** Output-encoding must cover EVERY field a reader renders as HTML —
not just the one CDATA field. The fix is contextual output-encoding AT THE
SINK: `html.escape(formatted.desc_text_truncated, quote=False)` on the
`<description>` `ET.SubElement` in `_emit_item` — the single per-item
`<description>` sink for BOTH the DE and EN feeds (`_emit_item` is invoked once
per language with the language-resolved `FormattedContent`, so one escape
covers both). Escaping at the SINK (not inside `_compose_description`, where the
2026-05-24 `desc_html` fix lives) keeps `desc_text_truncated` PLAIN display text
so the WL directional-`>` markers, line-prefix logic and truncation tests keep
operating on the unescaped form — the XML-text-node-rendered-as-HTML encoding
belongs to the sink that *knows* the rendering context (textbook contextual
output-encoding). CDATA-HTML bodies pre-encode at composition (the body is
*built* as HTML there); XML-text-node-rendered-as-HTML fields encode at their
element sink. The general rule from 2026-05-24 — *"every sink that renders its
output as HTML must HTML-escape it"* — had enumerated ONLY `<content:encoded>`;
`<description>` is the second member and was hiding behind ElementTree's XML
escaping. The named "plain-text-into-raw-HTML/CDATA audit walker" watch-list
item now has TWO confirmed members; a future generic walker must enumerate both
(a) CDATA HTML bodies AND (b) XML text nodes whose RSS element name is
HTML-rendered by convention (`description`, `content:encoded`) — the latter is
the trap, because ElementTree silently makes the XML *look* safe.

## 2026-05-24 - Stored HTML/JS Injection (XSS) in Published `<content:encoded>` via `html_to_text` Entity Double-Decode

**Vulnerability:** `_compose_description` (`src/build_feed.py`) built the
`desc_html` body of the feed's `<content:encoded>` element by joining the
PLAIN-TEXT `summary` + `time_line` with `<br/>` and emitting it verbatim
inside a raw CDATA block (`<![CDATA[{desc_cdata}]]>`) — the one feed field
every conformant RSS reader renders as HTML. The summary comes from
`html_to_text` (`src/utils/text.py`), which runs the upstream description
through `HTMLParser(convert_charrefs=True)`. That parser DECODES entity-
escaped angle brackets into its output: an upstream `description` of
`&lt;img src=x onerror=alert(...)&gt;` (the literal form for the JSON
providers WL/VOR; the double-escaped `&amp;lt;...` form for the XML/RSS ÖBB
provider that survives one XML-decode layer) becomes the LIVE string
`<img src=x onerror=alert(...)>` in the "plain text". With no output-encoding
at the HTML sink the tag reached `<content:encoded>` intact and executed in
subscribers' readers — stored XSS / HTML-injection on the public feed
(`https://origamihase.github.io/wien-oepnv/feed.xml`). Confirmed by an
end-to-end PoC that drives the real `_format_item_content` pipeline and
parses the resulting CDATA body with a reader-accurate `HTMLParser`
(`tests/test_content_encoded_html_injection.py`): pre-fix the `<img>` start
tag + `onerror` event-handler attribute survive; post-fix the bytes are
inert escaped text (`&lt;img…`). Literal `<script>`/`<img>` tags were already
dropped by `_HTMLToTextParser` (`_IGNORE_TAGS` + tags-not-captured) — which
is precisely why the ENTITY-escaped form was the surviving vector. The
asymmetry between `convert_charrefs=True` (decodes) and tag-stripping (drops)
is the whole bug.

**Learning:** The entire feed-sanitisation history (the multi-round
`_CONTROL_RE` BiDi / zero-width / Trojan-Source saga) hardened the
`<title>`/`<description>` XML TEXT nodes — where ElementTree's automatic
`<>&` escaping already neutralises structural injection — but never
output-encoded the ONE field that is *definitionally* HTML: the raw-CDATA
`<content:encoded>` body. Invisible-character display-confusion was being
patched round after round while live-script execution sat wide open. Fix =
context-correct output encoding AT THE SINK: `html.escape(part, quote=False)`
on each plain-text part in `_compose_description` (the shared DE+EN
chokepoint — `_apply_lang_overlay` re-composes through the same function), so
only the builder's own structural `<br/>` stays live. CDATA-as-TEXT sinks
(`<title>`) must NOT be escaped: CDATA content is not entity-decoded by the
XML parser, so escaping there would surface a literal `&amp;`. General rule
for this codebase: `html_to_text` returns DISPLAY text, never HTML-safe text;
every sink that renders its output as HTML (only `<content:encoded>` today)
must HTML-escape it. Future-round sibling watch-list: a generic
"plain-text-into-raw-HTML/CDATA" audit walker would pin this invariant the
way the JSON loader/writer walkers pin their parser/writer sites. Adding the
`import html` shifted `src/build_feed.py` line numbers +1; the `allow_nan`
writer-walker allowlist pins `_identity_for_item` by absolute line number
(2359/2368 → 2360/2369) and had to be re-synced in the same PR.

## 2026-05-23 - Stammstrecke Writer Non-Finite Literal Drift + Writer-Side `allow_nan=False` Audit Walker

**Vulnerability:** Two committed-state JSON writers in
`scripts/update_stammstrecke_status.py` emitted non-standard
`NaN` / `Infinity` / `-Infinity` literals (invalid per RFC 8259 §6)
when a non-finite float reached the payload — the writer-side
sibling of the parser-side `loads_finite` hook closed in the
2026-05-14 / 2026-05-15 rounds (PRs #1485 / #1487 / #1488):

1. `scripts/update_stammstrecke_status.py:_save_pending_trips`
   (line 656) writes `cache/stammstrecke/pending_trips.json` via
   `_json_lib.dump(payload, fh, indent=2, sort_keys=True,
   ensure_ascii=True)` with NO `allow_nan=False`. The payload's
   `latest_delay_minutes` field is a CONCRETE `float` type (not
   `float | None`) on `_PendingTrip` and flows directly from
   `_leg_departure_delay_minutes` arithmetic (`(actual -
   scheduled).total_seconds() / 60.0`). A future refactor that uses
   `float('inf')` as a missing-data sentinel, a third-party VAO peer
   SDK that surfaces `math.nan` for missing observations, or a
   derived-statistic division-by-zero (e.g. mean-of-empty
   computation) lands the non-standard literal verbatim in the
   committed-to-`main` sidecar. Confirmed by PoC: pre-fix a
   `_PendingTrip(latest_delay_minutes=float('nan'))` writes
   `"latest_delay_minutes": NaN` to disk; the next `_load_pending_trips`
   call invokes `loads_finite` which rejects the literal with
   `json.JSONDecodeError` and the `except (ValueError,
   json.JSONDecodeError, RecursionError)` handler treats the file
   as missing — **every observed-but-not-yet-finalised S-Bahn trip
   is silently lost** for the affected cron tick (the finalise pass
   produces a zero-observation CSV row).

2. `scripts/update_stammstrecke_status.py:_save_recently_finalised`
   (line 766) writes `cache/stammstrecke/recently_finalised.json`
   with the same writer shape and the same missing pin. Today's
   payload is `{key: ts.isoformat() for key, ts in
   finalised.items()}` (all-string values), so the present-day
   exploitability is gated on a schema widening that adds a numeric
   field (re-emission count, age-in-seconds for cleanup tooling,
   observed-delay arithmetic). The sibling pin keeps the
   writer-shape contract uniform across the two-file ledger pair so
   a future refactor cannot regress one half of the round-trip
   invariant.

Both files reach `main` via the IFTTT-triggered `update-cycle.yml`
workflow whose auto-commit step uses `add_options: '-A'` so every
modified file under `cache/` is staged and pushed.

**Learning:** The 2026-05-23 closing rule for the canonical four-axis
fix family was almost complete after the Trojan-Source scrub walker
shipped — but the **writer-side non-finite-literal** axis
(`allow_nan=False`) had only per-callsite source-grep tests at
`tests/test_sentinel_committed_writer_allow_nan_drift.py` /
`test_sentinel_companion_writer_allow_nan_drift.py` /
`test_sentinel_safe_json_formatter_allow_nan_drift.py`, never a
programmatic walker. The drift this round closes: the missing
writer-side dual of the parser-side
`test_sentinel_non_finite_literal_audit_walker.py`. With the new
walker the parser-site canonical fix-family axes
(RecursionError + size-cap + non-finite-literal) plus the
writer-site axes (Trojan-Source scrub + `allow_nan=False`) are now
**all five** programmatically enforced; a future contributor adding
a fresh `json.dump(...)` / `json.dumps(...)` callsite without the
pin fails the walker at PR-review time regardless of whether the
journal named the file. The walker lives at
`tests/test_sentinel_allow_nan_writer_audit_walker.py` and scans
every `*.py` under `src/` and `scripts/` via `ast.parse`, resolves
local `json` aliases (so `import json as _json_lib;
_json_lib.dump(...)` is picked up — the exact shape the stammstrecke
writers use), and asserts each writer carries `allow_nan=False` (or
a `**kwargs` spread the walker conservatively tolerates). Three
allowlist entries cover the documented non-disk / signed-payload
sites: `src/places/hafas_client.py:_serialise_payload` (line 282 —
MAC-signed HAFAS wire payload), `src/build_feed.py:_identity_for_item`
(lines 2261 and 2270 — in-memory hash compute, bytes flow into
`hashlib.sha256(...).hexdigest()` on the very next line). When
invoked against the pre-fix codebase the walker correctly flagged
both stammstrecke writers; post-fix it reports zero findings.
Behavioural PoC at
`tests/test_sentinel_stammstrecke_writer_allow_nan_drift.py` pins
the exact `ValueError(r"Out of range float")` shape that
`json.dump(..., allow_nan=False)` produces on a NaN / ±Infinity
input — three axes (`nan`, `+inf`, `-inf`) covered per writer,
plus the round-trip happy-path seal showing the post-fix writer
does NOT regress finite-payload behaviour.

## 2026-05-23 - Trojan-Source Scrub Audit Walker (Writer-Site Closing Rule)

**Vulnerability:** Two committed-state JSON writer sinks still emitted
the canonical CVE-2021-42574 Trojan-Source / BiDi-mark / zero-width /
8-bit C1 attack-byte union as raw UTF-8 bytes despite the 2026-05-14
Round 13 / 14 closing-checklist sweep:

1. `scripts/extract_oebb_geonetz_stops.py:main` (line ~300) writes
   `data/oebb_geonetz_stops.json` via
   `json.dumps(payload, ensure_ascii=False, indent=2,
   allow_nan=False)` with NO `scrub_trojan_source_primitives` call on
   the payload. The payload's `stops[].name`, `stops[].address`,
   `stops[].ifopt_id`, `stops[].bsts_id`, and `stops[].eva_nr`
   fields flow verbatim from the upstream ÖBB GeoNetz dump
   (`data.oebb.at` ÖBB-Infrastruktur AG endpoint) — a compromised
   CDN / DNS hijack / MITM on the GeoNetz fetch carrying U+202E
   (RIGHT-TO-LEFT OVERRIDE) in any of those fields lands the BiDi
   reversal trigger directly in the committed sidecar (`E2 80 AE`
   UTF-8 bytes). Same file's parser-site siblings (size-cap +
   non-finite literal) were closed in PR #1629 but the writer-side
   Trojan-Source scrub was missed.

2. `scripts/apply_station_overrides.py:apply_overrides` (line ~324)
   writes `data/stations.json` after applying the curated overrides
   list via `json.dumps(stations_payload, indent=2,
   ensure_ascii=False, allow_nan=False)` with NO
   `scrub_trojan_source_primitives` call. Two attack vectors land
   bytes here: (a) a previously-poisoned `data/stations.json`
   (planted via a bypass of the canonical writer, surviving from a
   corrupted previous cron run, or written by an early-deployment
   build pre-dating Round 12-14) survives the read-then-write cycle;
   (b) the `_op_restore` handler inserts the override's `entry`
   template verbatim via `dict(entry_template)`, so a hostile PR
   landing a tampered `data/stations_overrides.json` carrying
   U+202E in an `entry` `name` field plants the byte directly into
   the committed `data/stations.json`. Both files reach `main` via
   the weekly `update-stations.yml` cron pipeline.

**Learning:** The 2026-05-23 closing rule for the parser-site axes
(`Future canonical-loader rounds should ship the walker alongside the
per-site fix so every parser-site axis (RecursionError + size-cap +
non-finite-literal + Trojan-Source scrub) is programmatically
enforced from the start`) is now realised for the **Trojan-Source
scrub** axis via `tests/test_sentinel_trojan_source_audit_walker.py`.
The walker scans every `*.py` under `src/` and `scripts/` for any
`json.dump(...)` / `json.dumps(...)` call that explicitly pins
`ensure_ascii=False` as a keyword argument and asserts the smallest
enclosing function (or module body for module-level writers) contains
at least one `scrub_trojan_source_primitives(...)` call. Three
documented sibling-defence sites live in the `ALLOWLIST`:
`src/places/hafas_client.py:_serialise_payload` (HAFAS wire-format
bytes are MAC-signed and sent to a third-party endpoint, not
committed); `src/feed/reporting.py:write_feed_health_json` (per-field
`_CONTROL_CHARS_RE.sub("", ...)` strips the byte-equivalent canonical
union pre-serialisation); `src/feed/logging_safe.py:SafeJSONFormatter`
(two lines — `sanitize_log_message(dumped, strip_control_chars=False)`
always strips the byte-equivalent canonical union
post-serialisation). When invoked against the pre-fix codebase the
walker correctly flagged `scripts/apply_station_overrides.py:324` and
`scripts/extract_oebb_geonetz_stops.py:300`; post-fix it reports zero
findings. Any future contributor who adds a fresh
`json.dump(..., ensure_ascii=False, ...)` /
`json.dumps(..., ensure_ascii=False, ...)` callsite without a
sibling `scrub_trojan_source_primitives` call (or a documented
allowlist entry) fails the walker at PR-review time regardless of
whether the journal named the file. With this round all four
canonical fix-family axes (RecursionError + size-cap +
non-finite-literal + Trojan-Source scrub) are now programmatically
enforced; the journal-named closing rule from the 2026-05-23
non-finite-literal round is complete. Future fix families should
inherit this template: ship the walker alongside the per-site fix
from PR #1, never as a follow-up round.

## 2026-05-23 - Non-Finite Literal Audit Walker (Parser-Site Closing Rule)

**Vulnerability:** The 2026-05-14 / 2026-05-15 rounds (PR #1485 /
#1487 / #1488 / #1491 / #1503) pinned
`parse_constant=_reject_non_finite_constant` +
`parse_float=_reject_non_finite_float` on every documented
`json.loads` / `json.load` / `response.json()` call across the
committed-state-file readers and network-tainted parsers. Behavioural
PoC + named-list source-grep tests at
`tests/test_sentinel_committed_reader_non_finite_drift.py` pin the
canonical hook names on each named reader. However the
**non-finite-literal axis** never received a programmatic walker —
unlike the **RecursionError** axis (covered by
`tests/test_sentinel_json_audit_walker.py` since 2026-05-08) and the
**size-cap** axis (covered by
`tests/test_sentinel_size_cap_audit_walker.py` since 2026-05-23 — the
GeoNetz / i18n round closing rule). A future contributor adding a
fresh `json.loads(content)` / `response.json()` call without the
hooks would silently regress the entire fix family: bare
`json.loads("NaN")` returns `float('nan')`, bare `json.loads("1e1000")`
IEEE-754-overflows to `float('inf')`, and the planted non-finite
value propagates through `nan != nan` dedup comparisons (silent
breakage), `nan + 5` arithmetic (silent poison), and the writer-pin
round-trip (`allow_nan=False` → `ValueError` mid-write → cron crash).
The journal entry that closed the GeoNetz round explicitly named the
gap: "Future canonical-loader rounds should ship the walker alongside
the per-site fix so every parser-site axis (RecursionError +
size-cap + non-finite-literal + Trojan-Source scrub) is
programmatically enforced from the start."

**Learning:** The closing rule is now realised for the
**non-finite-literal** axis via
`tests/test_sentinel_non_finite_literal_audit_walker.py`. The walker
parses every `*.py` under `src/` and `scripts/` via `ast.parse`,
collects the local `json` module aliases (so
`import json as _json_lib; _json_lib.loads(c)` is resolved correctly),
finds every `<json-alias>.loads(...)` / `<json-alias>.load(...)` /
`<receiver>.json(...)` call, and asserts each carries both
`parse_constant=...` and `parse_float=...` keyword arguments (or a
`**kwargs` spread the walker conservatively tolerates). When invoked
against the post-fix codebase the walker correctly reports zero
findings (verified pre-merge); when invoked against a synthetic
`json.loads(content)` regression it flags the exact line + the
missing kwargs. Future contributors adding a fresh parser site
without the hooks fail the walker at PR-review time regardless of
whether the journal named the file. With this round all three of
the parser-site canonical axes (RecursionError + size-cap +
non-finite-literal) are now programmatically enforced; the
Trojan-Source scrub axis remains the open closing-rule item for a
future round.

## 2026-05-23 - i18n Coverage Gate MemoryError DoS + Size-Cap Walker

**Vulnerability:** `scripts/check_i18n_coverage.py:180-181` used
`HTML_PATH.read_text(encoding="utf-8")` and
`JS_PATH.read_text(encoding="utf-8")` with NO byte-size cap on the two
committed dashboard sources (`docs/site.html`, `docs/assets/site.js`).
The gate is invoked from TWO blocking pipelines: the local pre-commit
hook (`.pre-commit-config.yaml`) AND the canonical CI gauntlet at
`scripts/run_static_checks.py` (which is itself invoked by
`.github/workflows/test.yml`). A planted multi-GiB
`docs/site.html` / `docs/assets/site.js` (hostile PR replacing the
tracked source, compromised CI runner / `main` checkout, partial flush
+ power loss mid-edit) buffered via `read_text()` allocated
O(file_size) bytes and raised `MemoryError`. `MemoryError` is a
`BaseException` subclass — NOT caught by any handler in the script
(`main()` has no try/except at all) — so the unhandled exception
escaped the gate and crashed the full static-checks pipeline. Direct
sibling drift from the same canonical inventory: the sibling script
`scripts/optimize_site_assets.py` writes to the EXACT same two files
via `atomic_write` and already routes its reads through
`read_capped_text` with a 4-MiB cap (`MAX_CSS_FILE_BYTES`) — the i18n
coverage gate was the missed sibling.

**Learning:** The 2026-05-23 GeoNetz round's closing rule
("future canonical-loader rounds should ship the walker alongside the
per-site fix so every parser-site axis … is programmatically enforced
from the start") is now realised for the **size-cap** axis via
`tests/test_sentinel_size_cap_audit_walker.py`. The walker scans every
`*.py` under `src/` and `scripts/` for bare `<expr>.read_text(...)` /
`<expr>.read_bytes(...)` calls (banned shapes — they allocate
O(file_size) bytes before any defence layer can run). When invoked
against the pre-fix code it correctly flagged
`scripts/check_i18n_coverage.py:180` and `:181`; the post-fix
allowlist is empty by design. Any future contributor who adds a bare
`path.read_text()` / `path.read_bytes()` call to `src/` or `scripts/`
will fail the walker at PR-review time regardless of whether the
journal named the file. The post-fix PoC test in
`tests/test_sentinel_check_i18n_size_bomb_ondisk.py` uses AST-based
static inspection of the `main()` function body (excluding docstrings)
to lock the `read_capped_text` route + ban the bare `read_text` /
`read_bytes` shapes specifically for this script — defence-in-depth
against the walker drift case where a future PR silently broadens the
allowlist.

## 2026-05-23 - GeoNetz Loader MemoryError DoS + Non-Finite Literal Drift

**Vulnerability:** Two siblings of the JSON size-bomb / non-finite literal
defence family had escaped the canonical-loader inventory:

1. `scripts/update_station_directory.py:_load_geonetz_stops` used
   `json.loads(path.read_text(encoding="utf-8"))` with NO byte-size cap
   and caught only `Exception`. `MemoryError` is `BaseException` — it
   propagated past the broad catch and crashed the weekly station
   refresh cron pipeline (the orchestrator runs every update script via
   `subprocess.run(check=True)`). A planted huge file at
   `data/oebb_geonetz_stops.json` (compromised CI runner / hostile PR /
   corrupted previous run / partial flush + power loss) was sufficient
   to trigger the crash. The loader ALSO accepted `NaN` / `Infinity` /
   `1e1000` literals at the JSON parse boundary, propagating
   `float('nan')` / `float('inf')` into downstream enrichment that
   compares via `==`/`!=` (`nan != nan` is `True` — silent dedup
   invariant breakage).

2. `scripts/extract_oebb_geonetz_stops.py` parsed a fresh upstream
   GeoNetz dump via `json.loads(raw_bytes)` (no `parse_constant` /
   `parse_float` hooks), then `_coerce_float` returned the float
   unchanged for `NaN`/`±Inf` (the `isinstance(value, (int, float))`
   guard matches every Python float, finite or not), then
   `round(NaN, 6)` returned `NaN`, then `json.dumps(payload,
   ensure_ascii=False, indent=2)` emitted a non-standard `NaN` literal
   (invalid per RFC 8259) into the committed
   `data/oebb_geonetz_stops.json` sidecar. A poisoned upstream
   (compromised CDN / DNS hijack / MITM on the ÖBB-Infrastruktur fetch)
   was therefore sufficient to plant non-finite literals into the
   committed-to-`main` sidecar, which the loader above then re-read
   without rejection.

**Learning:** Closing-checklist drift for JSON-loader fix families
must explicitly walk EVERY `json.loads`/`json.load`/`response.json()`
call site in `src/` and `scripts/` — not just the named files in the
PR's audit narrative. The 2026-05-08 round canonicalised
`read_capped_json` / `loads_finite` and shipped a programmatic
`test_sentinel_json_audit_walker.py` that pins the RecursionError
catch invariant, but the size-cap / non-finite-literal axes did NOT
get a corresponding walker — so the GeoNetz sibling pair survived
five subsequent rounds. The post-fix tests in
`tests/test_sentinel_geonetz_size_bomb_ondisk.py` use AST-based
static inspection of the function bodies (excluding docstrings) so a
future refactor that re-introduces `path.read_text()` /
`json.loads(...)` fails loudly at PR-review time. Future canonical-
loader rounds should ship the walker alongside the per-site fix so
every parser-site axis (RecursionError + size-cap + non-finite-literal
+ Trojan-Source scrub) is programmatically enforced from the start.
