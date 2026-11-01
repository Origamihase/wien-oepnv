#!/usr/bin/env python3
"""C901 complexity gate against an allowlist baseline.

ruff measures the McCabe complexity of every function in ``src/`` and
``scripts/``. Every function above the project's threshold (15) has to be
listed in ``.c901-baseline.txt`` as ``<path>::<function> <complexity>``. The
gate passes only when the measurement and the baseline agree line for line,
and it names each difference:

* new: a function above 15 without an entry,
* increased: a function more complex than its entry,
* decreased: a function simpler than its entry but still above 15,
* gone: an entry without a function above 15 (it dropped to 15 or less,
  moved to another file or was renamed).

Decreased and gone entries fail the gate as well, so the baseline always
equals the code and no allowance is looser than the code needs. The fix is
one command in the same change (``bash scripts/regen_c901_baseline.sh``).
Until 2026-11-01 only new and increased entries failed: a function dropping
to 15 or less raised a ``::notice::`` and a decrease above 15 raised nothing.
``fetch_events`` kept its entry 51 for a month while the code measured 35,
16 points a later change could have used up without anyone noticing.

Entries are keyed by file because names repeat across files (``main`` alone
is defined in 38 files of ``src/`` and ``scripts/``). With bare names, every
other ``main`` would have passed under the entry
``scripts/generate_markdown_stats.py::main 16`` as long as it stayed at or
below 16. Two functions of the same name in one file get one line each.

The gate fails closed (exit 2) when it cannot measure: ruff missing or
crashing (an exit code other than 0 or 1), output that is not ruff's JSON, a
diagnostic other than C901, a C901 message that does not parse or names
another threshold, or an exit code that contradicts the findings. Until
2026-11-01 the exit code was not read: a ruff that started but failed (exit
2, nothing on stdout) or printed lines the gate could not read counted every
entry as fixed, and the gate passed without having measured anything. A
missing or hanging ruff ended in a traceback.

Threshold: 15 (the Surgeon-pass target). The default ruff McCabe threshold
is 10, but 10 is too tight for our security state machines
(``request_safe`` orchestrator stabilises at ~12 after Omega's extraction).

Run locally (uses the ruff of the running interpreter, the one
requirements-dev.txt installs, like CI):
    python scripts/check_complexity.py

Rewrite the baseline from the current tree:
    bash scripts/regen_c901_baseline.sh
(which runs ``python scripts/check_complexity.py --write-baseline``).

Exit codes:
    0  — the measurement equals the baseline
    1  — at least one new, increased, decreased or gone entry
    2  — no measurement (ruff missing, crashed or gave unreadable output)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.files import read_capped_text  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / ".c901-baseline.txt"
THRESHOLD = 15
#: Trees the gate measures. ``tests/`` stays outside: no test function is
#: above 15, and a test body is judged by how it reads, not by its branches.
SCAN_PATHS: tuple[str, ...] = ("src", "scripts")
RUFF_TIMEOUT_SECONDS = 120

# Security: per-loader byte cap. Pre-fix ``_parse_baseline`` read the
# baseline file via ``path.read_text(...).splitlines()`` with NO size
# cap — a planted huge baseline crashed the C901 CI gate (``MemoryError``
# propagates past the gate's surrounding ``except`` and aborts the whole
# static-checks run). ``.c901-baseline.txt`` is a small list of
# ``<path>::<name> <complexity>`` lines, typically a few KiB; 1 MiB is
# ~500x legit while still rejecting GiB-sized planted attacks.
MAX_BASELINE_FILE_BYTES = 1 * 1024 * 1024

# ruff's C901 message (``--output-format json``, field ``message``):
#   `fetch_events` is too complex (35 > 15)
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")
_MESSAGE_RE = re.compile(r"^`([^`]+)` is too complex \((\d+) > (\d+)\)$")

#: One baseline line or one measured function: ``(path::name, complexity)``.
Entry = tuple[str, int]


class MeasurementError(RuntimeError):
    """ruff gave no trustworthy measurement, so the gate must not pass."""


@dataclass
class GateResult:
    """Every difference between the baseline and the measurement."""

    new: list[Entry] = field(default_factory=list)
    increased: list[tuple[str, int, int]] = field(default_factory=list)
    decreased: list[tuple[str, int, int]] = field(default_factory=list)
    gone: list[Entry] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.new or self.increased or self.decreased or self.gone)


def _parse_baseline(path: Path) -> list[Entry]:
    """Read ``.c901-baseline.txt`` into a list of ``(key, complexity)`` entries.

    Lines are ``<path>::<name> <complexity>``; blank lines and ``#``
    comments are ignored. The value is split off at the last whitespace, so
    a path with a space reads back as written. Each line is one allowance:
    a key listed twice allows two functions of that name in that file, and
    the gate fails unless both exist with exactly these values, so a
    duplicated line (merge conflict, careless edit) can never relax the
    gate. A missing, unreadable or oversized file is an empty baseline (the
    strictest state).
    """
    if not path.exists():
        return []
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so
    # a planted huge baseline cannot exhaust memory and crash the CI
    # gate. Empty baseline = strictest state, exactly what the
    # missing-file branch above already returns.
    content = read_capped_text(path, MAX_BASELINE_FILE_BYTES, label="C901 baseline")
    if content is None:
        return []
    entries: list[Entry] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # rsplit: the key holds the file path, which may contain a space
        # (``_format_baseline`` writes it unquoted).
        parts = line.rsplit(None, 1)
        # ASCII digits only: ``str.isdigit`` also accepts "²", which ``int``
        # then rejects with a traceback instead of this warning.
        if len(parts) != 2 or _ASCII_DIGITS_RE.fullmatch(parts[1]) is None:
            print(
                f"::warning::malformed baseline line ignored: {raw!r}",
                file=sys.stderr,
            )
            continue
        if "::" not in parts[0]:
            print(
                f"::warning::baseline entry {parts[0]!r} has no file path "
                "(format before 2026-11-01); regenerate the baseline",
                file=sys.stderr,
            )
        entries.append((parts[0], int(parts[1])))
    return entries


def _ruff_command() -> list[str]:
    """ruff of the running interpreter (requirements-dev.txt), C901 only, as JSON."""
    return [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--select",
        "C901",
        "--no-cache",
        "--output-format",
        "json",
        f"--config=lint.mccabe.max-complexity={THRESHOLD}",
        *SCAN_PATHS,
    ]


def _relative_key_path(filename: str) -> str:
    """Repository-relative POSIX path of a file ruff reported."""
    path = Path(filename)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError as exc:
        raise MeasurementError(
            f"ruff reported a file outside the repository: {filename!r}"
        ) from exc


def _entry_from_diagnostic(diagnostic: object) -> Entry:
    """Turn one diagnostic of ruff's JSON output into an entry, or fail."""
    if not isinstance(diagnostic, dict):
        raise MeasurementError(f"ruff output holds a non-object: {diagnostic!r}")
    code = diagnostic.get("code")
    message = diagnostic.get("message")
    filename = diagnostic.get("filename")
    if code != "C901":
        raise MeasurementError(
            f"ruff reported {code!r} instead of C901 in {filename!r}: {message!r}"
        )
    if not isinstance(message, str) or not isinstance(filename, str):
        raise MeasurementError(f"C901 diagnostic without message or file: {diagnostic!r}")
    match = _MESSAGE_RE.match(message.strip())
    if match is None:
        raise MeasurementError(f"unreadable C901 message in {filename!r}: {message!r}")
    name, complexity, threshold = match.group(1), int(match.group(2)), int(match.group(3))
    if threshold != THRESHOLD:
        raise MeasurementError(
            f"ruff measured against threshold {threshold}, not {THRESHOLD}: {message!r}"
        )
    key = f"{_relative_key_path(filename)}::{name}"
    # The baseline holds one entry per line: a path with a line break (any
    # character ``str.splitlines`` splits at) could not be read back.
    if key.splitlines() != [key]:
        raise MeasurementError(
            f"a file path with a line break cannot be a baseline key: {filename!r}"
        )
    return (key, complexity)


