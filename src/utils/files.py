"""File utility helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from collections.abc import Iterator

# Default per-loader byte cap for on-disk JSON files. Sized at ~100x the
# largest legitimately-written stations.json (~175 KiB) and polygon
# (~146 KiB) shapes, comfortably below any cron runner's 1 GiB cgroup
# limit. Callers requiring a different ceiling (e.g. a multi-MiB GTFS
# manifest) pass an explicit ``max_bytes``; the default keeps the
# canonical defence available without per-site bikeshedding.
DEFAULT_MAX_JSON_FILE_BYTES = 50 * 1024 * 1024

# Canonical default cap for non-JSON text payloads (CSV, .env, log files)
# read into memory in one shot. Sized identically to the JSON cap so the
# two helpers share the same threat-model bound. Callers requiring a
# tighter ceiling (small mapping CSV, single-line secret) pass an explicit
# ``max_bytes``.
DEFAULT_MAX_TEXT_FILE_BYTES = 50 * 1024 * 1024

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


@contextmanager
def atomic_write(
    path: str | Path,
    mode: str = "w",
    encoding: str | None = "utf-8",
    permissions: int = 0o644,
    newline: str | None = None,
    overwrite: bool = True,
) -> Iterator[IO[Any]]:
    """Safe atomic file write using a temporary file.

    Args:
        path: Target file path.
        mode: Open mode ('w' for text, 'wb' for binary).
        encoding: Text encoding (default: 'utf-8'). Ignored if binary mode.
        permissions: File permissions (default: 0o644 for public readable).
                     Use 0o600 for secrets/internal caches.
        newline: Newline control (passed to open).
        overwrite: If False, raises FileExistsError if target exists.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite and target.exists():
        raise FileExistsError(f"File {target} already exists")

    text_mode = "b" not in mode
    if not text_mode:
        encoding = None
        newline = None

    # Generate a unique temporary filename with cryptographically secure random token to ensure no collision
    # and prevent "orphaned file blocking" issues if the process crashes.
    unique_id = secrets.token_hex(16)
    tmp_path = target.with_name(f"{target.name}.{unique_id}.tmp")

    f: IO[Any] | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL
        if "a" in mode:
            flags |= os.O_APPEND
        if "+" in mode:
            flags |= os.O_RDWR
        elif "r" in mode:
            flags |= os.O_RDONLY
        else:
            flags |= os.O_WRONLY

        fd = os.open(tmp_path, flags, 0o600)
        # Security: Immediately enforce restrictive permissions bypassing umask
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass

        f = open(fd, mode, encoding=encoding, newline=newline)
        yield f
        f.flush()
        os.fsync(f.fileno())

        # Set permissions before moving into place and closing
        try:
            os.fchmod(fd, permissions)
        except OSError:
            pass

        f.close()
        f = None  # Prevent double close in finally

        if overwrite:
            os.replace(tmp_path, target)
        else:
            # Security: Use os.link to prevent TOCTOU race condition
            # If target was created between our initial check and now, os.link will fail.
            try:
                os.link(tmp_path, target)
                # Hard link successful, now remove the temp file
                os.unlink(tmp_path)
            except FileExistsError as exc:
                raise FileExistsError(f"File {target} already exists") from exc

    except Exception:
        # Close if still open (e.g. exception during yield)
        if f is not None:
            try:
                f.close()
            except Exception as close_exc:
                import logging
                logging.getLogger(__name__).warning("Failed to close temporary file", exc_info=close_exc)
        # Cleanup temp file
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as unlink_exc:
                import logging
                logging.getLogger(__name__).warning("Failed to remove temporary file", exc_info=unlink_exc)
        raise


def safe_path_join(base: str | Path, *paths: str | Path) -> Path:
    """Safely join paths, ensuring the result is within the base directory."""
    base_abs = os.path.abspath(base)

    # Check for direct path traversal attempts in the inputs
    for p in paths:
        if '..' in str(p):
            raise ValueError(f"Path traversal detected in '{p}'")

    # os.path.join with an absolute path arg resets the path.
    # We must prevent absolute path bypass.
    for p in paths:
        if os.path.isabs(p):
            raise ValueError(f"Absolute path bypass detected in '{p}'")

    final_path = os.path.abspath(os.path.join(base_abs, *paths))

    # Ensure base_abs has a trailing separator for exact containment check
    # so that "/var/lib/cache_dir" doesn't mistakenly contain "/var/lib/cache_dir_evil"
    base_check = base_abs if base_abs.endswith(os.sep) else base_abs + os.sep

    if not final_path.startswith(base_check) and final_path != base_abs:
        raise ValueError(f"Path escape detected: {final_path} is outside of {base_abs}")

    return Path(final_path)


def sanitize_filename(filename_id: str) -> str:
    """Sanitize a filename ID to prevent path traversal."""
    # Only allow alphanumeric characters, dashes, and underscores
    safe_base = re.sub(r'[^a-zA-Z0-9_-]', '_', str(filename_id))
    # Verified: Uses SHA256 (no MD5) for security linter compliance
    id_hash = hashlib.sha256(str(filename_id).encode('utf-8')).hexdigest()[:6]
    return f"{safe_base}_{id_hash}"


