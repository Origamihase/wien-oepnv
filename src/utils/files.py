"""File utility helpers."""
from __future__ import annotations

import os
import tempfile
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

    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f"{target.name}.",
        suffix=".tmp",
        text=text_mode,
    )

    f: Optional[IO[Any]] = None
    try:
        f = os.fdopen(fd, mode, encoding=encoding, newline=newline)
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
        os.replace(tmp_path, target)

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
