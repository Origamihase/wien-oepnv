"""Paths and file names: safe joining, safe file names, content hash.

Moved out of :mod:`src.utils.files` (700-line rule); that module
re-exports every name.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

__all__ = ["get_file_hash", "safe_path_join", "sanitize_filename"]



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
