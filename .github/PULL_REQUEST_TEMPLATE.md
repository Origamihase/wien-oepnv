<!--
Educational PR template enforcing the seven-discipline standard.
Each section references the relevant ``.jules/`` journal so reviewers
and contributors know *why* the box exists, not just what to tick.
Failing checks aren't blockers — they're signals that the change
needs explicit reviewer attention or a deliberate "out of scope"
acknowledgement.
-->

## Beschreibung der Änderung
<!-- Was wurde geändert? Warum ist diese Änderung notwendig? -->

## Ticket-Referenz
<!-- Falls vorhanden, bitte Issue-Nummer verlinken (z.B. #123) -->

## Out-of-scope debt I'm deliberately NOT tackling
<!--
Optional. List anything you noticed but chose to leave for a follow-up
PR. Keeps reviewers from re-discovering known issues. Examples:
- "C901 is still 51 on _foo — refactor in a follow-up"
- "wl_fetch tests still mock at the wrong layer; will rewrite later"
-->

---

## Seven-discipline review checklist

This project's institutional memory lives in `.jules/`. Each checkbox
below references the journal that explains *why* the rule exists.
Tick each item OR explain in the PR description why the rule does not
apply to this change.

### 🛡 Sentinel — Security
- [ ] No new HTTP request bypasses `request_safe` (`src/utils/http.py`)
- [ ] No new `# nosec` marker hides a real vulnerability (markers must
      annotate trusted-input call sites only — see `docs/architecture.md` §2)
- [ ] No new SSRF / redirect / DNS-rebinding surface introduced
- [ ] If a new external host is contacted, the URL allowlist is updated
      and the response Content-Type is validated

### ⚡ Apex — Performance
- [ ] No `copy.deepcopy(items)` or unnecessary defensive copies on
      data-pipeline hot paths (Apex Phase 2 explicitly removed these)
- [ ] No new O(n²) regex re-parsing in pairwise loops; if the inner
      loop reads a derived projection, cache it (Apex Phase 2 pattern)
- [ ] No `wait()`-mocked test loop without a small `feed_config.PROVIDER_TIMEOUT`
      patch — see `.jules/apex.md` 2026-05-07 entry

### 💧 Purist — Static analysis
- [ ] `ruff check src/ tests/` is clean
- [ ] `python3 -m mypy --no-pretty src tests` (CI-pinned 1.10.1) is clean
- [ ] No `# type: ignore` or `# noqa` added to hide a structural issue
- [ ] No new mypy-allowlist entries unless the surfacing is documented

### 🥼 Surgeon — Complexity
- [ ] No new function exceeds **C901 = 15** (cap enforced by
      `scripts/check_complexity.py`)
- [ ] If a refactor reduces a baselined function below threshold,
      `.c901-baseline.txt` has been regenerated
      (`bash scripts/regen_c901_baseline.sh`)
- [ ] Extracted helpers are pure functions where possible, with
      explicit single-responsibility names

### Ω Omega — Joint Sentinel + Surgeon
- [ ] If `request_safe` or any of its 14 helpers were touched, every
      affected security gate has explicit test coverage (no implicit
      "the existing test catches this" assumptions)
- [ ] New security helpers carry a docstring beginning
      `Mitigates: <attack vector>`

### 🧨 Saboteur — Resilience
- [ ] Hostile-payload paths return `{}` / `[]` / `None` (fail-closed),
      never crash the cron
- [ ] `RecursionError` is caught alongside `JSONDecodeError` /
      `ET.ParseError` for any new payload parser
- [ ] If a new provider is added, it uses (or has a documented reason
      not to use) `CircuitBreaker` from `src/utils/circuit_breaker.py`
- [ ] Provider-level bulkhead preserved: one provider's exception
      does not drop the others' items

### 🪶 Scribe — Developer experience
- [ ] New public functions / classes have PEP-257 docstrings with
      `Args`, `Returns`, `Raises` sections
- [ ] Architecturally-visible changes update `docs/architecture.md`
      (and the corresponding Mermaid diagram if applicable)
- [ ] If this PR introduces a new pattern, an entry in the relevant
      `.jules/*.md` journal explains the rationale

---

## Local verification commands

Run these before pushing — they mirror CI exactly:

```bash
pre-commit run --all-files            # ruff + mypy + bandit + scan_secrets
python scripts/run_static_checks.py    # full CI parity check
python scripts/check_complexity.py     # C901 gate vs baseline
python -m pytest --timeout=120         # full test suite
```
