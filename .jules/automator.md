# Automator ⚙️ Governance — Journal

Eighth and final companion log to the project's institutional-memory
journals (`sentinel.md`, `apex.md`, `purist.md`, `surgeon.md`,
`omega.md`, `saboteur.md`, `scribe.md`). Automator documents
governance-as-code: the gates, hooks, and templates that prevent
future regressions from undoing the seven preceding passes.

Format mirrors the sister journals: only entries that capture a
unique governance pattern or a new automation artifact.

---

## 2026-05-07 - Automator Pass: Educational PR Template + C901 Allowlist Gate

### Mission

Lock down the high standards established by Sentinel, Apex, Purist,
Surgeon, Omega, Saboteur, and Scribe so that **future contributors
(human or AI) cannot merge regressions silently**. Three gaps were
identified in the diagnostic; this pass closes all three.

### Governance gaps closed

#### 1. PR template went from 3-line generic checklist to 7-discipline review checklist

Before:

```
### Beschreibung
### Ticket-Referenz
### Checkliste
- [ ] tested locally
- [ ] no new linting errors
- [ ] docs updated if needed
```

After: 36-line educational template with an explicit checkbox group
per discipline, each box referencing the relevant `.jules/*.md` so
reviewers and contributors know **why** the rule exists, not just
**what** to tick. Boxes cover:

- 🛡 Sentinel — `request_safe`, `# nosec`, SSRF/redirect/DNS surfaces
- ⚡ Apex — defensive copies, O(n²) regex, `wait()`-mock loops
- 💧 Purist — ruff/mypy clean, no `# type: ignore`/`# noqa` shortcuts
- 🥼 Surgeon — C901 ≤ 15 cap, baseline regeneration after refactors
- Ω Omega — security-helper docstring `Mitigates:` convention
- 🧨 Saboteur — fail-closed behavior, `RecursionError` catches,
  `CircuitBreaker` adoption
- 🪶 Scribe — PEP-257 docstrings, `docs/architecture.md` updates,
  journal entries for new patterns

Plus an "Out-of-scope debt I'm deliberately NOT tackling" section so
contributors can flag known issues without being blocked, and a
"Local verification commands" snippet so the right command is one
copy-paste away.

#### 2. Pre-commit config gained 2 new shift-left hooks

Existing hooks (kept): trailing-whitespace, end-of-file-fixer,
check-yaml/toml/json, check-merge-conflict, check-added-large-files,
ruff (--fix), mypy --strict, scan-secrets.

New hooks added:

- **`bandit`** — runs the same `bandit -r src scripts -q` invocation
  as the CI's `bandit.yml` workflow. Catches B-class issues locally
  before CI minutes are spent.
- **`c901-baseline`** — invokes `scripts/check_complexity.py`,
  rejecting any commit that introduces a NEW C901 violation above
  threshold 15 (or worsens an existing baselined one).

The earliest a developer gets feedback, the better the DX. Both new
hooks fire pre-push, not pre-commit, since they take a few seconds
each.

#### 3. C901 hard-cap with allowlist baseline (mirroring mypy-strict pattern)

The user asked for "fail the build if a function has C901 > 15". A
naïve cap at 15 would immediately break CI: 23 functions in `src/`
currently exceed it, with the worst at complexity 51. Solution:
ratchet-style allowlist baseline, identical in shape to the
existing `.mypy-baseline.txt` gate.

**New artifacts:**

| File | Purpose |
|---|---|
| `.c901-baseline.txt` | 23 lines, format `<function_name> <complexity>` — every existing offender frozen at its current value |
| `scripts/regen_c901_baseline.sh` | Re-emits the baseline from current source (run after intentional refactors) |
| `scripts/check_complexity.py` | The gate itself: parses ruff's C901 output, diffs against baseline, fails on new/worsened violations |
| `.github/workflows/complexity-gate.yml` | CI wrapper around `check_complexity.py` (mirrors `mypy-strict.yml` shape) |
| `scripts/run_static_checks.py` (modified) | Now invokes `check_complexity.py` so contributors reproduce CI locally with one command |

**Rules enforced by the gate:**

- A NEW function with complexity > 15 → ❌ fails CI
- A baselined function whose complexity has INCREASED → ❌ fails CI
- A baselined function whose complexity has DECREASED → ✅ passes
  (with `::notice::` asking the contributor to regen the baseline
  to lock in the improvement)
