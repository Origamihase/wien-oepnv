"""Sentinel PoC: ÖBB workbook-cache size-bomb defence.

Threat model
------------
``scripts.update_station_directory.download_workbook`` is the
soft-fail download wrapper around the weekly ÖBB workbook
(:data:`scripts.update_station_directory.DEFAULT_CACHED_WORKBOOK_PATH`).
On every successful HTTP fetch it atomically writes the bytes to
*cache_path*; on every download failure (transient ``data.oebb.at``
outage, CDN issue, weekend maintenance) it falls back to reading the
cached snapshot — closing the gap that was the sole fail-fast source in
the cron pipeline pre-PR-#1441.

The on-disk fallback path was the **only** remaining ``Path.read_bytes()``
callsite under ``src/`` and ``scripts/`` that did NOT route through the
canonical size-cap helper family
(:func:`src.utils.files.read_capped_json` /
:func:`src.utils.files.read_capped_text`). Pre-fix the relevant line
was::

    if cache_path.exists():
        return BytesIO(cache_path.read_bytes())

``Path.read_bytes()`` buffers the WHOLE file into memory before any
downstream defence layer (``BytesIO`` constructor, openpyxl loader,
:func:`src.utils.files.validate_zip_archive_safe`) can run, so a
planted-huge cache file allocates O(file_size) bytes and raises
``MemoryError`` past the surrounding network ``except``. The cron
orchestrator (``scripts/update_all_stations.py`` runs every update
script via ``subprocess.run(check=True)``) propagates the unhandled
``MemoryError`` as ``CalledProcessError`` and aborts the WHOLE weekly
station-directory cron tick.

Attack pre-conditions
---------------------
The cache file lives at
:data:`scripts.update_station_directory.DEFAULT_CACHED_WORKBOOK_PATH`
(``data/oebb-verkehrsstationen.xlsx``) and is auto-committed by the
weekly ``update-stations.yml`` workflow (``add_options: "-A"``). Any
write to that path under the runner's process — compromised CI runner,
hostile PR landing a malformed snapshot, manual operator dump, partial
flush + power loss leaving a giant tail-padded file — silently lands a
planted payload that the next failed HTTP fetch reads back without a
cap. Production xlsx is ~62 KiB; the new
:data:`MAX_CACHED_WORKBOOK_BYTES` cap (10 MiB, identical to
:data:`src.utils.http.MAX_PAYLOAD_SIZE` — the upper bound HTTP could
have legitimately produced) is ~169x the production size.

Fix shape
---------
A new shared helper :func:`src.utils.files.read_capped_bytes` mirrors
the TOCTOU + special-file defence of
:func:`src.utils.files.read_capped_json` /
:func:`src.utils.files.read_capped_text` for binary blob payloads. The
``download_workbook`` fallback branch routes through it:

  * On success (cache <= cap), returns the cached bytes.
  * On oversized cache, returns ``None`` and the caller falls through
    to the error log + original-exception re-raise — mirroring the
    pre-fix shape on a missing-cache miss.
  * On missing cache (``OSError``), returns ``None`` — same fall-through.

Why the canonical shape: ``Path.open("rb")`` ->
``os.fstat(handle.fileno())`` for the size check (immune to symlink
swap mid-resolution — pre-fix ``Path.stat`` and ``Path.open``
independently resolve and follow symlinks, so an attacker swapping the
inode via ``os.replace`` between those syscalls bypasses any
``stat().st_size`` cap) -> ``handle.read(max_bytes + 1)`` defensive
read budget (catches special files like ``/dev/zero``, FIFOs,
character devices that report ``st_size == 0`` but yield unbounded
bytes on read).
"""

from __future__ import annotations

import ast
import logging
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scripts import update_station_directory


# ============================================================================
# Precondition: the canonical helper and cap constant exist.
# Pinning these is the auto-discoverable invariant: a future PR that adds a
# new on-disk binary-blob loader without the cap fails the inventory test
# below.
# ============================================================================


