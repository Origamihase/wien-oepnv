"""File utility helpers."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Iterator, Optional, Union


@contextmanager
def atomic_write(
    path: Union[str, Path],
    mode: str = "w",
    encoding: Optional[str] = "utf-8",
    permissions: int = 0o644,
    newline: Optional[str] = None,
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

    f: Optional[IO[Any]] = None
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
            except FileExistsError:
                raise FileExistsError(f"File {target} already exists")

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


def safe_path_join(base: Union[str, Path], *paths: Union[str, Path]) -> Path:
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


def get_file_hash(filepath: Union[str, Path], chunk_size: int = 4096) -> str:
    """Calculate the SHA256 hash of a file using chunked reading to minimize memory footprint."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
