import os
from unittest import mock
import pytest
from src.utils.env import read_secret

def test_read_secret_from_env():
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        mock_instance = MockPath.return_value
        mock_instance.resolve.return_value = mock_instance
        mock_instance.__truediv__.return_value = mock_instance
        mock_instance.exists.return_value = False

        with mock.patch.dict(os.environ, {"MY_SECRET": "env_value"}):
            with mock.patch.object(os, "getenv", side_effect=lambda k, d=None: {"MY_SECRET": "env_value"}.get(k, d) if k != "CREDENTIALS_DIRECTORY" else None):
                assert read_secret("MY_SECRET") == "env_value"

def test_read_secret_default():
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        mock_instance = MockPath.return_value
        mock_instance.resolve.return_value = mock_instance
        mock_instance.__truediv__.return_value = mock_instance
        mock_instance.exists.return_value = False

        with mock.patch.dict(os.environ, clear=True):
            assert read_secret("MISSING_SECRET", "default") == "default"
            assert read_secret("MISSING_SECRET") == ""

def test_read_secret_docker_priority(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("MY_SECRET", "env_value")

    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        docker_base_mock = mock.MagicMock()
        docker_base_resolved = mock.MagicMock()
        docker_base_mock.resolve.return_value = docker_base_resolved

        docker_secret_mock = mock.MagicMock()
        docker_secret_resolved = mock.MagicMock()

        docker_base_resolved.__truediv__.return_value = docker_secret_mock
        docker_secret_mock.resolve.return_value = docker_secret_resolved

        docker_secret_resolved.exists.return_value = True
        docker_secret_resolved.is_file.return_value = True
        docker_secret_resolved.read_text.return_value = "docker_value\n"

        def path_side_effect(arg):
            if str(arg) == "/run/secrets":
                return docker_base_mock
            return mock.MagicMock()

        MockPath.side_effect = path_side_effect

        assert read_secret("MY_SECRET") == "docker_value"

def test_read_secret_systemd_priority(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/custom/creds")
    monkeypatch.setenv("MY_SECRET", "env_value")

    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        cred_base_mock = mock.MagicMock()
        cred_base_resolved = mock.MagicMock()
        cred_base_mock.resolve.return_value = cred_base_resolved

        cred_secret_mock = mock.MagicMock()
        cred_secret_resolved = mock.MagicMock()
        cred_base_resolved.__truediv__.return_value = cred_secret_mock
        cred_secret_mock.resolve.return_value = cred_secret_resolved

        cred_secret_resolved.exists.return_value = True
        cred_secret_resolved.is_file.return_value = True
        cred_secret_resolved.read_text.return_value = "systemd_value"

        def path_side_effect(arg):
            if str(arg) == "/custom/creds":
                return cred_base_mock
            if str(arg) == "/run/secrets":
                return mock.MagicMock()
            return mock.MagicMock()

        MockPath.side_effect = path_side_effect

        assert read_secret("MY_SECRET") == "systemd_value"

def test_read_secret_path_traversal(monkeypatch):
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

        def path_side_effect(arg):
            if str(arg) == "/custom/creds":
                return cred_base_mock
            if str(arg) == "/run/secrets":
                return docker_base_mock
            return mock.MagicMock()

        MockPath.side_effect = path_side_effect

        assert read_secret("../etc/passwd") == ""