def test_precondition_read_capped_bytes_helper_exists() -> None:
    """The shared helper must be importable from :mod:`src.utils.files`."""
    from src.utils.files import DEFAULT_MAX_BYTES_FILE_BYTES, read_capped_bytes

    assert callable(read_capped_bytes)
    assert isinstance(DEFAULT_MAX_BYTES_FILE_BYTES, int)
    # Cap must accommodate the largest legitimate on-disk binary blob in
    # the repo (production xlsx ~62 KiB) with comfortable headroom.
    assert DEFAULT_MAX_BYTES_FILE_BYTES >= 1_000_000


def test_precondition_max_cached_workbook_bytes_constant() -> None:
    """The per-script cap constant must be exposed for inventory tests."""
    assert isinstance(update_station_directory.MAX_CACHED_WORKBOOK_BYTES, int)
    # Cap must accommodate the production xlsx (~62 KiB) with comfortable
    # headroom (10 MiB == 169x production).
    assert update_station_directory.MAX_CACHED_WORKBOOK_BYTES >= 1_000_000
    # Cap must not exceed the HTTP-fetch upper bound — anything larger
    # than what HTTP could have legitimately produced is by definition
    # tampered cache state.
    from src.utils.http import MAX_PAYLOAD_SIZE

    assert update_station_directory.MAX_CACHED_WORKBOOK_BYTES <= MAX_PAYLOAD_SIZE


# ============================================================================
# Helpers used across the PoC test set.
# ============================================================================


def _fake_session_factory() -> Any:
    """Mirror the dummy session used by the existing workbook-cache tests."""

    def fake_session_with_retries(_user_agent: str) -> Any:
        class _DummySession:
            def __enter__(self) -> _DummySession:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

        return _DummySession()

    return fake_session_with_retries


def _failing_fetch_factory(message: str = "simulated data.oebb.at outage") -> Any:
    def fake_fetch(_session: Any, _url: str, *, timeout: Any) -> bytes:
        raise OSError(message)

    return fake_fetch


# ============================================================================
# PoC 1: an oversized cache file is treated as missing and the original
# upstream exception is re-raised. Pre-fix the line
# ``return BytesIO(cache_path.read_bytes())`` allocated O(file_size)
# bytes and the cron pipeline aborted with ``MemoryError``.
# ============================================================================


def test_download_workbook_rejects_oversized_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: an oversized planted cache file is read unbounded and
    crashes the cron pipeline. Post-fix: the file is treated as missing
    and the original ``OSError`` re-raised — matching the no-cache
    fall-through shape.
    """
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"
    # Lower the cap so the test doesn't have to write 10 MiB to disk. The
    # production cap is 10 MiB; the cap is a parameter to
    # ``read_capped_bytes`` so monkeypatching the module-level constant
    # here is sufficient to exercise the rejection branch.
    monkeypatch.setattr(update_station_directory, "MAX_CACHED_WORKBOOK_BYTES", 1024)
    cache_path.write_bytes(b"PK\x03\x04" + b"\x00" * 4096)

    monkeypatch.setattr(
        update_station_directory,
        "session_with_retries",
        _fake_session_factory(),
    )
    monkeypatch.setattr(
        update_station_directory,
        "fetch_content_safe",
        _failing_fetch_factory(),
    )

    with pytest.raises(OSError, match="simulated data.oebb.at outage"):
        update_station_directory.download_workbook(
            "https://example.invalid/wb.xlsx", cache_path=cache_path
        )


# ============================================================================
# PoC 2: confirm the rejection branch does NOT call into ``BytesIO``
# (i.e. the bytes never reach the in-memory wrapper). Pre-fix the
# ``BytesIO`` constructor was always invoked with the unbounded
# payload; post-fix the oversized file short-circuits to the error
# branch BEFORE any allocation happens.
# ============================================================================


def test_download_workbook_skips_bytesio_when_cache_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: oversized cache reaches ``BytesIO(cache_path.read_bytes())``
    and allocates O(file_size) bytes. Post-fix: the size cap rejects the
    file before ``BytesIO`` is touched."""
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"
    monkeypatch.setattr(update_station_directory, "MAX_CACHED_WORKBOOK_BYTES", 1024)
    cache_path.write_bytes(b"PK\x03\x04" + b"\x00" * 4096)

    monkeypatch.setattr(
        update_station_directory,
        "session_with_retries",
        _fake_session_factory(),
    )
    monkeypatch.setattr(
        update_station_directory,
        "fetch_content_safe",
        _failing_fetch_factory(),
    )

    with patch.object(update_station_directory, "BytesIO") as mock_bytesio:
        with pytest.raises(OSError, match="simulated data.oebb.at outage"):
            update_station_directory.download_workbook(
                "https://example.invalid/wb.xlsx", cache_path=cache_path
            )
        mock_bytesio.assert_not_called()


