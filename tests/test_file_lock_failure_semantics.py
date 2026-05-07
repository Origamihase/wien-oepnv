"""Verify that ``file_lock`` fails closed on exclusive-lock acquisition failure.

The earlier behaviour (silently logging at debug level and proceeding to
``yield`` regardless) conflated two failure modes:

* genuine cross-process contention exceeding the timeout — proceeding without
  a lock means TWO writers run their critical section concurrently;
* transient OS-level errors (rare).

For exclusive-lock callers this turned a defence into a footgun: the VOR
quota counter at ``providers/vor.py:save_request_count`` already has its own
``except (OSError, TimeoutError)`` clause designed to fail-closed (return
``MAX_REQUESTS_PER_DAY + 1``), but ``file_lock``'s swallow made that branch
unreachable. With multiple concurrent processes (manual full-refresh
workflow plus the regular cache cron), a contended lock could let both
processes read the same on-disk count, increment locally and overwrite each
other — double-spending the contractually-strict 100/day VAO Start budget.

Shared (``exclusive=False``) locks keep the historical "log and continue"
behaviour because ``atomic_write``-produced files give readers either the
pre- or post-write inode in its entirety; missing the reader lock relaxes
ordering, never integrity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils import locking


class _UnlockableFakeFile:
    """File-like object whose ``fileno()`` always raises OSError.

    fcntl/msvcrt both call ``fileno()`` first, so this guarantees the
    OS-level acquisition fails deterministically without any timing or
    racing on a real ``flock``.
    """

    def __init__(self, path: str) -> None:
        self.name = path

    def fileno(self) -> int:
        raise OSError("simulated lock-acquisition failure")


def test_exclusive_lock_failure_propagates_to_caller(tmp_path: Path) -> None:
    """``exclusive=True`` must re-raise so the caller's fail-closed branch fires."""
    fh = _UnlockableFakeFile(str(tmp_path / "exclusive.lock"))

    with pytest.raises(OSError, match="simulated lock-acquisition failure"):
        with locking.file_lock(fh, exclusive=True, timeout=0.1):
            # The body MUST NOT execute — that would mean the caller's
            # critical section ran without a lock held.
            pytest.fail(
                "file_lock yielded the body even though the OS lock could not "
                "be acquired; the exclusive-lock contract is violated and "
                "concurrent writers can corrupt the protected file."
            )


def test_shared_lock_failure_degrades_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``exclusive=False`` keeps the legacy degraded-but-readable behaviour."""
    import logging

    fh = _UnlockableFakeFile(str(tmp_path / "shared.lock"))

    body_executed = False
    caplog.set_level(logging.DEBUG, logger="src.utils.locking")
    with locking.file_lock(fh, exclusive=False, timeout=0.1):
        body_executed = True

    assert body_executed, (
        "Shared locks intentionally fall through on acquisition failure so "
        "concurrent reads of an atomically-replaced inode stay robust."
    )
    # The fallthrough is deliberate but must still be logged so operators can
    # diagnose unexpected contention patterns.
    assert any(
        "Dateisperre fehlgeschlagen" in record.getMessage()
        for record in caplog.records
    )


def test_thread_lock_counter_balanced_on_exclusive_failure(
    tmp_path: Path,
) -> None:
    """The exception path must still release the per-path thread-lock counter."""
    fh = _UnlockableFakeFile(str(tmp_path / "balance.lock"))

    locks_before = dict(locking._THREAD_LOCKS)
    counts_before = dict(locking._LOCK_COUNTS)

    with pytest.raises(OSError):
        with locking.file_lock(fh, exclusive=True, timeout=0.1):
            pass  # pragma: no cover

    locks_after = dict(locking._THREAD_LOCKS)
    counts_after = dict(locking._LOCK_COUNTS)
    assert locks_after == locks_before, (
        "Thread-lock dictionary leaked an entry on the failure path"
    )
    assert counts_after == counts_before, (
        "Reference counter leaked on the failure path"
    )


def test_exclusive_lock_failure_preserves_caller_remediation(
    tmp_path: Path,
) -> None:
    """Mirror the VOR quota counter's defensive ``except (OSError, TimeoutError)``.

    This pins the contract the caller relies on: if ``file_lock`` re-raises
    the acquisition error, the caller can fail-closed (return a sentinel,
    log critical, etc.). Before the fix, the caller's defensive branch was
    unreachable because ``file_lock`` swallowed the exception.
    """
    fh = _UnlockableFakeFile(str(tmp_path / "vor_like.lock"))

    fail_closed_branch_taken = False
    try:
        with locking.file_lock(fh, exclusive=True, timeout=0.1):
            pytest.fail("body must not run when acquisition fails")
    except (OSError, TimeoutError):
        # This is what providers/vor.py:save_request_count's outer except
        # clause does — return the sentinel that disables further requests.
        fail_closed_branch_taken = True

    assert fail_closed_branch_taken, (
        "The caller's fail-closed remediation branch must be reachable; "
        "this is the property file_lock used to silently break."
    )


def _resolve_repo_callers() -> list[tuple[str, str, bool]]:
    """List every in-repo file_lock(...) site so future callers stay covered.

    Returns a tuple ``(file, line_excerpt, exclusive)`` for each call site.
    The test below uses this to assert that every exclusive-lock caller has
    an OSError/TimeoutError handler on the surrounding context — the new
    file_lock contract is binary, so callers without remediation become
    unhandled-exception sites.
    """
    callers: list[tuple[str, str, bool]] = []
    repo = Path(__file__).resolve().parents[1]
    for relpath in (
        "src/providers/vor.py",
        "src/build_feed.py",
    ):
        text = (repo / relpath).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if "file_lock(" in stripped and "def " not in stripped:
                exclusive = "exclusive=True" in stripped
                callers.append((f"{relpath}:{lineno}", stripped, exclusive))
    return callers


def test_every_exclusive_caller_handles_lock_failure() -> None:
    """Smoke test: each in-repo ``file_lock(..., exclusive=True)`` site must
    sit inside a ``try`` block whose ``except`` covers OSError/TimeoutError."""
    repo = Path(__file__).resolve().parents[1]
    callers = _resolve_repo_callers()
    exclusive_callers = [c for c in callers if c[2]]
    assert exclusive_callers, "Audit assumption broken: no exclusive callers found"

    for site, _excerpt, _ in exclusive_callers:
        relpath, lineno = site.split(":")
        text = (repo / relpath).read_text(encoding="utf-8")
        # Look for ``except (OSError, TimeoutError)`` or equivalent within
        # ~50 lines after the call site (the typical try/except block
        # spans this range in vor.py and build_feed.py).
        offset = int(lineno)
        window = "\n".join(text.splitlines()[max(0, offset - 5) : offset + 50])
        has_handler = (
            "except (OSError, TimeoutError)" in window
            or "except (TimeoutError, OSError)" in window
            or "except OSError" in window
        )
        assert has_handler, (
            f"Exclusive file_lock caller at {site} lacks an "
            "OSError/TimeoutError handler — under the fail-closed contract, "
            "this site would crash on contention. Wrap the file_lock block "
            "in `try: ... except (OSError, TimeoutError): ...` and decide "
            "what to do (return a sentinel, log critical, retry on the "
            "next cron, …)."
        )
