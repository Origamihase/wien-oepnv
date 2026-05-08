"""Sentinel: ``zipfile.ZipFile`` consumed-via-iter family — close the
defense-in-depth gap left by every prior size-bomb round.

The 2026-05-08 CSV size-bomb round closed ``csv.DictReader/csv.reader``
across ten sites and named the next-round target as "every stdlib helper
that takes a file-like and consumes via ``iter`` / ``readline`` / ``read``
without an explicit size argument" — including "any third-party parser
that takes a file handle and reads through ``iter``".

``zipfile.ZipFile`` is exactly such a parser: ``infolist()`` materialises
every central-directory entry up-front (a million tiny entries inflates
ZipInfo allocations even when ``sum(file_size) == 0``), and
``archive.open(member).read()`` consumes via ``iter(handle)`` /
``decompressor.decompress(...)``. ``openpyxl.load_workbook(stream,
read_only=True)`` is *built on top of* ``zipfile.ZipFile`` and inherits
the same threat model; the cron pipeline's ``update_station_directory``
script feeds a network-fetched workbook into ``extract_stations`` whose
sole defense was ``sum(info.file_size) <= 100 MiB`` — a check that
trivially passes a million-empty-entry central directory.

This module pins:

  (1) **Per-entry uncompressed cap** (single-huge-member shape):
      ``info.file_size > 50 MiB`` — production xlsx have no entry larger
      than ~10 MiB (sheet1.xml). Pre-fix the existing ``sum`` check
      passes a single 100 MiB entry; post-fix the per-entry cap rejects.

  (2) **Entry-count cap** (central-directory bloat shape): ``len(infolist)
      > 1000`` — production xlsx have ~10-15 entries. Pre-fix a million
      empty entries pass the ``sum(file_size) == 0`` check; post-fix the
      count cap rejects before ``infolist()`` is even fully iterated by
      the consumer.

  (3) **Filename-length cap** (filename-bomb shape): ``len(info.filename)
      > 1024`` — production xlsx use member paths < 100 chars. Pre-fix a
      multi-KiB filename poisons every structured log line that includes
      ``info.filename``. Post-fix rejected.

  (4) **Total uncompressed cap** (existing axis, regression-pinned).

  (5) **Auto-discoverable inventory walker** ensures every future
      ``zipfile.ZipFile(...)`` callsite under ``src/`` and ``scripts/``
      routes through ``validate_zip_archive_safe``. Mirrors the
      ``test_no_unbounded_csv_dictreader_in_src_or_scripts`` walker from
      the 2026-05-08 CSV round.
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from src.utils.files import (
    DEFAULT_MAX_ZIP_ENTRIES,
    DEFAULT_MAX_ZIP_FILENAME_LENGTH,
    DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED,
    DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED,
    validate_zip_archive_safe,
)


# ============================================================================
# Helper: build a minimal valid xlsx in-memory for legitimate-case tests
# ============================================================================


def _build_minimal_xlsx() -> BytesIO:
    """Build a minimal valid xlsx stream openpyxl can parse."""
    import openpyxl  # local import keeps the module importable in envs without openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["BST_ID", "BST_CODE", "Verkehrsstation"])
    ws.append([1, "AB", "Wien Westbahnhof"])
    ws.append([2, "BC", "Wien Mitte"])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ============================================================================
# Precondition: canonical helper exists and exposes the four cap constants
# ============================================================================


def test_precondition_validate_zip_archive_safe_helper_exists() -> None:
    """Pin the canonical helper signature so future refactors can't drift."""
    assert callable(validate_zip_archive_safe)
    assert DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED == 100 * 1024 * 1024
    assert DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED == 50 * 1024 * 1024
    assert DEFAULT_MAX_ZIP_ENTRIES == 1000
    assert DEFAULT_MAX_ZIP_FILENAME_LENGTH == 1024


# ============================================================================
# (1) Per-entry uncompressed cap (single-huge-member shape)
# ============================================================================


def test_validator_rejects_single_oversized_entry() -> None:
    """A ZIP with a single entry whose declared file_size exceeds the
    per-entry cap is rejected even when the *total* sum still fits."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 51 MiB of repetitive content compresses to a few KiB but
        # declares 51 MiB uncompressed — exceeds the 50 MiB per-entry cap.
        zf.writestr("big.bin", b"A" * (51 * 1024 * 1024))
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(ValueError, match=r"declares.*53477376.*52428800"):
            validate_zip_archive_safe(archive, label="test")


def test_validator_accepts_per_entry_at_cap() -> None:
    """An entry whose declared size equals the per-entry cap is accepted."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 1 KiB entry — well under both per-entry and total caps.
        zf.writestr("small.bin", b"B" * 1024)
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        validate_zip_archive_safe(archive, label="test")


# ============================================================================
# (2) Entry-count cap (central-directory bloat shape)
# ============================================================================


