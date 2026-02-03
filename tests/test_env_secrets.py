import os
from unittest import mock
import pytest
from src.utils.env import read_secret

def test_read_secret_from_env():
    # We use MagicMock for Path so / operator works
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        # Configure Path to return non-existing files by default
        MockPath.return_value.exists.return_value = False
        MockPath.return_value.__truediv__.return_value.exists.return_value = False

        with mock.patch.dict(os.environ, {"MY_SECRET": "env_value"}):
             # Ensure CREDENTIALS_DIRECTORY is unset
            with mock.patch.object(os, "getenv", side_effect=lambda k, d=None: {"MY_SECRET": "env_value"}.get(k, d) if k != "CREDENTIALS_DIRECTORY" else None):
                assert read_secret("MY_SECRET") == "env_value"

def test_read_secret_default():
    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        MockPath.return_value.exists.return_value = False
        MockPath.return_value.__truediv__.return_value.exists.return_value = False

        with mock.patch.dict(os.environ, clear=True):
            assert read_secret("MISSING_SECRET", "default") == "default"
            assert read_secret("MISSING_SECRET") == ""

def test_read_secret_docker_priority(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("MY_SECRET", "env_value")

    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        # Mock /run/secrets/MY_SECRET exists

        docker_root = mock.MagicMock()
        docker_file = mock.MagicMock()
        docker_file.exists.return_value = True
        docker_file.is_file.return_value = True
        docker_file.read_text.return_value = "docker_value\n"
        docker_root.__truediv__.return_value = docker_file

        def path_side_effect(arg):
            if str(arg) == "/run/secrets":
                return docker_root
            # Return a mock that doesn't exist for others
            m = mock.MagicMock()
            m.exists.return_value = False
            m.__truediv__.return_value.exists.return_value = False
            return m

        MockPath.side_effect = path_side_effect

        assert read_secret("MY_SECRET") == "docker_value"

def test_read_secret_systemd_priority(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/custom/creds")
    monkeypatch.setenv("MY_SECRET", "env_value")

    with mock.patch("src.utils.env.Path", new_callable=mock.MagicMock) as MockPath:
        # Mock /custom/creds/MY_SECRET
        cred_root = mock.MagicMock()
        cred_file = mock.MagicMock()
        cred_file.exists.return_value = True
        cred_file.is_file.return_value = True
        cred_file.read_text.return_value = "systemd_value"
        cred_root.__truediv__.return_value = cred_file

        # Mock /run/secrets/MY_SECRET
        docker_root = mock.MagicMock()
        docker_file = mock.MagicMock()
        docker_file.exists.return_value = True
        docker_file.is_file.return_value = True
        docker_file.read_text.return_value = "docker_value"
        docker_root.__truediv__.return_value = docker_file

        def path_side_effect(arg):
            if str(arg) == "/custom/creds":
                return cred_root
            if str(arg) == "/run/secrets":
                return docker_root
            m = mock.MagicMock()
            m.exists.return_value = False
            m.__truediv__.return_value.exists.return_value = False
            return m

        MockPath.side_effect = path_side_effect

        assert read_secret("MY_SECRET") == "systemd_value"
