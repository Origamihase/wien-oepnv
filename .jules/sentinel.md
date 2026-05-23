# Sentinel's Journal

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
