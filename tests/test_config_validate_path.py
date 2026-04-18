import pytest
from pathlib import Path
from src.feed.config import validate_path, InvalidPathError, REPO_ROOT

def test_validate_path_foreign_cwd(tmp_path, monkeypatch):
    # Change cwd to a temporary directory outside the repo root
    monkeypatch.chdir(tmp_path)

    # Create a dummy "docs" folder in the tmp_path
    (tmp_path / "docs").mkdir()

    foreign_path = Path("docs/test.txt")

    # Disable pytest context to simulate real-world usage where CWD isn't allowed
    monkeypatch.delenv("PYTEST_CURRENT_TEST")

    # This should fail because CWD is no longer an allowed base.
    # The path resolves to /tmp/.../docs/test.txt, which is not inside REPO_ROOT
    with pytest.raises(InvalidPathError):
        validate_path(foreign_path, "TEST_PATH")

def test_validate_path_repo_root():
    # A path inside the REPO_ROOT's allowed dirs should succeed
    repo_docs = REPO_ROOT / "docs" / "feed.xml"
    resolved = validate_path(repo_docs, "TEST_PATH")
    assert resolved == repo_docs.resolve()
