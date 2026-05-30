#!/usr/bin/env python3
"""C901 complexity gate using an allowlist baseline.

Mirrors the ``.mypy-baseline.txt`` allowlist pattern: every function
whose McCabe complexity exceeds the project's threshold (15) must be
recorded in ``.c901-baseline.txt``. The CI gate then fails only if a
PR introduces a NEW violation, OR if an existing violation has gotten
worse (the function's complexity exceeds its baselined value).

Reducing a baselined function's complexity below threshold is always
allowed — the baseline ratchets monotonically downward, never upward.

Threshold: 15 (the Surgeon-pass target). The default ruff McCabe
threshold is 10, but 10 is too tight for our security state machines
(``request_safe`` orchestrator stabilises at ~12 after Omega's
extraction).

Run locally:
    python scripts/check_complexity.py

Regenerate the baseline after a refactor:
    bash scripts/regen_c901_baseline.sh

Exit codes:
    0  — no new violations
    1  — at least one new function exceeds the threshold, OR a
         baselined function has gotten more complex
"""
from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.files import read_capped_text  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / ".c901-baseline.txt"
THRESHOLD = 15

# Security: per-loader byte cap. Pre-fix ``_parse_baseline`` read the
# baseline file via ``path.read_text(...).splitlines()`` with NO size
# cap — a planted huge baseline crashed the C901 CI gate (``MemoryError``
# propagates past the gate's surrounding ``except`` and aborts the whole
# static-checks run). ``.c901-baseline.txt`` is a small list of
# ``<name> <complexity>`` lines, typically a few KiB; 1 MiB is ~500x
# legit while still rejecting GiB-sized planted attacks.
MAX_BASELINE_FILE_BYTES = 1 * 1024 * 1024

# ruff C901 lines look like:
#   C901 `request_safe` is too complex (12 > 10)
# We extract (function_name, complexity) tuples.
_C901_RE = re.compile(
    r"^C901\s+`([^`]+)`\s+is too complex \((\d+)\s*>\s*\d+\)\s*$"
)


def _parse_baseline(path: Path) -> dict[str, int]:
    """Read ``.c901-baseline.txt`` into ``{name: max_allowed_complexity}``.

    Lines are ``<name> <complexity>``; blank lines and ``#`` comments
    are ignored. A missing file is treated as an empty baseline (the
    strictest possible state).
    """
    if not path.exists():
        return {}
    out: dict[str, int] = {}
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so
    # a planted huge baseline cannot exhaust memory and crash the CI
    # gate. Empty baseline = strictest state, exactly what the
    # missing-file branch above already returns.
    content = read_capped_text(path, MAX_BASELINE_FILE_BYTES, label="C901 baseline")
    if content is None:
        return {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            print(
                f"::warning::malformed baseline line ignored: {raw!r}",
                file=sys.stderr,
            )
            continue
        try:
            value = int(parts[1])
        except ValueError:
            print(
                f"::warning::malformed baseline line ignored: {raw!r}",
                file=sys.stderr,
            )
            continue
        # Duplicate-name guard: a baseline that lists the same function
        # twice (operator error, merge conflict, careless regen) MUST NOT
        # silently relax the gate to the higher value. Pre-fix the
        # ``out[name] = value`` last-wins, so two lines ``foo 16`` then
        # ``foo 20`` weakened the gate to 20 — a real ``foo`` complexity-17
        # violation would then pass. Take the stricter (lower) value and
        # emit a workflow warning so the duplicate is visible in CI.
        existing = out.get(parts[0])
        if existing is not None:
            print(
                f"::warning::duplicate baseline entry for {parts[0]!r} "
                f"({existing} vs {value}); keeping stricter "
                f"{min(existing, value)}",
                file=sys.stderr,
            )
            out[parts[0]] = min(existing, value)
        else:
            out[parts[0]] = value
    return out


def _run_ruff_c901() -> list[tuple[str, int]]:
    """Invoke ``ruff check --select C901 --no-cache src/`` and parse the
    ``C901`` lines into ``(function_name, complexity)`` tuples.
    """
    completed = subprocess.run(  # nosec B603, B607
        [
            "ruff",
            "check",
            "--select",
            "C901",
            "--no-cache",
            "--output-format",
            "concise",
            f"--config=lint.mccabe.max-complexity={THRESHOLD}",
            "src",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # Ruff exits 1 when violations are found. We treat that as data, not
    # an error — the gate is the comparison against the baseline below.
    findings: list[tuple[str, int]] = []
    for line in completed.stdout.splitlines():
        # concise format: "<path>:<line>:<col>: C901 `name` is too complex (N > M)"
        # We only need the C901-prefixed substring.
        idx = line.find("C901 ")
        if idx < 0:
            continue
        match = _C901_RE.match(line[idx:].rstrip())
        if match:
            findings.append((match.group(1), int(match.group(2))))
    return findings


def main() -> int:
    baseline = _parse_baseline(BASELINE)
    current = _run_ruff_c901()

    new_violations: list[tuple[str, int]] = []
    increased: list[tuple[str, int, int]] = []  # name, old, new
    seen: set[str] = set()

    for name, complexity in current:
        seen.add(name)
        baseline_complexity = baseline.get(name)
        if baseline_complexity is None:
            new_violations.append((name, complexity))
        elif complexity > baseline_complexity:
            increased.append((name, baseline_complexity, complexity))

    fixed = [name for name in baseline if name not in seen]

    print("===== C901 complexity gate =====")
    print(f"baseline functions  : {len(baseline)}")
    print(f"current violations  : {len(current)}")
    print(f"new violations      : {len(new_violations)}")
    print(f"increased           : {len(increased)}")
    print(f"fixed (no longer >{THRESHOLD}): {len(fixed)}")

    if new_violations:
        print()
        print(f"::error::{len(new_violations)} new C901 violation(s) "
              f"introduced (threshold > {THRESHOLD}):")
        for name, complexity in new_violations:
            print(f"  - {name}: complexity {complexity} (cap: {THRESHOLD})")
        print()
        print(
            "Either refactor below the threshold (Surgeon's pattern: "
            "Extract Method along the natural seams), OR — if the new "
            "complexity is intentional and reviewer-approved — regenerate "
            "the baseline via `bash scripts/regen_c901_baseline.sh` and "
            "commit the result."
        )

    if increased:
        print()
        print(f"::error::{len(increased)} baselined function(s) got more "
              f"complex:")
        for name, old, new in increased:
            print(f"  - {name}: {old} → {new}")
        print()
        print(
            "A function on the C901 baseline is meant to ratchet DOWN, "
            "never up. Either reduce complexity to the baseline value, "
            "or — if the increase is intentional — regenerate the "
            "baseline."
        )

    if fixed:
        print()
        print(
            f"::notice::{len(fixed)} pre-existing offender(s) no longer "
            f"exceed {THRESHOLD}; consider regenerating the baseline to "
            f"lock in the improvement:"
        )
        for name in fixed:
            print(f"  - {name}")

    if new_violations or increased:
        return 1
    print()
    print(f"::notice::C901 gate passed (0 new violations above {THRESHOLD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
