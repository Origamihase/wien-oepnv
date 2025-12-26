import os
import pytest
from pathlib import Path
from src.utils.files import atomic_write

def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "test.txt"
    with atomic_write(target, mode="w", encoding="utf-8") as f:
        f.write("Hello")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "Hello"

def test_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "overwrite.txt"
    target.write_text("Old", encoding="utf-8")

    with atomic_write(target, mode="w") as f:
        f.write("New")

    assert target.read_text(encoding="utf-8") == "New"

def test_atomic_write_no_overwrite(tmp_path):
    target = tmp_path / "protected.txt"
    target.write_text("Old", encoding="utf-8")

    with pytest.raises(FileExistsError):
        with atomic_write(target, overwrite=False) as f:
            f.write("New")

    assert target.read_text(encoding="utf-8") == "Old"

def test_atomic_write_cleanup_on_error(tmp_path):
    target = tmp_path / "fail.txt"

    with pytest.raises(RuntimeError):
        with atomic_write(target) as f:
            f.write("Start")
            raise RuntimeError("Boom")

    assert not target.exists()
    # Check that no temp files are left
    assert len(list(tmp_path.glob("fail.txt.*.tmp"))) == 0

def test_atomic_write_binary(tmp_path):
    target = tmp_path / "binary.bin"
    data = b"\x00\x01\x02"

    with atomic_write(target, mode="wb") as f:
        f.write(data)

    assert target.read_bytes() == data

def test_atomic_write_permissions(tmp_path):
    if os.name == 'nt':
        pytest.skip("Permissions not fully supported on Windows")

    target = tmp_path / "perms.txt"

    with atomic_write(target, permissions=0o600) as f:
        f.write("Secret")

    assert target.exists()
    mode = target.stat().st_mode & 0o777
    # Note: On some systems, mkstemp creates 0o600 by default anyway.
    # But we check if it respects our request (though chmod failure is ignored in impl).
    # We at least ensure it's not world readable if we asked for 0o600.
    assert mode == 0o600

    target2 = tmp_path / "public.txt"
    with atomic_write(target2, permissions=0o644) as f:
        f.write("Public")

    mode2 = target2.stat().st_mode & 0o777
    assert mode2 == 0o644