- All existing baselined values stable → ✅ passes silently

**Threshold rationale (15, not 10):**

The default ruff McCabe threshold is 10. We choose 15 because:
- Surgeon's `_collect_items` orchestrator stabilises at 14 after the
  Phase-extraction refactor (it's the natural floor for a
  bulkhead-orchestration function with 3 closures).
- Omega's `request_safe` orchestrator stabilises at 12 after the
  14-helper extraction (it's the natural floor for a security state
  machine).
- Pushing both below 10 would require breaking up cohesive security
  layers or shared closures, hurting readability without reducing
  any meaningful "bug surface".

15 is the highest value at which all post-Surgeon-and-Omega
orchestrators pass while still being well below the codebase's
worst legacy violations. New contributors aiming under 15 will
naturally adopt Surgeon's Extract-Method pattern.

### Patterns for future Automator passes

1. **Allowlist baselines beat hard caps for incremental adoption.**
   A hard cap of 15 would have failed 23 existing functions on day
   one. An allowlist baseline locks current state, blocks
   regressions, and ratchets down monotonically. This is the same
   shape as the project's `.mypy-baseline.txt` and mirrors how
   security teams adopt SAST in mature codebases.

2. **Mirror existing patterns rather than inventing new ones.** The
   C901 gate's structure (baseline file at root + regen script + CI
   workflow + comparison script in `scripts/`) is identical to the
   mypy-strict gate. A contributor who has read the mypy gate can
   read the complexity gate at a glance. Cohesion across gates is
   itself a DX feature.

3. **Educational PR templates outperform prescriptive checklists.**
   "Did you test locally?" is information-free. "Did you ensure no
   new HTTP request bypasses `request_safe`? [Sentinel]" educates
   the contributor about the project's security architecture while
   asking them to verify it. The journal cross-reference (`[Sentinel]`)
   gives the reviewer a click-target if the contributor has questions.

4. **Shift-left every CI gate.** Every check that fires in CI should
   also fire in pre-commit. The CI gate is the safety net; pre-commit
   is the developer-experience win. Catching a B603 violation in
   pre-commit instead of CI saves ~3 minutes per developer per
   incident, plus the morale cost of "the CI failed" notifications.

5. **The PR template's "out-of-scope debt" section is high-value.**
   Without it, contributors either (a) fix the unrelated debt and
   bloat their PR, or (b) silently leave the debt and reviewers
   re-discover it next time. Naming the debt explicitly converts
   "hidden assumption" into "documented decision."

### What this pass deliberately does NOT do

- **Does not enforce branch-protection-as-code** — that's a GitHub
  organisation-level concern (settings.yml, OPA, etc.) and would be
  overkill for a single repo. The CI gates are the practical
  equivalent: a PR cannot merge without them passing.
- **Does not auto-merge approved PRs** — the existing CI/PR review
  flow is healthy; auto-merge would skip human review and undermine
  the new educational PR template.
- **Does not require docstrings on private helpers** — Surgeon and
  Omega's helpers already have great docstrings as part of their
  extraction passes. Future enforcement would be a Scribe-style PR.

### Verification

- `pytest`: 1937 passed, 3 skipped (unchanged — Automator touches
  no application logic).
- `ruff check src/ tests/ scripts/`: All checks passed!
- `python3 -m mypy --no-pretty src tests` (CI-pinned 1.10.1):
  Success: no issues found in 381 source files.
- `python scripts/run_static_checks.py`: EXIT 0 (all gates green
  including new C901 gate; bandit warnings are pre-existing
  noise, not failures).
- C901 gate baseline: **23 functions** above threshold 15;
  baseline locks current state.

### Cross-journal links

- **Sentinel/Apex/Purist/Surgeon/Omega/Saboteur/Scribe** — the
  seven preceding passes that established the standards this gate
  enforces.
- **Mypy-strict gate** (`.github/workflows/mypy-strict.yml` +
  `.mypy-baseline.txt` + `scripts/regen_mypy_baseline.sh`) — the
  pattern this complexity gate mirrors.
- **`docs/architecture.md`** — Scribe's visual companion to the PR
  template's discipline references.

This is the eighth and final journal entry in the seven-discipline
arc. The project is now structurally complete: every standard has
both human-facing documentation (Scribe) and machine-facing
enforcement (Automator).
