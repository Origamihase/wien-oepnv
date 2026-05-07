# Saboteur 🧨 Chaos & Resilience — Journal

Sixth and final companion log to the project's institutional-memory
journals (`sentinel.md`, `apex.md`, `purist.md`, `surgeon.md`,
`omega.md`). Saboteur catalogues simulated chaos scenarios, the
resilience patterns chosen to defend against them, and any genuine
chaos vulnerabilities that surfaced during the audit.

Format mirrors the sister journals: only entries that capture a unique
chaos pattern or a real defect uncovered.

---

## 2026-05-07 - Saboteur Pass: Reusable Circuit Breaker + Real Bug Found

**Mission.** Audit the codebase's resistance to hostile external transit
APIs (Wiener Linien, VOR, ÖBB). Three vulnerability classes inspected:
*The Liar* (truncated/schemaless/empty payloads), *The Zombie* (slow
retries that DDoS our own upstreams), *The Time Traveler* (clock skew /
DST in date parsing).

### Chaos Scenarios Simulated

| Scenario | Test | Outcome |
|---|---|---|
| WL returns mid-byte truncated JSON | `test_chaos_wl_truncated_json_payload_returns_empty_dict` | ✅ fail-closed (`{}`) |
| WL returns binary garbage in JSON envelope | `test_chaos_wl_invalid_json_chars_returns_empty_dict` | ✅ fail-closed |
| WL returns 200 OK + zero-byte body | `test_chaos_wl_empty_body_returns_empty_dict` | ✅ fail-closed |
| WL schema drift: array instead of dict | `test_chaos_wl_array_payload_returns_empty_dict` | ✅ Zero-Trust catch |
| WL returns top-level string `"error"` | `test_chaos_wl_string_payload_returns_empty_dict` | ✅ fail-closed |
| WL returns literal `null` | `test_chaos_wl_null_payload_returns_empty_dict` | ✅ fail-closed |
| **WL JSON depth-bomb (5000-deep)** | `test_chaos_wl_deep_nested_payload_does_not_crash` | 🐛 **CRASHED — fixed** |
| WL massive valid dict (10k keys) | `test_chaos_wl_huge_dict_payload_handled` | ✅ parses cleanly |
| Provider bulkhead: VOR raises mid-fetch | `test_chaos_provider_bulkhead_one_crash_doesnt_drop_others` | ✅ WL/ÖBB items still reach feed |
| Provider bulkhead: garbage non-list result | `test_chaos_provider_bulkhead_one_returns_garbage_doesnt_crash` | ✅ rejected by `_merge_result` |
| ÖBB malformed pubDate | `test_chaos_oebb_malformed_pubdate_does_not_crash` | ✅ all 7 malformed inputs handled |

### 🐛 Real Vulnerability Found: `RecursionError` on JSON Depth Bombs

**Discovered by:** `test_chaos_wl_deep_nested_payload_does_not_crash`.

The WL/VOR/ÖBB payload-parsing handlers all caught
`(ValueError, json.JSONDecodeError)` for malformed input. But
`json.loads` on a 5000-deep nested array raises `RecursionError`,
which is **not** a subclass of either. So a malicious or
mis-configured upstream serving deeply-nested JSON could crash the
entire build process — the exception would propagate up through
`_get_json` → `fetch_events` → `_collect_items` and abort the cron.

**Why nobody had noticed.** Defusedxml defends ÖBB's XML against
billion-laughs and quadratic blowup, but JSON has no equivalent
"defused" parser in the stdlib. The ten-megabyte byte cap
(`MAX_PAYLOAD_SIZE`) prevents resource-exhaustion via huge bodies,
but fits well over 5000 nested `[`s in 10MB.

**Fix.** Widened the exception handler in three sites:

```diff
- except (ValueError, json.JSONDecodeError) as exc:
+ except (ValueError, json.JSONDecodeError, RecursionError) as exc:
```

- `src/providers/wl_fetch.py::_get_json` — main WL JSON path
- `src/providers/vor.py::_fetch_location_name` — VOR location lookup
- `src/providers/vor.py::_fetch_departure_board_for_station` — VOR board

Also widened ÖBB's XML handler defensively:
```diff
- except (ValueError, ET.ParseError) as e:
+ except (ValueError, ET.ParseError, RecursionError) as e:
```

defusedxml already defends against the more common XML attacks, but a
deeply-nested but otherwise legitimate document could still trigger
`RecursionError` on parse — defence in depth.

**Impact.** Without this fix, an attacker controlling the Wiener
Linien upstream (or a misconfigured CDN serving malformed content)
could permanently kill the cron job with a single crafted payload.
With the fix, the build logs a structured warning and continues with
empty results from that provider — the bulkhead pattern in
`_collect_items` then ensures the other providers' data still reaches
the feed.

### Resilience Patterns Implemented

#### 1. Reusable `CircuitBreaker` primitive

New module `src/utils/circuit_breaker.py` (~150 lines, no dependencies
outside stdlib). Classic three-state machine:

```
   CLOSED ── failure_threshold consecutive failures ──▶ OPEN
      ▲                                                   │
      │                                                   │ recovery_timeout
      │                                                   ▼
      │                                               HALF_OPEN
      │     success on probe call          failure on probe
      └─────────────────────┬─────────────────────────────┘
                            │
                            └─▶ CLOSED                   ▶ OPEN
```

API:

