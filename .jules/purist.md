# Purist 💧 Clean-Code Architect — Journal

Companion log to `.jules/sentinel.md` (security) and `.jules/apex.md`
(performance). Purist documents technical-debt eradication: static-analysis
fixes, type-strictness work, dead-code removal, and complexity reduction.

Format mirrors the sister journals: only entries that capture a unique
finding or a reusable pattern. Routine "removed unused import" cleanups
belong in the PR description, not the journal.

---

## 2026-05-07 - The "Misplaced `# noqa`" Anti-Pattern: Bandit-Ruff Marker Mismatch

**Finding:** Three `subprocess.run(...)` calls in the test suite carried
`# noqa: S603` on the **arguments line** rather than the call line:

```python
result = subprocess.run(
    [sys.executable, "-c", ...],  # noqa: S603       ← marker here
    capture_output=True,
)
```

Ruff diagnoses S603 on the line where the call begins (`subprocess.run(`),
not where the suspect arguments live. Marker on the args line silently
no-ops; the warning continues to surface. Bandit (`# nosec B603`),
in contrast, accepts the marker anywhere within the call expression — so
the dual-suppression style "nosec on inner line, noqa on inner line" works
for half the toolchain and silently fails for the other half.

**Why it matters beyond these 3 sites:** the project pairs bandit with
ruff S-rules to defence-in-depth subprocess auditing. When markers are
co-located on the inner line, the file *appears* doubly suppressed but is
actually only bandit-suppressed; ruff still flags it. CI's ruff gate then
fails — or, worse, if a contributor adds the marker style by copy-paste,
they ship a PR that broke ruff and assume the reviewer-visible warnings
were always there.

**Fix applied:** moved `# noqa: S603` (and the optional `# nosec B603`
twin) to the line containing `subprocess.run(`. The intent — "we trust
this call, suppress both auditors" — is preserved; the marker now lands
where each tool actually reads it. `tests/test_provider_plugins.py:38`,
`tests/test_provider_plugins.py:280`, and
`tests/test_update_all_stations_wrapper.py:147`. Ruff: 3 errors → 0.

**Pattern for future code:** when stacking `# noqa: <rule>` and
`# nosec <id>` on a multi-line call, place both on the **call-opening
line** (the one with `func_name(`). Ruff reads the diagnostic line; bandit
reads anywhere in the expression but is happy with the call line too.
Same-line stacking is the only configuration both tools agree on.

---

## 2026-05-07 - Mypy Version Skew: Local Strict Errors That Don't Exist in CI

**Finding:** `mypy src/` on a developer machine showed 8 errors in
`src/utils/http.py` (Class cannot subclass "..." has type "Any"); under
`mypy --no-pretty src tests` it was 148 errors across 79 files. CI's
"mypy --strict (src/ + tests/) vs allowlist" gate stayed green on every
recent PR.

**Root cause:** `mypy --version` on the developer machine reported
`1.19.1` (PATH); but `pip show mypy` reported `1.10.1` (pinned in
`requirements-dev.txt` to match CI). The user-shell `mypy` command was
resolving to a uv/pipx-installed copy ahead of `python3 -m mypy`. Mypy
1.19 ships newer typeshed stubs (typing for `urllib3`, `dnspython` etc.)
which changes whether `class Foo(SomeBase)` triggers
`[misc] cannot subclass Any`. The 4 `[unused-ignore]` errors come from
the same upgrade — `# type: ignore` lines that *were* needed under 1.10
become unnecessary under 1.19's better stubs.

**Why this matters beyond mypy:** the same versioning trap can hide in
any tool with a pinned-CI version vs. a globally-installed local copy
(ruff, bandit, black, prettier, eslint…). The project's
`scripts/regen_mypy_baseline.sh` already pins `mypy>=1.10,<1.11` and uses
`python3 -m mypy` (not bare `mypy`) explicitly to avoid this. Running
the bare `mypy` command for a quick sanity check bypasses that pin and
yields false-positive technical-debt reports.

**Pattern for future debt audits:** before declaring "X errors in
repo Y", verify the tool version matches CI's pinned version. For mypy
specifically: `pip show mypy | grep Version` (the import-time version)
is more authoritative than `mypy --version` (the PATH-bound version). If
they disagree, `python3 -m mypy ...` always wins. Same caveat applies to
running ruff via `pre-commit` (pinned in `.pre-commit-config.yaml`) vs.
bare `ruff` from PATH.

**Pattern for future contributors:** the `scripts/regen_mypy_baseline.sh`
trick — `pip install -q "mypy>=1.10,<1.11"` followed by `python3 -m mypy`
— is the right way to invoke a version-pinned tool from any project
script. Worth replicating for ruff if/when the project adopts a pinned
ruff version too.

---

## 2026-05-07 - Inventory: Out-of-Scope Debt Identified But Deliberately Deferred

Documented here so future Purist passes don't re-discover and re-defer
the same items.

**Complexity (`C901`, not in selected ruleset):** 48 functions exceed
the McCabe-10 default. Top offenders:
- `src/build_feed.py::_collect_items` — complexity 53 (touched by Apex
  Phase 1; deeply intertwined with the deadline-eviction loop). Needs a
  dedicated refactor PR with full benchmark coverage to ensure the
  cleanup doesn't regress the wall-clock wins from PR #1315.
- `src/build_feed.py::_dedupe_items` — complexity 18.
- `src/build_feed.py::_drop_old_items` — complexity 15.

**Simplification (`SIM*`, not in selected ruleset):** 67 findings.
Mostly `SIM108` (if-else → ternary) and `SIM102` (collapsible-if). All
are stylistic; none indicate behavioural defects.

**Forward-defensive `# noqa` markers referencing unselected rules:**
- `# noqa: D401, ANN001` in `src/utils/text.py` (D and ANN families not
  selected)
- `# noqa: PT005` in `tests/conftest.py:211` (PT family not selected)

These are harmless today — ruff ignores noqa-codes for unselected rules
without warning. Removing them is *cosmetically* cleaner but also
*forward-fragile*: if the project ever expands its rule set, the markers
already in place would suppress the new diagnostics at exactly the spots
where the original author judged them needed. Leave alone unless the
project commits to a documented final ruleset.

**Stylistic `noqa` for active rules** that are correctly placed and
intentional: `# noqa: S110` (silent-except-pass for documented
fail-closed paths), `# noqa: S101` (asserts in non-test code where
the assertion is a precondition contract), `# noqa: S311` (random for
non-cryptographic UI seeding). All audited, all legitimate.
