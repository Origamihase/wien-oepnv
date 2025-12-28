from __future__ import annotations

from pathlib import Path

from src.utils import configuration_wizard as wizard


def test_compute_non_interactive_defaults() -> None:
    result = wizard.compute_non_interactive_configuration({}, {}, accept_defaults=True)

    assert result.managed["FEED_TITLE"] == wizard.CONFIG_OPTIONS[1].default_str()
    assert result.managed["WL_ENABLE"] == "true"
    assert "VOR_ACCESS_ID" not in result.managed
    assert any("VOR_ACCESS_ID" in warning for warning in result.warnings)


def test_compute_non_interactive_overrides_and_custom_entries() -> None:
    existing = {"FEED_TITLE": "Custom Feed", "CUSTOM_FLAG": "enabled"}
    overrides = {"MAX_ITEMS": "25", "EXTRA_SETTING": "value"}

    result = wizard.compute_non_interactive_configuration(existing, overrides, accept_defaults=False)

    assert result.managed["FEED_TITLE"] == "Custom Feed"
    assert result.managed["MAX_ITEMS"] == "25"
    assert result.custom["CUSTOM_FLAG"] == "enabled"
    assert result.custom["EXTRA_SETTING"] == "value"
    assert (None, "25") in result.changes.values()


def test_format_env_document_includes_secret_placeholder(tmp_path: Path) -> None:
    managed = {
        "OUT_PATH": "docs/feed.xml",
        "FEED_TITLE": "Titel",
        "FEED_DESC": "Beschreibung",
        "FEED_LINK": "https://example.invalid",
        "MAX_ITEMS": "10",
        "FEED_TTL": "15",
        "MAX_ITEM_AGE_DAYS": "365",
        "ABSOLUTE_MAX_AGE_DAYS": "540",
        "ENDS_AT_GRACE_MINUTES": "10",
        "PROVIDER_TIMEOUT": "25",
        "PROVIDER_MAX_WORKERS": "0",
        "STATE_RETENTION_DAYS": "60",
        "WL_ENABLE": "true",
        "OEBB_ENABLE": "true",
        "VOR_ENABLE": "true",
        "BAUSTELLEN_ENABLE": "true",
    }
    document = wizard.format_env_document(managed, {})

    assert "# VOR_ACCESS_ID" in document
    assert "OUT_PATH=docs/feed.xml" in document


def test_format_env_document_escapes_newlines() -> None:
    managed = {
        "FEED_TITLE": "Titel",
        "FEED_DESC": "Line 1\nLine 2",
        "FEED_LINK": "https://example.invalid",
        "MAX_ITEMS": "10",
        "FEED_TTL": "15",
        "MAX_ITEM_AGE_DAYS": "365",
        "ABSOLUTE_MAX_AGE_DAYS": "540",
        "ENDS_AT_GRACE_MINUTES": "10",
        "PROVIDER_TIMEOUT": "25",
        "PROVIDER_MAX_WORKERS": "0",
        "STATE_RETENTION_DAYS": "60",
        "WL_ENABLE": "true",
        "OEBB_ENABLE": "true",
        "VOR_ENABLE": "true",
        "BAUSTELLEN_ENABLE": "true",
    }

    document = wizard.format_env_document(managed, {})

    assert 'FEED_DESC="Line 1\\nLine 2"' in document


def test_calculate_changes_handles_add_and_remove() -> None:
    previous = {"FEED_TITLE": "Alt", "OLD_KEY": "value"}
    current = {"FEED_TITLE": "Neu", "NEW_KEY": "value"}

    diff = wizard.calculate_changes(previous, current)

    assert diff["FEED_TITLE"] == ("Alt", "Neu")
    assert diff["OLD_KEY"] == ("value", None)
    assert diff["NEW_KEY"] == (None, "value")