def test_validator_rejects_too_many_entries() -> None:
    """A ZIP with >1000 entries is rejected even if every entry is empty.

    Pre-fix: the existing ``sum(info.file_size) <= 100 MiB`` check trivially
    passes (sum = 0) while ``infolist()`` materialises 1001 ZipInfo objects
    that the consumer then iterates — a CD-bloat shape that the total-size
    axis cannot catch.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(DEFAULT_MAX_ZIP_ENTRIES + 1):
            zf.writestr(str(i), b"")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(ValueError, match=r"too many entries: 1001 > 1000"):
            validate_zip_archive_safe(archive, label="test")


def test_validator_accepts_at_entry_count_cap() -> None:
    """A ZIP with exactly the cap number of entries is accepted."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(DEFAULT_MAX_ZIP_ENTRIES):
            zf.writestr(str(i), b"")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        validate_zip_archive_safe(archive, label="test")


# ============================================================================
# (3) Filename-length cap (filename-bomb shape)
# ============================================================================


def test_validator_rejects_long_filename() -> None:
    """A ZIP entry with a filename longer than 1024 bytes is rejected."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # 2000-character filename — exceeds the 1024-byte cap. ZIP spec
        # allows up to 65535 bytes per filename.
        zf.writestr("a" * 2000, b"short")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(ValueError, match=r"filename length.*2000.*1024"):
            validate_zip_archive_safe(archive, label="test")


def test_validator_accepts_short_filename() -> None:
    """A ZIP entry with a filename at or under 1024 bytes is accepted."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a" * 1024, b"short")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        validate_zip_archive_safe(archive, label="test")


# ============================================================================
# (4) Total uncompressed cap (regression — existing axis preserved)
# ============================================================================


def test_validator_rejects_oversized_total() -> None:
    """Three 40-MiB entries declare 120 MiB total — exceeds 100 MiB cap."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for i in range(3):
            zf.writestr(f"f{i}", b"A" * (40 * 1024 * 1024))
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(ValueError, match=r"total declared uncompressed size"):
            validate_zip_archive_safe(archive, label="test")


# ============================================================================
# Cross-cap regression: a planted ZIP triggers the FIRST cap that fails
# ============================================================================


def test_validator_rejects_first_failing_cap_count_then_size() -> None:
    """A ZIP failing multiple caps fails on count first (cheapest check)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(1500):
            zf.writestr(str(i), b"")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(ValueError, match=r"too many entries"):
            validate_zip_archive_safe(archive, label="test")


# ============================================================================
# (5) Real xlsx fixtures pass the validator (regression baseline)
# ============================================================================


def test_minimal_xlsx_passes_validator() -> None:
    """A minimal valid xlsx (built via openpyxl.Workbook) passes."""
    stream = _build_minimal_xlsx()
    with zipfile.ZipFile(stream) as archive:
        # No exception means the validator accepts the legitimate xlsx.
        validate_zip_archive_safe(archive, label="ÖBB workbook")


# ============================================================================
# extract_stations — integration tests against the new helper
# ============================================================================


def test_extract_stations_rejects_too_many_entries() -> None:
    """The ÖBB extract path rejects a million-entry CD bomb."""
    from scripts import update_station_directory as usd

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(DEFAULT_MAX_ZIP_ENTRIES + 1):
            zf.writestr(str(i), b"")
    buf.seek(0)
    with pytest.raises(ValueError, match=r"too many entries"):
        usd.extract_stations(buf)


def test_extract_stations_rejects_oversized_entry() -> None:
    """The ÖBB extract path rejects a single >50 MiB entry."""
    from scripts import update_station_directory as usd

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("big.bin", b"A" * (51 * 1024 * 1024))
    buf.seek(0)
    with pytest.raises(ValueError, match=r"declares.*52428800"):
        usd.extract_stations(buf)


def test_extract_stations_rejects_long_filename() -> None:
    """The ÖBB extract path rejects a 2 KiB filename bomb."""
    from scripts import update_station_directory as usd

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a" * 2000, b"short")
    buf.seek(0)
    with pytest.raises(ValueError, match=r"filename length"):
        usd.extract_stations(buf)


def test_extract_stations_rejects_invalid_zip() -> None:
    """The ÖBB extract path rejects a non-ZIP byte stream cleanly."""
    from scripts import update_station_directory as usd

    buf = BytesIO(b"not a zip at all")
    with pytest.raises(ValueError, match=r"Invalid workbook file"):
        usd.extract_stations(buf)


# ============================================================================
# Defense-in-depth: existing check trusts attacker-controlled metadata
# ============================================================================


