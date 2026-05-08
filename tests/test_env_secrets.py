import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from src.utils.env import read_secret

def test_read_secret_from_env() -> None:
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        mock_instance = MockPath.return_value
        mock_instance.resolve.return_value = mock_instance
        mock_instance.__truediv__.return_value = mock_instance
        mock_instance.exists.return_value = False

        with mock.patch.dict(os.environ, {"MY_SECRET": "env_value"}):
            with mock.patch.object(
                os,
                "getenv",
                side_effect=lambda k, d=None: {"MY_SECRET": "env_value"}.get(k, d) if k != "CREDENTIALS_DIRECTORY" else None,
            ):
                assert read_secret("MY_SECRET") == "env_value"

def test_read_secret_default() -> None:
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        mock_instance = MockPath.return_value
        mock_instance.resolve.return_value = mock_instance
        mock_instance.__truediv__.return_value = mock_instance
        mock_instance.exists.return_value = False

        with mock.patch.dict(os.environ, clear=True):
            assert read_secret("MISSING_SECRET", "default") == "default"
            assert read_secret("MISSING_SECRET") == ""

def test_read_secret_docker_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Docker secrets backing store wins over the env var.

    Uses a real filesystem under tmp_path because ``read_secret`` now
    uses ``read_capped_text`` (TOCTOU-safe ``open + fstat + read``)
    which resists trivial ``read_text`` mocking.
    """
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("MY_SECRET", "env_value")

    docker_base = tmp_path / "secrets"
    docker_base.mkdir()
    (docker_base / "MY_SECRET").write_text("docker_value\n", encoding="utf-8")

    monkeypatch.setattr("src.utils.env.DOCKER_SECRETS_DIR", docker_base)

    assert read_secret("MY_SECRET") == "docker_value"


def test_read_secret_systemd_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Systemd credentials backing store wins over docker / env."""
    cred_dir = tmp_path / "creds"
    cred_dir.mkdir()
    (cred_dir / "MY_SECRET").write_text("systemd_value", encoding="utf-8")

    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
    monkeypatch.setenv("MY_SECRET", "env_value")
    # Ensure docker base is empty (no fallback hit)
    empty_docker = tmp_path / "empty_docker"
    empty_docker.mkdir()
    monkeypatch.setattr("src.utils.env.DOCKER_SECRETS_DIR", empty_docker)

    assert read_secret("MY_SECRET") == "systemd_value"

def test_read_secret_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/custom/creds")

    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        cred_base_mock = mock.MagicMock()
        cred_base_resolved = mock.MagicMock()
        cred_base_mock.resolve.return_value = cred_base_resolved

        cred_secret_mock = mock.MagicMock()
        cred_secret_resolved = mock.MagicMock()
        cred_base_resolved.__truediv__.return_value = cred_secret_mock
        cred_secret_mock.resolve.return_value = cred_secret_resolved

        # Simulate relative_to raising ValueError for Systemd
        cred_secret_resolved.relative_to.side_effect = ValueError("Traversal")

        # Mock Docker to not find anything (but also fail if traversal tried there)
        docker_base_mock = mock.MagicMock()
        docker_base_mock.resolve.return_value = docker_base_mock
        docker_base_mock.__truediv__.return_value = docker_base_mock
        docker_base_mock.exists.return_value = False
        # If traversal check happens here too (which it does), ensure it doesn't crash but falls through
        # Actually my code does try..except ValueError around relative_to, so raising ValueError is fine.
        # But if we just make exists() return False, it won't read.

        def path_side_effect(arg: Any) -> mock.MagicMock:
            if str(arg) == "/custom/creds":
                return cred_base_mock
            if str(arg) == "/run/secrets":
                return docker_base_mock
            return mock.MagicMock()

        MockPath.side_effect = path_side_effect

        assert read_secret("../etc/passwd") == ""
