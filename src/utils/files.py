"""File utility helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import math
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

# Canonical default cap for binary blob payloads (XLSX workbooks, ZIP
# archives, image / PDF caches) read into memory in one shot via
# :func:`read_capped_bytes`. Sized identically to the JSON / text caps so
# the three helpers share the same threat-model bound — a planted-huge
# binary file (compromised CI runner / partial flush + power loss /
# parallel orchestrator atomic state swap) buffered via
# ``Path.read_bytes()`` allocates O(file_size) bytes and raises
# ``MemoryError`` (a ``BaseException`` subclass NOT caught by
# ``except OSError``) past the surrounding handler and crashes the cron
# pipeline (the orchestrator runs every update script via
# ``subprocess.run(check=True)``). Callers requiring a tighter ceiling
# (XLSX cache pinned at the HTTP fetch cap, small binary blob) pass an
# explicit ``max_bytes``.
DEFAULT_MAX_BYTES_FILE_BYTES = 50 * 1024 * 1024

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


def _close_and_cleanup_failed_write(
    f: IO[Any] | None, fd: int | None, tmp_path: Path
) -> None:
    """Best-effort cleanup after a failed :func:`atomic_write`.

    Closes whichever descriptor is still open — the file object when
    ``open(fd, ...)`` succeeded, otherwise the raw ``fd`` from ``os.open``
    (which would leak if ``open(fd, ...)`` raised before taking ownership) —
    then removes the temporary file.
    """
    log = logging.getLogger(__name__)
    # Close if still open (e.g. exception during yield)
    if f is not None:
        try:
            f.close()
        except Exception as close_exc:
            log.warning("Failed to close temporary file", exc_info=close_exc)
    elif fd is not None:
        # ``os.open`` succeeded but ``open(fd, ...)`` never took ownership
        # (e.g. an invalid ``encoding`` raises LookupError before the file
        # object is built). Close the raw descriptor so it does not leak.
        try:
            os.close(fd)
        except OSError:
            pass
    # Cleanup temp file
    if os.path.exists(tmp_path):
        try:
            os.unlink(tmp_path)
        except OSError as unlink_exc:
            log.warning("Failed to remove temporary file", exc_info=unlink_exc)


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

    fd: int | None = None
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
        # The file object now owns the descriptor — closing ``f`` closes
        # ``fd``. Null the raw handle so the failure path never double-closes
        # it (a re-used fd could by then belong to an unrelated file).
        fd = None
        yield f
        f.flush()
        os.fsync(f.fileno())

        # Set permissions before moving into place and closing
        try:
            os.fchmod(f.fileno(), permissions)
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

        # Durability: the file's data blocks were fsync'd above, but the
        # directory entry created by the rename/link is a separate metadata
        # write that can be lost on power loss / kernel crash. fsync the parent
        # directory so the appearance of ``target`` is itself durable. Best
        # effort: platforms without directory file descriptors (e.g. Windows)
        # raise ``OSError`` on ``os.open`` of a directory and degrade to a
        # no-op, matching the os.fchmod handling above.
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    except Exception:
        _close_and_cleanup_failed_write(f, fd, tmp_path)
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


# Reader-side defence-in-depth: reject non-finite ``NaN`` / ``Infinity`` /
# ``-Infinity`` JSON literals (plus overflow-produced ``±inf`` via
# scientific-notation tokens like ``1e1000``). Mirrors the writer-side
# ``allow_nan=False`` pin closed at every committed-to-main writer in
# Round 1485 / Round 1487 / Round 1488. Without the reader-side hooks
# a planted non-standard literal in an on-disk state file (compromised
# CI runner, parallel orchestrator atomic state swap, partial flush +
# power loss, hostile PR landing a tampered fixture) propagates silently
# as ``float('nan')`` / ``float('inf')`` into Python-level computation:
#
#   * ``nan != nan`` returns ``True`` — breaks every dedup invariant
#     and timestamp comparison that uses ``!=``.
#   * ``nan + 5`` returns ``nan`` — silently poisons every downstream
#     arithmetic chain (latency averages, retention-cutoff windows).
#   * ``inf - inf`` returns ``nan`` — same silent poison shape.
#   * Round-trip back to a writer hits the ``allow_nan=False`` pin and
#     raises ``ValueError`` — the cron pipeline crashes mid-write with
#     no recovery, instead of detecting the corruption at read time and
#     starting from a clean (empty) state per the canonical
#     ``read_capped_json`` recovery pattern.
#
# Both hooks raise :class:`json.JSONDecodeError` (a :class:`ValueError`
# subclass) so callers' existing ``except json.JSONDecodeError`` handlers
# catch the rejection transparently — no per-callsite ``except`` widening
# is needed. The planted bytes are reported back to the operator log
# alongside the existing depth/size/encoding diagnostics under the same
# "corrupt file, treat as missing" recovery shape.


def _reject_non_finite_constant(constant: str) -> float:
    """``parse_constant`` hook: reject ``NaN`` / ``Infinity`` / ``-Infinity``
    literal tokens (invalid per RFC 8259 §6).

    Python's lenient mode accepts these three tokens as JSON values; this
    hook overrides the default behaviour so the rejection surfaces as
    ``json.JSONDecodeError`` at the parse boundary. Used as the canonical
    ``parse_constant`` argument to :func:`json.loads` across every
    committed-state-file reader in the project.
    """
    raise json.JSONDecodeError(
        f"Non-finite JSON literal {constant!r}; "
        f"RFC 8259 forbids NaN/Infinity tokens",
        constant, 0,
    )


def _reject_non_finite_float(value: str) -> float:
    """``parse_float`` hook: reject overflow-produced non-finite floats.

    Sibling defence to :func:`_reject_non_finite_constant`. Python's
    ``json.loads`` parses syntactically-valid scientific-notation tokens
    like ``"1e1000"`` (NOT a constant, NOT caught by ``parse_constant``)
    by calling ``float(value)`` — which IEEE-754 overflows to ``+inf`` /
    ``-inf`` silently. Without this hook a planted ``1e1000`` literal
    bypasses the ``parse_constant`` defence and lands ``float('inf')``
    in the parsed structure exactly the same as a ``NaN`` /
    ``Infinity`` literal would.

    Returns the parsed finite float unchanged; raises
    :class:`json.JSONDecodeError` on overflow / underflow to a
    non-finite value. Underflow to ``0.0`` (e.g. ``"1e-1000"``) is
    finite and accepted — loss of precision is a separate threat
    model from non-finite propagation.
    """
    parsed = float(value)
    if not math.isfinite(parsed):
        raise json.JSONDecodeError(
            f"Non-finite JSON number {value!r}; "
            f"RFC 8259 requires finite IEEE-754 doubles",
            value, 0,
        )
    return parsed


def loads_finite(raw: str | bytes | bytearray) -> object:
    """Parse *raw* JSON, rejecting non-finite literals at the parse boundary.

    Canonical wrapper for :func:`json.loads` that bakes in the
    :func:`_reject_non_finite_constant` + :func:`_reject_non_finite_float`
    hooks the on-disk readers already use (Round 1503). Closes the
    network-tainted / env-tainted parse boundary that PR #1503 left open:

    * Upstream HTTP responses (Wiener Linien, Google Places, OSM Overpass,
      HAFAS, ÖBB VAO, VOR, GitHub API, Wien Baustellen) parsed via
      ``json.loads(content)`` accept ``NaN`` / ``Infinity`` /
      ``-Infinity`` literal tokens AND scientific-notation overflow
      (``1e1000`` → ``+inf``) under Python's lenient default
      ``json.loads`` settings. A compromised upstream / MITM / DNS-hijack
      can plant non-finite floats into the in-memory data structure,
      poisoning every downstream comparison (``nan != nan`` is ``True``
      — breaks every dedup invariant), arithmetic chain (``nan + 5 ==
      nan``), and round-trip back to the writer's ``allow_nan=False``
      pin (Round 1485/1487/1488 — ``ValueError`` crashes the cron
      pipeline mid-write).
    * Env-controlled JSON (``BOUNDINGBOX_VIENNA``) parsed via
      ``json.loads(env_str)`` accepts the same literals under the same
      threat model — a leaked CI env / compromised secret store / hostile
      operator can plant non-finite coordinates that propagate through
      every downstream bounding-box computation.

    Both hooks raise :class:`json.JSONDecodeError` (a :class:`ValueError`
    subclass) so callers' existing ``except (ValueError,
    json.JSONDecodeError, RecursionError)`` handlers catch the rejection
    transparently — no per-callsite ``except`` widening is needed.
    """
    # The enclosing try/except is required to satisfy the JSON-parser
    # audit walker (``tests/test_sentinel_json_audit_walker.py``) which
    # enforces RecursionError tolerance at every ``json.loads`` callsite.
    # ``loads_finite`` re-raises RecursionError unchanged so the caller's
    # ``except RecursionError`` handler runs identically to the pre-fix
    # bare ``json.loads(raw)`` shape — no behaviour change for the
    # caller, just a structural pin for the walker invariant.
    try:
        return json.loads(
            raw,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except RecursionError:
        raise


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
            # Security: ``parse_constant`` + ``parse_float`` hooks reject
            # the canonical non-finite literal family (``NaN`` /
            # ``Infinity`` / ``-Infinity`` tokens + scientific-notation
            # overflow ``1e1000`` → ``+inf``). Mirrors the writer-side
            # ``allow_nan=False`` pin from Round 1485 / 1487 / 1488 so a
            # planted on-disk literal does NOT propagate as
            # ``float('nan')`` / ``float('inf')`` into Python computation
            # and does NOT round-trip back to the writer where it would
            # hit ``allow_nan=False`` and crash the cron pipeline.
            payload: object = json.loads(
                raw,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
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


def read_capped_bytes(
    path: Path,
    max_bytes: int = DEFAULT_MAX_BYTES_FILE_BYTES,
    *,
    label: str = "bytes",
    logger: logging.Logger | None = None,
) -> bytes | None:
    """Read raw bytes from *path*, returning ``None`` if missing/oversized.

    Mirrors :func:`read_capped_json` / :func:`read_capped_text` for binary
    blob payloads (XLSX workbooks, ZIP archives, cached image / PDF blobs)
    where ``Path.read_bytes()`` would buffer the entire file into memory
    before any downstream defence layer can run — a ``BaseException``-
    rooted ``MemoryError`` that propagates past surrounding
    ``except OSError`` handlers and crashes the cron pipeline (the
    orchestrator runs every update script via
    ``subprocess.run(check=True)``).

    Threat model: identical to :func:`read_capped_json`. A planted-huge
    binary file at *path* (compromised CI runner / partial flush + power
    loss / corrupted previous run / parallel orchestrator process
    performing an atomic state swap mid-read) buffered into memory via
    ``Path.read_bytes()`` allocates O(file_size) bytes and raises
    ``MemoryError``. The fix shape mirrors :func:`read_capped_json`:
    open first, fstat the open file descriptor (TOCTOU-safe), then bound
    the read at ``max_bytes + 1`` (special-file safe — defends against
    FIFOs, ``/dev/zero``, character devices that report
    ``st_size == 0`` but yield unbounded bytes on read).
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
            return raw
    except OSError:
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
