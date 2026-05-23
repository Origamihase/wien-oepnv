# Sentinel's Journal

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
