# Taskforce Omega Ω — Joint Sentinel + Surgeon Operation

Companion log to the four single-discipline journals (`sentinel.md`,
`apex.md`, `purist.md`, `surgeon.md`). Omega operates only on the rare
problem that requires both a security-defense audit AND a structural
refactor at the same time — when neither agent can act safely without
the other's coverage.

Format: only entries that capture a unique joint-discipline pattern.
Routine cleanups belong in the contributing PRs.

---

## 2026-05-07 - Conquering `request_safe`: Complexity 62 → 12 with Zero Security Drift

**Patient.** `src/utils/http.py::request_safe`. The project's most
complex function (cyclomatic 62, 454 lines) and Surgeon's deferred
top-rank target. Five nested security defense layers — Slowloris
timeouts, hook merging for the IP-verification security hook, manual
redirect (DNS-rebinding TOCTOU), per-redirect DNS pin + content-type
allowlist + payload-size cap, and a total-time-budget tracker across
the entire redirect chain. Every `if` was a Sentinel-audited gate.

**Pre-op map (Sentinel's domain).** Catalogued 23 distinct security
invariants. 21 had explicit test coverage. **Two were only implicitly
covered**:

1. *Hook merging + security-hook-always-attached* — if a refactor
   accidentally placed `_check_response_security` in a code path that
   doesn't fire (e.g., only HTTPS), DNS-rebinding TOCTOU defense would
   silently no-op on HTTP. No test would catch it.
2. *Tuple timeout summing as total budget* — the `timeout=(connect, read)`
   form MUST yield `total = connect + read`. If a refactor accidentally
   used just `connect` or just `read`, an adversary could chain
   redirects and stretch the wall-clock budget unboundedly. No test
   asserted the SUM behaviour.

**TDD step.** Wrote three regression tests *first* against the
unrefactored monolith and confirmed they passed before touching production
code:
- `test_request_safe_security_hook_always_attached` — asserts
  `_check_response_security` is in `kwargs["hooks"]["response"]` for
  every outgoing request.
- `test_request_safe_caller_hooks_preserved` — asserts that a
  caller-passed custom hook AND the security hook coexist after merge.
- `test_request_safe_tuple_timeout_total_budget_sums` — freezes
  `time.monotonic()` so elapsed=25s with timeout=(3.0, 15.0); asserts
  the timeout error references "18" (the sum), and that no HTTP request
  was issued because the budget tripped pre-flight.

**Surgical plan (Surgeon's domain).** Extract Method along
*security-module* boundaries — each helper is one cohesive defense
layer with a docstring naming the attack vector it mitigates:

| Helper | Mitigates |
|---|---|
| `_compute_total_time_budget(timeout)` | DoS via slow chained redirects (tuple sum) |
| `_check_total_budget_or_raise(total, elapsed)` | Slowloris across redirects |
| `_per_request_timeout(timeout, total, elapsed)` | Per-request timeout decay |
| `_merge_request_hooks(session, kwargs)` | Silent skip of `_check_response_security` (DNS-rebinding TOCTOU) |
| `_resolve_target_ip(parsed, current_url)` | SSRF via private-IP resolution |
| `_send_http_pinned(...)` | DNS-rebinding TOCTOU on HTTP |
| `_send_https_pinned(...)` | DNS-rebinding TOCTOU on HTTPS + SNI mismatch |
| `_strip_redirect_secrets(kwargs, current_url, next_url, session)` | Token/credential leak across origins |
| `_drop_body_for_get(kwargs)` | Malformed POST→GET conversion |
| `_apply_method_downgrade(method, status, kwargs)` | RFC-7231 method-preservation/downgrade compliance |
| `_drop_host_header(kwargs)` | SNI/Host mismatch on redirect |
| `_is_redirect(r)` | False-positive redirects from `MagicMock` |
| `_process_redirect(r, ...)` | Combined redirect attack-surface dispatch |
| `_validate_content_type(r, allowed_content_types)` | WAF/proxy block-page misinterpretation |
| `_compute_read_timeout(timeout, total, elapsed)` | Slowloris on read side |

**Result.**
- `request_safe`: complexity **62 → 12** (-81%). Below user's target ≤15.
- Body shrunk from 280 lines (control-flow only) to ~75 lines.
- All 14 helpers ≤ 10 (none appear in the C901 report).
- **All 23 security invariants preserved.** New regression tests pass.
- Test suite: **1903 passed (was 1900)**, 3 skipped — gained 3 from the
  TDD step, lost 0.

**The "security module" extraction pattern.** What unlocked this surgery
was treating each helper as a *defense layer*, not a code block. Every
extracted function:

1. Has a name that describes a defense, not a transformation
   (`_strip_redirect_secrets`, not `_handle_kwargs`)
2. Has a docstring beginning with "Mitigates: <attack vector>"
3. Owns its security gate end-to-end (inputs + outputs + side-effects all
   relevant to that one defense)

This convention makes the security state machine *legible*. A future
maintainer can read `request_safe`'s body and see the chain of defense
layers in plain Python:

```python
elapsed = time.monotonic() - start_time
_check_total_budget_or_raise(total_allowed_time, elapsed)
current_timeout = _per_request_timeout(timeout, total_allowed_time, elapsed)

safe_url = validate_http_url(current_url, check_dns=False)
...
ctx = _send_http_pinned(...) if scheme == "http" else _send_https_pinned(...)

with ctx as r:
    redirect = _process_redirect(r, current_url, method, kwargs, ...)
    if redirect is not None:
        current_url, method = redirect
        continue
    ...
    _validate_content_type(r, allowed_content_types)
    final_read_timeout = _compute_read_timeout(timeout, total_allowed_time, ...)
    content = read_response_safe(r, max_bytes, timeout=final_read_timeout)
```

Each line is a Sentinel-audited gate. Inserting a future line in the
wrong place is now obviously wrong; previously it was a 280-line wall of
nested ifs where security gates and bookkeeping interleaved.

**Pattern for future joint Sentinel+Surgeon operations:**

1. **Always TDD the implicit gates first.** A function with explicit
   coverage of N out of M security invariants leaves M-N silent traps.
   Find them by reading the function for invariants, not by reading the
   tests for assertions. If invariant X is "the security hook is always
   attached", and no test EXPLICITLY asserts that, write one before
   touching anything.
2. **Extract along defense layers, not code blocks.** It's tempting to
   extract `_compute_timeout(...)` because the timeout arithmetic is
   visually verbose. Resist. Extract `_check_total_budget_or_raise(...)`
   because *the budget check is one Slowloris-defense layer*. The unit
   of extraction is the defense, not the syntactic block.
3. **Docstrings name attacks, not behaviours.** `"""Compute the per-request
   timeout"""` says nothing about what breaks if the helper is wrong.
   `"""Mitigates: per-request timeout decay. Tuple timeouts retain
   their structure but are capped..."""` tells the next reader exactly
   what they must not break.
4. **The orchestrator should read like a security policy.** If you can
   read the top-level body and not see the defense pipeline at a glance,
   the extraction is incomplete or mis-named. The orchestrator is
   reviewable security policy; the helpers are tested implementation
   detail.

---

## Cross-journal links

- Sentinel laid the SSRF/DNS-rebinding/TOCTOU/Slowloris foundations
  that this refactor preserved (`.jules/sentinel.md`).
- Apex's "no-defensive-copy" rule guided the choice to mutate `kwargs`
  in place inside the redirect helpers (passing/returning kwargs
  copies on every iteration would re-introduce the kind of overhead
  Apex Phase 2 removed).
- Surgeon documented `request_safe` as the explicitly-deferred final
  boss in `.jules/surgeon.md`; this entry is the closure.
- Purist's "tool-version skew" caveat applies — verification used the
  CI-pinned `mypy 1.10.1` via `python3 -m mypy`, not PATH `mypy 1.19`.