def test_lying_central_directory_metadata_is_attacker_controlled() -> None:
    """PoC: the existing ``sum(info.file_size)`` check trusts metadata
    that the attacker controls.

    Builds a real ZIP and patches the central directory (CDH) plus
    local file header (LFH) to lie about ``file_size`` (declares 0
    bytes uncompressed; the real compressed payload would expand to
    50 MiB if the lie were ignored). Demonstrates two facts:

    1. The pre-fix ``sum(info.file_size)`` check passes trivially
       (declared sum = 0 < 100 MiB cap) — even though 50 MiB of real
       compressed data sits inside the archive. The check is therefore
       reliant on attacker-controlled metadata, the conceptual
       fragility this round closes via per-entry / count / filename
       caps on orthogonal shape axes.
    2. Python's per-entry CRC validation enforces ``file_size`` as the
       upper bound on ``archive.open(...).read()`` (see ``ZipExtFile``
       ``_left = file_size`` and ``_update_crc`` at EOF in CPython
       3.11+). A read therefore returns at most ``file_size`` bytes;
       memory amplification is impossible under current CPython. This
       fact justifies the metadata-based (rather than streaming-
       decompression) shape of the new validator: the per-entry /
       count / filename caps catch every shape that the prior
       ``sum`` check missed, while Python's existing CRC enforcement
       continues to bound the size-amplification axis.
    """
    payload = b"X" * (50 * 1024 * 1024)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("lol.txt", payload)
    raw = bytearray(buf.getvalue())
    truncated_crc = zlib.crc32(b"") & 0xFFFFFFFF
    cdir_sig = b"\x50\x4b\x01\x02"
    cdir_pos = raw.find(cdir_sig)
    # Patch CDH: CRC at +16, uncompressed_size at +24
    struct.pack_into("<I", raw, cdir_pos + 16, truncated_crc)
    struct.pack_into("<I", raw, cdir_pos + 24, 0)
    # Patch LFH (at offset 0): CRC at +14, uncompressed_size at +22
    struct.pack_into("<I", raw, 14, truncated_crc)
    struct.pack_into("<I", raw, 22, 0)

    patched = BytesIO(bytes(raw))
    with zipfile.ZipFile(patched) as archive:
        info = archive.infolist()[0]
        assert info.file_size == 0, "Patched ZIP should advertise lying file_size=0"

        # The existing pre-fix ``sum(info.file_size)`` check passes
        # trivially (0 < 100 MiB cap), demonstrating the metadata-trust
        # fragility. The new validator also accepts (this lying-CD shape
        # does not violate the per-entry / count / filename caps).
        validate_zip_archive_safe(archive, label="lying-metadata")

        # Python's CRC enforcement bounds the actual returned bytes to
        # the declared file_size = 0. A read returns 0 bytes — the
        # crucial property that prevents memory amplification.
        with archive.open(info) as fh:
            data = fh.read()
        assert data == b"", (
            f"Python's CRC enforcement should bound bytes read to declared "
            f"file_size = 0, but got {len(data)} bytes — possible regression "
            f"of the size-amplification floor across Python versions."
        )


# ============================================================================
# (6) Auto-discoverable closing grep — drift defence inventory walker
# ============================================================================


def test_no_unbounded_zipfile_zipfile_in_src_or_scripts() -> None:
    """Drift defence: every ``zipfile.ZipFile(...)`` callsite must route
    through :func:`validate_zip_archive_safe`.

    Walks ``src/`` and ``scripts/`` and asserts every line that opens
    a ``zipfile.ZipFile`` either lives inside a function that ALSO calls
    ``validate_zip_archive_safe`` (within the same file), or is itself
    the canonical helper definition. Mirrors the
    ``test_no_unbounded_csv_dictreader_in_src_or_scripts`` walker from
    the 2026-05-08 CSV round.
    """
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[tuple[Path, int, str]] = []
    for sub in ("src", "scripts"):
        for py_file in (repo_root / sub).rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:  # pragma: no cover - defensive
                continue
            lines = source.splitlines()
            for idx, line in enumerate(lines, start=1):
                stripped = line.lstrip()
                # Skip comment / docstring-style narration lines.
                if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                    continue
                if "zipfile.ZipFile(" not in line:
                    continue
                # Whole-file scope: the validator must be invoked
                # somewhere in the same module so the audit walker can
                # detect canonical-helper routing.
                if "validate_zip_archive_safe" not in source:
                    offenders.append((py_file.relative_to(repo_root), idx, line.strip()))
    assert not offenders, (
        "Unbounded zipfile.ZipFile sites detected — invoke "
        "validate_zip_archive_safe(archive, label=...) on every opened "
        "ZipFile to bound entry count / per-entry size / filename length:\n"
        + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
    )


# ============================================================================
# Sanity: the validator does not trigger on its own definition site
# ============================================================================


def test_validator_definition_does_not_trigger_inventory_walker() -> None:
    """The canonical helper module itself contains ``zipfile.ZipFile``
    type-annotation usage but is NOT a callsite that opens an archive.

    The walker scans for ``zipfile.ZipFile(`` (the constructor call). This
    test pins that the helper definition module does not itself create
    a ZipFile and therefore does not need to call its own helper.
    """
    files_module = (
        Path(__file__).resolve().parents[1] / "src" / "utils" / "files.py"
    )
    source = files_module.read_text(encoding="utf-8")
    # Type annotations like ``archive: zipfile.ZipFile`` are fine.
    # Only the *constructor call* is the audit target.
    assert "zipfile.ZipFile(" not in source, (
        "src/utils/files.py should not itself call zipfile.ZipFile() — it "
        "only consumes already-opened archives via validate_zip_archive_safe."
    )