```python
breaker = CircuitBreaker("vor", failure_threshold=5, recovery_timeout=300.0)

# Direct state machine:
breaker.record_failure()
breaker.record_success()
breaker.state          # → CircuitState.CLOSED / OPEN / HALF_OPEN
breaker.reset()        # admin-only, force back to CLOSED

# Wrapper API:
result = breaker.call(some_function, arg, kwarg=value)
# raises CircuitBreakerOpen if breaker is OPEN, else passes through
# (counts the result automatically)
```

Thread-safe: state transitions are guarded by `threading.RLock`.
Tested under contention with 50 concurrent threads recording failures.

#### 2. Why a project-local primitive (vs. third-party `circuitbreaker` / `pybreaker`)

The codebase had **three ad-hoc resilience implementations** when
Saboteur arrived:
- `src/places/client.py` — instance counter `_consecutive_5xx_errors`
  with a `>= 5` short-circuit; tightly coupled to the GooglePlaces
  client class
- `src/providers/vor.py` — "Emergency Stop" via shared dict counter +
  `RuntimeError("Emergency Stop: ...")`; one-shot kill switch with no
  auto-recovery
- `src/providers/wl_fetch.py` and `src/providers/oebb.py` — no
  circuit breaker at all, only urllib3-level retries

A future provider author copy-paste-mutating one of these three would
inherit whichever one's naming/semantics they hit first. Worse, the
"Emergency Stop" pattern aborts the whole VOR run on first trip with
no auto-recovery — so a 30-second outage causes a 24-hour blackout
until the next retry-after-cooldown human reviews the logs.

The shared primitive eliminates that drift and gives auto-recovery.
The third-party options (`circuitbreaker`, `pybreaker`) bring larger
surface than we need and don't compose cleanly with the existing
`session_with_retries` plumbing — `pybreaker` requires its own thread
or signal-based timeout, whereas this implementation hooks naturally
into the `record_failure`/`record_success` patterns the providers
already use.

#### 3. Adoption pattern (deliberately deferred)

This PR does **not** wire the new `CircuitBreaker` into existing
providers. Each provider's current ad-hoc resilience is working in
production; replacing it would be a behaviour change requiring
per-provider regression review with operator sign-off (the Emergency
Stop aborts on 5x5xx, but with auto-recovery it would resume after
60s — that's a different operational profile).

For the next provider author / future Saboteur pass, the canonical
adoption looks like:

```python
# At module scope, one breaker per provider:
_BREAKER = CircuitBreaker(
    "newprovider", failure_threshold=5, recovery_timeout=120.0
)

def fetch_events(timeout: int = 25) -> list[FeedItem]:
    try:
        return _BREAKER.call(_actual_fetch, timeout=timeout)
    except CircuitBreakerOpen:
        log.warning("newprovider breaker open; returning empty list")
        return []
```

The breaker logs state transitions itself (CLOSED→OPEN, OPEN→HALF_OPEN,
HALF_OPEN→CLOSED, HALF_OPEN→OPEN), so operators get visibility for
free.

### Test coverage added

| File | Tests | Type |
|---|---:|---|
| `tests/test_circuit_breaker.py` | 23 | Primitive contract + state-machine + thread-safety + chaos |
| `tests/test_saboteur_chaos.py` | 11 | Provider-level fail-closed scenarios |

**Total:** 34 new tests. 1900 → 1937 passed, 3 skipped (unchanged).

### Patterns for future Saboteur passes

1. **Hostile-payload chaos tests use mock-then-assert on `_get_json`-level helpers**, not on the network layer. The byte cap and Content-Type allow-list are already locked down by Sentinel; chaos tests focus on *what happens once a malformed-but-cap-passing payload reaches our parser*.
2. **Always run with the full suite, not just the new file.** Saboteur's caplog-based test was green in isolation but flaky in the full run (a logger-propagation interaction with another test). Drop caplog assertions and rely on observable return values for chaos scenarios — log warnings are nice-to-have but assertion-fragile.
3. **Mypy narrows enum properties aggressively.** `assert breaker.state == CircuitState.OPEN` followed by another state-changing call + assert produces `[comparison-overlap]` because mypy treats the property as pure. Wrap state reads in a helper function (`assert_state(breaker, X)`) — function-call boundaries break the narrowing chain.
4. **Build the primitive first; defer adoption.** Wiring the new breaker into existing working providers is a behaviour change with operator-visible side effects (different recovery semantics). A primitive PR + a separate adoption PR is safer than one mega-PR that does both.

### Cross-journal links

- **Sentinel** locked down byte caps + content-type validation, so chaos
  tests focus on the layer above (parser-level malformed input).
- **Apex Phase 2** removed defensive copies in `build_feed.main`; the
  bulkhead-by-ordering pattern there is what makes the
  "VOR crashes mid-fetch, WL+ÖBB still reach feed" scenario safe.
- **Surgeon** extracted `_collect_items` into helpers; one of those —
  the result-classification block in `_drain_completed_futures` — is
  exactly the bulkhead Saboteur's `test_chaos_provider_bulkhead_one_crash_doesnt_drop_others`
  exercises.
- **Purist** verified the new module is `mypy --strict` clean under
  CI-pinned mypy 1.10.1 (no `Any` leakage at the public API).
- **Omega** showed the "Mitigates: <attack>" docstring convention; the
  CircuitBreaker module follows it for the class and its three failure
  modes.