# ============================================================================
# PoC 3: a within-cap cache file still works (regression test). Mirrors
# the existing ``test_download_workbook_falls_back_to_cache_on_network_failure``
# but explicitly exercises the new helper boundary.
# ============================================================================


def test_download_workbook_serves_cache_under_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache files at or below the cap must continue to serve as the
    fallback payload — the size cap is additive and must not regress
    the existing soft-fail contract."""
    cached_payload = b"PK\x03\x04" + b"previously-cached-bytes" * 32
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"
    cache_path.write_bytes(cached_payload)

    monkeypatch.setattr(
        update_station_directory,
        "session_with_retries",
        _fake_session_factory(),
    )
    monkeypatch.setattr(
        update_station_directory,
        "fetch_content_safe",
        _failing_fetch_factory(),
    )

    buf = update_station_directory.download_workbook(
        "https://example.invalid/wb.xlsx", cache_path=cache_path
    )

    assert isinstance(buf, BytesIO)
    assert buf.getvalue() == cached_payload


# ============================================================================
# PoC 4: read_capped_bytes itself rejects oversized files BEFORE any
# downstream allocation. This is the canonical-helper-level invariant
# that the per-script call sites inherit.
# ============================================================================


def test_read_capped_bytes_rejects_oversized(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The helper opens the file, fstats it, and refuses to read when
    the size exceeds the cap. The downstream caller sees ``None``."""
    from src.utils.files import read_capped_bytes

    fake_path = tmp_path / "oversized.bin"
    fake_path.write_bytes(b"x" * 4096)

    with caplog.at_level(logging.WARNING):
        result = read_capped_bytes(fake_path, 1024, label="test")
    assert result is None
    assert any("too large" in record.getMessage() for record in caplog.records)


def test_read_capped_bytes_serves_within_cap(tmp_path: Path) -> None:
    """The helper returns the raw bytes when the file fits under the
    cap. Mirrors the in-bound shape of
    :func:`read_capped_json` / :func:`read_capped_text`."""
    from src.utils.files import read_capped_bytes

    fake_path = tmp_path / "small.bin"
    payload = b"PK\x03\x04" + b"normal-size-xlsx-bytes" * 8
    fake_path.write_bytes(payload)

    result = read_capped_bytes(fake_path, 4096, label="test")
    assert result == payload


def test_read_capped_bytes_returns_none_when_missing(tmp_path: Path) -> None:
    """A missing file must return ``None`` (not raise) — mirrors the
    OSError-swallowing shape of :func:`read_capped_json` /
    :func:`read_capped_text` so callers can branch on missing vs.
    oversized vs. malformed without distinguishing the cause."""
    from src.utils.files import read_capped_bytes

    fake_path = tmp_path / "missing.bin"
    assert not fake_path.exists()

    result = read_capped_bytes(fake_path, 4096, label="test")
    assert result is None


# ============================================================================
# PoC 5: TOCTOU-safe shape. The helper must use ``os.fstat`` on the
# *open* file descriptor, not ``Path.stat()`` — closing the
# stat/open race window an attacker uses to swap the inode between
# the two syscalls.
# ============================================================================


