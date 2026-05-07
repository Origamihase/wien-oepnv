# Surgeon 🥼 Structural Refactor — Journal

Companion log to `.jules/sentinel.md` (security), `.jules/apex.md`
(performance), and `.jules/purist.md` (static-analysis hygiene).
Surgeon documents complexity-reduction surgery on monolithic functions:
control-flow mapping, extraction patterns, and the rationale for which
seams were cut where.

Format mirrors the sister journals: only entries that capture a
reusable structural pattern, an architectural decision, or a deferred
target with explicit rationale.

---

## 2026-05-07 - Open-Heart on `_collect_items`: 53 → 14 via Phase Extraction

**Patient:** `src/build_feed.py::_collect_items` (was 279 lines, McCabe
complexity 53 — top of the file's offenders, second of the project's).

**Pre-op map.** The function compressed seven distinct phases into one
body:
1. Init (providers, report, items list, cache-alert state)
2. Closure: `_cache_alert_handler` (writes shared state under a lock)
3. Provider categorization (`cache_fetchers` vs `network_fetchers`,
   with the PROVIDERS-override merge logic)
4. Closure: `_merge_result` (mutates `items`, updates `report`)
5. Synchronous cache-fetcher loop
6. Async network-fetcher block:
    - Per-fetcher submit with `_run_fetch` closure (timeout +
      semaphore acquisition with starvation-prevention logic)
    - Deadline-eviction wait-loop (Apex Phase 1 hot zone — busy-spin
      against `perf_counter()` with `wait()` cap)
    - Result drain (3 distinct exception classes for accurate
      per-provider error categorization in the health report)
7. Cleanup (cancel pending) — wrapped in nested `try/finally`

The two captured closures (`_cache_alert_handler`, `_merge_result`)
contributed disproportionately to the complexity score because ruff's
McCabe counter walks nested function bodies as part of the outer.

**Surgical plan.** Extract Method along the phase boundaries, with a
new `_ProviderBuckets` NamedTuple to ferry the categorization output:

| Helper | Phase | Notes |
|---|---|---|
| `_categorize_providers(report) -> _ProviderBuckets` | 1+3 | Pure: returns frozen tuple, no side-effects beyond `report.register_provider` |
| `_run_cache_fetchers(...)` | 5 | Sequential disk-bound work; needs no executor |
| `_build_run_fetch(...)` | 6 submit-side closure | Factored from inline closure to module-level factory |
| `_submit_network_fetches(executor, ...)` | 6 submit-side | Returns `(futures, deadlines, pending)` |
| `_evict_expired_futures(...)` | 6 deadline sweep | Mutates `pending` and `cancelled_futures` in place |
| `_drain_completed_futures(...)` | 6 wait-loop | **Apex-Phase-1 invariant zone — body byte-identical to original** |
| `_run_network_fetchers(...)` | 6 wrapper | Owns the `with ThreadPoolExecutor(...):` + cleanup `finally` |

**Why two closures stayed inline** (`_cache_alert_handler`,
`_merge_result`): both share lock-protected state with the orchestrator
(`alert_lock`, `cache_alerts`, `seen_cache_alerts`, `items`,
`cache_alerts.get` reads). Extracting to module level would require
threading a 5- or 6-arg context object through every call site, and
`register_cache_alert_hook` expects a `(str, str) -> None` signature
which a free function couldn't satisfy without either currying or
wrapping. The closures capture exactly what they need; passing the
state explicitly was strictly worse on readability.

**Result.**
- `_collect_items`: complexity 53 → **14** (-73%), body 279 → ~75 lines
- New helper `_drain_completed_futures`: 11 (just over threshold; the
  4-branch result-classification block is the reason — splitting it
  further would scatter the 3 exception classes across helpers without
  actually reducing per-helper branching)
- All other new helpers ≤ 10 (below the C901 threshold, dropped
  out of the report entirely)
- Apex Phase 1 invariants preserved: the deadline-eviction loop body
  in `_drain_completed_futures` is textually identical to the original
  inner loop, including the `wait_timeout = max(min(remaining), 0.1)`
  busy-spin cap.

**Pattern for future extractions:** when a function has both
"linear pipeline phases" (categorize → run-cache → run-network) and
"shared mutating closures" (alert handler, result merger), extract the
phases as ordinary helpers but **leave shared-state closures inline**.
The closure boundary IS the natural seam between "what runs once" and
"what runs per-event". Forcing closures to module-level inverts the
abstraction — the outer function ends up plumbing state to its own
helpers instead of just calling them.

---

## 2026-05-07 - Per-Item ETL Extraction: `fetch_events` (ÖBB) 51 → ≤10

**Patient:** `src/providers/oebb.py::fetch_events` (was 105 lines, McCabe
complexity 51).

**Pre-op map.** A single `for item in channel.findall("item"):` loop
with three inline pipelines crammed into the loop body:

1. Title cleaning + GUID derivation (size-cap + fallback)
2. Route reconstruction + line-prefix injection
3. Three-attempt poor-title fallback ladder
4. Region-relevance filter (drop or append)

Critically: **zero shared state between iterations**. Every iteration
reads `item` and writes to the `out` accumulator. Pure ETL. The
complexity-51 score came almost entirely from the title-fallback ladder
(3 nested `if _is_poor_title(title):` guards × 2-4 inner branches each).

