from __future__ import annotations

from pathlib import Path

import os

import pytest

from src.utils import env as env_utils


def test_load_env_file_sets_missing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "secrets.env"
    env_file.write_text("VOR_ACCESS_ID=token\n# comment\nexport VOR_BASE_URL='https://example/'\n", encoding="utf-8")

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.delenv("VOR_BASE_URL", raising=False)

    loaded = env_utils.load_env_file(env_file)

    assert loaded == {
        "VOR_ACCESS_ID": "token",
        "VOR_BASE_URL": "https://example/",
    }
    assert os.environ["VOR_ACCESS_ID"] == "token"
    assert os.environ["VOR_BASE_URL"] == "https://example/"


def test_load_env_file_does_not_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "override.env"
    env_file.write_text("VOR_ACCESS_ID=new\n", encoding="utf-8")

    monkeypatch.setenv("VOR_ACCESS_ID", "existing")

    loaded = env_utils.load_env_file(env_file)

    assert loaded == {"VOR_ACCESS_ID": "new"}
    assert os.environ["VOR_ACCESS_ID"] == "existing"


def test_load_env_file_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "override.env"
    env_file.write_text("VOR_ACCESS_ID=new\n", encoding="utf-8")

    monkeypatch.setenv("VOR_ACCESS_ID", "existing")

    env_utils.load_env_file(env_file, override=True)

    assert os.environ["VOR_ACCESS_ID"] == "new"


def test_load_env_file_accepts_whitespace_around_equals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "spaced.env"
    env_file.write_text("TOKEN = abc123\nexport VALUE = quoted\n", encoding="utf-8")

    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("VALUE", raising=False)

    loaded = env_utils.load_env_file(env_file)

    assert loaded == {"TOKEN": "abc123", "VALUE": "quoted"}
    assert os.environ["TOKEN"] == "abc123"
    assert os.environ["VALUE"] == "quoted"


def test_load_env_file_strips_inline_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "inline.env"
    env_file.write_text(
        """
        TOKEN=abc123   # trailing comment
        QUOTED="value # kept"
        HASHY=abc#def
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("HASHY", raising=False)

    loaded = env_utils.load_env_file(env_file)

    assert loaded == {
        "TOKEN": "abc123",
        "QUOTED": "value # kept",
        "HASHY": "abc#def",
    }
    assert os.environ["TOKEN"] == "abc123"
    assert os.environ["QUOTED"] == "value # kept"
    assert os.environ["HASHY"] == "abc#def"


def test_load_default_env_files_respects_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extra_env = tmp_path / "extra.env"
    extra_env.write_text("EXTRA_VALUE=42\n", encoding="utf-8")

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", str(extra_env))
    monkeypatch.delenv("EXTRA_VALUE", raising=False)

    loaded = env_utils.load_default_env_files()

    assert loaded[extra_env] == {"EXTRA_VALUE": "42"}
    assert os.environ["EXTRA_VALUE"] == "42"


def test_load_env_file_handles_io_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file = tmp_path / "broken.env"
    env_file.write_bytes(b"\xff\xfe\xff")

    monkeypatch.delenv("BROKEN_VALUE", raising=False)

    caplog.set_level("WARNING", logger="build_feed")
    loaded = env_utils.load_env_file(env_file)

    assert loaded == {}
    assert "Kann .env-Datei" in caplog.text
    assert "BROKEN_VALUE" not in os.environ