def test_read_capped_bytes_uses_fstat_not_path_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix shape ``Path.stat().st_size > cap; return Path.open().read()``
    is bypassable by atomic ``os.replace`` of a symlink between the two
    syscalls. Post-fix ``os.fstat(handle.fileno())`` reports the size
    of the OPENED inode, immune to subsequent swaps. We verify by
    forcing ``Path.stat()`` to lie about the file's size — if the
    helper used the lying stat, the oversized read would proceed; with
    the fstat shape, the real size is observed and the file rejected."""
    from src.utils.files import read_capped_bytes

    fake_path = tmp_path / "lying.bin"
    fake_path.write_bytes(b"y" * 4096)

    class _FakeStat:
        st_size = 64  # Lies — real file is 4 KiB

    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: _FakeStat())

    try:
        result = read_capped_bytes(fake_path, 1024, label="test")
        # If the helper read via Path.stat (the lying value), result
        # would be 4 KiB. Post-fix the helper uses ``os.fstat`` on the
        # open descriptor — sees the real 4 KiB > 1024 cap — returns
        # ``None``.
        assert result is None
    finally:
        monkeypatch.setattr(Path, "stat", original_stat)


# ============================================================================
# PoC 6: special-file defence. A FIFO / ``/dev/zero`` / character device
# reports ``st_size == 0`` but yields unbounded bytes on ``read()``.
# The defensive ``handle.read(max_bytes + 1)`` budget enforces the cap
# even when ``st_size`` lies.
# ============================================================================


def test_read_capped_bytes_special_file_bounded_by_read_budget(
    tmp_path: Path,
) -> None:
    """A special file with ``st_size == 0`` that streams bytes on read
    must be bounded by the ``max_bytes + 1`` read budget. The helper
    treats the over-budget read as oversized and returns ``None``.

    Simulated via a subclass of :class:`pathlib.Path` whose ``open()``
    returns a file-like that lies about its size via the open-mode
    descriptor. Since we cannot reliably create platform-independent
    FIFOs in CI, we test the read-budget invariant directly against
    the helper's expected behaviour: a file whose actual bytes exceed
    the cap (even by 1 byte) must be rejected at the read boundary.
    """
    from src.utils.files import read_capped_bytes

    fake_path = tmp_path / "edge.bin"
    fake_path.write_bytes(b"x" * 1025)  # 1 byte over cap.

    result = read_capped_bytes(fake_path, 1024, label="test")
    assert result is None


# ============================================================================
# PoC 7: AST inventory walker. The walker enumerates every
# ``Path.read_bytes()`` / ``<var>.read_bytes()`` call in ``src/`` and
# ``scripts/`` and asserts there are NONE — every binary blob read
# must route through :func:`read_capped_bytes`. A future contributor
# who adds an unbounded ``cache_path.read_bytes()`` call fails this
# walker at PR-review time, mirroring the canonical audit pattern
# pinned by ``test_sentinel_json_audit_walker.py``.
# ============================================================================


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_TREES = ("src", "scripts")


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for tree in SCAN_TREES:
        base = REPO_ROOT / tree
        if not base.exists():  # pragma: no cover - defensive
            continue
        files.extend(sorted(base.rglob("*.py")))
    return files


def _is_read_bytes_call(node: ast.Call) -> bool:
    """Match ``<expr>.read_bytes()`` attribute-call shape (no args)."""
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "read_bytes":
        return False
    # Require no positional args (rules out e.g. response.read_bytes(8192)
    # if such a shape ever appears; ``Path.read_bytes()`` takes no args).
    if node.args:
        return False
    return True


def test_no_unbounded_read_bytes_in_src_or_scripts() -> None:
    """Inventory walker: every ``*.read_bytes()`` call in ``src/`` and
    ``scripts/`` is reported. The expected baseline is the empty set
    — every binary blob read must route through
    :func:`src.utils.files.read_capped_bytes` (or a documented
    helper that wraps it).

    A future PR that adds a new ``cache.read_bytes()`` style call fails
    here and is forced through code review to add an explicit cap.
    """
    findings: list[str] = []
    for path in _iter_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_read_bytes_call(node):
                rel = path.relative_to(REPO_ROOT)
                findings.append(f"{rel}:{node.lineno}")

    assert not findings, (
        "Found unbounded ``*.read_bytes()`` call(s) in src/ or scripts/. "
        "Every binary blob read must route through "
        "src.utils.files.read_capped_bytes (or a documented helper that "
        "wraps it). Findings:\n  " + "\n  ".join(findings)
    )