def get_file_hash(filepath: str | Path, chunk_size: int = 4096) -> str:
    """Calculate the SHA256 hash of a file using chunked reading to minimize memory footprint."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_capped_json(
    path: Path,
    max_bytes: int = DEFAULT_MAX_JSON_FILE_BYTES,
    *,
    label: str = "JSON",
    logger: logging.Logger | None = None,
) -> object | None:
    """Read JSON from *path*, returning ``None`` if missing/invalid/oversized.

    Combines the canonical size-bomb defence with the depth-bomb catch tuple
    ``(OSError, json.JSONDecodeError, RecursionError)``. The byte-size cap is
    enforced via ``os.fstat`` on the *open file descriptor* AND a defensive
    ``read(max_bytes + 1)`` budget — closing the TOCTOU window between
    ``Path.stat`` and ``Path.open`` that lets an attacker swap the inode
    (atomic ``os.replace`` of a symlink, parallel writer's ``atomic_write``
    rename) under the loader's feet.

    Threat model: a planted-huge JSON file (compromised CI runner / partial
    flush + power loss / corrupted previous run / parallel orchestrator
    process performing an atomic state swap mid-read) buffered into memory
    via ``json.load(handle)`` allocates O(file_size) bytes plus a multiplier
    of object overhead, exhausts the runner's cgroup memory limit, and
    propagates ``MemoryError`` (a ``BaseException`` subclass that is NOT
    caught by ``except (OSError, json.JSONDecodeError, RecursionError)``)
    past the loader to crash the cron pipeline.

    TOCTOU shape closed: pre-fix ``Path.stat()`` and ``Path.open()`` resolve
    the path AND follow symlinks INDEPENDENTLY. An attacker who can swap the
    inode at *path* between those two syscalls (``os.replace`` is atomic at
    the directory-entry level) bypasses the cap: stat sees the small target,
    open opens the swapped huge target, and ``json.load`` buffers the huge
    file. Post-fix ``os.fstat(handle.fileno())`` reports the size of the
    *opened* inode, immune to subsequent symlink swaps; the additional
    ``handle.read(max_bytes + 1)`` cap defends against special files
    (FIFOs, ``/dev/zero``, character devices) that report ``st_size == 0``
    but yield unbounded bytes on read.
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    # Security: emit a fingerprint of the path rather than the path
    # itself. ``path`` is a caller-controlled value reachable from
    # ``read_secret(name=...)`` in ``src/utils/env.py``; CodeQL's
    # ``py/clear-text-logging-sensitive-data`` taint analysis marks
    # the entire dataflow as secret-bearing because the helper is
    # transitively called with credential-coded parameter names.
    # ``sanitize_log_arg(str(path))`` is NOT recognised as a barrier
    # across the function boundary. Logging a one-way hash of the path
    # bytes (a) keeps an operator-correlatable fingerprint without
    # exposing the path string, (b) is recognised as a CodeQL barrier
    # via ``hashlib`` taint sinks, and (c) avoids leaking
    # Trojan-Source / control-character / ANSI-escape primitives a
    # hostile path name might carry. Operators rerun the hash on a
    # candidate path locally to verify identity.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes into
            # ``json.loads``.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            payload: object = json.loads(raw)
            return payload
    except (OSError, json.JSONDecodeError, RecursionError, UnicodeDecodeError):
        return None


def read_capped_text(
    path: Path,
    max_bytes: int = DEFAULT_MAX_TEXT_FILE_BYTES,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    label: str = "text",
    logger: logging.Logger | None = None,
) -> str | None:
    """Read text from *path*, returning ``None`` if missing/oversized/invalid.

    Mirrors :func:`read_capped_json` for non-JSON text payloads (CSV,
    log files, .env files) where ``Path.read_text()`` would buffer the
    entire file before processing — a ``BaseException``-rooted
    ``MemoryError`` that propagates past surrounding ``except OSError``
    handlers and crashes the cron pipeline.

    Threat model: identical to :func:`read_capped_json`. A planted-huge
    file at *path* (compromised CI runner / partial flush + power loss /
    corrupted previous run / parallel orchestrator process performing an
    atomic state swap mid-read) buffered into memory via
    ``Path.read_text()`` allocates O(file_size) bytes and raises
    ``MemoryError``. The fix shape mirrors ``read_capped_json``: open
    first, fstat the open file descriptor (TOCTOU-safe), then bound the
    read at ``max_bytes + 1`` (special-file safe).

    The optional *errors* parameter mirrors :func:`bytes.decode` /
    :func:`Path.read_text` — pass ``errors="ignore"`` for callers that
    previously consumed lossy text (e.g. the secret scanner walking
    every tracked file in the repo, where non-UTF-8 fragments must not
    drop the whole file).
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    # Security: see ``read_capped_json`` for the rationale of fingerprinting
    # ``path`` instead of interpolating it. Same CodeQL clear-text-logging
    # surface, same Trojan-Source / control-character defanging surface,
    # same hashlib-based barrier shape.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        # Open first so the size check is on the actual inode that
        # ``read()`` will consume — closes the stat/open TOCTOU.
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] is too large (> %d bytes); treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            # Defense in depth: bound the read at ``max_bytes + 1`` so a
            # special file with ``st_size == 0`` (FIFO, ``/dev/zero``,
            # character device) cannot stream unbounded bytes.
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning(
                    "%s file [path-sha256=%s] exceeded %d bytes during read; treating as missing.",
                    label, path_fingerprint, max_bytes,
                )
                return None
            return raw.decode(encoding, errors=errors)
    except (OSError, UnicodeDecodeError):
        return None


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
        if len(info.filename) > max_filename_length:
            raise ValueError(
                f"{label} archive entry filename length exceeds threshold: "
                f"{len(info.filename)} > {max_filename_length} bytes"
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