def _parse_ruff_output(stdout: str, returncode: int) -> list[Entry]:
    """Validate ruff's JSON output and its exit code against each other."""
    try:
        diagnostics = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"ruff output is not JSON ({exc.msg})") from exc
    if not isinstance(diagnostics, list):
        raise MeasurementError("ruff output is not a JSON list")
    entries = [_entry_from_diagnostic(diagnostic) for diagnostic in diagnostics]
    # ruff exits 1 exactly when it reports at least one diagnostic.
    if (returncode == 1) != bool(entries):
        raise MeasurementError(
            f"ruff exited with {returncode} but reported {len(entries)} finding(s)"
        )
    return entries


def _measure() -> list[Entry]:
    """Run ruff over ``SCAN_PATHS``; raise ``MeasurementError`` unless it measured."""
    try:
        completed = subprocess.run(  # nosec B603
            _ruff_command(),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=RUFF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasurementError(f"ruff could not run: {exc}") from exc
    # ruff's own warnings (removed rules, configuration hints) stay visible.
    if completed.stderr.strip():
        print(completed.stderr.rstrip(), file=sys.stderr)
    if completed.returncode not in (0, 1):
        raise MeasurementError(f"ruff exited with {completed.returncode}")
    return _parse_ruff_output(completed.stdout, completed.returncode)


def _compare(baseline: list[Entry], current: list[Entry]) -> GateResult:
    """Pair entries per key, highest value first, and collect every difference."""
    allowed: dict[str, list[int]] = {}
    for key, value in baseline:
        allowed.setdefault(key, []).append(value)
    measured: dict[str, list[int]] = {}
    for key, value in current:
        measured.setdefault(key, []).append(value)
    result = GateResult()
    for key in sorted(allowed.keys() | measured.keys()):
        before = sorted(allowed.get(key, []), reverse=True)
        after = sorted(measured.get(key, []), reverse=True)
        for old, new in zip(before, after, strict=False):
            if new > old:
                result.increased.append((key, old, new))
            elif new < old:
                result.decreased.append((key, old, new))
        result.new.extend((key, value) for value in after[len(before):])
        result.gone.extend((key, value) for value in before[len(after):])
    return result


def _format_baseline(entries: list[Entry]) -> str:
    """The baseline file for *entries*: header, then one sorted line per function."""
    header = (
        f"# C901 allowlist: every function in {' and '.join(f'{p}/' for p in SCAN_PATHS)} "
        f"above complexity {THRESHOLD}.\n"
        "# Written by `bash scripts/regen_c901_baseline.sh`. The gate fails on any\n"
        "# difference, so regenerate this file instead of editing it by hand.\n"
    )
    lines = sorted(entries, key=lambda entry: (entry[0], -entry[1]))
    return header + "".join(f"{key} {value}\n" for key, value in lines)


_REGEN_HINT = (
    "regenerate the baseline with `bash scripts/regen_c901_baseline.sh` and "
    "commit .c901-baseline.txt with the change."
)


def _report(baseline: list[Entry], current: list[Entry], result: GateResult) -> None:
    print("===== C901 complexity gate =====")
    print(f"measured trees      : {', '.join(SCAN_PATHS)} (threshold {THRESHOLD})")
    print(f"baseline entries    : {len(baseline)}")
    print(f"current violations  : {len(current)}")
    print(f"new                 : {len(result.new)}")
    print(f"increased           : {len(result.increased)}")
    print(f"decreased           : {len(result.decreased)}")
    print(f"gone (now <= {THRESHOLD}, moved or renamed): {len(result.gone)}")

    if result.new:
        print()
        print(f"::error::{len(result.new)} function(s) above {THRESHOLD} without a baseline entry:")
        for key, value in result.new:
            print(f"  - {key}: complexity {value} (cap: {THRESHOLD})")
        print(
            "Either refactor below the threshold (Surgeon's pattern: Extract "
            "Method along the natural seams), OR, if the complexity is "
            "intentional and reviewer-approved, " + _REGEN_HINT
        )

    if result.increased:
        print()
        print(f"::error::{len(result.increased)} baselined function(s) got more complex:")
        for key, old, new in result.increased:
            print(f"  - {key}: {old} → {new}")
        print(
            "A function on the C901 baseline is meant to ratchet DOWN, never "
            "up. Either reduce its complexity to the baseline value, or, if "
            "the increase is intentional, " + _REGEN_HINT
        )

    if result.decreased or result.gone:
        print()
        stale = len(result.decreased) + len(result.gone)
        print(
            f"::error::{stale} baseline entr{'y is' if stale == 1 else 'ies are'} "
            "looser than the code (an improvement that is not locked in yet):"
        )
        for key, old, new in result.decreased:
            print(f"  - {key}: {old} → {new}")
        for key, value in result.gone:
            print(f"  - {key}: {value} → at most {THRESHOLD}, moved or renamed")
        print(
            "An entry above the measured value lets the function grow back "
            "unseen, so " + _REGEN_HINT
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C901 complexity gate against .c901-baseline.txt."
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="rewrite .c901-baseline.txt from the current measurement",
    )
    args = parser.parse_args(argv)

    try:
        current = _measure()
    except MeasurementError as exc:
        print(f"::error title=C901 gate::no measurement, the gate fails: {exc}")
        return 2

    if args.write_baseline:
        BASELINE.write_text(_format_baseline(current), encoding="utf-8")
        print(
            f"Wrote {BASELINE.name} ({len(current)} function(s) above "
            f"C901={THRESHOLD} in {', '.join(SCAN_PATHS)})"
        )
        return 0

    baseline = _parse_baseline(BASELINE)
    result = _compare(baseline, current)
    _report(baseline, current, result)
    if not result.passed:
        return 1
    print()
    print(f"::notice::C901 gate passed (measurement equals the baseline, threshold {THRESHOLD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
