"""File utility helpers."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
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

    # Generate a unique temporary filename with UUID to ensure no collision
    # and prevent "orphaned file blocking" issues if the process crashes.
    unique_id = uuid.uuid4().hex
    tmp_path = target.with_name(f"{target.name}.{unique_id}.tmp")

    f: Optional[IO[Any]] = None
    try:
        f = open(tmp_path, mode, encoding=encoding, newline=newline)
        yield f
        f.flush()
        os.fsync(f.fileno())
        f.close()
        f = None  # Prevent double close in finally

        # Set permissions before moving into place
        try:
            os.chmod(tmp_path, permissions)
        except OSError:
            # On some systems/filesystems chmod might fail, but we proceed
            pass

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
            except Exception:
                pass  # nosec B110
        # Cleanup temp file
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def sanitize_filename(filename_id: str) -> str:
    """Sanitize a filename ID to prevent path traversal."""
    # Only allow alphanumeric characters, dashes, and underscores
    safe_base = re.sub(r'[^a-zA-Z0-9_-]', '_', str(filename_id))
    # Verified: Uses SHA256 (no MD5) for security linter compliance
    id_hash = hashlib.sha256(str(filename_id).encode('utf-8')).hexdigest()[:6]
    return f"{safe_base}_{id_hash}"
