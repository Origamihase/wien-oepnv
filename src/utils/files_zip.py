"""ZIP archive validation: caps against zip bombs and metadata tricks.

Holds the four default caps and :func:`validate_zip_archive_safe`. Moved
out of :mod:`src.utils.files` (700-line rule); that module re-exports
every name.
"""
from __future__ import annotations

import zipfile

__all__ = [
    "DEFAULT_MAX_ZIP_ENTRIES",
    "DEFAULT_MAX_ZIP_FILENAME_LENGTH",
    "DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED",
    "DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED",
    "validate_zip_archive_safe",
]


# Canonical defaults for the ``zipfile.ZipFile`` validator. The four caps
# below close the four orthogonal axes that the existing
# ``sum(info.file_size)`` check does NOT catch:
#   (a) total uncompressed size (existing axis, kept for API compatibility);
#   (b) per-entry uncompressed size (single huge member at exactly the
#       total cap is undesirable for memory pressure);
#   (c) entry count (millions-of-tiny-entries central-directory bombs
#       inflate ``infolist()`` ZipInfo allocations and Python dict
#       overhead even when ``sum(file_size) == 0``);
#   (d) filename length (per-entry filenames up to 65535 bytes per ZIP
#       spec — a cron pipeline's logging plumbing chokes on a multi-KiB
#       filename written into a structured log line).
# The defaults are sized at >>100x the largest legitimate xlsx shape so
# production state is never rejected: a real ÖBB ``Verzeichnis der
# Verkehrsstationen.xlsx`` has ~10-15 entries with the largest entry
# (sheet1.xml) at ~5-10 MiB. A future xlsx with 100 sheets would fit
# comfortably under the 1000-entry cap.
DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED = 50 * 1024 * 1024
DEFAULT_MAX_ZIP_ENTRIES = 1000
DEFAULT_MAX_ZIP_FILENAME_LENGTH = 1024


def validate_zip_archive_safe(
    archive: zipfile.ZipFile,
    *,
    max_total_uncompressed: int = DEFAULT_MAX_ZIP_TOTAL_UNCOMPRESSED,
    max_per_entry_uncompressed: int = DEFAULT_MAX_ZIP_PER_ENTRY_UNCOMPRESSED,
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES,
    max_filename_length: int = DEFAULT_MAX_ZIP_FILENAME_LENGTH,
    label: str = "ZIP",
) -> None:
    """Validate a :class:`zipfile.ZipFile` against zip-bomb / metadata-trust
    attacks. Raises :class:`ValueError` when any of the canonical caps is
    exceeded.

    Threat model: a compromised CDN / DNS-hijack / MITM serves a malicious
    ZIP that the cron pipeline downloads via ``fetch_content_safe`` (≤ 10
    MiB compressed). The orchestrator runs the consuming script via
    ``subprocess.run(check=True)``, so any unhandled ``MemoryError`` /
    ``zipfile.BadZipFile`` raises ``CalledProcessError`` and aborts the
    WHOLE cron pipeline. The four axes closed here are orthogonal to the
    existing ``sum(info.file_size) <= 100 MiB`` total cap and were left
    open by every prior round of the size-bomb family:

    1. **Per-entry uncompressed size** — the existing total-sum check
       passes a single 100 MiB entry; production xlsx have no entry
       larger than ~10 MiB (sheet1.xml). Capping per-entry independently
       defeats the "single huge member" shape that the total-sum check
       cannot catch.
    2. **Entry count** — a ZIP with millions of tiny entries (each
       declaring ``file_size = 0``) passes the total-sum check trivially
       but inflates ``archive.infolist()`` to a ZipInfo array whose
       Python overhead can OOM the runner before the consumer ever sees
       a row. Capping entry count at 1000 (>> 50 in any legitimate xlsx)
       defeats the central-directory bloat shape.
    3. **Filename length** — each ZIP entry name can be up to 65535 bytes
       per spec; a planted multi-KiB filename poisons every structured
       log line that includes ``info.filename`` and breaks downstream
       log parsers (and serializes-to-disk size in the cron pipeline's
       diagnostic dumps). Capping filename length at 1024 bytes (>> any
       legitimate xlsx member path) defeats the filename-bomb shape.
    4. **Total uncompressed size** — preserved as the canonical axis the
       prior fix-shape established (mirrors :func:`read_capped_json`).

    Why metadata is sufficient: Python's :mod:`zipfile` enforces
    ``info.file_size`` as the upper bound on ``archive.open(...).read()``
    via per-entry CRC validation (see ``ZipExtFile._read1`` /
    ``_update_crc`` in CPython). A lying central directory cannot
    therefore amplify memory beyond the declared value under current
    Python (3.11+) — an attacker who tries to ship a ZIP with declared
    ``file_size = 1`` but actual decompressed payload ≫ 1 byte hits
    ``BadZipFile: Bad CRC-32`` on the very first ``read()``. The
    metadata-based caps closed in this validator therefore add
    defense-in-depth on the *orthogonal* shape axes (per-entry, count,
    filename) rather than the size-amplification axis.

    Mirrors the canonical-helper pattern from :func:`read_capped_json` /
    :func:`read_capped_text` — a single point of audit + a single
    inventory-test target. New ZipFile constructor callsites that
    bypass this helper fail the
    ``test_no_unbounded_zipfile_zipfile_in_src_or_scripts`` walker at
    PR-review time.
    """
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ValueError(
            f"{label} archive has too many entries: {len(infos)} > {max_entries}"
        )
    total_declared = 0
    for info in infos:
        # Measure the encoded byte length: ``zipfile`` decodes entry names to a
        # ``str``, so ``len()`` would count code points and let a multibyte
        # (CJK/emoji) name reach ~4x the cap on disk / in a log line — the very
        # filename-bomb shape this guard defends against.
        filename_bytes = len(info.filename.encode("utf-8", "surrogatepass"))
        if filename_bytes > max_filename_length:
            raise ValueError(
                f"{label} archive entry filename length exceeds threshold: "
                f"{filename_bytes} > {max_filename_length} bytes"
            )
        if info.file_size > max_per_entry_uncompressed:
            raise ValueError(
                f"{label} archive entry declares {info.file_size} "
                f"uncompressed bytes > {max_per_entry_uncompressed}"
            )
        total_declared += info.file_size
        if total_declared > max_total_uncompressed:
            raise ValueError(
                f"{label} archive total declared uncompressed size "
                f"exceeds threshold: {total_declared} > {max_total_uncompressed} bytes"
            )