**Surgical plan.** Lift the per-item logic into pure helpers with
explicit signatures:

| Helper | Responsibility |
|---|---|
| `_derive_guid(raw_guid, title, link) -> str` | 128-byte cap + title/link fallback |
| `_apply_route_title(title, desc) -> str` | Route reconstruction + line-token injection |
| `_resolve_poor_title(title, link, guid, desc) -> str` | 3-attempt fallback ladder, short-circuits on success |
| `_build_item_from_xml(item) -> FeedItem \| None` | Top-level ETL; returns `None` for region-filtered items |

Top-level `fetch_events` collapses to: `for item in channel.findall("item"): if (fi := _build_item_from_xml(item)) is not None: out.append(fi)`.

**Result.**
- `fetch_events`: complexity 51 → **≤10** (dropped out of C901 report
  entirely)
- All 4 new helpers ≤ 10 (also below threshold)
- Body of `fetch_events` shrunk from ~80 inline lines to a 5-line loop
- **Security invariant preserved:** the `MAX_OEBB_FETCH_TIMEOUT` clamp
  + 128-byte GUID cap are byte-identical; `_derive_guid` carries the
  same comment annotating the GUID-bloat amplification defense.

**Bonus optimization (incidental):** the line-token regex
`re.search(r"\b((?:REX|S(?:-Bahn)?|U)\s*\d+)\b", desc)` was inline-compiled
on every loop iteration. Lifted to module-level
`_LINE_TOKEN_RE = re.compile(...)` so each ÖBB fetch (~hundreds of
items) now compiles the pattern once instead of per-item. This is
not a Surgeon mandate but the extraction surfaced it.

**Pattern for future extractions:** when a hot loop body is "pure ETL
with zero cross-iteration state", the seam is always the loop body
itself — extract `_build_X_from_Y(y) -> X | None` and let the loop become
a one-liner. Sub-pipelines within the body (like the 3-attempt fallback)
become their own helpers when each step has its own short-circuit
semantics worth naming.

---

## 2026-05-07 - DEFERRED: `request_safe` (62) — Sentinel Co-Review Required

**Patient:** `src/utils/http.py::request_safe` (454 lines, complexity
**62** — the project's highest).

**Why not now.** The function is a **stack of 5 nested security defense
layers**, each individually audited by Sentinel:
1. Slowloris timeout enforcement (default + clamp)
2. Hook merging (caller hooks + session hooks + `_check_response_security`)
3. Manual redirect loop (DNS-rebinding TOCTOU prevention,
   `max_redirects` cap, RFC-7231 method preservation for 307/308)
4. Per-redirect: DNS pin via `_pin_url_to_ip`, content-type allowlist
   validation, payload-size streaming check
5. Total-time-budget tracking across redirects

The complexity-62 score is **inherent to the security-state-machine
shape**: each defense generates branches × the number of HTTP states
it has to handle (3xx vs 2xx vs error × method preserved? × content-type
valid? × size under cap? × time budget remaining?). Naive Extract
Method would either:
- Split the redirect loop body into a "do one HTTP step" helper —
  but that hides the per-step DNS-pin invariant from the loop, making
  it easier to introduce a TOCTOU regression on a future change
- Split by defense layer (`_enforce_timeout`, `_validate_redirect`,
  `_check_payload_size`) — but each layer reads state from the
  outer loop (current_url, time_budget, redirects_remaining), so
  they'd take 4-6 args each AND mutate shared state

**Conditions for safe surgery.** A future Surgeon-Sentinel joint PR
should:
1. Land first as a **mechanical extraction** with no logic changes
   (one PR for "extract `_handle_redirect_step`", separately reviewed
   against Sentinel's redirect-TOCTOU test corpus)
2. Be accompanied by a property-test sweep over the
   {redirect-method × content-type × payload-cap × time-budget} matrix
3. Have the diff reviewed by someone who can speak to the
   `_pin_url_to_ip` invariant in detail

**Estimated complexity floor.** With the security state machine
preserved, the most aggressive extraction probably bottoms out at
complexity ~25-30 for the redirect loop and ~15 for the orchestrator.
**Going below 10 would require a structural change** (state-pattern
class, redirect-as-coroutine), which is too invasive for a "no behavior
change" PR. Documented for future reference so the next Surgeon doesn't
chase a target that's structurally unreachable without risk.

---

## Out-of-scope inventory (other 40+ C901 findings)

For future passes, ranked by extraction safety:

**Likely-safe targets** (provider-local, no Sentinel/Apex hot path):
- `_format_item_content` (33) — content rendering
- `_iter_messages` (46) — message iterator
- `validate_http_url` (34) — URL validation; some Sentinel adjacency
- `_station_lookup` (29) — station-directory query
- `_title_has_unknown_endpoint` (27) — title classifier
- `_clean_title_keep_places` (26) — title cleaner

**Risky targets** (Sentinel/Apex/concurrency adjacency):
- `_drain_completed_futures` (11, just-extracted) — leave; further
  splits would pull apart the 3-class exception handling
- `_dedupe_items` (18) — Apex Phase 1 ran the dedupe summary; touching
  the dedupe body itself needs benchmark coverage
- `_post` (28) — request submission, Sentinel-adjacent
- `_scan_content` (29) — secret scanner, full Sentinel territory

**Don't touch:**
- `request_safe` (62) — see deferred entry above
