"""``src.feed.logging`` and ``src.feed.logging_safe`` read the configuration at use.

``refresh_from_env()`` rebinds ``LOG_LEVEL``, ``LOG_FORMAT``, ``LOG_MAX_BYTES``
and ``LOG_BACKUP_COUNT`` in ``src.feed.config``. Both logging modules used to
import these names (``from .config import LOG_LEVEL``), which copies the value
of the first import; a refresh did not reach the copy. They now read
``feed_config.<NAME>`` inside the function that needs the value.

``LOG_DIR`` is not part of this: ``LOG_DIR``, ``error_log_path`` and
``diagnostics_log_path`` stay module-level values computed at import, because
``reporting.py`` and ``github_issue.py`` import the paths by name.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from src.feed import config as feed_config
from src.feed import logging as feed_logging
from src.feed import logging_safe


@pytest.fixture
def refreshed_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Hand out ``monkeypatch``; afterwards drop its changes and re-read the configuration.

    The autouse fixture ``_feed_config_stays`` (tests/_conftest/zustand.py)
    restores the values of ``src.feed.config`` as well; this fixture does not
    depend on it.
    """
    try:
        yield monkeypatch
    finally:
        monkeypatch.undo()
        feed_config.refresh_from_env()


@pytest.fixture
def log_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``configure_logging`` at ``tmp_path`` and close the file handlers it adds."""
    monkeypatch.setattr(feed_logging, "LOG_DIR", tmp_path.as_posix())
    monkeypatch.setattr(feed_logging, "error_log_path", tmp_path / "errors.log")
    monkeypatch.setattr(feed_logging, "diagnostics_log_path", tmp_path / "diagnostics.log")
    monkeypatch.setattr(feed_logging, "_LOGGING_CONFIGURED", False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        yield tmp_path
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def _added_file_handlers(directory: Path) -> list[RotatingFileHandler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).parent == directory.resolve()
    ]


# Two levels: whatever ``LOG_LEVEL`` was at the first import, one of them differs.
@pytest.mark.parametrize(("name", "level"), [("DEBUG", logging.DEBUG), ("WARNING", logging.WARNING)])
def test_configure_logging_uses_the_refreshed_log_level(
    refreshed_env: pytest.MonkeyPatch, log_files: Path, name: str, level: int
) -> None:
    refreshed_env.setenv("LOG_LEVEL", name)
    feed_config.refresh_from_env()

    feed_logging.configure_logging()

    assert logging.getLogger().level == level


def test_configure_logging_uses_the_refreshed_rotation_values(
    refreshed_env: pytest.MonkeyPatch, log_files: Path
) -> None:
    refreshed_env.setenv("LOG_MAX_BYTES", "4321")
    refreshed_env.setenv("LOG_BACKUP_COUNT", "2")
    feed_config.refresh_from_env()

    feed_logging.configure_logging()

    handlers = _added_file_handlers(log_files)
    assert sorted(Path(handler.baseFilename).name for handler in handlers) == [
        "diagnostics.log",
        "errors.log",
    ]
    assert [handler.maxBytes for handler in handlers] == [4321, 4321]
    assert [handler.backupCount for handler in handlers] == [2, 2]


def test_make_formatter_uses_the_refreshed_log_format(refreshed_env: pytest.MonkeyPatch) -> None:
    refreshed_env.setenv("LOG_FORMAT", "json")
    feed_config.refresh_from_env()
    assert isinstance(logging_safe._make_formatter(), logging_safe.SafeJSONFormatter)

    refreshed_env.setenv("LOG_FORMAT", "plain")
    feed_config.refresh_from_env()
    formatter = logging_safe._make_formatter()
    assert isinstance(formatter, logging_safe.SafeFormatter)
    assert not isinstance(formatter, logging_safe.SafeJSONFormatter)


def test_setup_script_logging_installs_the_refreshed_format(
    refreshed_env: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    safe_types = (logging_safe.SafeFormatter, logging_safe.SafeJSONFormatter)
    # The helper adds its handler only when no handler with one of the two
    # formatters is attached; set those aside for the duration of the test.
    original = list(root.handlers)
    for handler in original:
        if isinstance(handler.formatter, safe_types):
            root.removeHandler(handler)
    before = list(root.handlers)
    refreshed_env.setenv("LOG_FORMAT", "json")
    feed_config.refresh_from_env()
    try:
        logging_safe.setup_script_logging(root.level)

        added = [handler for handler in root.handlers if handler not in before]
        assert [type(handler.formatter) for handler in added] == [logging_safe.SafeJSONFormatter]
    finally:
        root.handlers[:] = original
