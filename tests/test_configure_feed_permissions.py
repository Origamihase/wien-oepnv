"""Verify that the configuration wizard writes .env files with restrictive permissions."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "configure_feed.py"


def _load_configure_feed_module() -> ModuleType:
    """Load configure_feed.py as a module so its helpers can be unit-tested.

    The script supports two import shapes (``utils.X`` when the script-local
    sys.path tweak is active, ``src.utils.X`` as a fallback). Ensure the
    project root is on sys.path so the ``src.utils.X`` fallback resolves.
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "configure_feed_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions not enforced on Windows")
def test_write_env_document_uses_owner_only_permissions(tmp_path: Path) -> None:
    """The .env file may carry secrets — it must never be group/world-readable."""
    # Force a typical default umask so we'd otherwise get 0o644.
    previous_umask = os.umask(0o022)
    try:
        module = _load_configure_feed_module()
        env_path = tmp_path / ".env"

        module._write_env_document(env_path, 'VOR_ACCESS_ID="test_secret"\n')

        mode = os.stat(env_path).st_mode & 0o777
        assert mode == 0o600, (
            f"Expected 0o600 (owner-only) for .env containing secrets, got 0o{mode:o}"
        )
        assert env_path.read_text(encoding="utf-8") == 'VOR_ACCESS_ID="test_secret"\n'
    finally:
        os.umask(previous_umask)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions not enforced on Windows")
def test_write_env_document_tightens_existing_permissions(tmp_path: Path) -> None:
    """Re-writing an existing world-readable .env must drop the loose permissions."""
    previous_umask = os.umask(0o022)
    try:
        module = _load_configure_feed_module()
        env_path = tmp_path / ".env"

        # Pre-create a world-readable file (as a previous insecure write would have left it).
        env_path.write_text("OLD=value\n", encoding="utf-8")
        os.chmod(env_path, 0o644)

        module._write_env_document(env_path, 'VOR_ACCESS_ID="rotated"\n')

        mode = os.stat(env_path).st_mode & 0o777
        assert mode == 0o600
    finally:
        os.umask(previous_umask)
